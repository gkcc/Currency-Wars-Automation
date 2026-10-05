"""Real local Git fixtures with inert canonical transport; no network or GUI."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
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
        self.remote_queries = 0
        self.commands = []

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

    def transport(self, project, *args, timeout=8, read_only=False):
        self.commands.append((args, timeout, read_only))
        if args[0] == 'fetch':
            self.fetches += 1
            return self.real_git(project, 'fetch', '--no-tags', '--no-recurse-submodules', str(self.upstream), 'main', timeout=timeout)
        if args[0] == 'ls-remote' and '--heads' in args:
            self.remote_queries += 1
            self.assertIn(args[2], updater.CANONICAL)
            self.assertEqual(args[3:], ('main',))
            return self.real_git(project, 'ls-remote', '--heads', str(self.upstream), 'main', timeout=timeout, read_only=read_only)
        return self.real_git(project, *args, timeout=timeout, read_only=read_only)

    def update(self, apply=True):
        with patch.object(updater, 'git', side_effect=self.transport):
            return updater.update(self.clone, apply=apply)

    def advance(self, path='source.txt', value='v2'):
        destination = self.upstream / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value, encoding='utf8')
        local_git(self.upstream, 'add', '-f', '--', path)
        local_git(self.upstream, 'commit', '-m', 'fixture advance')

    def git_snapshot(self):
        return {str(path.relative_to(self.clone)): path.read_bytes()
                for path in (self.clone / '.git').rglob('*') if path.is_file()}

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
        before = self.git_snapshot()
        result = self.update(apply=False)
        self.assertEqual(result['status'], 'remote_differs', result)
        self.assertIn('需安全同步核验', result['message'])
        self.assertFalse(result['sync_verified'])
        self.assertEqual(self.fetches, 0)
        self.assertEqual(self.remote_queries, 1)
        self.assertTrue(all(read_only for args, timeout, read_only in self.commands))
        self.assertEqual(self.git_snapshot(), before)
        self.assertEqual((self.clone / 'source.txt').read_text(), 'v1')
        def timeout_transport(project, *args, timeout=8):
            if args[0] == 'fetch':
                raise subprocess.TimeoutExpired(['inert-fetch'], 20)
            return self.real_git(project, *args, timeout=timeout)
        with patch.object(updater, 'git', side_effect=timeout_transport):
            result = updater.update(self.clone)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual((self.clone / 'source.txt').read_text(), 'v1')

    def test_read_only_check_reports_active_gui_and_dirty_source(self):
        self.advance()
        (self.clone / 'source.txt').write_text('user changes')
        (self.clone / 'untracked.txt').write_text('untracked user data')
        before = self.git_snapshot()
        with guard.activity(self.clone, 'gui'):
            result = self.update(apply=False)
        self.assertEqual(result['status'], 'remote_differs', result)
        self.assertEqual(result['current_commit'], local_git(self.clone, 'rev-parse', 'HEAD'))
        self.assertEqual(result['available_commit'], local_git(self.upstream, 'rev-parse', 'HEAD'))
        self.assertIn('当前不能自动同步', result['message'])
        self.assertTrue(any('GUI' in reason for reason in result['sync_blockers']))
        self.assertTrue(any('本地源码' in reason for reason in result['sync_blockers']))
        self.assertEqual(self.git_snapshot(), before)
        self.assertEqual((self.clone / 'source.txt').read_text(), 'user changes')
        self.assertEqual((self.clone / 'untracked.txt').read_text(), 'untracked user data')
        self.assertEqual(self.fetches, 0)

    def test_read_only_check_reports_unknown_activity_without_suppressing_query(self):
        directory = self.clone / 'docs/source-activity'
        directory.mkdir(parents=True)
        (directory / 'unknown.json').write_text('{}')
        result = self.update(apply=False)
        self.assertEqual(result['status'], 'up_to_date', result)
        self.assertTrue(result['sync_blockers'])
        self.assertIn('未知', result['message'])
        self.assertEqual(self.remote_queries, 1)
        self.assertEqual(self.fetches, 0)

    def test_read_only_check_refuses_origin_hijack_multiple_urls_and_wrong_branch(self):
        local_git(self.clone, 'config', 'url.https://example.invalid/other/.insteadOf', 'https://github.com/gkcc/')
        result = self.update(apply=False)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('origin', result['message'])
        self.assertEqual(self.remote_queries, 0)
        local_git(self.clone, 'config', '--unset', 'url.https://example.invalid/other/.insteadOf')
        local_git(self.clone, 'config', '--add', 'remote.origin.url', 'https://example.invalid/repository.git')
        self.assertEqual(self.update(apply=False)['status'], 'unavailable')
        self.assertEqual(self.remote_queries, 0)
        local_git(self.clone, 'config', '--unset-all', 'remote.origin.url')
        local_git(self.clone, 'config', 'remote.origin.url', 'https://github.com/gkcc/Currency-Wars-Automation.git')
        local_git(self.clone, 'switch', '-c', 'user-branch')
        self.assertEqual(self.update(apply=False)['status'], 'skipped')
        self.assertEqual(self.remote_queries, 0)
        self.assertEqual(local_git(self.clone, 'symbolic-ref', '--short', 'HEAD'), 'user-branch')

    def test_read_only_check_refuses_nested_repository_root(self):
        nested = self.clone / 'nested'
        nested.mkdir()
        (nested / '.git').write_text('gitdir: ../.git\n')
        with patch.object(updater, 'git', side_effect=lambda project, *args, **kwargs: str(self.clone)):
            result = updater.check_only(nested)
        self.assertEqual(result['status'], 'skipped', result)
        self.assertEqual(self.fetches, 0)

    def test_read_only_network_timeout_has_total_budget_and_preserves_git(self):
        before = self.git_snapshot()
        def transport(project, *args, timeout=8, read_only=False):
            if args[0] == 'ls-remote' and '--heads' in args:
                self.assertLessEqual(timeout, updater.CHECK_WORK_SECONDS)
                self.assertTrue(read_only)
                raise subprocess.TimeoutExpired(['inert-network-query'], timeout)
            return self.transport(project, *args, timeout=timeout, read_only=read_only)
        with patch.object(updater, 'git', side_effect=transport):
            result = updater.check_only(self.clone)
        self.assertEqual(result['status'], 'timeout', result)
        self.assertFalse(result['applied'])
        self.assertEqual(self.git_snapshot(), before)
        self.assertEqual(self.fetches, 0)
        with patch.object(updater, 'git') as calls:
            result = updater.check_only(self.clone, timeout=0)
        calls.assert_not_called()
        self.assertEqual(result['status'], 'timeout')

    def test_read_only_remote_response_requires_unique_valid_main(self):
        for invalid in ('', 'x' * 40 + '\trefs/heads/main',
                        'a' * 40 + '\trefs/heads/other',
                        '\n'.join(['a' * 40 + '\trefs/heads/main'] * 2)):
            def transport(project, *args, timeout=8, read_only=False):
                if args[0] == 'ls-remote' and '--heads' in args:
                    return invalid
                return self.transport(project, *args, timeout=timeout, read_only=read_only)
            with patch.object(updater, 'git', side_effect=transport):
                result = updater.check_only(self.clone)
            self.assertEqual(result['status'], 'unavailable', result)
            self.assertNotIn('available_commit', result)
        self.assertEqual(self.fetches, 0)

    def test_read_only_check_never_claims_upgrade_when_local_history_is_ahead(self):
        (self.clone / 'source.txt').write_text('local commit')
        local_git(self.clone, 'add', '--', 'source.txt')
        local_git(self.clone, 'commit', '-m', 'local newer history')
        before = self.git_snapshot()
        result = self.update(apply=False)
        self.assertEqual(result['status'], 'remote_differs', result)
        self.assertFalse(result['sync_verified'])
        self.assertIn('需安全同步核验', result['message'])
        self.assertNotIn('快进更新可用', result['message'])
        self.assertEqual(self.git_snapshot(), before)
        self.assertEqual(self.fetches, 0)

    def test_read_only_check_revalidates_origin_after_remote_query(self):
        def transport(project, *args, timeout=8, read_only=False):
            value = self.transport(project, *args, timeout=timeout, read_only=read_only)
            if args[0] == 'ls-remote' and '--heads' in args:
                local_git(self.clone, 'remote', 'set-url', 'origin', 'https://example.invalid/changed.git')
            return value
        with patch.object(updater, 'git', side_effect=transport):
            result = updater.check_only(self.clone)
        self.assertEqual(result['status'], 'unavailable', result)
        self.assertFalse(result['sync_verified'])
        self.assertEqual(self.fetches, 0)

    def test_status_atomic_replace_preserves_previous_record_on_failure(self):
        previous = {'status': 'previous', 'message': '已保存的真实结果'}
        updater.write_status(self.clone, previous)
        with patch.object(updater.os, 'replace', side_effect=OSError('inert replace failure')):
            with self.assertRaises(OSError):
                updater.write_status(self.clone, {'status': 'new'})
        path = self.clone / 'docs/UPDATE_STATUS.json'
        self.assertEqual(json.loads(path.read_text(encoding='utf8')), previous)
        self.assertEqual(list(path.parent.glob('.UPDATE_STATUS-*.tmp')), [])
        updater.write_status(self.clone, {'status': 'next', 'message': '新结果'})
        self.assertEqual(json.loads(path.read_text(encoding='utf8'))['status'], 'next')

    def test_owned_read_only_git_timeout_stops_only_its_new_process_tree(self):
        # An inert local git alias launches a waiting child, not the network,
        # game, GUI, broker or any pre-existing process.
        local_git(self.clone, 'config', 'alias.inert-wait', '!sleep 20')
        identity = artifacts.process_identity(os.getpid())
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.real_git(self.clone, 'inert-wait', timeout=.15, read_only=True)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(artifacts.process_identity(os.getpid()), identity)

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
