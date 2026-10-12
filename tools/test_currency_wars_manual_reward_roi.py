"""Single-ROI mailbox bindings only; no game input or native detection claim."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

import currency_wars_manual_steps as steps
import currency_wars_runner as runner
import test_currency_wars_submission_queue as queue_fixture


@contextlib.contextmanager
def roi_fixture():
    with tempfile.TemporaryDirectory(prefix='currency-wars-roi-mailbox-') as directory:
        project = Path(directory)
        run, records = project / 'runtime', project / 'debug' / 'records'
        run.mkdir()
        records.mkdir(parents=True)
        control = queue_fixture.transport(run)
        control.status = lambda: dict(ready=True, paused=False, input_halted=False, game_foreground=True)
        owner = dict(run_id='fixture-run', chat_id='fixture-chat')
        binding = dict(run_id=owner['run_id'], match_id='fixture-match', stage='1-1',
                       manual_id='manual-one', old_epoch='fixture-epoch')
        checkpoint_id = 'b' * 32
        source = records / 'original.png'
        with Image.new('RGB', (1920, 1080), (25, 35, 45)) as image:
            image.save(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        original = dict(page='preparation', snapshot_id=digest, capture_request_id='capture-one',
                        frame_id='frame-one', fields={'stage': '1-1'},
                        semantic={'rewards': {'uncertain': True, 'targets': []}})
        request = dict(request_id='c' * 32, kind='preparation_strategy', snapshot_id=digest,
                       match_id=binding['match_id'], resume_epoch=binding['old_epoch'],
                       deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                       original_png=str(source), observation=original,
                       preparation_checklist={'phase': 'rewards'})
        state = dict(owner, match_id=binding['match_id'], preparation_stage=binding['stage'],
                     journal_file=str(records / 'journal.jsonl'), decision_request=request)
        control.write_json(records / 'owner.json', owner)
        control.write_json(run / 'runner-state.json', state)
        control.write_json(run / 'runner-manual.json', {'manual_id': 'manual-one', 'reason': 'declared offline fixture'})
        control.write_json(run / 'runner-resume-epoch.json', {'id': binding['old_epoch']})
        (run / 'manual-results').mkdir()
        control.write_json(run / 'manual-results' / (checkpoint_id + '.json'),
                           dict(binding=binding, status='pending', phase='rewards'))
        worker = SimpleNamespace(run=run, records=records, owner=owner, c=control,
            active_match_id=binding['match_id'], state=state, last_observation=copy.deepcopy(original),
            manual_step_context=None, observed_count=0, annotations=[], notifications=[],
            profile_span=lambda *args, **kwargs: contextlib.nullcontext())
        worker.economy_receipt_watermark = MethodType(runner.Worker.economy_receipt_watermark, worker)
        worker.epoch = lambda: binding['old_epoch']
        worker.publish = lambda **values: worker.notifications.append(values)
        def observe(**unused):
            worker.observed_count += 1
            return worker.last_observation
        def ask(observed, kind, reason, **unused):
            worker.state['decision_request'] = dict(request_id='next-fixture-request', kind=kind)
        def collect(evidence):
            if not steps.active(worker):
                raise AssertionError('The original manual lease must remain active')
            worker.annotations.append(copy.deepcopy(evidence))
            control.write_json(run / 'reward-step.json', dict(step_id='d' * 32,
                manual_step_id=worker.manual_step_context['step_id'], source='supervising_agent',
                status='pending', outcome='unknown', request_id='declared-input-id',
                verification_reads=2, all_rewards_cleared=None))
        worker.observe, worker.ask, worker.collect_reward_roi = observe, ask, collect
        evidence = dict(source='supervising_agent', request_id=request['request_id'], snapshot_id=digest,
            capture_request_id=original['capture_request_id'], frame_id=original['frame_id'], page='preparation',
            match_id=binding['match_id'], stage=binding['stage'], resume_epoch=binding['old_epoch'],
            checkpoint_id=checkpoint_id, deadline_at=request['deadline_at'], kind='blue_orb',
            bounds=[1330, 380, 1410, 460], center=[1370, 420], findings='declared fixture annotation only')
        with patch.object(runner, 'PROJECT', project):
            yield SimpleNamespace(run=run, records=records, source=source, control=control, owner=owner,
                binding=binding, checkpoint_id=checkpoint_id, worker=worker, request=request, evidence=evidence)


def enqueue(bundle, step_id='a' * 32, evidence=None):
    return steps.submit(bundle.run, bundle.owner, bundle.control, manual_id='manual-one',
        step_id=step_id, operation='collect_rewards', checkpoint_id=bundle.checkpoint_id,
        reply={'reward_roi': evidence or bundle.evidence}, wait_seconds=0)


class ManualRewardRoiTests(unittest.TestCase):
    def test_current_source_binding_refuses_mismatched_frame_authority_scope_and_bytes(self):
        with roi_fixture() as bundle:
            digest = steps.validate_reward_roi(bundle.evidence, bundle.request, bundle.binding, bundle.checkpoint_id)
            altered = {**bundle.evidence, 'findings': 'another description'}
            self.assertEqual(digest, steps.validate_reward_roi(altered, bundle.request, bundle.binding, bundle.checkpoint_id))
            changes = dict(source='native_reader', request_id='other-request', snapshot_id='0' * 64,
                capture_request_id='other-capture', frame_id='other-frame', page='shop', match_id='other-match',
                stage='1-2', resume_epoch='other-epoch', checkpoint_id='f' * 32,
                kind='shop_item', bounds=[1300, 380, 1410, 460], center=[1410, 420])
            for key, value in changes.items():
                with self.subTest(field=key), self.assertRaises(ValueError):
                    steps.validate_reward_roi({**bundle.evidence, key: value}, bundle.request,
                                              bundle.binding, bundle.checkpoint_id)
            expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            with self.assertRaisesRegex(ValueError, 'deadline'):
                steps.validate_reward_roi({**bundle.evidence, 'deadline_at': expired},
                    {**bundle.request, 'deadline_at': expired}, bundle.binding, bundle.checkpoint_id)
            bundle.source.write_bytes(b'changed fixture bytes')
            with self.assertRaisesRegex(ValueError, 'bytes'):
                steps.validate_reward_roi(bundle.evidence, bundle.request, bundle.binding, bundle.checkpoint_id)

    def test_one_annotation_dispatch_keeps_native_unknown_pending_and_never_requeues_same_intent(self):
        with roi_fixture() as bundle:
            before = copy.deepcopy(bundle.request['observation']['semantic'])
            self.assertEqual(enqueue(bundle)['status'], 'queued')
            self.assertTrue(steps.process(bundle.worker))
            self.assertEqual(len(bundle.worker.annotations), 1)
            self.assertEqual(bundle.worker.observed_count, 0)  # Worker method owns its guarded first capture.
            self.assertEqual(bundle.request['observation']['semantic'], before)
            repeated = enqueue(bundle)
            self.assertTrue(repeated['pending'])
            self.assertEqual(repeated['result']['reward_step']['source'], 'supervising_agent')
            self.assertEqual(repeated['result']['reward_step']['verification_reads'], 2)
            self.assertIsNone(repeated['result']['all_rewards_cleared'])
            self.assertFalse(steps.process(bundle.worker))
            # Re-create the same authoritative request only in this inert fixture
            # to test the persisted deduplication independently of stale-request rejection.
            bundle.control.write_json(bundle.run / 'runner-state.json',
                {**bundle.worker.state, 'decision_request': bundle.request})
            with self.assertRaisesRegex(ValueError, 'already submitted'):
                enqueue(bundle, step_id='e' * 32, evidence={**bundle.evidence, 'findings': 'edited description'})
            self.assertEqual(bundle.control.published, [])  # Mailbox consumer fixture, not Worker input proof.

    def test_stale_claim_or_prior_unknown_refuses_before_capture_and_preserves_original_receipt(self):
        for fault in ('request_changed', 'source_changed', 'prior_unknown'):
            with self.subTest(fault=fault), roi_fixture() as bundle:
                enqueue(bundle)
                original_receipt = None
                if fault == 'request_changed':
                    bundle.worker.state['decision_request'] = {**bundle.request, 'request_id': 'changed'}
                elif fault == 'source_changed':
                    bundle.source.write_bytes(b'changed after queue')
                else:
                    rid = 'old-input'
                    path = queue_fixture.receipt_path(bundle.run, rid)
                    path.parent.mkdir()
                    request = dict(id=rid, kind='actions', chat_id=bundle.control.OWNER['chat_id'],
                        run_token=bundle.control.OWNER['run_token'], handoff=False,
                        actions=[dict(type='click', args=[1370., 420.])])
                    bundle.control.write_json(path, dict(id=rid, request=request,
                        result=dict(id=rid, ok=False, completed=[], input_attempted=True)))
                    original_receipt = path.read_bytes()
                self.assertTrue(steps.process(bundle.worker))
                result = enqueue(bundle)
                self.assertEqual(result['status'], 'refused')
                self.assertEqual(bundle.worker.observed_count, 0)
                self.assertEqual(bundle.worker.annotations, [])
                self.assertEqual(bundle.control.published, [])
                if original_receipt is not None:
                    self.assertTrue(result['pending'])
                    self.assertEqual(result['result']['prior_unknown_receipt_ids'], ['old-input'])
                    self.assertEqual(path.read_bytes(), original_receipt)


if __name__ == '__main__':
    unittest.main()
