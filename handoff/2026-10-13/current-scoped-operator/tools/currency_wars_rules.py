"""Compile released catalogue facts into reusable, explicitly partial rules.

No game connection, input, language model, inferred script, damage formula or
live-state certificate. A supported component never certifies the whole effect.
"""
from __future__ import annotations

import copy
import json
import math
import re

from currency_wars_database import normalize_name

SCHEMA = 'currency-wars-released-rules/v1'
KINDS = ('characters', 'bonds', 'equipment', 'investment_environments',
         'investment_strategies', 'enemy_affixes', 'enemy_units', 'opponents', 'economy_rules', 'shop_odds')


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _source(data):
    return {k: copy.deepcopy(data.get('source', {}).get(k))
            for k in ('revision', 'version', 'client_path', 'row_key', 'file_sha256')}


def _properties(rows):
    """Preserve raw modifier semantics; do not guess stacking/final values."""
    compiled, unresolved = [], []
    for row in rows:
        value = row.get('Value', {}).get('Value') if isinstance(row.get('Value'), dict) else None
        prop = row.get('PropertyType')
        if isinstance(prop, str) and prop and _finite(value):
            compiled.append({'property': prop, 'value': value,
                             'operation': 'released_modifier_component',
                             'final_stat_formula_verified': False})
        else:
            unresolved.append(copy.deepcopy(row))
    return compiled, unresolved


def _template(details):
    template = details.get('template')
    if not isinstance(template, str):
        return None
    # Only presentation tags. Semantic property/entity/dynamic tags remain and
    # therefore cannot accidentally match a fixed-resource rule.
    return re.sub(r'</?(?:color|unbreak)(?:=[^>]*)?>', '', template)


def compile_record(row):
    data = row['data']
    kind = row['kind']
    result = {'key': kind + ':' + str(row['source_key']), 'kind': kind,
              'name': row['name'], 'id': str(data.get('id', row['source_key'])),
              'version': row['version'], 'availability': row['availability'],
              'source': _source(data), 'components': [], 'unresolved': [],
              'complete_effect_model': False, 'current_game_verified': False}
    if row['availability'] != 'current':
        result['unresolved'].append('not_current_release_definition')
        return result
    if kind == 'characters':
        result['position'] = data.get('position')
        result['bonds'] = copy.deepcopy(data.get('bonds', []))
        result['base_cost'] = row.get('cost')
        for star in data.get('star_effects', []):
            props = {k: star[k] for k in ('FrontPowerBase', 'BackPowerBase')
                     if _finite(star.get(k))}
            if type(star.get('star')) is int and props:
                result['components'].append({'type': 'star_base_properties',
                    'star': star['star'], 'properties': props})
        result['unresolved'].append('skills_timing_targets_runtime_and_combat_formula')
    elif kind == 'bonds':
        result['activation_type'] = data.get('activation_type')
        result['thresholds'] = []
        for tier in data.get('thresholds', []):
            count = tier.get('members')
            if type(count) is not int or count <= 0:
                result['unresolved'].append('invalid_member_threshold')
                continue
            raw = tier.get('raw', {})
            modifiers = {}
            for scope, field in (('trait_members', 'TraitMemberPropertyList'),
                                 ('whole_team', 'AllMemberPropertyList')):
                values, unresolved = _properties(raw.get(field, []))
                modifiers[scope] = values
                if unresolved:
                    result['unresolved'].append('unparsed_' + scope + '_properties')
            result['thresholds'].append({'members': count, 'quality': tier.get('quality'),
                'effects': tier.get('effects'), 'property_components': modifiers,
                'source': _source(tier), 'replacement_not_cumulative': True})
        result['thresholds'].sort(key=lambda tier: tier['members'])
        if len({tier['members'] for tier in result['thresholds']}) != len(result['thresholds']):
            result['unresolved'].append('duplicate_member_threshold')
        if result['activation_type'] == 'GreaterEqualThan' and result['thresholds']:
            result['components'].append({'type': 'base_member_thresholds',
                'count_model': 'distinct_deployed_name_families_with_base_catalogue_membership',
                'context_adjustments_verified': False})
        else:
            result['unresolved'].append('unsupported_activation_type_or_empty_thresholds')
        result['unresolved'].append('emblems_dynamic_membership_investments_and_script_effects')
    elif kind == 'equipment':
        modifiers = []
        for item in data.get('general_property_modifiers', []):
            if (item.get('value_explicitly_present') is True
                    and isinstance(item.get('property_type'), str)
                    and _finite(item.get('value'))):
                modifiers.append({'property': item['property_type'], 'value': item['value'],
                    'operation': 'released_modifier_component', 'final_stat_formula_verified': False})
            else:
                result['unresolved'].append('unparsed_property_modifier')
        if modifiers:
            result['components'].append({'type': 'equipment_property_components', 'modifiers': modifiers})
        result['recipes'] = copy.deepcopy(data.get('recipe_variants', []))
        result['enable_conditions'] = copy.deepcopy(data.get('enable_conditions'))
        result['unresolved'].append('dress_eligibility_stacking_and_script_effects')
    elif kind in ('investment_environments', 'investment_strategies'):
        result['effects'] = data.get('effects')
        result['enable_conditions'] = copy.deepcopy(data.get('enable_conditions'))
        result['components'] = compile_investment(data.get('effect_details', {}))
        result['unresolved'].append('released_description_not_verified_script_execution')
        if not result['components']:
            result['unresolved'].append('unsupported_investment_description')
    elif kind in ('economy_rules', 'shop_odds'):
        result['components'].append({'type': 'alternative_released_economy_configuration',
            'value': copy.deepcopy(data.get('value', data)), 'effective_profile_verified': False})
        result['unresolved'].append('current_mode_effective_profile_and_dynamic_modifiers')
    else:
        result['effects'] = data.get('effects')
        result['unresolved'].append('enemy_or_faction_combat_behavior_not_compiled')
    result['config_reference'] = copy.deepcopy(data.get('config_reference'))
    return result


def compile_investment(details):
    """Strict whole-description grammar. No substring-triggered resource gain."""
    if (details.get('requires_runtime_context') or details.get('unresolved_placeholders')
            or details.get('unresolved_entity_references')):
        return []
    template = _template(details)
    parameters = details.get('parameters')
    if template is None or not isinstance(parameters, list):
        return []
    token = r'#(\d+)(?:\[i\])?'
    def components(pattern, expected, specifications):
        matched = re.fullmatch(pattern, template)
        if (not matched or len(parameters) != expected or
                not all(type(value) is int and 0 <= value <= 100000 for value in parameters)
                or list(map(int, matched.groups())) != list(range(1, expected + 1))):
            return []
        events = []
        for event, resource, index, conditions in specifications:
            events.append({'type': 'described_fixed_resource_event', 'event': event,
                'resource': resource, 'amount': parameters[index-1],
                'conditions': copy.deepcopy(conditions), 'execution_verified': False,
                'template': template, 'parameter_index': index})
        return events

    # Only complete verified descriptions. All clauses are represented; event
    # times and limited counters remain separate instead of instant cash.
    extended = (
        (r'第二位面开始时，获得' + token + r'个特权武装箱。', 1,
         [('plane_start', 'privileged_armament_boxes', 1,
           {'counter': 'plane_number', 'minimum': 2, 'maximum': 2})]),
        (r'获得' + token + r'经验值。', 1,
         [('selection_acquired', 'experience', 1, {})]),
        (r'立刻获得' + token + r'个金币，并且在每次进入新节点时，获得' + token + r'金币。', 2,
         [('selection_acquired', 'gold', 1, {}), ('node_enter', 'gold', 2, {})]),
        (r'现在及每次进入新节点时获得' + token + r'次免费刷新，立刻获得' + token + r'金币。', 2,
         [('selection_acquired', 'free_refreshes', 1, {}), ('selection_acquired', 'gold', 2, {}),
          ('node_enter', 'free_refreshes', 1, {})]),
        (r'现在及接下来' + token + r'次进入新节点时，每次获得' + token + r'金币。', 2,
         [('selection_acquired', 'gold', 2, {}), ('node_enter', 'gold', 2,
          {'counter': 'node_entries_after_acquisition', 'minimum': 1, 'maximum_parameter': 1})]),
        (r'现在以及每个位面开始时，获得' + token + r'经验值和' + token + r'次免费刷新。', 2,
         [('selection_acquired', 'experience', 1, {}), ('selection_acquired', 'free_refreshes', 2, {}),
          ('plane_start', 'experience', 1, {}), ('plane_start', 'free_refreshes', 2, {})]),
        (r'你每次消耗金币刷新商店，都会获得' + token + r'经验值。', 1,
         [('paid_shop_refresh', 'experience', 1, {})]),
    )
    for pattern, expected, specs in extended:
        events = components(pattern, expected, specs)
        if events:
            for component in events:
                condition = component['conditions']
                if 'maximum_parameter' in condition:
                    condition['maximum'] = parameters[condition.pop('maximum_parameter') - 1]
            return events
    grammars = (
        (r'进入首领节点时，获得' + token + r'枚金币。', 'boss_node_enter', 'gold'),
        (r'进入最后一个补给阶段后，获得' + token + r'金币。', 'final_supply_enter', 'gold'),
        (r'获得' + token + r'(?:枚)?金币。', 'selection_acquired', 'gold'),
        (r'开局时(?:额外)?获得' + token + r'(?:枚)?金币。', 'match_start', 'gold'),
        (r'获得' + token + r'次免费刷新。', 'selection_acquired', 'free_refreshes'),
        (r'开局时获得' + token + r'个简易武装箱。', 'match_start', 'simple_armament_boxes'),
    )
    for pattern, event, resource in grammars:
        matched = re.fullmatch(pattern, template)
        if not matched:
            continue
        index = int(matched.group(1)) - 1
        if not 0 <= index < len(parameters):
            return []
        value = parameters[index]
        if type(value) is not int or not 0 <= value <= 100000:
            return []
        # Extra unused parameters would make the fixed interpretation incomplete.
        if len(parameters) != 1 or index != 0:
            return []
        return [{'type': 'described_fixed_resource_event', 'event': event,
                 'resource': resource, 'amount': value,
                 'conditions': {}, 'execution_verified': False, 'template': template,
                 'parameter_index': index + 1}]
    return []


def evaluate_investment_events(pack, names, event, counters=None):
    """Describe one hypothetical event; never credit a wallet or advance counters."""
    allowed = {'selection_acquired', 'match_start', 'node_enter', 'boss_node_enter',
               'final_supply_enter', 'plane_start', 'paid_shop_refresh'}
    if event not in allowed:
        raise ValueError('unsupported investment event')
    if not isinstance(names, list) or len(names) > 64 or not all(
            isinstance(name, str) and 0 < len(name.strip()) <= 200 for name in names):
        raise ValueError('investment names must be bounded explicit strings')
    counters = {} if counters is None else counters
    if (not isinstance(counters, dict) or set(counters) - {'node_entries_after_acquisition', 'plane_number'}
            or not all(type(value) is int and 0 <= value <= 100000 for value in counters.values())):
        raise ValueError('invalid or unsupported investment counters')
    normalized = [normalize_name(name) for name in names]
    if len(set(normalized)) != len(normalized):
        raise ValueError('duplicate investment selection multiplicity is unsupported')
    names = sorted(normalized)
    contributions, unknown, totals = [], [], {}
    for name in names:
        candidates = [rule for rule in pack['rules'] if rule['kind'] in
            ('investment_environments', 'investment_strategies') and normalize_name(rule['name']) == name]
        if len(candidates) != 1 or not candidates[0]['components']:
            unknown.append({'name': name, 'reason': 'missing_ambiguous_or_unsupported_description'})
            continue
        rule = candidates[0]
        for component in rule['components']:
            if component['event'] != event:
                continue
            condition = component.get('conditions', {})
            if condition:
                value = counters.get(condition['counter'])
                if value is None:
                    unknown.append({'name': name, 'reason': 'missing_event_counter',
                                    'counter': condition['counter']})
                    continue
                if not condition['minimum'] <= value <= condition['maximum']:
                    continue
            resource, amount = component['resource'], component['amount']
            totals[resource] = totals.get(resource, 0) + amount
            contributions.append({'key': rule['key'], 'name': rule['name'],
                'resource': resource, 'described_amount': amount, 'source': copy.deepcopy(rule['source'])})
    return {'schema': 'currency-wars-investment-event-scenario/v1',
        'database_sha256': pack['database_sha256'], 'event_assumption': event,
        'counter_assumptions': copy.deepcopy(counters), 'described_resource_amounts': totals,
        'contributions': contributions, 'unresolved': unknown,
        'all_selected_descriptions_compiled': not unknown, 'execution_verified': False,
        'input_sent': False, 'execution_eligible': False,
        'scope': 'One hypothetical event; no repeated credit, game effect, counter progression or active selection proof.'}


def compile_catalogue(db, database_sha256):
    rules = []
    counts = {}
    for row in db.execute('SELECT * FROM entities WHERE availability=? ORDER BY kind,source_key', ('current',)):
        record = dict(row)
        if record['kind'] not in KINDS:
            continue
        record['data'] = json.loads(record.pop('data_json'))
        rule = compile_record(record)
        rules.append(rule)
        total = counts.setdefault(rule['kind'], {'entries': 0, 'with_compiled_components': 0,
                                                'complete_effect_models': 0})
        total['entries'] += 1
        total['with_compiled_components'] += bool(rule['components'])
    return {'schema': SCHEMA, 'database_sha256': database_sha256, 'input_sent': False,
            'execution_eligible': False, 'coverage': counts, 'rules': rules,
            'scope': 'Released component rules and conditional scenarios; not current game effects or full combat.'}


def evaluate_bonds(pack, names):
    """Base catalogue membership for an explicit hypothetical deployed list.

    Duplicate copies count once. Ambiguous variants preserve a count interval.
    Unknown names stay unknown; reported intervals exclude their contributions.
    No emblem or investment is assumed absent and no current activation claimed.
    """
    if not isinstance(names, list) or len(names) > 64 or not all(
            isinstance(name, str) and 0 < len(name.strip()) <= 200 for name in names):
        raise ValueError('deployed names must be a bounded explicit string list')
    families = sorted({normalize_name(name) for name in names})
    characters, bonds = {}, []
    for rule in pack['rules']:
        if rule['kind'] == 'characters':
            characters.setdefault(normalize_name(rule['name']), []).append(rule)
        elif rule['kind'] == 'bonds':
            bonds.append(rule)
    unresolved, membership = [], {}
    for name in families:
        variants = characters.get(name, [])
        if not variants:
            unresolved.append(name)
            continue
        sets = [set(v.get('bonds', [])) for v in variants]
        membership[name] = (set.intersection(*sets), set.union(*sets))
    evaluated = []
    for bond in bonds:
        definite = [name for name, (base, _) in membership.items() if bond['name'] in base]
        possible = [name for name, (_, base) in membership.items() if bond['name'] in base]
        if not possible:
            continue
        tiers = bond.get('thresholds', [])
        supported = bond.get('activation_type') == 'GreaterEqualThan' and not any(
            issue in bond['unresolved'] for issue in ('duplicate_member_threshold', 'invalid_member_threshold'))
        attained = []
        for count in (len(definite), len(possible)):
            eligible = [t for t in tiers if t['members'] <= count] if supported else []
            attained.append(eligible[-1] if eligible else None)
        following = next((t for t in tiers if t['members'] > len(definite)), None) if supported else None
        evaluated.append({'key': bond['key'], 'name': bond['name'],
            'base_count_interval': [len(definite), len(possible)],
            'definite_base_member_families': definite, 'possible_base_member_families': possible,
            'base_attained_threshold_interval': [t['members'] if t else None for t in attained],
            'next_base_threshold': following['members'] if following else None,
            'additional_distinct_base_members_needed': following['members'] - len(definite) if following else None,
            'base_attained_property_components': copy.deepcopy(attained[0]['property_components']) if attained[0] else None,
            'source': copy.deepcopy(bond['source']), 'current_activation_verified': False,
            'complete_effect_model': False})
    return {'schema': 'currency-wars-base-bond-scenario/v1',
            'database_sha256': pack['database_sha256'], 'distinct_name_families': families,
            'unknown_name_families': unresolved, 'known_catalogue_membership_complete': not unresolved,
            'bonds': evaluated, 'input_sent': False, 'execution_eligible': False,
            'scope': 'Hypothetical base membership only; no current stars/emblems/investment/special membership inference.'}
