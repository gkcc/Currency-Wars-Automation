"""Current reward HUD routing through the real Worker, mailbox and Entry.

Transport, OCR/semantic values, whole-frame combinations and successors are
explicit inert protocol fixtures. Native HUD crop OCR is tested separately.
No game/controller is started and no protocol outcome is a real pickup.
"""
from __future__ import annotations

import copy
from pathlib import Path
import unittest
import uuid

import currency_wars_runner as runner
import currency_wars_manual_steps as steps
import replay_currency_wars_rewards as transport
from test_currency_wars_reward_roi_flow import install_frames


EVIDENCE = []


def prepare_current(bundle, *, original_coins_unknown=True):
    """Two native-derived orb crops on a DECLARED 1-1 protocol canvas."""
    target = install_frames(bundle, 'gray_orb', native_unknown=False)
    for frame in bundle.frames.values():
        observed = frame['observation']
        observed['fields'].update(stage='1-1', level=None, deployed=None)
        for row in observed['rows']:
            if row['text'] == '3-6':
                row.update(text='1-1', raw_text='1-1')
        observed['semantic']['coins'] = dict(value=4, bounds=runner.GOLD_HUD,
            currency_icon_gold_fraction=.358, source='declared_protocol_hud')
        observed['semantic']['player_hud'] = dict(level=None, xp=None,
            reason='declared_protocol_unknown_player_level')
    reader, reads = bundle.reader.read, []
    gate = dict(coins=not original_coins_unknown, after_missing=0, after_reads=0)
    def read(path, force=False, *, scope='full', reuse_primary=False):
        observed = reader(path, force=force, scope=scope)
        missing_after = (bundle.control.orb_index > 0 and scope == 'rewards'
                         and gate['after_reads'] < gate['after_missing'])
        if bundle.control.orb_index > 0 and scope == 'rewards':
            gate['after_reads'] += 1
        if not gate['coins'] or missing_after:
            observed['semantic'].pop('coins', None)
        unread = []
        if scope == 'rewards':
            unread = ['team', 'inventory', 'shop', 'player_hud', 'deployed', 'hp', 'refresh_offer']
            for key in ('team', 'inventory', 'player_hud'):
                observed['semantic'][key] = dict(status='not_read', checked=False,
                    fully_read=False, snapshot_id=observed['snapshot_id'],
                    read_scope=scope, reason='declared_protocol_unread', units=[], items=[])
        observed['read_contract'] = dict(version=2, requested_scope=scope,
            effective_scope=scope, page_ocr='declared_protocol_rows', unread=unread)
        reads.append(dict(scope=scope, reuse_primary=reuse_primary,
            snapshot_id=observed['snapshot_id'], has_coins='coins' in observed['semantic']))
        return observed
    bundle.reader.read = read
    control, worker = bundle.control, bundle.worker
    control.write_json(bundle.runtime / 'runner-manual.json',
                       dict(manual_id='manual-hud', reason='declared current HUD protocol'))
    state = runner.entry.read_json(bundle.runtime / 'runner-state.json')
    state.update(preparation_stage='1-1', match_id=worker.active_match_id)
    control.write_json(bundle.runtime / 'runner-state.json', state)
    # Persist the real Worker-created requests in this inert fixture too.
    original_publish = worker.publish
    def publish(**changes):
        original_publish(**changes)
        saved = runner.entry.read_json(bundle.runtime / 'runner-state.json')
        saved.update(match_id=worker.active_match_id, preparation_stage='1-1',
            decision_request=copy.deepcopy(worker.state.get('decision_request')))
        control.write_json(bundle.runtime / 'runner-state.json', saved)
    worker.publish = publish
    checkpoint = runner.begin_manual_phase(bundle.runtime, bundle.owner, control,
                                          'manual-hud', 'rewards', reader=bundle.reader)
    observed = worker.observe()
    worker._advance_reward_once(observed)
    return target, checkpoint['checkpoint_id'], gate, reads


def job(bundle, operation, checkpoint_id=None, *, reply=None):
    kwargs = dict(manual_id='manual-hud', step_id=uuid.uuid4().hex,
        operation=operation, checkpoint_id=checkpoint_id,
        reply=reply, wait_seconds=0)
    steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
    steps.process(bundle.worker)
    result = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
    return result, kwargs


def annotation(worker, checkpoint_id, target):
    request = worker.state['decision_request']
    return {'reward_roi': dict(source='supervising_agent',
        **{key: request[key] for key in ('request_id', 'snapshot_id', 'match_id', 'resume_epoch', 'deadline_at')},
        **{key: request['observation'][key] for key in ('capture_request_id', 'frame_id', 'page')},
        stage='1-1', checkpoint_id=checkpoint_id, **target,
        findings='Explicit protocol supervisor ROI; no native full-frame qualification claim')}


def current_review(worker):
    request = worker.state['decision_request']
    return dict(proof=dict(source='observed_screen', snapshot_id=request['snapshot_id'],
        evidence_file=request['evidence_file'], resume_epoch=worker.epoch()),
        value=dict(reviewer='supervising_agent', phase='rewards', stage='1-1', completed=True,
            all_claimed=True, rescanned_after_claim=True,
            findings='Declared protocol empty full-field review; not a native clear result'))


class RewardHudFlowTests(unittest.TestCase):
    metrics = EVIDENCE

    def test_reward_result_inspect_one_orb_then_current_full_review_keeps_phase_order(self):
        with transport.protocol_fixture() as bundle:
            target, checkpoint, gate, reads = prepare_current(bundle)
            worker, control = bundle.worker, bundle.control
            original = copy.deepcopy(worker.state['decision_request'])
            self.assertEqual(original['kind'], 'reward_result')
            self.assertNotIn('coins', original['observation']['semantic'])
            self.assertEqual(control.action_index, 0)
            count, index = len(control.published), len(reads)
            gate['coins'] = True
            inspected, inspect_job = job(bundle, 'inspect')
            self.assertEqual(inspected['status'], 'returned', inspected)
            self.assertEqual(control.action_index, 0)
            self.assertEqual(len(control.published) - count, 1)
            self.assertEqual([item['scope'] for item in reads[index:]], ['full'])
            request = worker.state['decision_request']
            self.assertEqual(request['kind'], 'preparation_strategy')
            self.assertNotEqual(request['request_id'], original['request_id'])
            self.assertNotEqual(request['observation']['frame_id'], original['observation']['frame_id'])
            self.assertNotIn('coins', original['observation']['semantic'])
            self.assertEqual(worker.reward_coins(request['observation']), 4)
            self.assertIsNone(request['observation']['fields']['level'])
            repeat_count = len(control.published)
            steps.submit(bundle.runtime, bundle.owner, control, **inspect_job)
            self.assertFalse(steps.process(worker))
            self.assertEqual(len(control.published), repeat_count)
            one, unused = job(bundle, 'collect_rewards', checkpoint,
                              reply=annotation(worker, checkpoint, target))
            self.assertEqual(one['status'], 'returned', one)
            self.assertEqual(control.action_index, 1)
            self.assertEqual(one['result']['reward_step']['status'], 'verified', one)
            self.assertEqual(one['result']['reward_step']['verification_reads'], 1)
            self.assertIsNone(one['result']['all_rewards_cleared'])
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            self.assertEqual(len(worker.last_observation['semantic']['rewards']['targets']), 1)
            with self.assertRaisesRegex(ValueError, '原生观察仍有奖励目标'):
                worker.review_preparation(current_review(worker))
            # A second explicit native call acts only after the first is verified.
            # It is an input-capable call, never described as read-only.
            remainder, unused = job(bundle, 'collect_rewards', checkpoint)
            self.assertEqual(remainder['status'], 'returned', remainder)
            self.assertEqual(control.action_index, 2)
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            self.assertIsNone(remainder['result']['all_rewards_cleared'])
            full, unused = job(bundle, 'inspect')
            self.assertEqual(full['status'], 'returned', full)
            self.assertTrue(runner.full_observation(worker.state['decision_request']['observation']))
            inputs = one['result']['input_receipt_ids'] + remainder['result']['input_receipt_ids']
            completed = runner.finish_manual_phase(bundle.runtime, bundle.owner, control,
                checkpoint, inputs, current_review(worker)['value'], reader=bundle.reader)
            self.assertEqual(completed['status'], 'completed')
            old_epoch = worker.epoch()
            resumed = runner.explicit_resume(bundle.runtime, bundle.owner, control,
                uuid.uuid4().hex,
                expected_guard=runner.resume_guard_snapshot(bundle.runtime, bundle.owner, control))
            self.assertTrue(resumed['resumed'], resumed)
            self.assertNotEqual(worker.epoch(), old_epoch)
            self.assertIsNone(runner.manual_state(bundle.runtime))
            self.assertFalse(worker.tick_decision())  # Real old-epoch request retirement.
            observed = worker.observe()
            worker.ask(observed, 'preparation_strategy',
                       'Declared fresh-epoch full-field protocol review, not historical clear reuse')
            self.assertFalse(worker.consume_manual_results())
            self.assertFalse(getattr(worker, 'manual_results_rejected', set()))
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            request = worker.state['decision_request']
            self.assertTrue(runner.full_observation(request['observation']))
            before_review = len(control.published)
            worker.execute_plan(dict(request_id=request['request_id'], snapshot_id=request['snapshot_id'],
                resume_epoch=worker.epoch(), context_update={'preparation_review': current_review(worker)},
                actions=[dict(type='finish_preparation_review',
                    reason='Current full-field protocol sweep after actual fixture resume')]))
            self.assertEqual(len(control.published) - before_review, 1)  # execute_plan fresh observe.
            self.assertEqual(control.action_index, 2)
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'startup_guide')
            self.assertFalse(worker.preparation_checklist(worker.last_observation)['economy_allowed'])
            EVIDENCE.append(dict(contract='declared_current_hud_consumer_chain',
                initial_kind=original['kind'], inspect_capture_requests=1, inspect_physical_inputs=0,
                inspect_read_scopes=['full'], stale_native_coins_unchanged=True,
                one_orb_input_requests=1, one_orb_effect_reads=1, all_rewards_cleared=None,
                total_declared_orb_inputs=2, final_phase='startup_guide',
                full_review_source='supervising_agent', actual_fixture_resume=True,
                old_manual_clear_not_inherited=True, full_review_entry='Worker.execute_plan',
                full_review_fresh_captures=1, cli_dispatch_executed=False,
                global_fingerprint_exception_added=False, real_game_input=False))

    def test_after_coin_unknown_uses_same_two_read_budget_and_never_replays(self):
        for missing, verified in ((1, True), (99, False)):
            with self.subTest(after_coin_reads_unknown=missing), transport.protocol_fixture() as bundle:
                target, checkpoint, gate, unused = prepare_current(bundle)
                gate['coins'] = True
                job(bundle, 'inspect')
                gate['after_missing'] = missing
                returned, request = job(bundle, 'collect_rewards', checkpoint,
                                       reply=annotation(bundle.worker, checkpoint, target))
                pending = runner.entry.read_json(bundle.runtime / 'reward-step.json')
                self.assertEqual(bundle.control.action_index, 1)
                self.assertEqual(pending['verification_reads'], 2, pending)
                self.assertEqual(pending['status'], 'verified' if verified else 'pending', pending)
                self.assertEqual(returned['pending'], not verified, returned)
                self.assertIsNone(pending['all_rewards_cleared'])
                if not verified:
                    with self.assertRaisesRegex(ValueError, '不能覆盖原pending'):
                        bundle.worker.review_preparation(current_review(bundle.worker))
                count = len(bundle.control.published)
                steps.submit(bundle.runtime, bundle.owner, bundle.control, **request)
                self.assertFalse(steps.process(bundle.worker))
                self.assertEqual(len(bundle.control.published), count)
                EVIDENCE.append(dict(contract='declared_after_coin_unknown',
                    unknown_reads=missing if missing != 99 else 'all', input_requests=1,
                    verification_reads=2, status=pending['status'],
                    no_input_replay=True, real_game_input=False))

    def test_inspect_preserves_unknown_and_native_collect_is_not_a_read_only_api(self):
        with transport.protocol_fixture() as bundle:
            unused, checkpoint, gate, reads = prepare_current(bundle)
            original = copy.deepcopy(bundle.worker.state['decision_request'])
            count = len(bundle.control.published)
            result, unused = job(bundle, 'inspect')
            self.assertEqual(result['status'], 'returned', result)
            self.assertEqual(len(bundle.control.published) - count, 1)
            self.assertEqual(bundle.control.action_index, 0)
            self.assertEqual(bundle.worker.state['decision_request']['kind'], 'preparation_strategy')
            self.assertNotIn('coins', bundle.worker.state['decision_request']['observation']['semantic'])
            self.assertNotIn('coins', original['observation']['semantic'])
            self.assertIsNone(bundle.worker.context['economy_plan'])
            self.assertEqual(bundle.worker.preparation_checklist(bundle.worker.last_observation)['phase'], 'rewards')
            self.assertTrue(runner.manual_state(bundle.runtime))
            index = len(reads)
            blocked, unused = job(bundle, 'collect_rewards', checkpoint)
            self.assertEqual(blocked['status'], 'returned', blocked)
            self.assertEqual(bundle.control.action_index, 0)
            self.assertEqual(reads[index]['scope'], 'rewards')
            self.assertEqual(bundle.worker.state['decision_request']['kind'], 'reward_result')
            self.assertIn('金币', bundle.worker.state['decision_request']['reason'])
            self.assertIsNone(blocked['result']['all_rewards_cleared'])
            EVIDENCE.append(dict(contract='declared_unknown_hud_routing', inspect_physical_inputs=0,
                inspect_kind='preparation_strategy', native_loop_kind='reward_result',
                native_loop_first_scope='rewards', unknown_preserved=True,
                economic_authorization=False, real_game_input=False))


if __name__ == '__main__':
    unittest.main()
