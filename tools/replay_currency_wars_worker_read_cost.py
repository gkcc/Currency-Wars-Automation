"""Offline Worker cost to an identical consumer endpoint, including full upgrades.

The q02 case uses an unchanged historical PNG and production Perception. The
q00/q01 case keeps the original close completed list and must stop pending.
Six D / three F are separately labelled numeric/semantic/receipt contracts;
their fixture lookups are NEVER charged to OCR. No controller is constructed.

Run each --code-root in a separate process. Each case runs once cold and once
warm. Warm real cases use a new Perception and Worker with empty observation
caches, inheriting only the previous case instance's initialized engine/readers.
"""
from __future__ import annotations

import argparse
from collections import Counter
import contextlib
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch


CASES = ('q02_selection_request', 'q00_q01_original_completed',
         'six_d_protocol', 'three_f_protocol')
COMPONENTS = ('engine_initialization', 'primary_ocr', 'field_ocr', 'shop_ocr',
              'shop_other', 'state_reader', 'reward_rules', 'worker_rules_strategy',
              'publication_simulation', 'other_read_entry_io', 'protocol_fixture_lookup')
RULE_METHODS = ('tick', 'ask', 'execute_plan', 'preparation_policy', 'economic_policy',
                'accept_economy_plan', 'guard_preparation_action', 'require_economic_action',
                'finish_local_economy', 'reconcile_reward_step', 'guard_reward_step',
                'economy_observation')
IO_METHODS = ('observe', 'read_frame', 'command', 'ensure_full_observation', 'save_frame')


def configure(root):
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    if 'onnxruntime' in sys.modules:
        raise RuntimeError('Use a fresh process; disable ORT telemetry before importing it')
    started = time.perf_counter()
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    from rapidocr_onnxruntime import RapidOCR
    import_seconds = time.perf_counter() - started
    sys.path.insert(0, str(root / 'tools'))
    import currency_wars_runner as runner
    import currency_wars_perception as perception
    import currency_wars_shop_reader as shop
    import currency_wars_state_reader as state
    import currency_wars_rewards as rewards
    import test_local_runtime_compatibility as compatibility
    import test_currency_wars_economy as contracts
    import replay_currency_wars_preparation as preparation
    import replay_currency_wars_refresh as refresh
    from replay_currency_wars_perception_scope import Trace
    preparation.runner = runner
    refresh.contracts = contracts  # Bind its existing pure plan helper; do not run the old replay.
    return SimpleNamespace(root=root, runner=runner, perception=perception, shop=shop,
        state=state, rewards=rewards, compatibility=compatibility, contracts=contracts,
        preparation=preparation, refresh=refresh, Trace=Trace, RapidOCR=RapidOCR,
        dependency_import_seconds=import_seconds)


def sources(root):
    names = ('currency_wars_runner.py', 'currency_wars_perception.py', 'currency_wars_economy.py',
             'currency_wars_shop_reader.py', 'currency_wars_state_reader.py',
             'currency_wars_rewards.py', 'currency_wars_broker_entry.py', 'currency_wars_profile.py')
    return {name: hashlib.sha256((root / 'tools' / name).read_bytes()).hexdigest() for name in names}


def load_history(root):
    result = {}
    for name in ('reward-sequence', 'refresh-sequence'):
        directory = root / 'handoff' / '2026-10-07' / name
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf8'))
        frames = []
        for frame in manifest['frames']:
            path = directory / frame['file']
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != frame['export_image_sha256']:
                raise ValueError('Historical PNG SHA256 mismatch: ' + str(path))
            frames.append(payload)
        result[name] = {'manifest': manifest, 'frames': frames}
    return result


def selected(observed):
    semantic = observed.get('semantic', {})
    reward = semantic.get('rewards') or {}
    board = [unit for unit in (semantic.get('team') or {}).get('units', [])
             if unit.get('location') == 'board']
    return {'snapshot_id': observed.get('snapshot_id'), 'page': observed.get('page'),
        'fields': copy.deepcopy(observed.get('fields')),
        'coins': copy.deepcopy(semantic.get('coins')),
        'rewards': {key: copy.deepcopy(reward.get(key)) for key in
            ('scanned', 'reason', 'input_allowed', 'interaction_required', 'all_rewards_cleared', 'targets')},
        'read_contract': copy.deepcopy(observed.get('read_contract')),
        'team_checked': (semantic.get('team') or {}).get('checked'),
        'occupied_board_slots': len(board),
        'named_board_units': sum(bool(unit.get('name')) for unit in board),
        'star_read_board_units': sum(type(unit.get('star')) is int for unit in board),
        'name_and_star_read_board_units': sum(bool(unit.get('name')) and type(unit.get('star')) is int
                                               for unit in board)}


def components(events):
    totals = dict.fromkeys(COMPONENTS, 0.)
    for event in events:
        chain = [event]
        while chain[-1]['parent_id'] is not None:
            chain.append(events[chain[-1]['parent_id']])
        methods = [item['method'] for item in chain]
        ocr = next((item for item in chain if item['method'] == 'RapidOCR.__call__'), None)
        if 'RapidOCR.__init__' in methods:
            owner = 'engine_initialization'
        elif ocr:
            owner = ('shop_ocr' if 'ShopReader.read' in methods else
                     'primary_ocr' if ocr.get('use_det') is not False
                         and ocr.get('shape') == [720, 1280, 3] else 'field_ocr')
        elif any(method in methods for method in ('FixturePerception.read', 'FixtureOCR.lookup')):
            owner = 'protocol_fixture_lookup'
        elif 'ShopReader.read' in methods:
            owner = 'shop_other'
        elif 'StateReader.read' in methods:
            owner = 'state_reader'
        elif 'rewards.detect' in methods:
            owner = 'reward_rules'
        elif 'inert_publish' in methods:
            owner = 'publication_simulation'
        else:
            # The nearest explicitly instrumented owner wins. Reads and Entry
            # work nested under ask/tick are not added to policy time again.
            owner = 'other_read_entry_io'
            for method in methods:
                if method in ('Perception.read', 'control.write_json', 'Worker.save_frame',
                              'Worker.observe', 'Worker.read_frame', 'Worker.command',
                              'Worker.ensure_full_observation', 'worker.publish', 'worker.log'):
                    break
                if method.startswith('Worker.') and method[7:] in RULE_METHODS or method in (
                        'economy.classify_effect', 'economy.observe_fields', 'progression_plan'):
                    owner = 'worker_rules_strategy'
                    break
        totals[owner] += event['exclusive_seconds']
    return totals


@contextlib.contextmanager
def instrument(modules, worker, control, reader, trace, real, reads):
    seen = Counter()
    original_read = reader.read
    label = 'Perception.read' if real else 'FixturePerception.read'
    timed_read = trace.wrap(original_read, label)

    def read(path, force=False, *, scope='full', **kwargs):
        index = len(trace.events)
        result = timed_read(path, force=force, scope=scope, **kwargs)
        events = trace.events[index:]
        def is_primary(event):
            if (event['method'] != 'RapidOCR.__call__' or event.get('use_det') is False
                    or event.get('shape') != [720, 1280, 3]):
                return False
            parent_id = event['parent_id']
            while parent_id is not None:
                parent = trace.events[parent_id]
                if parent['method'] == 'ShopReader.read':
                    return False
                parent_id = parent['parent_id']
            return True
        raw_calls = [event for event in events if is_primary(event)]
        key = (result['snapshot_id'], scope)
        parent = trace.stack[-1] if trace.stack else {}
        item = {'event_id': index, 'caller': trace.stack[-1]['method'] if trace.stack else None,
            'capture_request_id': parent.get('request_id'), 'frame_id': parent.get('frame_id'),
            'path': str(path), 'scope': scope, 'force': force,
            'reuse_primary_requested': kwargs.get('reuse_primary', False),
            'snapshot_id': result['snapshot_id'], 'same_sha_scope_seen_before': bool(seen[key]),
            'previous_same_sha_scope_reads': seen[key],
            'read_contract': copy.deepcopy(result.get('read_contract')),
            'read_timing': copy.deepcopy(result.get('read_timing')),
            'image_scope_cache_hit': (result.get('read_timing') or {}).get('cache_hit'),
            'cache_metadata_is_production': real, 'primary_ocr_calls': len(raw_calls),
            'primary_ocr_executed': bool(raw_calls) if real else None,
            'primary_reused_reported': (result.get('read_timing') or {}).get('primary_ocr_reused'),
            'declared_protocol_fact_lookup': not real}
        trace.events[index].update({key: value for key, value in item.items() if key != 'event_id'})
        reads.append(item)
        seen[key] += 1
        return result

    def ocr_metadata(args, kwargs):
        array = args[1] if len(args) > 1 else None
        return {'shape': list(getattr(array, 'shape', [])),
                'use_det': kwargs.get('use_det', True), 'use_cls': kwargs.get('use_cls', True)}

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(reader, 'read', read))
        targets = [(modules.RapidOCR, '__init__', 'RapidOCR.__init__'),
            (modules.RapidOCR, '__call__', 'RapidOCR.__call__'),
            (modules.shop.ShopReader, 'read', 'ShopReader.read'),
            (modules.state.StateReader, 'read', 'StateReader.read'),
            (modules.rewards, 'detect', 'rewards.detect'),
            (modules.runner.economy, 'observe_fields', 'economy.observe_fields'),
            (modules.runner.economy, 'classify_effect', 'economy.classify_effect'),
            (modules.runner, 'progression_plan', 'progression_plan'),
            (control, 'publish_request', 'inert_publish'),
            (control, 'write_json', 'control.write_json'),
            (worker, 'publish', 'worker.publish'), (worker, 'log', 'worker.log')]
        targets += [(worker, name, 'Worker.' + name) for name in RULE_METHODS + IO_METHODS]
        if not real:
            targets.append((reader, 'engine', 'FixtureOCR.lookup'))
        for owner, name, title in targets:
            metadata = (ocr_metadata if title == 'RapidOCR.__call__' else
                (lambda args, kwargs: {'request_id': args[0].get('id'),
                    'frame_id': args[0].get('observation', {}).get('frame_id'),
                    'scope': kwargs.get('scope', 'full'), 'force': kwargs.get('force', False),
                    'reuse_primary_requested': kwargs.get('reuse_primary', False)})
                if title == 'Worker.read_frame' else None)
            stack.enter_context(patch.object(owner, name, trace.wrap(getattr(owner, name), title,
                metadata)))
        yield


def install_inert_publisher(modules, control, runtime, payloads, choose, trace):
    """Replace only the existing test Control publisher, never use a controller."""
    control.published, control.publication_trace = [], []
    control.current_index, control.action_index = 0, 0
    control.validate_actions = lambda actions: [
        {'type': item['type'], 'args': [float(value) for value in item.get('args', [])]}
        for item in actions]

    def publish(value):
        actions = value.get('actions', [])
        if any(item['type'] not in ('observe', 'wait', 'key', 'click') for item in actions):
            raise ValueError('Offline publisher refuses unsupported action type')
        mutations = [item for item in actions if item['type'] not in ('observe', 'wait')]
        index, completed, origin = choose(value, mutations)
        payload = payloads[index]
        digest = hashlib.sha256(payload).hexdigest()
        frame_id = modules.runner.uuid.uuid4().hex
        directory = runtime / 'frames' / (hashlib.sha256(value['id'].encode()).hexdigest() + '-' + frame_id)
        directory.mkdir(parents=True)
        for filename in ('original.png', 'preview.png'):
            (directory / filename).write_bytes(payload)
        result = {'id': value['id'], 'ok': True, 'completed': copy.deepcopy(completed),
            'input_attempted': bool(mutations), 'attempted_actions': copy.deepcopy(mutations),
            'offline_fixture_receipt': True, 'receipt_origin': origin,
            'observation': {'frame_protocol': 1, 'request_id': value['id'], 'frame_id': frame_id,
                'captured_at': modules.runner.now(), 'snapshot': str(directory / 'preview.png'),
                'original': str(directory / 'original.png'), 'snapshot_sha256': digest,
                'original_sha256': digest, 'snapshot_size': [1920, 1080], 'original_size': [1920, 1080]}}
        control.published.append(copy.deepcopy(value))
        control.publication_trace.append({'request_id': value['id'], 'frame_id': frame_id,
            'frame_index': index, 'snapshot_id': digest, 'actions': copy.deepcopy(actions),
            'completed': copy.deepcopy(completed), 'receipt_origin': origin,
            'requested_wait_seconds': sum(item['args'][0] for item in actions if item['type'] == 'wait'),
            'executed_wait_seconds': None})
        control.write_json(runtime / 'result.json', result)
        control.current_index = index
        trace.frame = str(index)
    control.publish_request = publish


def numeric_reader(modules, rendered, states):
    """Existing generated-number contract semantics, with explicit scope only."""
    by_digest = {hashlib.sha256(item[1]).hexdigest(): state for item, state in zip(rendered, states)}
    crops = {}
    for (image, unused, fields), state in zip(rendered, states):
        for name, field in fields.items():
            value = '/'.join(map(str, state[name])) if name == 'xp' else str(state[name])
            crops[hashlib.sha256(image.crop(field['bounds']).tobytes()).hexdigest()] = value
    reader = SimpleNamespace()
    reader.engine = lambda crop, **unused: ([[crops[hashlib.sha256(crop.tobytes()).hexdigest()], .99]], None)

    def read(path, force=False, *, scope='full', reuse_primary=False):
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        state = by_digest[digest]
        result = {'snapshot_id': digest, 'page': 'shop',
            'fields': {'stage': '2-3', 'deployed': '7/7', 'level': str(state['level'])},
            'rows': [{'text': '刷新', 'confidence': .99, 'box': [200, 780, 260, 810]}],
            'semantic': {'coins': {'value': state['coins'], 'bounds': modules.runner.GOLD_HUD,
                                  'currency_icon_gold_fraction': .8}},
            'shop': modules.contracts.shop(digest), 'elapsed_ms': 0., 'fingerprint': '0' * 64,
            'read_contract': {'version': modules.perception.READ_CONTRACT_VERSION,
                'requested_scope': scope, 'effective_scope': scope, 'page_ocr': 'declared_fixture',
                'unread': [] if scope == 'full' else ['team', 'inventory']},
            'read_timing': {'cache_hit': False, 'source': 'declared_protocol_lookup_not_ocr'}}
        if scope != 'full':
            omitted = {'status': 'not_read', 'checked': False, 'fully_read': False,
                       'read_scope': scope, 'snapshot_id': digest}
            result['semantic']['team'] = {**omitted, 'units': []}
            result['semantic']['inventory'] = {**omitted, 'items': []}
        return result
    reader.read = read
    return reader


def endpoint(modules, worker, control):
    observed = worker.last_observation or {}
    requests = [event['request'] for event in worker.log_events if event.get('event') == 'strategy_request']
    checklist = worker.preparation_checklist(observed) if observed else {}
    ledger = worker.economy_ledger('2-3') if observed.get('fields', {}).get('stage') == '2-3' else None
    pending_reward = worker.pending_reward_step(observed) if observed else None
    request = worker.state.get('decision_request') or {}
    return {'observation': selected(observed), 'phase': checklist.get('phase'),
        'battle_ready': checklist.get('battle_ready'), 'pending': bool(pending_reward or ledger and ledger['pending']),
        'reward_pending': ({key: pending_reward.get(key) for key in
            ('kind', 'status', 'outcome', 'reason', 'publication_attempted', 'all_rewards_cleared')}
            if pending_reward else None),
        'spent': copy.deepcopy(ledger['spent']) if ledger else None,
        'economy_completed': worker.preparation_reviews.get('economy', {}).get('completed') is True,
        'terminal_request_kind': request.get('kind'),
        'requests': [{'kind': item['kind'], 'snapshot_id': item['snapshot_id'],
                      'return_reason': item.get('return_reason')} for item in requests],
        'normal_root_requests': sum(item.get('return_reason', {}).get('category') != 'exception' for item in requests),
        'exception_root_requests': sum(item.get('return_reason', {}).get('category') == 'exception' for item in requests),
        'simulated_mutation_publications': sum(any(action['type'] not in ('observe', 'wait')
            for action in item.get('actions', [])) for item in control.published),
        'physical_inputs': 0, 'fresh_game_captures': 0}


def run_case(modules, history, name, phase, warm_resources=None):
    real = name.startswith('q')
    trace, reads = modules.Trace(), []
    trace.phase, trace.read_kind = phase, 'real_worker_consumer' if real else 'declared_protocol'
    fixture = modules.compatibility.RuntimeCompatibilityTests()
    error = None
    historical = history['reward-sequence']
    if real:
        indices = [2] if name == 'q02_selection_request' else [0, 1]
        payloads = [historical['frames'][index] for index in indices]
        reader = modules.perception.Perception()
        if warm_resources:
            for key, value in warm_resources.items():
                setattr(reader, key, value)
        premise = ['Actual retained PNG and production Perception; inert immutable frame transport only.',
                   'Window starts at a legal reward consumer, not the missing two-orb action chain.']
        if name == 'q00_q01_original_completed':
            premise.append('The entire original close completed list is retained, including its coordinates and wait. '
                           'Requested actions may differ in both; no single-cause mismatch claim. '
                           'q01 is an inert archival successor, never a new single-orb receipt.')
    else:
        states = ([modules.contracts.values(coins=coins, level=8, xp=[0, 72])
                   for coins in (70, 68, 66, 64, 62, 60, 58)] if name == 'six_d_protocol' else
                  [modules.contracts.values(), modules.contracts.values(coins=62, xp=[44, 52]),
                   modules.contracts.values(coins=58, xp=[48, 52]),
                   modules.contracts.values(coins=54, level=8, xp=[0, 72])])
        rendered = [modules.contracts.numeric_frame(state) for state in states]
        payloads = [item[1] for item in rendered]
        reader = numeric_reader(modules, rendered, states)
        premise = ['Generated numeric pixels, declared HUD, five verified-empty shop slots and numeric lookup engine.',
            'No actual OCR or actual game success. Lookup seconds have their own component.',
            'Rewards/startup_guide/inventory_cleanup completion, current standard-mode budget and guide are fixture premises.',
            'Matched inert completed and immutable request IDs are generated; six D historical waits are not adapted silently.',
            'Initial strategy request AND terminal lineup strategy request are inside this audit window.']

    with fixture.manual_bridge_fixture() as (runtime, records, owner, control, unused):
        (runtime / 'runner-manual.json').unlink()
        worker = fixture.frame_worker(runtime, records, owner, control, reader)
        modules.preparation.ready_fixture(worker, modules.root)
        worker.state['control_mode'] = 'auto'
        worker.profile_wait_id, worker.profile_wait_kind = None, None
        if not real:
            worker.preparation_scope = ('match', '2-3', worker.epoch())
            worker.preparation_reviews = {key: {'completed': True} for key in modules.runner.coaching.PHASES[:3]}
            worker.live_mode = {'match_id': 'match', 'value': '标准博弈'}
            worker.knowledge = {'roles': {}, 'guide': {}, 'progression': {}}
        correlations = []

        def choose(value, mutations):
            completed, origin = value.get('actions', []), 'inert_observation_transport'
            if mutations:
                if name == 'q02_selection_request':
                    raise ValueError('q02 selection consumer must not publish an input')
                expected_key = 68 if name == 'six_d_protocol' else 70
                if name == 'q00_q01_original_completed':
                    if control.action_index or len(mutations) != 1 or mutations[0]['type'] != 'click':
                        raise ValueError('No independent historical single-orb successor; refusing publication')
                    completed = historical['manifest']['receipts'][0]['projection']['completed']
                    origin = 'original_archival_close_completed_on_inert_transport'
                else:
                    if mutations != [{'type': 'key', 'args': [float(expected_key)]}]:
                        raise ValueError('Protocol emitted a different economic action')
                    origin = 'inert_matched_protocol_receipt_not_historical_success'
                control.action_index += 1
                if control.action_index >= len(payloads):
                    raise ValueError('No retained/declared successor beyond the fixed case')
                if name == 'six_d_protocol':
                    archive = history['refresh-sequence']['manifest']['receipts'][control.action_index - 1]
                    correlations.append({'index': control.action_index,
                        'historical_completed': copy.deepcopy(archive['receipt']['completed']),
                        'generated_completed': copy.deepcopy(completed), 'delta_requested_wait_seconds': .3,
                        'generated_frame_is_historical_png': False})
            return control.action_index, completed, origin

        install_inert_publisher(modules, control, runtime, payloads, choose, trace)

        def flow():
            observed = worker.observe(scope='rewards' if name == 'q02_selection_request' else 'full')
            if real:
                worker.tick(observed)
                return
            worker.ask(observed, 'shop_strategy', 'Declared offline initial budget request; include its cost.',
                       category='strategy', business_step='economy_budget')
            request = worker.state['decision_request']
            plan = modules.contracts.plan()
            if name == 'six_d_protocol':
                # Same explicit six-D plan as the existing refresh replay.
                plan = modules.refresh.refresh_plan(rendered[0][2])
                worker.strategy_reads['guide'] = {'match_id': worker.active_match_id,
                    'resume_epoch': worker.epoch(), 'observed_at': modules.runner.now(),
                    'snapshot_id': observed['snapshot_id'],
                    'value': {'applied': True, 'body_read': True, 'mode_label': '标准博弈',
                              'body_lines': ['后期：8级搜牌'], 'operating_rules': {}}}
            record = {'value': {**plan, 'fields': rendered[0][2]},
                'proof': {'source': 'observed_screen', 'snapshot_id': request['snapshot_id'],
                          'evidence_file': request['evidence_file'], 'resume_epoch': worker.epoch()}}
            worker.execute_plan({'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                'resume_epoch': worker.epoch(), 'context_update': {'economy_plan': record},
                'actions': [{'type': 'finish_preparation_review', 'reason': 'Explicit inert protocol budget only.'}]})
            worker.tick(worker.last_observation)
            if worker.preparation_checklist(worker.last_observation)['phase'] == 'lineup_equipment':
                worker.tick(worker.last_observation)  # Include the actual full-field ROOT handoff.

        with instrument(modules, worker, control, reader, trace, real, reads):
            timed = trace.wrap(flow, 'worker_case')
            started = time.perf_counter()
            try:
                timed()
            except Exception as exc:
                error = {'type': type(exc).__name__, 'message': str(exc)}
            wall = time.perf_counter() - started
        final = endpoint(modules, worker, control)
        publications = copy.deepcopy(control.publication_trace)

    expected = ({'terminal_request_kind': 'reward_selection', 'phase': 'rewards', 'pending': False,
                 'normal_root_requests': 1, 'exception_root_requests': 0, 'simulated_mutation_publications': 0}
        if name == 'q02_selection_request' else
        {'terminal_request_kind': 'reward_result', 'phase': 'rewards', 'pending': True,
         'normal_root_requests': 0, 'exception_root_requests': 1, 'simulated_mutation_publications': 1}
        if name == 'q00_q01_original_completed' else
        {'terminal_request_kind': 'shop_strategy', 'phase': 'lineup_equipment', 'pending': False,
         'normal_root_requests': 2, 'exception_root_requests': 0,
         'simulated_mutation_publications': 6 if name == 'six_d_protocol' else 3,
         'economy_completed': True,
         'spent': {'purchase': 0, 'refresh': 12 if name == 'six_d_protocol' else 0,
                   'experience': 12 if name == 'three_f_protocol' else 0}})
    events = trace.finalized()
    cost = components(events)
    measured = events[0]['inclusive_seconds']
    checks = {key: final.get(key) == value for key, value in expected.items()}
    checks['terminal_full_contract'] = (final['observation'].get('read_contract') or {}).get('effective_scope') == 'full'
    checks['component_accounting'] = abs(sum(cost.values()) - measured) < 1e-9
    checks['no_unexpected_exception'] = error is None
    result = {'case': name, 'pass': phase, 'kind': 'real_png_worker_consumer' if real else 'numeric_protocol_worker_contract',
        'inference_temperature': phase if real else None, 'premises': premise,
        'status': 'pass' if all(checks.values()) else 'fail', 'checks': checks, 'error': error,
        'window': 'First Worker.observe through the terminal strategy request publication; initial ask included.',
        'worker_wall_seconds': wall, 'worker_method_seconds': measured, 'exclusive_components_seconds': cost,
        'accounting_residual_seconds': measured - sum(cost.values()),
        'game_animation_wait_seconds': None, 'supervisor_wait_seconds': None, 'supervisor_model_seconds': None,
        'reads': reads, 'events': events, 'endpoint': final, 'publication_trace': publications,
        'historical_correlations': correlations,
        'input_frame_sha256': [hashlib.sha256(payload).hexdigest() for payload in payloads],
        'warm_reuse': 'Fresh Perception/Worker; only prior initialized engine/shop_reader/state_reader inherited.' if real and phase == 'warm' else None,
        'cache_limit': 'Repeated bytes on different inert observe requests are a frozen historical fixture, not proof a live re-observation stays identical.',
        'method_counts': dict(Counter(event['method'] for event in events))}
    reusable = {key: getattr(reader, key, None) for key in ('engine', 'shop_reader', 'state_reader')} if real else None
    return result, reusable


def compare(previous, current):
    if previous['machine'] != current['machine'] or previous['dependencies'] != current['dependencies']:
        raise ValueError('Comparison requires the same reported runtime signature')
    rows = []
    prior = {(case['case'], case['pass']): case for case in previous['cases']}
    for after in current['cases']:
        before = prior[(after['case'], after['pass'])]
        if before['input_frame_sha256'] != after['input_frame_sha256']:
            raise ValueError('Comparison input sequence differs')
        keys = ('phase', 'pending', 'terminal_request_kind', 'normal_root_requests',
                'exception_root_requests', 'simulated_mutation_publications', 'spent', 'economy_completed')
        semantic_keys = ('snapshot_id', 'page', 'fields', 'coins', 'rewards', 'team_checked',
                         'occupied_board_slots', 'named_board_units', 'star_read_board_units',
                         'name_and_star_read_board_units')
        semantic_differences = [key for key in semantic_keys
            if before['endpoint']['observation'].get(key) != after['endpoint']['observation'].get(key)]
        def request_sequence(case):
            return [(request['kind'], (request.get('return_reason') or {}).get('category'),
                     (request.get('return_reason') or {}).get('business_step'))
                    for request in case['endpoint']['requests']]
        request_sequence_matches = request_sequence(before) == request_sequence(after)
        def reward_pending_state(case):
            pending = case['endpoint'].get('reward_pending')
            return {key: pending.get(key) for key in ('status', 'outcome')} if pending else None
        reward_pending_matches = reward_pending_state(before) == reward_pending_state(after)
        both_pass = before['status'] == after['status'] == 'pass'
        same = (both_pass and all(before['endpoint'][key] == after['endpoint'][key] for key in keys)
                and not semantic_differences and request_sequence_matches and reward_pending_matches)
        rows.append({'case': after['case'], 'pass': after['pass'], 'same_endpoint': same,
            'both_cases_pass': both_pass, 'request_sequence_matches': request_sequence_matches,
            'reward_pending_status_outcome_matches': reward_pending_matches,
            'required_endpoint_semantic_differences': semantic_differences,
            'before_status': before['status'], 'after_status': after['status'],
            'before_worker_wall_seconds': before['worker_wall_seconds'],
            'after_worker_wall_seconds': after['worker_wall_seconds'],
            'delta_seconds': after['worker_wall_seconds'] - before['worker_wall_seconds'] if same else None,
            'before_components': before['exclusive_components_seconds'],
            'after_components': after['exclusive_components_seconds']})
    return {'rows': rows, 'no_speed_ratio_for_different_endpoint': True,
            'limit': 'One cold/warm pass per fixed case; no statistical speed guarantee, no real full-stage or game timing.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compare-to', type=Path)
    parser.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    args = parser.parse_args()
    root = args.code_root.resolve()
    before = sources(root)
    history = load_history(root)
    modules = configure(root)
    results = []
    for name in args.cases:
        resources = None
        for phase in ('cold', 'warm'):
            case, resources = run_case(modules, history, name, phase, resources)
            results.append(case)
    after = sources(root)
    report = {'schema': 'currency-wars-worker-consumer-cost/v1', 'offline_only': True,
        'physical_inputs': 0, 'fresh_game_captures': 0,
        'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'source_hashes': before, 'source_hashes_after': after, 'sources_unchanged': before == after,
        'benchmark_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'machine': {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version()},
        'dependencies': {name: importlib.metadata.version(name) for name in
                         ('onnxruntime', 'rapidocr-onnxruntime', 'numpy', 'Pillow', 'opencv-python')},
        'dependency_import_seconds': modules.dependency_import_seconds,
        'accounting_contract': 'Inclusive method intervals are nested. Exclusive intervals subtract immediate children '
            'and are assigned once. Worker rules/strategy includes its remaining inline bookkeeping; wrapped save/write '
            'and transport work goes to other_read_entry_io. That remainder also includes image decode/hash and uninstrumented '
            'local work. Inert publication is local receipt/frame writing, never game execution or animation. '
            'Protocol semantic/numeric lookup has its own component, never OCR. Engine init is inside cold case total, '
            'listed separately and never added twice. Imports and fixture construction precede case windows.',
        'cases': results,
        'limits': ['Full real seven-D Worker loop is NOT measured: public resources and a confirmed current numeric ROI/budget binding are incomplete.',
            'Private resource absence here does not contradict ROOT local resources or prior seven-shop recognition.',
            'No two-orb intermediate successor exists; q02 is only a terminal consumer window.',
            'Original close completed remains original; a transport wrapper does not make it a successful current receipt.',
            'Protocol normal ROOT count 2 includes initial budget plus terminal lineup request; old replay count 1 used a shorter window.',
            'Requested wait parameters are metadata, not measured physical waits. Tool-outside history remains unattributed.']}
    if args.compare_to:
        report['comparison'] = compare(json.loads(args.compare_to.read_text(encoding='utf8')), report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'output': str(args.output), 'sources_unchanged': report['sources_unchanged'],
        'cases': [{'case': item['case'], 'pass': item['pass'], 'status': item['status'],
                   'endpoint': item['endpoint']['terminal_request_kind'],
                   'worker_wall_seconds': item['worker_wall_seconds']} for item in results]}, ensure_ascii=False))
    if before != after or any(item['status'] != 'pass' for item in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
