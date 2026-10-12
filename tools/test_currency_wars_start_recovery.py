"""Portable contract tests. Mock OS evidence is NOT Windows recovery acceptance."""
import ast
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import currency_wars_source_guard as guard


class DeadStartRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.path = self.root / 'docs/source-activity' / ('a' * 32 + '.json')
        self.path.parent.mkdir(parents=True)
        self.value = dict(schema=1, project=str(self.root), kind='start', pid=110184,
                          process_identity='windows:134362871807656452',
                          children=[dict(pid=109668, process_identity='windows:134362871820681395')],
                          children_incomplete=True)
        self.payload = json.dumps(self.value, indent=2).encode()
        self.path.write_bytes(self.payload)
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.uptime = 1000000
        self.machine = 'b' * 32
        self.census = [dict(pid=900, process_identity='windows:100', executable='C:/Python/python.exe',
                           command_sha256='c' * 64, operator=True, actor_reasons=['interpreter_or_shell'])]
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(guard, 'mutation_lock', lambda *a, **k: contextlib.nullcontext()))
        self.stack.enter_context(patch.object(guard, 'identity_state', return_value='dead'))
        self.stack.enter_context(patch.object(guard, 'windows_uptime_ms', side_effect=lambda: self.uptime))
        self.stack.enter_context(patch.object(guard, 'windows_machine_id', side_effect=lambda: self.machine))
        self.stack.enter_context(patch.object(guard, 'source_hashes', return_value={'guard.py': 'd' * 64}))
        self.scanner = self.stack.enter_context(patch.object(guard, 'process_census', side_effect=lambda _: copy.deepcopy(self.census)))

    def run_recovery(self, **kwargs):
        return guard.recover_dead_start(self.root, self.path.name, self.digest,
                                        self.value['pid'], self.value['process_identity'], **kwargs)

    def prepared_after_restart(self):
        self.assertTrue(self.run_recovery(prepare=True)['restart_still_required'])
        self.uptime = 1000

    def test_prepare_does_not_retire_and_same_boot_census_is_insufficient(self):
        self.assertTrue(self.run_recovery(prepare=True)['prepared'])
        self.assertIsNotNone(guard.activity_reason(self.root))
        with self.assertRaisesRegex(RuntimeError, 'boot'):
            self.run_recovery(apply=True)
        self.scanner.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.payload)

    def test_missing_intent_refuses_even_if_all_processes_appear_dead(self):
        with self.assertRaises(OSError):
            self.run_recovery(apply=True)
        self.assertIsNotNone(guard.activity_reason(self.root))

    def test_reboot_dry_run_then_retire_preserves_original_and_deduplicates(self):
        self.prepared_after_restart()
        self.assertTrue(self.run_recovery()['checked_only'])
        self.assertIsNotNone(guard.activity_reason(self.root))
        self.assertTrue(self.run_recovery(apply=True)['retired'])
        self.assertEqual(self.path.read_bytes(), self.payload)
        self.assertIsNone(guard.activity_reason(self.root))
        self.assertTrue(self.run_recovery(apply=True)['already_retired'])
        receipt = guard.read_object(guard._recovery_path(self.root, self.path), limit=2_000_000)
        self.assertEqual(receipt['historical_parentage'], 'unknown')
        self.assertFalse(receipt['input_sent'])
        self.assertEqual(bytes.fromhex(receipt['original_bytes_hex']), self.payload)

    def test_pid_reuse_is_dead_but_inaccessible_is_unknown(self):
        # Exercise the real creation-identity classifier, not PID existence.
        with patch.object(guard, 'identity_state', wraps=ORIGINAL_IDENTITY_STATE):
            with patch.object(guard, 'process_identity', return_value=('active', 'windows:999')):
                self.assertEqual(guard.identity_state(110184, self.value['process_identity']), 'dead')
            with patch.object(guard, 'process_identity', return_value=('unknown', None)):
                self.assertEqual(guard.identity_state(110184, self.value['process_identity']), 'unknown')

    def test_parent_or_wrapper_active_unknown_refuses(self):
        for pid in (110184, 109668):
            for state in ('active', 'unknown'):
                with self.subTest(pid=pid, state=state), patch.object(guard, 'identity_state',
                        side_effect=lambda p, identity: state if p == pid else 'dead'):
                    with self.assertRaises(ValueError):
                        self.run_recovery(prepare=True)

    def test_exact_selection_and_non_start_are_not_adopted(self):
        with self.assertRaises(ValueError):
            guard.recover_dead_start(self.root, '../x', self.digest, 110184, self.value['process_identity'])
        with self.assertRaises(ValueError):
            guard.recover_dead_start(self.root, self.path.name, '0' * 64, 110184, self.value['process_identity'])
        changed = dict(self.value, kind='runner')
        self.path.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            self.run_recovery(prepare=True)

    def test_new_or_unknown_activity_and_current_broker_still_block(self):
        self.prepared_after_restart()
        other = self.path.with_name('e' * 32 + '.json')
        other.write_text(json.dumps(dict(self.value, kind='runner')))
        with self.assertRaises(RuntimeError):
            self.run_recovery(apply=True)
        other.unlink()
        current = self.root / 'docs/CURRENT_RUNNER.json'
        current.write_text(json.dumps(dict(owner='currency-wars-runner', run_id='retained', runner_pid=22,
                                           runner_creation_id='200', exit_evidence={'broker': {}})))
        with patch.object(guard, 'identity_state', side_effect=lambda p, i: 'unknown' if p is None else 'dead'):
            with self.assertRaisesRegex(RuntimeError, 'broker'):
                self.run_recovery(apply=True)

    def test_census_failure_or_new_process_refuses_without_receipt(self):
        self.prepared_after_restart()
        with patch.object(guard, 'process_census', side_effect=RuntimeError('access unknown')):
            with self.assertRaises(RuntimeError):
                self.run_recovery(apply=True)
        with patch.object(guard, 'process_census', side_effect=[self.census, self.census + [{'pid': 901}]]):
            with self.assertRaisesRegex(RuntimeError, 'census changed'):
                self.run_recovery(apply=True)
        self.assertFalse(guard._recovery_path(self.root, self.path).exists())

    def test_source_change_and_lease_change_refuse(self):
        self.prepared_after_restart()
        with patch.object(guard, 'source_hashes', side_effect=[{'a': '1'}, {'a': '2'}]):
            with self.assertRaisesRegex(RuntimeError, 'Source or activity'):
                self.run_recovery(apply=True)
        def changing_scan(_):
            self.path.write_bytes(self.payload + b' ')
            return self.census
        with patch.object(guard, 'process_census', side_effect=changing_scan):
            with self.assertRaises(ValueError):
                self.run_recovery(apply=True)

    def test_machine_change_and_uptime_not_reset_refuse(self):
        self.run_recovery(prepare=True)
        for uptime, machine in ((1000000, self.machine), (2000000, self.machine), (1000, 'f' * 32)):
            with self.subTest(uptime=uptime, machine=machine):
                self.uptime, self.machine = uptime, machine
                with self.assertRaises(RuntimeError):
                    self.run_recovery(apply=True)

    def test_tampered_or_partial_receipt_never_waives_guard(self):
        self.prepared_after_restart()
        self.run_recovery(apply=True)
        receipt = guard._recovery_path(self.root, self.path)
        original = receipt.read_bytes()
        for content in (b'{', original.replace(b'retired_after_boot_boundary', b'approved')):
            receipt.write_bytes(content)
            self.assertIsNotNone(guard.activity_reason(self.root))
        receipt.write_bytes(original)
        self.path.write_bytes(self.payload + b' ')
        self.assertIsNotNone(guard.activity_reason(self.root))

    def test_other_new_active_lease_remains_blocking_after_retirement(self):
        self.prepared_after_restart()
        self.run_recovery(apply=True)
        other = self.path.with_name('e' * 32 + '.json')
        other.write_text(json.dumps(dict(self.value, kind='start', pid=501, children=[], children_incomplete=False)))
        with patch.object(guard, 'identity_state', side_effect=lambda p, i: 'active' if p == 501 else 'dead'):
            self.assertIsNotNone(guard.activity_reason(self.root))

    def test_linked_audit_directory_refuses(self):
        outside = self.root / 'elsewhere'
        outside.mkdir()
        (self.root / 'docs/source-activity-recovery').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_recovery(prepare=True)

    def test_broad_actor_scope_does_not_exempt_an_unrelated_shell(self):
        for image in ('python.exe', 'pythonw.exe', 'pwsh.exe', 'cmd.exe', 'node.exe', 'CurrencyWars.exe'):
            self.assertTrue(guard._actor_reasons(self.root, 'C:/apps/' + image, ['unrelated']))
        self.assertTrue(guard._actor_reasons(self.root, 'C:/renamed.exe', [str(self.root / 'tools/run.py')]))


ORIGINAL_IDENTITY_STATE = guard.identity_state


class LaunchRegistrationTests(unittest.TestCase):
    def test_registration_persists_exact_launch_not_inferred_parentage(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(guard, 'process_identity', return_value=('active', 'windows:100')), \
                patch.object(guard, 'mutation_lock', lambda *a, **k: contextlib.nullcontext()):
            start = guard.Activity(tmp, 'start')
            start.write()
            start.bind_launch('root', 'f' * 32)
            worker = guard.Activity(tmp, 'runner')
            worker.write()
            worker.bind_launch('root', 'f' * 32, start.path.name)
            candidate = start.registered_launch_child()
            self.assertEqual(candidate['runner_creation_id'], '100')
            self.assertEqual(candidate['launch_id'], 'f' * 32)
            self.assertNotIn('parentage_verified', candidate)
            worker.value['launch_id'] = 'e' * 32
            worker.write()
            self.assertIsNone(start.registered_launch_child())

    def test_worker_registers_before_resource_validation_and_initialization(self):
        # Execute the actual platform orchestration body with inert services;
        # no heavy controller import or real Windows/game behavior is simulated.
        source = Path(__file__).with_name('currency_wars_runner.py').read_text(encoding='utf8')
        tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'worker_cli')
        order = []
        lease = SimpleNamespace(bind_launch=lambda *a: order.append(('bind', a)))
        @contextlib.contextmanager
        def activity(*args):
            order.append(('activity', args))
            yield lease
        namespace = dict(activity=activity, PROJECT='project',
                         reviewed_runtime_sources=lambda chat: order.append(('review', chat)),
                         _worker_cli=lambda args: order.append(('worker', args.chat_id)))
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<worker_cli>', 'exec'), namespace)
        namespace['worker_cli'](SimpleNamespace(chat_id='root', launch_id='f' * 32, source_start_lease='a' * 32 + '.json'))
        self.assertEqual([row[0] for row in order], ['activity', 'bind', 'review', 'worker'])


class LiveCensusContractTests(unittest.TestCase):
    def test_native_census_rejects_unknown_access_new_process_and_extra_interpreter(self):
        import psutil
        own = os.getpid()
        def identity(pid):
            return ('active', 'windows:' + str(pid + 1000))
        def process(pid):
            return SimpleNamespace(exe=lambda: '/apps/python.exe' if pid == own else '/apps/notepad.exe',
                                   cmdline=lambda: ['operator' if pid == own else 'note', 'private-argument'])
        with patch.object(guard.os, 'name', 'nt'), patch.object(guard, 'process_identity', side_effect=identity), \
                patch.object(psutil, 'pids', return_value=[0, 4, own, own + 1]), \
                patch.object(psutil, 'Process', side_effect=process):
            result = guard.process_census('/project')
            self.assertEqual(len(result), 2)
            self.assertNotIn('private-argument', json.dumps(result))
            with patch.object(psutil, 'Process', side_effect=psutil.AccessDenied(own + 1)):
                with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                    guard.process_census('/project')
            with patch.object(psutil, 'pids', side_effect=[[own], [own, own + 2]]):
                with self.assertRaisesRegex(RuntimeError, 'population changed'):
                    guard.process_census('/project')
            with patch.object(psutil, 'Process', return_value=SimpleNamespace(
                    exe=lambda: '/apps/python.exe', cmdline=lambda: ['python', 'unrelated.py'])):
                with self.assertRaisesRegex(RuntimeError, 'Active controller'):
                    guard.process_census('/project')
            with patch.object(psutil, 'Process', return_value=SimpleNamespace(
                    exe=lambda: '/apps/notepad.exe', cmdline=lambda: [])):
                with self.assertRaisesRegex(RuntimeError, 'Unknown'):
                    guard.process_census('/project')


class StartOrchestrationTests(unittest.TestCase):
    def test_parent_native_checks_and_transfers_early_registration_before_current(self):
        source = Path(__file__).with_name('currency_wars_runner.py').read_text(encoding='utf8')
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == '_start_cli')
        events = []
        registered = dict(launch_id='f' * 32, chat_id='root', runner_pid=200, runner_creation_id='300')
        lease = Mock()
        lease.path = Path('a' * 32 + '.json')
        lease.bind_launch.side_effect = lambda *a: events.append('bound-before-popen')
        lease.registered_launch_child.return_value = registered
        lease.transfer_to_registered_child.side_effect = lambda *a: events.append('transferred')
        child = SimpleNamespace(pid=100, poll=lambda: None)
        control = SimpleNamespace(C=SimpleNamespace(set_last_error=lambda n: None, get_last_error=lambda: 0),
                                  k=SimpleNamespace(CreateMutexW=lambda *a: 1, ReleaseMutex=lambda h: None,
                                                    CloseHandle=lambda h: None))
        def popen(command, **kwargs):
            self.assertIn('--source-start-lease', command)
            self.assertEqual(events[0], 'bound-before-popen')
            events.append('popen')
            return child
        def verify(*args):
            events.append('native-verified')
            return 200, 'windows:300'
        samples = iter([None, None, registered])
        def optional(_):
            value = next(samples)
            if value is not None:
                self.assertIn('transferred', events)
            return value
        namespace = dict(entry=SimpleNamespace(backend=lambda: control, PINNED='source'),
            hashlib=hashlib, optional=optional, CURRENT='current', uuid=SimpleNamespace(uuid4=lambda: SimpleNamespace(hex='f' * 32)),
            input_bridge=SimpleNamespace(runtime_location=lambda *a, **k: {}, runtime_child_environment=lambda *a: {}),
            json=json, sys=SimpleNamespace(executable='python'), SELF='runner.py',
            subprocess=SimpleNamespace(Popen=popen, DEVNULL=-3, CREATE_NO_WINDOW=0),
            artifacts=SimpleNamespace(process_identity=lambda pid: ('active', 'windows:100')),
            registered_worker_identity=verify, envelope=lambda v: v,
            time=SimpleNamespace(monotonic=Mock(side_effect=[0, 1, 2]), sleep=lambda s: None))
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<start_cli>', 'exec'), namespace)
        args = SimpleNamespace(chat_id='root', max_seconds=60, max_matches=1, continue_matches=False)
        self.assertEqual(namespace['_start_cli'](args, lease), registered)
        self.assertLess(events.index('native-verified'), events.index('transferred'))


if __name__ == '__main__':
    unittest.main()
