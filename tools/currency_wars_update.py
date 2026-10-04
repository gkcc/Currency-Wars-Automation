"""Canonical clean-clone fast-forward updates. No game or GUI control APIs."""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
from currency_wars_artifacts import process_identity

from currency_wars_source_guard import activity, activity_reason, mutation_lock

PROJECT = Path(__file__).resolve().parent.parent
CANONICAL = {'https://github.com/gkcc/Currency-Wars-Automation.git',
             'git@github.com:gkcc/Currency-Wars-Automation.git',
             'ssh://git@github.com/gkcc/Currency-Wars-Automation.git'}
PROTECTED = ('docs', 'runtime', 'sessions', 'debug', '.venv', 'gui/bin', 'gui/target', 'target', 'tools/shop_reader_resources')
_ACTIVE_UPDATE_LEASE = None


def git(project, *args, timeout=8):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never',
               GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=8')
    command = ['git', '-c', 'core.hooksPath=/dev/null', '-C', str(project), *args]
    records = {}
    unknown_children = False
    if _ACTIVE_UPDATE_LEASE is not None:
        _ACTIVE_UPDATE_LEASE.children([], complete=False)
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 encoding='utf8', errors='replace', env=env,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError:
        if _ACTIVE_UPDATE_LEASE is not None:
            _ACTIVE_UPDATE_LEASE.children([], complete=True)
        raise
    started = time.monotonic()
    try:
        while True:
            try:
                processes = [psutil.Process(child.pid)]
                processes += processes[0].children(recursive=True)
                for process in processes:
                    state, identity = process_identity(process.pid)
                    if state == 'active' and identity is not None:
                        records[(process.pid, identity)] = process.pid
                if _ACTIVE_UPDATE_LEASE is not None:
                    _ACTIVE_UPDATE_LEASE.children([{'pid': pid, 'process_identity': identity} for pid, identity in records], complete=False)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                unknown_children = True
            try:
                output, error = child.communicate(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - started >= timeout:
                    raise subprocess.TimeoutExpired(command, timeout)
        if child.returncode:
            raise RuntimeError('Git operation failed: ' + ' '.join(args[:2]) + f' (exit {child.returncode})')
        if unknown_children:
            raise RuntimeError('Owned Git child inventory is unknown; activity marker retained')
        return output.strip()
    finally:
        helper = Path(__file__).resolve().parent.parent / 'gui/processes.py'
        spec = importlib.util.spec_from_file_location('update_owned_processes', helper)
        owned = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(owned)
        for pid, identity in reversed(list(records)):
            if process_identity(pid) == ('active', identity):
                owned.stop_identity(pid, identity)
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
        if _ACTIVE_UPDATE_LEASE is not None and not unknown_children:
            _ACTIVE_UPDATE_LEASE.children([], complete=True)


def _clear_update_lease():
    global _ACTIVE_UPDATE_LEASE
    _ACTIVE_UPDATE_LEASE = None


def gui_fingerprint(project):
    root = Path(project)
    paths = [root / 'gui' / name for name in ('Cargo.toml', 'Cargo.lock', 'build.rs', 'tauri.conf.json')]
    for folder in ('src', 'ui', 'icons'):
        paths += sorted((root / 'gui' / folder).rglob('*'))
    return {str(p.relative_to(root)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}


def requirements_hash(project):
    return hashlib.sha256((Path(project) / 'requirements.txt').read_bytes()).hexdigest()


def installed_versions(project):
    output = {}
    for line in (Path(project) / 'requirements.txt').read_text(encoding='utf8').splitlines():
        if '==' not in line or line.startswith('#'):
            continue
        package, expected = line.split('==', 1)
        try:
            actual = version(package)
        except PackageNotFoundError:
            actual = None
        output[package] = {'expected': expected, 'actual': actual}
    return output


def launch_prerequisites(project):
    from currency_wars_source_guard import read_object
    project = Path(project)
    try:
        install = read_object(project / 'docs/INSTALL_STATE.json')
        if install.get('requirements_sha256') != requirements_hash(project) or any(v['expected'] != v['actual'] for v in installed_versions(project).values()):
            return 'Python dependencies changed. Run Setup.ps1 before starting.'
        build = read_object(project / 'docs/BUILD_STATE.json')
        binary = project / 'gui/bin/currency-wars-gui.exe'
        if build.get('sources') != gui_fingerprint(project) or not binary.is_file() or build.get('binary_sha256') != hashlib.sha256(binary.read_bytes()).hexdigest():
            return 'Rust/UI sources or binary changed. Run Setup.ps1 -BuildGui before starting.'
    except (OSError, ValueError):
        return 'Local installation/build attestation missing. Run Setup.ps1 -BuildGui.'
    return None


def protected_path(path):
    path = path.replace('\\', '/').casefold()
    return any(path == prefix or path.startswith(prefix + '/') for prefix in PROTECTED) or any(part.startswith('.env') for part in path.split('/'))


def update(project=PROJECT, apply=True):
    global _ACTIVE_UPDATE_LEASE
    project = Path(project).resolve()
    result = dict(status='skipped', applied=False, checked_at=datetime.now(timezone.utc).isoformat())
    try:
        with mutation_lock(project), contextlib.ExitStack() as lifecycle:
            reason = activity_reason(project)
            if reason:
                return dict(result, message=reason)
            lease = lifecycle.enter_context(activity(project, 'updater'))
            _ACTIVE_UPDATE_LEASE = lease
            lifecycle.callback(_clear_update_lease)
            if not (project / '.git').exists():
                return dict(result, status='zip_install', message='ZIP installation: automatic synchronization requires a Git clone. Clone the canonical repository; preserve local data separately.')
            if Path(git(project, 'rev-parse', '--show-toplevel')).resolve() != project:
                return dict(result, message='Nested/worktree project identity is not the selected root; update skipped.')
            if git(project, 'symbolic-ref', '--quiet', '--short', 'HEAD') != 'main':
                return dict(result, message='Only main is synchronized; current branch was preserved.')
            raw = git(project, 'config', '--get-all', 'remote.origin.url').splitlines()
            effective = git(project, 'ls-remote', '--get-url', 'origin')
            if len(raw) != 1 or raw[0] not in CANONICAL or effective not in CANONICAL:
                return dict(result, message='Origin raw/effective endpoint is not the canonical repository; update skipped.')
            if git(project, 'status', '--porcelain', '--untracked-files=normal'):
                return dict(result, message='Local source changes exist; automatic update skipped and changes preserved.')
            before = git(project, 'rev-parse', 'HEAD')
            git(project, 'fetch', '--no-tags', '--no-recurse-submodules', 'origin', 'main', timeout=20)
            after = git(project, 'rev-parse', 'FETCH_HEAD')
            result.update(current_commit=before, available_commit=after)
            if before == after:
                return dict(result, status='up_to_date', message='Source is up to date.')
            # Both the changed paths and new tracked tree must leave private
            # ignored data protected, including an upstream file added over it.
            changed = [path for path in git(project, 'diff', '--name-only', '-z', before, after).split('\0') if path]
            tracked = [path for path in git(project, 'ls-tree', '-r', '--name-only', '-z', after).split('\0') if path]
            if any(protected_path(path) for path in changed + tracked):
                return dict(result, message='Remote update touches protected local data; synchronization refused.')
            modes = git(project, 'ls-tree', '-r', '-z', after).split('\0')
            if any(row.startswith(('120000 ', '160000 ')) for row in modes):
                return dict(result, message='Remote update contains links/submodules; synchronization refused.')
            try:
                git(project, 'merge-base', '--is-ancestor', before, after)
            except RuntimeError:
                return dict(result, message='Local history is ahead/diverged; no reset, branch switch or overwrite was performed.')
            if not apply:
                return dict(result, status='update_available', message='A safe fast-forward source update is available.')
            # Revalidate mutable facts directly before merging. A new launcher
            # or worker must hold this same guard before registering activity.
            if (activity_reason(project, ignore_lease=lease.path) or git(project, 'status', '--porcelain', '--untracked-files=normal')
                    or git(project, 'rev-parse', 'HEAD') != before
                    or git(project, 'symbolic-ref', '--quiet', '--short', 'HEAD') != 'main'
                    or git(project, 'config', '--get-all', 'remote.origin.url').splitlines() != raw
                    or git(project, 'ls-remote', '--get-url', 'origin') != effective):
                return dict(result, message='Activity/local source changed during checking; synchronization skipped.')
            git(project, 'merge', '--ff-only', '--no-edit', after, timeout=15)
            result.update(status='updated', applied=True, message='Source synchronized by fast-forward; no game/GUI was started or restarted.')
            prerequisites = launch_prerequisites(project)
            if prerequisites:
                result['setup_required'] = prerequisites
            return result
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        return dict(result, status='unavailable', message=str(error)[:400] + '. Existing safe local installation remains available.')


def write_status(project, result):
    path = Path(project) / 'docs/UPDATE_STATUS.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--record-install', action='store_true')
    parser.add_argument('--record-build', action='store_true')
    args = parser.parse_args()
    (PROJECT / 'docs').mkdir(exist_ok=True)
    if args.record_install:
        versions = installed_versions(PROJECT)
        if any(v['expected'] != v['actual'] for v in versions.values()):
            raise RuntimeError('Installed dependencies do not match requirements')
        state = dict(requirements_sha256=requirements_hash(PROJECT), packages=versions)
        (PROJECT / 'docs/INSTALL_STATE.json').write_text(json.dumps(state), encoding='utf8')
        return
    if args.record_build:
        binary = PROJECT / 'gui/bin/currency-wars-gui.exe'
        state = dict(sources=gui_fingerprint(PROJECT), binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        (PROJECT / 'docs/BUILD_STATE.json').write_text(json.dumps(state), encoding='utf8')
        return
    result = update(apply=not args.check_only)
    write_status(PROJECT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
