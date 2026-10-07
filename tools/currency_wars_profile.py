"""Optional, local wall-time accounting; never sends or retries game input.

Each recorder owns a separate JSONL file. Only explicitly shared monotonic clock
domains are combined. Stage and operation totals are two views of the SAME time.
"""
from __future__ import annotations

import argparse
import contextlib
import contextvars
import csv
import html
import json
import re
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'currency-wars-profile/1'
PHASES = {'rewards': '奖励', 'startup_guide': '指南', 'inventory_cleanup': '整理',
          'economy': '购买升级', 'lineup_equipment': '布阵装备',
          'battle_acceptance': '出战验收', 'battle': '战斗',
          'settlement': '结算切换', 'recovery': '恢复', 'unknown': '未知阶段'}
OPERATIONS = {'capture': '采集', 'ocr': 'OCR', 'takeover': '接管',
              'input_animation': '输入动画', 'decision': '决策', 'rules': '规则',
              'publication': '发布', 'unknown': '未知空档'}
_MISSING = object()


class ProfileRecorder:
    """Small opt-in recorder. IO failure disables profiling, not the controller.

    run_id is the default clock domain: producers in one existing local runtime
    may share it; different leases are not silently joined by wall-clock time.
    comparison_key names an explicitly agreed comparable scenario, not a SHA.
    """
    def __init__(self, records_dir, *, run_id, source='worker', enabled=False,
                 source_sha=None, clock_id=None, comparison_key=None):
        self.enabled, self.error, self.path = bool(enabled), None, None
        self.session_id = uuid.uuid4().hex
        self.base = {'schema': SCHEMA, 'session_id': self.session_id,
                     'run_id': run_id, 'source': source, 'source_sha': source_sha,
                     'clock_id': clock_id or run_id, 'comparison_key': comparison_key}
        self.context = {'match_id': None, 'stage': None, 'resume_epoch': None}
        self.phase, self.node_id, self.phase_id = 'unknown', None, None
        self._node_seen = False
        self._spans = {}
        self._parents = contextvars.ContextVar('profile_' + self.session_id, default=())
        self._lock = threading.RLock()
        self._stream = None
        if self.enabled:
            try:
                directory = Path(records_dir)
                directory.mkdir(parents=True, exist_ok=True)
                safe_source = re.sub(r'[^a-zA-Z0-9_-]', '_', str(source))[:32]
                self.path = directory / ('profile-events-' + safe_source + '-' + self.session_id + '.jsonl')
                self._stream = self.path.open('x', encoding='utf-8')
                self._emit('session_begin', self.session_id)
            except OSError as exc:
                self._disable(exc)

    def _disable(self, exc):
        self.error = type(exc).__name__
        self.enabled = False
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
            self._stream = None

    def _emit(self, kind, item_id, *, stamp=None, context=None, **values):
        if not self.enabled:
            return
        with self._lock:
            event = {**self.base, **(self.context if context is None else context),
                     'event_id': uuid.uuid4().hex, 'kind': kind, 'id': item_id,
                     'monotonic_ns': time.monotonic_ns() if stamp is None else stamp,
                     'utc': datetime.now(timezone.utc).isoformat(), **values}
            try:
                self._stream.write(json.dumps(event, ensure_ascii=False) + '\n')
                self._stream.flush()
            except (OSError, TypeError, ValueError) as exc:
                # In particular: a failed timer write after a published input
                # must not make its caller mistake the input for an unissued one.
                self._disable(exc)

    def set_context(self, *, match_id=_MISSING, stage=_MISSING,
                    resume_epoch=_MISSING, phase=_MISSING, start_complete=None):
        if not self.enabled:
            return
        updated = dict(self.context)
        for key, value in (('match_id', match_id), ('stage', stage), ('resume_epoch', resume_epoch)):
            if value is not _MISSING:
                updated[key] = value
        if not isinstance(updated['stage'], str) or not re.fullmatch(r'[1-3]-[1-9]', updated['stage']):
            updated['stage'] = None
        next_phase = self.phase if phase is _MISSING else (phase if isinstance(phase, str) and phase in PHASES else 'unknown')
        node_changed = any(updated[key] != self.context[key] for key in ('match_id', 'stage'))
        same_match = bool(updated['match_id']) and updated['match_id'] == self.context['match_id']
        observed_transition = self._node_seen and same_match and bool(self.context['stage'])
        stamp = time.monotonic_ns()
        if node_changed or next_phase != self.phase:
            if self.phase_id:
                self._emit('phase_end', self.phase_id, stamp=stamp)
                self.phase_id = None
        if node_changed:
            if self.node_id:
                self._emit('node_end', self.node_id, stamp=stamp,
                           complete=bool(updated['stage']) and same_match, next_stage=updated['stage'])
                self.node_id = None
            self.context = updated
            if updated['stage'] and updated['match_id']:
                self.node_id = uuid.uuid4().hex
                self._emit('node_begin', self.node_id, stamp=stamp,
                           complete=observed_transition if start_complete is None else bool(start_complete))
                self._node_seen = True
        else:
            self.context = updated
        self.phase = next_phase
        if not self.phase_id:
            self.phase_id = uuid.uuid4().hex
            self._emit('phase_begin', self.phase_id, stamp=stamp, phase=self.phase)

    def start_span(self, name, *, phase=None, operation=None, parent_id=None,
                   request_id=None, receipt_id=None, snapshot_id=None, business_step=None):
        if not self.enabled:
            return None
        item_id = uuid.uuid4().hex
        parents = self._parents.get()
        record = {'context': dict(self.context), 'name': name,
                  'phase': phase if isinstance(phase, str) and phase in PHASES else None,
                  'operation': operation if isinstance(operation, str) and operation in OPERATIONS else None,
                  'parent_id': parent_id or (parents[-1] if parents else None),
                  'request_id': request_id, 'receipt_id': receipt_id, 'snapshot_id': snapshot_id,
                  'business_step': business_step}
        self._spans[item_id] = record
        self._emit('span_begin', item_id, **record)
        return item_id

    @property
    def current_span_id(self):
        parents = self._parents.get()
        return parents[-1] if parents else None

    def end_span(self, span_id, *, outcome='returned'):
        record = self._spans.pop(span_id, None)
        if record:
            self._emit('span_end', span_id, context=record['context'], outcome=outcome)

    def record_return(self, *, category, business_step, reason, request_id, snapshot_id):
        self._emit('root_return', uuid.uuid4().hex, phase=self.phase,
                   category=category, business_step=business_step, reason=reason,
                   request_id=request_id, snapshot_id=snapshot_id)

    def record_economy_flow(self, *, flow_id, context, **values):
        """An execution checkpoint, not a time interval or a ROOT request."""
        self._emit('economy_flow', flow_id, context=context, flow_id=flow_id, **values)

    def record_interval(self, name, *, start_ns, end_ns, phase=None, operation=None,
                        parent_id=None, request_id=None, receipt_id=None, snapshot_id=None):
        """Import an actual same-clock broker interval, never a guessed duration.

        The caller must authenticate the receipt and its existing run/clock
        binding. This diagnostic helper does not authenticate or approve input.
        Malformed telemetry cannot turn already published input into an error.
        """
        if not self.enabled:
            return None
        if (type(start_ns) is not int or type(end_ns) is not int
                or not 0 <= start_ns <= end_ns <= time.monotonic_ns()):
            self._emit('diagnostic', uuid.uuid4().hex, reason='invalid_imported_interval')
            return None
        item_id = uuid.uuid4().hex
        parents = self._parents.get()
        context = dict(self.context)
        self._emit('span_begin', item_id, stamp=start_ns, context=context,
                   name=name, phase=phase if isinstance(phase, str) and phase in PHASES else None,
                   operation=operation if isinstance(operation, str) and operation in OPERATIONS else None,
                   parent_id=parent_id or (parents[-1] if parents else None),
                   request_id=request_id, receipt_id=receipt_id, snapshot_id=snapshot_id,
                   timing_source='broker_receipt')
        self._emit('span_end', item_id, stamp=end_ns, context=context, outcome='recorded')
        return item_id

    @contextlib.contextmanager
    def span(self, name=None, **kwargs):
        span_id = self.start_span(name or kwargs.get('operation') or kwargs.get('phase') or 'step', **kwargs)
        token = self._parents.set(self._parents.get() + ((span_id,) if span_id else ()))
        outcome = 'returned'
        try:
            yield span_id
        except BaseException:
            outcome = 'raised'
            raise
        finally:
            self._parents.reset(token)
            self.end_span(span_id, outcome=outcome)

    def close(self, *, complete=False):
        """complete=True only when the last node really reached its end boundary."""
        stamp = time.monotonic_ns()
        if self.phase_id:
            self._emit('phase_end', self.phase_id, stamp=stamp)
        if self.node_id:
            self._emit('node_end', self.node_id, stamp=stamp, complete=bool(complete), next_stage=None)
        self._emit('session_end', self.session_id, stamp=stamp, complete=True)
        if self._stream:
            try:
                self._stream.close()
            except OSError as exc:
                self._disable(exc)
        self._stream = None
        self.enabled = False


def read_events(paths):
    events, issues = [], []
    for path in paths:
        try:
            with Path(path).open(encoding='utf-8-sig') as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError('event is not an object')
                        events.append(event)
                    except ValueError:
                        issues.append({'file': str(path), 'line': line_number, 'reason': 'invalid_json'})
        except (OSError, UnicodeError) as exc:
            issues.append({'file': str(path), 'reason': type(exc).__name__})
    return events, issues


def union_ns(intervals):
    total, edge = 0, None
    for start, end in sorted(intervals):
        if end <= start:
            continue
        total += max(0, end - max(start, edge if edge is not None else start))
        edge = end if edge is None else max(edge, end)
    return total


def _seconds(value):
    return round(value / 1_000_000_000, 9)


def summarize_events(events, issues=None):
    issues = list(issues or [])
    valid, seen = [], {}
    for event in events:
        if (event.get('schema') != SCHEMA or type(event.get('monotonic_ns')) is not int
                or event['monotonic_ns'] < 0
                or any(not isinstance(event.get(key), str) or not event[key]
                       for key in ('event_id', 'session_id', 'run_id', 'clock_id', 'kind', 'id'))
                or any(event.get(key) is not None and not isinstance(event[key], str)
                       for key in ('phase', 'operation', 'match_id', 'stage', 'parent_id', 'comparison_key', 'business_step',
                                   'source', 'source_sha', 'utc'))):
            issues.append({'reason': 'unsupported_or_invalid_event'})
            continue
        key = (event['session_id'], event['event_id'])
        if key in seen:
            if seen[key] != event:
                issues.append({'reason': 'conflicting_event_id', 'event_id': event['event_id']})
            continue
        seen[key] = event
        valid.append(event)
    paired, last, returns, economy_flows = defaultdict(dict), defaultdict(int), [], []
    for event in valid:
        last[event['session_id']] = max(last[event['session_id']], event['monotonic_ns'])
        if event['kind'] == 'root_return':
            returns.append({key: event.get(key) for key in ('run_id', 'clock_id', 'match_id', 'stage',
                'phase', 'business_step', 'category', 'reason', 'request_id', 'snapshot_id')})
            continue
        if event['kind'] == 'economy_flow':
            # Keep source identities and the actual point event. Do not pair
            # these into spans or infer causality from an adjacent ROOT return.
            economy_flows.append(dict(event))
            continue
        if event['kind'] == 'diagnostic':
            issues.append({'reason': event.get('reason', 'recorder_diagnostic'), 'event_id': event['event_id']})
            continue
        parts = event['kind'].rsplit('_', 1)
        if len(parts) != 2 or parts[0] not in ('session', 'node', 'phase', 'span') or parts[1] not in ('begin', 'end'):
            issues.append({'reason': 'unsupported_event_kind', 'kind': event['kind']})
            continue
        key = (event['session_id'], parts[0], event['id'])
        if parts[1] in paired[key]:
            issues.append({'reason': 'duplicate_boundary', 'id': event['id']})
        else:
            paired[key][parts[1]] = event
    intervals = []
    for (session, kind, item_id), pair in paired.items():
        begin, end = pair.get('begin'), pair.get('end')
        if not begin:
            issues.append({'reason': 'missing_begin', 'id': item_id})
            continue
        identities = ('run_id', 'clock_id') if kind == 'session' else ('run_id', 'clock_id', 'match_id', 'stage')
        if end and any(end.get(key) != begin.get(key) for key in identities):
            issues.append({'reason': 'boundary_identity_mismatch', 'id': item_id})
            continue
        if not end:
            issues.append({'reason': 'missing_end', 'id': item_id, 'kind': kind})
            if kind == 'span':
                # No invented operation duration after a crash; the enclosing
                # observed window will expose the corresponding unknown time.
                continue
        finish = end['monotonic_ns'] if end else last[session]
        if finish < begin['monotonic_ns']:
            issues.append({'reason': 'backward_clock', 'id': item_id})
            continue
        if kind == 'node' and (not isinstance(begin.get('stage'), str)
                or not re.fullmatch(r'[1-3]-[1-9]', begin['stage']) or not begin.get('match_id')):
            issues.append({'reason': 'invalid_node_identity', 'id': item_id})
            continue
        intervals.append({**begin, 'type': kind, 'start': begin['monotonic_ns'], 'end': finish,
                          'key': (session, item_id), 'domain': (begin['run_id'], begin['clock_id']),
                          'start_complete': bool(begin.get('complete')),
                          'end_complete': bool(end and end.get('complete')),
                          'next_stage': end.get('next_stage') if end else None,
                          'outcome': end.get('outcome') if end else None})
    spans = {item['key']: item for item in intervals if item['type'] == 'span'}
    ancestors = {}
    for key, item in spans.items():
        chain, parent = set(), item.get('parent_id')
        while parent:
            parent_key = (item['session_id'], parent)
            if parent_key == key or parent_key in chain:
                issues.append({'reason': 'cyclic_span_parent', 'id': item['id']})
                chain = set()
                break
            if parent_key not in spans:
                issues.append({'reason': 'missing_span_parent', 'id': item['id']})
                break
            chain.add(parent_key)
            parent = spans[parent_key].get('parent_id')
        ancestors[key] = chain

    def category(active, dimension):
        tagged = [item for item in active if item['type'] == 'span' and item.get(dimension)]
        leaves = [item for item in tagged if not any(item['key'] in ancestors.get(other['key'], ()) for other in tagged)]
        if not leaves and dimension == 'phase':
            leaves = [item for item in active if item['type'] == 'phase']
        names = {item.get(dimension) for item in leaves}
        allowed = PHASES if dimension == 'phase' else OPERATIONS
        if len(names) == 1 and next(iter(names)) in allowed:
            return next(iter(names)), False
        return 'unknown', len(names) > 1

    buckets, step_buckets, totals = {}, {}, {'observed_ns': 0, 'unassigned_ns': 0, 'node_conflict_ns': 0}
    domains = defaultdict(list)
    for item in intervals:
        domains[item['domain']].append(item)
    for domain, items in domains.items():
        edges = defaultdict(lambda: {'add': [], 'remove': []})
        for index, item in enumerate(items):
            if item['end'] > item['start']:
                edges[item['start']]['add'].append(index)
                edges[item['end']]['remove'].append(index)
        active, previous = {}, None
        for stamp, changes in sorted(edges.items()):
            current = list(active.values())
            if previous is not None and any(item['type'] == 'session' for item in current):
                duration = stamp - previous
                totals['observed_ns'] += duration
                nodes = {(item.get('match_id'), item.get('stage')) for item in current if item['type'] == 'node'}
                if len(nodes) != 1:
                    totals['unassigned_ns'] += duration
                    if len(nodes) > 1:
                        totals['node_conflict_ns'] += duration
                else:
                    match, stage = next(iter(nodes))
                    key = (*domain, match, stage)
                    bucket = buckets.setdefault(key, {'run_id': domain[0], 'clock_id': domain[1],
                        'match_id': match, 'stage': stage, 'plane': stage.split('-')[0],
                        'total_ns': 0, 'phase_ns': defaultdict(int), 'operation_ns': defaultdict(int),
                        'overlap_unknown_ns': 0})
                    bucket['total_ns'] += duration
                    for dimension in ('phase', 'operation'):
                        name, conflict = category(current, dimension)
                        bucket[dimension + '_ns'][name] += duration
                        if dimension == 'operation' and conflict:
                            bucket['overlap_unknown_ns'] += duration
                    tagged = [item for item in current if item['type'] == 'span' and item.get('business_step')]
                    leaves = [item for item in tagged if not any(item['key'] in ancestors.get(other['key'], ())
                                                                 for other in tagged)]
                    steps = {item['business_step'] for item in leaves}
                    step = next(iter(steps)) if len(steps) == 1 else 'unassigned'
                    step_key = (*key, step)
                    measured = step_buckets.setdefault(step_key, {'total_ns': 0, 'operations': defaultdict(int)})
                    measured['total_ns'] += duration
                    operation, unused = category(current, 'operation')
                    measured['operations'][operation] += duration
            for index in changes['remove']:
                active.pop(index, None)
            for index in changes['add']:
                active[index] = items[index]
            previous = stamp
    nodes = []
    for key, bucket in sorted(buckets.items()):
        records = [item for item in intervals if item['type'] == 'node'
                   and (*item['domain'], item.get('match_id'), item.get('stage')) == key]
        coverage = union_ns((item['start'], item['end']) for item in records)
        earliest, latest = min(item['start'] for item in records), max(item['end'] for item in records)
        complete = (any(item['start_complete'] for item in records if item['start'] == earliest)
                    and any(item['end_complete'] for item in records if item['end'] == latest)
                    and coverage == latest - earliest and bucket['total_ns'] == coverage)
        counts = defaultdict(int)
        for span in spans.values():
            if ((*span['domain'], span.get('match_id'), span.get('stage')) == key
                    and span.get('operation') in OPERATIONS):
                counts[span['operation']] += 1
        nodes.append({key: value for key, value in bucket.items() if not key.endswith('_ns')} | {
            'total_seconds': _seconds(bucket['total_ns']), 'complete_boundaries': complete,
            'phase_seconds': {name: _seconds(bucket['phase_ns'][name]) for name in PHASES},
            'operation_seconds': {name: _seconds(bucket['operation_ns'][name]) for name in OPERATIONS},
            'operation_calls': dict(counts), 'overlap_unknown_seconds': _seconds(bucket['overlap_unknown_ns'])})
    plane_groups = defaultdict(list)
    for node in nodes:
        plane_groups[(node['run_id'], node['clock_id'], node['match_id'], node['plane'])].append(node)
    planes = []
    for (run, clock, match, plane), group in sorted(plane_groups.items()):
        planes.append({'run_id': run, 'clock_id': clock, 'match_id': match, 'plane': plane,
                       'included_nodes': [node['stage'] for node in group],
                       'total_seconds': round(sum(node['total_seconds'] for node in group), 9),
                       'scope': 'recorded_nodes_only',
                       'phase_seconds': {name: round(sum(node['phase_seconds'][name] for node in group), 9) for name in PHASES},
                       'operation_seconds': {name: round(sum(node['operation_seconds'][name] for node in group), 9) for name in OPERATIONS}})
    span_rows = []
    for key, span in spans.items():
        children = [(max(span['start'], child['start']), min(span['end'], child['end']))
                    for child in spans.values() if key in ancestors.get(child['key'], ())]
        span_rows.append({name: span.get(name) for name in ('id', 'parent_id', 'source', 'run_id', 'clock_id',
            'match_id', 'stage', 'name', 'phase', 'operation', 'business_step', 'request_id', 'receipt_id', 'snapshot_id', 'outcome')} | {
            'inclusive_seconds': _seconds(span['end'] - span['start']),
            'exclusive_seconds': _seconds(span['end'] - span['start'] - union_ns(children))})
    comparison_keys = {event.get('comparison_key') for event in valid}
    operation_totals = {name: round(sum(node['operation_seconds'][name] for node in nodes), 9) for name in OPERATIONS}
    return {'schema': SCHEMA, 'methodology': 'monotonic-union-exclusive-v1',
            'scope': 'provided_instrumented_windows_only', 'event_count': len(valid),
            'comparison_key': next(iter(comparison_keys)) if len(comparison_keys) == 1 else None,
            'observed_seconds': _seconds(totals['observed_ns']),
            'unassigned_seconds': _seconds(totals['unassigned_ns']),
            'node_conflict_seconds': _seconds(totals['node_conflict_ns']),
            'clock_domains': [{'run_id': run, 'clock_id': clock} for run, clock in sorted(domains)],
            'sources': [dict(zip(('run_id', 'source', 'source_sha'), key)) for key in sorted(
                {(event['run_id'], event.get('source'), event.get('source_sha')) for event in valid}, key=str)],
            'utc_start': min((event.get('utc', '') for event in valid), default=None),
            'utc_end': max((event.get('utc', '') for event in valid), default=None),
            'nodes': nodes, 'plane_first_nodes': [node for node in nodes if node['stage'].endswith('-1')],
            'planes': planes, 'spans': span_rows, 'issues': issues, 'root_returns': returns,
            'economy_flow_events': economy_flows,
            'business_steps': [dict(zip(('run_id', 'clock_id', 'match_id', 'stage', 'business_step'), key)) | {
                'total_seconds': _seconds(value['total_ns']),
                'operation_seconds': {operation: _seconds(value['operations'][operation]) for operation in OPERATIONS}}
                for key, value in sorted(step_buckets.items())],
            'operation_totals_seconds': operation_totals,
            'largest_measured_operations': sorted(
                [{'operation': key, 'seconds': value} for key, value in operation_totals.items() if key != 'unknown' and value > 0],
                key=lambda row: row['seconds'], reverse=True),
            'live_automation_verified': False}


def compare_reports(before, after):
    if (before.get('schema') != SCHEMA or after.get('schema') != SCHEMA
            or before.get('methodology') != after.get('methodology')
            or not before.get('comparison_key') or before.get('comparison_key') != after.get('comparison_key')):
        return {'comparable': False, 'reason': '需要相同计时口径与显式相同的 comparison_key；没有生成提升比例。'}
    if ({source.get('source') for source in before.get('sources', [])}
            != {source.get('source') for source in after.get('sources', [])}):
        return {'comparable': False, 'reason': '计时生产者覆盖不同，不能直接比较只有 worker 与另含主管子段的报告。'}
    if before.get('issues') or after.get('issues'):
        return {'comparable': False, 'reason': '日志存在缺失或冲突，先修正证据覆盖；没有生成提升比例。'}
    if any(set(node.get('operation_seconds', {})) != set(OPERATIONS)
           for report in (before, after) for node in report.get('nodes', [])):
        return {'comparable': False, 'reason': '操作分类覆盖不同，旧报告不能补造新增规则或发布计时。'}
    left, right = defaultdict(list), defaultdict(list)
    for target, report in ((left, before), (right, after)):
        for node in report.get('nodes', []):
            if node['complete_boundaries']:
                target[node['stage']].append(node)
    rows = []
    for stage in sorted(left.keys() & right.keys()):
        if len(left[stage]) != 1 or len(right[stage]) != 1:
            continue
        a, b = left[stage][0], right[stage][0]
        rows.append({'stage': stage, 'before_seconds': a['total_seconds'], 'after_seconds': b['total_seconds'],
                     'delta_seconds': round(b['total_seconds'] - a['total_seconds'], 9),
                     'phase_delta_seconds': {key: round(b['phase_seconds'][key] - a['phase_seconds'][key], 9) for key in PHASES},
                     'operation_delta_seconds': {key: round(b['operation_seconds'][key] - a['operation_seconds'][key], 9) for key in OPERATIONS}})
    return {'comparable': bool(rows), 'nodes': rows,
            'reason': '仅比较同场景且两端完整的唯一节点样本；时长差异本身不证明改动导致提速。' if rows else '没有两端完整且唯一的同节点样本。'}


def render_html(report):
    def table(headers, rows):
        return '<div class="table"><table><thead><tr>' + ''.join('<th>' + html.escape(str(x)) + '</th>' for x in headers) + '</tr></thead><tbody>' + ''.join(
            '<tr>' + ''.join('<td>' + html.escape(str(x)) + '</td>' for x in row) + '</tr>' for row in rows) + '</tbody></table></div>'
    def timing_table(nodes, dimension, labels):
        return table(['节点', '运行', '秒数', '边界'] + list(labels.values()), [
            [node['stage'], node['run_id'][:12], f"{node['total_seconds']:.3f}",
             '完整' if node['complete_boundaries'] else '部分'] +
            [f"{node[dimension + '_seconds'][key]:.3f}" for key in labels] for node in nodes])
    sections = '<h2>已测耗时较大的操作</h2><p>下表只说明计时占比，不直接断言根因。未知空档应先补证据，不分摊给识别或决策。</p>'
    sections += table(['操作', '记录秒数'], [[OPERATIONS[row['operation']], f"{row['seconds']:.3f}"] for row in report['largest_measured_operations']])
    sections += '<h2>节点与阶段</h2>' + timing_table(report['nodes'], 'phase', PHASES)
    sections += '<h2>节点与操作</h2>' + timing_table(report['nodes'], 'operation', OPERATIONS)
    sections += '<h2>各位面首节点</h2>' + timing_table(report['plane_first_nodes'], 'phase', PHASES)
    sections += '<h2>业务步骤</h2><p>与节点耗时是同一段时间的另一种分解，不可再次相加。未标注步骤保持未归类。</p>' + table(
        ['节点', '步骤', '秒数'] + list(OPERATIONS.values()),
        [[row['stage'], row['business_step'], f"{row['total_seconds']:.3f}"] +
         [f"{row['operation_seconds'][key]:.3f}" for key in OPERATIONS] for row in report.get('business_steps', [])])
    sections += '<h2>主管回传</h2>' + table(['节点', '阶段', '步骤', '类别', '实际原因'],
        [[row['stage'], row['phase'], row['business_step'], row['category'], row['reason']]
         for row in report.get('root_returns', [])])
    sections += '<h2>位面汇总</h2><p>仅合计日志实际覆盖的节点，不推定整个位面已测全。</p>' + table(
        ['位面', '运行', '已记录节点', '秒数'], [[p['plane'], p['run_id'][:12], ', '.join(p['included_nodes']), f"{p['total_seconds']:.3f}"] for p in report['planes']])
    if 'comparison' in report:
        comparison = report['comparison']
        sections += '<h2>改前改后</h2><p>' + html.escape(comparison['reason']) + '</p>' + table(
            ['节点', '改前秒数', '改后秒数', '差值秒数'], [[r['stage'], r['before_seconds'], r['after_seconds'], r['delta_seconds']] for r in comparison.get('nodes', [])])
    sections += '<h2>证据缺口</h2><pre>' + html.escape(json.dumps(report['issues'], ensure_ascii=False, indent=2)) + '</pre>'
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>货币战争耗时报告</title><style>body{font:15px/1.6 system-ui,sans-serif;max-width:1400px;margin:auto;padding:24px;color:#17243a;background:#f5f7fa}'
            'h1,h2{line-height:1.3}h2{margin-top:32px}.table{overflow:auto}table{border-collapse:collapse;background:white;width:100%}'
            'th,td{padding:9px 12px;border:1px solid #dbe1ea;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}'
            'pre{white-space:pre-wrap;background:#fff;padding:16px}strong{color:#12665d}</style><h1>货币战争耗时报告</h1>'
            f'<p>日志覆盖 <strong>{report["observed_seconds"]:.3f} 秒</strong>；无法绑定节点 {report["unassigned_seconds"]:.3f} 秒；'
            f'有效事件 {report["event_count"]} 条。</p><p>阶段与操作是同一段墙钟时间的两种分解，不能相加。父子区间取并集扣除；'
            '不同操作并行且无明确父子关系的重叠部分保留为未知。缺失日志、未关闭节点及未观测时间不补造。'
            '多个运行时分别计并集后相加，运行之间的真实间隔未知，不能将合计当作整局连续耗时。'
            '本报告不证明整局自主成功，也不把用户反馈的 17:00–00:30 当作测量结果。</p>' + sections + '</html>')


def write_report(report, output_prefix):
    prefix = Path(output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = {kind: Path(str(prefix) + '.' + kind) for kind in ('json', 'csv', 'html')}
    paths['json'].write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    columns = ['scope', 'run_id', 'clock_id', 'match_id', 'stage', 'plane', 'total_seconds', 'complete_boundaries']
    columns += ['phase_' + key + '_seconds' for key in PHASES] + ['operation_' + key + '_seconds' for key in OPERATIONS]
    with paths['csv'].open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for scope, records in (('node', report['nodes']), ('plane_first_node', report['plane_first_nodes']), ('plane', report['planes'])):
            for record in records:
                row = {key: record.get(key) for key in columns[:8]}
                row['scope'] = scope
                row.update({'phase_' + key + '_seconds': value for key, value in record['phase_seconds'].items()})
                row.update({'operation_' + key + '_seconds': value for key, value in record['operation_seconds'].items()})
                writer.writerow(row)
    paths['html'].write_text(render_html(report), encoding='utf-8')
    return paths


def main():
    parser = argparse.ArgumentParser(description='按节点汇总本地性能事件；不访问游戏或发送输入。')
    parser.add_argument('events', nargs='+', help='本协议的 JSONL 文件，可同时传入 worker 与主管文件')
    parser.add_argument('--output-prefix', required=True)
    parser.add_argument('--before', help='同口径的改前 JSON 报告；只比较完整节点')
    args = parser.parse_args()
    events, issues = read_events(args.events)
    report = summarize_events(events, issues)
    if args.before:
        report['comparison'] = compare_reports(json.loads(Path(args.before).read_text(encoding='utf-8-sig')), report)
    paths = write_report(report, args.output_prefix)
    print(json.dumps({'files': {key: str(value) for key, value in paths.items()},
                      'events': report['event_count'], 'issues': len(report['issues']),
                      'live_automation_verified': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
