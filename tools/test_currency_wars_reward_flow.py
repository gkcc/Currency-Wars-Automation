"""Focused single-reward Worker/Entry contracts. All publishers are inert."""
import copy
import json
import unittest

import currency_wars_runner as runner
from replay_currency_wars_rewards import escape_reply, mutation_publications, protocol_fixture


class RewardFlowTests(unittest.TestCase):
    def pending(self, bundle):
        return json.loads((bundle.runtime / 'reward-step.json').read_text(encoding='utf8'))

    def test_real_worker_relocates_two_single_clicks_then_requires_full_field_review(self):
        for start_shop in (False, True):
            with self.subTest(start_shop=start_shop), protocol_fixture(start_shop=start_shop) as bundle:
                worker, control = bundle.worker, bundle.control
                first = worker.observe()
                self.assertFalse(first['semantic']['team']['checked'])
                worker.tick(first)
                clicks = [action['args'] for event in control.publication_trace
                    for action in event['actions'] if action['type'] == 'click']
                expected = ([[1623., 982.]] if start_shop else []) + [[1587., 299.], [1542., 358.]]
                self.assertEqual(clicks, expected)
                mutations = [event for event in control.publication_trace
                    if any(action['type'] == 'click' for action in event['actions'])]
                self.assertEqual(len(mutations), len(expected))
                self.assertTrue(all(sum(action['type'] == 'click' for action in event['actions']) == 1
                                    for event in mutations))
                self.assertEqual(len({event['request_id'] for event in mutations}), len(expected))
                self.assertEqual(control.publication_trace[-2]['after_frame'], 'one_fresh')
                effects = [event for event in worker.log_events if event.get('event') == 'reward_control_effect']
                expected_effects = (['shop_collapsed'] if start_shop else []) + ['one_visible_orb_removed'] * 2
                self.assertEqual([event['outcome'] for event in effects], expected_effects)
                self.assertEqual(worker.state['statistics']['decisions'], 1)
                self.assertEqual(worker.state['decision_request']['kind'], 'preparation_strategy')
                checklist = worker.preparation_checklist(worker.last_observation)
                self.assertEqual(checklist['phase'], 'rewards')
                self.assertEqual(worker.preparation_reviews, {})
                self.assertFalse(checklist['battle_ready'])
                self.assertEqual(len(runner.coaching.PHASES), 6)
                self.assertIsNone(self.pending(bundle)['all_rewards_cleared'])
                self.assertEqual(self.pending(bundle)['status'], 'verified')

    def test_zero_effect_two_to_zero_charge_and_group_receipt_stop_without_retry(self):
        for effect in ('zero_effect', 'drop_two', 'coins_down', 'grouped_completed'):
            with self.subTest(effect=effect), protocol_fixture(effect) as bundle:
                worker, control = bundle.worker, bundle.control
                worker.tick(worker.observe())
                self.assertEqual(control.action_index, 1)
                pending = self.pending(bundle)
                self.assertNotEqual(pending['status'], 'verified')
                self.assertEqual(pending['outcome'], 'unknown')
                self.assertEqual(worker.state['decision_request']['kind'], 'reward_result')
                self.assertEqual(worker.state['decision_request']['return_reason']['category'], 'exception')
                count = len(control.published)
                worker.advance_rewards(worker.last_observation)
                self.assertEqual(len(control.published), count)
                self.assertEqual(self.pending(bundle)['request_id'], pending['request_id'])
                self.assertNotIn('rewards', worker.preparation_reviews)

    def test_later_external_input_old_after_frame_and_changed_epoch_preserve_pending(self):
        for interruption in ('external_input', 'old_after_frame', 'new_epoch'):
            with self.subTest(interruption=interruption), protocol_fixture() as bundle:
                worker, control = bundle.worker, bundle.control
                first = worker.observe()
                old = copy.deepcopy(first)
                reconcile = worker.reconcile_reward_step
                def interrupted(pending, observed):
                    if interruption == 'external_input':
                        runner.entry.request(control, 'actions', ['key:27'], 'external-after-reward', False)
                    elif interruption == 'old_after_frame':
                        observed = old
                    else:
                        control.write_json(bundle.runtime / 'runner-resume-epoch.json', {'id': 'new-epoch'})
                    return reconcile(pending, observed)
                worker.reconcile_reward_step = interrupted
                worker.tick(first)
                pending = self.pending(bundle)
                self.assertEqual(control.action_index, 1)
                self.assertNotEqual(pending['status'], 'verified')
                self.assertEqual(pending['outcome'], 'unknown')
                self.assertEqual(worker.state['decision_request']['kind'], 'reward_result')
                expected = {'external_input': '其他输入', 'old_after_frame': '当前不可变', 'new_epoch': 'epoch'}
                self.assertIn(expected[interruption], pending['reason'])
                count = len(control.published)
                worker.advance_rewards(worker.last_observation)
                self.assertEqual(len(control.published), count)
                self.assertEqual(self.pending(bundle)['request_id'], pending['request_id'])

    def test_stop_guards_block_publication_and_same_pixels_do_not_restore_an_old_frame(self):
        for marker in ('runner-stop', 'broker-stop'):
            with self.subTest(marker=marker), protocol_fixture() as bundle:
                worker, control = bundle.worker, bundle.control
                first = worker.observe()
                (bundle.runtime / marker).touch()
                worker.tick(first)
                pending = self.pending(bundle)
                self.assertEqual(control.action_index, 0)
                self.assertNotEqual(pending['status'], 'verified')
                self.assertFalse(pending['publication_attempted'])
                self.assertEqual(pending['outcome'], 'not_published')
                count = mutation_publications(control)
                worker.advance_rewards(worker.last_observation)
                self.assertEqual(mutation_publications(control), count)
        with protocol_fixture('zero_effect') as bundle:
            worker, control = bundle.worker, bundle.control
            first = worker.observe()
            worker.tick(first)
            pending = self.pending(bundle)
            before = pending['before']['observation']
            self.assertEqual(before['snapshot_id'], worker.last_observation['snapshot_id'])
            self.assertNotEqual(before['frame_id'], worker.last_observation['frame_id'])
            count = len(control.published)
            with self.assertRaisesRegex(ValueError, '发布前帧'):
                worker.guard_reward_step(pending, before)
            self.assertEqual(len(control.published), count)

    def test_unknown_delivery_blocks_a_root_escape_reply_even_on_a_selection_page(self):
        with protocol_fixture('unknown_selection') as bundle:
            worker, control = bundle.worker, bundle.control
            worker.tick(worker.observe())
            pending = self.pending(bundle)
            self.assertEqual(worker.last_observation['page'], 'supply')
            self.assertIsNone(worker.last_observation['fields']['stage'])
            self.assertNotEqual(pending['status'], 'verified')
            original = runner.await_existing_receipt(bundle.runtime, control, pending['request_id'], 0)
            self.assertTrue(runner.manual_receipt_state(original)['unknown_input'])
            count = mutation_publications(control)
            with self.assertRaisesRegex((ValueError, RuntimeError), '领奖'):
                worker.execute_plan(escape_reply(worker))
            self.assertEqual(mutation_publications(control), count)
            self.assertEqual(self.pending(bundle)['request_id'], pending['request_id'])
            self.assertEqual(self.pending(bundle)['outcome'], 'unknown')

    def test_business_archive_keeps_same_match_unknown_without_importing_old_match_or_refused_steps(self):
        with protocol_fixture('zero_effect') as bundle:
            worker, control = bundle.worker, bundle.control
            worker.tick(worker.observe())
            pending = self.pending(bundle)
            original = bundle.records / ('reward-step-' + pending['step_id'] + '.json')
            original_bytes = original.read_bytes()
            old_lease = {**bundle.owner, 'journal_file': str(bundle.records / 'journal.jsonl'),
                         'entry_receipt_watermark': []}
            next_records = bundle.records.parent / 'successor'
            next_records.mkdir()
            next_owner = {'run_id': 'new-run', 'chat_id': 'new-chat'}
            control.write_json(next_records / 'owner.json', next_owner)
            next_lease = {**next_owner, 'journal_file': str(next_records / 'journal.jsonl'),
                          'entry_receipt_watermark': []}
            business = {'match_id': 'match', 'leases': [old_lease, next_lease], 'economy': {}}
            retained = runner.pending_business_requests(business)
            self.assertEqual(len(retained), 1)
            self.assertEqual(retained[0]['origin_run_id'], bundle.owner['run_id'])
            self.assertEqual(retained[0]['request_id'], pending['request_id'])
            self.assertEqual(retained[0]['kind'], 'reward')
            self.assertEqual(runner.pending_business_requests({**business, 'match_id': 'later-match'}), [])
            old_lease['entry_receipt_watermark'] = [pending['request_id']]
            self.assertEqual(runner.pending_business_requests(business), [])
            old_lease['entry_receipt_watermark'] = []
            control.write_json(bundle.records / 'reward-step-refused-fixture.json',
                {**pending, 'step_id': 'refused-fixture', 'request_id': 'never-published-fixture',
                 'status': 'refused', 'outcome': 'not_published', 'publication_attempted': False})
            self.assertEqual(runner.pending_business_requests(business), retained)
            self.assertEqual(original.read_bytes(), original_bytes)


if __name__ == '__main__':
    unittest.main()
