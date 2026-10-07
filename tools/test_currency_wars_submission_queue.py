"""Production mutex/Entry transport with inert publishers; no desktop inputs."""
import ast
import contextlib
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import currency_wars_broker_entry as entry


BUSY = 'another request is pending; no concurrent submission'


def transport(root):
    """Load exact filesystem-lock/validation functions without native APIs."""
    names = {'submission_lock', 'write_json', 'validate_actions', 'point'}
    tree = ast.parse(Path(__file__).with_name('currency_wars_control.py').read_bytes())
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in body} != names:
        raise AssertionError('Production transport functions changed')
    scope = dict(ROOT=str(root), OWNER={'chat_id': 'fixture-chat', 'run_token': 'fixture-token'},
                 contextlib=contextlib, Path=Path, os=os, json=json, uuid=uuid, time=time, math=math)
    exec(compile(ast.Module(body=body, type_ignores=[]), '<production filesystem mutex>', 'exec'), scope)
    control = SimpleNamespace(ROOT=str(root), OWNER=scope['OWNER'],
                              status=lambda: {'ready': True, 'paused': False, 'input_halted': False},
                              write_json=scope['write_json'], validate_actions=scope['validate_actions'],
                              published=[], busy=threading.Event(), lock_entries=0)

    @contextlib.contextmanager
    def lock():
        control.lock_entries += 1
        manager = scope['submission_lock']()
        try:
            manager.__enter__()
        except RuntimeError:
            control.busy.set()
            raise
        try:
            yield
        finally:
            manager.__exit__(None, None, None)

    def publish(value):
        control.published.append(value)
        control.write_json(root / 'result.json', {'id': value['id'], 'ok': True,
                                                'completed': value.get('actions', [])})

    control.submission_lock, control.publish_request = lock, publish
    return control


def receipt_path(root, rid):
    return root / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')


class SubmissionQueueTests(unittest.TestCase):
    def test_manual_capture_and_input_share_lease_while_worker_observe_waits(self):
        with tempfile.TemporaryDirectory(prefix='currency-wars-queue-') as temporary:
            root = Path(temporary)
            control = transport(root)
            timings = []
            with ThreadPoolExecutor(max_workers=1) as pool:
                with entry.submission_lease(control, queue_wait=timings.append) as leased:
                    entry.request(leased, 'actions', ['observe'], 'manual-before', False)
                    lock_bytes = (root / 'client-submit.lock').read_bytes()
                    observer = pool.submit(entry.request, control, 'actions', ['observe'], 'worker-observe', False,
                                           queue_wait=timings.append)
                    self.assertTrue(control.busy.wait(1), 'Worker must actually contend on the original mutex')
                    self.assertFalse(receipt_path(root, 'worker-observe').exists())
                    self.assertEqual((root / 'client-submit.lock').read_bytes(), lock_bytes)
                    self.assertEqual(len(control.published), 1)
                    entry.request(leased, 'actions', ['click:1:2'], 'manual-step', False)
                    self.assertEqual([value['id'] for value in control.published], ['manual-before', 'manual-step'])
                self.assertEqual(observer.result(timeout=2)['id'], 'worker-observe')
            self.assertEqual([value['id'] for value in control.published],
                             ['manual-before', 'manual-step', 'worker-observe'])
            self.assertEqual(len(timings), 2)
            self.assertEqual([value['contended'] for value in timings], [False, True])
            for value in timings:
                self.assertEqual(set(value), {'start_ns', 'end_ns', 'outcome', 'contended',
                                              'lease_reused', 'request_published'})
                self.assertLessEqual(value['start_ns'], value['end_ns'])
                self.assertEqual(value['outcome'], 'acquired')
                self.assertFalse(value['request_published'])
                self.assertFalse(value['lease_reused'])
            self.assertFalse((root / 'client-submit.lock').exists())
            with self.assertRaisesRegex(RuntimeError, 'not active for this caller'):
                entry.request(leased, 'actions', ['observe'], 'expired-lease', False)
            self.assertFalse(receipt_path(root, 'expired-lease').exists())
            with entry.submission_lease(control) as held, ThreadPoolExecutor(max_workers=1) as pool:
                transferred = pool.submit(entry.request, held, 'actions', ['observe'], 'other-thread', False)
                with self.assertRaisesRegex(RuntimeError, 'not active for this caller'):
                    transferred.result(timeout=1)
            self.assertFalse(receipt_path(root, 'other-thread').exists())

    def test_busy_queue_and_earlier_deadline_refuse_before_ledger_without_deleting_lock(self):
        with tempfile.TemporaryDirectory(prefix='currency-wars-queue-') as temporary:
            root = Path(temporary)
            control = transport(root)
            clock = [100.]
            timings = []
            def advance(seconds):
                clock[0] += seconds
            with control.submission_lock():
                lock_bytes = (root / 'client-submit.lock').read_bytes()
                with patch.object(entry.time, 'monotonic', side_effect=lambda: clock[0]), \
                     patch.object(entry.time, 'sleep', side_effect=advance):
                    started = clock[0]
                    with self.assertRaises(entry.SubmissionQueueTimeout) as refused:
                        entry.request(control, 'actions', ['observe'], 'queue-expired', False,
                                      queue_wait=timings.append)
                    self.assertFalse(refused.exception.request_published)
                    self.assertLessEqual(clock[0] - started, 2.000001)
                    self.assertFalse(receipt_path(root, 'queue-expired').exists())
                    started = clock[0]
                    with self.assertRaises(entry.SubmissionDeadlineExpired) as expired:
                        entry.request(control, 'actions', ['click:1:2'], 'deadline-expired', False,
                                      submit_deadline=started + .075, queue_wait=timings.append)
                    self.assertFalse(expired.exception.request_published)
                    self.assertLessEqual(clock[0] - started, .075001)
                    self.assertFalse(receipt_path(root, 'deadline-expired').exists())
                    # Payload rejection precedes queueing and cannot acquire or
                    # remove the other caller's mutex even at an expired deadline.
                    attempts = control.lock_entries
                    with self.assertRaises(ValueError):
                        entry.request(control, 'actions', ['key:999'], 'invalid-payload', False)
                    self.assertEqual(control.lock_entries, attempts)
                self.assertEqual((root / 'client-submit.lock').read_bytes(), lock_bytes)
                self.assertEqual(control.published, [])
                self.assertFalse((root / 'request-ledger').exists())
                self.assertEqual([value['outcome'] for value in timings], ['busy', 'deadline'])
                self.assertTrue(all(value['contended'] for value in timings))

    def test_runner_owned_raw_input_refuses_but_reads_resume_and_original_receipts_remain_available(self):
        with tempfile.TemporaryDirectory(prefix='currency-wars-queue-') as temporary:
            root = Path(temporary)
            control = transport(root)
            control.write_json(root / 'runner-owner.json', {'fixture': 'runner-owned'})
            cases = [(['click:1:2'], False), (['key:68'], False), (['key:70'], False),
                     (['drag:1:2:3:4'], False), (['scroll:1:2:120'], False), (['observe'], True)]
            for index, (tokens, handoff) in enumerate(cases):
                with self.subTest(tokens=tokens, handoff=handoff), \
                     self.assertRaisesRegex(ValueError, 'manual-step/decide'):
                    entry.request(control, 'actions', tokens, 'raw-' + str(index), handoff)
            self.assertEqual(control.lock_entries, 0)
            self.assertFalse((root / 'request-ledger').exists())
            self.assertEqual(control.published, [])
            self.assertTrue(entry.request(control, 'actions', ['observe'], 'read', False)['ok'])
            self.assertTrue(entry.request(control, 'actions', ['wait:0.1'], 'wait', False)['ok'])
            self.assertTrue(entry.request(control, 'resume', [], 'resume', True)['resumed'])
            control.business_guarded_submission = True
            guarded = entry.request(control, 'actions', ['click:1:2'], 'guarded', False)
            del control.business_guarded_submission
            published = len(control.published)
            self.assertEqual(entry.request(control, 'actions', ['click:1:2'], 'guarded', False), guarded)
            self.assertEqual(len(control.published), published)
            with self.assertRaisesRegex(ValueError, 'different payload'):
                entry.request(control, 'actions', ['click:3:4'], 'guarded', False)

    def test_runner_ownership_appearing_during_acquisition_is_checked_inside_original_mutex(self):
        with tempfile.TemporaryDirectory(prefix='currency-wars-queue-') as temporary:
            root = Path(temporary)
            control = transport(root)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with control.submission_lock():
                    pending = pool.submit(entry.request, control, 'actions', ['click:1:2'], 'raced-owner', False)
                    self.assertTrue(control.busy.wait(1))
                    control.write_json(root / 'runner-owner.json', {'fixture': 'runner-owned'})
                with self.assertRaisesRegex(ValueError, 'manual-step/decide'):
                    pending.result(timeout=2)
            self.assertFalse((root / 'request-ledger').exists())
            self.assertEqual(control.published, [])

    def test_publisher_busy_text_is_not_retried_and_pending_or_same_id_never_republishes(self):
        with tempfile.TemporaryDirectory(prefix='currency-wars-queue-') as temporary:
            root = Path(temporary)
            control = transport(root)
            def uncertain_publish(value):
                control.published.append(value)
                # This exception is inside the lease, not a failed acquisition.
                raise RuntimeError(BUSY)
            control.publish_request = uncertain_publish
            with self.assertRaisesRegex(RuntimeError, '^' + BUSY + '$'):
                entry.request(control, 'actions', ['click:1:2'], 'one-input', False)
            self.assertEqual(control.lock_entries, 1)
            self.assertEqual(len(control.published), 1)
            original = receipt_path(root, 'one-input').read_bytes()
            self.assertIsNone(json.loads(original)['result'])
            self.assertFalse((root / 'client-submit.lock').exists())
            with self.assertRaisesRegex(RuntimeError, 'input outcome unknown, never resend'):
                entry.request(control, 'actions', ['click:1:2'], 'one-input', False,
                              submit_deadline=time.monotonic() - 1)
            self.assertEqual(receipt_path(root, 'one-input').read_bytes(), original)
            with self.assertRaisesRegex(ValueError, 'different payload'):
                entry.request(control, 'actions', ['click:4:5'], 'one-input', False)
            self.assertEqual(len(control.published), 1)
            self.assertEqual(receipt_path(root, 'one-input').read_bytes(), original)
            result = {'id': 'one-input', 'ok': True, 'completed': control.published[0]['actions']}
            control.write_json(root / 'result.json', result)
            self.assertEqual(entry.request(control, 'actions', ['click:1:2'], 'one-input', False,
                                           submit_deadline=time.monotonic() - 1), result)
            self.assertEqual(entry.request(control, 'actions', ['click:1:2'], 'one-input', False), result)
            self.assertEqual(len(control.published), 1)
            self.assertEqual(entry.read_json(receipt_path(root, 'one-input'))['result'], result)

    def test_lease_and_production_guarded_submission_compose_without_reentering_original_mutex(self):
        from currency_wars_runner import GuardedSubmission
        for lease_outside_guard in (True, False):
            with self.subTest(lease_outside_guard=lease_outside_guard), \
                 tempfile.TemporaryDirectory(prefix='currency-wars-queue-') as temporary:
                root = Path(temporary)
                control = transport(root)
                control.write_json(root / 'runner-owner.json', {'fixture': 'runner-owned'})
                calls = []
                def guard(value):
                    calls.append(value['id'])
                    if value['id'] == 'refused-step':
                        raise RuntimeError(BUSY)
                if lease_outside_guard:
                    guarded = GuardedSubmission(control, guard)
                    guarded.business_guarded_submission = True
                    manager = entry.submission_lease(guarded)
                else:
                    manager = entry.submission_lease(control)
                with manager as leased:
                    if lease_outside_guard:
                        client = leased
                    else:
                        client = GuardedSubmission(leased, guard)
                        client.business_guarded_submission = True
                    entry.request(client, 'actions', ['click:1:2'], 'safe-step', False)
                    self.assertEqual(control.lock_entries, 1)
                    self.assertTrue((root / 'client-submit.lock').exists())
                    with self.assertRaisesRegex(RuntimeError, '^' + BUSY + '$'):
                        entry.request(client, 'actions', ['click:3:4'], 'refused-step', False)
                    self.assertEqual(control.lock_entries, 1)
                    self.assertTrue((root / 'client-submit.lock').exists())
                    self.assertFalse(receipt_path(root, 'refused-step').exists())
                self.assertEqual(calls, ['safe-step', 'refused-step'])
                self.assertEqual([value['id'] for value in control.published], ['safe-step'])
                self.assertFalse((root / 'client-submit.lock').exists())

    def test_queue_diagnostic_failure_does_not_replace_acquisition_deadline_busy_or_published_body_outcome(self):
        import currency_wars_profile as profile_module
        for outcome in ('acquired', 'busy', 'deadline', 'body'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory(prefix='currency-wars-queue-') as temporary:
                root = Path(temporary)
                control = transport(root)
                class Hook:
                    profile = profile_module.ProfileRecorder(root / 'profile', run_id='fixture-run', enabled=True)
                    def record(self, value):
                        raise RuntimeError('callback-private-value-must-not-be-exported')
                hook = Hook()
                if outcome == 'acquired':
                    with entry.submission_lease(control, queue_wait=hook.record) as leased:
                        self.assertTrue(leased.queue_wait_diagnostic_failed)
                        result = entry.request(leased, 'actions', ['observe'], 'allowed', False)
                    self.assertTrue(result['ok'])
                    self.assertEqual(len(control.published), 1)
                elif outcome == 'busy':
                    with control.submission_lock():
                        lock_bytes = (root / 'client-submit.lock').read_bytes()
                        with self.assertRaises(entry.SubmissionQueueTimeout) as error:
                            with entry.submission_lease(control, timeout=0, queue_wait=hook.record):
                                self.fail('Busy lock entered')
                        self.assertTrue(error.exception.queue_wait_diagnostic_failed)
                        self.assertFalse(error.exception.request_published)
                        self.assertEqual((root / 'client-submit.lock').read_bytes(), lock_bytes)
                    self.assertEqual(control.published, [])
                    self.assertFalse((root / 'request-ledger').exists())
                elif outcome == 'deadline':
                    with self.assertRaises(entry.SubmissionDeadlineExpired) as error:
                        entry.request(control, 'actions', ['observe'], 'expired', False,
                                      submit_deadline=time.monotonic() - 1, queue_wait=hook.record)
                    self.assertTrue(error.exception.queue_wait_diagnostic_failed)
                    self.assertFalse(error.exception.request_published)
                    self.assertEqual(control.published, [])
                    self.assertFalse((root / 'request-ledger').exists())
                else:
                    def unknown(value):
                        control.published.append(value)
                        raise ValueError('original publisher failure')
                    control.publish_request = unknown
                    with self.assertRaisesRegex(ValueError, '^original publisher failure$'):
                        entry.request(control, 'actions', ['click:1:2'], 'unknown', False, queue_wait=hook.record)
                    self.assertEqual(len(control.published), 1)
                    original = receipt_path(root, 'unknown').read_bytes()
                    self.assertIsNone(entry.read_json(receipt_path(root, 'unknown'))['result'])
                    with self.assertRaisesRegex(RuntimeError, 'input outcome unknown, never resend'):
                        entry.request(control, 'actions', ['click:1:2'], 'unknown', False)
                    self.assertEqual(receipt_path(root, 'unknown').read_bytes(), original)
                    self.assertEqual(len(control.published), 1)
                self.assertFalse(hook.profile.enabled)
                self.assertEqual(hook.profile.error, 'RuntimeError')
                self.assertNotIn('callback-private-value', hook.profile.path.read_text())
                self.assertFalse((root / 'client-submit.lock').exists())


if __name__ == '__main__':
    unittest.main()
