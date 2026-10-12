"""Offline Worker/Entry replay. Real PNG starts and synthetic contracts stay separate.

No Windows controller is constructed. The existing inert Entry fixture is the
only publisher. A real-PNG action with no retained successor is refused; it is
never supplied a made-up successful game receipt or an edited screenshot.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import csv
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace


def configure(code_root):
    # Official ORT privacy contract: this must precede initialization; the
    # Python API alone can be too late for an initialization event.
    # Only this offline process is changed, never the machine environment.
    if 'onnxruntime' in sys.modules:
        raise RuntimeError('Start a fresh process so ORT telemetry can be disabled before initialization')
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    sys.path.insert(0, str(Path(code_root).resolve() / 'tools'))
    global runner, compatibility, contracts
    import currency_wars_runner as runner
    import test_local_runtime_compatibility as compatibility
    import test_currency_wars_economy as contracts


def ready_fixture(worker, code_root):
    """Initialize process bookkeeping only; never add visible game facts."""
    worker.context = {name: None for name in ('guide', 'guide_tracking', 'investments', 'environment',
        'team', 'bonds', 'gear', 'tasks', 'hp', 'coins', 'xp', 'preparation_review', 'guide_reference',
        'reward_capacity', 'economy_plan', 'business_resume')}
    worker.preparation_scope, worker.preparation_reviews = None, {}
    worker.live_mode, worker.cached_guide_binding = None, None
    worker.last_preparation_stage = None
    worker.knowledge = runner.coaching.load_knowledge(Path(code_root) / 'docs' / 'GAME_KNOWLEDGE.json')
    worker.history, worker.inspections, worker.inspection_attempted = {}, {}, set()
    worker.shop_stages, worker.loot_pickup_attempted, worker.node_result_attempted = set(), set(), set()
    worker.wait_started, worker.wait_page = None, None
    worker.node_key, worker.node_attempts, worker.node_started = None, 0, None
    worker.node_last_page, worker.node_consecutive = None, 0
    worker.node_progress, worker.node_progress_seen = 0, 0
    worker.panel_index, worker.panel_state = 0, 'enter'
    worker.args = SimpleNamespace(max_matches=1)
    worker.state['statistics'].update(decisions=0, failures=0, matches_confirmed=0)
    worker.log_events = []
    worker.log = worker.log_events.append
    worker.publish = lambda **changes: worker.state.update(changes)


def replay_real_starts(fixtures, code_root):
    manifest = json.loads((fixtures / 'manifest.json').read_text(encoding='utf-8'))
    from currency_wars_perception import Perception
    reader, results = Perception(), []
    for index, case in enumerate(manifest['cases']):
        data = (fixtures / case['file']).read_bytes()
        if hashlib.sha256(data).hexdigest() != case['export_sha256']:
            raise ValueError('retained fixture hash mismatch: ' + case['id'])
        fixture = compatibility.RuntimeCompatibilityTests()
        with fixture.manual_bridge_fixture() as (runtime, records, owner, control, unused):
            (runtime / 'runner-manual.json').unlink()
            control.frame = data
            publish = control.publish_request
            candidates = []
            def inert(value):
                mutations = [a for a in value.get('actions', []) if a['type'] not in ('observe', 'wait')]
                if mutations:
                    candidates.extend(copy.deepcopy(mutations))
                    control.published.append(value)
                    control.write_json(runtime / 'result.json', {'id': value['id'], 'ok': False,
                        'completed': [], 'input_attempted': False, 'attempted_actions': [],
                        'error': 'offline_fixture_missing_successor: candidate recorded, zero physical input',
                        'offline_fixture_receipt': True})
                    return
                publish(value)
            control.publish_request = inert
            worker = fixture.frame_worker(runtime, records, owner, control, reader)
            ready_fixture(worker, code_root)
            begin = time.perf_counter()
            observed = worker.observe()  # Actual Entry immutable transport + actual production OCR.
            observed_at = time.perf_counter()
            error = None
            try:
                worker.tick(observed)
            except RuntimeError as exc:
                if 'offline_fixture_missing_successor' not in str(exc):
                    raise
                error = str(exc)
            end = time.perf_counter()
            request = worker.state.get('decision_request') or {}
            eligible = observed['page'] in ('shop', 'preparation')
            results.append({'id': case['id'], 'kind': 'retained_png_start_only',
                'snapshot_sha256': observed['snapshot_id'], 'engine_cold': index == 0,
                'page': observed['page'], 'fields': observed['fields'], 'observed': copy.deepcopy(observed),
                'normal_root_requests': int(bool(request) and request.get('kind') != 'unknown_page'),
                'exception_root_requests': int(request.get('kind') == 'unknown_page'),
                'request_kind': request.get('kind'), 'reason': request.get('reason') or error,
                'candidate_actions': candidates, 'missing_successor': bool(candidates),
                'phase': worker.preparation_checklist(observed)['phase'] if eligible else 'unclassified',
                'phase_coverage': {'denominator': list(runner.coaching.PHASES),
                    'reached': ['rewards'] if eligible else [], 'completed': [], 'ordered_transitions': 0},
                'timing_seconds': {'entry_and_perception': observed_at - begin, 'rule_and_inert_entry': end - observed_at,
                    'total': end - begin, 'ocr_and_field_read_reported': observed.get('elapsed_ms', 0) / 1000,
                    'exclusive': {'perception': observed.get('elapsed_ms', 0) / 1000,
                        'entry_io_unattributed': max(0., observed_at - begin - observed.get('elapsed_ms', 0) / 1000),
                        'decision_plus_inert_publication': end - observed_at},
                    'real_input_animation': None, 'supervisor_wait': None},
                'knowledge_available': worker.knowledge.get('error') is None,
                'physical_inputs': 0, 'fresh_game_captures': 0, 'historical_receipts_available': False})
    return results


def replay_economy_contract():
    """Existing generated-number/receipt fixture, never reported as real OCR."""
    sequence = [contracts.values(coins=62, xp=[44, 52]), contracts.values(coins=58, xp=[48, 52]),
                contracts.values(coins=54, level=8, xp=[0, 72])]
    result = []
    for name, changes in [('three_f_success', sequence), ('zero_effect', [contracts.values()])]:
        with contracts.EconomyTests().worker(changes) as (worker, record, control, frames):
            worker.panel_index, worker.panel_state = 0, 'enter'
            worker.node_key, worker.node_attempts, worker.node_started = None, 0, None
            worker.node_last_page, worker.node_consecutive = None, 0
            worker.node_progress, worker.node_progress_seen = 0, 0
            worker.wait_page, worker.wait_started, worker.shop_stages = None, None, set()
            begin = time.perf_counter()
            request = worker.state['decision_request']
            worker.execute_plan({'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                'resume_epoch': worker.epoch(), 'context_update': {'economy_plan': record},
                'actions': [{'type': 'finish_preparation_review', 'reason': 'synthetic fixture current budget'}]})
            worker.tick(worker.last_observation)
            if name == 'three_f_success' and worker.preparation_checklist(worker.last_observation)['phase'] == 'economy':
                request = worker.state['decision_request']
                assert request['kind'] == 'shop_strategy', 'baseline completion must return through the real tick path'
                review = {'proof': {'source': 'observed_screen', 'snapshot_id': request['snapshot_id'],
                    'evidence_file': request['evidence_file'], 'resume_epoch': worker.epoch()},
                    'value': {'reviewer': 'supervising_agent', 'phase': 'economy', 'completed': True,
                        'stage': '2-3', 'findings': 'Synthetic transaction fixture: budget and actual deltas resolved'}}
                worker.execute_plan({'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                    'resume_epoch': worker.epoch(), 'context_update': {'preparation_review': review},
                    'actions': [{'type': 'finish_preparation_review', 'reason': 'synthetic completion acknowledgement'}]})
            count = len(control.published)
            if name == 'zero_effect':
                worker.advance_economy(worker.last_observation)
                assert len(control.published) == count, 'unknown/zero effect must not replay'
            end = time.perf_counter()
            requests = [event['request']['kind'] for event in worker.log_events if event.get('event') == 'strategy_request']
            ledger = worker.economy_ledger('2-3')
            result.append({'id': name, 'kind': 'synthetic_numeric_transaction_contract',
                'frame_sequence': [hashlib.sha256(frame[1]).hexdigest() for frame in frames],
                'normal_root_requests': sum(kind != 'economy_result' for kind in requests),
                'exception_root_requests': requests.count('economy_result'), 'request_kinds': requests,
                'simulated_keys': [a['args'] for request in control.published for a in request.get('actions', []) if a['type'] == 'key'],
                'spent': copy.deepcopy(ledger['spent']), 'pending': bool(ledger['pending']),
                'next_phase': worker.preparation_checklist(worker.last_observation)['phase'],
                'economy_completed': worker.preparation_reviews.get('economy', {}).get('completed') is True,
                'phase_coverage': {'denominator': list(runner.coaching.PHASES), 'tested': ['economy'],
                    'fixture_preconditions_only': list(runner.coaching.PHASES[:3])},
                'physical_inputs': 0, 'timing_seconds': {'total': end - begin},
                'receipt_origin': 'inert_entry_fixture_not_historical_game_receipt',
                'timing_limit': 'Generated numeric frames and mocked crop OCR; excludes human decision latency, real OCR, game animation.'})
    return result


def seconds(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def union(intervals):
    answer, end = 0., None
    for left, right in sorted(intervals):
        answer += max(0., right - max(left, end if end is not None else left))
        end = max(right, end if end is not None else right)
    return answer


def audit_host(profile_path, host_path):
    profile, host = (json.loads(Path(path).read_text(encoding='utf-8')) for path in (profile_path, host_path))
    events, spans = profile['events'], host['tool_spans']
    intervals = [(seconds(item['start_utc']), seconds(item['end_utc'])) for item in spans]
    start, end = seconds(host['window_start_utc']), seconds(host['window_end_utc'])
    points = sorted({start, end, *(value for interval in intervals for value in interval)})
    partitions = Counter()
    for left, right in zip(points, points[1:]):
        tags = sorted({tag for item, (a, b) in zip(spans, intervals) if a < right and b > left for tag in item['tags']})
        partitions[' + '.join(tags) if tags else 'outside_matched_tools_unattributed'] += right - left
    helpers = [(seconds(item['time']) - item['seconds'], seconds(item['time'])) for item in events]
    return {'source_code_sha': profile['code_sha'], 'helper_events': len(events),
        'matched_host_span_count': len(spans), 'unique_host_call_ids': len({item['call_id'] for item in spans}),
        'helper_kind_counts': dict(Counter(item['kind'] for item in events)),
        'helper_result_counts': dict(Counter('reported_success' if item.get('ok') is True else
            'reported_refusal_or_error' if item.get('ok') is False else 'outcome_not_exported' for item in events)),
        'window_seconds': end - start, 'helper_internal_sum_seconds': sum(item['seconds'] for item in events),
        'helper_interval_union_seconds': union(helpers), 'host_tool_union_seconds': union(intervals),
        'host_outside_tools_unattributed_seconds': end - start - union(intervals),
        'host_tag_counts_nonexclusive': dict(Counter(tag for item in spans for tag in item['tags'])),
        'host_time_partition_seconds': dict(partitions), 'helper_annotations': events,
        'step_source_available': False, 'historical_request_and_receipt_payloads_available': False,
        'attribution_limit': 'Labels and tags describe operator activity, not Worker requests or semantic guards. Tool-outside time is not all reasoning; helper duration is not all OCR. No model roundtrip count inferred from 99 helper events.',
        'diagnostic_refresh_groups': host.get('diagnostic_refresh_groups', [])}


def write_outputs(report, prefix, before=None):
    prefix.parent.mkdir(parents=True, exist_ok=True)
    if before:
        previous = json.loads(Path(before).read_text(encoding='utf-8'))
        report['comparison'] = []
        for section in ('real_starts', 'synthetic_contracts'):
            if [item['id'] for item in previous[section]] != [item['id'] for item in report[section]]:
                raise ValueError('before/after case count or order differs')
            for old, new in zip(previous[section], report[section]):
                if old['id'] != new['id'] or old.get('snapshot_sha256', old.get('frame_sequence')) != new.get('snapshot_sha256', new.get('frame_sequence')):
                    raise ValueError('before/after fixture sequence differs')
                report['comparison'].append({'section': section, 'case': new['id'],
                    'normal_root_before': old['normal_root_requests'], 'normal_root_after': new['normal_root_requests'],
                    'exception_root_before': old['exception_root_requests'], 'exception_root_after': new['exception_root_requests'],
                    'seconds_before': old['timing_seconds']['total'], 'seconds_after': new['timing_seconds']['total'],
                    'timing_claim': 'one pass; no speedup inference'})
    prefix.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    rows = report.get('comparison') or [{'section': section, 'case': item['id'],
        'normal_root_requests': item['normal_root_requests'], 'exception_root_requests': item['exception_root_requests'],
        'total_seconds': item['timing_seconds']['total']}
        for section in ('real_starts', 'synthetic_contracts') for item in report[section]]
    with prefix.with_suffix('.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    escape = lambda value: html.escape(str(value))
    table = '<table><tr>' + ''.join('<th>' + escape(key) + '</th>' for key in rows[0]) + '</tr>'
    table += ''.join('<tr>' + ''.join('<td>' + escape(value) + '</td>' for value in row.values()) + '</tr>' for row in rows) + '</table>'
    details = ''.join('<details><summary>' + escape(item['id']) + '</summary><pre>' + escape(json.dumps({key: value for key, value in item.items() if key != 'observed'}, ensure_ascii=False, indent=2)) + '</pre></details>' for item in report['real_starts'] + report['synthetic_contracts'])
    text = '<!doctype html><meta charset="utf-8"><title>备战离线回放</title><style>body{font:16px system-ui;max-width:1250px;margin:40px auto;padding:0 20px;line-height:1.6}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #bbb;padding:8px;text-align:left}pre{white-space:pre-wrap}summary{cursor:pointer;font-weight:600}strong{color:#9b3b00}</style><h1>备战离线回放</h1><p><strong>真实输入 0，新游戏截图 0。四个保留截图起点不构成连续备战。</strong></p><p>真实覆盖：备战/商店图到达领奖阶段，完整准备阶段完成 0/6，历史动作转移证据 0。单独标记的合成事务仅测试经济阶段（1/6）；前三阶段是 fixture 前提，收据是惰性替身产物。ROOT 策略、布阵与出战验收保持独立。单次耗时不作速度提升结论；未知动画与主管延迟不填零。</p>' + table + details
    if report.get('host_audit'):
        audit = {key: value for key, value in report['host_audit'].items() if key != 'helper_annotations'}
        text += '<h2>历史宿主计时证据</h2><pre>' + escape(json.dumps(audit, ensure_ascii=False, indent=2)) + '</pre>'
    prefix.with_suffix('.html').write_text(text, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--before', type=Path)
    parser.add_argument('--root-profile', type=Path)
    parser.add_argument('--host-timing', type=Path)
    args = parser.parse_args()
    configure(args.code_root)
    names = ['currency_wars_runner.py', 'currency_wars_economy.py', 'currency_wars_perception.py', 'currency_wars_state_reader.py']
    report = {'schema': 'currency-wars-preparation-replay/v1', 'physical_inputs': 0, 'fresh_game_captures': 0,
        'telemetry_policy': 'ORT_DISABLE_TELEMETRY=1 before import; disable_telemetry_events; process-local only',
        'source_sha256': {name: hashlib.sha256((args.code_root / 'tools' / name).read_bytes()).hexdigest() for name in names},
        'real_starts': replay_real_starts(args.fixtures, args.code_root),
        'synthetic_contracts': replay_economy_contract(),
        'limits': ['Public corpus lacks continuous intermediate PNGs and historical receipts.',
            'Inert transport receipt timestamps are replay time, not original game capture time.',
            'Numeric transaction fixture is not Perception/OCR or game success evidence.',
            'No 1-1/1-2/3-1 screenshots; coverage of those requested start nodes is 0/3.']}
    if bool(args.root_profile) != bool(args.host_timing):
        parser.error('root-profile and host-timing must be provided together')
    if args.root_profile:
        report['host_audit'] = audit_host(args.root_profile, args.host_timing)
    write_outputs(report, args.output, args.before)
    print(json.dumps({'report': str(args.output.with_suffix('.json')), 'real_cases': len(report['real_starts']),
        'synthetic_cases': len(report['synthetic_contracts']), 'physical_inputs': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
