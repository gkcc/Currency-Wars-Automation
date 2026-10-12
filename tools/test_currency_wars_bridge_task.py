"""Offline bridge checks. Never run the task, original broker, GUI or input."""
import ast
import builtins
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import types
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import currency_wars_artifacts as artifacts
import currency_wars_bridge_task as bridge

TEST_ROOT = Path(r'D:\Codex\Temp\codex-agent-workflow')


def config_value():
    return {'schema': 1, 'installation_id': '1' * 32, 'user_sid': 'S-1-5-21-1-2-3-1001',
            'runtime_root': bridge.RUNTIME_ROOT, 'inbox': bridge.INBOX,
            'game_path': r'D:\Game\StarRail.exe', 'game_sha256': 'A' * 64,
            'broker_sha256': bridge.PINNED_BROKER_SHA256, 'driver_sha256': 'B' * 64,
            'task_name': 'CurrencyWarsInputBridge'}


def request_value(config=None):
    config = config or config_value()
    return {'schema': 1, 'installation_id': config['installation_id'], 'request_id': '2' * 32,
            'kind': 'probe', 'run_dir': bridge.RUNTIME_ROOT + r'\currency-wars-runner-offline',
            'chat_id': 'actual-test-chat', 'run_token': '3' * 48, 'run_id': '4' * 32,
            'launch_id': '5' * 32, 'worker_pid': 777, 'worker_creation_id': '1234567',
            'expires_at': datetime.fromtimestamp(time.time() + 20, timezone.utc).isoformat()}


def owner_values(request, config):
    chat, token = request['chat_id'], request['run_token']
    marker = {'schema': 1, 'tool': 'codex-agent-workflow', 'run_id': request['run_id'],
              'root': config['runtime_root'], 'path': request['run_dir'],
              'purpose': 'currency-wars-runner-offline', 'pid': request['worker_pid'],
              'process_identity': 'windows:' + request['worker_creation_id'], 'created_at': time.time(),
              'protected_children': [], 'children_incomplete': False,
              'session_hint': {'id': chat, 'source': 'environment-declared'}}
    runner = {'owner': 'currency-wars-runner', 'chat_id': chat, 'artifact_chat_id': chat,
              'run_id': request['run_id'], 'run_token': token, 'runner_pid': request['worker_pid'],
              'runner_creation_id': request['worker_creation_id'], 'launch_id': request['launch_id']}
    owner = {'owner': 'currency-wars-control', 'chat_id': chat, 'run_token': token,
             'artifact_run_id': request['run_id'], 'artifact_chat_id': chat}
    return marker, runner, owner


def medium_facts(config=None, *, impersonation=False):
    config = config or config_value()
    return {'user_sid': config['user_sid'], 'integrity_rid': bridge.MEDIUM_RID,
            'session_id': 1, 'authentication_id': (100, 0), 'elevation_type': 3,
            'token_type': 2 if impersonation else 1, 'impersonation_level': 2 if impersonation else 0}


class FakeAPI:
    """Simulates token APIs only; test filesystem stays at the real low token."""
    def __init__(self, config, request):
        self.config, self.request = config, request
        high = dict(medium_facts(config), integrity_rid=bridge.HIGH_RID, elevation_type=2,
                    authentication_id=(101, 0))
        identification = dict(medium_facts(config), token_type=2, impersonation_level=1)
        self.tokens = {1: high, 2: identification, 3: medium_facts(config), 4: high}
        self.thread, self.closed, self.transitions, self.linked_calls = None, [], [], 0
        self.restriction_sources = []
        self.fail_impersonation = False
        self.worker_created = int(request['worker_creation_id'])

    def current_token(self):
        return 1

    def facts(self, token):
        return dict(self.tokens[token])

    def linked_medium_token(self, token):
        self.linked_calls += 1
        if self.tokens[token]['integrity_rid'] != bridge.HIGH_RID:
            raise AssertionError('No low-to-high linked lookup')
        return 2

    def duplicate_medium(self, token):
        if self.tokens[token]['integrity_rid'] != bridge.MEDIUM_RID:
            raise AssertionError('High tokens must never be duplicated')
        if self.tokens[token]['token_type'] == 2 and self.tokens[token]['impersonation_level'] < 2:
            raise AssertionError('Identification token must never be promoted')
        duplicate = token + 100
        self.tokens[duplicate] = dict(self.tokens[token], token_type=2, impersonation_level=2)
        return duplicate

    def restricted_medium(self, source):
        self.restriction_sources.append(source)
        self.tokens[5] = dict(self.tokens[source], integrity_rid=bridge.MEDIUM_RID)
        return 5

    def thread_facts(self):
        return self.facts(self.thread) if self.thread is not None else None

    def impersonate(self, token):
        if self.fail_impersonation:
            raise OSError('simulated failure')
        self.thread = token
        self.transitions.append(('medium', token))

    def revert(self):
        self.thread = None
        self.transitions.append(('original', None))

    def process(self, pid):
        return 10000 + pid

    def process_token(self, handle):
        return 3 if handle == 10000 + self.request['worker_pid'] else 4

    def snapshot(self, handle, pid):
        created = self.worker_created if pid == self.request['worker_pid'] else 7654321
        if pid == 999:
            created = 2345678
        return {'pid': pid, 'creation_id': created, 'state': 'running', 'exit_code': 259}

    def session_id(self, pid):
        return 1

    def image_path(self, handle):
        return self.config['game_path']

    def close(self, handle):
        self.closed.append(handle)


class ProtocolTests(unittest.TestCase):
    def test_valid_fixed_protocol_and_config(self):
        config, request = config_value(), request_value()
        self.assertIs(bridge.validate_config(config), config)
        self.assertIs(bridge.validate_request(request, config), request)
        owners = owner_values(request, config)
        self.assertIs(bridge.validate_owners(request, config, *owners), owners[2])

    def test_request_rejects_bad_mode_expiry_identity_and_paths(self):
        values = [('kind', 'script'), ('kind', 'cmd'), ('worker_pid', True),
                  ('worker_creation_id', '0'), ('worker_creation_id', '123.0'),
                  ('request_id', '../cmd'), ('run_id', 'A' * 32), ('launch_id', '0' * 31),
                  ('installation_id', 'f' * 32), ('schema', True), ('run_token', 'secret'),
                  ('run_dir', bridge.RUNTIME_ROOT + r'\nested\currency-wars-runner-x'),
                  ('run_dir', bridge.RUNTIME_ROOT + r'\..\currency-wars-runner-x'),
                  ('run_dir', bridge.RUNTIME_ROOT + r'\currency-wars-runner-x:stream'),
                  ('run_dir', r'\\?\D:\Codex\Temp\codex-agent-workflow\currency-wars-runner-x'),
                  ('run_dir', r'C:\Temp\currency-wars-runner-x'),
                  ('expires_at', '2020-01-01T00:00:00+00:00'),
                  ('expires_at', datetime.fromtimestamp(time.time() + 60, timezone.utc).isoformat()),
                  ('expires_at', '2026-01-01T00:00:00'), ('expires_at', '2026-01-01T00:00:00+08:00')]
        for key, value in values:
            with self.subTest(key=key, value=value):
                request = request_value()
                request[key] = value
                with self.assertRaises(bridge.BridgeError):
                    bridge.validate_request(request, config_value())
        request = request_value()
        request['script_path'] = r'C:\evil.py'
        with self.assertRaises(bridge.BridgeError):
            bridge.validate_request(request, config_value())

    def test_config_rejects_unapproved_source_and_arbitrary_roots(self):
        for key, value in [('broker_sha256', 'F' * 64), ('driver_sha256', 'not-a-hash'),
                           ('user_sid', 'SYSTEM'), ('runtime_root', r'D:\Other\Temp'),
                           ('inbox', r'D:\Other\inbox'), ('game_path', 'StarRail.exe')]:
            config = config_value()
            config[key] = value
            with self.subTest(key=key), self.assertRaises(bridge.BridgeError):
                bridge.validate_config(config)

    def test_owner_fields_all_match_actual_launch(self):
        config, request = config_value(), request_value()
        originals = owner_values(request, config)
        cases = [(0, 'schema', True), (0, 'tool', 'other'), (0, 'run_id', '8' * 32),
                 (0, 'path', r'D:\Other\run'), (0, 'root', r'D:\Other'),
                 (0, 'purpose', 'ordinary-test'), (0, 'pid', 778),
                 (0, 'process_identity', 'windows:999'), (0, 'created_at', float('nan')),
                 (0, 'session_hint', {'id': 'other', 'source': 'environment-declared'}),
                 (1, 'runner_pid', 778), (1, 'runner_creation_id', '1234568'),
                 (1, 'owner', 'other'), (1, 'launch_id', '6' * 32), (1, 'run_token', '6' * 48),
                 (1, 'artifact_chat_id', 'other'), (2, 'owner', 'other'),
                 (2, 'artifact_run_id', '7' * 32), (2, 'chat_id', 'other'), (2, 'run_token', '6' * 48)]
        for index, key, value in cases:
            owners = copy.deepcopy(originals)
            owners[index][key] = value
            with self.subTest(index=index, key=key), self.assertRaises(bridge.BridgeError):
                bridge.validate_owners(request, config, *owners)

    def test_real_token_identity_is_required(self):
        config, original = config_value(), medium_facts()
        bridge.validate_identity(original, config)
        for key, value in [('user_sid', 'S-1-5-18'), ('integrity_rid', bridge.HIGH_RID),
                           ('session_id', 0), ('authentication_id', (200, 0))]:
            altered = dict(original, **{key: value})
            with self.subTest(key=key), self.assertRaises(bridge.BridgeError):
                bridge.validate_identity(altered, config, reference=original)
        with self.assertRaises(bridge.BridgeError):
            bridge.validate_identity(original, config, high=True)
        with self.assertRaises(bridge.BridgeError):
            bridge.validate_identity(dict(original, token_type=2, impersonation_level=1), config, impersonation=True)

    def test_json_duplicate_keys_constants_size_and_non_objects_are_rejected(self):
        for data in (b'{"schema":1,"schema":1}', b'{"x":NaN}', b'[]', b'{broken', b'\xff'):
            with self.subTest(data=data), self.assertRaises(bridge.BridgeError):
                bridge.parse_json(data)
        with artifacts.scratch_directory('currency-wars-bridge-size-test', root=TEST_ROOT) as owned:
            path = owned / 'too-large.json'
            path.write_bytes(b'x' * 100)
            with self.assertRaises(bridge.BridgeError):
                bridge.read_bytes(path, 10)

    def test_self_test_and_cli_never_get_linked_token_or_load_control(self):
        with patch.object(bridge.WindowsAPI, 'linked_medium_token', side_effect=AssertionError('No linked token')), \
             patch.object(bridge, 'load_control', side_effect=AssertionError('No controller')):
            output = io.StringIO()
            with patch('sys.stdout', output):
                self.assertEqual(bridge.main(['--self-test']), 0)
            value = json.loads(output.getvalue())
            self.assertTrue(value['no_input'])
            self.assertFalse(value['task_started'])
            self.assertFalse(value['protected_installation_verified'])
            for argv in (['serve'], ['--run-dir', r'D:\evil'], ['--self-test', 'script.py']):
                with patch('sys.stdout', io.StringIO()):
                    self.assertEqual(bridge.main(argv), 2)


class FilesystemTests(unittest.TestCase):
    def test_every_path_call_and_lazy_scan_uses_medium_then_reverts(self):
        with artifacts.scratch_directory('currency-wars-bridge-fs-test', root=TEST_ROOT) as owned:
            api = FakeAPI(config_value(), request_value())
            token = api.duplicate_medium(3)
            guard = bridge.FilesystemGuard(api, token, api.facts(token))
            originals = (builtins.open, io.open, os.open, os.stat, os.scandir)
            guard.install()
            try:
                path = owned / 'data.txt'
                path.write_text('medium', encoding='utf-8')
                self.assertIsNone(api.thread)
                with builtins.open(path, 'a', encoding='utf-8') as stream:
                    self.assertIsNone(api.thread)  # Low-opened handle may be used later.
                    stream.write('-handle')
                self.assertEqual(path.read_text(encoding='utf-8'), 'medium-handle')
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 0)
                with os.scandir(owned) as entries:
                    data = next(entry for entry in entries if entry.name == 'data.txt')
                    self.assertTrue(data.is_file())
                    self.assertGreater(data.stat().st_size, 0)
                os.replace(path, owned / 'renamed.txt')
                (owned / 'renamed.txt').unlink()
                self.assertIsNone(api.thread)
                self.assertGreater(len(api.transitions), 16)
                self.assertTrue(all(api.transitions[i][0] == 'medium' and api.transitions[i + 1][0] == 'original'
                                    for i in range(0, len(api.transitions), 2)))
            finally:
                guard.restore()
            self.assertEqual((builtins.open, io.open, os.open, os.stat, os.scandir), originals)

    def test_exception_nested_scope_and_impersonation_failure_do_not_fall_back(self):
        api = FakeAPI(config_value(), request_value())
        token = api.duplicate_medium(3)
        guard = bridge.FilesystemGuard(api, token, api.facts(token))
        with self.assertRaises(PermissionError):
            with guard.scope():
                with guard.scope():
                    self.assertEqual(api.thread, token)
                    raise PermissionError('low deny')
        self.assertEqual(api.transitions, [('medium', token), ('original', None)])
        self.assertIsNone(api.thread)
        api.fail_impersonation = True
        reached = []
        with self.assertRaises(OSError):
            with guard.scope():
                reached.append('unsafe operation')
        self.assertEqual(reached, [])
        self.assertIsNone(api.thread)

    def test_reparse_detection_is_a_medium_call(self):
        api = FakeAPI(config_value(), request_value())
        token = api.duplicate_medium(3)
        guard = bridge.FilesystemGuard(api, token, api.facts(token))
        observed = []
        def reparse_stat(path, **kwargs):
            observed.append(api.thread)
            return SimpleNamespace(st_mode=0o040000, st_file_attributes=0x400)
        with patch.object(os, 'lstat', side_effect=reparse_stat):
            guard.install()
            try:
                with self.assertRaisesRegex(bridge.BridgeError, 'reparse_path_denied'):
                    bridge.no_reparse(r'D:\Codex\Temp\codex-agent-workflow\currency-wars-runner-link')
                self.assertEqual(observed, [token])
                self.assertIsNone(api.thread)
            finally:
                guard.restore()

    @unittest.skipUnless(os.name == 'nt', 'Windows medium token APIs')
    def test_real_medium_token_filesystem_scope_restores_original_thread(self):
        # This test duplicates only the already-medium process token. It never
        # retrieves a linked token or obtains/uses a high token.
        api = bridge.WindowsAPI()
        token, restricted, impersonation = api.current_token(), None, None
        try:
            facts = api.facts(token)
            if facts['integrity_rid'] != bridge.MEDIUM_RID:
                self.skipTest('requires the ordinary medium test interpreter')
            self.assertIsNone(api.thread_facts())
            # Exercise the production reduction route from an already-medium
            # primary. It cannot grant a higher RID or fetch a linked token.
            restricted = api.restricted_medium(token)
            reduced = api.facts(restricted)
            self.assertEqual(reduced['integrity_rid'], bridge.MEDIUM_RID)
            self.assertEqual(reduced['token_type'], 1)
            for key in ('user_sid', 'session_id', 'authentication_id'):
                self.assertEqual(reduced[key], facts[key])
            access = api.restricted_security(restricted)
            self.assertTrue(not access['administrators_present'] or access['administrators_deny_only'])
            self.assertTrue(set(access['remaining_privileges']) <= {'SeChangeNotifyPrivilege'})
            impersonation = api.duplicate_medium(restricted)
            guard = bridge.FilesystemGuard(api, impersonation, api.facts(impersonation))
            with artifacts.scratch_directory('currency-wars-bridge-real-token-test', root=TEST_ROOT) as owned:
                guard.install()
                try:
                    with guard.scope():
                        self.assertEqual(api.thread_facts()['integrity_rid'], bridge.MEDIUM_RID)
                        (owned / 'medium.txt').write_text('real medium access', encoding='utf-8')
                    self.assertIsNone(api.thread_facts())
                    self.assertEqual((owned / 'medium.txt').read_text(encoding='utf-8'), 'real medium access')
                    self.assertIsNone(api.thread_facts())
                    with self.assertRaises(FileNotFoundError):
                        (owned / 'absent.txt').read_text(encoding='utf-8')
                    self.assertIsNone(api.thread_facts())
                    self.assertEqual(api.facts(token)['integrity_rid'], bridge.MEDIUM_RID)
                finally:
                    guard.restore()
        finally:
            if impersonation:
                api.close(impersonation)
            if restricted:
                api.close(restricted)
            api.close(token)

    @unittest.skipUnless(os.name == 'nt', 'Windows filesystem access checks')
    def test_real_medium_handle_open_denies_retargeted_write_without_retry(self):
        api, token, impersonation = bridge.WindowsAPI(), None, None
        token = api.current_token()
        try:
            facts = api.facts(token)
            if facts['integrity_rid'] != bridge.MEDIUM_RID:
                self.skipTest('requires the ordinary medium test interpreter')
            impersonation = api.duplicate_medium(token)
            with artifacts.scratch_directory('currency-wars-bridge-access-test', root=TEST_ROOT) as owned:
                target, alias = owned / 'denied.txt', owned / 'checked-before-open.txt'
                target.write_text('fixture', encoding='utf-8')
                # Only this owned fixture gets a temporary DACL denying write
                # data/append/EA/attributes to our SID; no installed files or
                # privilege settings are changed.
                C, W, security, kernel = bridge.C, bridge.W, api.a, api.k
                security.GetNamedSecurityInfoW.argtypes = [W.LPWSTR, C.c_int, W.DWORD,
                    W.LPVOID, W.LPVOID, C.POINTER(W.LPVOID), W.LPVOID, C.POINTER(W.LPVOID)]
                security.GetNamedSecurityInfoW.restype = W.DWORD
                security.SetNamedSecurityInfoW.argtypes = [W.LPWSTR, C.c_int, W.DWORD,
                    W.LPVOID, W.LPVOID, W.LPVOID, W.LPVOID]
                security.SetNamedSecurityInfoW.restype = W.DWORD
                security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [W.LPCWSTR, W.DWORD,
                    C.POINTER(W.LPVOID), W.LPVOID]
                security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = W.BOOL
                security.GetSecurityDescriptorDacl.argtypes = [W.LPVOID, C.POINTER(W.BOOL), C.POINTER(W.LPVOID), C.POINTER(W.BOOL)]
                security.GetSecurityDescriptorDacl.restype = W.BOOL
                saved, old_acl, temporary, new_acl = W.LPVOID(), W.LPVOID(), W.LPVOID(), W.LPVOID()
                path_buffer = C.create_unicode_buffer(str(target))
                code = security.GetNamedSecurityInfoW(path_buffer, 1, 4, None, None, C.byref(old_acl), None, C.byref(saved))
                self.assertEqual(code, 0)
                try:
                    api.checked(security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                        'D:(D;;0x116;;;' + facts['user_sid'] + ')(A;;FA;;;' + facts['user_sid'] + ')',
                        1, C.byref(temporary), None))
                    present, defaulted = W.BOOL(), W.BOOL()
                    api.checked(security.GetSecurityDescriptorDacl(temporary, C.byref(present), C.byref(new_acl), C.byref(defaulted)))
                    self.assertTrue(present.value)
                    self.assertEqual(security.SetNamedSecurityInfoW(path_buffer, 1, 4, None, None, new_acl, None), 0)
                    original_open, attempts = builtins.open, []
                    def redirected_open(path, *args, **kwargs):
                        if Path(path) == alias:
                            attempts.append(api.thread_facts()['integrity_rid'])
                            return original_open(target, *args, **kwargs)
                        return original_open(path, *args, **kwargs)
                    # Simulate a path changing after validation: the OPEN must
                    # use the medium token even when its destination changes.
                    with patch.object(builtins, 'open', redirected_open):
                        guard = bridge.FilesystemGuard(api, impersonation, api.facts(impersonation))
                        guard.install()
                        try:
                            with self.assertRaises(PermissionError):
                                with builtins.open(alias, 'w', encoding='utf-8') as stream:
                                    stream.write('must not reach')
                            self.assertEqual(attempts, [bridge.MEDIUM_RID])
                            self.assertIsNone(api.thread_facts())
                            self.assertEqual(target.read_text(encoding='utf-8'), 'fixture')
                        finally:
                            guard.restore()
                finally:
                    self.assertEqual(security.SetNamedSecurityInfoW(path_buffer, 1, 4, None, None, old_acl, None), 0)
                    if temporary:
                        kernel.LocalFree(temporary)
                    if saved:
                        kernel.LocalFree(saved)
        finally:
            if impersonation:
                api.close(impersonation)
            if token:
                api.close(token)


class StartupTests(unittest.TestCase):
    def exercise(self, owned, *, request_change=None, owner_change=None, api_change=None, serve=False,
                 stop=False, serve_error=False, client_rect=None, actual_rect=None):
        config, request = config_value(), request_value()
        if serve:
            request['kind'] = 'serve'
        if request_change:
            request.update(request_change)
        inbox, run, install = owned / 'inbox', owned / 'run', owned / 'install'
        for folder in (inbox, run, install / 'code'):
            folder.mkdir(parents=True)
        config['inbox'] = str(inbox)
        marker, runner, owner = owner_values(request, config)
        if owner_change:
            target, key, value = owner_change
            (marker, runner, owner)[target][key] = value
        for name, value in ((bridge.MARKER, marker), ('runner-owner.json', runner), ('owner.json', owner)):
            (run / name).write_text(json.dumps(value), encoding='utf-8')
        (run / 'manual-pause.json').write_text('{"reason":"initial pause"}', encoding='utf-8')
        binding = {'pid': 999, 'creation_id': 2345678, 'hwnd': 333,
                   'rect': list(client_rect if client_rect is not None else (0, 0, 1920, 1080))}
        (run / 'binding.json').write_text(json.dumps(binding), encoding='utf-8')
        if stop:
            (run / 'broker-stop').touch()
        (inbox / 'launch.json').write_text(json.dumps(request), encoding='utf-8')
        control_source = Path(bridge.__file__).with_name('currency_wars_control.py').read_bytes()
        (install / 'code' / 'currency_wars_control.py').write_bytes(control_source)
        game = owned / 'fixed-game-image.exe'
        game.write_bytes(b'offline fixture; never executed')
        config['game_path'] = str(game)
        config['game_sha256'] = hashlib.sha256(game.read_bytes()).hexdigest()
        api = FakeAPI(config, request)
        if api_change:
            api_change(api)
        calls = []
        def fake_serve():
            # The original controller is never imported or executed here.
            calls.append('fixed serve')
            identity = json.loads((run / 'broker-process.json').read_text(encoding='utf-8'))
            self.assertEqual(identity['pid'], os.getpid())
            self.assertEqual(identity['creation_id'], 7654321)
            self.assertEqual(identity['protocol_version'], 2)
            self.assertTrue((run / 'bridge-accepted.json').exists())
            self.assertTrue((run / 'manual-pause.json').exists())
            self.assertIsNone(api.thread)
            if serve_error:
                raise RuntimeError(request['run_token'])
        observed_rect = tuple(actual_rect if actual_rect is not None else binding['rect'])
        control = SimpleNamespace(PROTOCOL_VERSION=2, win=lambda: (333, 999, observed_rect), serve=fake_serve)
        with patch.object(bridge, 'validate_run_directory', return_value=run), \
             patch.object(bridge, 'protected_write_denied', return_value=True), \
             patch.object(bridge, 'load_control', return_value=control) as load:
            code = bridge.execute_task(api, config, str(install))
        self.assertIsNone(api.thread)
        self.assertEqual(len(api.closed), len(set(api.closed)))
        self.assertEqual(api.linked_calls, 1 if api.tokens[1]['user_sid'] == config['user_sid'] else 0)
        return code, inbox, run, api, calls, load.call_count, request

    def test_probe_returns_high_and_medium_facts_without_loading_game_code(self):
        with artifacts.scratch_directory('currency-wars-bridge-probe-test', root=TEST_ROOT) as owned:
            code, inbox, run, api, calls, loads, request = self.exercise(owned)
            self.assertEqual(code, 0)
            result = json.loads((inbox / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(result['request_id'], request['request_id'])
            self.assertEqual(result['process_integrity_rid'], bridge.HIGH_RID)
            self.assertEqual(result['filesystem_integrity_rid'], bridge.MEDIUM_RID)
            self.assertTrue(result['protected_write_denied'])
            self.assertEqual((calls, loads), ([], 0))
            self.assertEqual(api.tokens[2]['token_type'], 2)
            self.assertEqual(api.tokens[2]['impersonation_level'], 1)
            self.assertNotIn(102, api.closed)  # The linked identification token was never duplicated.
            self.assertEqual(api.restriction_sources, [1])
            self.assertNotEqual(api.tokens[1]['authentication_id'], api.tokens[2]['authentication_id'])
            self.assertFalse((run / 'broker-process.json').exists())
            self.assertFalse((run / 'bridge-exit.json').exists())
            self.assertFalse((inbox / 'launch.json').exists())

    def test_initial_protected_write_proof_precedes_any_inbox_read(self):
        with artifacts.scratch_directory('currency-wars-bridge-early-proof-test', root=TEST_ROOT) as owned:
            config, request = config_value(), request_value()
            inbox = owned / 'inbox'
            inbox.mkdir()
            config['inbox'] = str(inbox)
            api, diagnostic = FakeAPI(config, request), io.StringIO()
            report = bridge.status_reporter(diagnostic, api.snapshot(api.process(os.getpid()), os.getpid()))
            with patch.object(bridge, 'protected_write_denied', side_effect=bridge.BridgeError('protected_filesystem_writable')), \
                 patch.object(bridge, 'read_bytes', side_effect=AssertionError('No caller read before proof')) as read:
                self.assertEqual(bridge.execute_task(api, config, status=report), 2)
            read.assert_not_called()
            value = json.loads(diagnostic.getvalue())
            self.assertEqual(value['stage'], 'initial_protected_write')
            self.assertEqual(value['error'], 'protected_filesystem_writable')
            self.assertEqual(set(value), {'schema', 'pid', 'creation_id', 'stage', 'ok', 'time', 'error'})
            self.assertNotIn(request['run_token'], diagnostic.getvalue())
            self.assertIsNone(api.thread)

    def test_wrong_task_sid_rejects_before_linked_token_and_caller_filesystem(self):
        config, request = config_value(), request_value()
        api = FakeAPI(config, request)
        api.tokens[1]['user_sid'] = 'S-1-5-18'
        with patch.object(bridge, 'read_bytes', side_effect=AssertionError('No caller read')) as read:
            self.assertEqual(bridge.execute_task(api, config), 2)
        read.assert_not_called()
        self.assertEqual(api.linked_calls, 0)
        self.assertEqual(api.closed, [1])

    def test_stop_is_rejected_before_control_load_and_identity_publication(self):
        with artifacts.scratch_directory('currency-wars-bridge-stop-test', root=TEST_ROOT) as owned:
            code, inbox, run, unused, calls, loads, request = self.exercise(owned, serve=True, stop=True)
            self.assertEqual(code, 2)
            self.assertEqual((calls, loads), ([], 0))
            self.assertFalse((run / 'broker-process.json').exists())
            self.assertEqual(json.loads((run / 'broker-start-error.json').read_text())['error'], 'run_already_stopped')

    def test_invalid_owner_never_writes_failure_into_unverified_run(self):
        with artifacts.scratch_directory('currency-wars-bridge-owner-test', root=TEST_ROOT) as owned:
            code, inbox, run, unused, calls, loads, request = self.exercise(owned, owner_change=(1, 'runner_pid', 778))
            self.assertEqual(code, 2)
            self.assertFalse((run / 'broker-start-error.json').exists())
            result = (inbox / 'result.json').read_text(encoding='utf-8')
            self.assertNotIn(request['run_token'], result)
            self.assertEqual(json.loads(result)['request_id'], request['request_id'])
            self.assertEqual((calls, loads), ([], 0))

    def test_wrong_real_worker_creation_sid_or_logon_is_rejected(self):
        changes = [lambda api: setattr(api, 'worker_created', 1234568),
                   lambda api: api.tokens[3].update(user_sid='S-1-5-18'),
                   lambda api: api.tokens[3].update(authentication_id=(101, 0)),
                   lambda api: api.tokens[3].update(integrity_rid=bridge.HIGH_RID)]
        for index, change in enumerate(changes):
            with self.subTest(index=index), artifacts.scratch_directory('currency-wars-bridge-worker-test', root=TEST_ROOT) as owned:
                code, inbox, run, unused, calls, loads, request = self.exercise(owned, api_change=change)
                self.assertEqual(code, 2)
                self.assertFalse((run / 'broker-start-error.json').exists())
                self.assertEqual((calls, loads), ([], 0))
                self.assertNotIn(request['run_token'], (inbox / 'result.json').read_text())

    def test_serve_identity_is_published_before_fixed_callback_and_error_is_redacted(self):
        with artifacts.scratch_directory('currency-wars-bridge-accept-test', root=TEST_ROOT) as owned:
            code, inbox, run, unused, calls, loads, request = self.exercise(owned, serve=True, serve_error=True)
            self.assertEqual(code, 2)
            self.assertEqual((calls, loads), (['fixed serve'], 1))
            result = (inbox / 'result.json').read_text(encoding='utf-8')
            self.assertNotIn(request['run_token'], result)
            self.assertNotIn(request['run_token'], (run / 'broker-start-error.json').read_text())
            self.assertFalse(json.loads((run / 'bridge-exit.json').read_text())['process_exit_confirmed'])

    def test_4k_game_client_can_start_with_the_same_verified_binding(self):
        with artifacts.scratch_directory('currency-wars-bridge-4k-test', root=TEST_ROOT) as owned:
            code, inbox, run, unused, calls, loads, request = self.exercise(
                owned, serve=True, client_rect=(-3840, 100, 0, 2260))
            self.assertEqual(code, 0)
            self.assertEqual(calls, ['fixed serve'])
            self.assertTrue((run / 'bridge-accepted.json').exists())

    def test_unsupported_or_changed_game_client_never_starts_control(self):
        cases = [((0, 0, 2560, 1440), None, 'binding_invalid'),
                 ((0, 0, 3840, 1080), None, 'binding_invalid'),
                 ((0, 0, 3840, 2160), (0, 0, 1920, 1080), 'game_window_mismatch'),
                 ((0, 0, 3840, 2160), (10, 0, 3850, 2160), 'game_window_mismatch')]
        for rect, actual, error in cases:
            with self.subTest(rect=rect, actual=actual), artifacts.scratch_directory(
                    'currency-wars-bridge-client-test', root=TEST_ROOT) as owned:
                code, inbox, run, unused, calls, loads, request = self.exercise(
                    owned, serve=True, client_rect=rect, actual_rect=actual)
                self.assertEqual(code, 2)
                self.assertEqual(calls, [])
                self.assertFalse((run / 'broker-process.json').exists())
                self.assertEqual(json.loads((run / 'broker-start-error.json').read_text())['error'], error)

    def test_unprotected_directory_and_source_mutation_are_rejected(self):
        with artifacts.scratch_directory('currency-wars-bridge-source-test', root=TEST_ROOT) as owned:
            for folder in ('python', 'modules', 'code'):
                (owned / folder).mkdir()
            with self.assertRaisesRegex(bridge.BridgeError, 'protected_filesystem_writable'):
                bridge.protected_write_denied(str(owned))
            source = owned / 'code' / 'currency_wars_control.py'
            source.write_text('print("arbitrary code")', encoding='utf-8')
            with self.assertRaisesRegex(bridge.BridgeError, 'broker_source_hash_mismatch'):
                bridge.verified_broker_source(str(owned), config_value())


class ResumeTests(unittest.TestCase):
    """Run only the pinned resume functions, with every input/window API mocked."""
    def control(self, owned):
        data = Path(bridge.__file__).with_name('currency_wars_control.py').read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest().upper(), bridge.PINNED_BROKER_SHA256)
        tree = ast.parse(data)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in ('execute_resume', 'execute_request', 'attach_observation')]
        self.assertEqual({node.name for node in functions}, {'execute_resume', 'execute_request', 'attach_observation'})
        control = types.ModuleType('offline_pinned_resume')
        control.__dict__.update(ROOT=str(owned), OWNER={'chat_id': 'test-chat', 'run_token': 'a' * 48},
            BINDING={'hwnd': 333}, Path=Path, time=time, SEEN_RESUME_IDS=set(),
            RESUMING=False, RESUME_PAUSE_ID=None, BATCH_DEADLINE=None,
            win=Mock(return_value=(333, 999, (0, 0, 1920, 1080))), handoff_focus=Mock(),
            focus=Mock(), observe=Mock(return_value={'snapshot': 'mocked'}), input_guard=Mock(),
            acknowledge_pause=Mock(), execute_batch=Mock(return_value={'ok': True}),
            u=SimpleNamespace(GetForegroundWindow=Mock(return_value=333)),
            control_state_lock=contextlib.nullcontext)
        def assert_owner(chat, token):
            self.assertEqual((chat, token), ('test-chat', 'a' * 48))
        def read_optional(name):
            path = owned / name
            return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        control.assert_owner, control.read_optional, control.write_json = assert_owner, read_optional, bridge.write_json
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<pinned resume with mocked input>', 'exec'), control.__dict__)
        pause = {'pause_id': 'b' * 32, 'chat_id': 'test-chat', 'run_token': 'a' * 48, 'reason': 'initial manual pause'}
        bridge.write_json(owned / 'manual-pause.json', pause)
        request = {'id': 'c' * 32, 'kind': 'resume', 'chat_id': 'test-chat', 'run_token': 'a' * 48,
                   'handoff': True, 'expected_pause_id': pause['pause_id']}
        original_read = control.read_optional
        bridge.install_expected_resume(control)
        return control, request, original_read

    def test_missing_or_malformed_expected_pause_never_hands_off(self):
        with artifacts.scratch_directory('currency-wars-bridge-resume-test', root=TEST_ROOT) as owned:
            control, request, original_read = self.control(owned)
            for expected in ('missing', 123):
                altered = dict(request)
                if expected == 'missing':
                    del altered['expected_pause_id']
                else:
                    altered['expected_pause_id'] = expected
                with self.subTest(expected=expected), self.assertRaises(ValueError):
                    control.execute_request(altered)
                control.handoff_focus.assert_not_called()
                self.assertTrue((owned / 'manual-pause.json').exists())
                self.assertIs(control.read_optional, original_read)

    def test_new_pause_before_original_read_wins_without_handoff(self):
        with artifacts.scratch_directory('currency-wars-bridge-resume-old-test', root=TEST_ROOT) as owned:
            control, request, original_read = self.control(owned)
            newer = {'pause_id': 'd' * 32, 'chat_id': 'test-chat', 'run_token': 'a' * 48, 'reason': 'new user pause'}
            bridge.write_json(owned / 'manual-pause.json', newer)
            with self.assertRaisesRegex(RuntimeError, 'new pause before original resume read'):
                control.execute_request(request)
            control.handoff_focus.assert_not_called()
            self.assertEqual(json.loads((owned / 'manual-pause.json').read_text())['pause_id'], newer['pause_id'])
            self.assertIs(control.read_optional, original_read)

    def test_new_pause_during_resume_remains_latched(self):
        with artifacts.scratch_directory('currency-wars-bridge-resume-new-test', root=TEST_ROOT) as owned:
            control, request, original_read = self.control(owned)
            newer = {'pause_id': 'd' * 32, 'chat_id': 'test-chat', 'run_token': 'a' * 48, 'reason': 'new user pause'}
            control.handoff_focus.side_effect = lambda unused: bridge.write_json(owned / 'manual-pause.json', newer)
            result = control.execute_request(request)
            self.assertFalse(result['ok'])
            self.assertFalse(result['resumed'])
            control.handoff_focus.assert_called_once_with(333)
            self.assertEqual(json.loads((owned / 'manual-pause.json').read_text())['pause_id'], newer['pause_id'])
            self.assertFalse(control.RESUMING)
            self.assertIs(control.read_optional, original_read)

    def test_approved_resume_is_single_use(self):
        with artifacts.scratch_directory('currency-wars-bridge-resume-once-test', root=TEST_ROOT) as owned:
            control, request, original_read = self.control(owned)
            result = control.execute_request(request)
            self.assertTrue(result['ok'])
            self.assertTrue(result['resumed'])
            self.assertEqual(result['completed'], [])
            self.assertFalse((owned / 'manual-pause.json').exists())
            with self.assertRaisesRegex(ValueError, 'duplicate resume'):
                control.execute_request(dict(request, expected_pause_id=None))
            control.handoff_focus.assert_called_once_with(333)
            control.execute_batch.assert_not_called()
            self.assertIs(control.read_optional, original_read)


if __name__ == '__main__':
    unittest.main(verbosity=2)
