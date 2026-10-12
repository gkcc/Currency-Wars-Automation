"""Focused tests for economic flow evidence; all game inputs remain inert."""
from __future__ import annotations

from collections import defaultdict
import contextlib
from pathlib import Path
import unittest
from unittest.mock import patch

import replay_currency_wars_economy_boundary as replay
from test_currency_wars_profile import event, window


MODULES = replay.configure(Path(__file__).resolve().parent.parent)


def exits(result):
    return [item for item in result['economy_flow_events'] if item['point'] == 'exit']


class EconomyBoundaryTests(unittest.TestCase):
    def test_natural_budget_and_unrelated_policy_failure_keep_distinct_evidence(self):
        initial = replay.run_case(MODULES, 'missing_budget')
        first = exits(initial)[-1]
        self.assertEqual(first['origin'], 'tick_shop')
        self.assertEqual(first['reason'], 'policy_unavailable')
        self.assertIs(first['budget']['present'], False)
        self.assertIsNone(first['policy']['required_fields'])
        self.assertEqual(initial['endpoint']['unclassified_root_requests'], 1)
        self.assertEqual(initial['endpoint']['simulated_keys'], [])

        failed = replay.run_case(MODULES, 'policy_unavailable')
        last = exits(failed)[-1]
        self.assertIs(last['budget']['present'], True)
        self.assertIs(last['budget']['scope_matches'], True)
        self.assertEqual(last['policy']['reason'], 'declared immutable-frame evidence failure')
        self.assertEqual(last['reason'], 'policy_unavailable')
        self.assertIsNone(last['policy']['missing_required_fields'])
        self.assertEqual(failed['endpoint']['simulated_keys'], [])
        self.assertFalse(failed['endpoint']['economy_completed'])

    def test_unknown_free_count_and_completion_fence_are_different_stops(self):
        unknown = replay.run_case(MODULES, 'missing_free_refresh')
        stop = exits(unknown)[-1]
        self.assertEqual(stop['policy']['dependency_phase'], 'free_refresh')
        self.assertEqual(stop['policy']['required_fields'], ['coins', 'free_refreshes'])
        self.assertEqual(stop['policy']['missing_required_fields'], ['free_refreshes'])
        self.assertFalse(stop['completion_predicate'])
        self.assertFalse(stop['completion_accepted'])
        self.assertEqual(unknown['endpoint']['simulated_keys'], [])

        refused = replay.run_case(MODULES, 'completion_guard')
        stop = exits(refused)[-1]
        self.assertTrue(stop['completion_predicate'])
        self.assertTrue(stop['completion_attempted'])
        self.assertFalse(stop['completion_accepted'])
        self.assertIsNone(stop['completion_guard_detail'])
        self.assertEqual(stop['policy']['required_for'], 'review')
        self.assertEqual(stop['policy']['required_fields'], ['coins', 'free_refreshes'])
        self.assertEqual(stop['policy']['missing_required_fields'], [])
        self.assertFalse(refused['endpoint']['economy_completed'])
        self.assertEqual(refused['endpoint']['simulated_keys'], [])

    def test_pending_uses_original_receipt_at_first_exception_handoff(self):
        result = replay.run_case(MODULES, 'pending_result')
        disabled = replay.run_case(MODULES, 'pending_result', enabled=False)
        self.assertEqual(result['endpoint'], disabled['endpoint'])
        transaction = exits(result)[-1]
        self.assertEqual(transaction['reason'], 'transaction_return')
        self.assertTrue(transaction['pending']['publication_attempted'])
        self.assertNotEqual(transaction['capture_request_id'], transaction['policy']['capture_request_id'])
        self.assertEqual(transaction['capture_request_id'], transaction['pending']['request_id'])
        self.assertEqual(transaction['pending']['request_id'], result['original_pending_request_id'])
        self.assertTrue(result['endpoint']['requests'][-1]['pending_reference_verified'])
        self.assertTrue(transaction['ledger_loaded'])
        self.assertEqual(result['endpoint']['simulated_keys'], [[70.0]])
        self.assertTrue(result['endpoint']['pending'])
        self.assertEqual(result['endpoint']['exception_root_requests'], 1)
        self.assertEqual(result['endpoint']['spent']['experience'], 0)
        self.assertEqual(transaction['completed_actions'], 0)

    def test_profile_enabled_and_disabled_have_same_natural_d_f_endpoints_and_reads(self):
        for name, count, kind in (('six_d', 6, 'refresh'), ('three_f', 3, 'experience')):
            with self.subTest(case=name):
                disabled = replay.run_case(MODULES, name, enabled=False)
                enabled = replay.run_case(MODULES, name)
                self.assertEqual(disabled['endpoint'], enabled['endpoint'])
                self.assertEqual(disabled['economy_flow_events'], [])
                self.assertEqual(enabled['profile_issues'], [])
                self.assertEqual(enabled['endpoint']['normal_root_requests'], 2)
                self.assertEqual(enabled['endpoint']['unclassified_root_requests'], 2)
                self.assertEqual(enabled['endpoint']['phase'], 'lineup_equipment')
                self.assertFalse(enabled['endpoint']['battle_ready'])
                self.assertFalse(enabled['endpoint']['pending'])
                self.assertEqual(enabled['endpoint']['spent'][kind], 12)
                self.assertEqual(exits(enabled)[-1]['completed_actions'], count)
                self.assertEqual(exits(enabled)[-1]['reason'], 'economy_completed')
                groups = defaultdict(list)
                for point in enabled['economy_flow_events']:
                    groups[point['flow_id']].append(point)
                    self.assertEqual(point['origin'], 'tick_shop')
                    self.assertTrue(point['snapshot_id'])
                    self.assertTrue(point['capture_request_id'])
                    self.assertTrue(point['frame_id'])
                self.assertTrue(all([point['point'] for point in group] == ['entry', 'exit']
                                    for group in groups.values()))

    def test_diagnostic_failure_after_success_cannot_turn_input_into_an_error(self):
        @contextlib.contextmanager
        def failing_exit(recorder):
            original = recorder.record_economy_flow

            def emit(**kwargs):
                if kwargs.get('completed_actions', 0) > 0:
                    raise OSError('declared diagnostic write failure after successful transactions')
                return original(**kwargs)

            with patch.object(recorder, 'record_economy_flow', side_effect=emit):
                yield

        disabled = replay.run_case(MODULES, 'three_f', enabled=False)
        failed = replay.run_case(MODULES, 'three_f', profile_hook=failing_exit)
        self.assertEqual(disabled['endpoint'], failed['endpoint'])
        self.assertEqual(failed['profile_error'], 'OSError')
        self.assertEqual(failed['endpoint']['simulated_keys'], [[70.0], [70.0], [70.0]])
        self.assertTrue(failed['endpoint']['economy_completed'])

    def test_original_exception_is_not_hidden_by_diagnostics(self):
        with MODULES.contracts.EconomyTests().worker() as (worker, record, control, frames):
            replay.tick_bookkeeping(worker)
            recorder = MODULES.profile.ProfileRecorder(worker.records,
                run_id='offline-original-exception', enabled=True)
            worker.profile = recorder
            with patch.object(worker, 'economic_policy', side_effect=RuntimeError('original rule failure')):
                with self.assertRaisesRegex(RuntimeError, '^original rule failure$'):
                    worker.advance_economy(worker.last_observation)
            recorder.close(complete=False)
            events, issues = MODULES.profile.read_events([recorder.path])
            report = MODULES.profile.summarize_events(events, issues)
            last = report['economy_flow_events'][-1]
            self.assertEqual(last['reason'], 'exception')
            self.assertEqual(last['error_type'], 'RuntimeError')
            self.assertEqual(last['origin'], 'direct_call')
            self.assertEqual(control.action_index, 0)

    def test_point_events_do_not_add_duration_or_root_requests(self):
        base = window()
        points = [event('economy_flow', 'entry', 2, flow_id='flow', point='entry',
                        origin='tick_shop', phase='economy', reason='entered'),
                  event('economy_flow', 'exit', 7, flow_id='flow', point='exit',
                        origin='tick_shop', phase='economy', reason='no_actions')]
        old = MODULES.profile.summarize_events(base)
        new = MODULES.profile.summarize_events(base + points + points)
        for key in ('observed_seconds', 'operation_totals_seconds', 'business_steps', 'spans', 'root_returns'):
            self.assertEqual(new[key], old[key])
        self.assertEqual(len(new['economy_flow_events']), 2)
        self.assertEqual(new['issues'], [])


if __name__ == '__main__':
    unittest.main()
