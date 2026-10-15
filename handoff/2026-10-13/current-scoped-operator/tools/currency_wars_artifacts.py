"""Portable owned runtimes, compatible with the existing native marker protocol.

Use the installed Agent Workflow helper when available. The standalone
implementation only cleans directories created by this process; it never
sweeps old runs or adopts another chat's files.
"""
from __future__ import annotations

import hashlib
import contextlib
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

MARKER = '.agent-workflow-owner.json'
TOOL = 'codex-agent-workflow'


class ArtifactError(ValueError):
    pass


def default_root():
    return Path(tempfile.gettempdir()) / TOOL


def process_identity(pid):
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF or os.name != 'nt':
        return 'unknown', None
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ('dead', None) if ctypes.get_last_error() == 87 else ('unknown', None)
    try:
        created, exited, system, user = (w.FILETIME() for _ in range(4))
        code = w.DWORD()
        if not kernel.GetProcessTimes(handle, created, exited, system, user):
            return 'unknown', None
        identity = 'windows:' + str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        if not kernel.GetExitCodeProcess(handle, code):
            return 'unknown', None
        return ('active' if code.value == 259 else 'dead'), identity
    finally:
        kernel.CloseHandle(handle)


def _no_links(path):
    for candidate in (path, *path.parents):
        if candidate.exists() or candidate.is_symlink():
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ArtifactError('Runtime paths must not contain links or junctions')


def _target(path, root):
    path, root = Path(path), Path(root)
    if not path.is_absolute() or not root.is_absolute():
        raise ArtifactError('Runtime paths must be absolute')
    _no_links(path)
    _no_links(root)
    if root == Path(root.anchor) or path.parent != root or path.resolve().parent != root.resolve() or not path.is_dir():
        raise ArtifactError('Runtime must be a direct child of its declared root')
    return path, root


def _children(records):
    if not isinstance(records, list) or len(records) > 32:
        raise ArtifactError('Child inventory exceeds its bound')
    result = {}
    for record in records:
        if not isinstance(record, dict):
            raise ArtifactError('Invalid child identity')
        pid, identity = record.get('pid'), record.get('process_identity')
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF or not isinstance(identity, str) or not re.fullmatch(r'windows:[0-9]+', identity):
            raise ArtifactError('Invalid child identity')
        result[(pid, identity)] = {'pid': pid, 'process_identity': identity}
    return list(result.values())


def read_marker(path, *, root=None, purpose=None):
    path, root = _target(path, default_root() if root is None else root)
    marker = path / MARKER
    _no_links(marker)
    if not marker.is_file() or marker.stat().st_size > 4096:
        raise ArtifactError('Runtime marker is absent or too large')
    value = json.loads(marker.read_text(encoding='utf8'))
    if (not isinstance(value, dict) or value.get('schema') != 1 or value.get('tool') != TOOL
            or value.get('path') != str(path) or value.get('root') != str(root)
            or not re.fullmatch(r'[0-9a-f]{32}', str(value.get('run_id', '')))
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', str(value.get('purpose', '')))
            or (purpose is not None and value['purpose'] != purpose)
            or type(value.get('pid')) is not int or value['pid'] <= 0
            or not re.fullmatch(r'windows:[0-9]+', str(value.get('process_identity', '')))
            or type(value.get('children_incomplete')) is not bool):
        raise ArtifactError('Runtime provenance mismatch')
    _children(value.get('protected_children'))
    hint = value.get('session_hint')
    if hint is not None and (not isinstance(hint, dict) or hint.get('source') != 'environment-declared'
                            or not isinstance(hint.get('id'), str) or not 1 <= len(hint['id']) <= 128):
        raise ArtifactError('Runtime session hint is invalid')
    return value


def _owned(value):
    if value['pid'] != os.getpid() or process_identity(os.getpid()) != ('active', value['process_identity']):
        raise ArtifactError('Only the actual current owner may modify or remove this runtime')


def _windows_user_sid():
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [w.LPVOID]
    kernel.LocalFree.restype = w.LPVOID
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    security.OpenProcessToken.restype = w.BOOL
    security.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)]
    security.GetTokenInformation.restype = w.BOOL
    security.ConvertSidToStringSidW.argtypes = [w.LPVOID, ctypes.POINTER(w.LPWSTR)]
    security.ConvertSidToStringSidW.restype = w.BOOL
    token, text = w.HANDLE(), w.LPWSTR()
    try:
        if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        size = w.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value or size.value > 65536:
            raise ArtifactError('Current Windows user SID size could not be verified')
        data = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(data, ctypes.POINTER(w.LPVOID)).contents.value
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        result = text.value
        if not result or len(result) > 184 or not re.fullmatch(r'S-1-(?:[0-9]+-)*[0-9]+', result):
            raise ArtifactError('Current Windows user SID could not be verified')
        return result
    finally:
        if text:
            kernel.LocalFree(ctypes.cast(text, w.LPVOID))
        if token:
            kernel.CloseHandle(token)


def prepare_elevated_ipc_access(path, *, root=None, expected_run_id):
    """Keep one new runtime private to its initiating user across RunAs.

    OWNER RIGHTS alone follows an elevated child's Administrators owner.
    An explicit initiating-user ACE also covers that child's new files.
    """
    path = Path(path)
    declared_root = default_root() if root is None else Path(root)
    marker = read_marker(path, root=declared_root)
    _owned(marker)
    if (marker['run_id'] != expected_run_id or not marker['purpose'].startswith('currency-wars-runner-')
            or marker['children_incomplete'] or marker['protected_children']):
        raise ArtifactError('IPC access must be prepared for the original new worker before launching children')
    if os.name != 'nt':
        raise ArtifactError('Elevated IPC access requires Windows')
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [w.LPWSTR, w.UINT]
    kernel.GetSystemDirectoryW.restype = w.UINT
    system = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(system, len(system))
    if not 0 < length < len(system):
        raise ArtifactError('Windows system directory could not be verified')
    sid = _windows_user_sid()
    if read_marker(path, root=declared_root) != marker:
        raise ArtifactError('Runtime provenance changed before IPC access setup')
    _owned(marker)
    result = subprocess.run([str(Path(system.value) / 'icacls.exe'), str(path), '/grant',
                             '*' + sid + ':(OI)(CI)F'], capture_output=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ArtifactError('Current-user runtime IPC access setup failed: ' + str(result.returncode))
    if read_marker(path, root=declared_root) != marker:
        raise ArtifactError('Runtime provenance changed during IPC access setup')
    _owned(marker)
    return {'user_sid': sid, 'scope': 'this owned runtime and its inherited child files'}


def _dead(record):
    state, identity = process_identity(record['pid'])
    return state == 'dead' or (state == 'active' and identity is not None and identity != record['process_identity'])


def protect_children(path, children, *, root=None, complete=None, expected_run_id=None):
    root = default_root() if root is None else root
    value = read_marker(path, root=root)
    _owned(value)
    if expected_run_id is not None and expected_run_id != value['run_id']:
        raise ArtifactError('Runtime identity changed')
    records = _children(value['protected_children'] + _children(children))
    records = [record for record in records if not _dead(record)]
    if complete is True and records:
        raise ArtifactError('Owned child processes are still active or unknown')
    updated = dict(value, protected_children=records)
    if complete is not None:
        updated['children_incomplete'] = not complete
    payload = json.dumps(updated)
    if len(payload.encode('utf8')) > 4096:
        raise ArtifactError('Runtime marker exceeds its bound')
    staging = Path(path) / (MARKER + '.' + uuid.uuid4().hex)
    try:
        staging.write_text(payload, encoding='utf8')
        if read_marker(path, root=root) != value:
            raise ArtifactError('Runtime provenance changed during child registration')
        os.replace(staging, Path(path) / MARKER)
    finally:
        staging.unlink(missing_ok=True)
    return updated


def _cleanup_current(path, root, run_id):
    value = read_marker(path, root=root)
    _owned(value)
    if value['run_id'] != run_id or value['children_incomplete'] or any(not _dead(c) for c in value['protected_children']):
        raise ArtifactError('Cleanup requires the original run and verified child exit')
    # Inspect the entire bounded tree before removing any payload. Keep the
    # original marker if a locked file or a policy rejection interrupts removal.
    count, size = 0, 0
    for folder, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            candidate = Path(folder) / name
            _no_links(candidate)
            info = candidate.lstat()
            count += 1
            size += info.st_size if stat.S_ISREG(info.st_mode) else 0
            if (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
                    or count > 20000 or size > 512 * 1024 * 1024):
                raise ArtifactError('Cleanup tree type or size exceeds its bound')
    marker_bytes = (path / MARKER).read_bytes()
    directory_identity = (path.stat().st_dev, path.stat().st_ino)
    for child in path.iterdir():
        if child.name == MARKER:
            continue
        if read_marker(path, root=root) != value:
            raise ArtifactError('Runtime provenance changed during cleanup')
        _no_links(child)
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    (path / MARKER).unlink()
    try:
        path.rmdir()
    except BaseException:
        if path.is_dir() and (path.stat().st_dev, path.stat().st_ino) == directory_identity:
            with (path / MARKER).open('xb') as stream:
                stream.write(marker_bytes)
        raise


@contextlib.contextmanager
def scratch_directory(purpose='run', *, root=None):
    root = default_root() if root is None else Path(root)
    if not root.is_absolute() or root == Path(root.anchor) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', purpose):
        raise ArtifactError('Invalid runtime root or purpose')
    _no_links(root)
    root.mkdir(parents=True, exist_ok=True)
    state, identity = process_identity(os.getpid())
    if state != 'active' or identity is None:
        raise ArtifactError('Current process identity cannot be verified')
    path = Path(tempfile.mkdtemp(prefix=purpose + '-', dir=root))
    value = dict(schema=1, tool=TOOL, run_id=uuid.uuid4().hex, purpose=purpose,
                 pid=os.getpid(), process_identity=identity, created_at=time.time(),
                 root=str(root), path=str(path), protected_children=[], children_incomplete=False)
    session = os.environ.get('CODEX_THREAD_ID')
    if session and len(session) <= 128 and all(ord(char) >= 33 for char in session):
        value['session_hint'] = {'id': session, 'source': 'environment-declared'}
    (path / MARKER).write_text(json.dumps(value), encoding='utf8')
    try:
        yield path
    finally:
        _cleanup_current(path, root, value['run_id'])


# Local-only selection provenance, included by the unified readiness verifier.
PROVIDER_SOURCE = dict(kind='standalone', path=str(Path(__file__).resolve()),
                       sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest().upper())


# Existing installations keep their established lifecycle implementation.
# Standalone mode is also used by the packaging checks in an isolated temp run.
if os.environ.get('CW_ARTIFACTS_STANDALONE') != '1':
    homes = [Path(os.environ['CODEX_HOME'])] if os.environ.get('CODEX_HOME') else []
    homes.append(Path.home() / '.codex')
    for home in homes:
        helper = home / 'skills/agent-workflow/scripts/artifacts.py'
        if helper.is_file():
            spec = importlib.util.spec_from_file_location('_currency_wars_installed_artifacts', helper)
            installed = importlib.util.module_from_spec(spec)
            payload = helper.read_bytes()
            if not 0 < len(payload) <= 4_000_000:
                raise ArtifactError('Installed artifact provider size is invalid')
            if helper.read_bytes() != payload:
                raise ArtifactError('Installed artifact provider changed during loading')
            exec(compile(payload, str(helper), 'exec'), installed.__dict__)
            PROVIDER_SOURCE = dict(kind='installed', path=str(helper.resolve()),
                                   sha256=hashlib.sha256(payload).hexdigest().upper())
            for name in ('ArtifactError', 'default_root', 'process_identity', 'read_marker'):
                globals()[name] = getattr(installed, name)

            _installed_leases = {}

            @contextlib.contextmanager
            def scratch_directory(purpose='run', *, root=None):
                if root is not None:
                    installed.sweep_stale(root=root, purpose=purpose)
                owned = installed.create_owned_directory(purpose, root=root)
                key = os.path.normcase(str(owned.path))
                _installed_leases[key] = owned
                try:
                    yield owned.path
                finally:
                    try:
                        owned.close()
                    finally:
                        _installed_leases.pop(key, None)

            def protect_children(path, children, *, root=None, complete=None, expected_run_id=None):
                key = os.path.normcase(os.path.abspath(path))
                owned = _installed_leases.get(key)
                if owned is None:
                    return installed.protect_children(path, children, root=root, complete=complete,
                                                      expected_run_id=expected_run_id)
                declared_root = default_root() if root is None else Path(root)
                if os.path.normcase(os.path.abspath(declared_root)) != os.path.normcase(str(owned.root)):
                    raise ArtifactError('Runtime root changed during child registration')
                if expected_run_id is not None and expected_run_id != owned._value['run_id']:
                    raise ArtifactError('Runtime identity changed')
                # Never adopt a separately changed marker, even if its run id
                # is unchanged. Registration and close share one owned lease.
                if read_marker(owned.path, root=owned.root) != owned._value:
                    raise ArtifactError('Artifact provenance changed before child registration')
                owned.protect_children(children, complete=complete)
                return dict(owned._value)
            break
