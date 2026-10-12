"""Read-only client identity and credential boundary regressions."""
import os
import io
import logging
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import currency_wars_session_client as client


class SessionClientBoundaries(unittest.TestCase):
    def test_sdk_validation_exception_never_reaches_console_logger(self):
        console, private = io.StringIO(), io.StringIO()
        root = logging.getLogger()
        handler = logging.StreamHandler(console)
        root.addHandler(handler)
        sdk = logging.getLogger('mcp.client.stdio')
        original = (sdk.handlers[:], sdk.propagate)
        try:
            with client.sdk_diagnostics(private):
                try:
                    raise ValueError('credential-fragment-in-invalid-jsonrpc')
                except ValueError:
                    sdk.exception('Failed to parse server message')
            self.assertEqual(console.getvalue(), '')
            self.assertIn('credential-fragment-in-invalid-jsonrpc', private.getvalue())
            self.assertEqual((sdk.handlers, sdk.propagate), original)
        finally:
            root.removeHandler(handler)
            handler.close()

    def test_unbound_client_never_inherits_an_old_session(self):
        with patch.dict(os.environ, {key: 'old-binding' for key in client.BINDING_KEYS}):
            result = client.session_environment()
        self.assertTrue(all(key not in result for key in client.BINDING_KEYS))
        with self.assertRaises(ValueError):
            client.session_environment(chat_id='root')

    def owner_read(self, change=None, probe=('active', 'windows:123')):
        marker = {'pid': 41, 'run_id': 'owned', 'process_identity': 'windows:123',
                  'session_hint': {'id': 'artifact-root'}}
        owner = {'owner': 'currency-wars-runner', 'chat_id': 'root', 'run_id': 'owned',
                 'runner_pid': 41, 'runner_creation_id': '123',
                 'artifact_chat_id': 'artifact-root', 'run_token': 'f' * 48}
        owner.update(change or {})
        with (patch.object(client.artifacts, 'read_marker', return_value=marker),
              patch.object(client.artifacts, 'process_identity', return_value=probe),
              patch.object(Path, 'lstat', return_value=Mock(st_mode=0o100600, st_size=400,
                                                           st_file_attributes=0)),
              patch.object(Path, 'read_text', return_value=__import__('json').dumps(owner))):
            return client.session_environment(Path('C:/explicit-owned-run'), 'root')

    def test_explicit_owner_must_match_chat_marker_and_live_creation(self):
        for changes in ({'chat_id': 'another-root'}, {'run_id': 'different-run'},
                        {'runner_pid': 42}, {'runner_creation_id': '124'},
                        {'artifact_chat_id': 'another-artifact'}, {'run_token': ''}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.owner_read(changes)
        for probe in (('dead', None), ('unknown', None), ('active', 'windows:124')):
            with self.subTest(probe=probe), self.assertRaises(ValueError):
                self.owner_read(probe=probe)
        result = self.owner_read()
        self.assertEqual(result['CURRENCY_WARS_CHAT_ID'], 'root')
        self.assertEqual(result['CURRENCY_WARS_RUN_TOKEN'], 'f' * 48)

    def test_output_removes_credential_keys_and_known_credential_strings(self):
        value = {'status': 'unavailable', 'run_token': 'private',
                 'nested': [{'gui_token': 'gui-secret', 'owner_token': 'owner-secret',
                             'reason': 'contains private / gui-secret / owner-secret'}]}
        self.assertEqual(client.redacted(value, 'private'),
                         {'status': 'unavailable', 'nested': [
                             {'reason': 'contains [redacted] / [redacted] / [redacted]'}]})

    def test_summary_preserves_unknown_identity_freshness_and_plan_status(self):
        value = {'status': 'needs_review', 'binding': {'snapshot_id': 'current'},
                 'freshness': {'captured_at': None}, 'unknown': ['overflow'],
                 'fields': {'xp_gain': {'value': None, 'known': False}},
                 'semantic': {'team': {'checked': False, 'unknown_slots': [{'slot': 1}],
                                      'units': [{'name': None, 'star': None, 'row': 'bench', 'slot': 1}]}},
                 'solver_input': {'private_static_payload': 'large'}}
        result = client.compact(value)
        self.assertEqual(result['binding'], value['binding'])
        self.assertEqual(result['unknown'], ['overflow'])
        self.assertIsNone(result['freshness']['captured_at'])
        self.assertFalse(result['fields']['xp_gain']['known'])
        self.assertIsNone(result['team']['units'][0]['name'])
        self.assertNotIn('solver_input', result)
        self.assertTrue(result['execution_capability'].startswith('none'))


if __name__ == '__main__':
    unittest.main()
