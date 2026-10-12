"""Focused local completion tests; the numeric sequence is synthetic, not game OCR."""
import copy
from unittest import TestCase

import currency_wars_runner as runner
import test_currency_wars_economy as existing


class PreparationReplayTests(TestCase):
    def test_real_incomplete_joint_model_cannot_spend_complete_or_deploy_even_with_caller_status(self):
        with existing.EconomyTests().worker(joint_complete=False) as (worker, record, control, frames):
            record['value']['economic_allocation'] = {'schema': 'currency-wars-economic-allocation/v1',
                'status': 'conditional_joint_decision',
                'complete_dimensions': {'purchase': True, 'experience': True, 'refresh': True}}
            worker.accept_economy_plan(record)
            policy = worker.economic_policy(worker.last_observation)
            self.assertTrue(policy['available'])
            self.assertFalse(worker.joint_allocation_complete(policy))
            self.assertFalse(worker.economy_complete(policy))
            self.assertFalse(worker.finish_local_economy(worker.last_observation, policy))
            with self.assertRaises(ValueError):
                worker.require_economic_action({'type': 'buy_xp'}, worker.last_observation)
            with self.assertRaises(ValueError):
                worker.execute_deployment({'actions': [{'type': 'deploy_unit'}]}, {}, worker.last_observation)
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'economy')
            self.assertNotIn('economy', worker.preparation_reviews)
            self.assertEqual(control.action_index, 0)

    def test_each_joint_dimension_current_source_and_status_is_required(self):
        with existing.EconomyTests().worker(initial=existing.values(level=8, xp=[0, 72])) as (worker, record, control, frames):
            record['value']['budget']['experience'] = 0
            worker.accept_economy_plan(record)
            policy = worker.economic_policy(worker.last_observation)
            self.assertTrue(worker.economy_complete(policy))
            for key in ('purchase', 'experience', 'refresh'):
                altered = copy.deepcopy(policy)
                altered['allocation_review']['allocation']['complete_dimensions'][key] = False
                self.assertFalse(worker.economy_complete(altered))
            for key, value in (('origin', 'supervising_agent'), ('snapshot_id', 'old'), ('request_id', None)):
                altered = copy.deepcopy(policy)
                altered['allocation_review'][key] = value
                self.assertFalse(worker.economy_complete(altered))
            altered = copy.deepcopy(policy)
            altered['allocation_review']['allocation']['status'] = 'incomplete_joint_comparison'
            self.assertFalse(worker.economy_complete(altered))

    def test_shop_read_is_native_current_and_precedes_sale_economy_deployment(self):
        with existing.EconomyTests().worker() as (worker, record, control, frames):
            observed = worker.last_observation
            worker.preparation_reviews = {'rewards': {'completed': True}}
            original = copy.deepcopy(observed['shop'])
            for mutate in (
                    lambda shop: shop['slots'].pop(),
                    lambda shop: shop['slots'][0].update(status='unknown'),
                    lambda shop: shop['input'].update(sha256='old-snapshot'),
                    lambda shop: shop['page']['geometry'].update(overlays=['cover'])):
                observed['shop'] = copy.deepcopy(original)
                mutate(observed['shop'])
                self.assertEqual(worker.preparation_checklist(observed)['phase'], 'shop_inspection')
                for action in ({'type': 'click_text', 'text': '出售'}, {'type': 'buy_xp'},
                               {'type': 'buy_shop', 'slot': 1}, {'type': 'deploy_unit'},
                               {'type': 'key', 'args': [68]}):
                    with self.subTest(action=action), self.assertRaises(ValueError):
                        worker.guard_preparation_action(action, observed)
                worker.guard_preparation_action({'type': 'key', 'args': [27]}, observed)
            # A supervising completion assertion cannot bypass unread native slots.
            worker.preparation_reviews['shop_inspection'] = {'completed': True, 'origin': 'supervising_agent'}
            self.assertEqual(worker.preparation_checklist(observed)['phase'], 'shop_inspection')
            observed['shop'] = original
            self.assertEqual(worker.preparation_checklist(copy.deepcopy(observed))['phase'], 'shop_inspection')
            self.assertEqual(worker.preparation_checklist(observed)['phase'], 'inventory_cleanup')
            self.assertEqual(len(worker.preparation_reviews['shop_inspection']['offers']), 5)
            self.assertFalse(worker.preparation_checklist(observed)['economy_allowed'])
            observed['rows'].append({'text': '出售', 'confidence': .99, 'box': [900, 900, 1000, 940]})
            observed['rows'].extend([
                {'text': '出战', 'confidence': .99, 'box': [1700, 720, 1800, 770]},
                {'text': '备战阶段', 'confidence': .99, 'box': [240, 40, 340, 90]}])
            worker.guard_preparation_action({'type': 'click_text', 'text': '出售'}, observed)
            with self.assertRaises(ValueError):
                worker.guard_preparation_action({'type': 'drag', 'args': [500, 950, 600, 600]}, observed)
            worker.invalidate_preparation('inventory')
            self.assertEqual(worker.preparation_checklist(observed)['phase'], 'inventory_cleanup')
            worker.invalidate_preparation('economy')
            self.assertNotIn('shop_inspection', worker.preparation_reviews)
            worker.epoch = lambda: 'new-epoch'
            self.assertEqual(worker.preparation_checklist(observed)['phase'], 'rewards')
            self.assertEqual(control.action_index, 0)

    def test_shop_read_cannot_use_an_old_or_unknown_original_receipt(self):
        with existing.EconomyTests().worker() as (worker, record, control, frames):
            observed = worker.last_observation
            worker.preparation_reviews = {'rewards': {'completed': True}}
            rid = observed['capture_request_id']
            path = worker.run / 'request-ledger' / (runner.hashlib.sha256(rid.encode()).hexdigest() + '.json')
            saved = runner.entry.read_json(path)
            saved['result']['input_attempted'] = True
            saved['result']['attempted_actions'] = [{'type': 'key', 'args': [70.]}]
            control.write_json(path, saved)
            self.assertEqual(worker.preparation_checklist(observed)['phase'], 'shop_inspection')
            self.assertNotIn('shop_inspection', worker.preparation_reviews)
            self.assertEqual(control.action_index, 0)

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
