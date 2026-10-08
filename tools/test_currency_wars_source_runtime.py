"""Production inventory and child-runtime contracts; no GUI/broker execution."""
import copy
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import currency_wars_artifacts as artifacts
import currency_wars_broker_entry as entry
import currency_wars_input_bridge as bridge
import currency_wars_source_guard as guard
import currency_wars_update as updater


PROJECT = Path(__file__).resolve().parents[1]


class SourceRuntimeTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix='currency-wars-source-runtime-test-')
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def source_copy(self):
        project = self.root / 'generated-source-copy'
        manifest = json.loads((PROJECT / guard.SOURCE_MANIFEST).read_text(encoding='utf8'))
        for name in manifest['files']:
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT / name, target)
        return project

    def resource_fixture(self, project):
        # Explicit byte/inventory protocol only: inert files are never decoded
        # and do not claim recognition or coverage of the 57 private assets.
        files = {
            'tools/shop_reader_resources/SOURCES.json': json.dumps({'resources': [{'file': 'recommend_badge.png'}]}).encode(),
            'tools/shop_reader_resources/names.json': b'{"names":[]}',
            'tools/shop_reader_resources/recommend_badge.png': b'generated inert badge bytes',
            'tools/shop_reader_resources/state_reader/SOURCES.json': json.dumps({'resources': [{'file': 'unit.png'}]}).encode(),
            'tools/shop_reader_resources/state_reader/unit.png': b'generated inert unit bytes',
        }
        for name, payload in files.items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        for name in ('refresh_offer_resources', 'reward_resources'):
            shutil.copytree(PROJECT / 'tools' / name, project / 'tools' / name)
        return guard.runtime_source_binding(project)

    def ready_fixture(self, project):
        ready = {**self.resource_fixture(project), 'ready': True, 'owner': 'currency-wars-runner',
                 'chat_id': 'explicit-protocol-chat', 'independent_review': {
                     'status': 'PASS', 'reviewer_chat_id': 'generated-reviewer-fixture',
                     'reviewed_at': 'explicit protocol premise, not an actual approval'}}
        path = project / 'docs/RUNNER_READY.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(ready), encoding='utf8')
        return ready

    def test_inventory_covers_lazy_dependencies_and_rejects_missing_or_changed_bytes(self):
        project = self.source_copy()
        reviewed = guard.source_hashes(project)
        required = ('tools/currency_wars_refresh_offer.py', 'tools/currency_wars_rewards.py',
                    'tools/currency_wars_update.py', 'gui/launch.py', 'gui/processes.py',
                    'requirements.txt', guard.SOURCE_MANIFEST)
        self.assertTrue(set(required) <= set(reviewed))
        self.assertEqual(guard.verify_source_hashes(project, reviewed), reviewed)
        for name in required:
            with self.subTest(source=name):
                path = project / name
                payload = path.read_bytes()
                try:
                    # Whitespace alone changes bytes without changing behavior.
                    path.write_bytes(payload + b'\n')
                    with self.assertRaises(ValueError):
                        guard.verify_source_hashes(project, reviewed)
                finally:
                    path.write_bytes(payload)
                missing = dict(reviewed)
                missing.pop(name)
                with self.assertRaises(ValueError):
                    guard.verify_source_hashes(project, missing)
        path = project / 'tools/currency_wars_refresh_offer.py'
        path.unlink()
        with self.assertRaises(ValueError):
            guard.verify_source_hashes(project, reviewed)

    def test_new_lazy_local_import_cannot_escape_the_shared_inventory(self):
        project = self.source_copy()
        reviewed = guard.source_hashes(project)
        helper = 'tools/currency_wars_declared_fixture_dependency.py'
        (project / helper).write_text('value = 1\n', encoding='utf8')
        runner = project / 'tools/currency_wars_runner.py'
        with runner.open('a', encoding='utf8') as stream:
            stream.write('\ndef declared_fixture():\n    import currency_wars_declared_fixture_dependency\n')
        with self.assertRaisesRegex(ValueError, 'absent from the source inventory'):
            guard.source_hashes(project)
        path = project / guard.SOURCE_MANIFEST
        manifest = json.loads(path.read_text(encoding='utf8'))
        manifest['files'].append(helper)
        path.write_text(json.dumps(manifest), encoding='utf8')
        self.assertIn(helper, guard.source_hashes(project))
        with self.assertRaises(ValueError):
            guard.verify_source_hashes(project, reviewed)

    def test_changed_embedded_inventory_invalidates_actual_build_prerequisite(self):
        project = self.source_copy()
        self.ready_fixture(project)
        docs = project / 'docs'
        binary = project / 'gui/bin/currency-wars-gui.exe'
        binary.parent.mkdir()
        binary.write_bytes(b'Inert build-attestation fixture; never executed')
        (docs / 'INSTALL_STATE.json').write_text(json.dumps({
            'requirements_sha256': updater.requirements_hash(project)}), encoding='utf8')
        (docs / 'BUILD_STATE.json').write_text(json.dumps({
            'sources': updater.gui_fingerprint(project),
            'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}), encoding='utf8')
        self.assertIn(guard.SOURCE_MANIFEST, updater.gui_fingerprint(project))
        # Dependency versions are a declared satisfied precondition here;
        # this checks the real build gate, not an installed package fixture.
        with patch.object(updater, 'installed_versions', return_value={}):
            self.assertIsNone(updater.launch_prerequisites(project))
            (docs / 'RUNNER_READY.json').unlink()
            # A sound GUI build still opens to explain blocked readiness and
            # retain pause/stop for an already running worker. Public runner
            # startup refusal is tested separately below.
            self.assertIsNone(updater.launch_prerequisites(project))
            path = project / guard.SOURCE_MANIFEST
            path.write_bytes(path.read_bytes() + b'\n')
            self.assertIn('Rust/UI', updater.launch_prerequisites(project))

    def test_resources_and_actual_selected_provider_are_complete_current_byte_bindings(self):
        project = self.source_copy()
        ready = self.resource_fixture(project)
        self.assertEqual(guard.verify_runtime_sources(project, ready), ready)
        self.assertNotIn('ready', ready)
        self.assertNotIn('independent_review', ready)
        names = ('tools/refresh_offer_resources/currency_coin.png',
                 'tools/shop_reader_resources/recommend_badge.png',
                 'tools/shop_reader_resources/SOURCES.json')
        for name in names:
            with self.subTest(source=name):
                missing = copy.deepcopy(ready)
                missing['hashes'].pop(name)
                with self.assertRaises(ValueError):
                    guard.verify_runtime_sources(project, missing)
                path = project / name
                payload = path.read_bytes()
                try:
                    path.write_bytes(payload + b'changed')
                    with self.assertRaises(ValueError):
                        guard.verify_runtime_sources(project, ready)
                    path.unlink()
                    with self.assertRaises((OSError, ValueError)):
                        guard.verify_runtime_sources(project, ready)
                finally:
                    path.write_bytes(payload)
        extra = project / 'tools/shop_reader_resources/new-unreviewed-asset.png'
        extra.write_bytes(b'new byte source, not recognition evidence')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            guard.verify_runtime_sources(project, ready)
        extra.unlink()
        optional = project / 'tools/free_lineup_resources'
        optional.mkdir()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            guard.verify_runtime_sources(project, ready)
        optional.rmdir()
        provider = self.root / 'generated-external-provider.py'
        provider.write_bytes(b'# inert selected provider; never imported\n')
        source = dict(kind='installed', path=str(provider), sha256=hashlib.sha256(provider.read_bytes()).hexdigest().upper())
        with patch.object(artifacts, 'PROVIDER_SOURCE', source):
            approved = guard.runtime_source_binding(project)
            guard.verify_runtime_sources(project, approved)
            with self.assertRaisesRegex(ValueError, 'provider'):
                guard.verify_runtime_sources(project, ready)
            provider.write_bytes(b'# changed provider bytes\n')
            with self.assertRaisesRegex(ValueError, 'provider source changed'):
                guard.verify_runtime_sources(project, approved)

    def test_public_start_and_worker_refuse_missing_review_or_changed_bytes_before_backend(self):
        import currency_wars_runner as runner
        project = self.source_copy()
        ready = self.ready_fixture(project)
        path = project / 'docs/RUNNER_READY.json'
        args = SimpleNamespace(chat_id='explicit-protocol-chat')
        with patch.object(runner, 'PROJECT', project), \
                patch.object(runner, 'activity', side_effect=lambda *a: contextlib.nullcontext(object())), \
                patch.object(runner.entry, 'backend') as backend, \
                patch.object(runner.subprocess, 'Popen') as spawn:
            for name, target in (('start_cli', '_start_cli'), ('worker_cli', '_worker_cli')):
                with self.subTest(entrypoint=name), patch.object(runner, target, return_value='after-gate') as continuation:
                    self.assertEqual(getattr(runner, name)(args), 'after-gate')
                    continuation.assert_called_once()
                    continuation.reset_mock()
                    path.unlink()
                    with self.assertRaises((OSError, ValueError)):
                        getattr(runner, name)(args)
                    path.write_text(json.dumps(ready), encoding='utf8')
                    source = project / 'tools/currency_wars_refresh_offer.py'
                    saved = source.read_bytes()
                    try:
                        source.write_bytes(saved + b'\n')
                        with self.assertRaisesRegex(ValueError, 'source bytes changed'):
                            getattr(runner, name)(args)
                    finally:
                        source.write_bytes(saved)
                    wrong = copy.deepcopy(ready)
                    wrong['chat_id'] = 'another-session'
                    path.write_text(json.dumps(wrong), encoding='utf8')
                    with self.assertRaises(ValueError):
                        getattr(runner, name)(args)
                    path.write_text(json.dumps(ready), encoding='utf8')
                    continuation.assert_not_called()
            backend.assert_not_called()
            spawn.assert_not_called()

    def test_broker_loader_executes_verified_bytes_and_rejects_a_second_read_change(self):
        source = self.root / 'generated_backend.py'
        payload = b'value = "verified inert source"\n'
        source.write_bytes(payload)
        expected = hashlib.sha256(payload).hexdigest().upper()
        with patch.object(entry, 'SOURCE', source), patch.object(entry, 'PINNED', expected), \
                patch('importlib.machinery.SourceFileLoader.exec_module',
                      side_effect=AssertionError('Do not execute a second source/pyc loader read')):
            self.assertEqual(entry.backend().value, 'verified inert source')
            with patch.object(Path, 'read_bytes', side_effect=[payload, b'value = "changed"\n']):
                with self.assertRaisesRegex(ValueError, 'changed during loading'):
                    entry.backend()
            source.write_bytes(b'value = "changed before verification"\n')
            with self.assertRaisesRegex(ValueError, 'source changed'):
                entry.backend()

    def test_child_environment_revalidates_installation_without_mutating_parent_or_temp_cache(self):
        installation = self.root / 'declared-installation'
        installation.mkdir()
        runtime = self.root / 'D-temp' / artifacts.TOOL
        selected = {'schema': 1, 'source': 'installed_bridge', 'runtime_root': str(runtime),
                    'installation_id': 'a' * 32}
        config = {'runtime_root': str(runtime), 'installation_id': 'a' * 32}
        inherited = {'TEMP': str(self.root / 'C-temp'), 'TMP': str(self.root / 'C-temp'), 'KEEP': 'unchanged'}
        original = copy.deepcopy(inherited)
        process_environment, temp_cache = dict(os.environ), tempfile.tempdir
        with patch.object(bridge, 'INSTALL_ROOT', installation), \
                patch.object(bridge, 'configuration', return_value=config) as verify:
            child = bridge.runtime_child_environment(entry.PINNED, selected, environ=inherited)
            self.assertEqual(child, {**original, 'TEMP': str(runtime.parent), 'TMP': str(runtime.parent),
                                     'TMPDIR': str(runtime.parent)})
            self.assertEqual(inherited, original)
            self.assertEqual(dict(os.environ), process_environment)
            self.assertEqual(tempfile.tempdir, temp_cache)
            verify.assert_called_once_with(entry.PINNED)
            for changed in ({**config, 'installation_id': 'b' * 32},
                            {**config, 'runtime_root': str(self.root / 'other' / artifacts.TOOL)}):
                verify.return_value = changed
                with self.assertRaises(bridge.BridgeError):
                    bridge.runtime_child_environment(entry.PINNED, selected, environ=inherited)
            installation.rmdir()
            with self.assertRaises(bridge.BridgeError):
                bridge.runtime_child_environment(entry.PINNED, selected, environ=inherited)

    def test_launcher_reexecutes_only_with_verified_child_environment_before_runtime_creation(self):
        path = PROJECT / 'gui/launch.py'
        spec = importlib.util.spec_from_file_location('declared_gui_launcher_contract', path)
        launcher = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(path.parent), *sys.path]):
            spec.loader.exec_module(launcher)
        binary = self.root / 'never-executed-native.exe'
        binary.write_bytes(b'Inert launcher contract fixture')
        runtime = self.root / 'D-temp' / artifacts.TOOL
        location = {'schema': 1, 'source': 'installed_bridge', 'runtime_root': str(runtime),
                    'installation_id': 'a' * 32}
        parent = {'TEMP': str(self.root / 'C-temp'), 'CODEX_THREAD_ID': 'declared-fixture-chat'}
        child = {**parent, 'TEMP': str(runtime.parent), 'TMP': str(runtime.parent), 'TMPDIR': str(runtime.parent)}
        windows = SimpleNamespace(name='nt', path=os.path, environ=parent)
        arguments = ['launch.py', '--binary', str(binary), '--project-dir', str(self.root), '--skip-update']
        with patch.object(launcher, 'os', windows), patch.object(sys, 'argv', arguments), \
                patch.object(launcher.tempfile, 'gettempdir', return_value=parent['TEMP']), \
                patch.object(launcher.input_bridge, 'runtime_location', return_value=location), \
                patch.object(launcher.input_bridge, 'runtime_child_environment', return_value=child) as select, \
                patch.object(launcher.subprocess, 'call', return_value=17) as relaunch, \
                patch.object(launcher.subprocess, 'Popen') as native, \
                patch.object(launcher.artifacts, 'scratch_directory') as create, \
                patch.object(launcher, 'mutation_lock') as lifecycle:
            self.assertEqual(launcher.main(), 17)
            command = relaunch.call_args.args[0]
            self.assertEqual(json.loads(command[command.index('--runtime-location-json') + 1]), location)
            self.assertEqual(relaunch.call_args.kwargs['env'], child)
            select.assert_called_once_with(entry.PINNED, location)
            native.assert_not_called()
            create.assert_not_called()
            lifecycle.assert_not_called()
            self.assertEqual(parent['TEMP'], str(self.root / 'C-temp'))
            with self.assertRaisesRegex(RuntimeError, '临时目录仍'):
                launcher.runtime_launch_environment(location, inherited=True)
            self.assertEqual(relaunch.call_count, 1)


if __name__ == '__main__':
    unittest.main()
