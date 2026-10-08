"""Stop/start protocol regressions with inert processes and real durable files.

The authenticated load boundary is supplied by a fixture. No process, broker,
GUI, input, or image capture is launched; start stops before its Popen boundary.
"""
import contextlib
import copy
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import currency_wars_runner as runner


class StopProjectionTests(TestCase):
    @contextlib.contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory(prefix='cw-stop-projection-') as directory:
            project = Path(directory)
            run, records = project / 'runtime', project / 'debug' / 'original'
            run.mkdir()
            records.mkdir(parents=True)
            current = project / 'CURRENT_RUNNER.json'
            owner = {'owner': 'currency-wars-runner', 'chat_id': 'fixture-chat',
                'run_id': 'fixture-run', 'runner_pid': 41001, 'runner_creation_id': '410010',
                'launch_id': 'fixture-launch', 'run_token': 'inert-fixture-token'}
            broker = {'pid': 42001, 'creation_id': '420010'}
            probes = []

            def write_json(path, value):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(json.dumps(value), encoding='utf8')

            control = SimpleNamespace(OWNER=owner, write_json=write_json,
                EMERGENCY_BROKER_IDENTITY={**owner, **broker})
            control.read_optional = lambda name: runner.optional(run / name)
            control.assert_owner = lambda chat, token: self.assertEqual(
                (chat, token), (owner['chat_id'], owner['run_token']))
            control.C = SimpleNamespace(set_last_error=lambda value: None, get_last_error=lambda: 0)
            control.k = SimpleNamespace(CreateMutexW=lambda *args: 1,
                ReleaseMutex=lambda *args: None, CloseHandle=lambda *args: None)
            state = {**runner.redact(owner), 'protocol_version': 1, 'run_dir': str(run),
                'journal_file': str(records / 'journal.jsonl'), 'match_id': 'fixture-match',
                'broker_identity': broker, 'entry_receipt_watermark': ['prior-receipt'],
                'control_mode': 'auto', 'cleanup': {'removed': False, 'pending_finally': True}}
            path = records / 'business-fixture-match.json'
            business = {'schema': runner.BUSINESS_SCHEMA, 'chat_id': owner['chat_id'],
                'match_id': state['match_id'], 'revision': 5, 'status': 'active',
                'leases': [runner.business_lease(state)], 'economy': {}}
            state['business'] = {'checkpoint': str(path), 'match_id': state['match_id'],
                'status': 'active', 'continuation_required': True, 'all_rewards_completed': False}
            for target, value in ((records / 'owner.json', runner.redact(owner)),
                    (run / 'runner-owner.json', owner), (run / 'runner-state.json', state),
                    (path, business), (current, state)):
                write_json(target, value)
            final = copy.deepcopy(state)
            final.update(control_mode='stopped', cleanup={'removed': True, 'pending_finally': False},
                exit_evidence={'worker': {'state': 'exited'}, 'broker': {'state': 'exited'}})
            fixture = SimpleNamespace(project=project, run=run, records=records, current=current,
                owner=owner, control=control, state=state, final=final, business=business,
                path=path, broker=broker, probes=probes, broker_state='exited', disposed=False)

            def process_probe(pid, creation=None):
                probes.append((pid, str(creation)))
                if pid == owner['runner_pid'] and not fixture.disposed:
                    self.assertTrue((run / 'runner-stop').exists())
                    self.assertTrue((run / 'broker-stop').exists())
                    # Model the worker's final CURRENT publication and actual
                    # scratch disposal before stop's next current_state read.
                    write_json(current, final)
                    shutil.rmtree(run)
                    fixture.disposed = True
                return {'pid': pid, 'state': fixture.broker_state if pid == broker['pid'] else 'exited'}

            control.process_probe = process_probe
            with patch.object(runner, 'PROJECT', project), patch.object(runner, 'CURRENT', current), \
                    patch.object(runner, 'load', return_value=(run, owner, None, control)), \
                    patch.object(runner.entry, 'backend', return_value=control):
                yield fixture

    def stop(self, fixture):
        return runner.command_cli(SimpleNamespace(command='stop', run_dir=str(fixture.run),
            chat_id=fixture.owner['chat_id'], run_token=fixture.owner['run_token']))

    def test_real_stop_projection_reaches_start_continuation_after_runtime_disposal(self):
        with self.fixture() as f:
            # CURRENT can lack the broker identity while the authenticated
            # business lease still retains the original process creation.
            f.final.pop('broker_identity')
            result = self.stop(f)
            saved = runner.entry.read_json(f.current)
            self.assertTrue(result['ok'])
            self.assertEqual(saved['control_mode'], 'stopped')
            self.assertEqual(saved['journal_file'], f.state['journal_file'])
            self.assertEqual(saved['match_id'], 'fixture-match')
            self.assertEqual(saved['business'], f.state['business'])
            self.assertEqual(saved['broker_identity'], f.broker)
            self.assertEqual(saved['entry_receipt_watermark'], ['prior-receipt'])
            self.assertEqual(saved['cleanup'], {'removed': True, 'pending_finally': False})
            self.assertEqual(saved['exit_evidence']['broker']['expected_creation_id'], '420010')
            self.assertNotIn('run_token', saved)
            self.assertFalse(f.run.exists())

            class StartBoundary(Exception):
                pass

            def launch(command, **unused):
                continuation = json.loads(command[command.index('--business-resume-json') + 1])
                self.assertEqual(continuation['previous_run_id'], f.owner['run_id'])
                self.assertEqual(continuation['checkpoint'], str(f.path))
                self.assertTrue((f.records / 'business-archive.json').exists())
                raise StartBoundary()

            location = {'schema': 1, 'source': 'standalone',
                'runtime_root': str(f.project / 'next-runtime'), 'installation_id': None}
            args = SimpleNamespace(chat_id=f.owner['chat_id'], max_seconds=60,
                max_matches=1, continue_matches=False)
            with patch.object(runner.input_bridge, 'runtime_location', return_value=location), \
                    patch.object(runner.input_bridge, 'runtime_child_environment', return_value={}), \
                    patch.object(runner.subprocess, 'CREATE_NO_WINDOW', 0, create=True), \
                    patch.object(runner.subprocess, 'Popen', side_effect=launch) as popen:
                with self.assertRaises(StartBoundary):
                    runner._start_cli(args, SimpleNamespace(children=lambda *args, **kwargs: None))
                popen.assert_called_once()
            lease = runner.entry.read_json(f.path)['leases'][-1]
            self.assertEqual(lease['exit_evidence']['worker']['state'], 'exited')
            self.assertEqual(lease['broker_identity'], f.broker)

    def test_missing_projection_fields_can_only_come_from_matching_existing_business(self):
        with self.fixture() as f:
            f.final.pop('journal_file')
            f.final.pop('match_id')
            saved = self.stop(f)['state']
            self.assertEqual(saved['journal_file'], f.state['journal_file'])
            self.assertEqual(saved['match_id'], f.business['match_id'])
            self.assertEqual(runner.prepare_business_start(saved, f.control)['checkpoint'], str(f.path))
        with self.fixture() as f:
            for key in ('journal_file', 'match_id', 'business'):
                f.final.pop(key)
            saved = self.stop(f)['state']
            self.assertFalse({'journal_file', 'match_id', 'business'} & saved.keys())
            with self.assertRaisesRegex(ValueError, '原始回放目录'):
                runner.prepare_business_start(saved, f.control)

    def test_foreign_or_stale_current_is_not_overwritten_after_actual_stop(self):
        for key, value in (('chat_id', 'other-chat'), ('run_id', 'old-run'),
                ('runner_pid', 99991), ('runner_creation_id', '999910')):
            with self.subTest(key=key), self.fixture() as f:
                f.final[key] = value
                with self.assertRaisesRegex(ValueError, '不覆盖CURRENT'):
                    self.stop(f)
                self.assertEqual(runner.entry.read_json(f.current), f.final)
                self.assertTrue(f.disposed)

    def test_latest_business_lease_and_original_record_owner_must_match(self):
        for source, key, value in (('lease', 'run_id', 'old-run'),
                ('lease', 'runner_creation_id', '999910'), ('lease', 'chat_id', 'other-chat'),
                ('owner', 'runner_pid', 99991)):
            with self.subTest(source=source, key=key), self.fixture() as f:
                if source == 'lease':
                    f.business['leases'][-1][key] = value
                    f.control.write_json(f.path, f.business)
                else:
                    f.control.write_json(f.records / 'owner.json', {**runner.redact(f.owner), key: value})
                with self.assertRaises(ValueError):
                    self.stop(f)
                self.assertEqual(runner.entry.read_json(f.current), f.final)

    def test_business_and_broker_identity_conflicts_are_rejected(self):
        for key, value in (('broker_identity', {'pid': 99991, 'creation_id': '999910'}),
                ('journal_file', '/not-a-project-replay/journal.jsonl'), ('match_id', 'other-match')):
            with self.subTest(key=key), self.fixture() as f:
                f.final[key] = value
                with self.assertRaises(ValueError):
                    self.stop(f)
                self.assertEqual(runner.entry.read_json(f.current), f.final)

    def test_retained_original_broker_cannot_be_erased_by_a_display_terminal(self):
        for broker_state in ('running', 'unknown', 'exited'):
            with self.subTest(broker_state=broker_state), self.fixture() as f:
                f.broker_state = broker_state
                f.control.EMERGENCY_BROKER_IDENTITY = None
                f.final.pop('broker_identity')
                f.final['exit_evidence']['broker'] = {'state': 'not_launched',
                    'run_id': f.owner['run_id'], 'worker_pid': f.owner['runner_pid'],
                    'worker_creation_id': f.owner['runner_creation_id'], 'launch_id': f.owner['launch_id'],
                    'identity_observed': False, 'launch_attempted': False}
                if broker_state in ('running', 'unknown'):
                    with self.assertRaises(RuntimeError):
                        self.stop(f)
                    self.assertEqual(runner.entry.read_json(f.current), f.final)
                else:
                    result = self.stop(f)
                    self.assertEqual(result['exit_evidence']['broker']['state'], 'exited')
                    self.assertEqual(result['state']['broker_identity'], f.broker)
                    self.assertEqual(result['state']['broker']['broker_state']['state'], 'exited')
                self.assertIn((f.broker['pid'], f.broker['creation_id']), f.probes)
