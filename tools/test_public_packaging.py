"""Portable startup contract checks using inert Python children, never a GUI."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import currency_wars_artifacts as installed_artifacts
from check_install import check

PROJECT = Path(__file__).resolve().parent.parent


def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {'CW_ARTIFACTS_STANDALONE': '1'}):
            cls.portable = module_from(PROJECT / 'tools/currency_wars_artifacts.py', 'packaging_standalone')

    def test_standalone_cleans_only_its_original_run(self):
        with installed_artifacts.scratch_directory('currency-wars-packaging-test') as outer:
            with self.portable.scratch_directory('portable-test', root=outer) as runtime:
                marker = self.portable.read_marker(runtime, root=outer)
                self.assertEqual(marker['pid'], os.getpid())
                self.assertEqual(marker['tool'], 'codex-agent-workflow')
                (runtime / 'payload.txt').write_text('owned', encoding='utf8')
                with self.assertRaises(self.portable.ArtifactError):
                    self.portable.read_marker(runtime, root=outer.parent)
            self.assertFalse(runtime.exists())
        self.assertFalse(outer.exists())

    def test_active_child_blocks_completion_until_verified_exit(self):
        with installed_artifacts.scratch_directory('currency-wars-packaging-test') as outer:
            with self.portable.scratch_directory('portable-test', root=outer) as runtime:
                child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(20)'],
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    state, identity = self.portable.process_identity(child.pid)
                    self.assertEqual(state, 'active')
                    self.portable.protect_children(runtime, [{'pid': child.pid, 'process_identity': identity}], root=outer, complete=False)
                    with self.assertRaises(self.portable.ArtifactError):
                        self.portable.protect_children(runtime, [], root=outer, complete=True)
                    self.assertTrue((runtime / self.portable.MARKER).is_file())
                finally:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)
                    self.portable.protect_children(runtime, [], root=outer, complete=True)
            self.assertFalse(runtime.exists())

    def test_launcher_passes_current_paths_and_session_to_inert_child(self):
        sys.path.insert(0, str(PROJECT / 'gui'))
        launch = module_from(PROJECT / 'gui/launch.py', 'packaging_launcher')
        original_popen = subprocess.Popen
        commands = []
        def inert_child(command, **options):
            commands.append(command)
            return original_popen([sys.executable, '-B', '-c', 'pass'], **options)
        with installed_artifacts.scratch_directory('currency-wars-packaging-test') as outer:
            binary = outer / 'inert.exe'
            binary.write_bytes(b'inert fixture; never executed')
            alternate = outer / '下载 project with spaces'
            alternate.mkdir()
            with patch.object(launch, 'artifacts', self.portable), patch.object(launch.subprocess, 'Popen', side_effect=inert_child), \
                    patch.object(sys, 'argv', ['launch.py', '--test-mode', '--binary', str(binary), '--project-dir', str(alternate), '--chat-id', 'isolated-public-check']), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                launch.main()
            self.assertEqual(commands[0][commands[0].index('--project-dir') + 1], str(alternate))
            self.assertEqual(commands[0][commands[0].index('--python') + 1], sys.executable)
            self.assertEqual(commands[0][commands[0].index('--chat-id') + 1], 'isolated-public-check')
            self.assertIn('"removed": true', output.getvalue())
        self.assertFalse(outer.exists())

    def test_preflight_does_not_claim_missing_readiness_or_resources(self):
        with installed_artifacts.scratch_directory('currency-wars-packaging-test') as project:
            result = check(project)
            self.assertFalse(result['gui_binary_present'])
            self.assertFalse(result['shop_resources_present'])
            self.assertFalse(result['readiness_record_present'])
            self.assertEqual(result['controllers_started'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
