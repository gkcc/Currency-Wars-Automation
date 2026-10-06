"""Fixed protected scheduled-task driver; never accepts a command or script path.

The installer owns this file, its private interpreter, dependencies and config.
Every caller-controlled filesystem operation runs under a verified medium
impersonation token. Input remains in the original, hash-pinned controller.
"""
import sys

if __name__ == '__main__' and sys.argv[1:] not in ([], ['--self-test']):
    sys.stdout.write('{"schema":1,"ok":false,"error":"cli_mode_invalid"}\n')
    raise SystemExit(2)

# Run before importing even the stdlib. -I -S is also required by main(). Tests
# may import this module, or use --self-test, without changing their sys.path.
_BOOTSTRAP_ROOT = __file__.replace('/', '\\').rsplit('\\', 2)[0]
_PRIVATE_EXECUTABLE = _BOOTSTRAP_ROOT + '\\python\\python.exe'
_TASK_BOOTSTRAP = __name__ == '__main__' and (sys.argv[1:] != ['--self-test']
    or sys.executable.replace('/', '\\').lower() == _PRIVATE_EXECUTABLE.lower())
if _TASK_BOOTSTRAP:
    sys.path[:] = [_BOOTSTRAP_ROOT + '\\python\\Lib',
                   _BOOTSTRAP_ROOT + '\\python\\DLLs',
                   _BOOTSTRAP_ROOT + '\\modules', _BOOTSTRAP_ROOT + '\\code']

import builtins
import contextlib
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import io
import json
import ntpath
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
import types
import uuid
import _io

INSTALL_ROOT = r'D:\CurrencyWarsInputBridge'
RUNTIME_ROOT = r'D:\Codex\Temp\codex-agent-workflow'
INBOX = r'D:\Codex\Workspaces\CurrencyWars-InputBridge\inbox'
PINNED_BROKER_SHA256 = '2B93583C57EE7593CA17CC951F078FA9CD4238285CA84AA83325646F45D86B54'
MEDIUM_RID, HIGH_RID = 8192, 12288
TOKEN_QUERY, TOKEN_DUPLICATE, TOKEN_IMPERSONATE = 8, 2, 4
TOKEN_ADJUST_DEFAULT = 0x80
DISABLE_MAX_PRIVILEGE, LUA_TOKEN = 0x1, 0x4
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MARKER = '.agent-workflow-owner.json'
CONFIG_KEYS = {'schema', 'installation_id', 'user_sid', 'runtime_root', 'inbox',
               'game_path', 'game_sha256', 'broker_sha256', 'driver_sha256', 'task_name'}
REQUEST_KEYS = {'schema', 'installation_id', 'request_id', 'kind', 'run_dir',
                'chat_id', 'run_token', 'run_id', 'launch_id', 'worker_pid',
                'worker_creation_id', 'expires_at'}


class BridgeError(ValueError):
    """A stable public error code, never caller data or a secret token."""


def reject(code):
    raise BridgeError(code)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def local_path(value):
    """Accept only canonical absolute local Win32 paths, without ADS/devices."""
    if not isinstance(value, str) or not 3 <= len(value) <= 1024:
        reject('path_invalid')
    if (not re.match(r'^[A-Za-z]:\\', value) or '/' in value or ':' in value[2:]
            or any(ord(c) < 32 or c in '*?"<>|' for c in value)):
        reject('path_invalid')
    parts = value[3:].split('\\')
    if any(not p or p in ('.', '..') or p[-1:] in (' ', '.') for p in parts):
        reject('path_invalid')
    for part in parts:
        if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I):
            reject('path_invalid')
    if ntpath.normpath(value) != value:
        reject('path_invalid')
    return value


def same_path(one, two):
    return ntpath.normcase(one) == ntpath.normcase(two)


def hex_id(value, code='id_invalid'):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        reject(code)
    return value


def creation_id(value):
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,19}', value):
        result = int(value)
    else:
        reject('creation_id_invalid')
    if not 0 < result < 2**64:
        reject('creation_id_invalid')
    return result


def pid_value(value):
    if type(value) is not int or not 0 < value <= 0xffffffff:
        reject('pid_invalid')
    return value


def token_text(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 128 or not re.fullmatch(r'[0-9a-f]+', value):
        reject('run_token_invalid')
    return value


def validate_config(value):
    if not isinstance(value, dict) or set(value) != CONFIG_KEYS or type(value.get('schema')) is not int or value['schema'] != 1:
        reject('config_invalid')
    hex_id(value['installation_id'], 'installation_id_invalid')
    if not isinstance(value['user_sid'], str) or not re.fullmatch(r'S-1-(?:[0-9]+-){1,14}[0-9]+', value['user_sid']):
        reject('config_sid_invalid')
    for key in ('runtime_root', 'inbox', 'game_path'):
        local_path(value[key])
    if not same_path(value['runtime_root'], RUNTIME_ROOT) or not same_path(value['inbox'], INBOX):
        reject('config_location_invalid')
    for key in ('game_sha256', 'broker_sha256', 'driver_sha256'):
        if not isinstance(value[key], str) or not re.fullmatch(r'[0-9A-Fa-f]{64}', value[key]):
            reject('config_hash_invalid')
    if value['broker_sha256'].upper() != PINNED_BROKER_SHA256:
        reject('broker_hash_unapproved')
    if (not isinstance(value['task_name'], str) or not 1 <= len(value['task_name']) <= 180
            or any(ord(c) < 32 for c in value['task_name'])):
        reject('task_name_invalid')
    return value


def validate_request(value, config, now=None):
    if not isinstance(value, dict) or set(value) != REQUEST_KEYS or type(value.get('schema')) is not int or value['schema'] != 1:
        reject('request_invalid')
    if value['installation_id'] != config['installation_id']:
        reject('installation_mismatch')
    for key in ('request_id', 'run_id', 'launch_id'):
        hex_id(value[key])
    if value['kind'] not in ('serve', 'probe'):
        reject('request_kind_invalid')
    run = local_path(value['run_dir'])
    if (not same_path(ntpath.dirname(run), config['runtime_root'])
            or not ntpath.basename(run).startswith('currency-wars-runner-')):
        reject('run_location_invalid')
    if not isinstance(value['chat_id'], str) or not 1 <= len(value['chat_id']) <= 100 or any(ord(c) < 33 for c in value['chat_id']):
        reject('chat_id_invalid')
    token_text(value['run_token'])
    pid_value(value['worker_pid'])
    creation_id(value['worker_creation_id'])
    expires = value['expires_at']
    if not isinstance(expires, str) or len(expires) > 40:
        reject('expiry_invalid')
    try:
        end = datetime.fromisoformat(expires.replace('Z', '+00:00'))
        if end.tzinfo is None or end.utcoffset().total_seconds() != 0:
            reject('expiry_invalid')
        delta = end.timestamp() - (time.time() if now is None else now)
    except (ValueError, OverflowError, AttributeError):
        reject('expiry_invalid')
    if not 0 < delta <= 30:
        reject('request_expired')
    return value


def validate_identity(facts, config, *, high=False, reference=None, impersonation=False):
    expected = HIGH_RID if high else MEDIUM_RID
    if facts.get('integrity_rid') != expected or facts.get('user_sid') != config['user_sid']:
        reject('token_identity_mismatch')
    if type(facts.get('session_id')) is not int or facts['session_id'] <= 0:
        reject('token_session_invalid')
    if not isinstance(facts.get('authentication_id'), tuple) or len(facts['authentication_id']) != 2:
        reject('token_logon_invalid')
    if high and (facts.get('elevation_type') != 2 or facts.get('token_type') != 1):
        reject('task_not_split_high_token')
    if reference and any(facts.get(key) != reference.get(key) for key in ('user_sid', 'session_id', 'authentication_id')):
        reject('worker_logon_mismatch')
    if impersonation and (facts.get('token_type') != 2 or facts.get('impersonation_level') != 2):
        reject('impersonation_token_invalid')
    return facts


def validate_owners(request, config, marker, runner, owner):
    run, chat, token = request['run_dir'], request['chat_id'], request['run_token']
    hint = marker.get('session_hint') if isinstance(marker, dict) else None
    if (not isinstance(marker, dict) or type(marker.get('schema')) is not int or marker['schema'] != 1
            or marker.get('tool') != 'codex-agent-workflow' or marker.get('path') != run
            or marker.get('root') != config['runtime_root'] or marker.get('run_id') != request['run_id']
            or not isinstance(marker.get('purpose'), str)
            or not re.fullmatch(r'currency-wars-runner-[a-z0-9-]{1,43}', marker['purpose'])
            or type(marker.get('pid')) is not int or marker['pid'] != request['worker_pid']
            or marker.get('process_identity') != 'windows:' + str(creation_id(request['worker_creation_id']))
            or type(marker.get('created_at')) not in (int, float) or not 0 < marker['created_at'] <= 10**12
            or type(marker.get('children_incomplete')) is not bool
            or not isinstance(marker.get('protected_children'), list) or len(marker['protected_children']) > 32
            or not isinstance(hint, dict) or hint.get('source') != 'environment-declared' or hint.get('id') != chat):
        reject('artifact_owner_mismatch')
    for child in marker['protected_children']:
        if not isinstance(child, dict) or type(child.get('pid')) is not int or not 0 < child['pid'] <= 0xffffffff or not re.fullmatch(r'windows:[1-9][0-9]{0,19}', str(child.get('process_identity', ''))):
            reject('artifact_children_invalid')
    if (not isinstance(runner, dict) or runner.get('owner') != 'currency-wars-runner'
            or runner.get('chat_id') != chat or runner.get('artifact_chat_id') != chat
            or runner.get('run_id') != request['run_id'] or runner.get('launch_id') != request['launch_id']
            or type(runner.get('runner_pid')) is not int or runner['runner_pid'] != request['worker_pid']
            or creation_id(runner.get('runner_creation_id')) != creation_id(request['worker_creation_id'])
            or not isinstance(runner.get('run_token'), str) or not secrets.compare_digest(runner['run_token'], token)):
        reject('runner_owner_mismatch')
    if (not isinstance(owner, dict) or owner.get('owner') != 'currency-wars-control'
            or owner.get('chat_id') != chat or owner.get('artifact_chat_id') != chat
            or owner.get('artifact_run_id') != request['run_id']
            or not isinstance(owner.get('run_token'), str) or not secrets.compare_digest(owner['run_token'], token)):
        reject('control_owner_mismatch')
    return owner


def no_reparse(path):
    candidate = Path(path)
    for item in (candidate, *candidate.parents):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            reject('reparse_path_denied')


def validate_run_directory(request, config):
    run, root = Path(request['run_dir']), Path(config['runtime_root'])
    no_reparse(run)
    if not run.is_dir() or run.parent != root or run.resolve().parent != root.resolve():
        reject('run_location_invalid')
    return run


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            reject('json_duplicate_key')
        result[key] = value
    return result


def read_bytes(path, limit):
    # One low-opened handle for the bound and the read; do not stat/reopen.
    with builtins.open(path, 'rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        reject('file_size_exceeded')
    return data


def parse_json(data):
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_object,
                           parse_constant=lambda unused: reject('json_constant_invalid'))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        reject('json_invalid')
    if not isinstance(value, dict):
        reject('json_object_required')
    return value


def read_json(path, limit=16384):
    no_reparse(path)
    return parse_json(read_bytes(path, limit))


def write_json(path, value):
    temporary = Path(path).with_name(Path(path).name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with builtins.open(temporary, 'x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LUID(C.Structure):
    _fields_ = [('LowPart', W.DWORD), ('HighPart', W.LONG)]


class TOKEN_STATISTICS(C.Structure):
    _fields_ = [('TokenId', LUID), ('AuthenticationId', LUID), ('ExpirationTime', C.c_longlong),
                ('TokenType', C.c_int), ('ImpersonationLevel', C.c_int), ('DynamicCharged', W.DWORD),
                ('DynamicAvailable', W.DWORD), ('GroupCount', W.DWORD), ('PrivilegeCount', W.DWORD), ('ModifiedId', LUID)]


class SID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [('Sid', W.LPVOID), ('Attributes', W.DWORD)]


class TOKEN_GROUPS_HEAD(C.Structure):
    _fields_ = [('GroupCount', W.DWORD), ('Groups', SID_AND_ATTRIBUTES * 1)]


class LUID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [('Luid', LUID), ('Attributes', W.DWORD)]


class TOKEN_PRIVILEGES_HEAD(C.Structure):
    _fields_ = [('PrivilegeCount', W.DWORD), ('Privileges', LUID_AND_ATTRIBUTES * 1)]


class WindowsAPI:
    """Read-only token/process inspection, plus medium-only impersonation."""

    def __init__(self):
        if os.name != 'nt':
            reject('windows_required')
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.a = C.WinDLL('advapi32', use_last_error=True)
        self.k.GetCurrentProcess.restype = W.HANDLE
        self.k.GetCurrentThread.restype = W.HANDLE
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.CloseHandle.restype = W.BOOL
        self.k.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
        self.k.GetProcessTimes.restype = W.BOOL
        self.k.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
        self.k.GetExitCodeProcess.restype = W.BOOL
        self.k.ProcessIdToSessionId.argtypes = [W.DWORD, C.POINTER(W.DWORD)]
        self.k.ProcessIdToSessionId.restype = W.BOOL
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.k.LocalFree.argtypes = [W.LPVOID]
        self.k.LocalFree.restype = W.LPVOID
        self.a.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
        self.a.OpenProcessToken.restype = W.BOOL
        self.a.OpenThreadToken.argtypes = [W.HANDLE, W.DWORD, W.BOOL, C.POINTER(W.HANDLE)]
        self.a.OpenThreadToken.restype = W.BOOL
        self.a.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)]
        self.a.GetTokenInformation.restype = W.BOOL
        self.a.ConvertSidToStringSidW.argtypes = [W.LPVOID, C.POINTER(W.LPWSTR)]
        self.a.ConvertSidToStringSidW.restype = W.BOOL
        self.a.GetSidSubAuthorityCount.argtypes = [W.LPVOID]
        self.a.GetSidSubAuthorityCount.restype = C.POINTER(C.c_ubyte)
        self.a.GetSidSubAuthority.argtypes = [W.LPVOID, W.DWORD]
        self.a.GetSidSubAuthority.restype = C.POINTER(W.DWORD)
        self.a.DuplicateTokenEx.argtypes = [W.HANDLE, W.DWORD, W.LPVOID, C.c_int, C.c_int, C.POINTER(W.HANDLE)]
        self.a.DuplicateTokenEx.restype = W.BOOL
        self.a.CreateRestrictedToken.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.LPVOID,
            W.DWORD, W.LPVOID, W.DWORD, W.LPVOID, C.POINTER(W.HANDLE)]
        self.a.CreateRestrictedToken.restype = W.BOOL
        self.a.SetTokenInformation.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD]
        self.a.SetTokenInformation.restype = W.BOOL
        self.a.ConvertStringSidToSidW.argtypes = [W.LPCWSTR, C.POINTER(W.LPVOID)]
        self.a.ConvertStringSidToSidW.restype = W.BOOL
        self.a.GetLengthSid.argtypes = [W.LPVOID]
        self.a.GetLengthSid.restype = W.DWORD
        self.a.LookupPrivilegeNameW.argtypes = [W.LPCWSTR, C.POINTER(LUID), W.LPWSTR, C.POINTER(W.DWORD)]
        self.a.LookupPrivilegeNameW.restype = W.BOOL
        self.a.ImpersonateLoggedOnUser.argtypes = [W.HANDLE]
        self.a.ImpersonateLoggedOnUser.restype = W.BOOL
        self.a.RevertToSelf.argtypes = []
        self.a.RevertToSelf.restype = W.BOOL

    @staticmethod
    def checked(ok):
        if not ok:
            raise C.WinError(C.get_last_error())

    def close(self, handle):
        if handle:
            self.checked(self.k.CloseHandle(handle))

    def info(self, token, kind):
        size = W.DWORD()
        self.a.GetTokenInformation(token, kind, None, 0, C.byref(size))
        if not 0 < size.value <= 65536:
            reject('token_information_unavailable')
        data = C.create_string_buffer(size.value)
        self.checked(self.a.GetTokenInformation(token, kind, data, size.value, C.byref(size)))
        return data

    def facts(self, token):
        sid_data = self.info(token, 1)
        sid = C.cast(sid_data, C.POINTER(SID_AND_ATTRIBUTES)).contents.Sid
        user_sid = self.sid_string(sid)
        label_data = self.info(token, 25)
        label_sid = C.cast(label_data, C.POINTER(SID_AND_ATTRIBUTES)).contents.Sid
        count = self.a.GetSidSubAuthorityCount(label_sid)[0]
        if not count:
            reject('token_integrity_unavailable')
        rid = self.a.GetSidSubAuthority(label_sid, count - 1)[0]
        statistics_data = self.info(token, 10)
        statistics = C.cast(statistics_data, C.POINTER(TOKEN_STATISTICS)).contents
        return {'user_sid': user_sid, 'integrity_rid': int(rid),
                'session_id': C.cast(self.info(token, 12), C.POINTER(W.DWORD)).contents.value,
                'elevation_type': C.cast(self.info(token, 18), C.POINTER(W.DWORD)).contents.value,
                'authentication_id': (statistics.AuthenticationId.LowPart, statistics.AuthenticationId.HighPart),
                'token_type': statistics.TokenType, 'impersonation_level': statistics.ImpersonationLevel}

    def sid_string(self, sid):
        text = W.LPWSTR()
        try:
            self.checked(self.a.ConvertSidToStringSidW(sid, C.byref(text)))
            return text.value
        finally:
            if text:
                self.k.LocalFree(C.cast(text, W.LPVOID))

    def restricted_security(self, token):
        """Inspect actual access-check groups and remaining privileges."""
        groups = self.info(token, 2)
        group_count = C.cast(groups, C.POINTER(W.DWORD)).contents.value
        offset, stride = TOKEN_GROUPS_HEAD.Groups.offset, C.sizeof(SID_AND_ATTRIBUTES)
        if group_count > 512 or offset + group_count * stride > len(groups):
            reject('token_groups_invalid')
        administrators = None
        for index in range(group_count):
            group = SID_AND_ATTRIBUTES.from_buffer(groups, offset + index * stride)
            if self.sid_string(group.Sid) == 'S-1-5-32-544':
                administrators = int(group.Attributes)
        privileges = self.info(token, 3)
        privilege_count = C.cast(privileges, C.POINTER(W.DWORD)).contents.value
        offset, stride = TOKEN_PRIVILEGES_HEAD.Privileges.offset, C.sizeof(LUID_AND_ATTRIBUTES)
        if privilege_count > 128 or offset + privilege_count * stride > len(privileges):
            reject('token_privileges_invalid')
        names = []
        for index in range(privilege_count):
            privilege = LUID_AND_ATTRIBUTES.from_buffer(privileges, offset + index * stride)
            length = W.DWORD()
            self.a.LookupPrivilegeNameW(None, C.byref(privilege.Luid), None, C.byref(length))
            if not 0 < length.value <= 128:
                reject('token_privilege_name_invalid')
            text = C.create_unicode_buffer(length.value + 1)
            length.value += 1
            self.checked(self.a.LookupPrivilegeNameW(None, C.byref(privilege.Luid), text, C.byref(length)))
            names.append(text.value)
        return {'administrators_present': administrators is not None,
                'administrators_deny_only': administrators is not None and bool(administrators & 0x10) and not bool(administrators & 0x6),
                'remaining_privileges': names}

    def process_token(self, process):
        token = W.HANDLE()
        self.checked(self.a.OpenProcessToken(process, TOKEN_QUERY | TOKEN_DUPLICATE, C.byref(token)))
        return token.value

    def current_token(self):
        return self.process_token(self.k.GetCurrentProcess())

    def linked_medium_token(self, high_token):
        # Caller must have already verified high_token as the task's full high
        # process token. There is deliberately no medium-to-high operation.
        facts = self.facts(high_token)
        if facts['integrity_rid'] != HIGH_RID or facts['elevation_type'] != 2 or facts['token_type'] != 1:
            reject('linked_lookup_requires_high_process_token')
        data = self.info(high_token, 19)
        result = C.cast(data, C.POINTER(W.HANDLE)).contents.value
        if not result:
            reject('filtered_token_unavailable')
        return result

    def duplicate_medium(self, token):
        facts = self.facts(token)
        if facts['integrity_rid'] != MEDIUM_RID:
            reject('only_medium_can_be_duplicated')
        if facts['token_type'] == 2 and facts['impersonation_level'] < 2:
            reject('identification_token_not_impersonable')
        duplicate = W.HANDLE()
        self.checked(self.a.DuplicateTokenEx(token, TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_IMPERSONATE,
                                            None, 2, 2, C.byref(duplicate)))
        return duplicate.value

    def restricted_medium(self, source):
        """Only reduce a primary token; never raise an identification level.

        CreateRestrictedToken preserves source access rights and token type.
        A temporary primary duplicate requests ADJUST_DEFAULT solely so the
        new restricted token's mandatory label can be reduced to medium.
        Medium source is allowed for the ordinary, non-elevating offline check.
        """
        original = self.facts(source)
        if (original['token_type'] != 1 or original['integrity_rid'] not in (MEDIUM_RID, HIGH_RID)
                or original['integrity_rid'] == HIGH_RID and original['elevation_type'] != 2):
            reject('restricted_source_must_be_primary')
        adjustable, restricted, admin_sid, medium_sid = W.HANDLE(), W.HANDLE(), W.LPVOID(), W.LPVOID()
        returned = False
        try:
            self.checked(self.a.DuplicateTokenEx(source, TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ADJUST_DEFAULT,
                                                None, 2, 1, C.byref(adjustable)))
            self.checked(self.a.ConvertStringSidToSidW('S-1-5-32-544', C.byref(admin_sid)))
            disabled = SID_AND_ATTRIBUTES(admin_sid, 0)
            self.checked(self.a.CreateRestrictedToken(adjustable, LUA_TOKEN | DISABLE_MAX_PRIVILEGE,
                1, C.byref(disabled), 0, None, 0, None, C.byref(restricted)))
            self.checked(self.a.ConvertStringSidToSidW('S-1-16-8192', C.byref(medium_sid)))
            label = SID_AND_ATTRIBUTES(medium_sid, 0x20)
            self.checked(self.a.SetTokenInformation(restricted, 25, C.byref(label),
                                                   C.sizeof(label) + self.a.GetLengthSid(medium_sid)))
            reduced = self.facts(restricted)
            if (reduced['integrity_rid'] != MEDIUM_RID or reduced['token_type'] != 1
                    or any(reduced[key] != original[key] for key in ('user_sid', 'session_id', 'authentication_id'))):
                reject('restricted_medium_identity_mismatch')
            security = self.restricted_security(restricted)
            if ((security['administrators_present'] and not security['administrators_deny_only'])
                    or original['integrity_rid'] == HIGH_RID and not security['administrators_deny_only']
                    or any(name != 'SeChangeNotifyPrivilege' for name in security['remaining_privileges'])):
                reject('restricted_token_access_not_reduced')
            returned = True
            return restricted.value
        finally:
            if adjustable:
                self.close(adjustable)
            if restricted and not returned:
                self.close(restricted)
            for sid in (admin_sid, medium_sid):
                if sid:
                    self.k.LocalFree(sid)

    def thread_facts(self):
        token = W.HANDLE()
        if not self.a.OpenThreadToken(self.k.GetCurrentThread(), TOKEN_QUERY, True, C.byref(token)):
            if C.get_last_error() == 1008:
                return None
            raise C.WinError(C.get_last_error())
        try:
            return self.facts(token)
        finally:
            self.close(token)

    def impersonate(self, token):
        self.checked(self.a.ImpersonateLoggedOnUser(token))

    def revert(self):
        self.checked(self.a.RevertToSelf())

    def process(self, pid):
        handle = self.k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid_value(pid))
        if not handle:
            raise C.WinError(C.get_last_error())
        return handle

    def snapshot(self, process, pid):
        created, exited, kernel, user = (W.FILETIME() for unused in range(4))
        self.checked(self.k.GetProcessTimes(process, C.byref(created), C.byref(exited), C.byref(kernel), C.byref(user)))
        code = W.DWORD()
        self.checked(self.k.GetExitCodeProcess(process, C.byref(code)))
        return {'pid': pid, 'creation_id': (created.dwHighDateTime << 32) | created.dwLowDateTime,
                'state': 'running' if code.value == 259 else 'exited', 'exit_code': code.value}

    def session_id(self, pid):
        session = W.DWORD()
        self.checked(self.k.ProcessIdToSessionId(pid, C.byref(session)))
        return session.value

    def image_path(self, process):
        text, length = C.create_unicode_buffer(32768), W.DWORD(32768)
        self.checked(self.k.QueryFullProcessImageNameW(process, 0, text, C.byref(length)))
        return local_path(text.value)

    def safe_dll_search(self, root):
        self.k.SetDefaultDllDirectories.argtypes = [W.DWORD]
        self.k.SetDefaultDllDirectories.restype = W.BOOL
        self.k.AddDllDirectory.argtypes = [W.LPCWSTR]
        self.k.AddDllDirectory.restype = W.LPVOID
        self.checked(self.k.SetDefaultDllDirectories(0x1000))
        # SetDefaultDllDirectories excludes the current directory and PATH;
        # the only USER_DIRS are these two protected dependency locations.
        for folder in (Path(root, 'python', 'DLLs'), Path(root, 'modules', 'PIL')):
            if not self.k.AddDllDirectory(str(folder)):
                raise C.WinError(C.get_last_error())


class _ScopedDirEntry:
    def __init__(self, entry, guard):
        self._entry, self._guard = entry, guard
        self.name, self.path = entry.name, entry.path

    def __fspath__(self):
        return self.path

    def _call(self, name, *args, **kwargs):
        with self._guard.scope():
            return getattr(self._entry, name)(*args, **kwargs)

    def stat(self, **kwargs):
        return self._call('stat', **kwargs)

    def is_file(self, **kwargs):
        return self._call('is_file', **kwargs)

    def is_dir(self, **kwargs):
        return self._call('is_dir', **kwargs)

    def is_symlink(self):
        return self._call('is_symlink')

    def inode(self):
        return self._call('inode')


class _ScopedScandir:
    def __init__(self, iterator, guard):
        self._iterator, self._guard = iterator, guard

    def __iter__(self):
        return self

    def __next__(self):
        with self._guard.scope():
            return _ScopedDirEntry(next(self._iterator), self._guard)

    def close(self):
        with self._guard.scope():
            self._iterator.close()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


class FilesystemGuard:
    """Guard each filesystem call, including lazy scandir and Pillow saves.

    A handle opened while impersonating may safely outlive a scope: Windows
    fixes its access rights at open. No failure retries an operation as high.
    """
    OS_FUNCTIONS = ('open', 'stat', 'lstat', 'listdir', 'mkdir', 'replace', 'rename',
                    'unlink', 'remove', 'rmdir', 'access', 'readlink', 'symlink',
                    'link', 'chmod', 'utime', 'truncate', 'chdir', 'getcwd',
                    'read', 'write', 'close', 'fstat', 'fsync', 'lseek',
                    'ftruncate', 'fdopen', 'readv', 'writev')

    def __init__(self, api, token, facts):
        if facts.get('integrity_rid') != MEDIUM_RID or facts.get('token_type') != 2 or facts.get('impersonation_level') != 2:
            reject('filesystem_token_invalid')
        self.api, self.token, self.facts = api, token, facts
        self._lock, self._local, self._originals = threading.RLock(), threading.local(), []
        self.installed = False

    @contextlib.contextmanager
    def scope(self):
        with self._lock:
            depth = getattr(self._local, 'depth', 0)
            if depth:
                self._local.depth += 1
                try:
                    yield
                finally:
                    self._local.depth -= 1
                return
            if self.api.thread_facts() is not None:
                reject('unexpected_thread_impersonation')
            # A failed impersonation is a hard failure, never a high retry.
            self.api.impersonate(self.token)
            self._local.depth = 1
            try:
                actual = self.api.thread_facts()
                if not actual or any(actual.get(k) != self.facts.get(k) for k in
                                     ('integrity_rid', 'user_sid', 'session_id', 'authentication_id', 'token_type', 'impersonation_level')):
                    reject('filesystem_impersonation_mismatch')
                yield
            finally:
                self._local.depth = 0
                self.api.revert()
                if self.api.thread_facts() is not None:
                    reject('filesystem_revert_failed')

    def replace_token(self, token, facts):
        with self._lock:
            if getattr(self._local, 'depth', 0):
                reject('filesystem_token_switch_in_scope')
            if facts.get('integrity_rid') != MEDIUM_RID or facts.get('token_type') != 2 or facts.get('impersonation_level') != 2:
                reject('filesystem_token_invalid')
            self.token, self.facts = token, facts

    def _patch(self, module, name, special=None):
        original = getattr(module, name, None)
        if original is None:
            return
        def wrapper(*args, **kwargs):
            with self.scope():
                result = original(*args, **kwargs)
                return special(result, self) if special else result
        self._originals.append((module, name, original, wrapper))
        setattr(module, name, wrapper)

    def install(self):
        if self._originals:
            reject('filesystem_guard_already_installed')
        for module, names in ((builtins, ('open',)), (io, ('open', 'open_code')), (_io, ('open', 'open_code')),
                              (os, self.OS_FUNCTIONS)):
            for name in names:
                self._patch(module, name)
        self._patch(os, 'scandir', _ScopedScandir)
        self.installed = True

    def protect_image_save(self):
        # This import and any codec opened lazily also use the medium wrapper.
        from PIL import Image
        self._patch(Image.Image, 'save')

    def restore(self):
        for module, name, original, wrapper in reversed(self._originals):
            if getattr(module, name) is not wrapper:
                reject('filesystem_wrapper_changed')
            setattr(module, name, original)
        self._originals.clear()
        self.installed = False


def assert_bootstrap(root=INSTALL_ROOT):
    expected = [str(Path(root, 'python', 'Lib')), str(Path(root, 'python', 'DLLs')),
                str(Path(root, 'modules')), str(Path(root, 'code'))]
    if not same_path(_BOOTSTRAP_ROOT, root) or not same_path(str(Path(__file__)), str(Path(root, 'code', 'currency_wars_bridge_task.py'))):
        reject('unprotected_task_location')
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.no_user_site and sys.flags.ignore_environment and sys.dont_write_bytecode):
        reject('interpreter_isolation_required')
    if (not same_path(sys.executable, str(Path(root, 'python', 'python.exe')))
            or not same_path(sys.prefix, str(Path(root, 'python')))
            or not same_path(sys.base_prefix, str(Path(root, 'python')))
            or [ntpath.normcase(p) for p in sys.path] != [ntpath.normcase(p) for p in expected]):
        reject('private_runtime_required')
    for module in tuple(sys.modules.values()):
        source = getattr(module, '__file__', None)
        if source and not any(same_path(source, folder) or ntpath.normcase(source).startswith(ntpath.normcase(folder) + '\\') for folder in expected):
            reject('module_outside_private_runtime')
    for path in (Path(root), *[Path(p) for p in expected], Path(sys.executable), Path(root, 'install.json')):
        no_reparse(path)


def protected_write_denied(root):
    for folder in (Path(root), Path(root, 'python'), Path(root, 'modules'), Path(root, 'code')):
        no_reparse(folder)
        if not folder.is_dir():
            reject('private_runtime_incomplete')
        path = folder / ('.medium-write-probe-' + uuid.uuid4().hex)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except PermissionError:
            continue
        else:
            os.close(descriptor)
            path.unlink(missing_ok=True)
            reject('protected_filesystem_writable')
    return True


def verified_broker_source(root, config):
    source = Path(root, 'code', 'currency_wars_control.py')
    no_reparse(source)
    data = read_bytes(source, 256 * 1024)
    digest = hashlib.sha256(data).hexdigest().upper()
    if digest != PINNED_BROKER_SHA256 or digest != config['broker_sha256'].upper():
        reject('broker_source_hash_mismatch')
    return source, data


def load_control(source, data, guard):
    # Compile the bytes that were hashed, never reopen source or import the
    # project entry/artifacts modules (which could load a user workflow helper).
    module = types.ModuleType('currency_wars_sealed_control')
    module.__file__, module.__package__ = str(source), ''
    exec(compile(data, str(source), 'exec'), module.__dict__)
    if module.PROTOCOL_VERSION != 2:
        reject('broker_protocol_mismatch')
    guard.protect_image_save()
    install_expected_resume(module)
    return module


def install_expected_resume(control):
    """Preserve the approved client's pause CAS at the original actual read.

    This is the existing broker_entry adapter sealed into the installation;
    no game input implementation is replaced and no user entry is imported.
    """
    original_execute = control.execute_request
    original_read = control.read_optional
    def execute(request):
        if request.get('kind') != 'resume':
            return original_execute(request)
        if 'expected_pause_id' not in request:
            raise ValueError('expected pause identity required; no handoff')
        expected = request['expected_pause_id']
        if expected is not None and not isinstance(expected, str):
            raise ValueError('invalid expected pause identity')
        forwarded = {k: v for k, v in request.items() if k != 'expected_pause_id'}
        first = True
        def read(name):
            nonlocal first
            value = original_read(name)
            if name == 'manual-pause.json' and first:
                first = False
                if (value.get('pause_id') if value else None) != expected:
                    raise RuntimeError('new pause before original resume read; no handoff')
            return value
        control.read_optional = read
        try:
            return original_execute(forwarded)
        finally:
            control.read_optional = original_read
    control.execute_request = execute


def assert_not_stopped(run):
    if any((run / name).exists() for name in ('broker-stop', 'runner-stop')):
        reject('run_already_stopped')


def validate_game(control, binding, api, config, handles, reference):
    if not isinstance(binding, dict):
        reject('binding_invalid')
    pid = pid_value(binding.get('pid'))
    created = creation_id(binding.get('creation_id'))
    hwnd, rect = binding.get('hwnd'), binding.get('rect')
    if (type(hwnd) is not int or not 0 < hwnd < 2**64 or not isinstance(rect, list) or len(rect) != 4
            or any(type(v) is not int or abs(v) > 100000 for v in rect)
            # The pinned broker normalizes captures to 1920x1080 and maps
            # logical input coordinates into the verified physical client.
            or (rect[2] - rect[0], rect[3] - rect[1]) not in ((1920, 1080), (3840, 2160))):
        reject('binding_invalid')
    process = api.process(pid)
    handles.append(process)
    identity = api.snapshot(process, pid)
    if identity['state'] != 'running' or identity['creation_id'] != created:
        reject('game_process_mismatch')
    token = api.process_token(process)
    handles.append(token)
    facts = api.facts(token)
    if (facts['user_sid'] != config['user_sid'] or facts['session_id'] != reference['session_id']
            or facts['integrity_rid'] > HIGH_RID):
        reject('game_token_mismatch')
    if not same_path(api.image_path(process), config['game_path']):
        reject('game_image_mismatch')
    no_reparse(config['game_path'])
    digest = hashlib.sha256()
    with builtins.open(config['game_path'], 'rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest().upper() != config['game_sha256'].upper():
        reject('game_hash_mismatch')
    actual_hwnd, actual_pid, actual_rect = control.win()
    if int(actual_hwnd) != hwnd or actual_pid != pid or tuple(actual_rect) != tuple(rect):
        reject('game_window_mismatch')


def result_identity(api, process, high, medium):
    state = api.snapshot(process, os.getpid())
    if state['state'] != 'running':
        reject('task_process_unverified')
    return {'process_pid': state['pid'], 'creation_id': state['creation_id'],
            'process_integrity_rid': high['integrity_rid'], 'filesystem_integrity_rid': medium['integrity_rid'],
            'user_sid': high['user_sid'], 'session_id': high['session_id']}


def execute_task(api, config, root=INSTALL_ROOT, status=None):
    """One launch request per invocation. Tests replace only the read-only API."""
    handles, guard, request, run, owner, accepted = [], None, None, None, None, False
    public, error = None, None
    phase = 'task_token'
    def progress(stage):
        nonlocal phase
        phase = stage
        if status is not None:
            status(stage)
    try:
        high_token = api.current_token()
        handles.append(high_token)
        high = validate_identity(api.facts(high_token), config, high=True)
        if api.thread_facts() is not None or api.session_id(os.getpid()) != high['session_id']:
            reject('task_session_mismatch')
        progress('linked_identity')
        # This is the only linked-token lookup: from the verified high process
        # token to its filtered medium pair, before any caller filesystem read.
        linked = api.linked_medium_token(high_token)
        handles.append(linked)
        filtered = validate_identity(api.facts(linked), config)
        if (filtered.get('elevation_type') != 3 or filtered.get('token_type') not in (1, 2)
                or filtered.get('token_type') == 2 and filtered.get('impersonation_level', 0) < 1
                or filtered['session_id'] != high['session_id']):
            reject('filtered_token_invalid')
        # Task Scheduler can return a linked SecurityIdentification token.
        # It is only an identity/logon reference, never duplicated to raise its
        # level. Derive an impersonable *reduced* token from our own primary.
        progress('restricted_token')
        reduced = api.restricted_medium(high_token)
        handles.append(reduced)
        reduced_facts = validate_identity(api.facts(reduced), config, reference=high)
        initial_imp = api.duplicate_medium(reduced)
        handles.append(initial_imp)
        initial_facts = validate_identity(api.facts(initial_imp), config, reference=reduced_facts, impersonation=True)
        guard = FilesystemGuard(api, initial_imp, initial_facts)
        guard.install()
        progress('initial_protected_write')
        protected_write_denied(root)  # Must precede the first caller path read.
        progress('inbox_request')
        inbox = Path(config['inbox'])
        no_reparse(inbox)
        no_reparse(inbox / 'launch.json')
        first = read_bytes(inbox / 'launch.json', 16384)
        request = parse_json(first)
        validate_request(request, config)
        worker = api.process(request['worker_pid'])
        handles.append(worker)
        observed = api.snapshot(worker, request['worker_pid'])
        if observed['state'] != 'running' or observed['creation_id'] != creation_id(request['worker_creation_id']):
            reject('worker_process_mismatch')
        worker_token = api.process_token(worker)
        handles.append(worker_token)
        progress('worker_token')
        medium = validate_identity(api.facts(worker_token), config, reference=filtered)
        if medium.get('token_type') != 1 or api.session_id(request['worker_pid']) != medium['session_id']:
            reject('worker_session_mismatch')
        worker_imp = api.duplicate_medium(worker_token)
        handles.append(worker_imp)
        medium_imp = validate_identity(api.facts(worker_imp), config, reference=filtered, impersonation=True)
        guard.replace_token(worker_imp, medium_imp)
        # A handle obtained with the actual worker token must see the same
        # request; PID supplied by JSON has granted no filesystem privileges.
        if read_bytes(inbox / 'launch.json', 16384) != first:
            reject('launch_request_changed')
        claimed = inbox / ('launch-claimed-' + str(os.getpid()) + '-' + uuid.uuid4().hex + '.json')
        try:
            os.replace(inbox / 'launch.json', claimed)
            no_reparse(claimed)
            if read_bytes(claimed, 16384) != first:
                reject('launch_request_changed')
        finally:
            claimed.unlink(missing_ok=True)
        run = validate_run_directory(request, config)
        marker = read_json(run / MARKER, 4096)
        runner = read_json(run / 'runner-owner.json')
        candidate_owner = read_json(run / 'owner.json')
        validate_owners(request, config, marker, runner, candidate_owner)
        owner = candidate_owner  # Only now may a failure be recorded in run.
        progress('owned_run')
        assert_not_stopped(run)
        source, data = verified_broker_source(root, config)
        denied = protected_write_denied(root)
        own_process = api.process(os.getpid())
        handles.append(own_process)
        public = {'schema': 1, 'request_id': request['request_id'], 'installation_id': config['installation_id'],
                  'ok': True, **result_identity(api, own_process, high, medium_imp), 'protected_write_denied': denied}
        validate_request(request, config)
        if request['kind'] == 'probe':
            # Deliberately no control import, game/window lookup or input.
            write_json(inbox / 'result.json', public)
            progress('probe_complete')
            return 0
        control = load_control(source, data, guard)
        binding = read_json(run / 'binding.json')
        control.ROOT, control.OWNER, control.BINDING = str(run), owner, binding
        validate_game(control, binding, api, config, handles, high)
        assert_not_stopped(run)
        validate_request(request, config)
        if api.snapshot(worker, request['worker_pid']) != observed:
            reject('worker_process_changed')
        identity = api.snapshot(own_process, os.getpid())
        identity.update({'chat_id': request['chat_id'], 'run_token': request['run_token'],
                         'started': utc_now(), 'protocol_version': 2})
        # STOP can now identify this exact process even before serve's mutex.
        write_json(run / 'broker-process.json', identity)
        write_json(run / 'bridge-accepted.json', {**identity, **public, 'run_id': request['run_id'],
                   'launch_id': request['launch_id'], 'worker_pid': request['worker_pid'],
                   'worker_creation_id': request['worker_creation_id']})
        accepted = True
        write_json(inbox / 'result.json', public)
        assert_not_stopped(run)
        progress('serve')
        control.serve()  # Original mutex, pause/input guards and 7200/600 leases.
        progress('serve_returned')
        return 0
    except Exception as exc:
        error = error_code(exc, 'task_failed')
        if status is not None:
            status(phase, error=error, winerror=getattr(exc, 'winerror', None))
        if guard is not None and guard.installed:
            if request is not None and owner is not None and run is not None:
                try:
                    write_json(run / 'broker-start-error.json', {'schema': 1, 'error': error,
                        'request_id': request['request_id'], 'chat_id': request['chat_id'],
                        'run_id': request['run_id'], 'launch_id': request['launch_id'],
                        'worker_pid': request['worker_pid'], 'worker_creation_id': request['worker_creation_id'],
                        'time': utc_now()})
                except Exception:
                    pass
            try:
                request_id = request.get('request_id') if isinstance(request, dict) else None
                if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f]{32}', request_id):
                    request_id = None
                write_json(Path(config['inbox'], 'result.json'), {'schema': 1, 'ok': False,
                    'request_id': request_id, 'installation_id': config['installation_id'], 'error': error})
            except Exception:
                pass
        return 2
    finally:
        try:
            if guard is not None and accepted:
                try:
                    write_json(run / 'bridge-exit.json', {'schema': 1, 'request_id': request['request_id'],
                        'pid': public['process_pid'], 'creation_id': public['creation_id'],
                        'run_id': request['run_id'], 'launch_id': request['launch_id'],
                        'reason': error or 'serve_returned', 'process_exit_confirmed': False, 'time': utc_now()})
                except Exception:
                    pass
        finally:
            try:
                if guard is not None:
                    guard.restore()
            finally:
                for handle in reversed(handles):
                    try:
                        api.close(handle)
                    except OSError:
                        pass


def error_code(error, prefix):
    code = str(error) if isinstance(error, BridgeError) else prefix + '_' + type(error).__name__
    return code if re.fullmatch(r'[a-zA-Z0-9_]{1,100}', code) else prefix


def status_reporter(stream, identity):
    """Write through one preopened, fixed protected diagnostic handle only.

    No caller record, filename, token, SID, logon ID or exception message is
    accepted here. This is not a high filesystem retry for any caller operation.
    """
    allowed = {'bootstrap', 'config', 'driver_hash', 'dll_search', 'task_token',
               'linked_identity', 'restricted_token', 'initial_protected_write',
               'inbox_request', 'worker_token', 'owned_run', 'probe_complete',
               'serve', 'serve_returned'}
    def report(stage, error=None, winerror=None):
        if stage not in allowed:
            reject('bootstrap_stage_invalid')
        value = {'schema': 1, 'pid': identity['pid'], 'creation_id': identity['creation_id'],
                 'stage': stage, 'ok': error is None, 'time': utc_now()}
        if error is not None:
            value['error'] = error if re.fullmatch(r'[a-zA-Z0-9_]{1,100}', error) else 'bootstrap_failed'
        if type(winerror) is int and 0 <= winerror <= 0xffffffff:
            value['winerror'] = winerror
        try:
            stream.seek(0)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.truncate()
            stream.flush()
        except OSError:
            pass  # Never reopen the handle, change path or relax token checks.
    return report


def self_test():
    """Bounded offline schema checks; never linked tokens, control or input."""
    config = {'schema': 1, 'installation_id': '1' * 32, 'user_sid': 'S-1-5-21-1-2-3-1001',
              'runtime_root': RUNTIME_ROOT, 'inbox': INBOX, 'game_path': r'D:\Game\StarRail.exe',
              'game_sha256': 'A' * 64, 'broker_sha256': PINNED_BROKER_SHA256,
              'driver_sha256': 'B' * 64, 'task_name': 'CurrencyWarsInputBridge'}
    validate_config(config)
    request = {'schema': 1, 'installation_id': config['installation_id'], 'request_id': '2' * 32,
               'kind': 'probe', 'run_dir': RUNTIME_ROOT + r'\currency-wars-runner-offline',
               'chat_id': 'offline', 'run_token': '3' * 48, 'run_id': '4' * 32, 'launch_id': '5' * 32,
               'worker_pid': 1, 'worker_creation_id': '1', 'expires_at': '2026-01-01T00:00:20+00:00'}
    validate_request(request, config, now=datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    expected = [_BOOTSTRAP_ROOT + '\\python\\Lib', _BOOTSTRAP_ROOT + '\\python\\DLLs',
                _BOOTSTRAP_ROOT + '\\modules', _BOOTSTRAP_ROOT + '\\code']
    paths_private = [ntpath.normcase(p) for p in sys.path] == [ntpath.normcase(p) for p in expected]
    origins_private = all(not getattr(m, '__file__', None) or any(
        ntpath.normcase(m.__file__).startswith(ntpath.normcase(folder) + '\\')
        for folder in expected) for m in tuple(sys.modules.values()))
    result = {'schema': 1, 'ok': True, 'no_input': True, 'task_started': False,
              'broker_sha256': PINNED_BROKER_SHA256, 'request_modes': ['serve', 'probe'],
              'isolated': bool(sys.flags.isolated), 'no_site': bool(sys.flags.no_site),
              'no_user_site': bool(sys.flags.no_user_site), 'ignore_environment': bool(sys.flags.ignore_environment),
              'dont_write_bytecode': bool(sys.dont_write_bytecode), 'sys_path': list(sys.path),
              'sys_executable': sys.executable, 'paths_private': paths_private,
              'module_origins_private': origins_private, 'protected_installation_verified': False}
    if os.name == 'nt':
        api = WindowsAPI()
        token = api.current_token()
        try:
            facts = api.facts(token)
            result.update({'process_pid': os.getpid(), 'process_integrity_rid': facts['integrity_rid'],
                           'user_sid': facts['user_sid'], 'session_id': facts['session_id']})
        finally:
            api.close(token)
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    diagnostic, status, phase = None, None, 'bootstrap'
    try:
        if argv == ['--self-test']:
            print(json.dumps(self_test(), ensure_ascii=False))
            return 0
        if argv:
            reject('cli_mode_invalid')
        assert_bootstrap()
        api = WindowsAPI()
        bootstrap_token = api.current_token()
        try:
            facts = api.facts(bootstrap_token)
            if facts['integrity_rid'] != HIGH_RID or facts['token_type'] != 1 or facts['elevation_type'] != 2:
                reject('bootstrap_requires_full_high_token')
        finally:
            api.close(bootstrap_token)
        # All ancestors were checked by assert_bootstrap; the approved installer
        # owns the complete fixed root. This is opened before caller filesystem
        # reads, and never reopened after the medium guard is installed.
        diagnostic_path = Path(INSTALL_ROOT, 'bootstrap-status.json')
        if diagnostic_path.exists():
            no_reparse(diagnostic_path)
        identity = api.snapshot(api.k.GetCurrentProcess(), os.getpid())
        diagnostic = builtins.open(diagnostic_path, 'w', encoding='utf-8')
        status = status_reporter(diagnostic, identity)
        status(phase)
        phase = 'config'
        status(phase)
        config = validate_config(read_json(Path(INSTALL_ROOT, 'install.json')))
        phase = 'driver_hash'
        status(phase)
        if hashlib.sha256(read_bytes(Path(INSTALL_ROOT, 'code', 'currency_wars_bridge_task.py'), 256 * 1024)).hexdigest().upper() != config['driver_sha256'].upper():
            reject('driver_source_hash_mismatch')
        phase = 'dll_search'
        status(phase)
        api.safe_dll_search(INSTALL_ROOT)
        os.chdir(INSTALL_ROOT)
        return execute_task(api, config, status=status)
    except Exception as exc:
        code = error_code(exc, 'bootstrap_failed')
        if status is not None:
            status(phase, error=code, winerror=getattr(exc, 'winerror', None))
        print(json.dumps({'schema': 1, 'ok': False, 'error': code}, ensure_ascii=False))
        return 2
    finally:
        if diagnostic is not None:
            diagnostic.close()


if __name__ == '__main__':
    raise SystemExit(main())
