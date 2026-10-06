"""Stable goal selection and freshness contracts; no capture/input or files."""
import unittest
from currency_wars_progression import progression_plan, rank_guides


class GoalPlanningChecks(unittest.TestCase):
    def test_pending_target_outranks_completed_first_recommendation(self):
        guides = [{'title': '巡海减益队', 'bonds': ['巡海游侠', '减益']},
                  {'title': '昼群攻队', 'bonds': ['昼之半神', '群攻']}]
        result = rank_guides(guides, ['巡海游侠', '减益'], ['昼之半神'])
        self.assertEqual(result[0]['title'], '昼群攻队')
        self.assertTrue(result[1]['goal_preference']['all_stated_targets_completed'])

    def test_unknown_goal_is_not_declared_unfinished_and_ties_keep_order(self):
        guides = [{'title': 'A', 'bonds': ['甲']}, {'title': 'B', 'bonds': ['乙']}]
        result = rank_guides(guides, [], [])
        self.assertEqual([g['title'] for g in result], ['A', 'B'])
        self.assertEqual(result[0]['goal_preference']['pending_targets'], [])
        self.assertEqual(result[0]['goal_preference']['other_targets_unverified'], ['甲'])

    def task(self, met=False, claimed=False):
        return {'id': 'upgrade', 'condition': '累计使用2次特权赋予卡升级进阶装备',
                'condition_met': met, 'reward_claimed': claimed, 'progress': '0/2',
                'proof': {'source': 'observed_screen', 'snapshot_id': 'fresh'}}

    def test_unmet_condition_generates_deliberate_resource_action(self):
        result = progression_plan({}, {'snapshot_id': 'fresh'}, live_tasks=[self.task()])
        self.assertEqual(result['tasks'][0]['state'], 'pending')
        self.assertEqual(result['action_opportunities'][0]['seek_resources'], ['特权赋予卡'])
        self.assertFalse(result['action_opportunities'][0]['execute_ready'])

    def test_completed_but_unclaimed_requests_claim_and_no_repeat_action(self):
        result = progression_plan({}, {'snapshot_id': 'fresh'}, live_tasks=[self.task(True, False)])
        self.assertEqual(result['tasks'][0]['state'], 'completed_unclaimed')
        self.assertEqual(result['action_opportunities'], [])

    def test_old_progress_never_certifies_current_completion(self):
        result = progression_plan({}, {'snapshot_id': 'new'}, live_tasks=[self.task(True, True)])
        self.assertEqual(result['tasks'][0]['state'], 'unknown')
        self.assertEqual(result['observation_needed'], ['upgrade'])
        self.assertEqual(result['action_opportunities'], [])

    def test_resource_needs_exact_name_and_current_proof(self):
        inventory = {'snapshot_id': 'fresh', 'items': [
            {'name': '好运令牌', 'snapshot_id': 'fresh', 'verified': True},
            {'name': '特权赋予卡', 'snapshot_id': 'old', 'verified': True}]}
        observed = {'snapshot_id': 'fresh', 'semantic': {'inventory': inventory}}
        result = progression_plan({}, observed, live_tasks=[self.task()])
        self.assertEqual(result['action_opportunities'][0]['verified_resources_available'], [])
        inventory['items'].append({'name': '特权赋予卡', 'snapshot_id': 'fresh', 'verified': True})
        result = progression_plan({}, observed, live_tasks=[self.task()])
        self.assertEqual(result['action_opportunities'][0]['verified_resources_available'], ['特权赋予卡'])

    def test_accepted_current_node_goal_drives_intent_without_rewriting_panel_proof(self):
        scope = {'match_id': 'match', 'stage': '1-1', 'resume_epoch': 'epoch'}
        context = {**scope, 'validated': True, 'source_snapshot_id': 'fresh', 'tasks': [self.task()]}
        result = progression_plan({}, {'snapshot_id': 'preparation'},
            reviewed_task_context=context, observation_scope=scope)
        self.assertEqual(result['action_opportunities'][0]['seek_resources'], ['特权赋予卡'])
        self.assertFalse(result['tasks'][0]['live_progress_verified'])
        self.assertEqual(result['tasks'][0]['progress_proof']['snapshot_id'], 'fresh')
        self.assertFalse(result['action_opportunities'][0]['execute_ready'])

    def test_review_from_another_node_match_or_epoch_cannot_drive_current_plan(self):
        scope = {'match_id': 'match', 'stage': '1-1', 'resume_epoch': 'epoch'}
        for field in scope:
            context = {**scope, field: 'other', 'validated': True,
                       'source_snapshot_id': 'fresh', 'tasks': [self.task()]}
            result = progression_plan({}, {'snapshot_id': 'preparation'},
                reviewed_task_context=context, observation_scope=scope)
            self.assertEqual(result['action_opportunities'], [])


if __name__ == '__main__':
    unittest.main()
