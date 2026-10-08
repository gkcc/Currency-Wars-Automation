"""Deployment debt protocol only; no real roster reading or desktop input.

The inert existing Entry fixture produces immutable synthetic PNG receipts.
Declared rule results exercise source consumption, not real deployment success.
No old TestCase is inherited or included in this file's selection.
"""
from __future__ import annotations

import copy
import hashlib
import io
import unittest

from PIL import Image

import currency_wars_deployment as deployment
import currency_wars_manual_stage as stage_bridge
import currency_wars_runner as runner
import test_currency_wars_deployment_reading as reading
import test_local_runtime_compatibility as compatibility


class DeploymentPendingTests(unittest.TestCase):
    def fixture(self):
        return compatibility.RuntimeCompatibilityTests().manual_bridge_fixture()

    def make_step(self, run, records, owner, control, *, later_observation=False):
        control.published = []
        plan = deployment.spec(reading.action(), reading.knowledge())
        before = runner.entry.request(control, 'actions', ['observe'], 'protocol-deployment-before', False)
        before_png = records / 'protocol-deployment-before.png'
        before_png.write_bytes(runner.entry.observation_frame(run, before).read_bytes())
        changed = io.BytesIO()
        with Image.new('RGB', (1920, 1080), 'white') as image:
            image.save(changed, format='PNG')
        control.frame = changed.getvalue()
        rid, step_id = 'protocol-deployment-input', 'a' * 32
        drag = 'drag:' + ':'.join(map(str, plan['source']['point'] + plan['target']['point']))
        result = runner.entry.request(control, 'actions', [drag, 'wait:0.7'], rid, False)
        receipt = runner.redact(runner.await_existing_receipt(run, control, rid, 0))
        after = (runner.entry.request(control, 'actions', ['observe'], 'protocol-deployment-after', False)
                 if later_observation else result)
        after_png = records / 'protocol-deployment-after.png'
        after_png.write_bytes(runner.entry.observation_frame(run, after).read_bytes())
        def observed(captured, moved):
            value = reading.observation(moved=moved, snapshot=captured['observation']['snapshot_sha256'])
            value['fields']['stage'] = value['rows'][0]['text'] = value['rows'][0]['raw_text'] = '2-3'
            value.update(capture_request_id=captured['id'], frame_id=captured['observation']['frame_id'])
            return value
        prior = deployment.before(observed(before, False), plan)
        after_read = deployment.project(observed(after, True))
        value = {'schema': 'currency-wars-deployment-step/v1', 'step_id': step_id,
            'run_id': owner['run_id'], 'match_id': 'match', 'stage': '2-3', 'resume_epoch': 'old-epoch',
            'kind': 'deploy_unit', 'status': 'unverified', 'outcome': 'unknown',
            'request_id': rid, 'publication_attempted': True,
            'broker_actions': receipt['request']['actions'], 'receipt': receipt['result'],
            'spec': plan, 'before': prior, 'after_read': after_read,
            'after_result': deployment.after(after_read, plan, prior),
            'before_png': str(before_png), 'before_snapshot_id': before['observation']['snapshot_sha256'],
            'before_capture_request_id': before['id'], 'before_frame_id': before['observation']['frame_id'],
            'after_png': str(after_png), 'after_snapshot_id': after['observation']['snapshot_sha256'],
            'after_capture_request_id': after['id'], 'after_frame_id': after['observation']['frame_id']}
        self.save_step(run, records, control, value)
        return value

    @staticmethod
    def save_step(run, records, control, value):
        control.write_json(run / 'deployment-step.json', value)
        control.write_json(records / ('deployment-step-' + value['step_id'] + '.json'), value)

    @staticmethod
    def remaining(entries, run, records, owner, control):
        return stage_bridge.pending_remaining(entries, run=run, records=records, owner=owner, control=control)

    def test_completed_delivery_and_new_stage_bridge_preserve_deployment_debt(self):
        with self.fixture() as (run, records, owner, control, reader):
            value = self.make_step(run, records, owner, control)
            entries = stage_bridge.business_pending(run, owner, control, 'match', records)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['source_kind'], 'deployment')
            self.assertEqual(entries[0]['delivery_state'], 'completed')
            self.assertEqual(len(entries[0]['record_files']), 2)
            self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
            old_read = reader.read
            reader.read = lambda *args, **kwargs: {**old_read(*args, **kwargs),
                'fields': {'stage': '2-4'}, 'rows': [
                    {'text': '备战阶段', 'confidence': .99, 'box': [430, 33, 513, 60]},
                    {'text': '2-4', 'confidence': .99, 'box': [441, 62, 502, 98]}]}
            published = len(control.published)
            bridge = stage_bridge.create(run, owner, control, 'manual-one', reader=reader)
            self.assertEqual((bridge['from_stage'], bridge['to_stage']), ('2-3', '2-4'))
            self.assertEqual(bridge['blocked_ids'], [value['request_id']])
            self.assertEqual(bridge['business_pending'][0]['step_id'], value['step_id'])
            self.assertEqual(bridge['business_pending'][0]['resume_epoch'], 'old-epoch')
            self.assertEqual(bridge['completed_phases'], [])
            self.assertTrue(all(item['actions'] == [{'type': 'observe', 'args': []}]
                                for item in control.published[published:]))
            control.write_json(run / 'runner-resume-epoch.json', {'id': 'new-epoch'})
            self.assertEqual(self.remaining(entries, run, records, owner, control), entries)

    def test_verified_archive_cannot_hide_pending_or_reused_same_id_pointer(self):
        with self.fixture() as (run, records, owner, control, reader):
            value = self.make_step(run, records, owner, control)
            entries = stage_bridge.business_pending(run, owner, control, 'match', records)
            archive = records / ('deployment-step-' + value['step_id'] + '.json')
            pointer = run / 'deployment-step.json'
            verified = {**value, 'status': 'verified', 'outcome': 'deployed'}
            control.write_json(archive, verified)
            self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
            control.write_json(pointer, {**verified, 'step_id': 'b' * 32})
            self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
            control.write_json(pointer, {**value, 'step_id': 'b' * 32, 'request_id': 'protocol-next-input'})
            control.write_json(archive, value)
            retained = stage_bridge.business_pending(run, owner, control, 'match', records, retained=entries)
            self.assertTrue(any(item['request_id'] == value['request_id'] for item in retained))
            self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
            control.write_json(archive, verified)
            self.assertEqual(self.remaining(entries, run, records, owner, control), [])
            archive.unlink()
            self.assertEqual(self.remaining(entries, run, records, owner, control), entries)

    def test_original_or_read_only_successor_requires_exact_archived_png_and_frame(self):
        for later_observation in (False, True):
            with self.subTest(later_observation=later_observation), self.fixture() as (run, records, owner, control, reader):
                value = self.make_step(run, records, owner, control, later_observation=later_observation)
                entries = stage_bridge.business_pending(run, owner, control, 'match', records)
                verified = {**value, 'status': 'verified', 'outcome': 'deployed'}
                self.save_step(run, records, control, verified)
                self.assertEqual(self.remaining(entries, run, records, owner, control), [])
                self.assertEqual(stage_bridge.business_pending(run, owner, control, 'match', records), [])
                for key in ('before_frame_id', 'after_frame_id', 'before_capture_request_id'):
                    with self.subTest(changed=key):
                        self.save_step(run, records, control, {**verified, key: 'unbound-source'})
                        self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
                for fault in ('prior_plan', 'after_read_identity', 'native_result', 'reported_result'):
                    with self.subTest(fault=fault):
                        changed = copy.deepcopy(verified)
                        if fault == 'prior_plan':
                            changed['before']['plan']['name'] = '其他角色'
                        elif fault == 'after_read_identity':
                            changed['after_read']['capture_request_id'] = 'unbound-source'
                        elif fault == 'native_result':
                            changed['after_read']['fields']['deployed'] = '7/8'
                        else:
                            changed['after_result']['occupied'] = 9
                        self.save_step(run, records, control, changed)
                        self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
                self.save_step(run, records, control, verified)
                for filename in ('protocol-deployment-before.png', 'protocol-deployment-after.png'):
                    path = records / filename
                    original = path.read_bytes()
                    try:
                        path.write_bytes(b'corrupted synthetic PNG')
                        self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
                        self.assertEqual(len(stage_bridge.business_pending(run, owner, control, 'match', records)), 1)
                    finally:
                        path.write_bytes(original)

    def test_complete_actions_and_pure_observe_are_required_and_refusal_needs_zero_input(self):
        with self.fixture() as (run, records, owner, control, reader):
            value = self.make_step(run, records, owner, control)
            entries = stage_bridge.business_pending(run, owner, control, 'match', records)
            verified = {**value, 'status': 'verified', 'outcome': 'deployed'}
            altered = copy.deepcopy(verified)
            altered['broker_actions'][-1]['args'] = ['0.8']
            self.save_step(run, records, control, altered)
            self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
            other = runner.entry.request(control, 'actions', ['key:70'], 'protocol-not-read-only', False)
            changed = copy.deepcopy(verified)
            changed.update(after_capture_request_id=other['id'], after_frame_id=other['observation']['frame_id'])
            changed['after_read'].update(capture_request_id=other['id'], frame_id=other['observation']['frame_id'])
            self.save_step(run, records, control, changed)
            self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
            refused = {**value, 'status': 'refused', 'publication_attempted': False}
            self.save_step(run, records, control, refused)
            self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
            receipt_path = run / 'request-ledger' / (hashlib.sha256(value['request_id'].encode()).hexdigest() + '.json')
            receipt = runner.entry.read_json(receipt_path)
            receipt['result'].update(ok=False, completed=[], input_attempted=False, attempted_actions=[])
            control.write_json(receipt_path, receipt)
            refused['receipt'] = runner.redact(receipt['result'])
            self.save_step(run, records, control, refused)
            # Changed original evidence cannot discharge an already retained
            # debt, even when the replacement claims a zero-input refusal.
            self.assertEqual(self.remaining(entries, run, records, owner, control), entries)
            # A separately scanned, original zero-input refusal may close.
            self.assertEqual(stage_bridge.business_pending(run, owner, control, 'match', records), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
