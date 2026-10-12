"""Real-PNG full/scoped Perception method profile; no Worker or controller.

Use separate fresh processes with --mode full and --mode scoped, on the same
machine in the same image order. Scoped mode uses rewards for q00/q01/q02 and
economy for the seven refresh images. No synthetic business observations or
image crops are passed in place of the retained complete PNGs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import contextlib
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from unittest.mock import patch


class Trace:
    def __init__(self):
        self.events = []
        self.stack = []
        self.frame = None
        self.phase = None
        self.read_kind = None

    def wrap(self, function, name, metadata=None):
        @functools.wraps(function)
        def call(*args, **kwargs):
            event = {'id': len(self.events), 'method': name,
                'parent_id': self.stack[-1]['id'] if self.stack else None,
                'frame': self.frame, 'phase': self.phase, 'read_kind': self.read_kind}
            if metadata:
                event.update(metadata(args, kwargs))
            self.events.append(event)
            self.stack.append(event)
            event['start_ns'] = time.perf_counter_ns()
            try:
                return function(*args, **kwargs)
            except Exception as exc:
                event['exception_type'] = type(exc).__name__
                raise
            finally:
                event['end_ns'] = time.perf_counter_ns()
                event['inclusive_seconds'] = (event['end_ns'] - event['start_ns']) / 1e9
                self.stack.pop()
        return call

    def finalized(self):
        child_seconds = defaultdict(float)
        for event in self.events:
            if event['parent_id'] is not None:
                child_seconds[event['parent_id']] += event['inclusive_seconds']
        return [{**event, 'exclusive_seconds': event['inclusive_seconds'] - child_seconds[event['id']]}
                for event in self.events]


COMPONENTS = ('engine_initialization', 'primary_ocr', 'field_ocr', 'shop_ocr',
              'shop_other', 'state_reader', 'reward_rules', 'other_perception')


def exclusive_components(events, indices):
    """Each exclusive interval has one owner; nested OCR is not counted twice."""
    result = {name: 0. for name in COMPONENTS}
    for index in indices:
        event = events[index]
        chain = [event]
        while chain[-1]['parent_id'] is not None:
            chain.append(events[chain[-1]['parent_id']])
        methods = [item['method'] for item in chain]
        ocr = next((item for item in chain if item['method'] == 'RapidOCR.__call__'), None)
        if 'RapidOCR.__init__' in methods:
            component = 'engine_initialization'
        elif ocr:
            component = ('shop_ocr' if 'ShopReader.read' in methods else
                         'primary_ocr' if ocr.get('use_det') is not False else 'field_ocr')
        elif any(name in methods for name in ('TextDetector.__call__', 'TextRecognizer.__call__',
                                              'TextClassifier.__call__')):
            # A scoped production path may invoke installed detector/recognizer
            # members directly. Their costs still belong to primary OCR.
            component = 'shop_ocr' if 'ShopReader.read' in methods else 'primary_ocr'
        elif 'ShopReader.read' in methods:
            component = 'shop_other'
        elif 'StateReader.read' in methods:
            component = 'state_reader'
        elif 'rewards.detect' in methods:
            component = 'reward_rules'
        else:
            component = 'other_perception'
        result[component] += event['exclusive_seconds']
    return result


def selected_semantics(observed):
    semantic = observed.get('semantic', {})
    shop, team, reward = observed.get('shop') or {}, semantic.get('team') or {}, semantic.get('rewards') or {}
    player = semantic.get('player_hud') or {}
    return {'snapshot_id': observed['snapshot_id'], 'page': observed['page'], 'fields': observed['fields'],
        'coins': semantic.get('coins'), 'shop_ok': shop.get('ok'), 'shop_status': shop.get('status'),
        'shop_errors': shop.get('errors'), 'team_checked': team.get('checked'),
        'team_fully_read': team.get('fully_read'), 'team_status': team.get('status'),
        'inventory_status': (semantic.get('inventory') or {}).get('status'),
        'player_hud': {key: player.get(key) for key in ('actor', 'status', 'reason', 'level', 'xp')},
        'known_board_units': sum(unit.get('location') == 'board' for unit in team.get('units', [])),
        'read_contract': observed.get('read_contract'),
        'rewards': {key: reward.get(key) for key in ('scanned', 'reason', 'input_allowed',
            'interaction_required', 'area_fully_visible', 'uncertain', 'all_rewards_cleared')},
        'reward_targets': [{key: target.get(key) for key in ('kind', 'bounds', 'center', 'score')}
                           for target in reward.get('targets', [])],
        'ocr_rows_count': len(observed.get('rows', []))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('full', 'scoped'), default='full')
    parser.add_argument('--compare-to', type=Path)
    args = parser.parse_args()
    root = args.code_root.resolve()
    source_names = ('currency_wars_perception.py', 'currency_wars_shop_reader.py',
                    'currency_wars_state_reader.py', 'currency_wars_rewards.py')
    source_hashes = lambda: {name: hashlib.sha256((root / 'tools' / name).read_bytes()).hexdigest()
                             for name in source_names}
    source_before = source_hashes()
    sequence = []
    for directory, prefix in (('reward-sequence', 'q'), ('refresh-sequence', 'r')):
        path = root / 'handoff' / '2026-10-07' / directory
        manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
        for frame in manifest['frames']:
            image = (path / frame['file']).resolve()
            expected = frame['export_image_sha256']
            if hashlib.sha256(image.read_bytes()).hexdigest() != expected:
                raise ValueError('Historical image hash mismatch: ' + str(image))
            sequence.append({'id': prefix + format(frame['index'], '02d'),
                'path': str(image), 'sha256': expected})
    if len(sequence) != 10:
        raise ValueError('Expected q00/q01/q02 and seven retained refresh frames')

    # Disable ORT telemetry BEFORE importing the runtime, then use the API.
    # The existing installed inference models are used; no resource download.
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    started = time.perf_counter()
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    from rapidocr_onnxruntime import RapidOCR
    from rapidocr_onnxruntime.ch_ppocr_det import TextDetector
    from rapidocr_onnxruntime.ch_ppocr_rec import TextRecognizer
    from rapidocr_onnxruntime.ch_ppocr_cls import TextClassifier
    dependency_import_seconds = time.perf_counter() - started
    sys.path.insert(0, str(root / 'tools'))
    started = time.perf_counter()
    import currency_wars_perception as perception
    import currency_wars_shop_reader as shop
    import currency_wars_state_reader as state
    import currency_wars_rewards as rewards
    production_import_seconds = time.perf_counter() - started
    trace = Trace()
    has_scope = 'scope' in inspect.signature(perception.Perception.read).parameters
    if args.mode == 'scoped' and not has_scope:
        raise ValueError('This production source has no Perception.read(scope=...) API')
    def engine_metadata(call_args, kwargs):
        array = call_args[1] if len(call_args) > 1 else None
        return {'shape': list(getattr(array, 'shape', [])),
            'input_crops': len(array) if isinstance(array, list) else None,
            'use_det': kwargs.get('use_det', True), 'use_cls': kwargs.get('use_cls', True),
            'caller': trace.stack[-1]['method'] if trace.stack else None}

    observations = []
    with contextlib.ExitStack() as stack:
        targets = [(RapidOCR, '__init__', 'RapidOCR.__init__'),
            (RapidOCR, '__call__', 'RapidOCR.__call__'),
            (TextDetector, '__call__', 'TextDetector.__call__'),
            (TextRecognizer, '__call__', 'TextRecognizer.__call__'),
            (TextClassifier, '__call__', 'TextClassifier.__call__'),
            (perception.Perception, 'read', 'Perception.read'),
            (shop.ShopReader, 'read', 'ShopReader.read'), (shop.ShopReader, '_load', 'ShopReader._load'),
            (state.StateReader, 'read', 'StateReader.read'), (state.StateReader, '_load', 'StateReader._load'),
            (perception, 'classify', 'classify'),
            (perception, 'native_player_hud', 'native_player_hud'),
            (perception, 'semantic_facts', 'semantic_facts'), (rewards, 'detect', 'rewards.detect')]
        for owner, name, label in targets:
            stack.enter_context(patch.object(owner, name, trace.wrap(getattr(owner, name), label,
                engine_metadata if label in ('RapidOCR.__call__', 'TextDetector.__call__',
                    'TextRecognizer.__call__', 'TextClassifier.__call__') else None)))
        reader = perception.Perception()
        for phase in ('cold_sequence', 'warm_sequence'):
            for index, frame in enumerate(sequence):
                trace.frame, trace.phase = frame['id'], phase
                trace.read_kind = 'actual'
                requested_scope = ('rewards' if frame['id'].startswith('q') else 'economy') if args.mode == 'scoped' else 'full'
                scope_kwargs = {'scope': requested_scope} if has_scope else {}
                event_index = len(trace.events)
                begin = time.perf_counter()
                actual = reader.read(frame['path'], force=True, **scope_kwargs)
                elapsed = time.perf_counter() - begin
                actual_semantics = selected_semantics(actual)
                observations.append({'frame': frame['id'], 'phase': phase, 'read_kind': 'actual',
                    'requested_scope': requested_scope,
                    'engine_cold': phase == 'cold_sequence' and index == 0,
                    'wall_seconds': elapsed, 'perception_elapsed_ms': actual.get('elapsed_ms'),
                    'reported_read_timing': actual.get('read_timing'),
                    'event_ids': list(range(event_index, len(trace.events))), 'semantics': actual_semantics})
                trace.read_kind = 'cache'
                event_index = len(trace.events)
                begin = time.perf_counter()
                cached = reader.read(frame['path'], **scope_kwargs)
                elapsed = time.perf_counter() - begin
                cached_semantics = selected_semantics(cached)
                if actual_semantics != cached_semantics:
                    raise ValueError('Same-byte cache changed the returned semantic observations')
                observations.append({'frame': frame['id'], 'phase': phase, 'read_kind': 'cache',
                    'requested_scope': requested_scope,
                    'wall_seconds': elapsed, 'cache_returned_same_object': actual is cached,
                    'perception_elapsed_ms': cached.get('elapsed_ms'),
                    'reported_read_timing': cached.get('read_timing'),
                    'stored_elapsed_ms_is_original_computation': not bool(cached.get('read_timing', {}).get('cache_hit')),
                    'event_ids': list(range(event_index, len(trace.events))), 'semantics_equal_actual': True})

    events = trace.finalized()
    aggregate = defaultdict(lambda: defaultdict(lambda: {'calls': 0, 'inclusive_seconds': 0., 'exclusive_seconds': 0.}))
    for event in events:
        row = aggregate[event['phase'] + '/' + event['read_kind']][event['method']]
        row['calls'] += 1
        for name in ('inclusive_seconds', 'exclusive_seconds'):
            row[name] += event[name]
    for observed in observations:
        subset = [events[index] for index in observed['event_ids']]
        measured = next(event['inclusive_seconds'] for event in subset if event['method'] == 'Perception.read')
        exclusive = sum(event['exclusive_seconds'] for event in subset)
        observed['nested_exclusive_sum_seconds'] = exclusive
        observed['perception_method_inclusive_seconds'] = measured
        observed['accounting_residual_seconds'] = measured - exclusive
        observed['method_inclusive_seconds'] = {name: sum(event['inclusive_seconds'] for event in subset
            if event['method'] == name) for name in {event['method'] for event in subset}}
        observed['exclusive_components_seconds'] = exclusive_components(events, observed['event_ids'])
        observed['components_residual_seconds'] = measured - sum(observed['exclusive_components_seconds'].values())
    report = {'schema': 'currency-wars-perception-method-profile/v1', 'offline_only': True,
        'physical_inputs': 0, 'new_game_captures': 0,
        'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'source_hashes': source_before, 'source_hashes_after': source_hashes(),
        'source_unchanged_during_measurement': source_before == source_hashes(),
        'mode': args.mode, 'scope_api_available': has_scope,
        'machine': {'system': platform.system(), 'machine': platform.machine(),
            'node': platform.node(), 'python': platform.python_version()},
        'dependency_import_seconds': dependency_import_seconds,
        'production_import_seconds': production_import_seconds,
        'cold_start_contract': 'First actual read constructs the OCR engine. ORT/RapidOCR and production '
            'module imports are measured separately above, never silently added into each frame.',
        'cache_contract': 'Each actual forced read is followed by one unforced same-byte read. '
            'External wall time measures cache cost. Current scope-aware source reports current-call '
            'read_timing/elapsed_ms; older PR12 cached elapsed_ms belongs to the original computation. '
            'The per-observation flag states which contract was actually observed.',
        'accounting_contract': 'Inclusive method totals are nested, not additive. Exclusive method totals '
            'subtract immediate-child intervals and sum to the Perception.read interval. '
            'The exclusive component table is additive: primary_ocr is detection-enabled OCR outside ShopReader; '
            'field_ocr is recognition-only numeric OCR outside ShopReader; shop_ocr is OCR within ShopReader. '
            'ShopReader.read inclusive also contains shop_ocr and any internal initialization, so it is not '
            'added again to those component buckets. Engine initialization is always shown separately. '
            'Direct RapidOCR calls retain image shape, detection mode and caller. '
            'Missing private shop/state assets remain unknown; no synthetic shop or confidence injection.',
        'sequence': sequence, 'observations': observations, 'events': events,
        'method_totals': aggregate}
    if args.compare_to:
        report['comparison'] = compare_reports(json.loads(args.compare_to.read_text(encoding='utf-8')), report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'source': report['source_revision'],
        'mode': args.mode,
        'source_unchanged': report['source_unchanged_during_measurement'],
        'actual_reads': sum(item['read_kind'] == 'actual' for item in observations),
        'cache_reads': sum(item['read_kind'] == 'cache' for item in observations),
        'actual_wall_seconds': sum(item['wall_seconds'] for item in observations if item['read_kind'] == 'actual'),
        'cache_wall_seconds': sum(item['wall_seconds'] for item in observations if item['read_kind'] == 'cache'),
        'physical_inputs': 0}, ensure_ascii=False))


def compare_reports(previous, current):
    if [(frame['id'], frame['sha256']) for frame in previous['sequence']] != [
            (frame['id'], frame['sha256']) for frame in current['sequence']]:
        raise ValueError('Timing comparison requires the exact same ordered retained PNG sequence')
    if previous['machine'] != current['machine']:
        raise ValueError('Timing comparison requires the same machine/runtime identity')
    rows = []
    keys = ('page', 'fields', 'coins', 'rewards', 'reward_targets')
    old_observations = {(item['frame'], item['phase'], item['read_kind']): item for item in previous['observations']}
    for after in current['observations']:
        before = old_observations[(after['frame'], after['phase'], after['read_kind'])]
        differences = []
        required_differences = []
        omitted_field_differences = []
        if after['read_kind'] == 'actual':
            differences = [name for name in keys if before['semantics'].get(name) != after['semantics'].get(name)]
            for name in ('page', 'coins', 'rewards', 'reward_targets'):
                if name in differences:
                    required_differences.append(name)
            unread = (after['semantics'].get('read_contract') or {}).get('unread', [])
            for name in before['semantics']['fields'].keys() | after['semantics']['fields'].keys():
                if before['semantics']['fields'].get(name) == after['semantics']['fields'].get(name):
                    continue
                intentionally_omitted = (name == 'level' and 'player_hud' in unread) or name in unread
                (omitted_field_differences if intentionally_omitted else required_differences).append('fields.' + name)
        rows.append({'frame': after['frame'], 'phase': after['phase'], 'read_kind': after['read_kind'],
            'before_scope': before.get('requested_scope', 'full'), 'after_scope': after['requested_scope'],
            'before_wall_seconds': before['wall_seconds'], 'after_wall_seconds': after['wall_seconds'],
            'delta_seconds': after['wall_seconds'] - before['wall_seconds'],
            'observed_shared_semantic_differences': differences,
            'explicitly_unread_field_differences': omitted_field_differences,
            'required_semantic_differences': required_differences})
    return {'before_source_hashes': previous['source_hashes'], 'after_source_hashes': current['source_hashes'],
        'same_machine': True, 'same_sequence': True, 'rows': rows,
        'timing_limit': 'Two actual passes per mode, not a statistical speed guarantee or game-loop measurement. '
            'Cold engine construction and same-PNG cache costs remain separate. '
            'Different shared semantic fields are reported rather than silently treated as equivalent; '
            'scope-specific omissions need their own contract review.'}


if __name__ == '__main__':
    main()
