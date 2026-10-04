"""Real local Git fixtures with inert canonical transport; no network or GUI."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import currency_wars_artifacts as artifacts
import currency_wars_source_guard as guard
import currency_wars_update as updater


def local_git(directory, *args):
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(directory), *args],
                            capture_output=True, text=True, encoding='utf8', errors='replace',
                            timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.owned = artifacts.scratch_directory('currency-wars-update-test')
        self.run = self.owned.__enter__()
        self.upstream = self.run / 'upstream'
        self.upstream.mkdir()
        local_git(self.upstream, 'init', '-b', 'main')
        local_git(self.upstream, 'config', 'user.name', 'Offline fixture')
        local_git(self.upstream, 'config', 'user.email', 'fixture@example.invalid')
        (self.upstream / '.gitignore').write_text('docs/\n.venv/\ngui/bin/\ntools/shop_reader_resources/\n')
        (self.upstream / 'source.txt').write_text('v1')
        local_git(self.upstream, 'add', '--', '.gitignore', 'source.txt')
        local_git(self.upstream, 'commit', '-m', 'fixture initial')
        self.clone = self.run / '下载 clone with spaces'
        local_git(self.run, 'clone', '--no-hardlinks', str(self.upstream), str(self.clone))
        local_git(self.clone, 'remote', 'set-url', 'origin', 'https://github.com/gkcc/Currency-Wars-Automation.git')
        local_git(self.clone, 'config', 'user.name', 'Offline fixture')
        local_git(self.clone, 'config', 'user.email', 'fixture@example.invalid')
        self.real_git = updater.git
        self.fetches = 0

    def tearDown(self):
        # Git writes read-only object files on Windows. Prepare only this
        # freshly owned, no-hardlinks fixture before its first context cleanup.
        marker = artifacts.read_marker(self.run)
        self.assertEqual(marker['pid'], os.getpid())
        for repository in (self.upstream, self.clone):
            for path in (repository / '.git/objects').rglob('*'):
                info = path.lstat()
                if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise AssertionError('Fixture object links are prohibited')
                if path.is_file():
                    self.assertEqual(info.st_nlink, 1)
                    path.chmod(stat.S_IREAD | stat.S_IWRITE)
        self.owned.__exit__(None, None, None)
        self.assertFalse(self.run.exists())

    def transport(self, project, *args, timeout=8):
        if args[0] == 'fetch':
            self.fetches += 1
            return self.real_git(project, 'fetch', '--no-tags', '--no-recurse-submodules', str(self.upstream), 'main', timeout=timeout)
        return self.real_git(project, *args, timeout=timeout)

    def update(self, apply=True):
        with patch.object(updater, 'git', side_effect=self.transport):
            return updater.update(self.clone, apply=apply)

    def advance(self, path='source.txt', value='v2'):
        destination = self.upstream / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value, encoding='utf8')
        local_git(self.upstream, 'add', '-f', '--', path)
        local_git(self.upstream, 'commit', '-m', 'fixture advance')

    def test_clean_fast_forward_preserves_ignored_data(self):
        private = ['docs/local.json', '.venv/local.txt', 'gui/bin/local.exe', 'tools/shop_reader_resources/local.png']
        for name in private:
            path = self.clone / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'private fixture')
        self.advance()
        result = self.update()
        self.assertEqual(result['status'], 'updated', result)
        self.assertTrue(result['applied'])
        self.assertEqual((self.clone / 'source.txt').read_text(), 'v2')
        for name in private:
            self.assertEqual((self.clone / name).read_bytes(), b'private fixture')
        self.assertIn('setup_required', result)
        self.assertEqual(local_git(self.clone, 'symbolic-ref', '--short', 'HEAD'), 'main')
        self.assertEqual(local_git(self.clone, 'remote', 'get-url', 'origin'), 'https://github.com/gkcc/Currency-Wars-Automation.git')

    def test_dirty_and_diverged_source_are_preserved(self):
        self.advance()
        (self.clone / 'source.txt').write_text('user changes')
        result = self.update()
        self.assertFalse(result['applied'])
        self.assertEqual(self.fetches, 0)
        local_git(self.clone, 'add', '--', 'source.txt')
        local_git(self.clone, 'commit', '-m', 'local user commit')
        result = self.update()
        self.assertFalse(result['applied'])
        self.assertIn('diverged', result['message'])
        self.assertEqual((self.clone / 'source.txt').read_text(), 'user changes')

    def test_origin_redirect_and_wrong_branch_skip_without_fetch(self):
        local_git(self.clone, 'config', 'url.https://example.invalid/other/.insteadOf', 'https://github.com/gkcc/')
        result = self.update()
        self.assertFalse(result['applied'])
        self.assertIn('endpoint', result['message'])
        self.assertEqual(self.fetches, 0)
        local_git(self.clone, 'config', '--unset', 'url.https://example.invalid/other/.insteadOf')
        local_git(self.clone, 'switch', '-c', 'local-branch')
        self.assertFalse(self.update()['applied'])
        self.assertEqual(self.fetches, 0)
        self.assertEqual(local_git(self.clone, 'symbolic-ref', '--short', 'HEAD'), 'local-branch')

    def test_non_ascii_protected_path_cannot_overwrite_ignored_file(self):
        private = self.clone / 'docs/本机数据.txt'
        private.parent.mkdir()
        private.write_text('retain', encoding='utf8')
        self.advance('docs/本机数据.txt', 'remote replacement')
        result = self.update()
        self.assertFalse(result['applied'])
        self.assertIn('protected', result['message'])
        self.assertEqual(private.read_text(encoding='utf8'), 'retain')

    def test_active_and_unknown_activity_prevent_fetch(self):
        with guard.activity(self.clone, 'gui'):
            result = self.update()
            self.assertFalse(result['applied'])
            self.assertEqual(self.fetches, 0)
        directory = self.clone / 'docs/source-activity'
        directory.mkdir()
        (directory / 'unknown.json').write_text('{}')
        self.assertIn('unknown', self.update()['message'])
        self.assertEqual(self.fetches, 0)

    def test_update_and_start_registration_share_one_guard(self):
        blocked = []
        def register_while_locked():
            try:
                with guard.mutation_lock(self.clone, timeout_ms=80):
                    blocked.append(False)
            except RuntimeError:
                blocked.append(True)
        with guard.mutation_lock(self.clone):
            thread = threading.Thread(target=register_while_locked)
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(blocked, [True])
        import currency_wars_runner as runner
        def registered(args, lease):
            self.assertIn('active', guard.activity_reason(self.clone))
            return 'registered-before-worker-spawn'
        with patch.object(runner, 'PROJECT', self.clone), patch.object(runner, '_start_cli', side_effect=registered):
            self.assertEqual(runner.start_cli(object()), 'registered-before-worker-spawn')

    def test_check_only_and_network_timeout_leave_source_unchanged(self):
        self.advance()
        result = self.update(apply=False)
        self.assertEqual(result['status'], 'update_available')
        self.assertEqual((self.clone / 'source.txt').read_text(), 'v1')
        def timeout_transport(project, *args, timeout=8):
            if args[0] == 'fetch':
                raise subprocess.TimeoutExpired(['inert-fetch'], 20)
            return self.real_git(project, *args, timeout=timeout)
        with patch.object(updater, 'git', side_effect=timeout_transport):
            result = updater.update(self.clone)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual((self.clone / 'source.txt').read_text(), 'v1')

    def test_stale_dependency_and_native_source_fingerprints_are_visible(self):
        (self.clone / 'requirements.txt').write_bytes((updater.PROJECT / 'requirements.txt').read_bytes())
        (self.clone / 'gui/bin').mkdir(parents=True)
        (self.clone / 'gui/bin/currency-wars-gui.exe').write_bytes(b'inert binary fixture')
        (self.clone / 'gui/Cargo.toml').write_text('fixture source')
        (self.clone / 'docs').mkdir(exist_ok=True)
        (self.clone / 'docs/INSTALL_STATE.json').write_text(json.dumps({'requirements_sha256': updater.requirements_hash(self.clone)}))
        binary = self.clone / 'gui/bin/currency-wars-gui.exe'
        (self.clone / 'docs/BUILD_STATE.json').write_text(json.dumps({'sources': updater.gui_fingerprint(self.clone), 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}))
        self.assertIsNone(updater.launch_prerequisites(self.clone))
        (self.clone / 'gui/Cargo.toml').write_text('changed GUI source')
        self.assertIn('Rust/UI', updater.launch_prerequisites(self.clone))
        (self.clone / 'requirements.txt').write_text('Pillow==0.0.0')
        self.assertIn('dependencies', updater.launch_prerequisites(self.clone))
        self.assertFalse((self.clone / 'docs/RUNNER_READY.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2, failfast=True)
