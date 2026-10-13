"""Public read-only decision boundaries; synthetic states are never gameplay proof."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from currency_wars_decision import Catalogue, propose
from currency_wars_mcp import Adapter
from currency_wars_solver import solve

NOW = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
SNAP = 'a' * 64


def current_state():
    slots = []
    for location, row, count in (('board', 'front', 4), ('board', 'back', 6), ('bench', 'bench', 9)):
        for slot in range(1, count + 1):
            slots.append({'location': location, 'row': row, 'slot': slot, 'status': 'empty',
                          'snapshot_id': SNAP, 'name': None, 'star': None})
    slots[-9].update(status='occupied', name='丹恒·饮月', star=1, position='前台')
    slots[-8].update(status='occupied', name='花火', star=1, position='后台')
    units = [copy.deepcopy(s) for s in slots if s['status'] == 'occupied']
    return {'schema': 'currency-wars-mcp/v1', 'status': 'observed', 'source': 'authenticated_runner_status',
            'binding': {'request_id': 'synthetic-request', 'match_id': 'synthetic-match', 'resume_epoch': 0, 'snapshot_id': SNAP},
            'match_id': 'synthetic-match', 'snapshot_id': SNAP, 'page': 'preparation',
            'observation': {'snapshot_id': SNAP, 'read_contract': {'effective_scope': 'full'}},
            'freshness': {'captured_at': NOW.isoformat(), 'request_deadline': (NOW + timedelta(seconds=40)).isoformat()},
            'fields': {'coins': {'known': True, 'normalized_value': 5, 'proof': {'value': 5, 'snapshot_id': SNAP}},
                       'player_level': {'known': True, 'value': 3}, 'xp': {'known': True, 'value': [0, 4]},
                       'population': {'known': True, 'value': '0/3'}},
            'preparation_checklist': {'economy_allowed': True, 'steps': [
                {'id': phase, 'completed': True, 'proof': {'source': 'observed_screen', 'resume_epoch': 0,
                                                        'snapshot_id': SNAP, 'evidence_file': 'synthetic-only.png'}}
                for phase in ('rewards', 'inventory_cleanup')]},
            'semantic': {'player_hud': {'actor': 'player', 'origin': 'native_player_hud', 'snapshot_id': SNAP, 'level': 3, 'xp': [0, 4]},
                         'environment': {'checked': True, 'snapshot_id': SNAP, 'affixes': []},
                         'team': {'checked': True, 'fully_read': True, 'snapshot_id': SNAP, 'unknown_slots': [],
                                  'slots': slots, 'units': units, 'capacity': {'snapshot_id': SNAP, 'bench_checked': True,
                                      'bench_capacity': 9, 'occupied': 2, 'overflow_checked': True, 'overflow_count': 0}}}}


def current_shop_state():
    state = current_state()
    state['page'] = 'shop'
    state['semantic']['shop_terms'] = {'checked': True, 'snapshot_id': SNAP,
                                      'ordinary_one_star': True, 'base_prices_unchanged': True}
    slots = []
    for slot in range(1, 6):
        x, y, width, height = 390 + (slot - 1) * 270, 140, 210, 340
        slots.append({'slot': slot, 'status': 'empty', 'bounds': [x, y, width, height],
                      'position': [round(x + width / 2), round(y + height / 2)]})
    first = slots[0]
    x, y, _, _ = first['bounds']
    first.update(status='recognized', name='黑塔', cost=1, evidence={
        'name': {'method': 'ocr', 'confidence': .99, 'bounds': [x+15, y+100, x+150, y+125]},
        'cost': {'method': 'digit_ocr', 'confidence': .99, 'ocr_bounds': [x+15,y+20,x+45,y+45],
                 'raw_ocr': [{'raw': '1', 'confidence': .99}]}})
    state['shop'] = {'schema': 'currency-wars-shop-observation/v1',
                     'input': {'sha256': SNAP, 'size': [1920,1080], 'format': 'PNG'},
                     'page': {'reliable_open_shop': True, 'geometry': {'frame_scores': [.99]*5, 'overlays': []}},
                     'slots': slots}
    return state


class DecisionBoundaries(unittest.TestCase):
    def test_published_catalogue_resolves_fee_alias_and_star_without_live_session(self):
        c = Catalogue()
        result = c.query('丹恒·饮月', 'characters', star=2)
        self.assertFalse(result['input_sent'])
        row = result['records'][0]
        self.assertEqual(row['cost'], 2)
        self.assertEqual(row['data']['position'], '前台')
        self.assertEqual([r['star'] for r in row['data']['star_effects']], [2])
        self.assertEqual(row['data']['star_effects'][0]['FrontPowerBase'], 200)
        self.assertNotIn('source', row)  # The entire import manifest is not repeated.

    def test_complete_current_state_builds_legal_deployment_without_invented_xp_or_refresh_rules(self):
        result = propose(current_state(), now=NOW)
        self.assertEqual(result['status'], 'proposed', result)
        self.assertEqual(result['model_api_calls'], 0)
        self.assertFalse(result['input_sent'])
        self.assertFalse(result['auto_dispatch_allowed'])
        self.assertFalse(result['catalogue']['scopes']['experience'])
        self.assertFalse(result['catalogue']['scopes']['refresh'])
        self.assertNotIn('buy_cost', result['solver_input']['xp'])
        candidate = result['candidates'][0]
        self.assertEqual(candidate['actions'], [])
        placement = {r['name']: r['location']['zone'] for r in candidate['deployment']}
        self.assertEqual(placement, {'丹恒•饮月': 'front', '花火': 'back'})
        self.assertEqual(candidate['coins_retained'], 5)

    def test_unknown_identity_overflow_snapshot_and_preconditions_cannot_be_defaulted(self):
        for change in ('name', 'overflow', 'snapshot', 'rewards', 'partial_read', 'expired'):
            state = current_state()
            if change == 'name':
                state['semantic']['team']['units'][0]['name'] = None
            elif change == 'overflow':
                state['semantic']['team']['capacity']['overflow_checked'] = False
            elif change == 'snapshot':
                state['semantic']['team']['snapshot_id'] = 'b' * 64
            elif change == 'rewards':
                state['preparation_checklist']['steps'][0]['completed'] = False
            elif change == 'partial_read':
                state['observation']['read_contract']['effective_scope'] = 'rewards'
            else:
                state['freshness']['captured_at'] = (NOW - timedelta(seconds=61)).isoformat()
                state['freshness']['heartbeat_at'] = NOW.isoformat()
            with self.subTest(change=change):
                result = propose(state, now=NOW)
                self.assertEqual(result['status'], 'needs_review')
                self.assertEqual(result['candidates'], [])
                self.assertFalse(result['input_sent'])

    def test_ambiguous_cost_variant_is_rejected(self):
        state = current_state()
        for group in ('slots', 'units'):
            row = next(r for r in state['semantic']['team'][group] if r.get('name') == '丹恒·饮月')
            row['name'] = '银狼LV.999'
        result = propose(state, now=NOW)
        self.assertEqual(result['status'], 'needs_review')
        self.assertTrue(any('ambiguous_current_variant' in s for s in result['unknown']))

    def test_disabled_xp_and_refresh_scopes_reject_contradictory_budgets(self):
        result = propose(current_state(), now=NOW)
        for scope in ('experience', 'refresh', 'purchase'):
            data = copy.deepcopy(result['solver_input'])
            data['budgets'][scope] = 1
            with self.subTest(scope=scope):
                candidate = solve(data)
                self.assertEqual(candidate['status'], 'needs_review')
                self.assertEqual(candidate['candidates'], [])

    def test_mcp_no_session_never_reads_old_state_or_sends_input(self):
        with patch.dict(os.environ, {}, clear=True):
            result = asyncio.run(Adapter().decision())
        self.assertEqual(result['status'], 'no_session')
        self.assertEqual(result['candidates'], [])
        self.assertFalse(result['input_sent'])

    def test_public_mcp_projection_preserves_verified_terms_and_shop_purchase(self):
        current = current_shop_state()
        observation = {'snapshot_id': SNAP, 'page': 'shop', 'captured_at': NOW.isoformat(),
                       'read_contract': {'effective_scope':'full'}, 'fields': {'deployed':'0/3'},
                       'semantic': current['semantic'], 'shop': current['shop']}
        observation['semantic']['coins'] = {'value':5,'snapshot_id':SNAP}
        observation['semantic']['player_hud']['evidence'] = {'level': {'origin':'native_player_hud'},
                                                          'experience_progress': {'origin':'native_player_hud'}}
        request = {**current['binding'], 'observation':observation,
                   'deadline_at':current['freshness']['request_deadline'],
                   'preparation_checklist':current['preparation_checklist']}
        public = Adapter().project_state({'match_id':'synthetic-match','decision_request':request})
        proposal = propose(public, now=NOW)
        self.assertEqual(proposal['status'],'proposed', proposal)
        self.assertTrue(proposal['catalogue']['scopes']['shop_purchase'])
        actions = proposal['candidates'][0]['actions']
        self.assertTrue(any(a['type']=='buy' and a['slot']==0 and a['price']==1 for a in actions))
        self.assertFalse(any(a['type'] in ('buy_xp','refresh') for a in actions))
        self.assertFalse(proposal['input_sent'])


    def test_bond_threshold_changes_joint_purchase_decision_and_limit_is_explicit(self):
        data = propose(current_state(), now=NOW)['solver_input']
        def unit(name):
            return {'uid': name, 'role_id': name, 'name': name, 'cost': 1, 'star': 1,
                    'positions': ['front'], 'source': 'synthetic_integration'}
        data.update(units=[unit('a'), unit('b')], shop_enabled=True,
                    shop=[{'slot': i, 'unit': unit(name), 'price': 1, 'source': 'synthetic_shop'}
                          for i, name in enumerate(['c', 'd'])] +
                         [{'slot': i, 'unit': None, 'unavailable': True, 'source': 'synthetic_empty'}
                          for i in range(2, 5)],
                    budgets={'purchase': 2, 'experience': 0, 'refresh': 0})
        data['capacity'].update(front=2, back=0, bench=2)
        data['xp']['population_by_level'] = {'3': 2}
        data['scoring'].update(gold_weight=0, units={name: {str(star): {
            'bench': 0, 'front': {'damage': 0, 'utility': value}}
            for star in (1, 2, 3)} for name, value in [('a', 10), ('b', 9), ('c', 8), ('d', 7)]})
        baseline = solve(data)
        self.assertEqual(baseline['candidates'][0]['actions'], [])
        model = {'source': 'synthetic_verified_threshold_model', 'bonds': [{
            'id': 'pair', 'source': 'synthetic_rule', 'utility_source': 'synthetic_heuristic',
            'count_semantics': 'distinct_role_in_allowed_positions', 'semantics_verified': True,
            'members': [{'role_id': name, 'positions': ['front']} for name in ['c', 'd']],
            'thresholds': [{'count': 2, 'utility': 10}]}]}
        data['scoring']['bond_model'] = model
        result = solve(data)
        self.assertEqual(result['status'], 'proposed')
        best = result['candidates'][0]
        self.assertEqual(best['estimated_utility'], 25)
        self.assertEqual({a['slot'] for a in best['actions']}, {0, 1})
        self.assertEqual(best['bond_scores'][0]['count'], 2)
        self.assertTrue(best['deployment_exact'])
        data['scoring']['bond_model']['search_limits'] = {'time_limit_ms': 0}
        bounded = solve(data)
        self.assertEqual(bounded['status'], 'needs_review')
        self.assertIn('bond_model.search_limit', bounded['unknown'])
        self.assertFalse(bounded['candidates'][0]['deployment_exact'])

    def test_explicit_bond_policy_reaches_catalogue_solver_without_auto_dispatch(self):
        before = propose(current_state(), now=NOW)
        roles = [u['role_id'] for u in before['solver_input']['units']]
        model = {'source': 'synthetic_policy', 'bonds': [{
            'id': 'pair', 'source': 'synthetic_plain_roster_rule', 'utility_source': 'synthetic_heuristic',
            'count_semantics': 'distinct_role_in_allowed_positions', 'semantics_verified': True,
            'members': [{'role_id': role, 'positions': ['front', 'back']} for role in roles],
            'thresholds': [{'count': 2, 'utility': 40}]}]}
        result = propose(current_state(), now=NOW, policy={'bond_model': model})
        self.assertEqual(result['candidates'][0]['bond_scores'][0]['count'], 2)
        self.assertEqual(result['candidates'][0]['estimated_utility'],
                         before['candidates'][0]['estimated_utility'] + 40)
        self.assertFalse(result['auto_dispatch_allowed'])
        self.assertFalse(result['input_sent'])

    def test_impossible_purchase_staging_does_not_discard_stop_with_bond_model(self):
        data = propose(current_shop_state(), now=NOW)['solver_input']
        data['capacity']['bench'] = 0
        baseline = solve(data)
        data['scoring']['bond_model'] = {'source': 'synthetic_empty_bond_model', 'bonds': []}
        result = solve(data)
        self.assertEqual(baseline['status'], 'proposed')
        self.assertEqual(result['status'], 'proposed', result)
        self.assertTrue(result['candidates'])
        self.assertEqual(result['candidates'][0]['actions'], [])
        self.assertEqual(result['candidates'][0]['estimated_utility'], baseline['candidates'][0]['estimated_utility'])


class ReleasedRuleScenarios(unittest.TestCase):
    """Protect rule timing/count semantics before they influence decisions."""

    def test_scenario_results_cannot_mutate_cached_catalogue_components_or_provenance(self):
        c = Catalogue()
        before = c.rules('战技点', 'bonds')
        result = c.bond_scenario(['丹恒·饮月', '花火'])
        row = next(bond for bond in result['bonds'] if bond['name'] == '战技点')
        row['source']['revision'] = 'mutated'
        row['base_attained_property_components']['whole_team'][0]['value'] = 987654321
        self.assertEqual(c.rules('战技点', 'bonds'), before)
        event = c.investment_scenario(['特战资金'], 'boss_node_enter')
        event['contributions'][0]['source']['revision'] = 'mutated'
        self.assertNotEqual(c.investment_scenario(['特战资金'], 'boss_node_enter')
                            ['contributions'][0]['source']['revision'], 'mutated')
        baseline = c.query('黑塔', 'characters')['metadata']
        mutated = c.query('黑塔', 'characters')
        mutated['metadata']['client_version'] = 'mutated'
        self.assertEqual(c.query('黑塔', 'characters')['metadata'], baseline)
        before_model = c.bond_proxy_model()
        altered_model = c.bond_proxy_model()
        altered_model['property_weights']['ExtraSpeedAddedRatio1'] = 999
        self.assertEqual(c.bond_proxy_model(), before_model)

    def test_duplicate_character_aliases_do_not_create_extra_bond_members(self):
        result = Catalogue().bond_scenario(['丹恒·饮月', '丹恒•饮月', '花火', '未知角色'])
        skill_points = next(row for row in result['bonds'] if row['name'] == '战技点')
        self.assertEqual(skill_points['base_count_interval'], [2, 2])
        self.assertEqual(skill_points['base_attained_threshold_interval'], [2, 2])
        self.assertEqual(skill_points['additional_distinct_base_members_needed'], 2)
        self.assertEqual(result['unknown_name_families'], ['未知角色'])
        self.assertFalse(result['known_catalogue_membership_complete'])
        self.assertFalse(skill_points['current_activation_verified'])

    def test_gold_is_given_at_its_described_event_and_unsupported_remains_unknown(self):
        c = Catalogue()
        acquire = c.investment_scenario(['特战资金', '定期福利'], 'selection_acquired')
        boss = c.investment_scenario(['特战资金', '定期福利'], 'boss_node_enter')
        node = c.investment_scenario(['定期福利'], 'node_enter')
        self.assertEqual(acquire['described_resource_amounts'], {'gold': 4})
        self.assertEqual(boss['described_resource_amounts'], {'gold': 7})
        self.assertEqual(node['described_resource_amounts'], {'gold': 2})
        unknown = c.investment_scenario(['策略大师'], 'node_enter')
        self.assertTrue(unknown['unresolved'])
        self.assertFalse(unknown['all_selected_descriptions_compiled'])
        self.assertFalse(boss['execution_verified'])

    def test_limited_future_events_need_a_counter_and_do_not_spend_it(self):
        c = Catalogue()
        missing = c.investment_scenario(['长期主义'], 'node_enter')
        self.assertEqual(missing['unresolved'][0]['reason'], 'missing_event_counter')
        counter = {'node_entries_after_acquisition': 3}
        self.assertEqual(c.investment_scenario(['长期主义'], 'node_enter', counter)
                         ['described_resource_amounts'], {'gold': 7})
        self.assertEqual(counter, {'node_entries_after_acquisition': 3})
        self.assertEqual(c.investment_scenario(['长期主义'], 'node_enter',
                         {'node_entries_after_acquisition': 4})['described_resource_amounts'], {})
        with self.assertRaises(ValueError):
            c.investment_scenario(['长期主义', '长期主义'], 'node_enter', counter)

    def test_extra_conditions_and_swapped_parameter_indices_are_not_fixed_cash(self):
        from currency_wars_rules import compile_investment
        def description(template, parameters):
            return {'template': template, 'parameters': parameters,
                    'requires_runtime_context': False, 'unresolved_placeholders': [],
                    'unresolved_entity_references': []}
        for template, parameters in (
            ('进入首领节点时，如果获胜，获得#1[i]枚金币。', [7]),
            ('进入首领节点时，获得#1[i]枚金币。额外随机获得装备。', [7]),
            ('立刻获得#2个金币，并且在每次进入新节点时，获得#1金币。', [4, 2]),
            ('进入首领节点时，获得#1[i]枚金币。', [True]),
            ('进入首领节点时，获得#1[i]枚金币。', [7, 12]),
        ):
            with self.subTest(template=template, parameters=parameters):
                self.assertEqual(compile_investment(description(template, parameters)), [])
        parsed = compile_investment(description('进入首领节点时，获得#1[i]枚金币。', [7]))
        self.assertEqual(parsed[0]['amount'], 7)
        self.assertEqual(parsed[0]['event'], 'boss_node_enter')


class BondFeatureRanking(unittest.TestCase):
    """The regression is losing a threshold-completing set at equal slot counts."""
    def model(self, thresholds=None):
        return {'bonds': [{'name': 'pair', 'thresholds': thresholds or [
            {'members': 2, 'trait_members': 1., 'whole_team': 0.}]}],
            'membership': {name: {'minimum': [int(name in ('B', 'C'))],
                                  'maximum': [int(name in ('B', 'C'))]}
                           for name in ('A', 'B', 'C')}}

    def items(self):
        return [{'ref': name, 'family': name,
                 'powers': {'front': {'minimum': power, 'maximum': power}}, 'bench_weight': 0.}
                for name,power in (('A', 100), ('B', 90), ('C', 89))]

    def test_threshold_can_change_selection_at_same_population(self):
        from currency_wars_bond_proxy import layouts
        result = layouts(self.items(), self.model(), scale=100)
        self.assertEqual(result['minimum'][2][1], (('B', 'front'), ('C', 'front')))
        self.assertEqual(result['minimum'][2][0], 379)
        self.assertTrue(result['search']['exact_for_declared_model'])

    def test_higher_tier_replaces_lower_and_duplicate_copies_count_once(self):
        from currency_wars_bond_proxy import layouts
        items = self.items()
        items.append({**items[1], 'ref': 'B-copy'})
        model = self.model([{'members': 1, 'trait_members': 1., 'whole_team': 0.},
                            {'members': 2, 'trait_members': .1, 'whole_team': 0.}])
        result = layouts(items, model, scale=100)
        names = [ref for ref,_ in result['minimum'][3][1]]
        self.assertEqual(result['minimum'][3][0], 299)
        self.assertEqual(len(names), 3)
        self.assertFalse('B' in names and 'B-copy' in names)

    def test_transition_limit_preserves_hold_and_marks_search_incomplete(self):
        from currency_wars_bond_proxy import layouts
        result = layouts(self.items(), self.model(), budget={'remaining': 1})
        self.assertFalse(result['search']['exact_for_declared_model'])
        self.assertTrue(result['search']['transition_limit_reached'])
        self.assertEqual(result['minimum'][0], (0., ()))


class DeploymentProposalTests(unittest.TestCase):
    """Synthetic public-contract checks; these never prove live deployment."""

    def fixture(self):
        slots = []
        for location, row, count in [('board', 'front', 4), ('board', 'back', 6), ('bench', 'bench', 9)]:
            for number in range(1, count + 1):
                slots.append({'location': location, 'row': row, 'slot': number, 'status': 'empty',
                              'name': None, 'star': None, 'position': None, 'snapshot_id': SNAP})
        source = slots[14]
        source.update(status='occupied', name='丹恒·饮月', star=1, position='前台',
                      evidence={'native_unit_bank': {'bank_sha256': 'synthetic-only',
                          **{k: {'matches': True} for k in ('portrait', 'star', 'position')}}})
        binding = {'request_id': 'synthetic-request', 'snapshot_id': SNAP,
                   'resume_epoch': 'synthetic-epoch', 'match_id': 'synthetic-match'}
        state = {'status': 'observed', 'source': 'authenticated_runner_status', 'binding': binding,
                 'snapshot_id': SNAP, 'control_mode': 'waiting_decision', 'page': 'preparation',
                 'decision': {'kind': 'preparation_strategy'}, 'preparation_checklist': {
                     'phase': 'lineup_equipment', 'steps': [{'id': 'economy', 'completed': True}]},
                 'native_fields': {'stage': '1-1', 'level': '3', 'deployed': '0/3'},
                 'semantic': {'team': {'snapshot_id': SNAP, 'slots': slots},
                              'coins': {'value': 6, 'snapshot_id': SNAP, 'confidence': .99,
                                        'bounds': [1613, 892, 1687, 950]}}}
        advice = {'source': {'binding': copy.deepcopy(binding)}, 'observed_entities':
                  [{'entity_ref': 'synthetic-unit', 'observed': copy.deepcopy(source)}],
                  'joint_economy': {'best_by_conditional_capacity': [{'usable_places_assumption': 3,
                      'preferred_candidate': 'hold'}], 'portfolios': [{'id': 'hold', 'purchase_refs': [],
                      'conditional_layouts': [{'usable_places_assumption': 3, 'minimum_proxy_layout':
                          [{'entity_ref': 'synthetic-unit', 'zone': 'front'}]}]}]}}
        return state, advice

    def test_local_choice_maps_to_one_native_identity_without_coordinates(self):
        from currency_wars_deployment import propose_deployment
        state, advice = self.fixture()
        proposal = propose_deployment(state, advice)
        self.assertEqual(proposal['status'], 'proposed')
        self.assertFalse(proposal['execution_eligible'])
        self.assertFalse(proposal['input_sent'])
        self.assertEqual(len(proposal['actions']), 1)
        action = proposal['actions'][0]
        self.assertEqual(action['unit']['name'], '丹恒·饮月')
        self.assertEqual(action['destination'], {'row': 'front', 'slot': 1})
        self.assertNotIn('args', action)

    def test_changed_binding_wrong_zone_and_unread_slot_produce_no_action(self):
        from currency_wars_deployment import propose_deployment
        for kind in ('epoch', 'zone', 'slot', 'phase'):
            with self.subTest(kind=kind):
                state, advice = self.fixture()
                if kind == 'epoch': advice['source']['binding']['resume_epoch'] = 'another'
                if kind == 'zone': advice['joint_economy']['portfolios'][0]['conditional_layouts'][0]['minimum_proxy_layout'][0]['zone'] = 'back'
                if kind == 'slot': state['semantic']['team']['slots'][0]['status'] = 'unknown'
                if kind == 'phase': state['preparation_checklist']['phase'] = 'rewards'
                self.assertEqual(propose_deployment(state, advice)['actions'], [])

    def test_unscored_occupied_board_reservation_reduces_solver_capacity(self):
        from currency_wars_deployment import propose_deployment
        state, advice = self.fixture()
        state['native_fields']['deployed'] = '1/3'
        state['semantic']['team']['slots'][0]['status'] = 'occupied'
        joint = advice['joint_economy']
        joint['reserved_unscored_board_places'] = {'front': 1, 'back': 0}
        joint['best_by_conditional_capacity'][0]['usable_places_assumption'] = 2
        joint['portfolios'][0]['conditional_layouts'][0]['usable_places_assumption'] = 2
        proposal = propose_deployment(state, advice)
        self.assertEqual(proposal['status'], 'proposed')
        self.assertEqual(proposal['actions'][0]['destination'], {'row': 'front', 'slot': 2})
        self.assertIsNone(state['semantic']['team']['slots'][0]['name'])
        joint['reserved_unscored_board_places']['front'] = 2
        self.assertEqual(propose_deployment(state, advice)['actions'], [])

    def test_effect_requires_population_source_destination_and_unchanged_other_facts(self):
        from currency_wars_deployment import propose_deployment, verify_deployment_effect
        state, advice = self.fixture()
        action = propose_deployment(state, advice)['actions'][0]
        before = {'snapshot_id': SNAP, 'page': 'preparation', 'fields': state['native_fields'],
                  'semantic': state['semantic'], 'capture_request_id': 'before-observe', 'frame_id': 'before-frame'}
        after = copy.deepcopy(before)
        after.update(snapshot_id='b' * 64, capture_request_id='after-observe', frame_id='after-frame')
        after['fields']['deployed'] = '1/3'
        after['semantic']['team']['snapshot_id'] = after['snapshot_id']
        after['semantic']['coins']['snapshot_id'] = after['snapshot_id']
        for slot in after['semantic']['team']['slots']: slot['snapshot_id'] = after['snapshot_id']
        after['semantic']['team']['slots'][14].update(status='empty', name=None, star=None, position=None)
        after['semantic']['team']['slots'][0]['status'] = 'occupied'
        result = verify_deployment_effect(before, after, action)
        self.assertEqual(result['outcome'], 'observed_transition')
        self.assertFalse(result['native_destination_identity_verified'])
        gaps = copy.deepcopy(after)
        gaps['semantic']['team']['slots'][0]['status'] = 'unknown'
        gaps['semantic'].pop('coins')
        review = {'source': 'supervising_agent', 'original_request_id': 'synthetic-request',
            'input_request_id': 'synthetic-input', 'snapshot_id': gaps['snapshot_id'],
            'capture_request_id': gaps['capture_request_id'], 'frame_id': gaps['frame_id'],
            'destination': {**action['destination'], 'status': 'occupied'}, 'coins': 6}
        self.assertEqual(verify_deployment_effect(before, gaps, action)['outcome'], 'unknown')
        supplemented = verify_deployment_effect(before, gaps, action, read_review=review)
        self.assertEqual(supplemented['outcome'], 'observed_transition')
        self.assertEqual(supplemented['effect_read_origin'], 'supervising_agent')
        self.assertEqual(gaps['semantic']['team']['slots'][0]['status'], 'unknown')
        self.assertNotIn('coins', gaps['semantic'])
        for field, value in (('coins', 5), ('source', 'native_visual_state_reader'), ('snapshot_id', 'old')):
            invalid = {**review, field: value}
            self.assertEqual(verify_deployment_effect(before, gaps, action, read_review=invalid)['outcome'], 'unknown')
        self.assertEqual(verify_deployment_effect(before, after, action, read_review=review)['outcome'], 'unknown')
        for kind in ('coins', 'population', 'source', 'destination', 'other'):
            bad = copy.deepcopy(after)
            if kind == 'coins': bad['semantic']['coins']['value'] -= 1
            if kind == 'population': bad['fields']['deployed'] = '0/3'
            if kind == 'source': bad['semantic']['team']['slots'][14]['status'] = 'occupied'
            if kind == 'destination': bad['semantic']['team']['slots'][0]['status'] = 'empty'
            if kind == 'other': bad['semantic']['team']['slots'][10]['status'] = 'occupied'
            with self.subTest(kind=kind):
                self.assertEqual(verify_deployment_effect(before, bad, action)['outcome'], 'unknown')


class GoldAllocationComparison(unittest.TestCase):
    """Catch duplicate use of the same gold and premature economic completion."""

    def compare(self, *, shop_complete=True, reserve=0, coins=6, threshold=4):
        from currency_wars_advisor import _allocate_gold
        state = {'binding': {'request_id': 'synthetic', 'snapshot_id': SNAP}, 'snapshot_id': SNAP,
            'semantic': {'player_hud': {'actor': 'player', 'origin': 'native_player_hud',
                'snapshot_id': SNAP, 'level': 3, 'xp': [0, threshold]},
                'experience_offer': {'origin': 'native_experience_offer', 'snapshot_id': SNAP,
                                     'cost_checked': True, 'buy_cost': 4, 'buy_gain': None}}}
        def scores(power):
            return {cap: ((power if cap <= 3 else power + 150), ()) for cap in range(11)}
        models = [{'id': 'hold', 'purchase_spend': 0, 'purchase_refs': [],
                   'minimum': scores(100), 'maximum': scores(100), 'search': {}},
                  {'id': 'buy5', 'purchase_spend': 5, 'purchase_refs': ['synthetic_offer'],
                   'minimum': scores(105), 'maximum': scores(105), 'search': {}}]
        return _allocate_gold(state, Catalogue(), {'reserve': reserve, 'gold_weight': 20},
            {'verified_native': True, 'value': coins},
            {'projection_known': True, 'maximum': 3}, {'front': 0, 'back': 0}, models, shop_complete)

    def test_upgrade_is_compared_with_buying_under_the_same_wallet(self):
        result = self.compare()
        best = result['conditional_best']
        self.assertEqual(best['budget'], {'purchase': 0, 'experience': 4, 'refresh': 0})
        self.assertEqual(best['coins_retained'], 2)
        self.assertEqual(best['population_assumption'], 4)
        for candidate in result['conditional_candidates']:
            self.assertEqual(sum(candidate['budget'].values()) + candidate['coins_retained'], 6)
            self.assertGreaterEqual(candidate['coins_retained'], 0)
            self.assertFalse(candidate['budget']['purchase'] and candidate['budget']['experience'])

    def test_unknown_refresh_and_native_gain_do_not_become_complete_zero_budgets(self):
        result = self.compare()
        self.assertEqual(result['status'], 'incomplete_joint_comparison')
        self.assertFalse(result['complete_dimensions']['experience'])
        self.assertFalse(result['complete_dimensions']['refresh'])
        self.assertFalse(result['execution_eligible'])
        self.assertTrue(result['blocked_reasons'])
        self.assertFalse(self.compare(shop_complete=False)['complete_dimensions']['purchase'])

    def test_current_threshold_filters_profiles_and_reserve_limits_spending(self):
        result = self.compare(threshold=6)
        self.assertEqual({c['profile_assumption'] for c in result['conditional_candidates']}, {'level_v2'})
        for candidate in self.compare(reserve=4)['conditional_candidates']:
            self.assertGreaterEqual(candidate['coins_retained'], 4)
            self.assertEqual(candidate['xp_purchases'], 0)


class ReleasedEconomicPrior(unittest.TestCase):
    """Protect the one-wallet contract and keep assumptions out of native facts."""

    def compare(self, coins=3, threshold=4, *, free=False, upgrade_value=False, **overrides):
        from currency_wars_advisor import _allocate_gold, _layouts, _policy
        state = {'binding': {'request_id': 'synthetic', 'snapshot_id': SNAP}, 'snapshot_id': SNAP,
            'semantic': {'player_hud': {'actor': 'player', 'origin': 'native_player_hud',
                'snapshot_id': SNAP, 'level': 3, 'xp': [0, threshold]},
                'experience_offer': {'origin': 'native_experience_offer', 'snapshot_id': SNAP,
                    'cost_checked': True, 'buy_cost': 4, 'buy_gain': None},
                'refresh_offer': {'schema': 'currency-wars-refresh-offer/v1', 'snapshot_id': SNAP,
                    'page': 'shop', 'status': 'known', 'mode': 'free' if free else 'paid',
                    'free_remaining': 2 if free else None, 'paid_cost': None if free else 2}}}
        policy = _policy({'economy_model': 'released_prior', 'bond_weight': 0,
                          'bench_weight': .5 if upgrade_value else .15, **overrides})
        items = [{'ref': str(i), 'family': str(i), 'bench_weight': policy['bench_weight'],
                  'powers': {'front': {'minimum': 5000, 'maximum': 5000}}} for i in range(4)] if upgrade_value else []
        layouts = _layouts(items, 'minimum')
        models = [{'id': 'hold', 'purchase_spend': 0, 'purchase_refs': [], 'items': items,
            'minimum': layouts, 'maximum': layouts, 'front_capacity': 4, 'back_capacity': 6,
            'remaining_bench_slots': 5, 'search': {'exact_for_declared_model': True}}]
        original = copy.deepcopy(state)
        result = _allocate_gold(state, Catalogue(), policy, {'verified_native': True, 'value': coins},
            {'projection_known': True, 'maximum': 3}, {'front': 0, 'back': 0}, models, True)
        self.assertEqual(state, original)
        return result

    def test_paid_reroll_reserves_future_purchase_and_is_deterministic(self):
        result = self.compare(coins=3)
        self.assertEqual(result, self.compare(coins=3))
        self.assertEqual(result['status'], 'conditional_joint_decision', result)
        self.assertEqual(result['conditional_best']['refresh_count'], 1)
        self.assertEqual(result['conditional_best']['budget'], {'purchase': 1, 'experience': 0, 'refresh': 2})
        for row in result['conditional_candidates']:
            self.assertEqual(sum(row['budget'].values()) + row['coins_retained'], 3)
            self.assertGreaterEqual(row['coins_retained'], 0)
            for outcome in row.get('future_scenarios', []):
                self.assertLessEqual(outcome['price'], row['future_purchase_budget'])
        self.assertFalse(result['execution_eligible'])
        self.assertFalse(result['native_mechanics_verified'])
        self.assertFalse(result['search']['stochastic_expectation_exact'])

    def test_upgrade_and_free_refresh_are_compared_in_the_same_candidate(self):
        result = self.compare(coins=8, free=True, upgrade_value=True)
        best = result['conditional_best']
        self.assertEqual(best['xp_purchases'], 1, result)
        self.assertEqual(best['refresh_count'], 1)
        self.assertEqual(best['budget']['experience'], 4)
        self.assertEqual(best['budget']['refresh'], 0)
        self.assertGreater(best['future_purchase_budget'], 0)
        self.assertTrue(all(result['complete_dimensions'].values()))
        capped = self.compare(coins=8, free=True, upgrade_value=True, max_node_refreshes=1, refreshes_used=1)
        self.assertEqual(capped['conditional_best']['refresh_count'], 0)
        self.assertEqual(capped['allocation']['refresh_count_max'], 0)

    def test_missing_profile_bounds_and_explicit_reserve_remain_visible(self):
        unmatched = self.compare(threshold=5)
        self.assertEqual(unmatched['status'], 'incomplete_joint_comparison')
        self.assertFalse(any(unmatched['complete_dimensions'].values()))
        bounded = self.compare(coins=8, max_economy_evaluations=1)
        self.assertEqual(bounded['status'], 'incomplete_joint_comparison')
        self.assertIn('economic_marginal_search_limit', bounded['blocked_reasons'])
        reserved = self.compare(coins=3, reserve=2)
        self.assertEqual(reserved['conditional_best']['refresh_count'], 0)
        limited = self.compare(coins=8, purchase_budget=0)
        self.assertEqual(limited['conditional_best']['future_purchase_budget'], 0)

    def state(self):
        """Synthetic native geometry for the public read-only advice path."""
        state = current_shop_state()
        state['native_fields'] = {'deployed': '0/3'}
        state['fields']['population']['source'] = 'perception'
        state['fields']['coins']['proof'].update(method='fixed_hud_complete_digit_ocr', confidence=.99,
                                                bounds=[1613, 892, 1687, 950], raw_text='5')
        for slot in state['semantic']['team']['slots']:
            left, top, step, width, height = {'front': (678, 327, 145, 132, 143),
                'back': (536, 597, 144, 132, 145), 'bench': (381, 844, 125, 116, 137)}[slot['row']]
            left += (slot['slot']-1)*step
            slot.update(origin='native_visual_state_reader', bounds=[left, top, left+width, top+height],
                        point=[left+width//2, top+height//2])
        state['semantic']['team']['units'] = [copy.deepcopy(s) for s in state['semantic']['team']['slots'] if s['status'] == 'occupied']
        state['semantic']['experience_offer'] = {'origin': 'native_experience_offer', 'snapshot_id': SNAP,
            'cost_checked': True, 'buy_cost': 4, 'buy_gain': None}
        state['semantic']['refresh_offer'] = {'schema': 'currency-wars-refresh-offer/v1', 'snapshot_id': SNAP,
            'page': 'shop', 'status': 'known', 'mode': 'paid', 'paid_cost': 2, 'free_remaining': None}
        return state

    def test_public_mcp_advice_evaluates_prior_without_input_or_native_rewrite(self):
        state = self.state()
        original = copy.deepcopy(state)
        adapter = Adapter()
        adapter.state = AsyncMock(return_value=state)
        result = asyncio.run(adapter.advice({'mode': 'replay', 'economy_model': 'released_prior', 'bond_weight': 0}))
        self.assertEqual(result['economic_allocation']['status'], 'conditional_joint_decision', result)
        self.assertFalse(result['input_sent'])
        self.assertFalse(result['execution_eligible'])
        self.assertEqual(state, original)
        self.assertIsNone(state['semantic']['experience_offer']['buy_gain'])

    def test_comparison_view_keeps_prior_roster_snapshot_and_rejects_changed_epoch(self):
        from currency_wars_advisor import advise
        state = self.state()
        old = 'b' * 64
        team = copy.deepcopy(state['semantic']['team'])
        team['snapshot_id'] = old
        for rows in ('slots', 'units'):
            for s in team[rows]: s['snapshot_id'] = old
        prior = {'snapshot_id': old, 'capture_request_id': 'before', 'frame_id': 'before-frame',
            'captured_at': NOW.isoformat(), 'stage': '1-1', 'match_id': 'synthetic-match', 'resume_epoch': 0}
        state['comparison_view'] = {'schema': 'currency-wars-comparison-view/v1',
            'origin': 'authenticated_runner_view_pair', 'binding': copy.deepcopy(state['binding']),
            'team': team, 'population': {'value': '0/3', 'snapshot_id': old, 'source': 'receipt_bound_preparation_view'},
            'roster_provenance': prior, 'current_provenance': {**prior, 'snapshot_id': SNAP,
                'capture_request_id': 'now', 'frame_id': 'now-frame', 'captured_at': (NOW+timedelta(seconds=20)).isoformat()},
            'navigation_receipt_ids': ['synthetic-nav'], 'raw_native_unchanged': True}
        state['native_fields']['deployed'] = None
        state['fields']['population']['known'] = False
        original = copy.deepcopy(state)
        policy = {'mode': 'replay', 'economy_model': 'released_prior', 'bond_weight': 0}
        result = advise(state, Catalogue(), policy)
        self.assertEqual(result['economic_allocation']['status'], 'conditional_joint_decision')
        self.assertTrue(result['comparison_source']['accepted_for_comparison'])
        self.assertTrue(all(e['observed']['snapshot_id'] == old for e in result['observed_entities']))
        self.assertEqual(state, original)
        state['comparison_view']['current_provenance']['resume_epoch'] = 1
        rejected = advise(state, Catalogue(), policy)
        self.assertFalse(rejected['comparison_source']['accepted_for_comparison'])
        self.assertEqual(rejected['economic_allocation']['status'], 'incomplete_joint_comparison')


    def test_public_value_modifier_changes_joint_scores_without_rewriting_state(self):
        state = self.state()
        adapter = Adapter()
        adapter.state = AsyncMock(return_value=state)
        policy = {'mode': 'replay', 'economy_model': 'released_prior', 'bond_weight': 0}
        baseline = asyncio.run(adapter.advice(policy))
        self.assertIn('observed_entities', baseline, baseline)
        entity = baseline['observed_entities'][0]
        modifier = {**state['binding'], 'entity_ref': entity['entity_ref'],
                    'role_id': entity['catalogue']['role_id'],
                    'status': 'hypothetical', 'utility_multiplier': 1.5}
        self.assertTrue(baseline['joint_economy']['portfolios'])
        original = copy.deepcopy(state)
        result = asyncio.run(adapter.advice({**policy, 'candidate_value_modifiers': [modifier]}))
        self.assertEqual(result['candidate_value_modifier_diagnostics'], [])
        before = {p['id']: p['approximate_score'] for p in baseline['joint_economy']['portfolios']}
        after = {p['id']: p['approximate_score'] for p in result['joint_economy']['portfolios']}
        self.assertNotEqual(before, after)
        self.assertFalse(result['input_sent'])
        self.assertFalse(result['execution_eligible'])
        self.assertEqual(state, original)

    def test_public_value_modifier_rejects_incomplete_or_conflicting_current_binding(self):
        state = self.state()
        adapter = Adapter()
        adapter.state = AsyncMock(return_value=state)
        policy = {'mode': 'replay', 'economy_model': 'released_prior', 'bond_weight': 0}
        baseline = asyncio.run(adapter.advice(policy))
        self.assertIn('observed_entities', baseline, baseline)
        entity = baseline['observed_entities'][0]
        modifier = {**state['binding'], 'entity_ref': entity['entity_ref'],
                    'role_id': entity['catalogue']['role_id'],
                    'status': 'hypothetical', 'utility_multiplier': 1.5}
        for kind in ('empty_binding', 'state_snapshot', 'binding_snapshot', 'state_match',
                     'binding_match', 'missing_observation', 'missing_observation_snapshot',
                     'observation_snapshot', 'observation_match', 'observation_request',
                     'observation_bool_epoch', 'binding_bool_epoch'):
            with self.subTest(kind=kind):
                bad = copy.deepcopy(state)
                assumption = copy.deepcopy(modifier)
                if kind == 'empty_binding':
                    bad['binding'] = {}
                    assumption.update(request_id=None, match_id=None, resume_epoch=None)
                elif kind == 'state_snapshot':
                    bad['snapshot_id'] = 'other-snapshot'
                elif kind == 'binding_snapshot':
                    bad['binding']['snapshot_id'] = 'other-snapshot'
                    assumption['snapshot_id'] = 'other-snapshot'
                elif kind == 'state_match':
                    bad['match_id'] = 'other-match'
                elif kind == 'binding_match':
                    bad['binding']['match_id'] = 'other-match'
                    assumption['match_id'] = 'other-match'
                elif kind == 'missing_observation':
                    bad.pop('observation')
                elif kind == 'missing_observation_snapshot':
                    bad['observation'].pop('snapshot_id')
                elif kind == 'observation_snapshot':
                    bad['observation']['snapshot_id'] = 'other-snapshot'
                elif kind == 'observation_match':
                    bad['observation']['match_id'] = 'other-match'
                elif kind == 'observation_request':
                    bad['observation']['request_id'] = 'other-request'
                elif kind == 'observation_bool_epoch':
                    bad['observation']['resume_epoch'] = False
                elif kind == 'binding_bool_epoch':
                    bad['binding']['resume_epoch'] = False
                    assumption['resume_epoch'] = False
                original = copy.deepcopy(bad)
                adapter.state = AsyncMock(return_value=bad)
                unmodified = asyncio.run(adapter.advice(policy))
                result = asyncio.run(adapter.advice(
                    {**policy, 'candidate_value_modifiers': [assumption]}))
                self.assertTrue(result['candidate_value_modifier_diagnostics'])
                self.assertEqual(result['joint_economy'], unmodified['joint_economy'])
                self.assertEqual(result['economic_allocation'], unmodified['economic_allocation'])
                self.assertFalse(result['input_sent'])
                self.assertFalse(result['execution_eligible'])
                self.assertEqual(bad, original)

    def test_public_value_modifier_rejects_invalid_assumption_without_score_change(self):
        state = self.state()
        adapter = Adapter()
        adapter.state = AsyncMock(return_value=state)
        policy = {'mode': 'replay', 'economy_model': 'released_prior', 'bond_weight': 0}
        baseline = asyncio.run(adapter.advice(policy))
        self.assertIn('observed_entities', baseline, baseline)
        entity = baseline['observed_entities'][0]
        modifier = {**state['binding'], 'entity_ref': entity['entity_ref'],
                    'role_id': entity['catalogue']['role_id'],
                    'status': 'hypothetical', 'utility_multiplier': 1.5}
        original = copy.deepcopy(state)
        for field, value, diagnostic in (
                ('resume_epoch', False, '0:invalid_epoch'),
                ('role_id', 'not-this-character', '0:role_mismatch'),
                ('utility_multiplier', 10**400, '0:invalid_multiplier')):
            with self.subTest(field=field):
                result = asyncio.run(adapter.advice(
                    {**policy, 'candidate_value_modifiers': [{**modifier, field: value}]}))
                self.assertIn(diagnostic, result['candidate_value_modifier_diagnostics'])
                self.assertEqual(result['joint_economy'], baseline['joint_economy'])
                self.assertEqual(result['economic_allocation'], baseline['economic_allocation'])
                self.assertFalse(result['input_sent'])
                self.assertFalse(result['execution_eligible'])
                self.assertEqual(state, original)

    def test_public_value_modifier_accepts_matching_optional_observation_identity(self):
        state = self.state()
        adapter = Adapter()
        adapter.state = AsyncMock(return_value=state)
        policy = {'mode': 'replay', 'economy_model': 'released_prior', 'bond_weight': 0}
        baseline = asyncio.run(adapter.advice(policy))
        self.assertIn('observed_entities', baseline, baseline)
        entity = baseline['observed_entities'][0]
        modifier = {**state['binding'], 'entity_ref': entity['entity_ref'],
                    'role_id': entity['catalogue']['role_id'],
                    'status': 'hypothetical', 'utility_multiplier': 1.5}
        state['observation'].update({key: state['binding'][key]
                                     for key in ('match_id', 'request_id', 'resume_epoch')})
        original = copy.deepcopy(state)
        result = asyncio.run(adapter.advice({**policy, 'candidate_value_modifiers': [modifier]}))
        self.assertEqual(result['candidate_value_modifier_diagnostics'], [])
        self.assertNotEqual(
            [p['approximate_score'] for p in result['joint_economy']['portfolios']],
            [p['approximate_score'] for p in baseline['joint_economy']['portfolios']])
        self.assertFalse(result['input_sent'])
        self.assertFalse(result['execution_eligible'])
        self.assertEqual(state, original)


if __name__ == '__main__':
    unittest.main()
