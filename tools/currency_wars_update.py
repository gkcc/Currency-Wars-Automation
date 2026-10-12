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
import re
import subprocess
import sys
import tempfile
import time

import psutil
from currency_wars_artifacts import process_identity

from currency_wars_source_guard import activity, activity_reason, mutation_lock

PROJECT = Path(__file__).resolve().parent.parent
CANONICAL = {'https://github.com/gkcc/Currency-Wars-Automation.git',
             'git@github.com:gkcc/Currency-Wars-Automation.git',
             'ssh://git@github.com/gkcc/Currency-Wars-Automation.git'}
PROTECTED = ('docs', 'runtime', 'sessions', 'debug', '.venv', 'gui/bin', 'gui/target', 'target', 'tools/shop_reader_resources',
             'tools/free_lineup_resources', 'tools/loot_resources')
_ACTIVE_UPDATE_LEASE = None
CHECK_TIMEOUT_SECONDS = 25
CHECK_WORK_SECONDS = 21


class _ReadOnlyGitJob:
    """Only a newly spawned Git tree belongs to this non-inheritable job."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', w.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                        ('active_limit', w.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ('reads', 'writes', 'others', 'read_bytes', 'write_bytes', 'other_bytes')]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', IoCounters),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [('user', ctypes.c_int64), ('kernel', ctypes.c_int64),
                        ('period_user', ctypes.c_int64), ('period_kernel', ctypes.c_int64),
                        ('faults', w.DWORD), ('total', w.DWORD),
                        ('active', w.DWORD), ('terminated', w.DWORD)]
        self.ctypes, self.Accounting = ctypes, Accounting
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, arguments, result in (
                ('CreateJobObjectW', [w.LPVOID, w.LPCWSTR], w.HANDLE),
                ('SetInformationJobObject', [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
                ('AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
                ('TerminateJobObject', [w.HANDLE, w.UINT], w.BOOL),
                ('QueryInformationJobObject', [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID], w.BOOL),
                ('CloseHandle', [w.HANDLE], w.BOOL)):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError('Cannot create owned read-only Git job')
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise OSError('Cannot protect owned read-only Git job')

    def attach(self, child):
        # The process is still suspended, so it cannot launch an unowned child.
        if not self.kernel.AssignProcessToJobObject(self.handle, int(child._handle)):
            raise OSError('Cannot bind owned read-only Git child')
        psutil.Process(child.pid).resume()

    def close(self):
        if not self.handle:
            return
        try:
            if not self.kernel.TerminateJobObject(self.handle, 2):
                raise OSError('Cannot stop owned read-only Git job')
            deadline = time.monotonic() + 2
            while True:
                counters = self.Accounting()
                if not self.kernel.QueryInformationJobObject(self.handle, 1, self.ctypes.byref(counters), self.ctypes.sizeof(counters), None):
                    raise OSError('Cannot verify owned read-only Git child exit')
                if counters.active == 0:
                    return
                if time.monotonic() >= deadline:
                    raise RuntimeError('Owned read-only Git child exit is unconfirmed')
                time.sleep(.02)
        finally:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _read_only_git(project, args, timeout, env):
    env = dict(env, GIT_OPTIONAL_LOCKS='0')
    command = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-C', str(project), *args]
    job, child = None, None
    try:
        options = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
        if os.name == 'nt':
            job = _ReadOnlyGitJob()
            options['creationflags'] |= 0x00000004  # CREATE_SUSPENDED
        else:
            options['start_new_session'] = True
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 encoding='utf8', errors='replace', env=env, **options)
        if job is not None:
            job.attach(child)
        output, error = child.communicate(timeout=timeout)
        if child.returncode:
            raise RuntimeError('只读 Git 查询失败：' + ' '.join(args[:2]) + f'（退出码 {child.returncode}）')
        return output.strip()
    finally:
        try:
            if job is not None:
                job.close()
            elif child is not None and os.name != 'nt':
                import signal
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=2)


def git(project, *args, timeout=8, read_only=False):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never',
               GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=8')
    if read_only:
        return _read_only_git(project, args, timeout, env)
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
    # The native readiness checker embeds this shared production inventory.
    paths.append(root / 'tools/currency_wars_runtime_sources.json')
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
    # READY/source failure disables native start/resume, not GUI access to an
    # existing worker's pause/stop. Normal runner entrypoints enforce the same
    # source gate independently before loading any controller backend.
    return None


def protected_path(path):
    path = path.replace('\\', '/').casefold()
    return any(path == prefix or path.startswith(prefix + '/') for prefix in PROTECTED) or any(part.startswith('.env') for part in path.split('/'))


def _activity_message(reason):
    if not reason:
        return None
    if 'active/unknown' in reason:
        return 'GUI、执行器或其子进程正在运行，或退出状态尚未核验'
    if 'guard is busy' in reason:
        return '源码同步或启动登记正在进行'
    for prefix, message in (
            ('Activity directory', '运行归属目录存在链接或状态未知'),
            ('Activity inventory', '运行归属记录超过检查上限'),
            ('Incomplete/unknown', 'GUI 或执行器的子进程登记不完整或归属未知'),
            ('Unreadable controller', 'GUI 或执行器的活动记录无法读取'),
            ('Existing runner ownership', '当前执行器的归属未知'),
            ('Broker startup/exit', '输入进程的启动或退出证据尚未核验'),
            ('Existing runner record', '当前执行器记录无法读取')):
        if reason.startswith(prefix):
            return message
    return '运行或子进程归属记录未通过核验'


def check_only(project=PROJECT, timeout=CHECK_TIMEOUT_SECONDS):
    """Read HEAD and official main without fetching, index refresh or merging.

    Active controllers and dirty sources are reported as synchronization
    blockers, never used to suppress this read-only remote query. No ancestor
    or protected-tree claim is possible from ls-remote alone.
    """
    project = Path(project).resolve()
    timeout = min(float(timeout), CHECK_TIMEOUT_SECONDS)
    deadline = time.monotonic() + max(0, min(timeout - 4, CHECK_WORK_SECONDS))
    result = dict(status='unavailable', applied=False, check_only=True, sync_verified=False,
                  checked_at=datetime.now(timezone.utc).isoformat(), sync_blockers=[])

    def query(*args, limit=8):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(['git', *args], timeout)
        return git(project, *args, timeout=min(limit, remaining), read_only=True)

    def canonical_origin():
        raw = query('config', '--get-all', 'remote.origin.url').splitlines()
        effective = query('ls-remote', '--get-url', 'origin')
        if len(raw) != 1 or raw[0] not in CANONICAL or effective not in CANONICAL:
            raise ValueError('origin 原始或解析后的地址不是官方仓库，已拒绝远端查询')
        return raw, effective

    try:
        if not (project / '.git').exists():
            return dict(result, status='zip_install', message='当前是 ZIP 安装，无法比较 Git 提交；自动同步需要官方仓库的 Git 克隆。')
        if Path(query('rev-parse', '--show-toplevel')).resolve() != project:
            return dict(result, status='skipped', message='Git 顶层目录与选定项目不一致，已拒绝检查。')
        if query('symbolic-ref', '--quiet', '--short', 'HEAD') != 'main':
            return dict(result, status='skipped', message='当前分支不是 main，已保留原分支；检查仅支持官方 main。')
        raw, effective = canonical_origin()
        before = query('rev-parse', '--verify', 'HEAD')
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', before):
            raise ValueError('本地 HEAD 提交格式无效')
        result['current_commit'] = before
        dirty = bool(query('status', '--porcelain', '--untracked-files=normal'))
        reason = activity_reason(project)
        if dirty:
            result['sync_blockers'].append('检查时本地源码存在改动，必须先保留并处理这些改动')
        if reason:
            result['sync_blockers'].append(_activity_message(reason))
        # The literal allowlisted URL is used; origin and insteadOf resolution
        # are checked independently before any network operation.
        if query('ls-remote', '--get-url', raw[0]) not in CANONICAL:
            raise ValueError('官方地址被 Git 配置重定向，已拒绝远端查询')
        remote = query('ls-remote', '--heads', raw[0], 'main', limit=CHECK_WORK_SECONDS)
        lines = remote.splitlines()
        if len(lines) != 1 or not re.fullmatch(r'([0-9a-f]{40}|[0-9a-f]{64})\s+refs/heads/main', lines[0]):
            raise ValueError('官方仓库未返回唯一、有效的 main 提交')
        after = lines[0].split()[0]
        result['available_commit'] = after
        # These checks describe the state observed at completion. They neither
        # authorize synchronization nor take the source mutation lock.
        if Path(query('rev-parse', '--show-toplevel')).resolve() != project:
            raise ValueError('检查期间 Git 顶层目录改变，结果需要重新核验')
        if query('symbolic-ref', '--quiet', '--short', 'HEAD') != 'main' or canonical_origin() != (raw, effective):
            raise ValueError('检查期间分支或 origin 配置改变，结果需要重新核验')
        current = query('rev-parse', '--verify', 'HEAD')
        changed = current != before
        if changed:
            result['sync_blockers'].append('检查期间本地 HEAD 改变，请重新检查')
        if query('status', '--porcelain', '--untracked-files=normal') and not dirty:
            result['sync_blockers'].append('检查时本地源码存在改动，必须先保留并处理这些改动')
        activity_message = _activity_message(activity_reason(project))
        if activity_message and activity_message not in result['sync_blockers']:
            result['sync_blockers'].append(activity_message)
        if changed:
            result.update(status='source_changed', message='已读取官方 main，但检查期间本地提交改变；需重新核验。')
        elif before == after:
            result.update(status='up_to_date', message='本地 HEAD 与官方 main 的提交一致。')
        else:
            result.update(status='remote_differs', message='远端版本不同，需安全同步核验。')
        if result['sync_blockers']:
            result['message'] += ' 当前不能自动同步：' + '；'.join(result['sync_blockers']) + '。'
        elif result['status'] == 'remote_differs':
            result['message'] += ' 同步前仍须核验快进历史、受保护路径与运行状态。'
        return result
    except subprocess.TimeoutExpired:
        return dict(result, status='timeout', message=f'检查更新超时（总时限 {timeout:g} 秒），本地源码未更改。')
    except (OSError, ValueError, RuntimeError, psutil.Error) as error:
        return dict(result, status='unavailable', message='检查更新未完成：' + str(error)[:300] + '。本地源码未更改。')


def update(project=PROJECT, apply=True):
    if not apply:
        return check_only(project)
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
    descriptor, staged = tempfile.mkstemp(prefix='.UPDATE_STATUS-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf8') as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--json', action='store_true', help='Return one JSON line for native GUI invocation')
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
    try:
        write_status(PROJECT, result)
    except OSError as error:
        result['status_write_error'] = str(error)[:200]
        result['message'] += ' 检查结果未能写入本地状态文件。'
    print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))


if __name__ == '__main__':
    main()
