"""Replay the retained six-D evidence without capture, input or deployment.

The PNG/receipt audit and the generated-number Worker contract are separate.
The former never receives invented confidence, shop completeness or reviews.
The latter reuses the existing inert EconomyTests/Entry fixture, explicitly
adapting archival D + wait(.4) to the current D + wait(.7) protocol. Its generated
receipts are not the original historical receipts and cannot authorize input.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
from unittest.mock import patch


SCHEMA = 'currency-wars-refresh-replay/v1'
TIMING_CATEGORIES = ('recognition', 'rules', 'publication_simulation', 'entry_io_unattributed')


def configure(code_root, *, real_ocr=False):
    code_root = Path(code_root).resolve()
    for name in ('currency_wars_runner', 'test_currency_wars_economy'):
        loaded = sys.modules.get(name)
        if loaded and Path(loaded.__file__).resolve().parent != code_root / 'tools':
            raise ValueError('A different code root is already imported; compare in separate processes')
    if real_ocr:
        if 'onnxruntime' in sys.modules:
            raise ValueError('Use a fresh process to disable ORT telemetry before import')
        os.environ['ORT_DISABLE_TELEMETRY'] = '1'
        import onnxruntime
        onnxruntime.disable_telemetry_events()
    sys.path.insert(0, str(code_root / 'tools'))
    global runner, compatibility, contracts, preparation_replay
    import currency_wars_runner as runner
    import test_local_runtime_compatibility as compatibility
    import test_currency_wars_economy as contracts
    import replay_currency_wars_preparation as preparation_replay
    # Reuse its bookkeeping-only helper without rerunning ORT initialization.
    preparation_replay.runner = runner


class ExclusiveTiming:
    """Attribute nested calls once; no clock, broker or controller changes."""
    def __init__(self):
        self.step = 'setup'
        self.stack = []
        self.previous = time.perf_counter()
        self.seconds = defaultdict(lambda: defaultdict(float))

    def mark(self):
        current = time.perf_counter()
        if self.stack:
            self.seconds[self.step][self.stack[-1]] += current - self.previous
        self.previous = current

    def set_step(self, step):
        self.mark()
        self.step = step

    @contextlib.contextmanager
    def span(self, category):
        self.mark()
        self.stack.append(category)
        try:
            yield
        finally:
            self.mark()
            self.stack.pop()

    def wrap(self, callback, category):
        def timed(*args, **kwargs):
            with self.span(category):
                return callback(*args, **kwargs)
        return timed

    def report(self):
        steps = []
        for step, values in self.seconds.items():
            row = {name: values.get(name, 0.) for name in TIMING_CATEGORIES}
            row.update(step=step, active_total=sum(row.values()), game_animation_wait=None,
                       supervisor_wait=None, scope='offline_in_process_only')
            steps.append(row)
        return {'steps': steps,
                'exclusive_totals': {name: sum(row[name] for row in steps) for name in TIMING_CATEGORIES},
                'active_total': sum(row['active_total'] for row in steps),
                'game_animation_wait': None, 'supervisor_wait': None,
                'contract': 'Deepest active category owns each measured interval once. '
                    'Rules includes existing policy/guard/effect calls; uninstrumented Entry/file work remains '
                    'entry_io_unattributed. Historical waits and helper timings are separate, never added. '
                    'No physical waits or supervisor waiting are simulated.'}


def load_evidence(directory):
    directory = Path(directory).resolve()
    manifest_bytes = (directory / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    frames, receipts = manifest['frames'], manifest['receipts']
    if (manifest.get('offline_only') is not True or len(frames) != 7 or len(receipts) != 6
            or [frame.get('index') for frame in frames] != list(range(7))):
        raise ValueError('Expected the exact seven-frame/six-receipt offline sequence')
    payloads = []
    for frame in frames:
        payload = (directory / frame['file']).read_bytes()
        if hashlib.sha256(payload).hexdigest() != frame['export_image_sha256']:
            raise ValueError('Retained PNG hash mismatch: ' + frame['file'])
        payloads.append(payload)
    ids = set()
    for index, item in enumerate(receipts, 1):
        original = item['receipt']
        if (item['index'] != index or item['before_frame_index'] != index - 1
                or item['after_frame_index'] != index or original.get('ok') is not True
                or original.get('completed') != [{'type': 'key', 'args': [68]}, {'type': 'wait', 'args': [0.4]}]
                or original.get('id') in ids):
            raise ValueError('Unexpected or duplicated archival receipt')
        ids.add(original['id'])
    return manifest, payloads, hashlib.sha256(manifest_bytes).hexdigest()


def receipt_audit(manifest):
    """Classify originals against an explicitly hypothetical current request."""
    results = []
    for item in manifest['receipts']:
        original = copy.deepcopy(item['receipt'])
        request = {'id': original['id'], 'kind': 'actions', 'handoff': False,
                   'actions': [{'type': 'key', 'args': [68]}, {'type': 'wait', 'args': [.7]}]}
        classified = runner.manual_receipt_state({'id': original['id'], 'request': request, 'result': original})
        results.append({'index': item['index'], 'original_receipt': original,
            'original_receipt_sha256': item['original_receipt_sha256'],
            'original_hash_verification': 'Original unredacted receipt bytes were not exported; '
                'the supplied source hash is provenance, not independently recomputed here.',
            'hypothetical_current_request_actions': request['actions'],
            'exact_current_protocol_delivery': classified,
            'delta_requested_wait_seconds': .3,
            'historical_input_identity': 'One completed D68; original wait parameter .4 retained verbatim.',
            'timing_source': copy.deepcopy(item['profile_event']),
            'live_authorization': False})
    return results


def read_retained_pngs(manifest, payloads, code_root):
    """Real Perception through immutable-frame Entry, with no business context."""
    from currency_wars_perception import Perception
    reader = Perception()
    timer = ExclusiveTiming()
    results = []
    with patch.object(reader, 'read', timer.wrap(reader.read, 'recognition')):
        for index, (metadata, payload) in enumerate(zip(manifest['frames'], payloads)):
            fixture = compatibility.RuntimeCompatibilityTests()
            with fixture.manual_bridge_fixture() as (runtime, records, owner, control, unused):
                (runtime / 'runner-manual.json').unlink()
                control.frame = payload
                original_publish = control.publish_request
                def publish(value):
                    if any(action['type'] not in ('observe', 'wait') for action in value.get('actions', [])):
                        raise ValueError('Real-PNG layer is observation only; no successor is invented')
                    return original_publish(value)
                control.publish_request = timer.wrap(publish, 'publication_simulation')
                worker = fixture.frame_worker(runtime, records, owner, control, reader)
                preparation_replay.ready_fixture(worker, code_root)
                timer.set_step('historical_shop_frame_' + str(index))
                with timer.span('entry_io_unattributed'):
                    observed = worker.observe()
                    with timer.span('rules'):
                        checklist = worker.preparation_checklist(observed)
                team = observed.get('semantic', {}).get('team') or {}
                shop = observed.get('shop') or {}
                coins = observed.get('semantic', {}).get('coins') or {}
                results.append({'index': index, 'snapshot_sha256': observed['snapshot_id'],
                    'engine_cold': index == 0, 'page': observed['page'], 'fields': observed['fields'],
                    'coins': coins, 'shop_ok': shop.get('ok'), 'shop_status': shop.get('status'),
                    'shop_errors': shop.get('errors'),
                    'shop_slots': [{key: slot.get(key) for key in ('slot', 'name', 'cost', 'status', 'reasons')}
                                   for slot in shop.get('slots', [])],
                    'team_fully_read': team.get('fully_read'), 'team_checked': team.get('checked'),
                    'known_board_units': sum(unit.get('location') == 'board' for unit in team.get('units', [])),
                    'perception_reported_elapsed_ms': observed.get('elapsed_ms'),
                    'phase': checklist['phase'], 'phase_completed': [], 'policy_dispatched': False,
                    'normal_root_requests': 0, 'exception_root_requests': 0,
                    'request_count_scope': 'No Worker.tick or synthetic preparation/strategy review in this read-only layer.',
                    'references_match': {'coins': coins.get('value') == metadata['recorded_coins']['value'],
                        'player_level': str(observed['fields'].get('level')) == str(metadata['expected_player_level']),
                        'stage': observed['fields'].get('stage') == metadata['expected_stage']},
                    'physical_inputs': 0, 'fresh_game_captures': 0})
    deltas = []
    for item in manifest['receipts']:
        before, after = results[item['before_frame_index']], results[item['after_frame_index']]
        a, b = before['coins'].get('value'), after['coins'].get('value')
        delta = a - b if type(a) is int and type(b) is int else None
        # Source observations contain no verified free-refresh binding or full
        # transaction authorization. Even exact coin deltas do not fill those.
        outcome = runner.economy.classify_effect('refresh',
            {'values': {'coins': a} if type(a) is int else {}},
            {'values': {'coins': b} if type(b) is int else {}}, expected_cost=2)
        deltas.append({'index': item['index'], 'independently_read_coin_delta': delta,
            'historical_recorded_delta': item['recorded_coin_delta'], 'strict_effect_result': outcome,
            'original_completed': copy.deepcopy(item['receipt']['completed']),
            'unsupported_fields': ['current budget/reviewer', 'free_refreshes binding',
                                   'five-slot transaction proof', 'prior preparation phases']})
    return {'kind': 'real_retained_png_ocr_no_injected_business_facts', 'frames': results,
        'coin_effects': deltas,
        'independently_read_total_coin_spent': sum(row['independently_read_coin_delta'] for row in deltas)
            if all(type(row['independently_read_coin_delta']) is int for row in deltas) else None,
        'timing': timer.report(), 'completed_preparation_phases': [],
        'endpoint': 'Seven archived observations only; no current authorization for any preparation phase.'}


def refresh_plan(fields):
    return {**contracts.plan(), 'stage': '2-3',
        'reason': 'Explicit offline contract: six paid D calls, then stop with a five-coin purchase reserve.',
        'fields': fields,
        'targets': [{'name': '飞霄', 'copies': 1, 'critical': False,
                     'reason': 'Synthetic protocol target absent from these generated shop slots.'}],
        'budget': {'purchase': 5, 'refresh': 12, 'experience': 0},
        'paid_search': {'max_refreshes': 6, 'purchase_reserve': 5, 'targets': ['飞霄'],
            'reason': 'Offline fixture budget; not a retained historical strategy approval.',
            'stop_conditions': ['budget_exhausted', 'max_refreshes', 'purchase_reserve', 'target_acquired']},
        'experience': {'target_level': 8, 'critical': False, 'reason': 'No F in this six-D contract.'}}


def replay_protocol(manifest, *, original_completed=False):
    """Actual Worker.tick/Entry, generated frames; archival actions only correlate."""
    coin_sequence = [frame['recorded_coins']['value'] for frame in manifest['frames']]
    if coin_sequence != [70, 68, 66, 64, 62, 60, 58]:
        raise ValueError('Six-D source amount sequence changed; review the contract explicitly')
    states = [contracts.values(coins=coins, level=8, xp=[0, 72]) for coins in coin_sequence]
    timer = ExclusiveTiming()
    correlations = []
    with contracts.EconomyTests().worker(states[1:], initial=states[0]) as (worker, record, control, frames):
        # Deliberate fixture premises, kept out of the real-PNG layer above.
        record['value'] = refresh_plan(frames[0][2])
        worker.strategy_reads['guide'] = {'match_id': worker.active_match_id, 'resume_epoch': worker.epoch(),
            'observed_at': runner.now(), 'snapshot_id': worker.last_observation['snapshot_id'],
            'value': {'applied': True, 'body_read': True, 'mode_label': '标准博弈',
                      'body_lines': ['后期：8级搜牌'], 'operating_rules': {}}}
        worker.panel_index, worker.panel_state = 0, 'enter'
        worker.node_key, worker.node_attempts, worker.node_started = None, 0, None
        worker.node_last_page, worker.node_consecutive = None, 0
        worker.node_progress, worker.node_progress_seen = 0, 0
        worker.wait_page, worker.wait_started, worker.shop_stages = None, None, set()
        original_publish = control.publish_request
        def publish(value):
            physical = [a for a in value.get('actions', []) if a['type'] not in ('observe', 'wait')]
            if physical:
                index = len(correlations)
                if index >= len(manifest['receipts']):
                    raise ValueError('No archival successor beyond six D calls; inert fixture refuses')
                original = manifest['receipts'][index]['receipt']
                archived_physical = [a for a in original['completed'] if a['type'] not in ('observe', 'wait')]
                if physical != archived_physical:
                    raise ValueError('Generated Worker physical action differs from archival D68')
                correlations.append({'index': index + 1,
                    'origin': 'archival_completed_on_inert_transport' if original_completed else 'inert_protocol_adapter',
                    'historical_receipt_id': original['id'], 'generated_request_id': value['id'],
                    'historical_completed': copy.deepcopy(original['completed']),
                    'requested_actions': copy.deepcopy(value['actions']),
                    'generated_completed': copy.deepcopy(original['completed'] if original_completed else value['actions']),
                    'delta_requested_wait_seconds': .3,
                    'before_historical_sha256': manifest['frames'][index]['export_image_sha256'],
                    'after_historical_sha256': manifest['frames'][index + 1]['export_image_sha256'],
                    'generated_frame_is_historical_png': False})
            original_publish(value)
            if physical:
                result_path = worker.run / 'result.json'
                result = json.loads(result_path.read_text(encoding='utf-8'))
                if original_completed:
                    # Deliberately keep the historical .4 completed verbatim.
                    # Frame IDs/PNG remain an inert transport fixture, not the
                    # unexported original ownership/frame protocol.
                    result['completed'] = copy.deepcopy(original['completed'])
                result['receipt_origin'] = correlations[-1]['origin']
                result['historical_correlation_only'] = copy.deepcopy(correlations[-1])
                control.write_json(result_path, result)

        original_execute = worker.execute_economic_action
        def execute(*args, **kwargs):
            timer.set_step('paid_refresh_' + str(len(correlations) + 1))
            result = original_execute(*args, **kwargs)
            timer.set_step('paid_refresh_' + str(len(correlations) + 1)
                           if len(correlations) < 6 else 'economy_completion')
            return result

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(control, 'publish_request', timer.wrap(publish, 'publication_simulation')))
            stack.enter_context(patch.object(worker.perception, 'read', timer.wrap(worker.perception.read, 'recognition')))
            stack.enter_context(patch.object(worker.perception, 'engine', timer.wrap(worker.perception.engine, 'recognition')))
            stack.enter_context(patch.object(worker, 'economy_observation', timer.wrap(worker.economy_observation, 'recognition')))
            stack.enter_context(patch.object(worker, 'execute_economic_action', execute))
            for name in ('accept_economy_plan', 'economic_policy', 'guard_preparation_action',
                         'require_economic_action', 'finish_local_economy'):
                stack.enter_context(patch.object(worker, name, timer.wrap(getattr(worker, name), 'rules')))
            stack.enter_context(patch.object(runner.economy, 'classify_effect',
                                            timer.wrap(runner.economy.classify_effect, 'rules')))
            timer.set_step('fixture_budget_binding')
            with timer.span('entry_io_unattributed'):
                request = worker.state['decision_request']
                worker.execute_plan({'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                    'resume_epoch': worker.epoch(), 'context_update': {'economy_plan': record},
                    'actions': [{'type': 'finish_preparation_review', 'reason': 'Explicit inert contract budget only.'}]})
                timer.set_step('paid_refresh_1')
                worker.tick(worker.last_observation)
        count_before_retry = len(control.published)
        if original_completed:
            worker.advance_economy(worker.last_observation)
        no_replay = len(control.published) == count_before_retry
        ledger = worker.economy_ledger('2-3')
        observed_coins = worker.economy_observation(worker.last_observation)['values'].get('coins')
        cached_acknowledgement = None
        if original_completed:
            cached_acknowledgement = {'attempted': False,
                'reason': 'No pending remains in this source revision; do not invent one to claim historical coverage.'}
            if ledger['pending']:
                pending = copy.deepcopy(ledger['pending'])
                request = worker.state['decision_request']
                resolution = copy.deepcopy(record)
                resolution['proof'].update(snapshot_id=request['snapshot_id'], evidence_file=request['evidence_file'])
                resolution['value'].update(resolve_request_id=pending['request_id'],
                    fields=frames[control.action_index][2], revision=2)
                before_bytes = worker.economy_ledger_path('2-3').read_bytes()
                before_ledger = copy.deepcopy(ledger)
                count = len(control.published)
                error = None
                try:
                    # Exercise the actual public acknowledgement path with
                    # cached after_png AND receipt. Neither cache permits a
                    # contradictory original completed to clear the pending.
                    worker.accept_economy_plan(resolution)
                except ValueError as exc:
                    error = str(exc)
                cached_acknowledgement = {'attempted': True, 'entrypoint': 'Worker.accept_economy_plan',
                    'cached_after_png_present': bool(pending.get('after_png') and Path(pending['after_png']).exists()),
                    'cached_receipt_present': bool(pending.get('receipt')),
                    'original_completed': copy.deepcopy(pending.get('receipt', {}).get('completed')),
                    'requested_actions': copy.deepcopy(pending['broker_actions']),
                    'rejected': error is not None, 'reason': error,
                    'observed_coin_change': observed_coins - coin_sequence[0],
                    'formally_reconciled_spent': copy.deepcopy(ledger['spent']),
                    'pending_request_before': pending['request_id'],
                    'pending_request_after': ledger['pending'].get('request_id') if ledger['pending'] else None,
                    'in_memory_ledger_unchanged': ledger == before_ledger,
                    'durable_ledger_unchanged': worker.economy_ledger_path('2-3').read_bytes() == before_bytes,
                    'no_new_publication': len(control.published) == count,
                    'fixture_scope': 'Current source only; explicit same-frame supervisor budget acknowledgement '
                        'over the existing generated numeric/Entry fixture, not a retained historical ROOT reply.',
                    'timing_included': False}
        requests = [event['request']['kind'] for event in worker.log_events if event.get('event') == 'strategy_request']
        effects = [{key: event.get(key) for key in ('request_id', 'kind', 'outcome', 'observed_spent', 'actual_spent', 'input_resent')}
                   for event in worker.log_events if event.get('event') == 'economic_effect_verified']
        report = {'kind': 'generated_number_worker_entry_contract_correlated_to_archival_six_d',
            'normal_root_requests': sum(kind != 'economy_result' for kind in requests),
            'exception_root_requests': requests.count('economy_result'), 'request_kinds': requests,
            'initial_root_request': 'Existing EconomyTests fixture calls Worker.ask before timed budget binding.',
            'spent': copy.deepcopy(ledger['spent']), 'paid_refreshes': ledger['paid_refreshes'],
            'observed_coin_change': observed_coins - coin_sequence[0] if type(observed_coins) is int else None,
            'observed_coin_spent': coin_sequence[0] - observed_coins if type(observed_coins) is int else None,
            'accounting_note': 'spent is the formally reconciled ledger. Observed coin change is retained '
                'separately; a zero ledger with pending does not mean the game charged nothing.',
            'pending': bool(ledger['pending']), 'economy_completed': 'economy' in worker.preparation_reviews,
            'pending_request_id': ledger['pending'].get('request_id') if ledger['pending'] else None,
            'cached_acknowledgement': cached_acknowledgement,
            'simulated_input_count': len(correlations), 'no_replay_on_next_advance': no_replay,
            'next_phase': worker.preparation_checklist(worker.last_observation)['phase'],
            'battle_ready': worker.preparation_checklist(worker.last_observation)['battle_ready'],
            'phase_coverage': {'tested': ['economy'], 'fixture_preconditions_only': list(runner.coaching.PHASES[:3]),
                               'not_tested': list(runner.coaching.PHASES[4:]), 'real_complete_preparation_count': 0},
            'fixture_preconditions': ['Synthetic stage 2-3 in the existing EconomyTests fixture; historical label is 3-4.',
                'Fixture marks rewards/startup_guide/inventory_cleanup complete; no historical proof of these phases.',
                'Synthetic standard-mode budget 5 purchase / 12 refresh / 0 experience; reserve 50.',
                'Synthetic applied guide body says level 8 reroll; no actual historical guide authorization exported.',
                'Generated numeric frames bind level 8, free_refreshes 0, refresh_cost 2, XP fields.',
                'Generated shop is five verified-empty slots; the real seven shops contain cards, '
                'and ROOT reported readable local slots separately. This does not verify real shop recognition.',
                'No team.checked/fully_read is injected; no purchase occurs.',
                'Synthetic immutable frame IDs/current epochs and matched receipts exist only in a temporary inert runtime.',
                ('Original completed wait(.4) is inserted verbatim into an inert current wait(.7) request; '
                 'this is the exact mismatch negative, never a successful historical transport replay.'
                 if original_completed else
                 'Wait(.7) is a generated protocol acknowledgement; the archival receipt records wait(.4).')],
            'generated_frame_sequence': [hashlib.sha256(frame[1]).hexdigest() for frame in frames],
            'historical_frame_sequence': [frame['export_image_sha256'] for frame in manifest['frames']],
            'correlations': correlations, 'effects': effects, 'timing': timer.report(),
            'physical_inputs': 0, 'fresh_game_captures': 0,
            'receipt_origin': 'archival_completed_on_inert_transport' if original_completed else 'inert_protocol_adapter'}
        return report


def compare_reports(previous, current):
    if previous['manifest_sha256'] != current['manifest_sha256']:
        raise ValueError('Comparison requires the same retained manifest')
    old, new = previous['protocol'], current['protocol']
    if old['generated_frame_sequence'] != new['generated_frame_sequence'] or old['historical_frame_sequence'] != new['historical_frame_sequence']:
        raise ValueError('Comparison requires identical generated and archival frame sequences')
    old_negative, new_negative = previous['protocol_original_completed'], current['protocol_original_completed']
    return {'before_source': previous['source'], 'after_source': current['source'],
        'protocol': {'normal_root_requests': [old['normal_root_requests'], new['normal_root_requests']],
            'exception_root_requests': [old['exception_root_requests'], new['exception_root_requests']],
            'refresh_spent': [old['spent']['refresh'], new['spent']['refresh']],
            'next_phase': [old['next_phase'], new['next_phase']],
            'offline_active_seconds': [old['timing']['active_total'], new['timing']['active_total']]},
        'original_completed_negative': {
            'normal_root_requests': [old_negative['normal_root_requests'], new_negative['normal_root_requests']],
            'exception_root_requests': [old_negative['exception_root_requests'], new_negative['exception_root_requests']],
            'simulated_inputs': [old_negative['simulated_input_count'], new_negative['simulated_input_count']],
            'formally_reconciled_refresh_spent': [old_negative['spent']['refresh'], new_negative['spent']['refresh']],
            'observed_coin_change': [old_negative['observed_coin_change'], new_negative['observed_coin_change']],
            'pending': [old_negative['pending'], new_negative['pending']],
            'next_phase': [old_negative['next_phase'], new_negative['next_phase']],
            'no_replay_on_next_advance': [old_negative['no_replay_on_next_advance'], new_negative['no_replay_on_next_advance']]},
        'real_ocr_seconds': [previous.get('real_png', {}).get('timing', {}).get('active_total'),
                             current.get('real_png', {}).get('timing', {}).get('active_total')],
        'real_ocr_source': [previous.get('real_png', {}).get('source', previous['source']),
                            current.get('real_png', {}).get('source', current['source'])],
        'same_machine': previous['machine'] == current['machine'],
        'speed_claim': 'No speedup inferred from single passes, generated protocol timings, '
            'or different historical sequences; this records the same-machine same-sequence comparison.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--before', type=Path)
    parser.add_argument('--skip-real-ocr', action='store_true')
    args = parser.parse_args()
    configure(args.code_root, real_ocr=not args.skip_real_ocr)
    manifest, payloads, digest = load_evidence(args.evidence)
    source_names = ('currency_wars_runner.py', 'currency_wars_economy.py', 'currency_wars_perception.py',
                    'currency_wars_profile.py', 'currency_wars_broker_entry.py')
    report = {'schema': SCHEMA, 'offline_only': True, 'physical_inputs': 0, 'fresh_game_captures': 0,
        'manifest_sha256': digest,
        'machine': {'system': platform.system(), 'machine': platform.machine(), 'node': platform.node(),
                    'python': platform.python_version()},
        'source': {name: hashlib.sha256((args.code_root / 'tools' / name).read_bytes()).hexdigest() for name in source_names},
        'source_historical_core_sha': manifest['core_source_sha'],
        'archival_receipts': receipt_audit(manifest)}
    if not args.skip_real_ocr:
        report['real_png'] = read_retained_pngs(manifest, payloads, args.code_root)
    report['protocol'] = replay_protocol(manifest)
    report['protocol_original_completed'] = replay_protocol(manifest, original_completed=True)
    if args.before:
        report['comparison'] = compare_reports(json.loads(args.before.read_text(encoding='utf-8')), report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'protocol_root_normal': report['protocol']['normal_root_requests'],
        'protocol_root_exception': report['protocol']['exception_root_requests'], 'protocol_spent': report['protocol']['spent'],
        'endpoint': report['protocol']['next_phase'], 'original_receipts_current_protocol_unknown':
            sum(item['exact_current_protocol_delivery']['unknown_input'] for item in report['archival_receipts']),
        'original_completed_negative': {key: report['protocol_original_completed'][key] for key in
            ('simulated_input_count', 'spent', 'observed_coin_change', 'pending', 'exception_root_requests')},
        'physical_inputs': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
