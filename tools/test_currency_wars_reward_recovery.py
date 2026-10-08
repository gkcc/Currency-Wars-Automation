"""Exhausted reward verification and explicit current recovery, never game input.

The unchanged native reward crops are placed on the earlier DECLARED protocol
canvas. Gray disappearance, blue disappearance, OCR rows and gold 4 -> 6 are
synthetic successors. Production target_effect/stable_absence, Worker, Entry,
ManualPhase, mailbox and resume consumers are used without replacing guards.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import uuid

from PIL import Image

import currency_wars_manual_stage as manual_stage
import currency_wars_manual_steps as steps
import currency_wars_rewards as rewards
import currency_wars_runner as runner
from currency_wars_perception import fingerprint
import replay_currency_wars_rewards as transport
from test_currency_wars_reward_hud_flow import prepare_current, job, current_review


EVIDENCE = []


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def prepare_exhausted(bundle):
    """First two post-input contexts unknown, third the FIRST native absence."""
    target, checkpoint, gate, reads = prepare_current(bundle)
    gate['coins'] = True
    inspected, unused = job(bundle, 'inspect')
    if inspected['status'] != 'returned':
        raise AssertionError(inspected)
    read = bundle.reader.read
    state = dict(post_gray_reads=0, coins=6, mutation=None)

    def delayed_context(path, force=False, *, scope='full', **kwargs):
        observed = read(path, force=force, scope=scope, **kwargs)
        if bundle.control.orb_index > 0:
            observed['semantic']['coins']['value'] = state['coins']
        if bundle.control.orb_index == 1 and scope == 'rewards':
            state['post_gray_reads'] += 1
            if state['post_gray_reads'] <= 2:
                # Missing declared OCR anchor, not a fabricated animation cause.
                observed['rows'] = [r for r in observed['rows'] if r['text'] != '商店']
            if state['mutation'] == 'page':
                observed['page'] = 'reward_overlay'
                observed['semantic']['rewards']['interaction_required'] = True
            elif state['mutation'] == 'unknown_coins':
                observed['semantic'].pop('coins', None)
        return observed

    bundle.reader.read = delayed_context
    returned, request = job(bundle, 'collect_rewards', checkpoint)
    pending = runner.entry.read_json(bundle.runtime / 'reward-step.json')
    if (returned['status'] != 'returned' or bundle.control.action_index != 1
            or pending['kind'] != 'gray_orb' or pending['status'] != 'pending'
            or pending['outcome'] != 'unknown' or pending['verification_reads'] != 2
            or len(pending['verification_frames']) != 3
            or bundle.worker.state['decision_request']['kind'] != 'reward_result'):
        raise AssertionError((returned, pending))
    effects = [f.get('effect_evidence', {}).get('reason') for f in pending['verification_frames']]
    if effects != ['reward_effect_context_unverified', 'reward_effect_context_unverified',
                   'target_absent_in_exposed_reward_region']:
        raise AssertionError(effects)
    return dict(target=target, checkpoint=checkpoint, gate=gate, reads=reads,
                state=state, pending=pending, original_job=request,
                archive=bundle.records / ('reward-step-' + pending['step_id'] + '.json'))


def recovery_reply(bundle, case):
    request = bundle.worker.state['decision_request']
    pending = case['pending']
    return {'reward_recovery': dict(source='supervising_agent',
        **{key: request[key] for key in ('request_id', 'snapshot_id', 'match_id', 'resume_epoch', 'deadline_at')},
        **{key: request['observation'][key] for key in ('capture_request_id', 'frame_id', 'page')},
        stage='1-1', checkpoint_id=case['checkpoint'], reward_step_id=pending['step_id'],
        input_request_id=pending['request_id'], prior_outcome_remains_unknown=True,
        target_now_absent=True, continue_current_rewards=True,
        findings='Declared current supervisor proof; original unknown effect is retained, no historical success claim')}


def recover(bundle, case, *, reply=None):
    return job(bundle, 'recover_reward', case['checkpoint'],
               reply=recovery_reply(bundle, case) if reply is None else reply)


def recovery_path(bundle, case):
    return bundle.records / ('reward-continuation-' + case['pending']['step_id'] + '.json')


def set_successor_pixels(bundle, case, mutation):
    """Declared adverse successor only; no original source image is edited."""
    target = case['pending']['target']
    x, y, right, bottom = target['bounds']
    if mutation == 'present':
        bundle.frames['one_fresh'] = copy.deepcopy(bundle.frames['two'])
        return
    with Image.open(io.BytesIO(bundle.frames['one_after']['payload'])) as image:
        changed = image.convert('RGB')
    try:
        if mutation == 'move':
            with Image.open(io.BytesIO(bundle.frames['two']['payload'])) as before:
                with before.crop((x, y, right, bottom)) as orb:
                    changed.paste(orb, (x + 7, y))
        elif mutation == 'cover':
            changed.paste((25, 35, 55), (x, y, right, bottom))
        else:
            raise AssertionError(mutation)
        data = io.BytesIO()
        changed.save(data, format='PNG')
        payload = data.getvalue()
        snapshot = hashlib.sha256(payload).hexdigest()
        observed = copy.deepcopy(bundle.frames['one_after']['observation'])
        observed.update(snapshot_id=snapshot, fingerprint=fingerprint(changed))
        observed['semantic']['rewards'] = rewards.detect(changed, 'preparation', observed['rows'], snapshot)
        bundle.frames['one_fresh'] = dict(payload=payload, observation=observed)
    finally:
        changed.close()


class RewardRecoveryTests(unittest.TestCase):
    metrics = EVIDENCE

    def assert_original_unchanged(self, bundle, case, raw):
        self.assertEqual(case['archive'].read_bytes(), raw)
        value = runner.entry.read_json(case['archive'])
        self.assertEqual((value['status'], value['outcome'], value['verification_reads'],
                          len(value['verification_frames'])), ('pending', 'unknown', 2, 3))
        self.assertIsNone(value['all_rewards_cleared'])

    def test_exhausted_pending_current_recovery_blue_once_then_new_epoch_full_review(self):
        with transport.protocol_fixture() as bundle:
            case = prepare_exhausted(bundle)
            worker, control = bundle.worker, bundle.control
            raw, before = case['archive'].read_bytes(), len(control.published)
            old_pending = copy.deepcopy(case['pending'])
            retained = manual_stage.business_pending(bundle.runtime, bundle.owner, control,
                                                     worker.active_match_id, bundle.records)
            self.assertEqual(len(retained), 1)
            self.assertEqual(len(manual_stage.pending_remaining(retained, run=bundle.runtime,
                owner=bundle.owner, control=control, records=bundle.records)), 1)
            recovered, recovery_job = recover(bundle, case)
            self.assertEqual(recovered['status'], 'returned', recovered)
            self.assertEqual(control.action_index, 1)
            self.assertEqual(len(control.published) - before, 1)
            result = recovered['result']['reward_recovery']
            self.assertTrue(result['continuation_allowed'])
            self.assertTrue(result['prior_outcome_remains_unknown'])
            self.assertEqual(result['original_request_id'], old_pending['request_id'])
            self.assertEqual(result['current_coins'], 6)
            self.assertFalse(result['input_resent'])
            self.assertIsNone(recovered['result']['all_rewards_cleared'])
            continuation = runner.entry.read_json(recovery_path(bundle, case))
            self.assertEqual(continuation['schema'], 'supervised-reward-continuation/v1')
            self.assertEqual(continuation['status'], 'authorized')
            self.assertTrue(continuation['prior_outcome_remains_unknown'])
            self.assertIsNone(continuation['all_rewards_cleared'])
            self.assertEqual(continuation['reward_step_id'], old_pending['step_id'])
            self.assertEqual(continuation['original_request_id'], old_pending['request_id'])
            self.assertEqual(continuation['source_record_sha256'], digest(old_pending))
            receipt = runner.await_existing_receipt(bundle.runtime, control, old_pending['request_id'], 0)
            self.assertEqual(continuation['original_receipt_sha256'], digest(runner.redact(receipt)))
            self.assert_original_unchanged(bundle, case, raw)
            self.assertIsNone(worker.pending_reward_step(worker.last_observation))
            self.assertEqual(len(manual_stage.business_pending(bundle.runtime, bundle.owner, control,
                worker.active_match_id, bundle.records)), 1)  # History is still pending.
            self.assertEqual(manual_stage.pending_remaining(retained, run=bundle.runtime,
                owner=bundle.owner, control=control, records=bundle.records), [])
            count = len(control.published)
            repeat = steps.submit(bundle.runtime, bundle.owner, control, **recovery_job)
            self.assertEqual(repeat['status'], recovered['status'])
            self.assertFalse(steps.process(worker))
            self.assertEqual(len(control.published), count)
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            with self.assertRaisesRegex(ValueError, '原生观察仍有奖励目标'):
                worker.review_preparation(current_review(worker))
            blue, unused = job(bundle, 'collect_rewards', case['checkpoint'])
            self.assertEqual(blue['status'], 'returned', blue)
            self.assertEqual(control.action_index, 2)
            self.assertEqual(blue['result']['reward_step']['status'], 'verified', blue)
            self.assertIsNone(blue['result']['all_rewards_cleared'])
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            self.assert_original_unchanged(bundle, case, raw)
            job(bundle, 'inspect')
            completed = runner.finish_manual_phase(bundle.runtime, bundle.owner, control,
                case['checkpoint'], [old_pending['request_id']] + blue['result']['input_receipt_ids'],
                current_review(worker)['value'], reader=bundle.reader)
            self.assertEqual(completed['status'], 'completed')
            epoch = worker.epoch()
            resumed = runner.explicit_resume(bundle.runtime, bundle.owner, control, uuid.uuid4().hex,
                expected_guard=runner.resume_guard_snapshot(bundle.runtime, bundle.owner, control))
            self.assertTrue(resumed['resumed'], resumed)
            self.assertNotEqual(worker.epoch(), epoch)
            self.assertIsNone(runner.manual_state(bundle.runtime))
            self.assertFalse(worker.tick_decision())
            observed = worker.observe()
            worker.ask(observed, 'preparation_strategy', 'Declared new-epoch full-field review')
            self.assertFalse(worker.consume_manual_results())
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            request = worker.state['decision_request']
            worker.execute_plan(dict(request_id=request['request_id'], snapshot_id=request['snapshot_id'],
                resume_epoch=worker.epoch(), context_update={'preparation_review': current_review(worker)},
                actions=[dict(type='finish_preparation_review', reason='Declared current full-field review')]))
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'startup_guide')
            self.assertFalse(worker.preparation_checklist(worker.last_observation)['economy_allowed'])
            self.assertEqual(control.action_index, 2)
            self.assert_original_unchanged(bundle, case, raw)
            EVIDENCE.append(dict(contract='declared_exhausted_pending_recovery_to_current_phase',
                original_receipt_delivery='completed', original_outcome='unknown',
                original_input_count=1, original_verification_reads=2, original_verification_frames=3,
                original_effect_reasons=['context_unverified', 'context_unverified', 'first_absence'],
                recovery_captures=1, recovery_inputs=0, original_record_unchanged=True,
                native_blue_input_count=1, all_rewards_cleared=None,
                original_debt_retained=True, automatic_phase_completion=False,
                current_full_review='supervising_agent', actual_fixture_resume=True,
                endpoint='new_epoch/startup_guide', real_game_input=False))

    def test_recovery_proof_domains_and_expired_request_refuse_before_capture(self):
        with transport.protocol_fixture() as bundle:
            case = prepare_exhausted(bundle)
            raw = case['archive'].read_bytes()
            before = len(bundle.control.published)
            original = recovery_reply(bundle, case)
            mutations = dict(source='native_reader', request_id='wrong-request', snapshot_id='0' * 64,
                capture_request_id='wrong-capture', frame_id='wrong-frame', match_id='wrong-match',
                stage='1-2', resume_epoch='wrong-epoch', checkpoint_id='0' * 32,
                reward_step_id='0' * 32, input_request_id='wrong-input',
                deadline_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                prior_outcome_remains_unknown=False, target_now_absent=False,
                continue_current_rewards=False)
            for key, value in mutations.items():
                with self.subTest(field=key):
                    reply = copy.deepcopy(original)
                    reply['reward_recovery'][key] = value
                    try:
                        refused, unused = recover(bundle, case, reply=reply)
                    except ValueError:
                        pass  # Submit-time proof rejection.
                    else:
                        self.assertEqual(refused['status'], 'refused', refused)
                    self.assertEqual(len(bundle.control.published), before)
                    self.assert_original_unchanged(bundle, case, raw)
            request = bundle.worker.state['decision_request']
            old_deadline = request['deadline_at']
            request['deadline_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            state = runner.entry.read_json(bundle.runtime / 'runner-state.json')
            state['decision_request'] = copy.deepcopy(request)
            bundle.control.write_json(bundle.runtime / 'runner-state.json', state)
            try:
                try:
                    refused, unused = recover(bundle, case)
                except ValueError:
                    pass
                else:
                    self.assertEqual(refused['status'], 'refused', refused)
            finally:
                request['deadline_at'] = old_deadline
            self.assertEqual(len(bundle.control.published), before)
            self.assertEqual(bundle.control.action_index, 1)
            EVIDENCE.append(dict(contract='declared_recovery_proof_rejections',
                wrong_fields=list(mutations), expired_matching_request=True,
                new_captures=0, new_inputs=0, original_effect_unchanged=True))

    def test_unknown_original_delivery_blocks_recovery_before_new_capture(self):
        with transport.protocol_fixture() as bundle:
            case = prepare_exhausted(bundle)
            raw = case['archive'].read_bytes()
            rid = case['pending']['request_id']
            path = bundle.runtime / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
            receipt = runner.entry.read_json(path)
            receipt['result'].update(ok=False, completed=[], input_attempted=True)
            bundle.control.write_json(path, receipt)
            self.assertTrue(runner.manual_receipt_state(receipt)['unknown_input'])
            before = len(bundle.control.published)
            returned, unused = recover(bundle, case)
            self.assertEqual(returned['status'], 'refused', returned)
            self.assertEqual(len(bundle.control.published), before)
            self.assertEqual(bundle.control.action_index, 1)
            self.assertFalse(recovery_path(bundle, case).exists())
            self.assert_original_unchanged(bundle, case, raw)
            EVIDENCE.append(dict(contract='declared_original_receipt_unknown', new_captures=0,
                                 new_inputs=0, historical_outcome='unknown'))

    def test_current_change_low_or_unknown_coins_present_moved_or_covered_target_refuses(self):
        for mutation in ('page', 'low_coins', 'unknown_coins', 'present', 'move', 'cover'):
            with self.subTest(successor=mutation), transport.protocol_fixture() as bundle:
                case = prepare_exhausted(bundle)
                raw, before = case['archive'].read_bytes(), len(bundle.control.published)
                if mutation == 'low_coins':
                    case['state']['coins'] = 3
                elif mutation in ('page', 'unknown_coins'):
                    case['state']['mutation'] = mutation
                else:
                    set_successor_pixels(bundle, case, mutation)
                returned, unused = recover(bundle, case)
                self.assertEqual(returned['status'], 'refused', returned)
                self.assertLessEqual(len(bundle.control.published) - before, 1)
                self.assertEqual(bundle.control.action_index, 1)
                self.assertIsNotNone(bundle.worker.pending_reward_step(bundle.worker.last_observation))
                self.assert_original_unchanged(bundle, case, raw)
                EVIDENCE.append(dict(contract='declared_adverse_recovery_successor', mutation=mutation,
                    new_captures=len(bundle.control.published) - before, new_inputs=0,
                    original_verification_reads=2, original_frames=3, original_outcome='unknown'))

    def test_recovery_record_tamper_or_distinct_duplicate_cannot_grant_new_input(self):
        with transport.protocol_fixture() as bundle:
            case = prepare_exhausted(bundle)
            raw = case['archive'].read_bytes()
            reply = recovery_reply(bundle, case)
            returned, recovery_job = recover(bundle, case, reply=reply)
            self.assertEqual(returned['status'], 'returned', returned)
            before = len(bundle.control.published)
            # A second mailbox ID cannot obtain another recovery capture for
            # the same original obligation and supervisor request.
            try:
                repeated, unused = recover(bundle, case, reply=reply)
            except ValueError:
                pass
            else:
                self.assertEqual(repeated['status'], 'refused', repeated)
            self.assertEqual(len(bundle.control.published), before)
            path = recovery_path(bundle, case)
            saved = path.read_bytes()
            value = json.loads(saved)
            value['source_record_sha256'] = '0' * 64
            bundle.control.write_json(path, value)
            try:
                self.assertIsNotNone(bundle.worker.pending_reward_step(bundle.worker.last_observation))
                self.assertEqual(len(bundle.control.published), before)
            finally:
                path.write_bytes(saved)
            mailbox = bundle.runtime / 'manual-steps' / (recovery_job['step_id'] + '.json')
            saved_job = mailbox.read_bytes()
            for field, value in (('receipt_delivery_verified', False),
                    ('receipt_watermark_verified', False), ('input_receipt_ids', ['declared-unknown-input']),
                    ('evidence_errors', [{'stage': 'declared-error'}]),
                    ('prior_unknown_receipt_ids', ['declared-unknown-input']),
                    ('finalization_errors', [{'stage': 'declared-error'}])):
                with self.subTest(altered_result=field):
                    item = json.loads(saved_job)
                    (item if field == 'finalization_errors' else item['result'])[field] = value
                    bundle.control.write_json(mailbox, item)
                    try:
                        self.assertIsNotNone(bundle.worker.pending_reward_step(bundle.worker.last_observation))
                    finally:
                        mailbox.write_bytes(saved_job)
            self.assertIsNone(bundle.worker.pending_reward_step(bundle.worker.last_observation))
            self.assertEqual(len(bundle.control.published), before)
            self.assert_original_unchanged(bundle, case, raw)
            self.assertEqual(bundle.control.action_index, 1)
            EVIDENCE.append(dict(contract='declared_recovery_integrity_and_distinct_duplicate',
                additional_capture=0, additional_input=0, altered_record_rejected=True,
                incomplete_returned_report_rejected=True, original_record_unchanged=True))

    def test_failed_read_only_report_retained_and_only_new_current_proof_may_retry(self):
        with transport.protocol_fixture() as bundle:
            case = prepare_exhausted(bundle)
            raw = case['archive'].read_bytes()
            reply = recovery_reply(bundle, case)
            before = len(bundle.control.published)
            # Inject a notification failure AFTER real visual verification and
            # immutable recovery persistence. No visual/input guard is patched.
            with patch.object(steps, '_ask_current', side_effect=RuntimeError('declared current-request notification failure')):
                failed, failed_job = recover(bundle, case, reply=reply)
            self.assertEqual(failed['status'], 'refused', failed)
            self.assertEqual(len(bundle.control.published) - before, 1)
            self.assertEqual(bundle.control.action_index, 1)
            path = recovery_path(bundle, case)
            failed_record = path.read_bytes()
            self.assertIsNotNone(bundle.worker.pending_reward_step(bundle.worker.last_observation))
            self.assert_original_unchanged(bundle, case, raw)
            before = len(bundle.control.published)
            with self.assertRaises(ValueError):
                recover(bundle, case, reply=reply)  # Another ID is not fresh proof.
            self.assertEqual(path.read_bytes(), failed_record)
            self.assertEqual(len(bundle.control.published), before)
            inspected, unused = job(bundle, 'inspect')
            self.assertEqual(inspected['status'], 'returned', inspected)
            fresh_reply = recovery_reply(bundle, case)
            self.assertNotEqual(fresh_reply['reward_recovery']['request_id'], reply['reward_recovery']['request_id'])
            self.assertNotEqual(fresh_reply['reward_recovery']['frame_id'], reply['reward_recovery']['frame_id'])
            mailbox = bundle.runtime / 'manual-steps' / (failed_job['step_id'] + '.json')
            saved_job = mailbox.read_bytes()
            before = len(bundle.control.published)
            for field, value in (('status', 'running'), ('status', 'returned'),
                    ('receipt_delivery_verified', False), ('receipt_watermark_verified', False),
                    ('input_receipt_ids', ['declared-unknown-input'])):
                with self.subTest(old_report_field=field, value=value):
                    item = json.loads(saved_job)
                    (item if field == 'status' else item['result'])[field] = value
                    bundle.control.write_json(mailbox, item)
                    try:
                        with self.assertRaises(ValueError):
                            recover(bundle, case, reply=fresh_reply)
                        self.assertEqual(path.read_bytes(), failed_record)
                        self.assertEqual(len(bundle.control.published), before)
                    finally:
                        mailbox.write_bytes(saved_job)
            recovered, unused = recover(bundle, case, reply=fresh_reply)
            self.assertEqual(recovered['status'], 'returned', recovered)
            self.assertEqual(len(bundle.control.published) - before, 1)
            archive = path.with_name(path.stem + '-failed-' + failed_job['step_id'] + '.json')
            self.assertEqual(archive.read_bytes(), failed_record)
            self.assertEqual(runner.entry.read_json(mailbox)['status'], 'refused')
            self.assertIsNone(bundle.worker.pending_reward_step(bundle.worker.last_observation))
            self.assert_original_unchanged(bundle, case, raw)
            self.assertEqual(bundle.control.action_index, 1)
            EVIDENCE.append(dict(contract='declared_failed_recovery_notification',
                original_effect_unchanged=True, failed_report_preserved=True,
                old_proof_new_id_refused=True, incomplete_failed_report_refused=True,
                fresh_inspect_required=True, second_recovery_captures=1,
                recovery_game_inputs=0, original_verification_reads=2, original_frames=3))

    def test_user_stop_or_worker_deadline_refuses_new_recovery_capture(self):
        for reason in ('runner-stop', 'broker-stop', 'deadline'):
            with self.subTest(reason=reason), transport.protocol_fixture() as bundle:
                case = prepare_exhausted(bundle)
                raw = case['archive'].read_bytes()
                kwargs = dict(manual_id='manual-hud', step_id=uuid.uuid4().hex,
                    operation='recover_reward', checkpoint_id=case['checkpoint'],
                    reply=recovery_reply(bundle, case), wait_seconds=0)
                queued = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
                self.assertEqual(queued['status'], 'queued')
                if reason == 'deadline':
                    bundle.worker.deadline = time.monotonic() - 1
                else:
                    (bundle.runtime / reason).write_text('declared test stop', encoding='utf-8')
                before = len(bundle.control.published)
                if reason == 'deadline':
                    steps.process(bundle.worker)
                else:
                    # The original _manual_binding refuses an actual stop
                    # before claiming the queued job. Do not manufacture a
                    # returned receipt or remove the stop to finish a test.
                    with self.assertRaisesRegex(ValueError, '当前接管/所属run已改变'):
                        steps.process(bundle.worker)
                returned = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
                self.assertEqual(returned['status'], 'refused' if reason == 'deadline' else 'queued', returned)
                self.assertEqual(len(bundle.control.published), before)
                self.assertEqual(bundle.control.action_index, 1)
                self.assert_original_unchanged(bundle, case, raw)
                EVIDENCE.append(dict(contract='declared_stop_or_deadline', reason=reason,
                    result_status=returned['status'], new_captures=0, new_inputs=0,
                    original_outcome='unknown'))

    def test_original_completed_and_later_control_handoff_allow_paused_read_only_recovery(self):
        with transport.protocol_fixture() as bundle:
            case = prepare_exhausted(bundle)
            worker, control = bundle.worker, bundle.control
            raw = case['archive'].read_bytes()
            original = runner.await_existing_receipt(bundle.runtime, control, case['pending']['request_id'], 0)
            self.assertEqual(runner.manual_receipt_state(original)['state'], 'completed')
            epoch, manual = worker.epoch(), runner.manual_state(bundle.runtime)
            status, publish = control.status, control.publish_request
            paused = dict(paused=True, pause_id='declared-before-broker-handoff')
            control.status = lambda: {**status(), **paused}

            def inert_handoff(value):
                publish(value)
                if value.get('kind') == 'resume':
                    paused.update(paused=False, pause_id=None)

            control.publish_request = inert_handoff
            handoff_id = uuid.uuid4().hex
            resume = runner.entry.request(control, 'resume', [], handoff_id, True,
                                          expected_pause_id=paused['pause_id'])
            self.assertTrue(resume['resumed'], resume)
            handoff = runner.await_existing_receipt(bundle.runtime, control, handoff_id, 0)
            self.assertEqual(runner.manual_receipt_state(handoff)['state'], 'control')
            self.assertEqual(worker.epoch(), epoch)
            self.assertEqual(runner.manual_state(bundle.runtime), manual)
            self.assertEqual(control.action_index, 1)
            # A later explicit pause is transport state, not a new historical
            # input effect. Recovery may READ while this pause still holds.
            paused.update(paused=True, pause_id='declared-recovery-pause')
            before = len(control.published)
            returned, unused = recover(bundle, case)
            self.assertEqual(returned['status'], 'returned', returned)
            self.assertTrue(returned['result']['reward_recovery']['continuation_allowed'])
            self.assertTrue(control.status()['paused'])
            self.assertEqual(len(control.published) - before, 1)
            self.assertEqual(control.published[-1]['actions'], [{'type': 'observe', 'args': []}])
            self.assertIs(control.published[-1]['handoff'], False)
            self.assertEqual(control.action_index, 1)
            self.assert_original_unchanged(bundle, case, raw)
            self.assertEqual(worker.epoch(), epoch)
            self.assertEqual(runner.manual_state(bundle.runtime), manual)
            EVIDENCE.append(dict(contract='declared_control_handoff_and_paused_recovery',
                original_delivery='completed', later_delivery='control',
                broker_pause_preserved=True, manual_intent_preserved=True, epoch_unchanged=True,
                recovery_captures=1, recovery_inputs=0, original_outcome='unknown',
                handoff_is_not_an_orb_effect=True))


if __name__ == '__main__':
    unittest.main()
