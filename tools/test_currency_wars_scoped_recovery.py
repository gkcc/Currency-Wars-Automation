"""Portable recovery protocol tests; native Windows acceptance remains separate."""
import contextlib
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import currency_wars_scoped_recovery as recovery
import currency_wars_source_guard as guard
import currency_wars_input_bridge as bridge


class ScopedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.lease = self.root / 'docs/source-activity' / ('a' * 32 + '.json')
        self.lease.parent.mkdir(parents=True)
        self.value = dict(schema=1, project=str(self.root), kind='start', pid=11,
                          process_identity='windows:11', children_incomplete=True,
                          children=[dict(pid=12, process_identity='windows:12')])
        self.raw = json.dumps(self.value, indent=2).encode(); self.lease.write_bytes(self.raw)
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.run = self.root / 'original-run'; self.run.mkdir()
        self.marker = dict(schema=1,tool=guard.artifacts.TOOL,purpose="currency-wars-runner-test",pid=13, process_identity='windows:13', children_incomplete=False,
                           protected_children=[], path=str(self.run), root=str(self.root), run_id='b' * 32)
        (self.run / guard.artifacts.MARKER).write_text(json.dumps(self.marker))
        self.owner = self.root / 'replay-owner.json'
        self.owner.write_text(json.dumps(dict(owner='currency-wars-runner', run_id='b' * 32,
                                             runner_pid=13, runner_creation_id='13', launch_id='c' * 32)))
        self.old_binding = self.root / 'old-binding.json'
        self.old_binding.write_text(json.dumps(dict(pid=80,creation_id=80,hwnd=80)))
        self.actors = [dict(pid=100, identity='windows:100', role='operator_or_venv_parent')]
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(guard, 'mutation_lock', lambda *a: contextlib.nullcontext()))
        self.stack.enter_context(patch.object(bridge, 'inbox_lock', lambda *a: contextlib.nullcontext()))
        self.stack.enter_context(patch.object(recovery, 'validate_game', return_value=None))
        self.stack.enter_context(patch.object(recovery, 'game_lock', lambda *a, **kw: contextlib.nullcontext()))
        self.stack.enter_context(patch.object(guard, 'identity_state', return_value='dead'))
        self.stack.enter_context(patch.object(guard, 'windows_machine_id', return_value='d' * 32))
        self.stack.enter_context(patch.object(guard, 'source_hashes', return_value={'source': 'e' * 64}))
        self.stack.enter_context(patch.object(recovery, 'bridge_evidence', return_value={'installed': False}))
        self.stack.enter_context(patch.object(recovery, 'census', return_value=self.actors))

    def call(self, **kwargs):
        return recovery.recover(self.root, self.lease.name, self.digest, 11, 'windows:11',
                                self.run, self.owner, 13, 'windows:13',
                                dict(pid=90, creation_id=90, hwnd=90), self.old_binding, **kwargs)

    def test_read_only_then_apply_preserves_bytes_and_deduplicates(self):
        before = {p: p.read_bytes() for p in (self.lease, self.owner, self.run / guard.artifacts.MARKER)}
        self.assertTrue(self.call(accept_scope=True)['checked_only'])
        self.assertFalse(recovery.receipt_path(self.root, self.lease).exists())
        self.assertIsNotNone(guard.activity_reason(self.root))
        self.assertTrue(self.call(accept_scope=True, apply=True)['retired'])
        self.assertIsNone(guard.activity_reason(self.root))
        self.assertTrue(self.call(accept_scope=True, apply=True)['already_retired'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        receipt = json.loads(recovery.receipt_path(self.root, self.lease).read_text())
        self.assertEqual(receipt['historical_parentage'], 'unknown')
        self.assertFalse(receipt['input_sent'])

    def test_requires_explicit_scope(self):
        with self.assertRaisesRegex(ValueError, 'acceptance'):
            self.call(apply=True)

    def test_active_unknown_known_identity_refused(self):
        for state in ('active', 'unknown'):
            with self.subTest(state=state), patch.object(guard, 'identity_state', return_value=state):
                with self.assertRaises((RuntimeError, ValueError)):
                    self.call(accept_scope=True, apply=True)
        self.assertFalse(recovery.receipt_path(self.root, self.lease).exists())

    def test_other_unknown_lease_not_waived(self):
        (self.lease.parent / ('f' * 32 + '.json')).write_text(json.dumps(self.value))
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            self.call(accept_scope=True, apply=True)

    def test_existing_current_broker_unknown_not_waived(self):
        (self.root / 'docs/CURRENT_RUNNER.json').write_text(json.dumps(dict(owner='currency-wars-runner',
              run_id='prior', runner_pid=22, runner_creation_id='22', exit_evidence={'broker': {'state': 'unknown'}})))
        def identity(pid, value): return 'dead' if pid else 'unknown'
        with patch.object(guard, 'identity_state', side_effect=identity):
            with self.assertRaisesRegex(RuntimeError, 'broker'):
                self.call(accept_scope=True, apply=True)

    def test_actor_changes_refuse(self):
        with patch.object(recovery, 'census', side_effect=[self.actors, self.actors + [dict(pid=101)]]):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                self.call(accept_scope=True, apply=True)

    def test_source_changes_refuse(self):
        with patch.object(guard, 'source_hashes', side_effect=[{'a':'1'}, {'a':'2'}]):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                self.call(accept_scope=True, apply=True)

    def test_broker_stage_artifacts_refuse(self):
        for name in ('broker-process.json', 'bridge-dispatch.json', 'broker-ready.json', 'request.json'):
            with self.subTest(name=name):
                p = self.run / name; p.write_text('{}')
                try:
                    with self.assertRaisesRegex(RuntimeError, 'controller stage'):
                        self.call(accept_scope=True)
                finally: p.unlink()

    def test_complete_marker_is_required(self):
        self.marker['children_incomplete'] = True
        (self.run / guard.artifacts.MARKER).write_text(json.dumps(self.marker))
        with self.assertRaisesRegex(RuntimeError, 'evidence'):
            self.call(accept_scope=True)

    def test_original_change_invalidates_receipt(self):
        self.call(accept_scope=True, apply=True)
        self.lease.write_bytes(self.raw + b' ')
        self.assertIsNotNone(guard.activity_reason(self.root))

    def test_run_change_invalidates_receipt(self):
        self.call(accept_scope=True, apply=True)
        (self.run / 'late.json').write_text('{}')
        self.assertIsNotNone(guard.activity_reason(self.root))

    def test_tampered_receipt_refuses(self):
        self.call(accept_scope=True, apply=True)
        p = recovery.receipt_path(self.root, self.lease); r = json.loads(p.read_text())
        r['historical_parentage'] = 'proven'; p.write_text(json.dumps(r))
        self.assertIsNotNone(guard.activity_reason(self.root))

    def test_both_game_locks_are_required(self):
        with patch.object(recovery, 'game_lock', side_effect=RuntimeError('occupied')):
            with self.assertRaisesRegex(RuntimeError, 'occupied'):
                self.call(accept_scope=True, apply=True)
        self.assertFalse(recovery.receipt_path(self.root, self.lease).exists())
        with patch.object(recovery, 'game_lock', side_effect=lambda *a, **kw: contextlib.nullcontext()) as locks:
            self.call(accept_scope=True)
        self.assertEqual(locks.call_count, 2)
        self.assertEqual(locks.call_args_list[0].args[0]['pid'],80)
        self.assertTrue(locks.call_args_list[0].kwargs['exited'])
        self.assertEqual(locks.call_args_list[1].args[0]['pid'],90)

    def test_last_game_validation_failure_writes_no_receipt(self):
        with patch.object(recovery, 'validate_game', side_effect=RuntimeError('changed')):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                self.call(accept_scope=True, apply=True)
        self.assertFalse(recovery.receipt_path(self.root, self.lease).exists())

    def test_scheduled_inspection_is_read_only_and_active_instance_refuses(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('scoped_task_check', recovery.__file__)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        inbox=self.root/'inbox';inbox.mkdir()
        config=dict(inbox=str(inbox),task_name='fixed-task')
        with patch.object(module,'INBOX',inbox),patch.object(bridge,'configuration',return_value=config), \
                patch.object(bridge,'task_rpc',return_value={'instances':[]}) as rpc:
            self.assertEqual(module.bridge_evidence(self.root)['instances'],[])
            rpc.assert_called_once_with(config,'inspect')
            rpc.return_value={'instances':[{'id':'live'}]}
            with self.assertRaisesRegex(RuntimeError,'instances'): module.bridge_evidence(self.root)

    def test_bridge_pending_request_refuses_without_deleting(self):
        inbox = self.root / 'inbox'; inbox.mkdir(); request = inbox / 'launch.json'; request.write_text('{}')
        # Invoke original implementation, not the transaction fixture patch.
        import importlib.util
        spec=importlib.util.spec_from_file_location('scoped_bridge_check', recovery.__file__)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(module, 'INBOX', inbox), patch.object(module, 'BRIDGE', self.root / 'absent'):
            with self.assertRaisesRegex(RuntimeError, 'pending'):
                module.bridge_evidence(self.root)
        self.assertEqual(request.read_text(), '{}')


class ProcessClassificationTests(unittest.TestCase):
    def facts(self, pid, ppid, name, argv):
        return dict(pid=pid, ppid=ppid, name=name, exe='C:\\bin\\'+name,
                    identity='windows:'+str(pid), argv=argv)

    def base(self):
        argv=['D:\\repo\\tools\\currency_wars_scoped_recovery.py', '--project', 'D:\\repo']
        own=self.facts(100,50,'python.exe',['C:\\bin\\python.exe',*argv])
        parent=self.facts(50,0,'powershell.exe',['powershell.exe','-NoLogo'])
        return argv,[own,parent],{100:own,50:parent}

    def test_interactive_parent_and_exact_venv_parent_allowed(self):
        argv,rows,details=self.base()
        result=recovery.classify(rows,details,'D:\\repo',100,argv)
        self.assertEqual({r['role'] for r in result},{'interactive_operator_parent','operator_or_venv_parent'})
        parent=self.facts(75,50,'python.exe',['C:\\bin\\python.exe',*argv])
        details[100]['ppid']=75;rows.append(parent);details[75]=parent
        self.assertEqual(len(recovery.classify(rows,details,'D:\\repo',100,argv)),3)

    def test_project_actor_and_opaque_interpreter_refused(self):
        for args in (['python.exe','D:\\repo\\tools\\currency_wars_runner.py','worker'],
                     ['python.exe','-c','something'],['python.exe'],['python.exe','-m','other']):
            argv,rows,details=self.base(); item=self.facts(200,0,'python.exe',args)
            rows.append(item);details[200]=item
            with self.assertRaises(RuntimeError): recovery.classify(rows,details,'D:\\repo',100,argv)

    def test_scripted_parent_not_blindly_exempt(self):
        argv,rows,details=self.base();details[50]['argv']+=['-Command','Start-Process python.exe']
        with self.assertRaises(RuntimeError): recovery.classify(rows,details,'D:\\repo',100,argv)

    def test_unrelated_absolute_script_classified_without_disclosing_command(self):
        argv,rows,details=self.base();item=self.facts(200,0,'python.exe',['python.exe','C:\\other\\job.py','secret'])
        rows.append(item);details[200]=item
        result=recovery.classify(rows,details,'D:\\repo',100,argv)
        self.assertNotIn('secret',json.dumps(result))

    def test_protected_system_not_opened_but_relevant_access_denied_refuses(self):
        argv,rows,details=self.base();rows.append(dict(pid=332,ppid=0,name='lsass.exe'))
        with patch.object(guard,'_source_bytes',return_value=b'mcp'), patch.object(recovery,'MCP_SHA256',hashlib.sha256(b'mcp').hexdigest()), \
                patch.object(recovery,'native_process_table',return_value=rows), \
                patch.object(recovery.os,'getpid',return_value=100),patch.object(recovery.sys,'argv',argv), \
                patch.object(recovery,'process_details',side_effect=lambda row:details[row['pid']]) as query:
            self.assertEqual(len(recovery.census('D:\\repo')),2)
            self.assertEqual({c.args[0]['pid'] for c in query.call_args_list},{50,100})
        with patch.object(guard,'_source_bytes',return_value=b'mcp'), patch.object(recovery,'MCP_SHA256',hashlib.sha256(b'mcp').hexdigest()), \
                patch.object(recovery,'native_process_table',return_value=rows), \
                patch.object(recovery,'process_details',side_effect=RuntimeError('Relevant access unavailable')):
            with self.assertRaises(RuntimeError): recovery.census('D:\\repo')

    def test_same_project_mcp_entry_allowed_and_argument_text_not_controller(self):
        argv,rows,details=self.base()
        item=self.facts(200,0,'python.exe',['python.exe','-B','-X','utf8','D:\\repo\\tools\\currency_wars_mcp.py'])
        rows.append(item); details[200]=item
        self.assertEqual(recovery.classify(rows,details,'D:\\repo',100,argv)[-1]['role'],'reviewed_nonlaunching_mcp')
        details[200]['argv']=['python.exe','C:\\other\\diagnose.py','currency_wars_runner.py']
        self.assertEqual(recovery.classify(rows,details,'D:\\repo',100,argv)[-1]['role'],'unrelated_direct_script')

    def test_literal_operator_shell_command_only(self):
        argv,rows,details=self.base()
        child=details[100]['argv']
        command="& "+" ".join("'"+a+"'" for a in child)+"; exit $LASTEXITCODE"
        details[50]['argv']=['powershell.exe','-NoProfile','-Command',command]
        result=recovery.classify(rows,details,'D:\\repo',100,argv)
        self.assertIn('literal_operator_shell_parent',{r['role'] for r in result})
        details[50]['argv'][-1]+='; Start-Process python.exe'
        with self.assertRaises(RuntimeError): recovery.classify(rows,details,'D:\\repo',100,argv)

    def test_expected_supported_names(self):
        for name in ('python.exe','pythonw.exe','python3.13.exe','py.exe','powershell.exe','currency-wars-gui.exe'):
            self.assertTrue(recovery.candidate(name))
        for name in ('lsass.exe','svchost.exe','System','StarRail.exe'):
            self.assertFalse(recovery.candidate(name))


if __name__ == '__main__': unittest.main()
