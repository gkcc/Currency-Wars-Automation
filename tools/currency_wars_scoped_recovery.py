"""Operator-only migration of one dead start into the supported control domain.

No historical parentage claim, process termination, input, or marker mutation.
The scope is the repository's standard Python/venv, GUI and shell entrypoints;
renamed interpreters, arbitrary embedding/injection and remote launch services
are not covered. Operator must explicitly accept this cooperative boundary.
"""
import argparse
import contextlib
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import sys

import currency_wars_source_guard as guard

SCHEMA = 'currency-wars-scoped-retirement-v1'
POLICY = 'standard-python-gui-shell-cooperative-v1'
SHELLS = {'powershell.exe', 'pwsh.exe', 'cmd.exe', 'wscript.exe', 'cscript.exe', 'mshta.exe'}
GUI = 'currency-wars-gui.exe'
SCRIPT = 'currency_wars_scoped_recovery.py'
MCP_SHA256 = 'a057b12c930f1f09b9473f8afcf39559e9d9d250cc881bf6ea4c142d8ebd0e28'
BRIDGE = Path('D:/CurrencyWarsInputBridge')
INBOX = Path('D:/Codex/Workspaces/CurrencyWars-InputBridge/inbox')


def candidate(name):
    name = name.casefold()
    return (bool(re.fullmatch(r'(?:pythonw?|pypy\d?w?)(?:\d+(?:\.\d+)*)?(?:_d)?\.exe', name))
            or name in SHELLS | {GUI, 'py.exe', 'pyw.exe'})


def native_process_table():
    """Toolhelp process-only snapshot. No protected-process handle is opened.

    PROCESSENTRY32W provides PID, PPID and basename. Creation identity is then
    queried ONLY for supported candidates, never fabricated for system actors.
    """
    if os.name != 'nt':
        raise RuntimeError('Native Windows process table required')
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD), ('pid', W.DWORD),
                    ('heap', ctypes.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                    ('ppid', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD),
                    ('name', W.WCHAR * 260)]
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    k.CreateToolhelp32Snapshot.restype = W.HANDLE
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [W.HANDLE, ctypes.POINTER(Entry)]
    k.CloseHandle.argtypes = [W.HANDLE]
    handle = k.CreateToolhelp32Snapshot(2, 0)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise RuntimeError('Native process inventory unavailable')
    try:
        entry = Entry(); entry.dwSize = ctypes.sizeof(Entry)
        if not k.Process32FirstW(handle, ctypes.byref(entry)):
            raise RuntimeError('Native process inventory is empty/unknown')
        rows = []
        while True:
            rows.append(dict(pid=int(entry.pid), ppid=int(entry.ppid), name=entry.name))
            if len(rows) > 32768 or not entry.name:
                raise RuntimeError('Native process inventory exceeds supported bounds')
            if not k.Process32NextW(handle, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:
                    raise RuntimeError('Native process inventory is incomplete')
                return rows
    finally:
        k.CloseHandle(handle)


def process_details(row):
    import psutil
    state, identity = guard.process_identity(row['pid'])
    if state != 'active' or identity is None:
        raise RuntimeError('Candidate identity unavailable at PID ' + str(row['pid']))
    try:
        process = psutil.Process(row['pid'])
        exe, argv, ppid = process.exe(), process.cmdline(), process.ppid()
    except (psutil.Error, OSError) as exc:
        raise RuntimeError('Relevant process access unavailable at PID ' + str(row['pid'])) from exc
    if (not exe or not argv or ppid != row['ppid']
            or ntpath.basename(exe).casefold() != row['name'].casefold()
            or guard.process_identity(row['pid']) != (state, identity)):
        raise RuntimeError('Candidate changed/unknown at PID ' + str(row['pid']))
    return dict(row, identity=identity, exe=exe, argv=argv)


def _norm(value):
    return str(value).replace('/', '\\').casefold()


def python_entry(argv):
    """Parse interpreter options until the entry, never inspect later data args."""
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg in ('-X', '-W'):
            index += 2
        elif arg in ('-B', '-I', '-S', '-s', '-E', '-u', '-O', '-OO', '-q'):
            index += 1
        elif arg.startswith(('-X', '-W')) and len(arg) > 2:
            index += 1
        elif not arg.startswith('-') and ntpath.isabs(arg) and arg.casefold().endswith(('.py', '.pyw')):
            return arg, argv[index + 1:]
        else:
            return None, []
    return None, []


def literal_powershell_child(argv, child_commands):
    """Only a single literal native command; no diagnostic-code guessing."""
    lower=[a.casefold() for a in argv]
    if '-command' not in lower:
        return False
    index=lower.index('-command')
    if index != len(argv)-2 or any(a not in ('-noprofile','-noninteractive','-nologo') for a in lower[1:index]):
        return False
    command=argv[-1].strip()
    suffix='; exit $LASTEXITCODE'
    if command.endswith(suffix): command=command[:-len(suffix)].rstrip()
    if command.startswith('& '): command=command[2:].lstrip()
    # Quoted literals may contain spaces. Metacharacters, expansions and escaped
    # code are rejected rather than interpreting a PowerShell program.
    tokens=[]
    pattern=re.compile(r"\s*(?:'([^']*)'|\"([^\"]*)\"|([^\s'\";|&$`]+))")
    pos=0
    while pos<len(command):
        match=pattern.match(command,pos)
        if not match: return False
        token=next(v for v in match.groups() if v is not None)
        if any(c in token for c in '$`\n\r'): return False
        tokens.append(token);pos=match.end()
    return any([_norm(a) for a in tokens]==[_norm(a) for a in child] for child in child_commands)


def classify(rows, details, project, operator_pid, operator_argv):
    """Classify actual entrypoints. Argument strings are never actor identities."""
    table = {r['pid']: r for r in rows}
    if len(table) != len(rows) or operator_pid not in details:
        raise RuntimeError('Operator missing/duplicate process inventory')
    parents, pid = set(), operator_pid
    for unused in range(64):
        pid = table.get(pid, {}).get('ppid', 0)
        if not pid or pid not in table:
            break  # An exited ancestor gives no exception to a live process.
        if pid in parents or pid == operator_pid:
            raise RuntimeError('Operator parent chain is cyclic')
        parents.add(pid)
    else:
        raise RuntimeError('Operator parent chain exceeds bounds')
    for item in details.values():
        parent = details.get(item['ppid'])
        if parent and int(parent['identity'].split(':')[1]) >= int(item['identity'].split(':')[1]):
            raise RuntimeError('Native parent creation does not precede child')
    project_name = _norm(project).rstrip('\\')
    mcp = project_name + '\\tools\\currency_wars_mcp.py'
    result = []
    for pid, item in sorted(details.items()):
        argv = item['argv']; name = item['name'].casefold()
        entry, tail = python_entry(argv) if name not in SHELLS | {GUI} else (None, [])
        same_operator = (len(argv) >= len(operator_argv) + 1
                         and [_norm(a) for a in argv[-len(operator_argv):]] == [_norm(a) for a in operator_argv])
        if pid == operator_pid or (pid in parents and same_operator):
            if not same_operator:
                raise RuntimeError('Operator command identity mismatch')
            role = 'operator_or_venv_parent'
        elif (pid in parents and name in {'powershell.exe','pwsh.exe'}
              and literal_powershell_child(argv,[d['argv'] for d in details.values()
                    if d['ppid']==pid and (d['pid']==operator_pid or d['pid'] in parents)])):
            role = 'literal_operator_shell_parent'
        elif name in SHELLS:
            # Do not call a diagnostic -Command string a controller merely
            # because it contains a filename. Opaque shell programs instead
            # fail with an explicit scope error; use a plain interactive shell.
            if not all(_norm(a) in {'-nologo', '-noprofile', '-noexit', '/d'} for a in argv[1:]):
                raise RuntimeError('Script launcher semantics unknown at PID ' + str(pid))
            role = 'interactive_operator_parent' if pid in parents else 'unrelated_interactive_shell'
        elif name == GUI:
            raise RuntimeError('Supported GUI is active at PID ' + str(pid))
        elif entry is None:
            raise RuntimeError('Python entrypoint scope unknown at PID ' + str(pid))
        elif _norm(entry) == mcp:
            # This reviewed adapter can query, bind a decision, or pause an
            # existing run. It has no start/resume/input entrypoint. Its CLI
            # children, if any, are still inspected independently.
            role = 'reviewed_nonlaunching_mcp'
        elif _norm(entry).startswith(project_name + '\\') or ntpath.basename(entry).startswith('currency_wars_'):
            raise RuntimeError('Supported project actor is active at PID ' + str(pid))
        else:
            role = 'unrelated_direct_script'
        result.append(dict(pid=pid, ppid=item['ppid'], image=item['name'], identity=item['identity'],
                           role=role, command_sha256=hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
                           image_sha256=hashlib.sha256(_norm(item['exe']).encode()).hexdigest()))
    return result


def census(project):
    if hashlib.sha256(guard._source_bytes(project, 'tools/currency_wars_mcp.py')).hexdigest() != MCP_SHA256:
        raise RuntimeError('MCP entrypoint differs from reviewed nonlaunching contract')
    rows = native_process_table()
    details = {r['pid']: process_details(r) for r in rows if candidate(r['name'])}
    return classify(rows, details, project, os.getpid(), sys.argv)


def validate_game(binding, *, exited=False):
    """Read-only native identity and HWND binding."""
    if any(type(binding.get(k)) is not int or binding[k] <= 0 for k in ('pid', 'creation_id', 'hwnd')):
        raise ValueError('Exact native game binding required')
    expected = 'dead' if exited else 'active'
    if guard.identity_state(binding['pid'], 'windows:' + str(binding['creation_id'])) != expected:
        raise RuntimeError('Game process identity changed/unknown')
    if not exited:
        u = ctypes.WinDLL('user32', use_last_error=True)
        u.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
        pid = W.DWORD()
        if not u.GetWindowThreadProcessId(binding['hwnd'], ctypes.byref(pid)) or pid.value != binding['pid']:
            raise RuntimeError('Game HWND identity changed/unknown')


@contextlib.contextmanager
def game_lock(binding, *, exited=False):
    """Original per-game mutex, held across the recovery transaction."""
    validate_game(binding, exited=exited)
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateMutexW.argtypes = [W.LPVOID, W.BOOL, W.LPCWSTR]; k.CreateMutexW.restype = W.HANDLE
    k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    k.ReleaseMutex.argtypes = k.CloseHandle.argtypes = [W.HANDLE]
    name = 'Local\\CurrencyWarsControl-P{pid}-T{creation_id}-H{hwnd}'.format(**binding)
    ctypes.set_last_error(0)
    handle = k.CreateMutexW(None, True, name)
    error = ctypes.get_last_error()
    if not handle:
        raise RuntimeError('Original game control mutex unavailable')
    owned = error != 183
    try:
        if not owned:
            raise RuntimeError('Original game controller mutex already exists')
        yield
        validate_game(binding, exited=exited)
    finally:
        if owned:
            k.ReleaseMutex(handle)
        k.CloseHandle(handle)


def run_evidence(run, owner_path, worker_pid, worker_identity):
    run = guard._plain_directory(Path(run).absolute()).resolve()
    marker_path = run / guard.artifacts.MARKER
    marker = guard.read_object(marker_path)
    owner_path = Path(owner_path).absolute()
    guard._plain_directory(owner_path.parent)
    owner = guard.read_object(owner_path)
    if (marker.get('schema') != 1 or marker.get('tool') != guard.artifacts.TOOL
            or not str(marker.get('purpose', '')).startswith('currency-wars-runner-')
            or marker.get('pid') != worker_pid or marker.get('process_identity') != worker_identity
            or marker.get('children_incomplete') is not False or marker.get('protected_children') != []
            or marker.get('path') != str(run) or marker.get('root') != str(run.parent)
            or owner.get('owner') != 'currency-wars-runner' or owner.get('run_id') != marker.get('run_id')
            or owner.get('runner_pid') != worker_pid
            or 'windows:' + str(owner.get('runner_creation_id')) != worker_identity
            or not re.fullmatch('[0-9a-f]{32}', str(owner.get('launch_id', '')))
            or guard.identity_state(worker_pid, worker_identity) != 'dead'):
        raise RuntimeError('Original run/worker evidence is incomplete or active')
    files = sorted(run.iterdir())
    if len(files) > 256:
        raise RuntimeError('Original run inventory exceeds bound')
    prohibited = {'broker-process.json', 'broker-ready.json', 'broker-exit.json', 'broker-start-error.json',
                  'bridge-dispatch.json', 'bridge-accepted.json', 'request.json', 'result.json'}
    if any(p.name in prohibited for p in files):
        raise RuntimeError('Run reached controller stage; this early-start recovery does not apply')
    hashes = {}
    for path in files:
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400 or not path.is_file():
            raise RuntimeError('Original run contains unknown/linked entry')
        if path.stat().st_size > 2_000_000:
            raise RuntimeError('Original run evidence exceeds bound')
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(run_dir=str(run), owner_path=str(owner_path), owner_sha256=hashlib.sha256(owner_path.read_bytes()).hexdigest(),
                worker_pid=worker_pid, worker_identity=worker_identity, run_id=marker['run_id'],
                launch_id=owner['launch_id'], files=hashes)


def bridge_evidence(project):
    """Original installation/task verification, inspect only, under inbox lock."""
    import currency_wars_input_bridge as bridge
    import currency_wars_broker_entry as entry
    guard._plain_directory(INBOX)
    files = list(INBOX.iterdir()) if INBOX.exists() else []
    if len(files) > 256 or any(p.name == 'launch.json' or p.name.startswith('launch-claimed-') for p in files):
        raise RuntimeError('Bridge has a pending/claimed request or unknown inventory')
    config = bridge.configuration(entry.PINNED)
    if _norm(config.get('inbox')) != _norm(INBOX):
        raise RuntimeError('Installed bridge inbox outside reviewed protocol')
    status = bridge.task_rpc(config, 'inspect')
    if status.get('instances') != []:
        raise RuntimeError('Installed bridge has active/unknown scheduled instances')
    # Do not retain task ACLs, user IDs, or tokens in the public result.
    return dict(installed=True, config_sha256=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
                task_name=config['task_name'], instances=[], broker_sha256=entry.PINNED)


def old_binding_evidence(path):
    path = Path(path).absolute()
    guard._plain_directory(path.parent)
    value = guard.read_object(path)
    binding = {key: value.get(key) for key in ('pid', 'creation_id', 'hwnd')}
    # Existing binding creation_id may be stored as a decimal string.
    if isinstance(binding['creation_id'], str) and binding['creation_id'].isdigit():
        binding['creation_id'] = int(binding['creation_id'])
    if any(type(v) is not int or v <= 0 for v in binding.values()):
        raise ValueError('Original game binding is incomplete')
    if guard.identity_state(binding['pid'], 'windows:' + str(binding['creation_id'])) != 'dead':
        raise RuntimeError('Original game creation is active/unknown')
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), binding=binding)


def receipt_path(project, lease):
    return guard._plain_directory(Path(project) / 'docs/source-activity-recovery') / (lease.stem + '.scoped-v1.json')


def accepted_receipt(project, lease, value):
    path = receipt_path(project, lease)
    if not path.exists():
        return False
    r = guard.read_object(path, limit=2_000_000)
    payload = lease.read_bytes()
    if (r.get('schema') != SCHEMA or r.get('policy') != POLICY or r.get('project') != str(Path(project).resolve())
            or r.get('lease_name') != lease.name or r.get('original_lease') != value
            or r.get('lease_sha256') != hashlib.sha256(payload).hexdigest() or r.get('original_bytes_hex') != payload.hex()
            or r.get('historical_parentage') != 'unknown' or r.get('input_sent') is not False
            or r.get('assessment') != 'retired_under_supported_control_domain'
            or r.get('machine_id') != guard.windows_machine_id()
            or not r.get('sources') or not isinstance(r.get('actors'), list) or not r['actors']
            or not isinstance(r.get('run_evidence'), dict) or not isinstance(r.get('bridge'), dict)
            or r.get('other_activity_reason') is not None):
        raise ValueError('Scoped retirement receipt incomplete or changed')
    guard._dead_start(Path(project).resolve(), lease, r['lease_sha256'], value['pid'], value['process_identity'])
    old_game = r.get('old_game', {})
    if not old_game or old_binding_evidence(old_game['path']) != old_game:
        raise ValueError('Retained old game binding changed')
    evidence = r['run_evidence']
    if run_evidence(evidence['run_dir'], evidence['owner_path'], evidence['worker_pid'], evidence['worker_identity']) != evidence:
        raise ValueError('Retained original run evidence changed')
    return True


def recover(project, lease_name, digest, parent_pid, parent_identity, run, owner_path,
            worker_pid, worker_identity, binding, old_binding_file, *, accept_scope=False, apply=False):
    if not accept_scope:
        raise ValueError('Explicit supported-control-domain acceptance required')
    project = guard._plain_directory(Path(project).absolute()).resolve()
    if not re.fullmatch('[0-9a-f]{32}\\.json', lease_name):
        raise ValueError('One exact original lease required')
    lease = project / 'docs/source-activity' / lease_name
    # Source lock fences registered startup; dispatch lock fences the installed
    # bridge's queue; original game mutex fences direct serve/start entrypoints.
    import currency_wars_input_bridge as bridge
    old_game = old_binding_evidence(old_binding_file)
    with guard.mutation_lock(project), bridge.inbox_lock(INBOX), game_lock(old_game['binding'], exited=True), game_lock(binding):
        value, payload = guard._dead_start(project, lease, digest, parent_pid, parent_identity)
        target = receipt_path(project, lease)
        if target.exists():
            return dict(ok=accepted_receipt(project, lease, value), already_retired=True, input_sent=False)
        reason = guard.activity_reason(project, ignore_lease=lease)
        if reason:
            raise RuntimeError(reason)
        sources = guard.source_hashes(project)
        registry = guard._activity_snapshot(project)
        original = run_evidence(run, owner_path, worker_pid, worker_identity)
        pending = bridge_evidence(project)
        actors = census(project)
        if (census(project) != actors or bridge_evidence(project) != pending
                or run_evidence(run, owner_path, worker_pid, worker_identity) != original
                or old_binding_evidence(old_binding_file) != old_game
                or guard.source_hashes(project) != sources or guard._activity_snapshot(project) != registry
                or guard.activity_reason(project, ignore_lease=lease) is not None
                or guard._dead_start(project, lease, digest, parent_pid, parent_identity)[1] != payload):
            raise RuntimeError('Evidence/actor changed during recovery; nothing retired')
        validate_game(old_game['binding'], exited=True)
        validate_game(binding)
        r = dict(schema=SCHEMA, policy=POLICY, project=str(project), lease_name=lease_name,
                 lease_sha256=digest, original_lease=value, original_bytes_hex=payload.hex(),
                 historical_parentage='unknown', assessment='retired_under_supported_control_domain',
                 machine_id=guard.windows_machine_id(), input_sent=False, sources=sources,
                 actors=actors, run_evidence=original, bridge=pending, registry=registry,
                 game_binding=binding, old_game=old_game, other_activity_reason=None)
        from datetime import datetime, timezone
        r['observed_at'] = datetime.now(timezone.utc).isoformat()
        if apply:
            encoded = json.dumps(r, sort_keys=True, ensure_ascii=False, indent=2).encode()
            if len(encoded) > 2_000_000:
                raise RuntimeError('Audit exceeds bound')
            target.parent.mkdir(parents=True, exist_ok=True)
            guard._plain_directory(target.parent)
            with target.open('xb') as stream:
                stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
            if not accepted_receipt(project, lease, value):
                raise RuntimeError('Receipt readback failed')
        return dict(ok=True, retired=apply, checked_only=not apply, input_sent=False,
                    historical_parentage='unknown', scope=POLICY, receipt=str(target) if apply else None)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'lease', 'lease-sha256', 'parent-identity', 'run-dir', 'owner-file', 'worker-identity', 'old-game-binding-file'):
        p.add_argument('--' + name, required=True)
    for name in ('parent-pid', 'worker-pid', 'game-pid', 'game-creation-id', 'game-hwnd'):
        p.add_argument('--' + name, type=int, required=True)
    p.add_argument('--accept-supported-control-domain', action='store_true')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    try:
        result = recover(a.project, a.lease, a.lease_sha256, a.parent_pid, a.parent_identity,
                         a.run_dir, a.owner_file, a.worker_pid, a.worker_identity,
                         dict(pid=a.game_pid, creation_id=a.game_creation_id, hwnd=a.game_hwnd), a.old_game_binding_file,
                         accept_scope=a.accept_supported_control_domain, apply=a.apply)
    except Exception as exc:
        print(json.dumps(dict(ok=False, retired=False, input_sent=False, error=str(exc)))); return 2
    print(json.dumps(result)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
