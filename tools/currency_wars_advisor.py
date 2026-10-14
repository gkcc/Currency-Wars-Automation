"""Bounded, read-only partial advice. Never a runner plan or execution certificate.

advise accepts the existing currency-wars-mcp/v1 projection and a Catalogue.
policy.mode='replay' explicitly permits historical/saved-image comparisons.
No capture, controller, SDK, network, model API, automatic merge or sale occurs.
Scores describe only sourced, visible components and declared hypothetical
positions/stars. Their intervals are NOT damage or real-performance bounds.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time

from currency_wars_database import normalize_name
from currency_wars_shop_contract import purchase_slot, priced_read_slot

SCHEMA = 'currency-wars-partial-advice/v1'
POSITIONS = {'前台': ('front',), '后台': ('back',), '前后台': ('front', 'back')}
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_ENTITIES = 128
MAX_VARIANTS = 16
MAX_OFFERS = 5
COMMON_REQUIREMENTS = (
    'current_authenticated_owner_request_match_stage_epoch',
    'accepted_same_scope_business_resume', 'no_pending_or_unknown_input',
    'ROOT_current_action_acceptance', 'original_executor_guard_and_result_readback')
BUY_REQUIREMENTS = COMMON_REQUIREMENTS + (
    'current_rewards_and_inventory_cleanup_accepted', 'native_current_wallet',
    'native_offer_name_price_complete_ROI', 'current_empty_bench_or_certified_storage',
    'accepted_joint_economy_budget_and_reserve', 'population_for_joint_comparison',
    'purchase_star_and_trial_terms_unverified')
DEPLOY_REQUIREMENTS = COMMON_REQUIREMENTS + (
    'current_source_card_name_star_position_ROI', 'current_legal_empty_board_target_ROI',
    'native_current_population_with_free_place', 'no_same_name_already_deployed')


def _finite(value, low=0, high=1_000_000):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _integer(value, low=0, high=1_000_000):
    return type(value) is int and low <= value <= high


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 200


def _box(value):
    return (isinstance(value, list) and len(value) == 4
            and all(type(v) is int for v in value)
            and 0 <= value[0] < value[2] <= 1920
            and 0 <= value[1] < value[3] <= 1080)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value, maximum, field):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(field + ': unsupported size/type; no entries silently discarded')
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(field + ': entries must be objects')
    return value


def _policy(value):
    value = {} if value is None else copy.deepcopy(value)
    if not isinstance(value, dict) or set(value) - {
            'mode', 'gold_weight', 'bench_weight', 'purchase_budget', 'reserve',
            'purchase_star_for_scoring', 'bond_weight', 'bond_reference_power',
            'max_layout_states', 'max_layout_transitions', 'economy_model',
            'released_profile', 'max_xp_purchases', 'max_node_refreshes',
            'refreshes_used', 'max_refresh_samples', 'max_economy_evaluations', 'candidate_value_modifiers'}:
        raise ValueError('policy: unsupported keys/type')
    result = {'mode': 'current', 'gold_weight': 20., 'bench_weight': .15,
              'purchase_star_for_scoring': 1, 'purchase_budget': None, 'reserve': None, **value}
    result = {'bond_weight': 1., 'bond_reference_power': 100.,
              'max_layout_states': 1024, 'max_layout_transitions': 250000,
              'economy_model': 'partial', 'released_profile': 'matching',
              'max_xp_purchases': 12, 'max_node_refreshes': 3, 'refreshes_used': 0,
              'max_refresh_samples': 8, 'max_economy_evaluations': 4096,
              'candidate_value_modifiers': [], **result}
    if result['mode'] not in ('current', 'replay'):
        raise ValueError('policy.mode must be current or replay')
    if result['economy_model'] not in ('partial', 'released_prior'):
        raise ValueError('policy.economy_model')
    if result['released_profile'] not in ('matching', 'player_levels', 'level_v2'):
        raise ValueError('policy.released_profile')
    for key, maximum in (('gold_weight', 10000), ('bench_weight', 1),
                         ('bond_weight', 100), ('bond_reference_power', 10000)):
        if not _finite(result[key], high=maximum):
            raise ValueError('policy.' + key)
    if not _integer(result['purchase_star_for_scoring'], 1, 4):
        raise ValueError('policy.purchase_star_for_scoring')
    for key, minimum, maximum in (('max_layout_states', 35, 8192),
                                  ('max_layout_transitions', 1, 1000000),
                                  ('max_xp_purchases', 0, 16), ('max_node_refreshes', 0, 8),
                                  ('refreshes_used', 0, 8), ('max_refresh_samples', 1, 8),
                                  ('max_economy_evaluations', 1, 4096)):
        if not _integer(result[key], minimum, maximum):
            raise ValueError('policy.' + key)
    for key in ('purchase_budget', 'reserve'):
        if result[key] is not None and not _integer(result[key]):
            raise ValueError('policy.' + key)
    return result


def _binding(state, replay):
    binding = _dict(state.get('binding'))
    missing = []
    if state.get('status') != 'observed' or state.get('source') != 'authenticated_runner_status':
        missing.append('authenticated_runner_status_claim_missing')
    for key in ('request_id', 'snapshot_id', 'match_id'):
        if not _text(binding.get(key)):
            missing.append('binding.' + key)
    epoch = binding.get('resume_epoch')
    if not (_integer(epoch) or _text(epoch)):
        missing.append('binding.resume_epoch')
    if (not _text(state.get('snapshot_id')) or binding.get('snapshot_id') != state.get('snapshot_id')
            or _dict(state.get('observation')).get('snapshot_id') != state.get('snapshot_id')
            or not _text(state.get('match_id')) or binding.get('match_id') != state.get('match_id')):
        missing.append('binding.atomic_snapshot_and_match')
    freshness = _dict(state.get('freshness'))
    now = datetime.now(timezone.utc)
    for key in ('captured_at', 'request_deadline'):
        try:
            value = datetime.fromisoformat(freshness[key].replace('Z', '+00:00'))
            if value.tzinfo is None:
                raise ValueError('timezone missing')
            valid = 0 <= (now - value).total_seconds() <= 60 if key == 'captured_at' else now < value
            if not valid:
                missing.append('freshness.' + key + '.stale_or_future')
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            missing.append('freshness.' + key + '.missing_or_invalid')
    return {'mode': 'historical_non_executable' if replay else 'current_claim_unverified',
            'binding': copy.deepcopy(binding), 'freshness': copy.deepcopy(freshness),
            'diagnostics': missing, 'projection_binding_consistent_and_fresh': not missing,
            'authentication_verified_by_advisor': False,
            'note': 'Projection provenance only; this module never authenticates a broker or authorizes input.'}


def _native_slot(slot, snapshot):
    row, index = slot.get('row'), slot.get('slot')
    maximum = {'front': 4, 'back': 6, 'bench': 9}.get(row, 0)
    bounds, point = slot.get('bounds'), slot.get('point')
    if not _integer(index, 1, maximum):
        return False
    # Only the existing source-backed 1920x1080 native slot geometry. Other
    # layouts remain observations, never invented targets/storage certificates.
    left, top, step, width, height = {'front': (678, 327, 145, 132, 143),
        'back': (536, 597, 144, 132, 145), 'bench': (381, 844, 125, 116, 137)}[row]
    left += (index-1)*step
    expected = [left, top, left+width, top+height]
    return (bool(snapshot) and slot.get('snapshot_id') == snapshot
            and slot.get('origin') == 'native_visual_state_reader'
            and slot.get('location') == ('bench' if row == 'bench' else 'board')
            and _box(bounds) and bounds == expected and isinstance(point, list) and len(point) == 2
            and all(type(v) is int for v in point)
            and bounds[0] < point[0] < bounds[2] and bounds[1] < point[1] < bounds[3])


def _address(slot):
    return tuple(slot.get(k) for k in ('location', 'row', 'slot', 'snapshot_id'))


def _roster(team):
    """Alias matching slot/unit records; preserve conflicting/unmatched records."""
    slots = _list(team.get('slots'), MAX_ENTITIES, 'team.slots')
    units = _list(team.get('units'), MAX_ENTITIES, 'team.units')
    entries, occupied = [], {}
    for index, slot in enumerate(slots):
        if slot.get('status') != 'occupied':
            continue
        item = {'entity_ref': f'semantic.team.slots[{index}]', 'observed': copy.deepcopy(slot),
                'aliases': [], 'conflicts': []}
        entries.append(item)
        occupied.setdefault(_address(slot), []).append(item)
    equality = ('name', 'star', 'position', 'status', 'bounds', 'point', 'origin')
    for index, unit in enumerate(units):
        prior = occupied.get(_address(unit), [])
        equal = [p for p in prior if all(p['observed'].get(k) == unit.get(k) for k in equality)]
        if len(equal) == 1:
            equal[0]['aliases'].append(f'semantic.team.units[{index}]')
        else:
            item = {'entity_ref': f'semantic.team.units[{index}]', 'observed': copy.deepcopy(unit),
                    'aliases': [], 'conflicts': ['conflicting_slot_record'] if prior else []}
            entries.append(item)
            for p in prior:
                p['conflicts'].append('conflicting_unit_record')
    addresses = {}
    for entry in entries:
        addresses.setdefault(_address(entry['observed']), []).append(entry)
    for same in addresses.values():
        if len(same) > 1:
            for entry in same:
                entry['conflicts'].append('duplicate_physical_address')
    if len(entries) > MAX_ENTITIES:
        raise ValueError('too many roster entries; no entities discarded')
    return entries, slots


def _character(catalogue, name, star, cache):
    result = {'status': 'name_unknown', 'role_id': None, 'variants': [],
              'legal_positions_guaranteed': [], 'legal_positions_possible': [],
              'power_interval': {}, 'missing': [], 'database_sha256': None}
    if not _text(name):
        result['missing'] = ['observed_name']
        return result
    key = (name, star)
    if key in cache:
        return copy.deepcopy(cache[key])
    # Deliberately NO cost argument: native sale/buy price cannot pick a variant.
    response = catalogue.query(name, kind='characters', detail=True, star=None)
    rows = _list(response.get('records'), MAX_VARIANTS, 'catalogue.records')
    current = [row for row in rows if row.get('availability') == 'current']
    result['database_sha256'] = response.get('database_sha256')
    result['excluded_noncurrent'] = [str(row.get('source_key')) for row in rows if row not in current]
    positions = []
    for row in current:
        data = _dict(row.get('data'))
        legal = list(POSITIONS.get(data.get('position'), ()))
        positions.append(set(legal))
        effects = _list(data.get('star_effects'), 4, 'catalogue.star_effects')
        selected = [effect for effect in effects if star is None or effect.get('star') == star]
        power = {}
        for zone in legal:
            field = 'FrontPowerBase' if zone == 'front' else 'BackPowerBase'
            values = [effect.get(field) for effect in selected]
            if values and all(_finite(value) for value in values):
                power[zone] = {'minimum': min(values), 'maximum': max(values)}
            else:
                result['missing'].append(f'catalogue_power:{data.get("id")}:{zone}:{star}')
        result['variants'].append({
            'role_id': str(data.get('id')), 'name': row.get('name'), 'version': row.get('version'),
            'base_cost': row.get('cost'), 'position': data.get('position'), 'bonds': data.get('bonds'),
            'power_interval': power, 'source': copy.deepcopy(data.get('source')),
            'star_effects': [{k: copy.deepcopy(effect[k]) for k in (
                'star', 'FrontPowerBase', 'BackPowerBase', 'front_summary', 'back_summary', 'source')
                if k in effect} for effect in selected]})
    if current:
        result['status'] = 'unique_current_catalogue_candidate' if len(current) == 1 else 'ambiguous_current_variants'
        if len(current) == 1:
            result['role_id'] = result['variants'][0]['role_id']
        result['legal_positions_guaranteed'] = sorted(set.intersection(*positions))
        result['legal_positions_possible'] = sorted(set.union(*positions))
        for zone in result['legal_positions_guaranteed']:
            values = [variant['power_interval'].get(zone) for variant in result['variants']]
            if all(value is not None for value in values):
                result['power_interval'][zone] = {'minimum': min(v['minimum'] for v in values),
                                                   'maximum': max(v['maximum'] for v in values)}
    else:
        result['status'] = 'no_current_catalogue_candidate'
        result['missing'].append('current_catalogue_name_definition')
    result['runtime_variant_verified'] = False
    result['runtime_pool_verified'] = False
    cache[key] = copy.deepcopy(result)
    return result


def _wallet(state, snapshot):
    field = _dict(_dict(state.get('fields')).get('coins'))
    value, proof = field.get('normalized_value'), _dict(field.get('proof'))
    raw = proof.get('raw_text')
    raw = raw.strip() if isinstance(raw, str) else ''
    valid = (field.get('known') is True and _integer(value) and bool(snapshot)
             and proof.get('snapshot_id') == snapshot and proof.get('value') == value
             and proof.get('method') in ('native_hud_row', 'fixed_hud_digit_ocr', 'fixed_hud_complete_digit_ocr')
             and _finite(proof.get('confidence'), .90, 1)
             and proof.get('bounds') == [1613, 892, 1687, 950]
             and re.fullmatch(r'[0-9]{1,7}', raw) is not None and int(raw) == value)
    return {'value': value if valid else None, 'verified_native': valid,
            'observed_field': copy.deepcopy(field), 'missing': [] if valid else ['native_current_wallet']}


def _comparison_view(state):
    """Accept only the separately labelled worker projection; never alter raw state.

    This checks its public provenance envelope, not broker authenticity or the
    original receipt chain. Original executor remains the authentication gate.
    """
    view = _dict(state.get('comparison_view'))
    result = {'accepted_for_comparison': False, 'authentication_verified_by_advisor': False,
              'diagnostics': [], 'scope': 'current_raw_native_view'}
    if not view:
        return result
    prior, current = _dict(view.get('roster_provenance')), _dict(view.get('current_provenance'))
    binding, snapshot = _dict(state.get('binding')), state.get('snapshot_id')
    valid = (view.get('schema') == 'currency-wars-comparison-view/v1'
        and view.get('origin') == 'authenticated_runner_view_pair' and view.get('binding') == binding
        and view.get('raw_native_unchanged') is True and current.get('snapshot_id') == snapshot
        and _text(prior.get('snapshot_id')) and prior.get('snapshot_id') != snapshot
        and _dict(view.get('team')).get('snapshot_id') == prior.get('snapshot_id')
        and _dict(view.get('population')).get('snapshot_id') == prior.get('snapshot_id')
        and _dict(view.get('population')).get('source') == 'receipt_bound_preparation_view'
        and isinstance(view.get('navigation_receipt_ids'), list) and bool(view['navigation_receipt_ids'])
        and all(_text(r) for r in view['navigation_receipt_ids'])
        and prior.get('stage') is not None and prior.get('stage') == current.get('stage')
        and all(p.get('match_id') == binding.get('match_id')
                and p.get('resume_epoch') == binding.get('resume_epoch')
                and _text(p.get('capture_request_id')) and _text(p.get('frame_id')) for p in (prior, current)))
    try:
        t_prior = datetime.fromisoformat(prior['captured_at'].replace('Z', '+00:00'))
        t_current = datetime.fromisoformat(current['captured_at'].replace('Z', '+00:00'))
        valid = valid and t_prior.tzinfo is not None and t_current.tzinfo is not None and 0 <= (t_current-t_prior).total_seconds() <= 180
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        valid = False
    if not valid:
        result['diagnostics'].append('comparison_view_envelope_binding_or_provenance_invalid')
        return result
    return {**result, 'accepted_for_comparison': True, 'scope': 'receipt_bound_preparation_view_plus_current_shop',
            'roster_snapshot_id': prior['snapshot_id'], 'source_view': copy.deepcopy(view),
            'assumption': 'Worker certifies nav-only stability and matching dynamic fields; advisor does not authenticate original receipts.'}


def _population(state, comparison=None):
    if comparison and comparison['accepted_for_comparison']:
        value = comparison['source_view']['population'].get('value')
        match = re.fullmatch(r'(\d{1,2})/(\d{1,2})', value) if isinstance(value, str) else None
        if match and 0 <= int(match[1]) <= int(match[2]) <= 10:
            return {'deployed': int(match[1]), 'maximum': int(match[2]), 'projection_known': True,
                'observed_field': copy.deepcopy(comparison['source_view']['population']),
                'source_scope': comparison['scope'], 'verified_native_current_frame': False,
                'note': 'Separately sourced nav-only comparison projection; original native facts remain unchanged.'}
    field = _dict(_dict(state.get('fields')).get('population'))
    value = field.get('value')
    match = re.fullmatch(r'(\d{1,2})/(\d{1,2})', value) if isinstance(value, str) else None
    valid = (field.get('known') is True and field.get('source') == 'perception' and match is not None
             and _dict(state.get('native_fields')).get('deployed') == value
             and bool(state.get('snapshot_id'))
             and _dict(state.get('observation')).get('snapshot_id') == state.get('snapshot_id'))
    if valid:
        deployed, maximum = map(int, match.groups())
        valid = 0 <= deployed <= maximum <= 10
    return {'deployed': deployed if valid else None, 'maximum': maximum if valid else None,
            'projection_known': bool(valid), 'observed_field': copy.deepcopy(field),
            'note': 'Projection only; original native HUD/owner proof remains required for an action.'}


def _candidate(kind, reference, required, observed, assumptions, score=None, **extra):
    return {'kind': kind, 'reference': reference, 'execution_eligible': False,
            'required_evidence': list(required),
            'missing': [key for key in required if key not in observed],
            'evidence_satisfied_in_projection': sorted(observed),
            'assumptions': list(assumptions), 'approximate_score': score, **extra}




def _candidate_value_modifiers(state, policy=None, valid_entities=None):
    """Read declared comparison assumptions from policy only.

    These are hypothetical comparison modifiers. They never become observed
    state, native facts, or execution authorization.
    """
    policy = _dict(policy)
    raw = policy.get('candidate_value_modifiers', [])
    diagnostics = []
    accepted = {}
    if raw is None:
        return accepted, diagnostics
    if not isinstance(raw, list) or len(raw) > 32:
        return accepted, ['candidate_value_modifiers_invalid']

    binding = _dict(state.get('binding'))
    # A modifier must bind to a complete current observation envelope. Do not
    # allow None==None or bool/int aliasing to satisfy the binding.
    required_binding = ('request_id', 'match_id', 'snapshot_id')
    for key in required_binding:
        if not _text(binding.get(key)):
            diagnostics.append('binding_invalid:' + key)
            return accepted, diagnostics
    binding_epoch = binding.get('resume_epoch')
    if type(binding_epoch) is not int or binding_epoch < 0:
        diagnostics.append('binding_invalid:resume_epoch')
        return accepted, diagnostics
    if (binding.get('snapshot_id') != state.get('snapshot_id')
            or binding.get('match_id') != state.get('match_id')):
        diagnostics.append('binding_invalid:state_mismatch')
        return accepted, diagnostics

    observation = _dict(state.get('observation'))
    if observation.get('snapshot_id') != binding['snapshot_id']:
        diagnostics.append('binding_invalid:observation_snapshot_mismatch')
        return accepted, diagnostics
    # Snapshot is required by the observation contract. Other envelope fields
    # are optional in existing projections, but must agree when supplied.
    for key in ('match_id', 'request_id', 'resume_epoch'):
        if key in observation and (type(observation[key]) is not type(binding[key])
                                   or observation[key] != binding[key]):
            diagnostics.append('binding_invalid:observation_' + key + '_mismatch')
            return accepted, diagnostics

    valid_entities = _dict(valid_entities)
    for index, item in enumerate(raw):
        prefix = f'{index}:'
        if not isinstance(item, dict):
            diagnostics.append(prefix + 'not_object')
            continue
        required = ('request_id', 'match_id', 'resume_epoch', 'snapshot_id',
                    'entity_ref', 'utility_multiplier')
        if any(k not in item for k in required):
            diagnostics.append(prefix + 'missing_required_field')
            continue
        if not all(_text(item.get(k)) for k in ('request_id', 'match_id', 'snapshot_id')):
            diagnostics.append(prefix + 'invalid_binding_text')
            continue
        if type(item.get('resume_epoch')) is not int or item.get('resume_epoch') < 0:
            diagnostics.append(prefix + 'invalid_epoch')
            continue
        if (item.get('request_id') != binding.get('request_id')
                or item.get('match_id') != binding.get('match_id')
                or item.get('resume_epoch') != binding_epoch
                or item.get('snapshot_id') != binding.get('snapshot_id')):
            diagnostics.append(prefix + 'binding_mismatch')
            continue
        if item.get('status') != 'hypothetical':
            diagnostics.append(prefix + 'not_hypothetical')
            continue
        entity = valid_entities.get(item.get('entity_ref'))
        if entity is None:
            diagnostics.append(prefix + 'entity_not_current')
            continue
        if item.get('role_id') != entity.get('role_id'):
            diagnostics.append(prefix + 'role_mismatch')
            continue
        value = item.get('utility_multiplier')
        if type(value) not in (int, float) or isinstance(value, bool):
            diagnostics.append(prefix + 'invalid_multiplier_type')
            continue
        try:
            numeric = float(value)
        except (OverflowError, ValueError, TypeError):
            diagnostics.append(prefix + 'invalid_multiplier')
            continue
        if not math.isfinite(numeric) or numeric <= 0 or numeric > 2:
            diagnostics.append(prefix + 'invalid_multiplier')
            continue
        if item['entity_ref'] in accepted:
            diagnostics.append(prefix + 'duplicate_entity')
            continue
        accepted[item['entity_ref']] = {
            'utility_multiplier': numeric,
            'source': 'declared_hypothetical',
        }
    return accepted, diagnostics

def _score_item(entry, policy, assumed_star=None, value_modifiers=None):
    resolution = entry['catalogue']
    legal = resolution['legal_positions_guaranteed']
    observed_position = entry['observed'].get('position')
    if isinstance(observed_position, str) and observed_position in POSITIONS:
        legal = [zone for zone in legal if zone in POSITIONS[observed_position]]
    powers = resolution['power_interval']
    modifier = _dict(value_modifiers).get(entry['entity_ref'])
    if modifier:
        factor = modifier['utility_multiplier']
        powers = {zone: {'minimum': power['minimum'] * factor, 'maximum': power['maximum'] * factor}
                  for zone, power in powers.items()}
    if (entry.get('source_usable') is not True or entry.get('conflicts')
            or not legal or any(zone not in powers for zone in legal)):
        return None
    return {'ref': entry['entity_ref'], 'family': normalize_name(entry['observed']['name']),
            'powers': {zone: powers[zone] for zone in legal},
            'bench_weight': policy['bench_weight'], 'assumed_star': assumed_star}


def _layouts(items, endpoint, front_capacity=4, back_capacity=6):
    """At most 35 front4/back6 count states; one member per exact name family.

    This is a conditional placement of scored components, with no physical UID,
    target slot, merge or invented identity. Unscored entities remain outside
    this objective and are explicitly retained by the caller.
    """
    baseline, groups = 0., {}
    for item in items:
        bench = max(power[endpoint] for power in item['powers'].values()) * item['bench_weight']
        baseline += bench
        groups.setdefault(item['family'], []).append((item, bench))
    states = {(0, 0): (baseline, ())}
    for family in sorted(groups):
        following = dict(states)
        for (front, back), (score, selected) in states.items():
            for item, bench in groups[family]:
                for zone, power in sorted(item['powers'].items()):
                    counts = (front + (zone == 'front'), back + (zone == 'back'))
                    if counts[0] > front_capacity or counts[1] > back_capacity:
                        continue
                    candidate = (score + power[endpoint] - bench, selected + ((item['ref'], zone),))
                    if counts not in following or candidate > following[counts]:
                        following[counts] = candidate
        states = following
    return {cap: max(value for counts, value in states.items() if sum(counts) <= cap)
            for cap in range(11)}


def _released_prior_allocation(state, catalogue, policy, wallet, population, reserved, models, shop_complete):
    """Joint bounded model, explicitly conditional on a released ordinary IID prior.

    Eight deterministic stratified five-card scenarios approximate one reroll;
    each permits at most one future purchase. Current purchase subsets and XP
    counts are enumerated. A refresh always ends the action prefix and replans.
    """
    result = {'schema': 'currency-wars-economic-allocation/v1',
        'binding': copy.deepcopy(state.get('binding')), 'snapshot_id': state.get('snapshot_id'),
        'status': 'incomplete_joint_comparison', 'input_sent': False, 'execution_eligible': False,
        'complete_dimensions': {'purchase': False, 'experience': False, 'refresh': False},
        'conditional_candidates': [], 'blocked_reasons': [], 'model': 'released_prior',
        'native_mechanics_verified': False, 'native_unknowns': [
            'XP_increment_and_transition_modifiers', 'active_runtime_pool',
            'finite_pool_depletion_and_return', 'refresh_weight_modifiers',
            'future_card_bond_equipment_investment_utility', 'purchased_star_and_trial_terms'],
        'scope': 'Current buy subsets + bounded XP + one refresh with one future buy, under a declared prior; not real combat optimality.'}
    reasons = result['blocked_reasons']
    snapshot, semantic = state.get('snapshot_id'), _dict(state.get('semantic'))
    hud, xp_offer = _dict(semantic.get('player_hud')), _dict(semantic.get('experience_offer'))
    refresh = _dict(semantic.get('refresh_offer'))
    if not wallet['verified_native'] or not population['projection_known']:
        reasons.append('native_wallet_or_population_missing')
    if not shop_complete:
        reasons.append('complete_current_shop_required')
    if not models:
        reasons.append('current_portfolio_models_missing')
    level, xp, xp_cost = hud.get('level'), hud.get('xp'), xp_offer.get('buy_cost')
    if (hud.get('actor') != 'player' or hud.get('origin') != 'native_player_hud'
            or hud.get('snapshot_id') != snapshot or not _integer(level, 1, 9)
            or not isinstance(xp, list) or len(xp) != 2 or not _integer(xp[0])
            or not _integer(xp[1], 1) or xp[0] >= xp[1]):
        reasons.append('native_player_XP_required')
    if (xp_offer.get('origin') != 'native_experience_offer' or xp_offer.get('snapshot_id') != snapshot
            or xp_offer.get('cost_checked') is not True or not _integer(xp_cost, 1)):
        reasons.append('native_XP_price_required')
    mode = refresh.get('mode')
    refresh_valid = (refresh.get('schema') == 'currency-wars-refresh-offer/v1'
        and refresh.get('snapshot_id') == snapshot and refresh.get('page') == 'shop'
        and refresh.get('status') == 'known' and (
            mode == 'free' and _integer(refresh.get('free_remaining'), 1)
            or mode == 'paid' and _integer(refresh.get('paid_cost'), 1)))
    if not refresh_valid:
        reasons.append('native_refresh_mode_and_price_or_count_required')
    result['refresh_observation'] = copy.deepcopy(refresh)
    config = catalogue.economy_config() if callable(getattr(catalogue, 'economy_config', None)) else {}
    gain = _dict(config.get('constants')).get('GridFight_LevelUpExp')
    if not _integer(gain, 1):
        reasons.append('released_XP_increment_missing')
    result.update(configured_XP_gain_assumption=gain, database_sha256=config.get('database_sha256'),
                  source_files=copy.deepcopy(config.get('sources', [])))
    if reasons:
        return result
    profiles = []
    for name, rows in _dict(config.get('profiles')).items():
        if policy['released_profile'] not in ('matching', name):
            continue
        by_level = {row.get('PlayerLevel', row.get('GridFightLevel')): row
                    for row in rows if isinstance(row, dict)}
        current = by_level.get(level, {})
        if current.get('LevelUpExp') == xp[1] and current.get('AvatarMaxNumber') == population['maximum']:
            profiles.append((name, by_level))
    observed_transition = not profiles
    if observed_transition:
        # The bound current HUD is the current threshold, not a release-version
        # assertion. Future population/rarity remain separate prior scenarios.
        for name, rows in _dict(config.get('profiles')).items():
            if policy['released_profile'] not in ('matching', name):
                continue
            by_level = {row.get('PlayerLevel', row.get('GridFightLevel')): copy.deepcopy(row)
                        for row in rows if isinstance(row, dict)}
            current = by_level.get(level, {})
            if current.get('AvatarMaxNumber') == population['maximum']:
                by_level[level]['LevelUpExp'] = xp[1]
                profiles.append((name, by_level))
        if not profiles:
            reasons.append('released_current_population_prior_missing_or_conflicting')
            return result
        crossing_count = (xp[1] - xp[0] + gain - 1) // gain
        affordable = max(0, (wallet['value'] - (policy['reserve'] or 0)) // xp_cost)
        if crossing_count <= affordable and crossing_count > policy['max_xp_purchases']:
            reasons.append('XP_search_bound_excludes_affordable_observed_current_transition')
            return result
        result['current_transition_constraint'] = {
            'source': 'current_native_player_hud', 'snapshot_id': snapshot,
            'binding': copy.deepcopy(state.get('binding')), 'level': level,
            'xp': copy.deepcopy(xp), 'population': population['maximum'],
            'XP_increment_origin': 'released_prior_assumption',
            'max_level_crossings': 1, 'first_crossing_purchases_assumption': crossing_count,
            'future_population_and_rarity_origin': 'released_prior_assumption',
            'release_profile_verified': False}
    pool = catalogue.economic_prior_pool() if callable(getattr(catalogue, 'economic_prior_pool', None)) else {}
    cards = pool.get('cards', [])
    if pool.get('database_sha256') != config.get('database_sha256') or not cards or len(cards) > MAX_ENTITIES:
        reasons.append('current_released_ordinary_pool_missing_or_changed')
        return result
    result['pool_prior'] = {'database_sha256': pool.get('database_sha256'),
        'included_roles': [c['role_id'] for c in cards], 'excluded': copy.deepcopy(pool.get('excluded', [])),
        'within_rarity': 'uniform_over_conservative_released_current_ordinary_candidates',
        'draws': 'IID_with_replacement_assumption; finite_pool_unknown', 'activated_pool_verified': False}
    reserve = policy['reserve'] if policy['reserve'] is not None else 0
    coins, refresh_cost = wallet['value'], 0 if mode == 'free' else refresh['paid_cost']
    remaining_refreshes = max(0, policy['max_node_refreshes'] - policy['refreshes_used'])
    result['bounds'] = {'max_xp_purchases': policy['max_xp_purchases'],
        'node_max_refreshes': policy['max_node_refreshes'], 'node_refreshes_used': policy['refreshes_used'],
        'remaining_node_refreshes': remaining_refreshes, 'max_refresh_actions_before_readback': 1,
        'refresh_samples': policy['max_refresh_samples'], 'future_buys_per_scenario': 1,
        'max_economy_evaluations': policy['max_economy_evaluations']}
    result['assumptions'] = ['Released XP increment used only as a model assumption, never promoted to native buy_gain.',
        'Matching profiles remain prior scenarios; an unmatched current native threshold constrains only one crossing, while future population/rarity remain prior assumptions.',
        'Refresh rarity weights come from the same assumed post-XP profile.',
        'Future one-star price comes from the released price table; recheck the actual new shop before buying.',
        'One future card uses additive PowerBase marginal gain; future bond interactions are unscored.',
        'No mandatory 50-coin reserve; explicit reserve and declared gold weight only.',
        'Eight-or-fewer deterministic stratified scenarios approximate IID value, not a probability guarantee.']
    # Cache additive marginal layouts separately from current bonded scores.
    marginal, evaluations = {}, 0
    candidates = []
    for name, profile in profiles:
        new_level, progress = level, xp[0]
        horizon = min(policy['max_xp_purchases'], max(0, (coins-reserve)//xp_cost))
        if observed_transition:
            horizon = min(horizon, crossing_count)
        for count in range(horizon + 1):
            if count:
                if new_level >= 10:
                    break
                progress += gain
                while new_level < 10:
                    threshold = profile.get(new_level, {}).get('LevelUpExp')
                    if not _integer(threshold, 1):
                        reasons.append('released_future_XP_threshold_missing')
                        break
                    if progress < threshold:
                        break
                    progress -= threshold
                    new_level += 1
                    if observed_transition:
                        break  # No unobserved second XP threshold is used.
            row = profile.get(new_level, {})
            capacity = row.get('AvatarMaxNumber')
            if not _integer(capacity, 0, 10) or capacity < sum(reserved.values()):
                reasons.append('released_future_population_missing_or_reserved_exceeds_profile')
                break
            usable = capacity - sum(reserved.values())
            weights = {rarity: row.get(f'Rarity{rarity}Weight', 0) for rarity in range(1, 6)}
            if any(not _finite(w) for w in weights.values()) or not sum(weights.values()):
                reasons.append('released_future_rarity_weights_missing')
                break
            groups = {r: [c for c in cards if c['rarity'] == r] for r in weights}
            if any(w > 0 and not groups[r] for r, w in weights.items()):
                reasons.append('positive_rarity_weight_without_released_pool_candidates')
                break
            distribution, cumulative = [], 0.
            for rarity in sorted(groups):
                for card in sorted(groups[rarity], key=lambda c: c['role_id']):
                    cumulative += weights[rarity] / sum(weights.values()) / len(groups[rarity])
                    distribution.append((cumulative, card))
            samples = []
            for sample in range(policy['max_refresh_samples']):
                offers = []
                for slot in range(5):
                    quantile = ((sample + .5) / policy['max_refresh_samples'] + slot * .618033988749895) % 1
                    offers.append(next((card for threshold, card in distribution if quantile < threshold), distribution[-1][1]))
                samples.append(offers)
            sampled = {c['role_id']: c for s in samples for c in s}
            for model in models:
                purchase_spend, xp_spend = model['purchase_spend'], count * xp_cost
                retained = coins - purchase_spend - xp_spend
                if retained < reserve:
                    continue
                low, high = model['minimum'][usable], model['maximum'][usable]
                base_score = low[0] + retained * policy['gold_weight']
                candidate = {'profile_assumption': name, 'portfolio_id': model['id'],
                    'purchase_refs': model['purchase_refs'], 'xp_purchases': count, 'refresh_count': 0,
                    'budget': {'purchase': purchase_spend, 'experience': xp_spend, 'refresh': 0},
                    'current_purchase_spend': purchase_spend, 'future_purchase_budget': 0,
                    'coins_retained': retained, 'reserve': reserve, 'level_assumption': new_level,
                    'xp_assumption': progress, 'population_assumption': capacity,
                    'minimum_proxy': base_score, 'maximum_proxy': high[0] + retained * policy['gold_weight'],
                    'minimum_proxy_layout': [{'entity_ref': ref, 'zone': zone} for ref, zone in low[1]],
                    'search': copy.deepcopy(model['search']), 'rarity_weights_assumption': weights,
                    'refresh_expected_net_proxy': 0., 'refresh_expected_purchase_spend': 0.}
                candidates.append(candidate)
                available = retained - refresh_cost - reserve
                if policy['purchase_budget'] is not None:
                    available = min(available, policy['purchase_budget'] - purchase_spend)
                if not remaining_refreshes or available < 0 or model['remaining_bench_slots'] < 1:
                    continue
                additions = {}
                for role_id, card in sampled.items():
                    if card['price'] > available:
                        continue
                    key = model['id'], role_id
                    if key not in marginal:
                        if evaluations >= policy['max_economy_evaluations']:
                            reasons.append('economic_marginal_search_limit')
                            break
                        evaluations += 1
                        item = {'ref': 'future:' + role_id, 'family': normalize_name(card['name']),
                            'powers': {z: {'minimum': p, 'maximum': p} for z, p in card['powers'].items()},
                            'bench_weight': policy['bench_weight']}
                        before = _layouts(model['items'], 'minimum', model['front_capacity'], model['back_capacity'])
                        after = _layouts(model['items'] + [item], 'minimum', model['front_capacity'], model['back_capacity'])
                        marginal[key] = {cap: after[cap][0]-before[cap][0] for cap in range(11)}
                    additions[role_id] = marginal[key][usable] - card['price'] * policy['gold_weight']
                outcomes = []
                for sample in samples:
                    options = [(additions.get(c['role_id'], -math.inf), -c['price'], c['role_id']) for c in sample]
                    gain_value, negative_price, role_id = max(options)
                    if gain_value > 0:
                        outcomes.append({'role_id': role_id, 'net_proxy': gain_value, 'price': -negative_price})
                    else:
                        outcomes.append({'role_id': None, 'net_proxy': 0., 'price': 0})
                expected_gain = sum(o['net_proxy'] for o in outcomes) / len(outcomes) - refresh_cost * policy['gold_weight']
                future_budget = max(o['price'] for o in outcomes)
                refreshed = copy.deepcopy(candidate)
                refreshed.update(refresh_count=1, refresh_mode=mode, future_purchase_budget=future_budget,
                    budget={'purchase': purchase_spend+future_budget, 'experience': xp_spend, 'refresh': refresh_cost},
                    coins_retained=retained-refresh_cost-future_budget,
                    minimum_proxy=base_score+expected_gain, maximum_proxy=high[0]+retained*policy['gold_weight']+expected_gain,
                    refresh_expected_net_proxy=expected_gain,
                    refresh_expected_purchase_spend=sum(o['price'] for o in outcomes)/len(outcomes),
                    future_scenarios=outcomes,
                    scoring_interval_scope='current_variant_endpoints_plus_same_empirical_refresh_expectation; not confidence bounds')
                candidates.append(refreshed)
    candidates.sort(key=lambda c: (-c['minimum_proxy'], sum(c['budget'].values()),
        c['refresh_count'], c['xp_purchases'], c['portfolio_id'], c['profile_assumption']))
    result.update(compared_candidates=len(candidates), marginal_evaluations=evaluations,
        conditional_best=copy.deepcopy(candidates[0]) if candidates else None,
        conditional_candidates=candidates[:16],
        best_by_dimension={label: copy.deepcopy(next((c for c in candidates if predicate(c)), None))
            for label, predicate in (
                ('without_XP', lambda c: c['xp_purchases'] == 0),
                ('with_XP', lambda c: c['xp_purchases'] > 0),
                ('without_refresh', lambda c: c['refresh_count'] == 0),
                ('with_refresh', lambda c: c['refresh_count'] > 0))},
        matched_profile_assumptions=[name for name, rows in profiles],
        profile_selection_scope='Highest scoring conditional scenario; no assertion of current effective profile.',
        search={'current_subsets_and_XP': 'enumerated_with_policy_bounds',
                'refresh': 'deterministic_stratified_empirical_approximation',
                'global_real_combat_optimum_claimed': False, 'stochastic_expectation_exact': False})
    if reasons or not candidates:
        result['blocked_reasons'] = sorted(set(reasons or ['no_affordable_joint_candidate']))
        return result
    best = result['conditional_best']
    if sum(reserved.values()) > 0 and not any(best['budget'].values()):
        result['blocked_reasons'] = ['zero_spend_with_unscored_board_requires_ROOT_power_review']
        result['power_review_required'] = True
        return result  # A partial hold proxy is not a completed strengthening decision.
    result['status'] = 'conditional_joint_decision'
    result['complete_dimensions'] = {'purchase': True, 'experience': True, 'refresh': True}
    result['allocation'] = {'budget': copy.deepcopy(best['budget']), 'reserve': reserve,
        'coins_retained_worst_case': best['coins_retained'], 'xp_count': best['xp_purchases'],
        'refresh_count_max': best['refresh_count'], 'node_refresh_count_max': policy['max_node_refreshes'],
        'future_purchase_budget': best['future_purchase_budget'],
        'stop_condition': 'After every resource or inventory change reread and recompute; after one refresh end prefix, read all five offers, stop at node cap/reserve or nonpositive conditional marginal value.',
        'execution_eligible': False}
    result['allocation']['budget_scope'] = 'Includes held future-purchase funds; spend only current visible purchases, conditional XP and one refresh before rereading.'
    result['conditional_raw_actions'] = ([{'type': 'buy_xp', 'count': best['xp_purchases'],
        'price_per_purchase': xp_cost, 'gain_model_assumption': gain,
        'execute_at_most_before_readback': 1, 'readback_and_replan_before_next': True}] if best['xp_purchases'] else [])
    result['conditional_raw_actions'] += [{'type': 'buy', 'reference': ref,
        'readback_and_replan_before_next': True} for ref in best['purchase_refs']]
    if best['refresh_count']:
        result['conditional_raw_actions'].append({'type': 'refresh', 'count': 1, 'mode': mode,
            'price': refresh_cost, 'then': 'end_prefix_read_all_five_and_replan; no hypothetical future buy is dispatched'})
    return result


def _allocate_gold(state, catalogue, policy, wallet, population, reserved, models, shop_complete):
    """ONE wallet for shop+XP scenarios; missing reroll value stays missing.

    Released increments/profile matches are conditional comparisons, never
    native facts. An affordable refresh count is not a recommended count.
    """
    if policy.get('economy_model', 'partial') == 'released_prior':
        return _released_prior_allocation(state, catalogue, policy, wallet, population, reserved, models, shop_complete)
    result = {'schema': 'currency-wars-economic-allocation/v1',
        'binding': copy.deepcopy(state.get('binding')), 'snapshot_id': state.get('snapshot_id'),
        'status': 'incomplete_joint_comparison', 'input_sent': False, 'execution_eligible': False,
        'complete_dimensions': {'purchase': False, 'experience': False, 'refresh': False},
        'conditional_candidates': [], 'blocked_reasons': [],
        'scope': 'Current shop subsets and bounded XP purchases; refresh expected value not yet modeled.'}
    reasons = result['blocked_reasons']
    if not wallet['verified_native'] or not population['projection_known']:
        reasons.append('native_wallet_or_population_missing')
        return result
    if not shop_complete:
        reasons.append('complete_current_shop_required')
    result['complete_dimensions']['purchase'] = shop_complete
    config = catalogue.economy_config() if callable(getattr(catalogue, 'economy_config', None)) else {}
    if not config:
        reasons.append('released_economy_configuration_missing')
        return result
    snapshot, semantic = state.get('snapshot_id'), _dict(state.get('semantic'))
    hud, offer = _dict(semantic.get('player_hud')), _dict(semantic.get('experience_offer'))
    level, xp = hud.get('level'), hud.get('xp')
    if (hud.get('actor') != 'player' or hud.get('origin') != 'native_player_hud'
            or hud.get('snapshot_id') != snapshot or not _integer(level, 1, 10)
            or not isinstance(xp, list) or len(xp) != 2 or not _integer(xp[0])
            or not _integer(xp[1], 1) or xp[0] >= xp[1]):
        reasons.append('native_player_XP_required')
        return result
    cost = offer.get('buy_cost')
    if (offer.get('origin') != 'native_experience_offer' or offer.get('snapshot_id') != snapshot
            or offer.get('cost_checked') is not True or not _integer(cost, 1)):
        reasons.append('native_XP_price_required')
        cost = None
    gain = _dict(config.get('constants')).get('GridFight_LevelUpExp')
    if not _integer(gain, 1):
        reasons.append('released_XP_increment_missing')
        return result
    reasons.append('current_XP_increment_and_modifiers_unverified')
    profiles = []
    for name, rows in _dict(config.get('profiles')).items():
        by_level = {row.get('PlayerLevel', row.get('GridFightLevel')): row
                    for row in rows if isinstance(row, dict)}
        current = by_level.get(level, {})
        if (current.get('LevelUpExp') == xp[1]
                and current.get('AvatarMaxNumber') == population['maximum']):
            profiles.append((name, by_level))
    if not profiles:
        reasons.append('no_released_profile_matches_current_XP_and_population')
        return result
    reserve = policy['reserve'] if policy['reserve'] is not None else 0
    coins = wallet['value']
    for name, profile in profiles:
        new_level, progress = level, xp[0]
        for count in range(0, min(8, coins // cost if cost else 0) + 1):
            if count:
                if new_level >= 10:
                    break
                progress += gain
                while new_level < 10:
                    threshold = profile.get(new_level, {}).get('LevelUpExp')
                    if not _integer(threshold, 1) or progress < threshold:
                        break
                    progress -= threshold
                    new_level += 1
            capacity = profile.get(new_level, {}).get('AvatarMaxNumber')
            if not _integer(capacity, 0, 10):
                reasons.append('released_future_population_missing')
                break
            usable = capacity - sum(reserved.values())
            if not 0 <= usable <= 10:
                reasons.append('reserved_population_exceeds_profile')
                break
            for model in models:
                xp_spend, purchase_spend = count * cost if count else 0, model['purchase_spend']
                retained = coins - xp_spend - purchase_spend
                if retained < reserve:
                    continue
                low, high = model['minimum'][usable], model['maximum'][usable]
                result['conditional_candidates'].append({
                    'profile_assumption': name, 'portfolio_id': model['id'],
                    'purchase_refs': model['purchase_refs'], 'xp_purchases': count,
                    'budget': {'purchase': purchase_spend, 'experience': xp_spend, 'refresh': 0},
                    'coins_retained': retained, 'level_assumption': new_level,
                    'xp_assumption': progress, 'population_assumption': capacity,
                    'minimum_proxy': low[0] + retained * policy['gold_weight'],
                    'maximum_proxy': high[0] + retained * policy['gold_weight'],
                    'minimum_proxy_layout': [{'entity_ref': ref, 'zone': zone} for ref, zone in low[1]],
                    'search': copy.deepcopy(model['search'])})
    # Hidden paid/free modes are not inferred, nor are missing free counts zero.
    result['refresh_observation'] = copy.deepcopy(_dict(semantic.get('refresh_offer')))
    reasons.append('refresh_distribution_pool_and_investment_value_unmodeled')
    result['configured_XP_gain_assumption'] = gain
    result['database_sha256'] = config.get('database_sha256')
    result['source_files'] = copy.deepcopy(config.get('sources', []))
    candidates = result['conditional_candidates']
    candidates.sort(key=lambda c: (-c['minimum_proxy'], sum(c['budget'].values()),
                                   c['xp_purchases'], c['portfolio_id'], c['profile_assumption']))
    result['compared_candidates'] = len(candidates)
    result['conditional_best'] = copy.deepcopy(candidates[0]) if candidates else None
    result['conditional_candidates'] = candidates[:16]
    result['blocked_reasons'] = sorted(set(reasons))
    return result


def advise(state, catalogue, policy=None):
    """Return partial, non-executable advice; preserve unknown observed entities.

    Policy budgets/reserve and purchase_star_for_scoring are hypothetical
    comparison inputs, never accepted match budget or observed unit properties.
    No complete-state prepare() gate is invoked. All returned actions must still
    go through the original executor and ROOT; execution_eligible is always False.
    """
    started = time.perf_counter()
    if not isinstance(state, dict) or state.get('schema') != 'currency-wars-mcp/v1':
        raise ValueError('state.schema must be currency-wars-mcp/v1')
    encoded = json.dumps(state, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError('state exceeds bounded input size')
    policy = _policy(policy)
    source = _binding(state, policy['mode'] == 'replay')
    snapshot = state.get('snapshot_id')
    semantic, fields = _dict(state.get('semantic')), _dict(state.get('fields'))
    comparison = _comparison_view(state)
    roster_snapshot = comparison.get('roster_snapshot_id', snapshot)
    team = (_dict(comparison['source_view'].get('team')) if comparison['accepted_for_comparison']
            else _dict(semantic.get('team')))
    entities, slots = _roster(team)
    cache, scored, unscored, actions = {}, [], [], []
    value_modifiers, value_modifier_diagnostics = {}, []
    addresses = [_address(slot) for slot in slots]
    usable_slots = [slot for slot in slots if _native_slot(slot, roster_snapshot)
                    and (slot.get('location') == 'bench' or state.get('page') == 'preparation'
                         or comparison['accepted_for_comparison'])
                    and addresses.count(_address(slot)) == 1]
    empty_bench = [slot for slot in usable_slots if slot['row'] == 'bench' and slot.get('status') == 'empty']
    wallet, population = _wallet(state, snapshot), _population(state, comparison)
    native_cap = _dict(team.get('capacity'))
    for entry in entities:
        observed = entry['observed']
        star = observed.get('star') if _integer(observed.get('star'), 1, 4) else None
        entry['catalogue'] = _character(catalogue, observed.get('name'), star, cache)
        entry['source_usable'] = (_native_slot(observed, roster_snapshot) and observed.get('status') == 'occupied'
                                 and (observed.get('location') == 'bench' or state.get('page') == 'preparation'
                                      or comparison['accepted_for_comparison']))
        entry['source_scope'] = comparison['scope']
        entry['observed_star'] = star
        entry['observed_physical_uid'] = observed.get('physical_uid')  # Never invented from a slot.
        score_item = _score_item(entry, policy, value_modifiers=value_modifiers)
        if score_item is None:
            unscored.append(entry['entity_ref'])
        else:
            scored.append(score_item)
        if observed.get('location') != 'bench':
            continue
        for zone in entry['catalogue']['legal_positions_possible']:
            targets = [slot for slot in usable_slots if slot['row'] == zone and slot.get('status') == 'empty']
            satisfied = set()
            if (entry['source_usable'] and not entry['conflicts'] and star is not None
                    and isinstance(observed.get('position'), str)
                    and zone in POSITIONS.get(observed.get('position'), ())):
                satisfied.add('current_source_card_name_star_position_ROI')
            if targets and zone in entry['catalogue']['legal_positions_guaranteed']:
                satisfied.add('current_legal_empty_board_target_ROI')
            if population['projection_known'] and population['deployed'] < population['maximum']:
                satisfied.add('native_current_population_with_free_place')
            board = [slot for slot in usable_slots if slot['location'] == 'board']
            if (len(board) == 10 and all(slot.get('status') == 'empty' or
                    (slot.get('status') == 'occupied' and _text(slot.get('name'))
                     and normalize_name(slot['name']) != normalize_name(observed.get('name') or '')) for slot in board)):
                satisfied.add('no_same_name_already_deployed')
            power = entry['catalogue']['power_interval'].get(zone)
            actions.append(_candidate('deploy', entry['entity_ref'], DEPLOY_REQUIREMENTS, satisfied,
                ['Catalogue position/power is conditional on the listed current variants; no buy price required.',
                 'Global overflow is not assumed empty; unknown entities are retained.'],
                {'minimum': power['minimum'] * (1-policy['bench_weight']),
                 'maximum': power['maximum'] * (1-policy['bench_weight']),
                 'basis': 'static PowerBase gain over modeled bench value; not damage'} if power else None,
                zone=zone, target=copy.deepcopy(targets[0]) if targets else None,
                source_snapshot_id=observed.get('snapshot_id')))
    valid_entities = {}
    for entry in entities:
        role_id = _dict(entry.get('catalogue')).get('role_id')
        valid_entities[entry['entity_ref']] = {'role_id': role_id}
    value_modifiers, value_modifier_diagnostics = _candidate_value_modifiers(
        state, policy, valid_entities)
    # Rebuild scored items once after binding validation.  Declared assumptions
    # are comparison-only and never become observed facts.
    rescored = []
    for entry in entities:
        item = _score_item(entry, policy, value_modifiers=value_modifiers)
        if item is not None:
            rescored.append(item)
    scored = rescored

    shop = _dict(state.get('shop'))
    native_offers = _list(shop.get('slots'), MAX_OFFERS, 'shop.slots')
    offers, comparison_offers = [], []
    for index, slot in enumerate(native_offers):
        slot_id = slot.get('slot')
        verified = None
        if state.get('page') == 'shop':
            try:
                verified = purchase_slot(shop, slot_id, snapshot)
                if type(slot.get('recommended')) is not bool:
                    verified = None
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
        observation = copy.deepcopy(slot)
        # Native shop currently does not certify star/trial; preserve any raw field,
        # but do not promote it to an observed fact without a dedicated contract.
        observed_star = None
        entry = {'entity_ref': f'shop.slots[{index}]', 'observed': observation,
                 'observed_star': observed_star, 'observed_trial': None,
                 'source_usable': verified is not None, 'conflicts': [],
                 'catalogue': _character(catalogue, slot.get('name'), policy['purchase_star_for_scoring'], cache)}
        offers.append(entry)
        satisfied = set()
        if verified is not None:
            satisfied.add('native_offer_name_price_complete_ROI')
        if wallet['verified_native']:
            satisfied.add('native_current_wallet')
        if empty_bench:
            satisfied.add('current_empty_bench_or_certified_storage')
        if population['projection_known']:
            satisfied.add('population_for_joint_comparison')
        score_item = _score_item(entry, policy, policy['purchase_star_for_scoring'], value_modifiers=value_modifiers)
        enabled = bool(verified is not None and wallet['verified_native'] and empty_bench
                       and verified['cost'] <= wallet['value'] and score_item is not None)
        reasons = [key for key in ('native_current_wallet', 'native_offer_name_price_complete_ROI',
                    'current_empty_bench_or_certified_storage') if key not in satisfied]
        if verified is not None and wallet['verified_native'] and verified['cost'] > wallet['value']:
            reasons.append('price_exceeds_observed_wallet')
        if score_item is None:
            reasons.append('catalogue_score_or_source_unresolved')
        action = _candidate('buy', entry['entity_ref'], BUY_REQUIREMENTS, satisfied,
            [f'Only scoring assumes purchase star={policy["purchase_star_for_scoring"]}; observed_star remains unknown.',
             'Trial terms, activated pool and merge effects are unmodeled; price never chooses a role variant.'],
            comparison_enabled=enabled, comparison_disabled_reasons=reasons,
            native_slot=slot_id, price=verified['cost'] if verified else None,
            price_source='current_native_offer_ROI' if verified else None,
            observed_star=None, observed_trial=None)
        actions.append(action)
        if enabled:
            comparison_offers.append((entry, score_item, verified['cost']))
    # Unknown population creates explicit hypothetical usable-place scenarios,
    # never an observed maximum. Same set is used for every purchase/no-buy option.
    locked = {zone: sum(entry['entity_ref'] in unscored
                        and entry['observed'].get('location') == 'board'
                        and entry['observed'].get('row') == zone for entry in entities)
              for zone in ('front', 'back')}
    front_capacity, back_capacity = max(0, 4-locked['front']), max(0, 6-locked['back'])
    known_places = max(0, population['maximum']-sum(locked.values())) if population['projection_known'] else None
    caps = [known_places] if known_places is not None else list(range(front_capacity+back_capacity+1))
    portfolios, scenarios, allocation_models = [], {}, []
    bond_model = None
    bond_disabled_reason = None
    if policy['bond_weight']:
        if callable(getattr(catalogue, 'bond_proxy_model', None)):
            bond_model = catalogue.bond_proxy_model()
            from currency_wars_bond_proxy import restrict_model
            bond_model = restrict_model(bond_model, [item['family'] for item in scored]
                + [item[1]['family'] for item in comparison_offers])
            digests = {entry['catalogue']['database_sha256'] for entry in entities + offers
                       if entry['catalogue']['database_sha256'] is not None}
            if digests and digests != {bond_model['database_sha256']}:
                raise ValueError('catalogue.changed_during_bond_model')
        else:
            bond_disabled_reason = 'catalogue_bond_model_provider_missing'
    search_budget = {'remaining': policy['max_layout_transitions']}
    if wallet['verified_native']:
        for mask in range(1 << len(comparison_offers)):
            selected = [comparison_offers[i] for i in range(len(comparison_offers)) if mask & (1 << i)]
            spend = sum(item[2] for item in selected)
            if (len(selected) > len(empty_bench) or spend > wallet['value']
                    or (policy['purchase_budget'] is not None and spend > policy['purchase_budget'])
                    or (policy['reserve'] is not None and wallet['value'] - spend < policy['reserve'])):
                continue
            items = scored + [item[1] for item in selected]
            search = {'algorithm': 'additive_dp', 'exact_for_declared_model': True}
            if bond_model is not None:
                from currency_wars_bond_proxy import layouts as bond_layouts
                compared = bond_layouts(items, bond_model, front_capacity=front_capacity,
                    back_capacity=back_capacity,
                    scale=policy['bond_weight'] * policy['bond_reference_power'],
                    max_states=policy['max_layout_states'], budget=search_budget)
                low, high, search = compared['minimum'], compared['maximum'], compared['search']
            else:
                low = _layouts(items, 'minimum', front_capacity, back_capacity)
                high = _layouts(items, 'maximum', front_capacity, back_capacity)
            retained = wallet['value'] - spend
            values = []
            identifier = 'hold_and_compare_deployment' if not mask else 'buy_subset_' + str(mask)
            allocation_models.append({'id': identifier, 'purchase_spend': spend,
                'purchase_refs': [item[0]['entity_ref'] for item in selected],
                'minimum': low, 'maximum': high, 'search': search, 'items': items,
                'front_capacity': front_capacity, 'back_capacity': back_capacity,
                'remaining_bench_slots': len(empty_bench)-len(selected)})
            for cap in caps:
                lower = low[cap][0] + retained * policy['gold_weight']
                upper = high[cap][0] + retained * policy['gold_weight']
                values.append((lower, upper))
                scenarios.setdefault(cap, []).append((lower, -spend, identifier))
            portfolios.append({'id': identifier, 'execution_eligible': False,
                'purchase_refs': [item[0]['entity_ref'] for item in selected],
                'purchase_spend': spend, 'coins_retained': retained,
                'deployment_search': search,
                'approximate_score': {'minimum': min(value[0] for value in values),
                    'maximum': max(value[1] for value in values),
                    'kind': 'known_component_search_incumbent_scenarios', 'complete_team_score': False},
                'conditional_layouts': [{'usable_places_assumption': cap,
                    'capacity_from_population_projection': population['projection_known'],
                    'minimum_proxy': values[i][0], 'maximum_proxy': values[i][1],
                    'minimum_proxy_layout': [{'entity_ref': ref, 'zone': zone} for ref, zone in low[cap][1]],
                    'maximum_proxy_layout': [{'entity_ref': ref, 'zone': zone} for ref, zone in high[cap][1]]}
                    for i, cap in enumerate(caps)],
                'unscored_preserved_entities': list(unscored),
                'assumptions': ['Only sourced visible components scored; incomplete roster is not a zero-strength roster.',
                    'Hypothetical rearrangement needs actual targets and population before any drag.',
                    'No merging, selling or bonus for unknown copies; one name family deployed at a time.',
                    'Each purchase uses one proved empty bench; read back and recompute before another action.'],
                'missing': ['accepted_joint_economy_budget_and_reserve']
                    + ([] if population['projection_known'] else ['native_current_population'])})
    hold = next((p for p in portfolios if p['id'] == 'hold_and_compare_deployment'), None)
    if hold:
        for action in actions:
            if action['kind'] != 'buy':
                continue
            single = next((p for p in portfolios if p['purchase_refs'] == [action['reference']]), None)
            if single:
                deltas = [(p['minimum_proxy']-b['maximum_proxy'], p['maximum_proxy']-b['minimum_proxy'])
                          for p, b in zip(single['conditional_layouts'], hold['conditional_layouts'])]
                action['approximate_score'] = {
                    'minimum': min(v[0] for v in deltas), 'maximum': max(v[1] for v in deltas),
                    'basis': 'single-buy minus no-buy known-component proxy, including native price, retained gold and enabled base-bond features',
                    'interval_scope': 'explored incumbent variant/capacity scenarios; not a global optimum or real performance bound',
                    'joint_portfolio_id': single['id']}
    best_by_cap = [{'usable_places_assumption': cap, 'preferred_candidate': max(values)[2],
                   'ranking_rule': 'maximum lower known-component proxy, then minimum spend; conditional only'}
                  for cap, values in sorted(scenarios.items())]
    preferred = sorted({item['preferred_candidate'] for item in best_by_cap})
    # These structural features come from the released DB, not another model
    # interpretation. They describe only the explicit hypothetical layout;
    # actual emblems/investments and combat utility are still unverified.
    mechanics = {'status': 'unavailable', 'reason': 'catalogue_rules_provider_missing'}
    if callable(getattr(catalogue, 'bond_scenario', None)):
        coverage = catalogue.rules()
        definitions = {entry['catalogue']['database_sha256'] for entry in entities + offers
                       if entry['catalogue']['database_sha256'] is not None}
        if definitions and definitions != {coverage['database_sha256']}:
            raise ValueError('catalogue.changed_during_advice')
        ref_names = {entry['entity_ref']: entry['observed'].get('name') for entry in entities + offers}
        by_id = {portfolio['id']: portfolio for portfolio in portfolios}
        scenarios = []
        for preference in best_by_cap:
            portfolio = by_id[preference['preferred_candidate']]
            layout = next(row for row in portfolio['conditional_layouts']
                          if row['usable_places_assumption'] == preference['usable_places_assumption'])
            names = [ref_names[item['entity_ref']] for item in layout['minimum_proxy_layout']]
            scenario = catalogue.bond_scenario(names)
            if scenario['database_sha256'] != coverage['database_sha256']:
                raise ValueError('catalogue.changed_during_bond_scenarios')
            scenarios.append({**preference, 'base_bond_scenario': scenario})
        mechanics = {'status': 'conditional_catalogue_features',
            'coverage': coverage['coverage'],
            'database_sha256': coverage['database_sha256'],
            'preferred_layout_base_bonds': scenarios,
            'changes_power_proxy_ranking': bond_model is not None,
            'bond_feature_model': ({k: copy.deepcopy(bond_model[k]) for k in
                ('schema', 'database_sha256', 'property_weights', 'formula', 'excluded_bonds',
                 'structurally_unreachable_in_modeled_choices',
                 'current_effects_verified', 'complete_combat_model', 'scope')}
                if bond_model is not None else None),
            'bond_model_disabled_reason': bond_disabled_reason,
            'scope': 'Base bond properties contribute a declared heuristic; dynamic modifiers, scripts and real combat utility unscored.'}
    actions.append(_candidate('hold', 'no_purchase', COMMON_REQUIREMENTS, set(),
        ['Retaining funds is compared jointly with native priced purchase subsets and conditional deployment.'],
        comparison_enabled=bool(portfolios)))
    for kind, reason in (('buy_xp', 'native_experience_gain_and_transition_model_unverified'),
                         ('refresh', 'native_refresh_distribution_and_value_model_unverified'),
                         ('sell', 'native_sell_price_and_retention_certificate_unverified'),
                         ('merge', 'physical_copy_identity_and_result_readback_unverified'),
                         ('battle', 'current_lineup_equipment_and_ROOT_battle_acceptance_required')):
        actions.append(_candidate(kind, kind, COMMON_REQUIREMENTS + (reason,), set(), [],
                                  comparison_enabled=False, disabled=True, disabled_reason=reason))
    geometry = _dict(_dict(shop.get('page')).get('geometry'))
    frames = geometry.get('frame_scores', [])
    read_review = state.get('shop_read_review')
    if 'shop_read_review' not in state:
        reviews = _dict(_dict(_dict(_dict(state.get('request')).get('context')).get('preparation_review')).get('value'))
        read_review = _dict(reviews.get('shop_inspection')).get('read_review')
    if isinstance(read_review, dict) and read_review.get('snapshot_id') != snapshot:
        read_review = None
    shop_complete = (state.get('page') == 'shop'
        and shop.get('schema') == 'currency-wars-shop-observation/v1'
        and _dict(shop.get('input')).get('sha256') == snapshot
        and _dict(shop.get('input')).get('format') == 'PNG'
        and _dict(shop.get('input')).get('size') == [1920, 1080]
        and _dict(shop.get('page')).get('reliable_open_shop') is True
        and geometry.get('overlays') == [] and isinstance(frames, list) and len(frames) == 5
        and all(_finite(f, .60, 1.) for f in frames)
        and len(native_offers) == 5 and {s.get('slot') for s in native_offers} == set(range(1, 6))
        and all(s.get('status') == 'empty' or priced_read_slot(shop, s['slot'], snapshot, review=read_review) is not None
                for s in native_offers))
    allocation = _allocate_gold(state, catalogue, policy, wallet, population, locked,
                                allocation_models, shop_complete)
    allocation['comparison_source_scope'] = comparison['scope']
    for action in actions:
        if action['kind'] in ('buy_xp', 'refresh') and allocation['status'] == 'conditional_joint_decision':
            action.update(comparison_enabled=True, disabled=False,
                          disabled_reason=None, model_assumption='released_prior',
                          selected_count=allocation['allocation']['xp_count' if action['kind'] == 'buy_xp' else 'refresh_count_max'])
    return {'schema': SCHEMA, 'status': ('historical_non_executable' if policy['mode'] == 'replay'
            else 'unverified_non_executable' if source['diagnostics'] else 'conditional_advice'),
        'input_sent': False, 'execution_eligible': False, 'source': source,
        'comparison_source': comparison,
        'input_sha256': hashlib.sha256(encoded).hexdigest(), 'snapshot_id': snapshot,
        'policy': {**policy, 'scope': 'explicit heuristic/model assumptions; not an accepted match budget'},
        'observed_entities': entities, 'observed_offers': offers,
        'candidate_value_modifier_diagnostics': value_modifier_diagnostics,
        'coverage': {'team_checked': team.get('checked'), 'team_fully_read': team.get('fully_read'),
            'unknown_slots': [copy.deepcopy(slot) for slot in slots if slot.get('status') not in ('empty', 'occupied')],
            'observed_capacity': copy.deepcopy(native_cap), 'observed_inventory': copy.deepcopy(semantic.get('inventory')),
            'global_overflow_checked': native_cap.get('overflow_checked'),
            'global_overflow_count': native_cap.get('overflow_count'),
            'verified_empty_bench_slots': copy.deepcopy(empty_bench), 'unscored_preserved_entities': unscored},
        'wallet': wallet, 'population': population, 'candidates': actions, 'mechanics': mechanics,
        'economic_allocation': allocation,
        'joint_economy': {'portfolios': portfolios, 'best_by_conditional_capacity': best_by_cap,
            'reserved_unscored_board_places': locked,
            'preferred_across_modeled_scenarios': preferred[0] if len(preferred) == 1 else None,
            'disabled_reasons': [] if portfolios else ['native_wallet_storage_score_or_policy_constraints_missing'],
            'all_deployment_searches_exact_for_declared_model': all(p['deployment_search']['exact_for_declared_model'] for p in portfolios),
            'scope': 'Purchase/deployment comparison only; complete economic allocation is a separate gated result.'},
        'limitations': ['Static FrontPowerBase/BackPowerBase is an uncalibrated heuristic, not DPS or victory probability.',
            'Intervals cover listed catalogue variants/stars and hypothetical capacities, not unknown combat interactions.',
            'Unresolved holdings are preserved and explicitly unscored; comparison is not a complete roster valuation.',
            'Base bond properties use an explicit weighted-feature heuristic, not damage; scripts and dynamic bond modifiers remain unmodeled.',
            'Equipment, investment combat utility, trial effects and activated pool remain unmodeled; XP/refresh comparisons require an explicit released_prior policy.',
            'No action is executable here; the original executor and ROOT must verify each action separately.'],
        'timing_ms': round((time.perf_counter() - started) * 1000, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--replay', action='store_true', help='Label saved state historical_non_executable')
    args = parser.parse_args()
    try:
        for path in (args.state, args.policy):
            if path is not None and path.stat().st_size > MAX_INPUT_BYTES:
                raise ValueError('input file exceeds bound')
        state_bytes = args.state.read_bytes()
        payload = json.loads(state_bytes.decode('utf-8-sig'))
        if isinstance(payload, dict) and payload.get('schema') == 'currency-wars-session-read/v1':
            if payload.get('tool') != 'get_state':
                raise ValueError('session-read wrapper must contain get_state')
            payload = payload.get('result')
        policy = json.loads(args.policy.read_text(encoding='utf-8-sig')) if args.policy else {}
        if args.replay:
            policy['mode'] = 'replay'
        from currency_wars_decision import Catalogue
        result = advise(payload, Catalogue(args.database) if args.database else Catalogue(), policy)
        result['replay_file'] = {'path': str(args.state.resolve()),
                               'sha256': hashlib.sha256(state_bytes).hexdigest()}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({'schema': SCHEMA, 'status': 'invalid_input', 'input_sent': False,
                          'execution_eligible': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
