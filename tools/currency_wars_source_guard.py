"""Serialize source updates with registration of GUI/runner lifetimes.

No game input or historical cleanup. Explicit operator recovery supports either
a verified intervening reboot or the separately reviewed scoped-control protocol.
"""
import ast
import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import uuid

from currency_wars_artifacts import process_identity
import currency_wars_artifacts as artifacts

SOURCE_MANIFEST = 'tools/currency_wars_runtime_sources.json'
MAX_ACTIVITY_RECORDS = 512


def _source_bytes(project, name, *, max_bytes=4_000_000):
    path = Path(project) / name
    for item in (path, *path.parents):
        info = item.lstat()
        if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Production source path is linked: ' + name)
        if item == Path(project):
            break
    if (type(max_bytes) is not int or not 1 <= max_bytes <= 32_000_000
            or not path.is_file() or not 0 < path.stat().st_size <= max_bytes):
        raise ValueError('Production source is missing or exceeds its bound: ' + name)
    return path.read_bytes()


def production_files(project):
    """One shared, bounded inventory, including lazy local imports and itself."""
    project = Path(project).resolve()
    manifest = json.loads(_source_bytes(project, SOURCE_MANIFEST))
    files, roots = manifest.get('files'), manifest.get('entrypoints')
    if (manifest.get('schema') != 1 or type(manifest['schema']) is not int
            or not isinstance(files, list) or not 1 <= len(files) <= 128
            or not isinstance(roots, list) or not roots
            or any(not isinstance(name, str) or PurePosixPath(name).is_absolute()
                   or ':' in name or '\\' in name or any(part in ('', '.', '..') for part in name.split('/')) for name in files)
            or len(set(files)) != len(files) or SOURCE_MANIFEST not in files
            or not set(roots) <= set(files)):
        raise ValueError('Production source inventory is invalid')
    # Parse only; never import a module or initialize an OCR/model/controller.
    # Include imports inside functions so the typed refresh reader is covered.
    covered, pending = set(), list(roots)
    while pending:
        name = pending.pop()
        if name in covered:
            continue
        covered.add(name)
        tree = ast.parse(_source_bytes(project, name), filename=name)
        for node in ast.walk(tree):
            imports = ([item.name for item in node.names] if isinstance(node, ast.Import) else
                       [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for imported in imports:
                module = imported.split('.')[0]
                candidates = [PurePosixPath(name).parent / (module + '.py'),
                              PurePosixPath('tools') / (module + '.py'),
                              PurePosixPath('gui') / (module + '.py')]
                local = next((str(path) for path in candidates if (project / path).is_file()), None)
                if local is not None:
                    if local not in files:
                        raise ValueError('Production dependency is absent from the source inventory: ' + local)
                    pending.append(local)
                elif module.startswith('currency_wars_'):
                    raise ValueError('Production dependency is missing: ' + module)
    return tuple(files)


def source_hashes(project):
    """Current bytes only; this does not grant or manufacture a ready review."""
    project = Path(project).resolve()
    files = production_files(project)
    return {name: hashlib.sha256(_source_bytes(project, name)).hexdigest().upper() for name in files}


def verify_source_hashes(project, expected):
    """Refuse a missing dependency or changed byte against the reviewed set."""
    actual = source_hashes(project)
    if not isinstance(expected, dict):
        raise ValueError('Reviewed production source hashes are missing')
    for name, digest in actual.items():
        approved = expected.get(name)
        if not isinstance(approved, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', approved) or approved.upper() != digest:
            raise ValueError('Production source changed or was not reviewed: ' + name)
    return actual


def _relative(name):
    if (not isinstance(name, str) or not name or PurePosixPath(name).is_absolute()
            or ':' in name or '\\' in name
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Resource inventory path is invalid')
    return name


def resource_snapshot(project):
    """Bind selected local assets without copying them or decoding any image.

    Required reader manifests must be complete. Optional native resource roots
    record absence explicitly; absence does not grant that feature readiness.
    """
    project = Path(project).resolve()
    manifest = json.loads(_source_bytes(project, SOURCE_MANIFEST))
    roots, declarations = manifest.get('resource_roots'), manifest.get('resource_manifests')
    if not isinstance(roots, list) or not roots or not isinstance(declarations, list) or not declarations:
        raise ValueError('Production resource inventory is missing')
    inventory, hashes = {}, {}
    for spec in roots:
        name = _relative(spec['path'])
        if type(spec.get('required')) is not bool or name in inventory:
            raise ValueError('Resource root declaration is invalid')
        root = project / name
        try:
            root_info = root.lstat()
        except FileNotFoundError:
            if spec['required']:
                raise ValueError('Required reader resources are missing: ' + name)
            inventory[name] = dict(present=False, files=[])
            continue
        if not root.is_dir() or root.is_symlink() or getattr(root_info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Resource root is linked or invalid: ' + name)
        files = []
        for path in root.rglob('*'):
            info = path.lstat()
            if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Resource entry is linked: ' + name)
            if path.is_dir():
                continue
            relative = path.relative_to(project).as_posix()
            hashes[relative] = hashlib.sha256(_source_bytes(project, relative)).hexdigest().upper()
            files.append(relative)
            if len(files) > 512:
                raise ValueError('Resource inventory exceeds its bound')
        inventory[name] = dict(present=True, files=sorted(files))
    for spec in declarations:
        name = _relative(spec['path'])
        if name not in hashes:
            raise ValueError('Reader source manifest is missing: ' + name)
        payload = _source_bytes(project, name)
        if hashlib.sha256(payload).hexdigest().upper() != hashes[name]:
            raise ValueError('Reader source manifest changed during validation: ' + name)
        value = json.loads(payload)
        entries = value.get('resources') if spec['layout'] == 'resources' else [value] if spec['layout'] == 'single' else None
        if not isinstance(entries, list) or not 1 <= len(entries) <= 512:
            raise ValueError('Reader source manifest has no bounded resource list: ' + name)
        declared = set()
        for entry in entries:
            filename = _relative(entry['file'])
            if filename in declared:
                raise ValueError('Reader source manifest has duplicate assets: ' + name)
            declared.add(filename)
            relative = str(PurePosixPath(name).parent / filename)
            if relative not in hashes:
                raise ValueError('Declared reader resource is missing: ' + relative)
            if 'sha256' in entry and str(entry['sha256']).upper() != hashes[relative]:
                raise ValueError('Reader resource differs from its source manifest: ' + relative)
        if not set(spec.get('required_files', [])) <= declared:
            raise ValueError('Reader source manifest omits a required asset: ' + name)
    if not set(manifest.get('resource_files', [])) <= hashes.keys():
        raise ValueError('Required reader metadata is missing')
    # Individually named data files avoid recursively binding imports, caches
    # or another chat's data. The catalogue is larger than small reader assets.
    data_files = manifest.get('data_files', [])
    if not isinstance(data_files, list) or len(data_files) > 16:
        raise ValueError('Bounded data file inventory is invalid')
    for spec in data_files:
        if not isinstance(spec, dict) or set(spec) != {'path', 'max_bytes'}:
            raise ValueError('Data file declaration is invalid')
        name = _relative(spec['path'])
        if name in hashes or name in inventory:
            raise ValueError('Data file declaration is duplicated')
        payload = _source_bytes(project, name, max_bytes=spec['max_bytes'])
        hashes[name] = hashlib.sha256(payload).hexdigest().upper()
        inventory[name] = dict(present=True, files=[name])
    return dict(resource_inventory=inventory, hashes=hashes)


def runtime_provider():
    """Selected loaded provider identity. Private paths stay in local READY."""
    value = dict(artifacts.PROVIDER_SOURCE)
    path = Path(value['path'])
    if set(value) != {'kind', 'path', 'sha256'} or value['kind'] not in ('installed', 'standalone') or not path.is_absolute():
        raise ValueError('Selected artifact provider identity is invalid')
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        if ancestor.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Selected artifact provider path is linked')
    payload = _source_bytes(path.parent, path.name)
    if hashlib.sha256(payload).hexdigest().upper() != value['sha256']:
        raise ValueError('Loaded artifact provider source changed; restart and review it')
    return value


def runtime_source_binding(project):
    """Return only a local binding fragment; never grant ready/PASS/ownership."""
    resources = resource_snapshot(project)
    resources['hashes'] = dict(source_hashes(project), **resources['hashes'])
    resources['runtime_provider'] = runtime_provider()
    return resources


def verify_runtime_sources(project, ready):
    """Shared GUI/launcher/runner byte gate; caller verifies review ownership."""
    if not isinstance(ready, dict):
        raise ValueError('Reviewed runtime sources are missing')
    actual = runtime_source_binding(project)
    if ready.get('resource_inventory') != actual['resource_inventory']:
        raise ValueError('Reviewed resource inventory is missing or changed')
    if ready.get('runtime_provider') != actual['runtime_provider']:
        raise ValueError('Reviewed artifact provider is missing or changed')
    expected = ready.get('hashes')
    if not isinstance(expected, dict) or set(expected) != set(actual['hashes']):
        raise ValueError('Reviewed runtime source set is missing or changed')
    for name, digest in actual['hashes'].items():
        if not isinstance(expected[name], str) or expected[name].upper() != digest:
            raise ValueError('Reviewed runtime source bytes changed: ' + name)
    return actual


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


def read_object(path, *, limit=65536):
    path = Path(path)
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400 or not path.is_file() or info.st_size > limit:
        raise ValueError('Activity record path or size is invalid')
    payload = path.read_bytes()
    if len(payload) > limit:
        raise ValueError('Activity record path or size is invalid')
    value = json.loads(payload.decode('utf8'))
    if not isinstance(value, dict):
        raise ValueError('Activity record is not an object')
    return value


def _native_creation_table():
    """Bounded complete x64 SystemProcessInformation; no process handles."""
    if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8:
        return None
    try:
        query = ctypes.WinDLL('ntdll').NtQuerySystemInformation
        query.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
                          ctypes.POINTER(wintypes.ULONG)]
        query.restype = ctypes.c_long
        capacity = 65536
        for unused in range(8):
            buffer = ctypes.create_string_buffer(capacity)
            returned = wintypes.ULONG()
            status = query(5, buffer, capacity, ctypes.byref(returned)) & 0xffffffff
            if status == 0xc0000004:
                capacity = max(capacity * 2, int(returned.value))
                if capacity > 16_777_216:
                    return None
                continue
            if status != 0 or not 256 <= returned.value <= capacity:
                return None
            data = buffer.raw[:returned.value]
            records, offset = {}, 0
            for unused_record in range(65536):
                if offset + 256 > len(data):
                    return None
                next_offset = int.from_bytes(data[offset:offset + 4], 'little')
                threads = int.from_bytes(data[offset + 4:offset + 8], 'little')
                creation = int.from_bytes(data[offset + 32:offset + 40], 'little', signed=True)
                pid = int.from_bytes(data[offset + 80:offset + 88], 'little')
                size = next_offset or len(data) - offset
                if (size < 256 + threads * 80 or size % 8 or offset + size > len(data)
                        or pid > 0xffffffff or pid in records
                        or creation < 0 or pid != 0 and creation == 0):
                    return None
                records[pid] = creation
                if next_offset == 0:
                    observer_state, observer_identity = process_identity(os.getpid())
                    if (observer_state != 'active' or not isinstance(observer_identity, str)
                            or not re.fullmatch(r'windows:[1-9][0-9]*', observer_identity)
                            or records.get(os.getpid()) != int(observer_identity.split(':', 1)[1])):
                        return None
                    return records
                offset += next_offset
            return None
        return None
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        return None


def identity_state(pid, identity):
    if type(pid) is not int or not isinstance(identity, str) or not re.fullmatch(r'windows:[0-9]+', identity):
        return 'unknown'
    state, observed = process_identity(pid)
    if state == 'dead' or (state == 'active' and observed is not None and observed != identity):
        return 'dead'
    if state == 'unknown' and pid > 0 and int(identity.split(':', 1)[1]) > 0:
        table = _native_creation_table()
        if table is not None:
            return 'active' if table.get(pid) == int(identity.split(':', 1)[1]) else 'dead'
    return 'active' if state == 'active' and observed == identity else 'unknown'


def activity_reason(project, ignore_lease=None):
    directory = Path(project) / 'docs/source-activity'
    if directory.exists():
        info = directory.lstat()
        if directory.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            return 'Activity directory is linked or unknown; update skipped.'
        files = list(directory.iterdir())
        if len(files) > MAX_ACTIVITY_RECORDS:
            return 'Activity inventory exceeds its bound; update skipped.'
        for path in files:
            if ignore_lease is not None and path == ignore_lease:
                continue
            try:
                value = read_object(path)
                if (value.get('kind') == 'start' and value.get('children_incomplete') is True
                        and _retired_start(project, path, value)):
                    continue
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
            # CURRENT retains full native observations and terminal receipts.
            # Match the original runner JSON bound; activity leases stay 64 KiB.
            state = read_object(current, limit=2_000_000)
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

    def bind_launch(self, chat_id, launch_id, start_lease=None):
        """Record current launch facts before slow startup; not historical repair."""
        if (not isinstance(chat_id, str) or not 1 <= len(chat_id) <= 100
                or not isinstance(launch_id, str) or not re.fullmatch(r'[0-9a-f]{32}', launch_id)
                or start_lease is not None and not re.fullmatch(r'[0-9a-f]{32}\.json', start_lease)):
            raise ValueError('Invalid current launch registration')
        with mutation_lock(self.project):
            if read_object(self.path) != self.value:
                raise RuntimeError('Activity ownership changed; original record retained')
            self.value.update(chat_id=chat_id, launch_id=launch_id, start_lease=start_lease)
            self.write()

    def registered_launch_child(self):
        """A candidate only: the caller MUST verify native Popen parentage."""
        with mutation_lock(self.project):
            paths = list(self.path.parent.iterdir())
            if len(paths) > MAX_ACTIVITY_RECORDS:
                raise RuntimeError('Activity inventory exceeds its bound')
            matches = []
            for path in paths:
                value = read_object(path)
                if (value.get('schema') == 1 and value.get('project') == str(self.project)
                        and value.get('kind') == 'runner' and value.get('start_lease') == self.path.name
                        and value.get('chat_id') == self.value.get('chat_id')
                        and value.get('launch_id') == self.value.get('launch_id')
                        and re.fullmatch(r'windows:[0-9]+', str(value.get('process_identity', '')))):
                    matches.append(dict(chat_id=value['chat_id'], launch_id=value['launch_id'],
                                        runner_pid=value.get('pid'),
                                        runner_creation_id=value['process_identity'].split(':', 1)[1]))
            if len(matches) > 1:
                raise RuntimeError('Ambiguous launch registration; original activity retained')
            return matches[0] if matches else None

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
                    # A Windows venv redirector can outlive this start CLI.
                    # Transfer the registered worker only; retain any original
                    # launch wrapper until its creation identity is dead.
                    retained = [record for record in self.value['children']
                                if (record['pid'], record['process_identity']) != (pid, identity)]
                    self.value.update(children=retained, children_incomplete=False)
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


# This is an operator recovery for the cooperative source-activity protocol,
# not a general process cleaner or proof about arbitrary unregistered software.
RECOVERY_SCHEMA = 'currency-wars-dead-start-v2'


def _plain_directory(path):
    path = Path(path)
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Recovery path contains a link or reparse point')
    return path


def windows_uptime_ms():
    """Documented monotonic boot-relative clock; never infer reboot from UTC."""
    if os.name != 'nt':
        raise RuntimeError('Windows uptime is unavailable on this platform')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetTickCount64.argtypes = []
    kernel.GetTickCount64.restype = ctypes.c_uint64
    value = kernel.GetTickCount64()
    if not 0 < value < 2 ** 63:
        raise RuntimeError('Windows uptime could not be verified')
    return value


def windows_machine_id():
    """Keep a prepared receipt local to this Windows installation."""
    if os.name != 'nt':
        raise RuntimeError('Windows installation identity is unavailable')
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                        0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        value, kind = winreg.QueryValueEx(key, 'MachineGuid')
    if kind != winreg.REG_SZ:
        raise RuntimeError('Windows installation identity is invalid')
    return uuid.UUID(value).hex


def _dead_start(project, path, digest, parent_pid, parent_identity):
    _plain_directory(path.parent)
    value = read_object(path)
    payload = path.read_bytes()
    if (not re.fullmatch(r'[0-9a-f]{64}', digest)
            or hashlib.sha256(payload).hexdigest() != digest
            or json.loads(payload) != value
            or type(value.get('schema')) is not int or value['schema'] != 1
            or value.get('kind') != 'start' or value.get('project') != str(project)
            or value.get('children_incomplete') is not True
            or value.get('pid') != parent_pid or value.get('process_identity') != parent_identity
            or not isinstance(value.get('children'), list) or len(value['children']) > 32):
        raise ValueError('Exact incomplete start lease does not match the operator selection')
    records = [dict(pid=parent_pid, process_identity=parent_identity)] + value['children']
    if any(not isinstance(row, dict) or identity_state(row.get('pid'), row.get('process_identity')) != 'dead'
           for row in records):
        raise ValueError('Original parent/wrapper is active or unknown; recovery refused')
    return value, payload


def _recovery_path(project, lease_path):
    return _plain_directory(Path(project) / 'docs/source-activity-recovery') / (lease_path.stem + '.retirement-v2.json')


def _retired_start(project, path, value):
    """Only this unchanged dead start may consume its append-only receipt.

    Other leases and CURRENT_RUNNER (including broker exit) are still checked.
    A receipt never changes historical parentage, markers, or ready approval.
    """
    from currency_wars_scoped_recovery import accepted_receipt
    if accepted_receipt(project, path, value):
        return True
    receipt_path = _recovery_path(project, path)
    if not receipt_path.exists():
        return False
    receipt = read_object(receipt_path, limit=2_000_000)
    intent_path = receipt_path.with_suffix('.intent.json')
    intent = read_object(intent_path, limit=300_000)
    if (receipt.get('schema') != RECOVERY_SCHEMA or receipt.get('lease_name') != path.name
            or receipt.get('project') != str(Path(project).resolve())
            or receipt.get('historical_parentage') != 'unknown'
            or receipt.get('input_sent') is not False
            or receipt.get('original_lease') != value
            or receipt.get('assessment') != 'retired_after_boot_boundary'
            or receipt.get('intent_sha256') != hashlib.sha256(intent_path.read_bytes()).hexdigest()
            or intent.get('schema') != RECOVERY_SCHEMA or intent.get('lease_sha256') != receipt.get('lease_sha256')
            or intent.get('original_bytes_hex') != receipt.get('original_bytes_hex')
            or intent.get('project') != receipt.get('project') or intent.get('lease_name') != path.name
            or intent.get('machine_id') != receipt.get('machine_id')
            or receipt.get('machine_id') != windows_machine_id()
            or type(intent.get('uptime_ms')) is not int or type(receipt.get('uptime_ms')) is not int
            or not 0 < receipt['uptime_ms'] < intent['uptime_ms'] < 2 ** 63
            or receipt.get('other_activity_reason') is not None
            or not isinstance(receipt.get('activity_snapshot'), dict)
            or not isinstance(receipt.get('source_hashes'), dict) or not receipt['source_hashes']):
        raise ValueError('Recovery receipt is incomplete or changed')
    _, payload = _dead_start(Path(project).resolve(), path, receipt.get('lease_sha256', ''),
                            value.get('pid'), value.get('process_identity'))
    if receipt.get('original_bytes_hex') != payload.hex():
        raise ValueError('Original lease bytes changed after recovery')
    return True


def _activity_snapshot(project):
    """Hash only the existing bounded registry and CURRENT; not unrelated PIDs."""
    directory = _plain_directory(Path(project) / 'docs/source-activity')
    paths = sorted(directory.iterdir()) if directory.exists() else []
    if len(paths) > MAX_ACTIVITY_RECORDS:
        raise ValueError('Activity inventory exceeds its bound')
    hashes = {}
    for path in paths:
        read_object(path)  # Original size, object and no-link contract.
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    current = Path(project) / 'docs/CURRENT_RUNNER.json'
    current_hash = None
    if current.exists():
        _plain_directory(current.parent)
        read_object(current, limit=2_000_000)
        current_hash = hashlib.sha256(current.read_bytes()).hexdigest()
    return dict(leases=hashes, current_runner_sha256=current_hash)


def recover_dead_start(project, lease_name, digest, parent_pid, parent_identity, *, apply=False, prepare=False, preflight=False):
    """One selected lease, no deletion/move/marker write, no game or READY write.

    The same mutation lock fences all cooperating new activity registrations.
    A prior persisted GetTickCount64 MUST exceed the current uptime. This
    extinguishes the old process lineage without claiming historical parentage
    or a machine-wide absence of unrelated/new unregistered processes.
    This API is intentionally not called by normal startup or the updater.
    """
    project = _plain_directory(Path(project).absolute()).resolve()
    if not re.fullmatch(r'[0-9a-f]{32}\.json', lease_name):
        raise ValueError('Select one exact activity lease basename')
    path = project / 'docs/source-activity' / lease_name
    with mutation_lock(project):
        value, payload = _dead_start(project, path, digest, parent_pid, parent_identity)
        target = _recovery_path(project, path)
        if target.exists():
            if _retired_start(project, path, value):
                return dict(ok=True, already_retired=True, input_sent=False)
            raise ValueError('An existing receipt is not a verified retirement')
        intent_path = target.with_suffix('.intent.json')
        uptime, machine = windows_uptime_ms(), windows_machine_id()
        if sum(bool(flag) for flag in (apply, prepare, preflight)) > 1:
            raise ValueError('Preflight, prepare and apply are separate operations')
        if preflight:
            sources = source_hashes(project)
            reason = activity_reason(project, ignore_lease=path)
            if reason:
                raise RuntimeError(reason)
            _activity_snapshot(project)
            return dict(ok=True, preflight_only=True, retired=False, input_sent=False,
                        reboot_proven=False, reviewed_source_count=len(sources))
        if prepare:
            if apply:
                raise ValueError('Prepare and apply are separate operations')
            intent = dict(schema=RECOVERY_SCHEMA, project=str(project), lease_name=lease_name,
                          lease_sha256=digest, original_bytes_hex=payload.hex(), uptime_ms=uptime, machine_id=machine)
            if intent_path.exists():
                previous = read_object(intent_path, limit=300_000)
                if any(previous.get(key) != intent[key] for key in intent if key != 'uptime_ms'):
                    raise ValueError('Existing recovery intent differs; retained without overwrite')
                return dict(ok=True, prepared=True, retired=False, input_sent=False,
                            restart_still_required=not (type(previous.get('uptime_ms')) is int and 0 < uptime < previous['uptime_ms']))
            target.parent.mkdir(parents=True, exist_ok=True)
            _plain_directory(target.parent)
            with intent_path.open('xb') as stream:
                stream.write(json.dumps(intent, sort_keys=True).encode('utf8'))
                stream.flush()
                os.fsync(stream.fileno())
            if read_object(intent_path, limit=300_000) != intent:
                raise RuntimeError('Recovery intent readback failed')
            return dict(ok=True, prepared=True, retired=False, input_sent=False,
                        restart_still_required=True)
        intent = read_object(intent_path, limit=300_000)
        if (intent.get('schema') != RECOVERY_SCHEMA or intent.get('project') != str(project)
                or intent.get('lease_name') != lease_name or intent.get('lease_sha256') != digest
                or intent.get('original_bytes_hex') != payload.hex()
                or intent.get('machine_id') != machine
                or type(intent.get('uptime_ms')) is not int
                or not 0 < uptime < intent['uptime_ms'] < 2 ** 63):
            raise RuntimeError('No verified intervening Windows boot; current process absence cannot retire this lease')
        intent_digest = hashlib.sha256(intent_path.read_bytes()).hexdigest()
        # No other incomplete, active or unknown actor, including CURRENT's
        # original broker, is waived. This does not adopt another dead lease.
        reason = activity_reason(project, ignore_lease=path)
        if reason:
            raise RuntimeError(reason)
        sources = source_hashes(project)
        activity_snapshot = _activity_snapshot(project)
        if activity_reason(project, ignore_lease=path) is not None or source_hashes(project) != sources:
            raise RuntimeError('Source or activity changed during recovery')
        if _activity_snapshot(project) != activity_snapshot:
            raise RuntimeError('Registered activity changed; no retirement written')
        _, final_payload = _dead_start(project, path, digest, parent_pid, parent_identity)
        if (final_payload != payload or not uptime <= windows_uptime_ms() < intent['uptime_ms']
                or windows_machine_id() != machine
                or hashlib.sha256(intent_path.read_bytes()).hexdigest() != intent_digest):
            raise RuntimeError('Selected lease changed during recovery')
        receipt = dict(schema=RECOVERY_SCHEMA, project=str(project), lease_name=lease_name,
                       lease_sha256=digest, original_lease=value, original_bytes_hex=payload.hex(),
                       historical_parentage='unknown', assessment='retired_after_boot_boundary',
                       uptime_ms=uptime, machine_id=machine, intent_sha256=intent_digest,
                       input_sent=False, other_activity_reason=None,
                       activity_snapshot=activity_snapshot, source_hashes=sources)
        import datetime
        receipt['observed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2).encode('utf8')
        if len(encoded) > 2_000_000:
            raise RuntimeError('Recovery audit exceeds its bound')
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            _plain_directory(target.parent)
            # Exclusive write: failed/partial receipts remain fail-closed for
            # inspection. Never overwrite or clean up an uncertain receipt.
            with target.open('xb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if not _retired_start(project, path, value):
                raise RuntimeError('Recovery receipt readback failed')
        return dict(ok=True, retired=apply, checked_only=not apply, input_sent=False,
                    lease_sha256=digest, receipt=str(target) if apply else None)


def recovery_cli():
    import argparse
    parser = argparse.ArgumentParser(description='Audit one dead start lease; default is read-only.')
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--lease', required=True)
    parser.add_argument('--lease-sha256', required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--parent-identity', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--preflight', action='store_true', help='Read-only API/lock/registry check before any restart')
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--prepare', action='store_true', help='Seal monotonic uptime and machine identity; does not retire or reboot')
    args = parser.parse_args()
    try:
        result = recover_dead_start(args.project, args.lease, args.lease_sha256,
                                    args.parent_pid, args.parent_identity, apply=args.apply, prepare=args.prepare, preflight=args.preflight)
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError) as exc:
        print(json.dumps(dict(ok=False, retired=False, input_sent=False, error=str(exc)), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(recovery_cli())
