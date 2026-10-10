"""Public safety boundaries of the optional adapter; no game/SDK required."""
import asyncio
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from currency_wars_mcp import Adapter, PROJECT


class MCPBoundaries(unittest.TestCase):
    def test_import_does_not_load_game_input_layer(self):
        code = ('import sys; import currency_wars_mcp; '
                'assert not any(x in sys.modules for x in '
                '("currency_wars_runner", "currency_wars_control", '
                '"currency_wars_input_bridge", "currency_wars_safety"))')
        subprocess.run([sys.executable, '-B', '-c', code], cwd=PROJECT / 'tools', check=True)

    def test_no_explicit_session_cannot_submit_or_pause(self):
        with patch.dict(os.environ, {}, clear=True):
            adapter = Adapter()
            for result in (asyncio.run(adapter.submit({'request_id': 'old'})),
                           asyncio.run(adapter.invoke('pause', reason='test'))):
                self.assertEqual(result['status'], 'no_session')
                self.assertIs(result['input_sent'], False)

    def test_main_has_no_native_economy_contract_even_with_opt_in(self):
        adapter = Adapter(enable_candidate_economy=True)
        result = asyncio.run(adapter.economy('test', {}, 100, {}, {}))
        self.assertEqual(result['status'], 'unsupported')
        self.assertIs(result['input_sent'], False)

    def test_unknown_fields_and_credentials_are_preserved_or_redacted(self):
        with patch.dict(os.environ, {'CURRENCY_WARS_RUN_TOKEN': 'private-test-token'}):
            adapter = Adapter()
            state = adapter.project_state({'observation': {'snapshot_id': 'image', 'page': 'shop',
                                            'fields': {'level': '99'}}})
            self.assertIsNone(state['fields']['coins']['value'])
            self.assertFalse(state['fields']['coins']['known'])
            self.assertEqual(state['fields']['coins']['source'], 'unknown')
            self.assertIsNone(state['fields']['player_level']['value'])
            self.assertFalse(state['freshness']['live_capture_requested'])
            result = adapter.redact({'run_token': 'private-test-token',
                                     'nested': [{'gui_token': 'hidden', 'error': 'private-test-token'}]})
            self.assertNotIn('private-test-token', str(result))
            self.assertNotIn('run_token', result)
            self.assertNotIn('gui_token', result['nested'][0])


if __name__ == '__main__':
    unittest.main()
