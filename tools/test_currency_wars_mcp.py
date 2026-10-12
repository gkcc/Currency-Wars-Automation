"""Public safety boundaries of the optional adapter; no game/SDK required."""
import asyncio
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from currency_wars_mcp import Adapter, PROJECT, propose_economic_step, propose_economic_macro


class MCPBoundaries(unittest.TestCase):
    def macro_state(self):
        from currency_wars_economy import MACRO_CONTRACT
        return {'status': 'observed', 'source': 'authenticated_runner_status', 'control_mode': 'waiting_decision',
            'page': 'shop', 'binding': dict(zip(('request_id', 'snapshot_id', 'resume_epoch', 'match_id'), ('root', 'frame', 'epoch', 'match'))),
            'decision': {'kind': 'shop_strategy', 'economic_macro_contract': MACRO_CONTRACT,
                         'allowed_action_types': ['advance_local_economy', 'advance_local_economy_macro']},
            'preparation_checklist': {'phase': 'economy', 'steps': [{'id': phase, 'completed': True}
                                     for phase in ('rewards', 'shop_inspection', 'inventory_cleanup')]}}

    def test_macro_is_explicit_supported_and_single_step_remains_default(self):
        state = self.macro_state()
        caps = {'max_economic_actions': 2, 'max_total_spent': 8, 'max_paid_refreshes': 0,
                'max_navigation_clicks': 4, 'max_wall_seconds': 30}
        self.assertEqual(propose_economic_step(state)['actions'][0]['type'], 'advance_local_economy')
        proposal = propose_economic_macro(state, caps)
        self.assertEqual(proposal['actions'][0]['type'], 'advance_local_economy_macro')
        self.assertEqual(proposal['reply']['actions'][0]['limits'], caps)
        self.assertFalse(proposal['input_sent'])
        for invalid in (None, {}, {**caps, 'max_wall_seconds': True}, {**caps, 'max_economic_actions': 0},
                        {**caps, 'budget': 50}, {**caps, 'max_total_spent': -1}):
            self.assertEqual(propose_economic_macro(state, invalid)['status'], 'blocked')
        state['decision'].pop('economic_macro_contract')
        self.assertEqual(propose_economic_macro(state, caps)['status'], 'unsupported')

    def test_active_macro_exposes_latest_source_and_blocks_second_proposal(self):
        state = self.macro_state()
        state['economy_macro'] = {'status': 'active', 'request_id': 'root', 'latest_source': {'snapshot_id': 'latest'}}
        self.assertEqual(propose_economic_step(state)['status'], 'blocked')
        self.assertEqual(propose_economic_macro(state, {})['status'], 'blocked')
        projected = Adapter().project_state({'economy_macro': state['economy_macro'],
            'decision_request': {'observation': {'snapshot_id': 'old-parent', 'page': 'shop'}},
            'observation': {'snapshot_id': 'latest', 'page': 'preparation'}})
        self.assertEqual(projected['snapshot_id'], 'latest')
        self.assertEqual(projected['economy_macro']['status'], 'active')

    def test_submit_acknowledgement_is_not_effect_completion(self):
        adapter = Adapter()
        async def invoke(*unused, **kwargs):
            return {'ok': True}
        adapter.invoke = invoke
        result = asyncio.run(adapter.submit({'request_id': 'root'}))
        self.assertEqual(result['status'], 'submitted')
        self.assertFalse(result['effects_verified'])

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

    def test_xp_price_requires_same_snapshot_native_offer_and_gain_stays_unknown(self):
        adapter = Adapter()
        player = {'actor': 'player', 'origin': 'native_player_hud', 'snapshot_id': 'frame'}
        offer = {'actor': 'player', 'origin': 'native_experience_offer', 'snapshot_id': 'frame',
                 'cost_checked': True, 'buy_cost': 4, 'buy_gain': 999,
                 'evidence': {'cost': {'source': 'current_native_experience_price',
                                      'snapshot_id': 'frame', 'value': 4}}}
        def project(value, hud=player):
            return adapter.project_state({'observation': {'snapshot_id': 'frame', 'page': 'shop',
                'semantic': {'player_hud': hud, 'experience_offer': value}}})
        result = project(offer)
        self.assertTrue(result['fields']['xp_cost']['known'])
        self.assertEqual(result['fields']['xp_cost']['value'], 4)
        self.assertEqual(result['semantic']['experience_offer'], offer)
        self.assertFalse(result['fields']['xp_gain']['known'])
        for bad in ({**offer, 'snapshot_id': 'old'}, {**offer, 'actor': 'enemy'},
                    {**offer, 'buy_cost': True}, {**offer, 'cost_checked': False},
                    {**offer, 'evidence': {}}, {**offer, 'buy_cost': 5}):
            self.assertFalse(project(bad)['fields']['xp_cost']['known'])
        self.assertFalse(project(offer, {**player, 'snapshot_id': 'old'})['fields']['xp_cost']['known'])


if __name__ == '__main__':
    unittest.main()
