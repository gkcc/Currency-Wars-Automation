"""Low privilege client for the explicitly installed, fixed Windows input task.

This module never requests elevation. The installer grants only execution of a
protected fixed broker; mutable strategy and GUI code remain ordinary programs.
"""
from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes as W
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import currency_wars_artifacts as artifacts

INSTALL_ROOT = Path('D:/CurrencyWarsInputBridge')
PROTOCOL = 1
REQUEST_LIFETIME = 30


class BridgeError(RuntimeError):
    pass


def read_object(path, limit=32768):
    path = Path(path)
    if path.stat().st_size > limit:
        raise BridgeError('输入权限组件记录超过大小限制')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise BridgeError('输入权限组件记录格式无效')
    return value


def write_object(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def task_name(sid):
    if not re.fullmatch(r'S-1-5-21-[0-9-]+', sid):
        raise BridgeError('输入权限组件只支持当前交互用户')
    return 'CurrencyWarsInputBridge-' + hashlib.sha256(sid.encode('ascii')).hexdigest()[:16]


def powershell_executable():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    buffer = ctypes.create_unicode_buffer(32768)
    if not kernel.GetSystemDirectoryW(buffer, len(buffer)):
        raise BridgeError('无法定位Windows系统PowerShell')
    return str(Path(buffer.value) / 'WindowsPowerShell/v1.0/powershell.exe')


def task_rpc(config, operation, instance_id=None):
    """Only inspect/run the fixed installed task; no shell or user commands."""
    name = config['task_name']
    if name != task_name(config['user_sid']) or operation not in ('inspect', 'run'):
        raise BridgeError('固定输入任务请求不匹配')
    expected_exe = str(INSTALL_ROOT / 'python/python.exe')
    expected_args = subprocess.list2cmdline(['-I','-S','-B','-X','utf8',str(INSTALL_ROOT/'code/currency_wars_bridge_task.py')])
    script = """$ErrorActionPreference='Stop'
$env:PSModulePath=$PSHOME+'\\Modules'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$service=New-Object -ComObject 'Schedule.Service'
$service.Connect()
$task=$service.GetFolder('\\').GetTask('%s')
$definition=$task.Definition
$taskUser=$definition.Principal.UserId
if ($taskUser -notmatch '^S-1-') { $taskUser=([System.Security.Principal.NTAccount]::new($taskUser)).Translate([System.Security.Principal.SecurityIdentifier]).Value }
$actions=$definition.Actions
if ($actions.Count -ne 1) { throw 'Unexpected task action count' }
$action=$actions.Item(1)
if ($taskUser -ne '%s' -or [int]$definition.Principal.LogonType -ne 3 -or [int]$definition.Principal.RunLevel -ne 1 -or [int]$definition.Settings.MultipleInstances -ne 2 -or $action.Path -ne '%s' -or $action.Arguments -cne '%s' -or $action.WorkingDirectory -ne '%s' -or -not $task.Enabled) { throw 'Fixed task definition changed before invocation' }
$security=$task.GetSecurityDescriptor(5)
$descriptor=[System.Security.AccessControl.RawSecurityDescriptor]::new($security)
if ($descriptor.Owner.Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Task owner can modify the privileged action' }
if (($descriptor.ControlFlags -band [System.Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -eq 0) { throw 'Task access is not protected' }
$userAccess=$false
foreach ($ace in $descriptor.DiscretionaryAcl) {
    if ($ace.AceQualifier -ne [System.Security.AccessControl.AceQualifier]::AccessAllowed) { throw 'Unexpected task access rule' }
    $sid=$ace.SecurityIdentifier.Value
    if ($sid -in @('S-1-5-18','S-1-5-32-544')) { continue }
    if ($sid -ne '%s' -or $ace.AceFlags -ne [System.Security.AccessControl.AceFlags]::None -or $ace.AccessMask -notin @(-1610612736,0x1200a9)) { throw 'Task allows unexpected callers or writes' }
    $userAccess=$true
}
if (-not $userAccess) { throw 'Task does not grant this user read/execute access' }
$instances=@($task.GetInstances(0) | ForEach-Object { @{id=$_.InstanceGuid;state=[int]$_.State} })
$value=@{name=$task.Name;path=$task.Path;enabled=$task.Enabled;user=$taskUser;logon_type=[int]$definition.Principal.LogonType;run_level=[int]$definition.Principal.RunLevel;multiple_instances=[int]$definition.Settings.MultipleInstances;executable=$action.Path;arguments=$action.Arguments;working_directory=$action.WorkingDirectory;security=$security;task_owner=$descriptor.Owner.Value;instances=$instances;last_result=[int64]$task.LastTaskResult}
%s
$value | ConvertTo-Json -Depth 8 -Compress
""" % (name, config['user_sid'], expected_exe, expected_args, str(INSTALL_ROOT), config['user_sid'],
           "$value['instance_id']=$task.Run($null).InstanceGuid" if operation == 'run' else '')
    result = subprocess.run([powershell_executable(), '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, timeout=10,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise BridgeError('固定输入组件状态核验或调用失败；已停止启动，请检查组件安装记录。')
    try:
        value = json.loads(result.stdout.decode('utf-8-sig'))
    except (UnicodeError, ValueError) as error:
        raise BridgeError('固定输入任务没有返回有效记录') from error
    validate_task(config, value)
    return value


def validate_task(config, value):
    root = INSTALL_ROOT
    expected_exe = str(root / 'python/python.exe')
    expected_args = subprocess.list2cmdline(['-I', '-S', '-B', '-X', 'utf8', str(root / 'code/currency_wars_bridge_task.py')])
    normalized = lambda p: os.path.normcase(os.path.abspath(p))
    if (not isinstance(value, dict) or value.get('name') != config['task_name']
            or value.get('user') != config['user_sid'] or value.get('enabled') is not True
            or value.get('logon_type') != 3 or value.get('run_level') != 1
            or value.get('multiple_instances') != 2
            or value.get('task_owner') not in ('S-1-5-18', 'S-1-5-32-544')
            or normalized(value.get('executable', '')) != normalized(expected_exe)
            or value.get('arguments') != expected_args
            or normalized(value.get('working_directory', '')) != normalized(root)):
        raise BridgeError('固定输入任务定义已变；拒绝启动或代为修复权限')
    # Ordinary clients may read/execute, never update the highest-privilege task.
    security = str(value.get('security', ''))
    if f';;;{config["user_sid"]})' not in security or not re.search(r'D:P', security):
        raise BridgeError('固定输入任务访问权限未保护')
    for ace in re.findall(r'\(([^)]+)\)', security):
        parts = ace.split(';')
        if len(parts) != 6:
            raise BridgeError('固定输入任务访问规则格式无效')
        if parts[5] in ('SY', 'BA', 'S-1-5-18', 'S-1-5-32-544'):
            continue
        if parts[0] != 'A' or parts[1] or parts[3] or parts[4] or parts[5] != config['user_sid'] or parts[2] not in ('GRGX', 'GXGR', '0x1200a9'):
            raise BridgeError('固定输入任务允许了非预期的写入/调用者')
    return value


class InstallationAccess:
    """Native effective access checks using only this ordinary user's token."""
    class Mapping(ctypes.Structure):
        _fields_ = [('read',W.DWORD),('write',W.DWORD),('execute',W.DWORD),('all',W.DWORD)]

    def __init__(self):
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.a=ctypes.WinDLL('advapi32',use_last_error=True)
        self.k.GetCurrentProcess.restype=W.HANDLE
        self.k.CloseHandle.argtypes=[W.HANDLE]
        self.k.LocalFree.argtypes=[W.LPVOID]
        self.a.OpenProcessToken.argtypes=[W.HANDLE,W.DWORD,ctypes.POINTER(W.HANDLE)]
        self.a.DuplicateToken.argtypes=[W.HANDLE,ctypes.c_int,ctypes.POINTER(W.HANDLE)]
        self.a.GetNamedSecurityInfoW.argtypes=[W.LPWSTR,ctypes.c_int,W.DWORD,
                       ctypes.POINTER(W.LPVOID),W.LPVOID,W.LPVOID,W.LPVOID,ctypes.POINTER(W.LPVOID)]
        self.a.GetNamedSecurityInfoW.restype=W.DWORD
        self.a.ConvertSidToStringSidW.argtypes=[W.LPVOID,ctypes.POINTER(W.LPWSTR)]
        self.a.AccessCheck.argtypes=[W.LPVOID,W.HANDLE,W.DWORD,ctypes.POINTER(self.Mapping),
                    W.LPVOID,ctypes.POINTER(W.DWORD),ctypes.POINTER(W.DWORD),ctypes.POINTER(W.BOOL)]
        original=W.HANDLE();duplicate=W.HANDLE()
        try:
            self.checked(self.a.OpenProcessToken(self.k.GetCurrentProcess(),10,ctypes.byref(original)))
            self.checked(self.a.DuplicateToken(original,2,ctypes.byref(duplicate)))
            self.token=duplicate.value
        finally:
            if original:self.k.CloseHandle(original)

    @staticmethod
    def checked(ok):
        if not ok:raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.token:self.k.CloseHandle(self.token);self.token=None

    def check(self,path,*,volume_parent=False):
        path=Path(path)
        artifacts._no_links(path)
        owner,descriptor=W.LPVOID(),W.LPVOID()
        text=W.LPWSTR()
        try:
            result=self.a.GetNamedSecurityInfoW(str(path),1,7,ctypes.byref(owner),None,None,None,ctypes.byref(descriptor))
            if result:raise ctypes.WinError(result)
            self.checked(self.a.ConvertSidToStringSidW(owner,ctypes.byref(text)))
            if text.value not in ('S-1-5-18','S-1-5-32-544'):
                raise BridgeError('固定输入组件目录或文件可由普通用户改变权限')
            mapping=self.Mapping(0x120089,0x120116,0x1200a0,0x1f01ff)
            privileges=ctypes.create_string_buffer(4096);length=W.DWORD(len(privileges))
            granted,allowed=W.DWORD(),W.BOOL()
            self.checked(self.a.AccessCheck(descriptor,self.token,0x02000000,ctypes.byref(mapping),
                         privileges,ctypes.byref(length),ctypes.byref(granted),ctypes.byref(allowed)))
            unsafe=0xc0040 if volume_parent else 0xd0156
            if not allowed or granted.value & unsafe:
                raise BridgeError('固定输入组件仍可被普通进程写入或替换；拒绝启动')
        finally:
            if text:self.k.LocalFree(ctypes.cast(text,W.LPVOID))
            if descriptor:self.k.LocalFree(descriptor)


def verify_installation(access):
    root=INSTALL_ROOT
    access.check(root.anchor,volume_parent=True)
    access.check(root)
    access.check(root/'snapshot.json')
    snapshot=read_object(root/'snapshot.json',limit=1024*1024)
    files=snapshot.get('files')
    if snapshot.get('schema')!=1 or not isinstance(files,dict) or not 1<=len(files)<=4096:
        raise BridgeError('固定输入组件文件清单无效')
    required={'install.json','python/python.exe','code/currency_wars_control.py',
              'code/currency_wars_bridge_task.py'}
    if not required.issubset(files):raise BridgeError('固定输入组件文件清单不完整')
    checked={os.path.normcase(str(root))}
    for relative,expected in files.items():
        if (not isinstance(relative,str) or '\\' in relative or ':' in relative
                or any(part in ('','.','..') for part in relative.split('/'))
                or not re.fullmatch('[0-9A-F]{64}',str(expected))):
            raise BridgeError('固定输入组件清单路径或哈希无效')
        path=root/relative
        for parent in reversed(path.parents):
            if parent==root or root in parent.parents:
                key=os.path.normcase(str(parent))
                if key not in checked:access.check(parent);checked.add(key)
        access.check(path)
        if hashlib.sha256(path.read_bytes()).hexdigest().upper()!=expected:
            raise BridgeError('固定输入组件已安装文件改变；拒绝执行')


def configuration(expected_broker_hash):
    try:
        access=InstallationAccess()
        try:verify_installation(access)
        finally:access.close()
        config = read_object(INSTALL_ROOT / 'install.json')
        # Use the installed task's unchanged fixed-path contract before any
        # client creates a runtime. An absolute but unapproved root is invalid.
        from currency_wars_bridge_task import validate_config
        validate_config(config)
        if (config.get('schema') != PROTOCOL or config.get('user_sid') != artifacts._windows_user_sid()
                or config.get('task_name') != task_name(config['user_sid'])
                or not re.fullmatch(r'[0-9a-f]{32}', str(config.get('installation_id', '')))
                or config.get('broker_sha256') != expected_broker_hash
                or config.get('driver_sha256') != hashlib.sha256(Path(__file__).with_name('currency_wars_bridge_task.py').read_bytes()).hexdigest().upper()
                or not Path(config['inbox']).is_absolute() or not Path(config['runtime_root']).is_absolute()):
            raise BridgeError('固定输入权限组件归属或版本不匹配')
        for path in (INSTALL_ROOT / 'python/python.exe', INSTALL_ROOT / 'code/currency_wars_bridge_task.py'):
            artifacts._no_links(path)
            if not path.is_file():
                raise BridgeError('固定输入权限组件不完整')
        return config
    except (OSError, ValueError, KeyError) as error:
        if isinstance(error, BridgeError):
            raise
        raise BridgeError('固定输入权限组件缺失或配置/权限无效；未改用其他运行目录，也不会反复请求Python管理员授权。') from error


def runtime_location(expected_broker_hash, *, inherited=None):
    """Choose once before creating a run; recheck installed identity in child.

    Only an absent installation permits the existing standalone default. A
    present but incomplete/unreadable installation must never fall back. The
    child carries its parent's standalone choice without consulting another
    process's TEMP cache; an installed choice is always freshly validated.
    """
    if inherited is not None:
        if (not isinstance(inherited, dict)
                or set(inherited) != {'schema', 'source', 'runtime_root', 'installation_id'}
                or type(inherited['schema']) is not int or inherited['schema'] != 1
                or inherited['source'] not in ('installed_bridge', 'standalone')
                or not isinstance(inherited['runtime_root'], str)
                or (inherited['source'] == 'standalone') != (inherited['installation_id'] is None)
                or (inherited['installation_id'] is not None
                    and not re.fullmatch(r'[0-9a-f]{32}', str(inherited['installation_id'])))):
            raise BridgeError('启动运行目录绑定格式无效')
    try:
        artifacts._no_links(INSTALL_ROOT)
        try:
            INSTALL_ROOT.lstat()
        except FileNotFoundError:
            if inherited is not None and inherited['installation_id'] is not None:
                raise BridgeError('本次已绑定的输入权限组件消失；拒绝改用默认运行目录')
            root = Path(inherited['runtime_root']) if inherited is not None else artifacts.default_root()
            location = {'schema': 1, 'source': 'standalone', 'runtime_root': str(root), 'installation_id': None}
        else:
            config = configuration(expected_broker_hash)
            root = Path(config['runtime_root'])
            location = {'schema': 1, 'source': 'installed_bridge', 'runtime_root': str(root),
                        'installation_id': config['installation_id']}
        if not root.is_absolute() or root == Path(root.anchor):
            raise BridgeError('启动运行根目录必须是明确绝对目录，不能是卷根')
        artifacts._no_links(root)
        if inherited is not None and (inherited['installation_id'] != location['installation_id']
                or os.path.normcase(os.path.abspath(inherited['runtime_root']))
                != os.path.normcase(os.path.abspath(root))):
            raise BridgeError('父子启动间输入组件或运行根目录变化；未创建运行或另启控制器')
        return location
    except (OSError, ValueError) as error:
        raise BridgeError('启动运行根目录无法验证；未改用其他目录') from error


@contextlib.contextmanager
def inbox_lock(inbox):
    inbox = Path(inbox)
    artifacts._no_links(inbox)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[W.LPVOID,W.BOOL,W.LPCWSTR]
    kernel.CreateMutexW.restype=W.HANDLE
    kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]
    kernel.ReleaseMutex.argtypes=[W.HANDLE]
    kernel.CloseHandle.argtypes=[W.HANDLE]
    name='Local\\CurrencyWarsInputBridgeDispatch-'+hashlib.sha256(os.path.normcase(str(inbox)).encode()).hexdigest()[:32]
    handle=kernel.CreateMutexW(None,False,name)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    acquired=False
    try:
        state=kernel.WaitForSingleObject(handle,0)
        if state not in (0,128):
            raise BridgeError('另一个固定输入启动请求正在派发；未重复启动')
        acquired=True
        yield
    finally:
        if acquired:kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def prepare_launch(run, owner, expected_broker_hash, kind='serve'):
    config = configuration(expected_broker_hash)
    run = Path(run).absolute()
    if kind not in ('serve', 'probe') or os.path.normcase(str(run.parent)) != os.path.normcase(config['runtime_root']):
        raise BridgeError('固定输入组件运行目录或请求类型不匹配')
    marker = artifacts.read_marker(run, root=run.parent)
    if marker['run_id'] != owner['run_id'] or not marker['purpose'].startswith('currency-wars-runner-'):
        raise BridgeError('固定输入组件需要本次已登记的执行器目录')
    request = dict(schema=PROTOCOL, installation_id=config['installation_id'], request_id=uuid.uuid4().hex,
                   kind=kind, run_dir=str(run), chat_id=owner['chat_id'], run_token=owner['run_token'],
                   run_id=owner['run_id'], launch_id=owner['launch_id'], worker_pid=owner['runner_pid'],
                   worker_creation_id=str(owner['runner_creation_id']),
                   expires_at=(datetime.now(timezone.utc) + timedelta(seconds=REQUEST_LIFETIME)).isoformat())
    return dict(config=config, request=request, instance_id=None, dispatch_attempted=False)


def dispatch(ticket):
    config, request = ticket['config'], ticket['request']
    inbox = Path(config['inbox'])
    with inbox_lock(inbox):
        existing = task_rpc(config, 'inspect')
        if existing.get('instances'):
            raise BridgeError('固定输入组件仍有活跃实例；先核验旧控制器退出，不再弹授权')
        if (inbox / 'launch.json').exists():
            raise BridgeError('固定输入组件仍有待确认启动请求；未覆盖或重复启动')
        write_object(inbox / 'launch.json', request)
        ticket['dispatch_attempted'] = True
        # Run is authorized by installation, never by a new ShellExecute RunAs.
        response = task_rpc(config, 'run')
        instance = response.get('instance_id')
        if not isinstance(instance, str) or not re.fullmatch(r'\{[0-9A-Fa-f-]{36}\}', instance):
            raise BridgeError('固定输入任务实例未确认；保留停止标记和运行目录')
        ticket['instance_id'] = instance
        write_object(Path(request['run_dir']) / 'bridge-dispatch.json',
                     {k:v for k,v in ticket.items() if k not in ('request', 'config')})
    return ticket


def cancel_pending(ticket):
    """Keep uncertain/active scheduled instances; never fake a child exit."""
    config, request = ticket['config'], ticket['request']
    with inbox_lock(config['inbox']):
        path = Path(config['inbox']) / 'launch.json'
        if path.exists():
            current = read_object(path)
            if current.get('request_id') == request['request_id']:
                path.unlink()
        if not ticket['dispatch_attempted']:
            return {'launch_attempted':False, 'identity_observed':False, 'state':'not_launched'}
        # TaskScheduler LastTaskResult is global task state, not evidence for
        # this request's actual PID/creation identity. Preserve uncertain runs.
        task_rpc(config, 'inspect')
        raise BridgeError('固定输入任务的本次真实进程退出尚未确认；保留目录防止晚到控制器')


def probe_result(ticket, timeout=25):
    end = time.monotonic() + timeout
    path = Path(ticket['config']['inbox']) / 'result.json'
    while time.monotonic() < end:
        try:
            result = read_object(path)
            if result.get('request_id') == ticket['request']['request_id']:
                return result
        except FileNotFoundError:
            pass
        time.sleep(.1)
    raise BridgeError('固定输入组件核验没有返回本次结果；未重试')
