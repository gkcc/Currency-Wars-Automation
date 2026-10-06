"""Focused local completion tests; the numeric sequence is synthetic, not game OCR."""
import copy
from unittest import TestCase

import currency_wars_runner as runner
import test_currency_wars_economy as existing


class PreparationReplayTests(TestCase):
    def test_three_verified_f_finish_economy_without_another_root_review(self):
        changes = [existing.values(coins=62, xp=[44, 52]), existing.values(coins=58, xp=[48, 52]),
                   existing.values(coins=54, level=8, xp=[0, 72])]
        with existing.EconomyTests().worker(changes) as (worker, record, control, frames):
            worker.accept_economy_plan(record)
            worker.state['decision_request'] = None
            worker.advance_economy(worker.last_observation)
            self.assertEqual(control.action_index, 3)
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'lineup_equipment')
            review = worker.preparation_reviews['economy']
            self.assertEqual(review['origin'], 'local_economy_execution')
            self.assertNotIn('reviewer', review)
            self.assertEqual(review['actual_spent']['experience'], 12)
            self.assertEqual(review['proof']['capture_request_id'], worker.last_observation['capture_request_id'])
            self.assertFalse(worker.preparation_checklist(worker.last_observation)['battle_ready'])
            self.assertEqual(worker.state['statistics']['decisions'], 1)  # Original budget request only.
            self.assertFalse(worker.advance_economy(worker.last_observation))
            self.assertEqual(control.action_index, 3)
            worker.invalidate_preparation('inventory')
            self.assertNotIn('economy', worker.preparation_reviews)

    def test_pending_partial_unknown_scope_and_pause_cannot_finish(self):
        with existing.EconomyTests().worker(initial=existing.values(level=8, xp=[0, 72])) as (worker, record, control, frames):
            record['value']['budget']['experience'] = 0
            worker.accept_economy_plan(record)
            policy = worker.economic_policy(worker.last_observation)
            self.assertTrue(worker.economy_complete(policy))
            for change in ({'pending': {'outcome': 'unknown'}}, {'available': False},
                           {'experience_resolved': False}, {'actions': [{'type': 'buy_xp'}]}):
                with self.subTest(change=change):
                    self.assertFalse(worker.finish_local_economy(worker.last_observation, {**policy, **change}))
            unread = copy.deepcopy(policy)
            unread['observation']['unknown'].append('free_refreshes')
            self.assertFalse(worker.finish_local_economy(worker.last_observation, unread))
            original_scope = worker.economy_binding['scope']
            worker.economy_binding['scope'] = ('match', '2-3', 'new-epoch')
            self.assertFalse(worker.finish_local_economy(worker.last_observation, policy))
            worker.economy_binding['scope'] = original_scope
            control.write_json(worker.run / 'runner-manual.json', {'manual_id': 'manual', 'reason': 'manual'})
            self.assertFalse(worker.finish_local_economy(worker.last_observation, policy))
            (worker.run / 'runner-manual.json').unlink()
            worker.preparation_reviews.pop('rewards')
            self.assertFalse(worker.finish_local_economy(worker.last_observation, policy))
            self.assertEqual(control.action_index, 0)
            self.assertNotIn('economy', worker.preparation_reviews)

    def test_terminal_receipt_unknown_input_does_not_become_local_completion(self):
        with existing.EconomyTests().worker(initial=existing.values(level=8, xp=[0, 72])) as (worker, record, control, frames):
            record['value']['budget']['experience'] = 0
            worker.accept_economy_plan(record)
            rid = worker.last_observation['capture_request_id']
            import hashlib
            path = worker.run / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
            saved = runner.entry.read_json(path)
            saved['result']['input_attempted'] = True
            saved['result']['attempted_actions'] = [{'type': 'key', 'args': [70.]}]
            control.write_json(path, saved)
            self.assertFalse(worker.finish_local_economy(worker.last_observation, worker.economic_policy(worker.last_observation)))
            self.assertNotIn('economy', worker.preparation_reviews)

    def test_later_real_entry_input_cannot_complete_from_old_frame(self):
        with existing.EconomyTests().worker(initial=existing.values(level=8, xp=[0, 72])) as (worker, record, control, frames):
            record['value']['budget']['experience'] = 0
            worker.accept_economy_plan(record)
            observed, policy = worker.last_observation, worker.economic_policy(worker.last_observation)
            runner.entry.request(control, 'actions', ['key:46'], 'external-later-sale', False)
            count = len(control.published)
            self.assertFalse(worker.finish_local_economy(observed, policy))
            self.assertNotIn('economy', worker.preparation_reviews)
            self.assertEqual(len(control.published), count)
