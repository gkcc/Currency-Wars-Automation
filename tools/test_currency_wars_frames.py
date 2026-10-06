"""Actual frame/receipt paths with synthetic pixels and no Windows/game inputs."""
import ast
import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from PIL import Image

import currency_wars_broker_entry as entry


def frame_control(root):
    """Compile the production functions without importing any native input API."""
    names = {'_publish_png', '_check_frame_capacity', 'observe', 'attach_observation', 'execute_batch',
             'write_json', 'utc_now', 'validate_actions', 'point'}
    tree = ast.parse(Path(__file__).with_name('currency_wars_control.py').read_bytes())
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in body} != names:
        raise AssertionError('Production frame functions changed')
    state = dict(ROOT=str(root), OWNER={'chat_id': 'test-chat', 'run_token': 'test-token'},
                 Path=Path, time=time, os=os, io=io, uuid=uuid, hashlib=hashlib, stat=stat,
                 math=math, json=json, Image=Image, datetime=datetime, timezone=timezone,
                 BATCH_DEADLINE=None, assert_owner=Mock(), acknowledge_pause=Mock(),
                 input_guard=Mock(), begin_batch=Mock(return_value=333), wait_guarded=Mock(),
                 click=Mock(), key=Mock(), drag=Mock(), scroll=Mock(),
                 win=Mock(return_value=(333, 999, (0, 0, 1920, 1080))),
                 u=SimpleNamespace(GetForegroundWindow=Mock(return_value=333)),
                 ImageGrab=SimpleNamespace(grab=Mock(side_effect=lambda **unused: Image.new('RGB', (1920, 1080), 'black'))))
    def read_optional(name):
        path = root / name
        return json.loads(path.read_text()) if path.exists() else None
    state['read_optional'] = read_optional
    exec(compile(ast.Module(body=body, type_ignores=[]), '<actual broker, mocked game>', 'exec'), state)
    return state


def action_request(actions, rid='input-one'):
    return {'id': rid, 'kind': 'actions', 'chat_id': 'test-chat', 'run_token': 'test-token',
            'handoff': False, 'actions': actions}


class ImmutableFrameTests(unittest.TestCase):
    def test_delayed_reader_and_concurrent_observers_keep_each_receipt_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = frame_control(root)
            first = control['observe']('worker-first')
            result = {'id': 'worker-first', 'observation': first}
            path = entry.observation_frame(root, result)
            original_bytes = path.read_bytes()
            with Image.open(path) as delayed:
                control['ImageGrab'].grab.side_effect = lambda **unused: Image.new('RGB', (1920, 1080), 'white')
                with ThreadPoolExecutor(max_workers=2) as pool:
                    observations = list(pool.map(control['observe'], ['worker-next', 'supervisor']))
                delayed.load()
                self.assertEqual(delayed.getpixel((0, 0)), (0, 0, 0))
            self.assertEqual(path.read_bytes(), original_bytes)
            self.assertEqual(len({first['snapshot'], *(value['snapshot'] for value in observations)}), 3)
            for request_id, observation in zip(['worker-next', 'supervisor'], observations):
                receipt = {'id': request_id, 'observation': observation}
                with Image.open(entry.observation_frame(root, receipt)) as image:
                    self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
                self.assertEqual(entry.observation_frame(root, receipt, original=True), Path(observation['original']))
            self.assertFalse((root / 'game-preview.png').exists())
            self.assertFalse(list(root.rglob('*.tmp')))

    def test_partial_encoded_png_never_reaches_published_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = frame_control(root)
            buffer = io.BytesIO()
            Image.new('RGB', (1920, 1080), 'black').save(buffer, format='PNG')
            payload = buffer.getvalue()[:len(buffer.getvalue()) // 2]
            with Image.open(io.BytesIO(payload)) as image:
                self.assertEqual(image.size, (1920, 1080))  # Header alone passes open.
            broken = SimpleNamespace(size=(1920, 1080), load=lambda: None,
                                     save=lambda stream, **unused: stream.write(payload))
            target = root / 'preview.png'
            with self.assertRaises(OSError):
                control['_publish_png'](broken, target)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_rename_sharing_retry_keeps_same_staging_and_never_overwrites_a_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = frame_control(root)
            target, moves = root / 'preview.png', []
            actual_replace = os.replace
            def replace(source, destination):
                self.assertEqual(Path(source).parent, Path(destination).parent)
                self.assertFalse(target.exists())
                with Image.open(source) as decoded:
                    decoded.load()
                moves.append((source, destination))
                if len(moves) == 1:
                    error = PermissionError('simulated sharing violation')
                    error.winerror = 32
                    raise error
                return actual_replace(source, destination)
            with Image.new('RGB', (1920, 1080), 'black') as image, patch.object(os, 'replace', replace):
                control['_publish_png'](image, target)
            self.assertEqual(len(moves), 2)
            self.assertEqual(moves[0], moves[1])
            original = target.read_bytes()
            with Image.new('RGB', (1920, 1080), 'white') as image, self.assertRaises(ValueError):
                control['_publish_png'](image, target)
            self.assertEqual(target.read_bytes(), original)

    def test_receipt_identity_hash_and_full_decode_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = frame_control(root)
            observation = control['observe']('receipt-one')
            result = {'id': 'receipt-one', 'observation': observation}
            for altered in ({**result, 'id': 'receipt-two'},
                            {**result, 'observation': {**observation, 'snapshot': str(root / 'game-preview.png')}},
                            {**result, 'observation': {**observation, 'captured_at': None}},
                            {**result, 'observation': {**observation, 'captured_at': 'invalid date'}},
                            {**result, 'observation': {**observation, 'captured_at': '2026-10-07T01:00:00'}},
                            {**result, 'observation': {'snapshot': observation['snapshot']}}):
                with self.assertRaises(entry.ObservationUnavailable):
                    entry.observation_frame(root, altered)
            path = Path(observation['snapshot'])
            truncated = path.read_bytes()[:path.stat().st_size // 2]
            path.write_bytes(truncated)
            with self.assertRaisesRegex(entry.ObservationUnavailable, 'hash mismatch'):
                entry.observation_frame(root, result)
            observation['snapshot_sha256'] = hashlib.sha256(truncated).hexdigest()
            with self.assertRaises(entry.ObservationUnavailable):
                entry.observation_frame(root, result)

    def test_release_only_consumed_final_receipt_keeps_other_frames_and_never_republishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = frame_control(root)
            published = []
            def publish_request(request):
                published.append(request)
                control['write_json'](root / 'result.json', control['execute_batch'](request))
            client = SimpleNamespace(**control, status=lambda: {'ready': True},
                submission_lock=contextlib.nullcontext, publish_request=publish_request)
            first = entry.request(client, 'actions', ['observe'], 'consumed', False)
            other = entry.request(client, 'actions', ['observe'], 'still-reading', False)
            ledger = root / 'request-ledger' / (hashlib.sha256(first['id'].encode()).hexdigest() + '.json')
            actual = ledger.read_bytes()
            value = json.loads(actual)
            value['result'] = None
            control['write_json'](ledger, value)
            with self.assertRaises(entry.ObservationUnavailable):
                entry.release_observation(root, first)
            self.assertTrue(Path(first['observation']['snapshot']).exists())
            ledger.write_bytes(actual)
            with self.assertRaises(entry.ObservationUnavailable):
                entry.release_observation(root, {**first, 'observation': other['observation']})
            release = entry.release_observation(root, first)
            self.assertTrue(release['released'])
            self.assertGreater(release['released_bytes'], 0)
            self.assertEqual(ledger.read_bytes(), actual)
            entry.observation_frame(root, other)
            with self.assertRaises(entry.ObservationUnavailable):
                entry.observation_frame(root, first)
            self.assertTrue(entry.release_observation(root, first)['already_released'])
            self.assertEqual(entry.request(client, 'actions', ['observe'], 'consumed', False), first)
            self.assertEqual(len(published), 2)
            self.assertEqual(control['ImageGrab'].grab.call_count, 2)

    def test_capture_storage_bound_stops_without_evicting_or_repeating_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = frame_control(root)
            frame_root = root / 'frames'
            frame_root.mkdir()
            retained = frame_root / 'retained.png'
            with retained.open('wb') as stream:
                stream.truncate(384 * 1024 * 1024)
            result = control['execute_batch'](action_request([{'type': 'click', 'args': [1, 2]}]))
            self.assertTrue(result['ok'])
            self.assertEqual(result['completed'], [{'type': 'click', 'args': [1.0, 2.0]}])
            self.assertIn('capacity reached', result['observation_error'])
            self.assertTrue(retained.exists())
            control['ImageGrab'].grab.assert_not_called()
            control['click'].assert_called_once()
            self.assertFalse((root / 'input-halted.json').exists())

    def test_component_and_client_pin_match_the_changed_actual_broker(self):
        import currency_wars_bridge_task as task
        actual = hashlib.sha256(entry.SOURCE.read_bytes()).hexdigest().upper()
        self.assertEqual(actual, entry.PINNED)
        self.assertEqual(actual, task.PINNED_BROKER_SHA256)


class ObservationFailureTests(unittest.TestCase):
    def test_capture_failure_reobserves_but_does_not_repeat_completed_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = frame_control(root)
            observe = Mock(side_effect=[OSError('image file is truncated'), {'frame': 'new'}])
            control['observe'] = observe
            actions = [{'type': 'click', 'args': [1, 2]}]
            result = control['execute_batch'](action_request(actions))
            self.assertTrue(result['ok'])
            self.assertEqual(result['completed'], actions)
            self.assertTrue(result['input_attempted'])
            self.assertEqual(result['attempted_actions'], actions)
            control['click'].assert_called_once_with(1.0, 2.0)
            self.assertEqual(observe.call_count, 2)
            self.assertEqual(result['observation_attempts'], 2)
            self.assertNotIn('observation_error', result)
            self.assertFalse((root / 'input-halted.json').exists())
            intervals = result['profile_intervals']
            self.assertEqual([item['operation'] for item in intervals], ['unknown', 'input_animation', 'capture'])
            for index, interval in enumerate(intervals):
                self.assertLessEqual(interval['start_ns'], interval['end_ns'])
                if index:
                    self.assertLessEqual(intervals[index - 1]['end_ns'], interval['start_ns'])

    def test_unavailable_frame_is_separate_from_successful_input_and_pure_observe(self):
        for actions in ([{'type': 'click', 'args': [1, 2]}], [{'type': 'observe', 'args': []}]):
            with self.subTest(actions=actions), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                control = frame_control(root)
                control['observe'] = Mock(side_effect=OSError('image file is truncated'))
                result = control['execute_batch'](action_request(actions))
                self.assertTrue(result['ok'])
                self.assertEqual(result['completed'], actions)
                self.assertEqual(result['input_attempted'], actions[0]['type'] == 'click')
                self.assertTrue(result['observation_retry_allowed'])
                self.assertNotIn('observation', result)
                self.assertEqual(control['observe'].call_count, 2)
                self.assertLessEqual(control['click'].call_count, 1)
                self.assertFalse((root / 'input-halted.json').exists())

    def test_input_then_focus_failure_keeps_attempt_evidence_and_latches_halt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = frame_control(root)
            control['click'].side_effect = RuntimeError('focus lost after physical click')
            control['observe'] = Mock(return_value={'frame': 'fresh'})
            click, key = {'type': 'click', 'args': [1, 2]}, {'type': 'key', 'args': [70]}
            result = control['execute_batch'](action_request([click, key]))
            self.assertFalse(result['ok'])
            self.assertEqual(result['completed'], [])
            self.assertTrue(result['input_attempted'])
            self.assertEqual(result['attempted_actions'], [click])
            self.assertEqual(result['failing_action_index'], 0)
            self.assertTrue((root / 'input-halted.json').exists())
            control['click'].assert_called_once()
            control['key'].assert_not_called()

    def test_paused_broker_refuses_input_but_still_allows_passive_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = frame_control(root)
            control['write_json'](root / 'manual-pause.json', {'pause_id': 'new-pause'})
            control['observe'] = Mock(return_value={'frame': 'fresh'})
            with self.assertRaisesRegex(RuntimeError, 'manual pause latched'):
                control['execute_batch'](action_request([{'type': 'click', 'args': [1, 2]}]))
            control['click'].assert_not_called()
            result = control['execute_batch'](action_request([{'type': 'observe', 'args': []}]))
            self.assertTrue(result['ok'])
            self.assertFalse(result['input_attempted'])
            self.assertTrue((root / 'manual-pause.json').exists())

    def test_same_receipt_with_failed_frame_never_republishes_actions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control = frame_control(root)
            control['observe'] = Mock(side_effect=OSError('image file is truncated'))
            published = []
            def publish_request(request):
                published.append(request)
                control['write_json'](root / 'result.json', control['execute_batch'](request))
            client = SimpleNamespace(**control, status=lambda: {'ready': True},
                submission_lock=contextlib.nullcontext, publish_request=publish_request)
            first = entry.request(client, 'actions', ['click:1:2'], 'same-request', False)
            again = entry.request(client, 'actions', ['click:1:2'], 'same-request', False)
            self.assertEqual(first, again)
            self.assertEqual(len(published), 1)
            self.assertTrue(first['ok'])
            self.assertTrue(first['observation_retry_allowed'])
            control['click'].assert_called_once()
            self.assertEqual(control['observe'].call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
