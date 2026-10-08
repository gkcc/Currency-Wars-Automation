"""Existing read-only consumers of native tooltip candidates.

The black PNG, OCR rows and old completed receipt are declared protocols,
not game recognition or a real owner/slot chain. Only the OCR engine is
substituted. Perception, StateReader, Worker.read_frame, Entry frame guards
and ManualPhase capture/checkpoint code are the production implementations.
The inert publisher accepts pure observe only and cannot send game input.
No old TestCase is inherited or selected.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
from pathlib import Path
import unittest

import currency_wars_runner as runner
from currency_wars_perception import Perception
from currency_wars_state_reader import StateReader
import test_local_runtime_compatibility as compatibility


class DeclaredOCR:
    """Raw OCR protocol fixture; no claim that the black PNG contains text."""

    def __init__(self, *, candidate=True, after_read=None):
        self.calls = 0
        self.after_read = after_read
        self.rows = []
        if candidate:
            for text, bounds in (
                    ('协议装备甲', [1200, 300, 1350, 335]),
                    ('进阶装备', [1200, 353, 1300, 380])):
                x, y, right, bottom = bounds
                self.rows.append(([[x / 1.5, y / 1.5], [right / 1.5, y / 1.5],
                                   [right / 1.5, bottom / 1.5], [x / 1.5, bottom / 1.5]],
                                  text, .99))

    def __call__(self, image, **kwargs):
        self.calls += 1
        if self.after_read is not None:
            self.after_read()
        return copy.deepcopy(self.rows), None


class EquippedTooltipConsumerTests(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self, *, candidate=True):
        helper = compatibility.RuntimeCompatibilityTests()
        with helper.manual_bridge_fixture() as (run, records, owner, control, unused_reader):
            original_publish = control.publish_request
            control.published = []

            def observe_only(value):
                if (value.get('kind') != 'actions' or value.get('handoff') is not False
                        or value.get('actions') != [{'type': 'observe', 'args': []}]):
                    raise AssertionError('tooltip consumer fixture permits pure observe only')
                original_publish(value)

            control.publish_request = observe_only
            reader = Perception()
            reader.engine = DeclaredOCR(candidate=candidate)
            # Explicit absent resources are a forced protocol condition, never
            # evidence that ROOT lacks its original local resource collection.
            reader.state_reader = StateReader(resources=run / 'absent-protocol-resources')
            worker = helper.frame_worker(run, records, owner, control, reader)
            yield run, records, owner, control, reader, worker

    def assert_unknown_owner(self, observed, *, candidate=True):
        native = observed['semantic']['native_tooltips']
        self.assertEqual(native['schema'], 'currency-wars-native-tooltip-observation/v1')
        self.assertEqual(native['snapshot_id'], observed['snapshot_id'])
        self.assertEqual(native['rows_snapshot_id'], observed['snapshot_id'])
        self.assertEqual(native['input']['sha256'], observed['snapshot_id'])
        self.assertEqual(native['status'], 'unknown')
        self.assertFalse(native['checked'])
        for key in ('equipped', 'owned', 'location', 'owner', 'slot'):
            self.assertIsNone(native[key])
        self.assertIn('actual_unit_panel_source_missing', native['reasons'])
        self.assertIn('actual_equipment_slot_source_missing', native['reasons'])
        self.assertIn('owner_selection_receipt_unbound', native['reasons'])
        self.assertEqual(len(native['candidates']), int(candidate))
        if candidate:
            value = native['candidates'][0]
            self.assertEqual(value['name'], '协议装备甲')
            self.assertEqual(value['item_type'], '进阶装备')
            self.assertEqual(value['snapshot_id'], observed['snapshot_id'])
            for key in ('owned', 'location', 'owner', 'slot', 'equipped'):
                self.assertIsNone(value[key])
        gear = observed['semantic'].get('gear') or {}
        self.assertIsNot(gear.get('checked'), True)
        self.assertIsNot(gear.get('inventory_checked'), True)
        self.assertIsNone(gear.get('equipped'))
        return native

    @staticmethod
    def seed_declared_completed(run, control):
        """Already-recorded protocol receipt, not a newly published click."""
        rid = 'declared-original-tooltip-click'
        actions = [{'type': 'click', 'args': [1, 2]}, {'type': 'wait', 'args': [.7]}]
        request = dict(id=rid, kind='actions', chat_id=control.OWNER['chat_id'],
            run_token=control.OWNER['run_token'], handoff=False, actions=actions)
        receipt = dict(id=rid, request=request, result=dict(id=rid, ok=True,
            completed=copy.deepcopy(actions), input_attempted=True,
            attempted_actions=copy.deepcopy(actions[:1])))
        directory = run / 'request-ledger'
        directory.mkdir(exist_ok=True)
        path = directory / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
        control.write_json(path, receipt)
        return rid, path, path.read_bytes()

    def test_worker_read_frame_preserves_native_candidate_and_current_frame_binding(self):
        with self.fixture() as (run, records, owner, control, reader, worker):
            result = runner.entry.request(control, 'actions', ['observe'], 'tooltip-current', False)
            observed = worker.read_frame(result)
            native = self.assert_unknown_owner(observed)
            self.assertEqual(observed['capture_request_id'], result['id'])
            self.assertEqual(observed['frame_id'], result['observation']['frame_id'])
            self.assertEqual(observed['captured_at'], result['observation']['captured_at'])
            self.assertEqual(worker.state['observation']['semantic']['native_tooltips'], native)
            self.assertEqual(reader.engine.calls, 1)

            # A current immutable frame can rederive full semantics using its
            # existing primary OCR. No new capture or role binding is invented.
            again = worker.read_frame(result, reuse_primary=True)
            self.assertEqual(self.assert_unknown_owner(again), native)
            self.assertTrue(again['read_timing']['primary_ocr_reused'])
            self.assertEqual(reader.engine.calls, 1)
            self.assertEqual(len(control.published), 1)
            self.assertEqual(worker.state['statistics']['local_inputs'], 0)

    def test_worker_rejects_changed_sha_or_frame_before_reading_without_capture_retry(self):
        for field, changed in (('snapshot_sha256', '0' * 64), ('frame_id', 'f' * 32)):
            with self.subTest(field=field), self.fixture() as (run, records, owner, control, reader, worker):
                result = runner.entry.request(control, 'actions', ['observe'], 'tooltip-source', False)
                wrong = copy.deepcopy(result)
                wrong['observation'][field] = changed
                with self.assertRaises(runner.entry.ObservationUnavailable):
                    worker.read_frame(wrong)
                self.assertEqual(reader.engine.calls, 0)
                self.assertIsNone(worker.last_observation)
                self.assertEqual(len(control.published), 1)
                self.assertEqual(worker.state['statistics']['local_inputs'], 0)

    def test_manual_checkpoint_saves_candidate_but_completed_input_is_not_business_success(self):
        with self.fixture() as (run, records, owner, control, reader, worker):
            rid, original_path, original_bytes = self.seed_declared_completed(run, control)
            receipt = runner.await_existing_receipt(run, control, rid, 0)
            self.assertEqual(runner.manual_receipt_state(receipt)['state'], 'completed')

            # This uses the real first-phase checkpoint and _manual_capture.
            # Its phase stays pending; the fixture does not approve rewards or
            # turn a tooltip read into an equipment/lineup completion review.
            checkpoint = runner.begin_manual_phase(run, owner, control, 'manual-one', 'rewards', reader=reader)
            self.assertEqual(checkpoint['status'], 'pending')
            self.assertNotIn('outcome', checkpoint)
            self.assertNotIn('review', checkpoint)
            self.assertEqual(checkpoint['binding']['old_epoch'], 'old-epoch')
            captured = checkpoint['before']
            native = self.assert_unknown_owner(captured['observation'])
            self.assertIn(rid, captured['receipt_watermark'])
            self.assertEqual(hashlib.sha256(Path(captured['evidence_file']).read_bytes()).hexdigest(),
                             captured['snapshot_id'])
            capture_receipt = runner.await_existing_receipt(run, control, captured['receipt_id'], 0)
            self.assertEqual(runner.manual_receipt_state(capture_receipt)['state'], 'zero_input')
            self.assertEqual(captured['frame_id'], capture_receipt['result']['observation']['frame_id'])
            saved = runner.entry.read_json(run / 'manual-results' / (checkpoint['checkpoint_id'] + '.json'))
            archive = runner.entry.read_json(records / ('manual-' + checkpoint['checkpoint_id'] + '.json'))
            self.assertEqual(saved['before']['observation']['semantic']['native_tooltips'], native)
            self.assertEqual(archive, runner.redact(saved))
            self.assertEqual(original_path.read_bytes(), original_bytes)
            self.assertEqual(len(control.published), 1)
            self.assertEqual(reader.engine.calls, 1)

    def test_manual_capture_refuses_epoch_change_during_the_actual_read(self):
        with self.fixture() as (run, records, owner, control, reader, worker):
            binding, unused = runner._manual_binding(run, owner, control, 'manual-one')
            reader.engine.after_read = lambda: control.write_json(
                run / 'runner-resume-epoch.json', {'id': 'changed-epoch'})
            with self.assertRaisesRegex(ValueError, '观察期间发生新接管/新局'):
                runner._manual_capture(run, owner, control, binding, records, 'tooltip-epoch', reader)
            self.assertEqual(len(control.published), 1)
            self.assertEqual(reader.engine.calls, 1)
            self.assertFalse((run / 'manual-results').exists())

    def test_missing_tooltip_returns_unknown_from_one_manual_capture_without_resending_original(self):
        with self.fixture(candidate=False) as (run, records, owner, control, reader, worker):
            rid, original_path, original_bytes = self.seed_declared_completed(run, control)
            binding, unused = runner._manual_binding(run, owner, control, 'manual-one')
            captured = runner._manual_capture(run, owner, control, binding, records, 'tooltip-missing', reader)
            self.assert_unknown_owner(captured['observation'], candidate=False)
            self.assertIn(rid, captured['receipt_watermark'])
            self.assertEqual(original_path.read_bytes(), original_bytes)
            self.assertEqual(len(control.published), 1)
            self.assertEqual(reader.engine.calls, 1)
            self.assertFalse((run / 'manual-results').exists())


if __name__ == '__main__':
    unittest.main()
