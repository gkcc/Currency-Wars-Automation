"""Serialize source updates with registration of GUI/runner lifetimes.

No process enumeration, game-window APIs, input, or historical-run cleanup.
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


def _source_bytes(project, name):
    path = Path(project) / name
    for item in (path, *path.parents):
        info = item.lstat()
        if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Production source path is linked: ' + name)
        if item == Path(project):
            break
    if not path.is_file() or not 0 < path.stat().st_size <= 4_000_000:
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
