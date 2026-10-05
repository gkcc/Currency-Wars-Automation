"""Regression checks for local startup compatibility; no game or GUI input."""
import ast
import contextlib
import io
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import currency_wars_artifacts as artifacts
import currency_wars_broker_entry as entry
import currency_wars_perception as perception
import currency_wars_runner as runner
import currency_wars_source_guard as guard


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_update_notice_is_not_a_panel_mentioned_in_its_body(self):
        rows = [
            {'text': '货币战争·零和博奔赛季扩充说明V4.4', 'confidence': .95, 'box': [452, 251, 1048, 291]},
            {'text': '详情', 'confidence': .95, 'box': [1300, 260, 1349, 287]},
            {'text': '扩充内容概览', 'confidence': .95, 'box': [456, 737, 613, 765]},
            {'text': '预期收益与羁绊链路也新增了部分内容，竞争对手阵营更加丰富',
             'confidence': .95, 'box': [456, 545, 1440, 584]},
        ]
        self.assertEqual(perception.classify(rows), 'update_notice')
        for index in (0, 1, 2):
            with self.subTest(missing_modal_marker=index):
                self.assertNotEqual(perception.classify(rows[:index] + rows[index+1:]), 'update_notice')
        moved = [dict(row, box=[0, 0, 100, 40]) for row in rows]
        self.assertNotEqual(perception.classify(moved), 'update_notice')

    def test_recognized_update_notice_only_closes_and_rereads(self):
        worker = object.__new__(runner.Worker)
        worker.wait_page, worker.wait_started = None, None
        worker.node_guard = lambda observed: True
        commands = []
        worker.command = lambda *args: commands.append(args)
        worker.tick({'page': 'update_notice', 'fields': {}})
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], ['key:27', 'wait:0.7'])
        self.assertEqual(commands[0][2], 'update_notice')

    def test_manual_wait_does_not_spend_node_budget_or_extend_hard_deadline(self):
        worker = object.__new__(runner.Worker)
        worker.panel_index, worker.panel_state = 0, 'enter'
        worker.node_key, worker.node_started = ('panel', 0, 'enter'), 100.0
        worker.node_attempts, worker.node_consecutive, worker.node_last_page = 1, 1, 'lobby'
        worker.wait_started, worker.deadline = 100.0, 7200.0
        halted = []
        worker.pause_internal = halted.append
        with patch.object(runner.time, 'monotonic', return_value=520.0):
            worker.account_manual_wait(120.0)
            self.assertTrue(worker.node_guard({'page': 'lobby', 'fields': {}}))
        self.assertEqual(worker.wait_started, 500.0)
        self.assertEqual(worker.deadline, 7200.0)
        self.assertEqual(worker.node_attempts, 2)
        with patch.object(runner.time, 'monotonic', return_value=681.0):
            self.assertFalse(worker.node_guard({'page': 'lobby', 'fields': {}}))
        self.assertEqual(len(halted), 1)
        worker.node_attempts = 70
        with patch.object(runner.time, 'monotonic', return_value=682.0):
            worker.account_manual_wait(681.0)
            self.assertFalse(worker.node_guard({'page': 'lobby', 'fields': {}}))
        self.assertEqual(worker.node_attempts, 70)

    def test_new_ipc_directory_grants_its_real_user_inheritable_access(self):
        with artifacts.scratch_directory('currency-wars-compatibility-test') as outer:
            with artifacts.scratch_directory('currency-wars-runner-acl-test', root=outer) as runtime:
                marker = artifacts.read_marker(runtime, root=outer)
                with self.assertRaises(artifacts.ArtifactError):
                    artifacts.prepare_elevated_ipc_access(runtime, root=outer, expected_run_id='0'*32)
                access = artifacts.prepare_elevated_ipc_access(runtime, root=outer,
                                                               expected_run_id=marker['run_id'])
                self.assertEqual(artifacts.read_marker(runtime, root=outer), marker)
                child = runtime / 'inherited-file.json'
                child.write_text('{"real":"readable"}', encoding='utf8')
                saved_acl = runtime / 'inherited-file.acl'
                checked = subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/icacls.exe'),
                                          str(child), '/save', str(saved_acl)],
                                         capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(checked.returncode, 0, checked.stderr.decode('utf8', errors='replace'))
                self.assertRegex(saved_acl.read_text(encoding='utf-16-le'),
                                 r'\(A;[^;]*ID[^;]*;(?:FA|0x1f01ff);;;'+re.escape(access['user_sid'])+r'\)')
                self.assertEqual(json.loads(child.read_text(encoding='utf8')), {'real': 'readable'})
                artifacts.protect_children(runtime, [], root=outer, complete=False)
                with self.assertRaises(artifacts.ArtifactError):
                    artifacts.prepare_elevated_ipc_access(runtime, root=outer, expected_run_id=marker['run_id'])
                artifacts.protect_children(runtime, [], root=outer, complete=True)

    def test_windows_launcher_failures_preserve_utf8_and_ansi_diagnostics(self):
        message = 'Start-Process : 由于用户取消了操作，启动失败。'
        self.assertEqual(runner.decode_launcher_output(message.encode('utf-8-sig')), message)
        self.assertEqual(runner.decode_launcher_output(message.encode('mbcs')), message)
        self.assertEqual(runner.decode_launcher_output(b'{"Id":123}'), '{"Id":123}')
        self.assertIsInstance(runner.decode_launcher_output(b'\xff'), str)

    def test_authenticated_run_does_not_depend_on_elevated_temp_directory(self):
        with artifacts.scratch_directory('currency-wars-compatibility-test') as outer:
            with artifacts.scratch_directory('elevation-temp-test', root=outer) as runtime:
                marker = artifacts.read_marker(runtime, root=outer)
                owner = {'owner': 'currency-wars-control', 'chat_id': 'inert-elevation-check',
                         'run_token': 'fixture-only', 'artifact_run_id': marker['run_id'],
                         'artifact_chat_id': marker.get('session_hint', {}).get('id')}
                (runtime / 'owner.json').write_text(json.dumps(owner), encoding='utf8')
                with patch('tempfile.tempdir', str(outer / 'different-elevated-temp')):
                    with self.assertRaises(artifacts.ArtifactError):
                        artifacts.read_marker(runtime)
                    control = SimpleNamespace()
                    found, actual_owner = entry.authenticate(runtime, 'inert-elevation-check', 'fixture-only', control)
                    self.assertEqual(found, runtime)
                    self.assertEqual(actual_owner, owner)
                    self.assertEqual(control.ROOT, str(runtime))
                    for chat, token in [('other-chat', 'fixture-only'), ('inert-elevation-check', 'wrong')]:
                        with self.assertRaises(ValueError):
                            entry.authenticate(runtime, chat, token, SimpleNamespace())

    def test_installed_child_registration_and_verified_exit_cleanup(self):
        with artifacts.scratch_directory('currency-wars-compatibility-test') as outer:
            with artifacts.scratch_directory('owned-child-test', root=outer) as runtime:
                child = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-B', '-c', 'import time; time.sleep(20)'],
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    state, identity = artifacts.process_identity(child.pid)
                    self.assertEqual(state, 'active')
                    artifacts.protect_children(runtime, [{'pid': child.pid, 'process_identity': identity}],
                                               root=outer, complete=False)
                    with self.assertRaises(artifacts.ArtifactError):
                        artifacts.protect_children(runtime, [], root=outer, complete=True)
                    self.assertTrue(runtime.exists())
                finally:
                    child.terminate()
                    child.wait(timeout=5)
                    artifacts.protect_children(runtime, [], root=outer, complete=True)
            self.assertFalse(runtime.exists())
        self.assertFalse(outer.exists())

    def test_external_marker_change_is_not_adopted_by_registration_or_close(self):
        with artifacts.scratch_directory('currency-wars-compatibility-test') as outer:
            with artifacts.scratch_directory('owned-marker-test', root=outer) as runtime:
                marker = runtime / artifacts.MARKER
                original = marker.read_bytes()
                value = json.loads(original)
                value['created_at'] += 1
                marker.write_text(json.dumps(value), encoding='utf8')
                try:
                    with self.assertRaises(artifacts.ArtifactError):
                        artifacts.protect_children(runtime, [], root=outer, complete=True)
                    owned = artifacts._installed_leases[os.path.normcase(str(runtime))]
                    with self.assertRaises(artifacts.ArtifactError):
                        owned.close()
                    self.assertTrue(runtime.exists())
                finally:
                    marker.write_bytes(original)

    def test_incomplete_and_unknown_children_keep_owned_directory(self):
        with artifacts.scratch_directory('currency-wars-compatibility-test') as outer:
            with artifacts.scratch_directory('owned-incomplete-test', root=outer) as runtime:
                owned = artifacts._installed_leases[os.path.normcase(str(runtime))]
                artifacts.protect_children(runtime, [], root=outer, complete=False)
                with self.assertRaises(artifacts.ArtifactError):
                    owned.close()
                native = artifacts.installed
                current = artifacts.process_identity(os.getpid())[1]
                with patch.object(native, 'process_identity', return_value=('unknown', None)):
                    with self.assertRaises(artifacts.ArtifactError):
                        artifacts.protect_children(runtime, [{'pid': os.getpid(), 'process_identity': current}],
                                                   root=outer, complete=True)
                artifacts.protect_children(runtime, [], root=outer, complete=True)

    def test_venv_redirector_real_child_is_registered_and_wrong_identity_rejected(self):
        command = 'import json,os,time; print(json.dumps({"pid":os.getpid()}),flush=True); time.sleep(20)'
        child = subprocess.Popen([sys.executable, '-B', '-c', command], stdout=subprocess.PIPE,
                                 text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        actual_pid = None
        try:
            actual_pid = json.loads(child.stdout.readline())['pid']
            child_identity = artifacts.process_identity(child.pid)[1]
            actual_identity = artifacts.process_identity(actual_pid)[1]
            self.assertIsNotNone(actual_identity)
            def probe(pid, creation):
                expected = 'windows:' + creation
                return {'state': 'running' if artifacts.process_identity(pid) == ('active', expected) else 'reused'}
            control = SimpleNamespace(process_probe=probe)
            value = {'launch_id': 'this-launch', 'chat_id': 'this-chat', 'runner_pid': actual_pid,
                     'runner_creation_id': actual_identity.split(':', 1)[1]}
            self.assertEqual(runner.registered_worker_identity(value, 'this-launch', 'this-chat',
                             child.pid, child_identity, control), (actual_pid, actual_identity))
            for field, bad in [('launch_id', 'other-launch'), ('chat_id', 'other-chat'),
                               ('runner_creation_id', '1')]:
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    runner.registered_worker_identity({**value, field: bad}, 'this-launch', 'this-chat',
                                                      child.pid, child_identity, control)
            with self.assertRaises(RuntimeError):
                runner.registered_worker_identity(value, 'this-launch', 'this-chat', child.pid,
                                                  'windows:1', control)
        finally:
            if actual_pid is not None and artifacts.process_identity(actual_pid) == ('active', actual_identity):
                runner.psutil.Process(actual_pid).terminate()
            if child.poll() is None:
                child.wait(timeout=5)
            child.stdout.close()

    def test_transfer_keeps_original_redirector_protected(self):
        value = dict(kind='runner', pid=22, process_identity='windows:222')
        lease = object.__new__(guard.Activity)
        lease.project = Path('D:/inert-project')
        lease.path = Path('D:/inert-project/activity/start.json')
        wrapper = {'pid': 11, 'process_identity': 'windows:111'}
        lease.value = {'children': [wrapper], 'children_incomplete': True}
        lease.write = lambda: None
        with patch.object(guard, 'mutation_lock', return_value=contextlib.nullcontext()), \
                patch.object(Path, 'iterdir', return_value=[Path('inert-worker.json')]), \
                patch.object(guard, 'read_object', return_value=value), \
                patch.object(guard, 'identity_state', return_value='active'):
            lease.transfer_to_registered_child(22, 'windows:222', 'runner')
            self.assertEqual(lease.value['children'], [wrapper])
            self.assertFalse(lease.value['children_incomplete'])
            with self.assertRaises(RuntimeError):
                lease.transfer_to_registered_child(22, 'windows:wrong', 'runner')

    def test_secure_desktop_null_foreground_never_allows_input(self):
        source = runner.PROJECT / 'tools/currency_wars_control.py'
        tree = ast.parse(source.read_text(encoding='utf8'))
        function = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == 'status')
        for foreground in (None, 0):
            namespace = {'read_optional': lambda _: None, 'Path': Path, 'ROOT': 'D:/inert-nonexistent',
                         'u': SimpleNamespace(GetForegroundWindow=lambda: foreground),
                         'win': lambda: (123, 456, [0, 0, 1920, 1080]),
                         'BINDING': {'hwnd': 123, 'creation_id': '789'}, 'OWNER': {'chat_id': 'inert'},
                         'PROTOCOL_VERSION': 2, 'gamepad_activity': lambda: {}}
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
            result = namespace['status']()
            self.assertFalse(result['ready'])
            self.assertFalse(result['game_foreground'])
            self.assertIsNone(result['broker_pid'])
            self.assertEqual(result['foreground'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
