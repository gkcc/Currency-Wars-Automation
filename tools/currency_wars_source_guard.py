"""Serialize source updates with registration of GUI/runner lifetimes.

No process enumeration, game-window APIs, input, or historical-run cleanup.
"""
import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from currency_wars_artifacts import process_identity


@contextlib.contextmanager
def mutation_lock(project, timeout_ms=5000):
    name = 'Local\\CurrencyWarsSource-' + hashlib.sha256(str(Path(project).resolve()).casefold().encode()).hexdigest()[:32]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise RuntimeError('Cannot establish source update guard')
    owned = False
    try:
        if kernel.WaitForSingleObject(handle, timeout_ms) not in (0, 128):
            raise RuntimeError('Source update/startup guard is busy')
        owned = True
        yield
    finally:
        if owned:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def read_object(path):
    path = Path(path)
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400 or not path.is_file() or info.st_size > 65536:
        raise ValueError('Activity record path or size is invalid')
    value = json.loads(path.read_text(encoding='utf8'))
    if not isinstance(value, dict):
        raise ValueError('Activity record is not an object')
    return value


def identity_state(pid, identity):
    if type(pid) is not int or not isinstance(identity, str) or not re.fullmatch(r'windows:[0-9]+', identity):
        return 'unknown'
    state, observed = process_identity(pid)
    if state == 'dead' or (state == 'active' and observed is not None and observed != identity):
        return 'dead'
    return 'active' if state == 'active' and observed == identity else 'unknown'


def activity_reason(project, ignore_lease=None):
    directory = Path(project) / 'docs/source-activity'
    if directory.exists():
        info = directory.lstat()
        if directory.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            return 'Activity directory is linked or unknown; update skipped.'
        files = list(directory.iterdir())
        if len(files) > 128:
            return 'Activity inventory exceeds its bound; update skipped.'
        for path in files:
            if ignore_lease is not None and path == ignore_lease:
                continue
            try:
                value = read_object(path)
                if value.get('schema') != 1 or value.get('project') != str(Path(project).resolve()) or value.get('children_incomplete') is not False:
                    return 'Incomplete/unknown controller activity; update skipped.'
                records = [{'pid': value.get('pid'), 'process_identity': value.get('process_identity')}] + value.get('children', [])
                if any(identity_state(record.get('pid'), record.get('process_identity')) != 'dead' for record in records):
                    return 'GUI/runner or owned child is active/unknown; update skipped.'
            except (OSError, ValueError, TypeError, AttributeError):
                return 'Unreadable controller activity; update skipped.'
    current = Path(project) / 'docs/CURRENT_RUNNER.json'
    if current.exists():
        try:
            state = read_object(current)
            if state.get('owner') != 'currency-wars-runner' or not isinstance(state.get('run_id'), str):
                return 'Existing runner ownership is unknown; update skipped.'
            worker = identity_state(state.get('runner_pid'), 'windows:' + str(state.get('runner_creation_id', '')))
            if worker != 'dead':
                return 'Existing runner is active/unknown; update skipped.'
            evidence = state.get('exit_evidence', {}).get('broker', {})
            if evidence.get('state') in ('not_launched', 'launch_failed'):
                if (evidence.get('identity_observed') is not False or evidence.get('run_id') != state.get('run_id')
                        or evidence.get('worker_pid') != state.get('runner_pid')
                        or str(evidence.get('worker_creation_id')) != str(state.get('runner_creation_id'))
                        or evidence.get('launch_id') != state.get('launch_id')
                        or (evidence['state'] == 'not_launched' and evidence.get('launch_attempted') is not False)
                        or (evidence['state'] == 'launch_failed' and (evidence.get('launch_attempted') is not True
                            or type(evidence.get('launch_exit_code')) is not int or evidence['launch_exit_code'] == 0))):
                    return 'Broker startup/exit is unknown; update skipped.'
            elif identity_state(evidence.get('pid'), 'windows:' + str(evidence.get('expected_creation_id', ''))) != 'dead':
                return 'Existing broker is active/unknown; update skipped.'
        except (OSError, ValueError, TypeError, AttributeError):
            return 'Existing runner record is unreadable; update skipped.'
    return None


class Activity:
    def __init__(self, project, kind):
        self.project = Path(project).resolve()
        self.path = self.project / 'docs/source-activity' / (uuid.uuid4().hex + '.json')
        state, identity = process_identity(os.getpid())
        if state != 'active' or identity is None:
            raise RuntimeError('Startup owner creation cannot be verified')
        self.value = dict(schema=1, project=str(self.project), kind=kind, pid=os.getpid(),
                          process_identity=identity, children=[], children_incomplete=False)

    def write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.value), encoding='utf8')

    def children(self, records, complete):
        with mutation_lock(self.project):
            self.value.update(children=list(records), children_incomplete=not complete)
            self.write()

    def close(self):
        with mutation_lock(self.project):
            if self.value['children_incomplete'] or any(identity_state(r['pid'], r['process_identity']) != 'dead' for r in self.value['children']):
                return  # Preserve original ownership when exit is unknown.
            if read_object(self.path) != self.value:
                raise RuntimeError('Activity ownership changed; record retained')
            self.path.unlink()
            try:
                self.path.parent.rmdir()
            except OSError:
                pass  # Other current/unknown owners retain their own records.

    def transfer_to_registered_child(self, pid, identity, kind):
        with mutation_lock(self.project):
            for path in self.path.parent.iterdir():
                value = read_object(path)
                if (value.get('kind') == kind and value.get('pid') == pid
                        and value.get('process_identity') == identity
                        and identity_state(pid, identity) == 'active'):
                    self.value.update(children=[], children_incomplete=False)
                    self.write()
                    return
            raise RuntimeError('Child lifecycle registration is not verified; original activity retained')


@contextlib.contextmanager
def activity(project, kind):
    with mutation_lock(project):
        lease = Activity(project, kind)
        lease.write()
    try:
        yield lease
    finally:
        lease.close()
