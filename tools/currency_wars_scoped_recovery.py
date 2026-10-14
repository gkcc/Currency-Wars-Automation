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

if __name__ == '__main__' and '--stopped-cleaned-run' in sys.argv:
    if sys.argv.count('--project') != 1:
        raise ValueError('One explicit canonical project required')
    sys.path.insert(0, str(Path(sys.argv[sys.argv.index('--project')+1]).absolute() / 'tools'))
import currency_wars_source_guard as guard

SCHEMA = 'currency-wars-scoped-retirement-v1'
STOPPED_SCHEMA = 'currency-wars-stopped-run-retirement-v1'
MAX_ACTIVITY_RECORDS = 512
STOPPED_FILES = ('tools/currency_wars_runner.py', 'tools/currency_wars_source_guard.py', 'tools/currency_wars_scoped_recovery.py')
POLICY = 'standard-python-gui-shell-cooperative-v1'
SHELLS = {'powershell.exe', 'pwsh.exe', 'cmd.exe', 'wscript.exe', 'cscript.exe', 'mshta.exe'}
GUI = 'currency-wars-gui.exe'
SCRIPT = 'currency_wars_scoped_recovery.py'
MCP_SHA256 = '57d8c9dd274783242c1345383428f3fce46bf9300e911d2758b9fc1ee957e25d'
HISTORICAL_MCP_SHA256 = 'a057b12c930f1f09b9473f8afcf39559e9d9d250cc881bf6ea4c142d8ebd0e28'
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


def python_entry(argv, *, module=False):
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
        elif module and arg == '-m' and index + 1 < len(argv):
            name = argv[index + 1]
            if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*', name):
                return name, argv[index + 2:]
            return None, []
        elif not module and not arg.startswith('-') and ntpath.isabs(arg) and arg.casefold().endswith(('.py', '.pyw')):
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
    utf8_prefix = 'try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}\n'
    if command.startswith(utf8_prefix):
        command = command[len(utf8_prefix):].lstrip()
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


def literal_cmd_entry(argv):
    """Parse CMD's supported single literal /C command, not batch semantics."""
    lower = [a.casefold() for a in argv]
    if '/c' not in lower:
        return None
    index = lower.index('/c')
    if index == len(argv) - 1 or any(a not in {'/d', '/e:on', '/v:off', '/s'} for a in lower[1:index]):
        return None
    command = ' '.join(argv[index + 1:]).strip()
    # CMD /S /C often wraps a quoted executable in an additional quote pair.
    if command.startswith('""') and command.endswith('"'):
        command = command[1:-1]
    # A final stderr-to-stdout duplication is a redirect, not command chaining.
    stderr_merge = bool(re.search(r'\s+2>&1$', command))
    if stderr_merge:
        command = command[:-4].rstrip()
    if any(c in command for c in '&|^%!\r\n()'):
        return None
    tokens = []
    pos = 0
    pattern = re.compile(r'\s*(?:"([^"\r\n]*)"|(>>|[<>])|([^\s"<>]+))')
    while pos < len(command):
        match = pattern.match(command, pos)
        if not match:
            return None
        tokens.append(next(v for v in match.groups() if v is not None))
        pos = match.end()
        if (pos < len(command) and not command[pos].isspace()
                and command[pos] not in '<>' and tokens[-1] not in ('<', '>', '>>')):
            return None
    redirected = len(tokens) >= 5 and tokens[-4] == '<' and tokens[-2] in ('>', '>>')
    if redirected:
        if tokens[-2] == '>>' and not stderr_merge:
            return None
        for path in (tokens[-3], tokens[-1]):
            drive, tail = ntpath.splitdrive(path)
            if path.casefold() != 'nul' and (not drive or not tail.startswith(('\\', '/')) or any(c in path for c in '<>')):
                return None
        tokens = tokens[:-4]
    if stderr_merge and not redirected:
        return None
    if any('<' in token or '>' in token for token in tokens):
        return None
    if (not tokens or not ntpath.isabs(tokens[0])
            or not tokens[0].casefold().endswith(('.exe', '.cmd', '.bat'))
            or (redirected and not tokens[0].casefold().endswith('.exe'))):
        return None
    return tokens[0]


def in_control_domain(entry, project):
    path = _norm(ntpath.normpath(str(entry)))
    roots = (project, BRIDGE, INBOX.parent)
    return (any(path == _norm(ntpath.normpath(str(root))).rstrip('\\')
                or path.startswith(_norm(ntpath.normpath(str(root))).rstrip('\\') + '\\') for root in roots)
            or ntpath.basename(path).startswith('currency_wars_'))


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
        module, unused = python_entry(argv, module=True) if name not in SHELLS | {GUI} else (None, [])
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
        elif name == 'cmd.exe' and '/c' in [a.casefold() for a in argv]:
            command_entry = literal_cmd_entry(argv)
            if command_entry is None:
                raise RuntimeError('CMD entrypoint scope unknown at PID ' + str(pid))
            if in_control_domain(command_entry, project):
                raise RuntimeError('Supported CMD control-domain actor is active at PID ' + str(pid))
            role = 'unrelated_direct_script'
        elif name in SHELLS:
            # Do not call a diagnostic -Command string a controller merely
            # because it contains a filename. Opaque shell programs instead
            # fail with an explicit scope error; use a plain interactive shell.
            if not all(_norm(a) in {'-nologo', '-noprofile', '-noexit', '/d'} for a in argv[1:]):
                raise RuntimeError('Script launcher semantics unknown at PID ' + str(pid))
            role = 'interactive_operator_parent' if pid in parents else 'unrelated_interactive_shell'
        elif name == GUI:
            raise RuntimeError('Supported GUI is active at PID ' + str(pid))
        elif module is not None:
            # Same cooperative boundary as external direct .py entrypoints.
            # This records the declared module, not a claim about arbitrary
            # external code's future behavior; children are checked separately.
            parts = module.casefold().split('.')
            if (in_control_domain(item['exe'], project)
                    or any(part.startswith('currency_wars') or 'bridge' in part for part in parts)):
                raise RuntimeError('Supported module control-domain actor is active at PID ' + str(pid))
            role = 'unrelated_direct_module'
        elif entry is None:
            raise RuntimeError('Python entrypoint scope unknown at PID ' + str(pid))
        elif _norm(entry) == mcp:
            # This reviewed adapter can query, bind a decision, or pause an
            # existing run. It has no start/resume/input entrypoint. Its CLI
            # children, if any, are still inspected independently.
            role = 'reviewed_nonlaunching_mcp'
        elif in_control_domain(entry, project):
            raise RuntimeError('Supported project actor is active at PID ' + str(pid))
        else:
            role = 'unrelated_direct_script'
        result.append(dict(pid=pid, ppid=item['ppid'], image=item['name'], identity=item['identity'],
                           role=role, command_sha256=hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
                           image_sha256=hashlib.sha256(_norm(item['exe']).encode()).hexdigest()))
        if role == 'unrelated_direct_module':
            result[-1]['module'] = module
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


def manual_intent_hashes(directory):
    """Existing latch_manual data, bound by the enclosing verified run."""
    from datetime import datetime
    guard._plain_directory(directory)
    entries = sorted(directory.iterdir())
    if len(entries) > 2000:
        raise ValueError('Manual intent count exceeds original protocol bound')
    hashes = {}
    for path in entries:
        if (not re.fullmatch('[0-9a-f]{32}\\.json', path.name) or not path.is_file()
                or path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Unknown/linked manual intent entry')
        value = guard.read_object(path, limit=4096)
        if (set(value) != {'manual_id', 'reason', 'time', 'priority_ns'}
                or value['manual_id'] != path.stem
                or not isinstance(value['reason'], str) or not 1 <= len(value['reason']) <= 200
                or type(value['priority_ns']) is not int or value['priority_ns'] <= 0
                or not isinstance(value['time'], str) or not 1 <= len(value['time']) <= 64):
            raise ValueError('Manual intent does not match latch_manual schema')
        # latch_manual uses now(), which emits a timezone-aware ISO timestamp.
        if datetime.fromisoformat(value['time']).utcoffset() is None:
            raise ValueError('Manual intent timestamp lacks timezone')
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


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
    manual = None
    for path in files:
        if path.name == 'manual-intents':
            manual = manual_intent_hashes(path)
            continue
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400 or not path.is_file():
            raise RuntimeError('Original run contains unknown/linked entry')
        if path.stat().st_size > 2_000_000:
            raise RuntimeError('Original run evidence exceeds bound')
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(run_dir=str(run), owner_path=str(owner_path), owner_sha256=hashlib.sha256(owner_path.read_bytes()).hexdigest(),
                worker_pid=worker_pid, worker_identity=worker_identity, run_id=marker['run_id'],
                launch_id=owner['launch_id'], files=hashes, manual_intents=manual)


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


def operator_provenance():
    """Actual loaded recovery providers, separate from canonical deployment."""
    import psutil
    import currency_wars_broker_entry
    modules = {'operator': Path(__file__).absolute()}
    for name, module in list(sys.modules.items()):
        if name.startswith('currency_wars_') and getattr(module, '__file__', None):
            modules[name] = Path(module.__file__).absolute()
    providers = {'psutil': dict(version=psutil.__version__, path=str(Path(psutil.__file__).absolute()))}
    modules['psutil'] = Path(psutil.__file__).absolute()
    if len(modules) > 128:
        raise ValueError('Loaded operator module inventory exceeds bound')
    facts = {}
    for name, path in sorted(modules.items()):
        guard._plain_directory(path.parent)
        data = guard._source_bytes(path.parent, path.name)
        facts[name] = dict(path=str(path), sha256=hashlib.sha256(data).hexdigest())
    return dict(modules=facts, providers=providers)


def receipt_path(project, lease):
    return guard._plain_directory(Path(project) / 'docs/source-activity-recovery') / (lease.stem + '.scoped-v1.json')


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-fA-F]{64}', value) is not None


def proof_digest(receipt):
    """Corruption binding only, not a signature or an authentication claim."""
    body = {k: v for k, v in receipt.items() if k != 'proof_sha256'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def validate_proof(r):
    """Validate historical facts without comparing them to upgraded sources."""
    def require(ok):
        if not ok:
            raise ValueError('Scoped retirement proof is incomplete or contradictory')
    require(isinstance(r, dict))
    require(_sha(r.get('proof_sha256')) and r['proof_sha256'] == proof_digest(r))
    sources = r.get('sources')
    require(isinstance(sources, dict) and 1 <= len(sources) <= 128)
    for name, digest in sources.items():
        guard._relative(name)
        require(_sha(digest))
    required = {guard.SOURCE_MANIFEST, 'tools/currency_wars_control.py',
                'tools/currency_wars_mcp.py',
                'tools/currency_wars_source_guard.py', 'tools/currency_wars_broker_entry.py',
                'tools/currency_wars_input_bridge.py'}
    require(required <= sources.keys())
    allowed_mcp = {MCP_SHA256} if r.get('schema') == STOPPED_SCHEMA else {HISTORICAL_MCP_SHA256, MCP_SHA256}
    require(sources['tools/currency_wars_mcp.py'].lower() in allowed_mcp)
    provenance = r.get('operator_provenance')
    require(isinstance(provenance, dict) and set(provenance) == {'modules', 'providers'})
    modules = provenance['modules']
    require(isinstance(modules, dict) and 1 <= len(modules) <= 128)
    require({'operator', 'currency_wars_source_guard', 'currency_wars_artifacts',
             'currency_wars_input_bridge', 'currency_wars_broker_entry', 'psutil'} <= modules.keys())
    for name, item in modules.items():
        require(isinstance(name, str) and (name in {'operator', 'psutil'}
                or re.fullmatch('currency_wars_[a-z0-9_]+', name) is not None))
        require(isinstance(item, dict) and set(item) == {'path', 'sha256'}
                and isinstance(item['path'], str)
                and (Path(item['path']).is_absolute() or ntpath.isabs(item['path']))
                and _sha(item['sha256']))
    providers = provenance['providers']
    require(isinstance(providers, dict) and set(providers) == {'psutil'})
    provider = providers['psutil']
    require(isinstance(provider, dict) and set(provider) == {'version', 'path'}
            and isinstance(provider['version'], str) and 1 <= len(provider['version']) <= 64
            and provider['path'] == modules['psutil']['path'])

    bridge = r.get('bridge')
    require(isinstance(bridge, dict) and bridge.get('installed') is True
            and bridge.get('instances') == [] and _sha(bridge.get('config_sha256'))
            and _sha(bridge.get('broker_sha256'))
            and isinstance(bridge.get('task_name'), str)
            and re.fullmatch('CurrencyWarsInputBridge-[0-9a-f]{16}', bridge['task_name']) is not None)
    require(bridge['broker_sha256'].upper() == sources['tools/currency_wars_control.py'].upper())
    registry = r.get('registry')
    require(isinstance(registry, dict) and set(registry) == {'leases', 'current_runner_sha256'})
    leases = registry['leases']
    require(isinstance(leases, dict) and 1 <= len(leases) <= MAX_ACTIVITY_RECORDS)
    for name, digest in leases.items():
        require(isinstance(name, str) and re.fullmatch('[0-9a-f]{32}\\.json', name) is not None and _sha(digest))
    require(leases.get(r.get('lease_name')) == r.get('lease_sha256'))
    require(registry['current_runner_sha256'] is None or _sha(registry['current_runner_sha256']))
    stopped = r.get('schema') == STOPPED_SCHEMA
    old_game = r.get('old_game')
    if not stopped:
        require(isinstance(old_game, dict) and isinstance(old_game.get('path'), str)
                and bool(old_game['path']) and _sha(old_game.get('sha256')))
    for binding in ((r.get('game_binding'),) if stopped else (r.get('game_binding'), old_game.get('binding'))):
        require(isinstance(binding, dict) and set(binding) == {'pid', 'creation_id', 'hwnd'}
                and all(type(v) is int and v > 0 for v in binding.values()))
    if not stopped:
        require(r['game_binding'] != old_game['binding'])
    actors = r.get('actors')
    require(isinstance(actors, list) and 1 <= len(actors) <= 32768)
    roles = {'operator_or_venv_parent', 'literal_operator_shell_parent',
             'interactive_operator_parent', 'unrelated_interactive_shell',
             'reviewed_nonlaunching_mcp', 'unrelated_direct_script', 'unrelated_direct_module'}
    indexed = {}
    for actor in actors:
        require(isinstance(actor, dict))
        module_actor = actor.get('role') == 'unrelated_direct_module'
        keys = {'pid', 'ppid', 'image', 'identity', 'role', 'command_sha256', 'image_sha256'}
        require(set(actor) == (keys | {'module'} if module_actor else keys))
        if module_actor:
            require(isinstance(actor['module'], str) and re.fullmatch(
                r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*', actor['module']) is not None)
            require(not any(part.startswith('currency_wars') or 'bridge' in part
                            for part in actor['module'].casefold().split('.')))
        require(type(actor['pid']) is int and actor['pid'] > 0 and actor['pid'] not in indexed
                and type(actor['ppid']) is int and actor['ppid'] >= 0 and actor['ppid'] != actor['pid']
                and isinstance(actor['identity'], str) and re.fullmatch('windows:[1-9][0-9]*', actor['identity']) is not None
                and isinstance(actor['image'], str) and candidate(actor['image'])
                and actor['role'] in roles and _sha(actor['command_sha256']) and _sha(actor['image_sha256']))
        shell = actor['image'].casefold() in SHELLS
        shell_role = actor['role'] in {'literal_operator_shell_parent', 'interactive_operator_parent',
                                       'unrelated_interactive_shell'}
        require(shell_role == shell or (actor['image'].casefold() == 'cmd.exe'
                                       and actor['role'] == 'unrelated_direct_script'))
        require(actor['image'].casefold() != GUI)
        indexed[actor['pid']] = actor
    require(any(a['role'] == 'operator_or_venv_parent' for a in actors))
    original = r.get('original_lease')
    evidence = r.get('run_evidence')
    require(isinstance(original, dict) and isinstance(evidence, dict))
    known_dead = [(original.get('pid'), original.get('process_identity')),
                  (evidence.get('worker_pid'), evidence.get('worker_identity'))]
    require(isinstance(original.get('children'), list))
    for child in original['children']:
        require(isinstance(child, dict))
        known_dead.append((child.get('pid'), child.get('process_identity')))
    if stopped:
        known_dead.append((evidence.get('broker_pid'), evidence.get('broker_identity')))
    require(not any((a['pid'], a['identity']) in known_dead for a in actors))
    for actor in actors:
        parent = indexed.get(actor['ppid'])
        if parent:
            require(int(parent['identity'].split(':')[1]) < int(actor['identity'].split(':')[1]))
    from datetime import datetime
    require(isinstance(r.get('observed_at'), str))
    require(datetime.fromisoformat(r['observed_at']).utcoffset() is not None)


def stopped_receipt_path(project, lease):
    return receipt_path(project, lease).with_name(lease.stem + '.stopped-v1.json')


def stopped_run_evidence(project, lease, value, terminal_file, owner_file, run, worker_pid, worker_identity, *, require_current=False):
    terminal_file, owner_file, run = map(lambda p: Path(p).absolute(), (terminal_file, owner_file, run))
    for path in (terminal_file, owner_file):
        guard._plain_directory(path.parent)
    terminal = guard.read_object(terminal_file, limit=2_000_000)
    owner = guard.read_object(owner_file)
    if (owner_file.name != 'owner.json' or terminal_file.parent != owner_file.parent
            or owner.get('owner') != 'currency-wars-runner' or terminal.get('owner') != owner['owner']
            or not re.fullmatch('[0-9a-f]{32}', str(owner.get('run_id', '')))
            or owner_file.parent != Path(project) / 'debug' / ('runner-' + str(owner.get('chat_id', ''))[:8] + '-' + owner['run_id'][:12])
            or any(terminal.get(k) != owner.get(k) for k in ('run_id', 'chat_id', 'launch_id', 'runner_pid', 'runner_creation_id'))
            or value.get('chat_id') != owner.get('chat_id') or value.get('launch_id') != owner.get('launch_id')
            or owner.get('runner_pid') != worker_pid or 'windows:' + str(owner.get('runner_creation_id')) != worker_identity
            or terminal.get('control_mode') != 'stopped' or terminal.get('run_dir') != str(run)):
        raise ValueError('Stopped terminal/owner/exact start launch does not match')
    registry = list((Path(project) / 'docs/source-activity').iterdir())
    if len(registry) > MAX_ACTIVITY_RECORDS:
        raise ValueError('Activity inventory exceeds its bound')
    matching = []
    for path in registry:
        item = guard.read_object(path)
        if item.get('kind') == 'start' and item.get('chat_id') == owner['chat_id'] and item.get('launch_id') == owner['launch_id']:
            matching.append(path)
    if matching != [lease]:
        raise ValueError('Stopped owner launch does not identify one exact original start')
    cleanup, exits = terminal.get('cleanup', {}), terminal.get('exit_evidence', {})
    worker, broker = exits.get('worker', {}), exits.get('broker', {})
    broker_id = terminal.get('broker_identity', {})
    broker_pid, broker_identity = broker_id.get('pid'), 'windows:' + str(broker_id.get('creation_id'))
    for row, pid, identity in ((worker, worker_pid, worker_identity), (broker, broker_pid, broker_identity)):
        if (type(pid) is not int or pid <= 0 or not re.fullmatch('windows:[1-9][0-9]*', identity)
                or row.get('pid') != pid or 'windows:' + str(row.get('expected_creation_id')) != identity
                or row.get('state') not in ('exited', 'absent')
                or row.get('state') == 'exited' and (type(row.get('exit_code')) is not int or row['exit_code'] != 0)
                or guard.identity_state(pid, identity) != 'dead'):
            raise ValueError('Original worker/broker exit is incomplete, active or unknown')
    if (cleanup.get('directory') != str(run) or cleanup.get('removed') is not True
            or cleanup.get('pending_finally') is not False
            or run.parent != Path(owner.get('runtime_location', {}).get('runtime_root', '')).absolute()
            or not re.fullmatch('currency-wars-runner-' + re.escape(owner['chat_id'][:8]) + '-[A-Za-z0-9_-]{6,32}', run.name)):
        raise ValueError('Original owned cleanup path is incomplete')
    guard._plain_directory(run.parent)
    try:
        run.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError('Original owned runtime still exists')
    if require_current and (Path(project) / 'docs/CURRENT_RUNNER.json').read_bytes() != terminal_file.read_bytes():
        raise ValueError('CURRENT no longer matches this original stopped terminal')
    return dict(kind='stopped_cleaned', terminal_file=str(terminal_file),
                terminal_sha256=hashlib.sha256(terminal_file.read_bytes()).hexdigest(),
                owner_file=str(owner_file), owner_sha256=hashlib.sha256(owner_file.read_bytes()).hexdigest(),
                run_dir=str(run), run_id=owner['run_id'], chat_id=owner['chat_id'], launch_id=owner['launch_id'],
                worker_pid=worker_pid, worker_identity=worker_identity, broker_pid=broker_pid, broker_identity=broker_identity)


def accepted_stopped_receipt(project, lease, value):
    r = guard.read_object(stopped_receipt_path(project, lease), limit=2_000_000)
    validate_proof(r)
    payload = lease.read_bytes()
    deployment = r.get('deployment', {})
    candidates, after = deployment.get('candidate_sources', {}), deployment.get('after_sources', {})
    if (r.get('schema') != STOPPED_SCHEMA or r.get('policy') != POLICY or r.get('project') != str(Path(project).resolve())
            or r.get('lease_name') != lease.name or r.get('original_lease') != value
            or r.get('lease_sha256') != hashlib.sha256(payload).hexdigest() or r.get('original_bytes_hex') != payload.hex()
            or r.get('historical_parentage') != 'unknown' or r.get('input_sent') is not False
            or r.get('assessment') != 'retired_after_original_stop_cleanup'
            or r.get('machine_id') != guard.windows_machine_id() or r.get('other_activity_reason') is not None
            or set(candidates) != set(STOPPED_FILES) or any(not _sha(v) for v in candidates.values())
            or after != {**r['sources'], **candidates}
            or r['operator_provenance']['modules']['operator']['sha256'].upper() != candidates['tools/currency_wars_scoped_recovery.py'].upper()):
        raise ValueError('Stopped retirement proof/approved deployment incomplete or changed')
    guard._dead_start(Path(project).resolve(), lease, r['lease_sha256'], value['pid'], value['process_identity'])
    e = r['run_evidence']
    if stopped_run_evidence(project, lease, value, e['terminal_file'], e['owner_file'], e['run_dir'],
                            e['worker_pid'], e['worker_identity']) != e:
        raise ValueError('Immutable original stopped run evidence changed')
    # CURRENT and every other lease remain the outer activity_reason's domain.
    return True


def _replace_source(project, name, payload):
    import uuid
    path = Path(project) / name
    guard._plain_directory(path.parent)
    temporary = path.with_name(path.name + '.stopped-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def recover_stopped(project, lease_name, digest, parent_pid, parent_identity, run, owner_file,
                    worker_pid, worker_identity, binding, terminal_file, expected_sources_file,
                    candidate_dir, candidate_hashes_file, *, accept_scope=False, apply=False):
    """Exact stopped-run bootstrap: old mutex/guard, approved 3 sources, exclusive proof."""
    if not accept_scope:
        raise ValueError('Explicit supported-control-domain acceptance required')
    project = guard._plain_directory(Path(project).absolute()).resolve()
    candidate_dir = guard._plain_directory(Path(candidate_dir).absolute()).resolve()
    if candidate_dir == project or project in candidate_dir.parents:
        raise ValueError('Frozen operator candidate must be outside canonical project')
    if not re.fullmatch(r'[0-9a-f]{32}\.json', lease_name):
        raise ValueError('One exact original start required')
    expected = guard.read_object(Path(expected_sources_file).absolute(), limit=300_000)
    approved = guard.read_object(Path(candidate_hashes_file).absolute(), limit=300_000)
    if set(approved) != set(STOPPED_FILES) or any(not _sha(v) for v in (*expected.values(), *approved.values())):
        raise ValueError('Exact canonical-before and approved three-source hash maps required')
    expected = {k: v.upper() for k, v in expected.items()}
    approved = {k: v.upper() for k, v in approved.items()}
    if (Path(__file__).absolute() != candidate_dir / 'tools/currency_wars_scoped_recovery.py'
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest().upper() != approved['tools/currency_wars_scoped_recovery.py']):
        raise ValueError('Executing operator is not the approved frozen candidate')
    new = {name: guard._source_bytes(candidate_dir, name) for name in STOPPED_FILES}
    if any(hashlib.sha256(new[name]).hexdigest().upper() != approved[name] for name in STOPPED_FILES):
        raise ValueError('Candidate source changed after approval')
    import currency_wars_input_bridge as bridge
    lease = project / 'docs/source-activity' / lease_name
    target = stopped_receipt_path(project, lease)
    with guard.mutation_lock(project), bridge.inbox_lock(INBOX), game_lock(binding):
        value, payload = guard._dead_start(project, lease, digest, parent_pid, parent_identity)
        if target.exists():
            raise ValueError('Stopped retirement already has an exclusive record; read original result')
        if guard.activity_reason(project, ignore_lease=lease) is not None:
            raise RuntimeError('Another activity/current actor is active or unknown')
        sources = guard.source_hashes(project)
        if sources != expected:
            raise ValueError('Canonical old sources differ from approved bootstrap map')
        registry = guard._activity_snapshot(project)
        evidence = stopped_run_evidence(project, lease, value, terminal_file, owner_file, run,
                                        worker_pid, worker_identity, require_current=True)
        pending, actors, provenance = bridge_evidence(project), census(project), operator_provenance()
        if (census(project) != actors or bridge_evidence(project) != pending or operator_provenance() != provenance
                or guard.source_hashes(project) != sources or guard._activity_snapshot(project) != registry
                or guard.activity_reason(project, ignore_lease=lease) is not None
                or stopped_run_evidence(project, lease, value, terminal_file, owner_file, run,
                    worker_pid, worker_identity, require_current=True) != evidence
                or guard._dead_start(project, lease, digest, parent_pid, parent_identity)[1] != payload):
            raise RuntimeError('Stopped bootstrap evidence changed; nothing deployed or retired')
        validate_game(binding)
        r = dict(schema=STOPPED_SCHEMA, policy=POLICY, project=str(project), lease_name=lease_name,
                 lease_sha256=digest, original_lease=value, original_bytes_hex=payload.hex(), historical_parentage='unknown',
                 assessment='retired_after_original_stop_cleanup', machine_id=guard.windows_machine_id(), input_sent=False,
                 sources=sources, registry=registry, actors=actors, run_evidence=evidence, bridge=pending,
                 operator_provenance=provenance, game_binding=binding, other_activity_reason=None,
                 deployment=dict(candidate_sources=approved, after_sources={**sources, **approved}))
        from datetime import datetime, timezone
        r['observed_at'] = datetime.now(timezone.utc).isoformat();r['proof_sha256'] = proof_digest(r)
        validate_proof(r)
        if not apply:
            return dict(ok=True, checked_only=True, retired=False, deployed=False, input_sent=False,
                        before_sources=sources, candidate_sources=approved, evidence=evidence)
        originals = {name: guard._source_bytes(project, name) for name in STOPPED_FILES}
        replaced = []
        try:
            for name in STOPPED_FILES:
                _replace_source(project, name, new[name]); replaced.append(name)
            if guard.source_hashes(project) != r['deployment']['after_sources']:
                raise RuntimeError('Deployed source hash readback failed')
            # Once created this proof is retained, including a failed partial write.
            target.parent.mkdir(parents=True, exist_ok=True);guard._plain_directory(target.parent)
            encoded = json.dumps(r, sort_keys=True, ensure_ascii=False, indent=2).encode()
            if len(encoded) > 2_000_000:
                raise RuntimeError('Stopped bootstrap proof exceeds its bound')
            with target.open('xb') as stream:
                stream.write(encoded);stream.flush();os.fsync(stream.fileno())
            if not accepted_stopped_receipt(project, lease, value):
                raise RuntimeError('Stopped proof readback failed')
        except Exception as error:
            failures = []
            for name in reversed(replaced):
                try:
                    if hashlib.sha256(guard._source_bytes(project, name)).hexdigest().upper() != approved[name]:
                        raise RuntimeError('Source changed; rollback must not overwrite it')
                    _replace_source(project, name, originals[name])
                except Exception as rollback:
                    failures.append(name + ': ' + str(rollback))
            raise RuntimeError('Stopped bootstrap failed; original marker retained; rollback=' +
                               ('verified' if not failures and guard.source_hashes(project) == sources else repr(failures)) + '; ' + str(error)) from error
        return dict(ok=True, retired=True, deployed=True, input_sent=False, receipt=str(target),
                    before_sources=sources, after_sources=r['deployment']['after_sources'])


def accepted_receipt(project, lease, value):
    if stopped_receipt_path(project, lease).exists():
        return accepted_stopped_receipt(project, lease, value)
    path = receipt_path(project, lease)
    if not path.exists():
        return False
    r = guard.read_object(path, limit=2_000_000)
    validate_proof(r)
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
        provenance = operator_provenance()
        if (census(project) != actors or bridge_evidence(project) != pending
                or run_evidence(run, owner_path, worker_pid, worker_identity) != original
                or old_binding_evidence(old_binding_file) != old_game
                or operator_provenance() != provenance
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
                 operator_provenance=provenance,
                 game_binding=binding, old_game=old_game, other_activity_reason=None)
        from datetime import datetime, timezone
        r['observed_at'] = datetime.now(timezone.utc).isoformat()
        r['proof_sha256'] = proof_digest(r)
        validate_proof(r)
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
    for name in ('project', 'lease', 'lease-sha256', 'parent-identity', 'run-dir', 'owner-file', 'worker-identity'):
        p.add_argument('--' + name, required=True)
    for name in ('parent-pid', 'worker-pid', 'game-pid', 'game-creation-id', 'game-hwnd'):
        p.add_argument('--' + name, type=int, required=True)
    p.add_argument('--old-game-binding-file')
    p.add_argument('--stopped-cleaned-run', action='store_true')
    for name in ('terminal-file', 'expected-sources-file', 'candidate-dir', 'candidate-hashes-file'):
        p.add_argument('--' + name)
    p.add_argument('--accept-supported-control-domain', action='store_true')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    try:
        if a.stopped_cleaned_run:
            if not all((a.terminal_file, a.expected_sources_file, a.candidate_dir, a.candidate_hashes_file)):
                raise ValueError('Stopped bootstrap requires terminal and both approved source maps')
            result = recover_stopped(a.project, a.lease, a.lease_sha256, a.parent_pid, a.parent_identity,
                a.run_dir, a.owner_file, a.worker_pid, a.worker_identity,
                dict(pid=a.game_pid, creation_id=a.game_creation_id, hwnd=a.game_hwnd),
                a.terminal_file, a.expected_sources_file, a.candidate_dir, a.candidate_hashes_file,
                accept_scope=a.accept_supported_control_domain, apply=a.apply)
            print(json.dumps(result));return 0
        if not a.old_game_binding_file:
            raise ValueError('Early-start recovery requires the original old-game binding')
        result = recover(a.project, a.lease, a.lease_sha256, a.parent_pid, a.parent_identity,
                         a.run_dir, a.owner_file, a.worker_pid, a.worker_identity,
                         dict(pid=a.game_pid, creation_id=a.game_creation_id, hwnd=a.game_hwnd), a.old_game_binding_file,
                         accept_scope=a.accept_supported_control_domain, apply=a.apply)
    except Exception as exc:
        print(json.dumps(dict(ok=False, retired=False, input_sent=False, error=str(exc)))); return 2
    print(json.dumps(result)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
