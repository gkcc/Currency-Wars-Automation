"""Two consumer regressions for PR29-R1 / PR30-R1 on the frozen PR30 stack.

Reuse the real existing Worker, Entry, mailbox, ManualPhase and saved receipts.
The parent fixtures supply declared pixels/OCR/successors and inert transport;
no new native image, real game input or elapsed-game-time claim is made here.
The driver selects exact methods, never this module's imported parent classes.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import currency_wars_manual_steps as steps
import currency_wars_runner as runner
import replay_currency_wars_rewards as transport
import test_currency_wars_reward_recovery as recovery
import test_currency_wars_guide_actionability as guide
from test_currency_wars_reward_hud_flow import job

EVIDENCE = []


class RetryObligationTests(unittest.TestCase):
    def test_pre_persistence_refusal_requires_new_inspect_not_new_job_id(self):
        with transport.protocol_fixture() as bundle:
            case = recovery.prepare_exhausted(bundle)
            worker, control = bundle.worker, bundle.control
            old_reply = recovery.recovery_reply(bundle, case)
            old_q = copy.deepcopy(worker.state['decision_request'])
            raw = case['archive'].read_bytes()
            pointer = (bundle.runtime / 'reward-step.json').read_bytes()
            before = len(control.published)
            case['state']['mutation'] = 'unknown_coins'
            failed, failed_args = recovery.recover(bundle, case, reply=old_reply)
            self.assertEqual(failed['status'], 'refused', failed)
            self.assertEqual(len(control.published) - before, 1)
            self.assertEqual(failed['result']['input_receipt_ids'], [])
            self.assertTrue(failed['result']['receipt_watermark_verified'])
            self.assertTrue(failed['result']['receipt_delivery_verified'])
            self.assertFalse(recovery.recovery_path(bundle, case).exists())
            self.assertEqual(worker.state['decision_request']['request_id'], old_q['request_id'])
            mailbox = bundle.runtime / 'manual-steps' / (failed_args['step_id'] + '.json')
            archived = bundle.records / ('manual-step-' + failed_args['step_id'] + '.json')
            failed_bytes, archived_bytes = mailbox.read_bytes(), archived.read_bytes()
            case['state']['mutation'] = None
            before = len(control.published)
            same_id = steps.submit(bundle.runtime, bundle.owner, control, **failed_args)
            self.assertEqual(same_id['status'], 'refused')
            self.assertFalse(steps.process(worker))
            for edit in ('same_proof', 'findings_only'):
                with self.subTest(edit=edit):
                    reply = copy.deepcopy(old_reply)
                    if edit == 'findings_only':
                        reply['reward_recovery']['findings'] = 'Different prose, still the same already-used source.'
                    with self.assertRaises(ValueError):
                        recovery.recover(bundle, case, reply=reply)
                    self.assertEqual(len(control.published), before)
                    self.assertEqual(control.action_index, 1)
                    self.assertFalse(recovery.recovery_path(bundle, case).exists())
                    self.assertIsNotNone(worker.pending_reward_step(worker.last_observation))
            self.assertEqual(mailbox.read_bytes(), failed_bytes)
            self.assertEqual(archived.read_bytes(), archived_bytes)
            self.assertEqual((bundle.runtime / 'reward-step.json').read_bytes(), pointer)
            recovery.RewardRecoveryTests.assert_original_unchanged(self, bundle, case, raw)

            inspected, inspect_args = job(bundle, 'inspect')
            self.assertEqual(inspected['status'], 'returned', inspected)
            fresh_reply = recovery.recovery_reply(bundle, case)
            fresh = fresh_reply['reward_recovery']
            # These parent fixtures deliberately reuse the same image bytes.
            self.assertEqual(fresh['snapshot_id'], old_reply['reward_recovery']['snapshot_id'])
            for key in ('request_id', 'capture_request_id', 'frame_id'):
                self.assertNotEqual(fresh[key], old_reply['reward_recovery'][key])
            # A changed assertion or an incomplete old report cannot replace
            # its archived known-zero-input result. This sends no new input.
            before = len(control.published)
            altered = json.loads(failed_bytes)
            altered['result']['receipt_delivery_verified'] = False
            control.write_json(mailbox, altered)
            try:
                with self.assertRaises(ValueError):
                    recovery.recover(bundle, case, reply=fresh_reply)
                self.assertEqual(len(control.published), before)
            finally:
                mailbox.write_bytes(failed_bytes)
            recovered, unused = recovery.recover(bundle, case, reply=fresh_reply)
            self.assertEqual(recovered['status'], 'returned', recovered)
            self.assertEqual(len(control.published) - before, 1)
            self.assertEqual(control.action_index, 1)
            self.assertTrue(recovered['result']['reward_recovery']['continuation_allowed'])
            self.assertTrue(recovered['result']['reward_recovery']['prior_outcome_remains_unknown'])
            self.assertEqual(recovered['result']['input_receipt_ids'], [])
            self.assertEqual(mailbox.read_bytes(), failed_bytes)
            self.assertEqual(archived.read_bytes(), archived_bytes)
            self.assertEqual((bundle.runtime / 'reward-step.json').read_bytes(), pointer)
            recovery.RewardRecoveryTests.assert_original_unchanged(self, bundle, case, raw)
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            EVIDENCE.append(dict(regression='PR29-R1', first_refusal_before_continuation=True,
                stale_proof_extra_captures=0, stale_proof_extra_inputs=0,
                same_id_result_only=True, genuinely_new_inspect=True,
                same_png_sha_new_capture_frame=True, fresh_recovery_captures=1,
                original_effect='unknown', original_auto_read_budget=2,
                original_record_and_failed_report_unchanged=True, game_input=False))

    def test_refused_navigation_successor_preserves_prior_supervisor_obligation(self):
        with guide.worker_fixture(manual=True, successor='unknown') as bundle:
            worker, control = bundle.worker, bundle.control
            first, first_args = guide.manual_job(bundle)
            self.assertEqual(first['status'], 'returned', first)
            first_result = first['result']['startup_navigation']
            self.assertEqual((first_result['status'], first_result['outcome'],
                              first_result['verification_reads']), ('pending', 'unknown', 2))
            original_path = Path(first_result['record_file'])
            original_bytes = original_path.read_bytes()
            original = json.loads(original_bytes)
            self.assertEqual(control.action_index, 1)

            def inspect_current():
                control.current_frame = 'original'
                kwargs = dict(manual_id='manual-one', step_id=uuid.uuid4().hex,
                    operation='inspect', checkpoint_id=None, reply=None, wait_seconds=0)
                steps.submit(bundle.runtime, bundle.owner, control, **kwargs)
                self.assertTrue(steps.process(worker))
                result = steps.submit(bundle.runtime, bundle.owner, control, **kwargs)
                self.assertEqual(result['status'], 'returned', result)
                q = worker.state['decision_request']
                self.assertEqual(q['snapshot_id'], bundle.request['snapshot_id'])
                return q

            def make_reply(q, supervising):
                reply = {key: q[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')}
                reply['actions'] = [guide.action_for(q,
                    checkpoint_id=bundle.checkpoint['checkpoint_id'] if supervising else None)]
                return reply

            second_q = inspect_current()
            begin = worker.begin_startup_navigation
            def lose_foreground(*args, **kwargs):
                value = begin(*args, **kwargs)
                control.foreground = False
                return value
            try:
                with patch.object(worker, 'begin_startup_navigation', side_effect=lose_foreground):
                    refused, refused_args = guide.manual_job(bundle, reply=make_reply(second_q, True))
            finally:
                control.foreground = True
            self.assertEqual(refused['status'], 'refused', refused)
            latest = runner.entry.read_json(bundle.runtime / 'startup-navigation.json')
            self.assertEqual((latest['status'], latest['outcome'], latest['publication_attempted']),
                             ('refused', 'zero_input', False))
            self.assertEqual(latest['prior_unknown_navigation_request_id'], original['request_id'])
            self.assertEqual(control.action_index, 1)
            refused_archive = Path(latest['record_file'])
            refused_bytes = refused_archive.read_bytes()
            before = len(control.published)
            self.assertEqual(steps.submit(bundle.runtime, bundle.owner, control, **refused_args)['status'], 'refused')
            self.assertFalse(steps.process(worker))
            self.assertEqual(len(control.published), before)

            native_q = inspect_current()
            before = len(control.published)
            native_reply = make_reply(native_q, False)
            native, native_args = guide.manual_job(bundle, reply=native_reply)
            self.assertEqual(native['status'], 'refused', native)
            self.assertEqual(len(control.published), before)
            self.assertEqual(control.action_index, 1)
            self.assertFalse((bundle.records / ('guide-navigation-' + native_q['request_id'] + '.json')).exists())
            self.assertEqual(original_path.read_bytes(), original_bytes)
            self.assertEqual(refused_archive.read_bytes(), refused_bytes)
            self.assertEqual(steps.submit(bundle.runtime, bundle.owner, control, **native_args)['status'], 'refused')
            self.assertFalse(steps.process(worker))
            self.assertEqual(len(control.published), before)

            fresh_q = inspect_current()
            fresh_reply = make_reply(fresh_q, True)
            # Broken ancestry must fail before another capture or publication.
            original_path.unlink()
            try:
                with self.assertRaises(ValueError):
                    worker.check_startup_navigation_attempt(fresh_reply['actions'][0], fresh_q)
            finally:
                original_path.write_bytes(original_bytes)
            self.assertEqual(worker.check_startup_navigation_attempt(fresh_reply['actions'][0], fresh_q),
                             original['request_id'])
            control.successor_mode = 'title'
            returned, unused = guide.manual_job(bundle, reply=fresh_reply)
            self.assertEqual(returned['status'], 'returned', returned)
            result = returned['result']['startup_navigation']
            new_record = runner.entry.read_json(Path(result['record_file']))
            self.assertEqual(result['source'], 'supervising_agent')
            self.assertEqual(result['outcome'], 'expected_title_observed')
            self.assertEqual(new_record['prior_unknown_navigation_request_id'], original['request_id'])
            self.assertEqual(control.action_index, 2)
            self.assertEqual(original_path.read_bytes(), original_bytes)
            self.assertEqual(refused_archive.read_bytes(), refused_bytes)
            self.assertFalse(result['automatic_phase_completion'])
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'startup_guide')
            self.assertNotIn('startup_guide', worker.preparation_reviews)
            EVIDENCE.append(dict(regression='PR30-R1', original_effect='unknown', original_reads=2,
                refused_successor_inputs=0, subsequent_native_inputs=0, subsequent_native_captures=0,
                same_id_result_only=True, missing_ancestor_refused=True,
                new_explicit_supervisor_inputs=1, prior_link_preserved=True,
                original_pending_and_refused_archive_unchanged=True,
                final_outcome='expected_title_observed', automatic_phase_completion=False,
                game_input=False))
