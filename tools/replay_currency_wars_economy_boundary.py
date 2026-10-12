"""Offline audit of the actual tick -> economy -> ROOT handoff boundary.

All game facts in this driver are explicit numeric/empty-shop fixtures. It does
not read game images, construct a controller, run a broker, or call a model.
The fixture's setup request is discarded BEFORE the audit starts. Initial and
terminal requests below are produced by Worker.tick, never labelled by hand.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch


CASES = ('missing_budget', 'missing_free_refresh', 'policy_unavailable',
         'pending_result', 'completion_guard', 'six_d', 'three_f')


def configure(root):
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    root = Path(root).resolve()
    sys.path.insert(0, str(root / 'tools'))
    import currency_wars_runner as runner
    import currency_wars_profile as profile
    import test_currency_wars_economy as contracts
    import test_currency_wars_perception_scope as scopes
    import replay_currency_wars_refresh as refresh
    refresh.contracts = contracts
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    return SimpleNamespace(root=root, revision=revision, runner=runner, profile=profile,
                           contracts=contracts, scopes=scopes, refresh=refresh)


def sources(root):
    names = ('currency_wars_runner.py', 'currency_wars_profile.py',
             'currency_wars_economy.py', 'currency_wars_perception.py',
             'currency_wars_broker_entry.py', 'test_currency_wars_economy.py',
             'test_currency_wars_perception_scope.py')
    return {name: hashlib.sha256((root / 'tools' / name).read_bytes()).hexdigest()
            for name in names}


def tick_bookkeeping(worker):
    """Only process bookkeeping; the existing fixture declares phase facts."""
    worker.panel_index, worker.panel_state = 0, 'enter'
    worker.node_key, worker.node_attempts, worker.node_started = None, 0, None
    worker.node_last_page, worker.node_consecutive = None, 0
    worker.node_progress, worker.node_progress_seen = 0, 0
    worker.wait_page, worker.wait_started, worker.shop_stages = None, None, set()
    worker.profile_wait_id, worker.profile_wait_kind = None, None
    worker.state['decision_request'] = None
    worker.state['control_mode'] = 'auto'
    worker.state['statistics']['decisions'] = 0
    worker.log_events.clear()


def endpoint(modules, worker, control, reads, policy_calls):
    observed = worker.last_observation
    requests = [event['request'] for event in worker.log_events
                if event.get('event') == 'strategy_request']
    ledger = worker.economy_ledger('2-3')
    pending = ledger['pending']
    checklist = worker.preparation_checklist(observed)
    request = worker.state.get('decision_request') or {}
    comparable_requests = []
    for item in requests:
        reason = copy.deepcopy(item['return_reason'])
        pending_reference_verified = None
        if item['kind'] == 'economy_result' and pending:
            original_id = pending['request_id']
            pending_reference_verified = reason['reason'].count(original_id) == 1
            if not pending_reference_verified:
                raise AssertionError('Exception request does not identify its actual pending transaction')
            # Only this exact generated receipt identity differs between clones.
            # Raw requests and original IDs remain in the profile evidence.
            reason['reason'] = reason['reason'].replace(original_id, '{pending_request_id}')
        comparable_requests.append({'kind': item['kind'], 'snapshot_id': item['snapshot_id'],
                                    'return_reason': reason,
                                    'pending_reference_verified': pending_reference_verified})
    return {'phase': checklist['phase'], 'battle_ready': checklist['battle_ready'],
            'economy_completed': worker.preparation_reviews.get('economy', {}).get('completed') is True,
            'spent': copy.deepcopy(ledger['spent']), 'pending': bool(pending),
            'pending_kind': pending.get('kind') if pending else None,
            'terminal_request_kind': request.get('kind'),
            'requests': comparable_requests,
            'normal_root_requests': sum(item['return_reason']['category'] != 'exception' for item in requests),
            'exception_root_requests': sum(item['return_reason']['category'] == 'exception' for item in requests),
            'unclassified_root_requests': sum(item['return_reason']['category'] == 'unclassified' for item in requests),
            'simulated_keys': [action['args'] for item in control.published
                               for action in item.get('actions', []) if action['type'] == 'key'],
            'perception_read_count': len(reads), 'economic_policy_call_count': policy_calls,
            'team_checked': (observed.get('semantic', {}).get('team') or {}).get('checked'),
            'team_fully_read': (observed.get('semantic', {}).get('team') or {}).get('fully_read')}


def run_case(modules, name, *, enabled=True, profile_hook=None):
    if name not in CASES:
        raise ValueError('Unknown offline case: ' + str(name))
    values = modules.contracts.values
    initial, transitions = values(), []
    if name == 'six_d':
        states = [values(coins=coins, level=8, xp=[0, 72])
                  for coins in (70, 68, 66, 64, 62, 60, 58)]
        initial, transitions = states[0], states[1:]
    elif name == 'three_f':
        transitions = [values(coins=62, xp=[44, 52]), values(coins=58, xp=[48, 52]),
                       values(coins=54, level=8, xp=[0, 72])]
    elif name == 'pending_result':
        transitions = [values()]

    with modules.contracts.EconomyTests().worker(transitions, initial=initial) as (worker, unused, control, frames):
        tick_bookkeeping(worker)
        reads, unused_attempts = modules.scopes.record_fixture_reads(worker)
        recorder = modules.profile.ProfileRecorder(worker.records, run_id='offline-economy-boundary',
            enabled=enabled, source='worker', source_sha=modules.revision,
            comparison_key='declared-natural-economy-boundary-v1')
        worker.profile = recorder
        recorder.set_context(match_id=worker.active_match_id, stage='2-3',
                             resume_epoch=worker.epoch(), phase='economy')
        policy_calls = 0
        original_policy = worker.economic_policy

        def counted_policy(*args, **kwargs):
            nonlocal policy_calls
            policy_calls += 1
            return original_policy(*args, **kwargs)

        worker.economic_policy = counted_policy
        hook = profile_hook(recorder) if profile_hook else contextlib.nullcontext()
        with hook:
            worker.tick(worker.last_observation)  # Natural initial budget exit.
            initial_request = worker.state['decision_request']
            if initial_request is None:
                raise AssertionError('Fixture must reach the natural unbound-budget request')
            if name != 'missing_budget':
                plan = modules.contracts.plan()
                if name == 'six_d':
                    plan = modules.refresh.refresh_plan(frames[0][2])
                    worker.strategy_reads['guide'] = {'match_id': worker.active_match_id,
                        'resume_epoch': worker.epoch(), 'observed_at': modules.runner.now(),
                        'snapshot_id': worker.last_observation['snapshot_id'],
                        'value': {'applied': True, 'body_read': True, 'mode_label': '标准博弈',
                                  'body_lines': ['后期：8级搜牌'], 'operating_rules': {}}}
                plan['fields'] = copy.deepcopy(frames[0][2])
                if name == 'missing_free_refresh':
                    plan['fields']['free_refreshes'] = None
                if name == 'completion_guard':
                    plan['budget']['experience'] = 0
                    plan['fields'] = {key: plan['fields'][key] for key in ('coins', 'free_refreshes')}
                record = {'value': plan, 'proof': {'source': 'observed_screen',
                    'snapshot_id': initial_request['snapshot_id'], 'evidence_file': initial_request['evidence_file'],
                    'resume_epoch': worker.epoch()}}
                worker.execute_plan({'request_id': initial_request['request_id'],
                    'snapshot_id': initial_request['snapshot_id'], 'resume_epoch': worker.epoch(),
                    'context_update': {'economy_plan': record},
                    'actions': [{'type': 'finish_preparation_review',
                                 'reason': 'Explicit protocol budget; no real game authorization.'}]})
                if name == 'policy_unavailable':
                    # A real budget exists; a non-budget failure must not be relabelled.
                    failure = patch.object(worker, 'economy_observation',
                        side_effect=ValueError('declared immutable-frame evidence failure'))
                elif name == 'completion_guard':
                    status = control.status
                    failure = patch.object(control, 'status',
                        side_effect=lambda: {**status(), 'paused': True})
                else:
                    failure = contextlib.nullcontext()
                with failure:
                    worker.tick(worker.last_observation)
                if name in ('six_d', 'three_f'):
                    worker.tick(worker.last_observation)  # Same terminal lineup handoff.
        final = endpoint(modules, worker, control, reads, policy_calls)
        pending = worker.economy_ledger('2-3')['pending']
        original_pending_request_id = pending['request_id'] if pending else None
        recorder.close(complete=False)
        if recorder.path is not None:
            events, issues = modules.profile.read_events([recorder.path])
            summary = modules.profile.summarize_events(events, issues)
        else:
            events, summary = [], {}
        return {'case': name, 'evidence_kind': 'declared_numeric_shop_receipt_protocol',
                'profile_enabled_requested': enabled,
                'fixture_preconditions': list(modules.runner.coaching.PHASES[:3]),
                'phase_claim_limit': 'Prior phase flags are fixture premises, never real completed business.',
                'endpoint': final, 'economy_flow_events': summary.get('economy_flow_events', []),
                'original_pending_request_id': original_pending_request_id,
                'root_returns': summary.get('root_returns', []),
                'profile_issues': summary.get('issues', []), 'profile_error': recorder.error,
                'profile_events': events, 'real_ocr_calls': 0, 'game_inputs': 0, 'new_game_captures': 0,
                'game_latency_seconds': None, 'model_latency_seconds': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare-to', type=Path)
    parser.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    args = parser.parse_args()
    modules = configure(args.code_root)
    before_hashes = sources(modules.root)
    cases = [run_case(modules, name) for name in args.cases]
    after_hashes = sources(modules.root)
    if before_hashes != after_hashes:
        raise RuntimeError('Execution source changed during the offline audit')
    result = {'schema': 'currency-wars-economy-boundary-audit/v1',
              'source_revision': modules.revision,
              'source_hashes': before_hashes, 'source_hashes_after': after_hashes,
              'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'offline_only': True, 'game_inputs': 0, 'new_game_captures': 0,
              'candidate_installed': False, 'online_model_calls': 0,
              'request_boundary': 'Natural Worker.tick initial budget and terminal request; fixture setup excluded.',
              'counting_limit': 'normal_root_requests means non-exception under the unchanged request category; it does not count necessary strategy decisions.',
              'read_boundary': 'The fixture initial observation precedes the audit; only reads caused by natural requests, replies and their consumers are counted.',
              'comparison_identity_rule': 'Only the exact pending request ID in an economy_result reason is mapped to {pending_request_id}, after checking the actual ledger reference; raw IDs/reasons remain in profile evidence.',
              'timing_limit': 'Point diagnostics only. No real OCR, game, ROOT service, animation, or speed claim.',
              'cases': cases}
    if args.compare_to:
        baseline = json.loads(args.compare_to.read_text(encoding='utf-8'))
        if baseline['driver_sha256'] != result['driver_sha256']:
            raise ValueError('Comparison requires the identical frozen audit driver')
        if [case['case'] for case in baseline['cases']] != [case['case'] for case in cases]:
            raise ValueError('Comparison case order differs')
        result['comparison'] = [{'case': before['case'],
            'same_endpoint': before['endpoint'] == after['endpoint'],
            'different_endpoint_fields': [key for key in before['endpoint']
                if before['endpoint'].get(key) != after['endpoint'].get(key)],
            'before_flow_points': len(before['economy_flow_events']),
            'after_flow_points': len(after['economy_flow_events'])}
            for before, after in zip(baseline['cases'], cases)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'cases': len(cases),
                      'comparison': result.get('comparison')}, ensure_ascii=False))
    if (any(case['profile_issues'] or case['profile_error'] for case in cases)
            or any(not row['same_endpoint'] for row in result.get('comparison', []))):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
