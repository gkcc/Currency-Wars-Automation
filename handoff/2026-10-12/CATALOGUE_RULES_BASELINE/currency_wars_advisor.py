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
from currency_wars_shop_contract import purchase_slot

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
            'purchase_star_for_scoring'}:
        raise ValueError('policy: unsupported keys/type')
    result = {'mode': 'current', 'gold_weight': 20., 'bench_weight': .15,
              'purchase_star_for_scoring': 1, 'purchase_budget': None, 'reserve': None, **value}
    if result['mode'] not in ('current', 'replay'):
        raise ValueError('policy.mode must be current or replay')
    for key, maximum in (('gold_weight', 10000), ('bench_weight', 1)):
        if not _finite(result[key], high=maximum):
            raise ValueError('policy.' + key)
    if not _integer(result['purchase_star_for_scoring'], 1, 4):
        raise ValueError('policy.purchase_star_for_scoring')
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


def _population(state):
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


def _score_item(entry, policy, assumed_star=None):
    resolution = entry['catalogue']
    legal = resolution['legal_positions_guaranteed']
    observed_position = entry['observed'].get('position')
    if isinstance(observed_position, str) and observed_position in POSITIONS:
        legal = [zone for zone in legal if zone in POSITIONS[observed_position]]
    powers = resolution['power_interval']
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
    team = _dict(semantic.get('team'))
    entities, slots = _roster(team)
    cache, scored, unscored, actions = {}, [], [], []
    addresses = [_address(slot) for slot in slots]
    usable_slots = [slot for slot in slots if _native_slot(slot, snapshot)
                    and (slot.get('location') == 'bench' or state.get('page') == 'preparation')
                    and addresses.count(_address(slot)) == 1]
    empty_bench = [slot for slot in usable_slots if slot['row'] == 'bench' and slot.get('status') == 'empty']
    wallet, population = _wallet(state, snapshot), _population(state)
    native_cap = _dict(team.get('capacity'))
    for entry in entities:
        observed = entry['observed']
        star = observed.get('star') if _integer(observed.get('star'), 1, 4) else None
        entry['catalogue'] = _character(catalogue, observed.get('name'), star, cache)
        entry['source_usable'] = (_native_slot(observed, snapshot) and observed.get('status') == 'occupied'
                                 and (observed.get('location') == 'bench' or state.get('page') == 'preparation'))
        entry['observed_star'] = star
        entry['observed_physical_uid'] = observed.get('physical_uid')  # Never invented from a slot.
        score_item = _score_item(entry, policy)
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
    shop = _dict(state.get('shop'))
    native_offers = _list(shop.get('slots'), MAX_OFFERS, 'shop.slots')
    offers, comparison_offers = [], []
    for index, slot in enumerate(native_offers):
        slot_id = slot.get('slot')
        verified = None
        if state.get('page') == 'shop':
            try:
                verified = purchase_slot(shop, slot_id, snapshot)
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
        score_item = _score_item(entry, policy, policy['purchase_star_for_scoring'])
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
    portfolios, scenarios = [], {}
    if wallet['verified_native']:
        for mask in range(1 << len(comparison_offers)):
            selected = [comparison_offers[i] for i in range(len(comparison_offers)) if mask & (1 << i)]
            spend = sum(item[2] for item in selected)
            if (len(selected) > len(empty_bench) or spend > wallet['value']
                    or (policy['purchase_budget'] is not None and spend > policy['purchase_budget'])
                    or (policy['reserve'] is not None and wallet['value'] - spend < policy['reserve'])):
                continue
            items = scored + [item[1] for item in selected]
            low = _layouts(items, 'minimum', front_capacity, back_capacity)
            high = _layouts(items, 'maximum', front_capacity, back_capacity)
            retained = wallet['value'] - spend
            values = []
            identifier = 'hold_and_compare_deployment' if not mask else 'buy_subset_' + str(mask)
            for cap in caps:
                lower = low[cap][0] + retained * policy['gold_weight']
                upper = high[cap][0] + retained * policy['gold_weight']
                values.append((lower, upper))
                scenarios.setdefault(cap, []).append((lower, -spend, identifier))
            portfolios.append({'id': identifier, 'execution_eligible': False,
                'purchase_refs': [item[0]['entity_ref'] for item in selected],
                'purchase_spend': spend, 'coins_retained': retained,
                'approximate_score': {'minimum': min(value[0] for value in values),
                    'maximum': max(value[1] for value in values),
                    'kind': 'known_component_scenario_envelope', 'complete_team_score': False},
                'conditional_layouts': [{'usable_places_assumption': cap,
                    'capacity_from_population_projection': population['projection_known'],
                    'minimum_proxy': values[i][0], 'maximum_proxy': values[i][1],
                    'minimum_proxy_layout': [{'entity_ref': ref, 'zone': zone} for ref, zone in low[cap][1]]}
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
                    'basis': 'single-buy minus no-buy known-component proxy, including native price and retained gold',
                    'interval_scope': 'conservative variant/capacity comparison envelope; not real performance',
                    'joint_portfolio_id': single['id']}
    best_by_cap = [{'usable_places_assumption': cap, 'preferred_candidate': max(values)[2],
                   'ranking_rule': 'maximum lower static proxy, then minimum spend; conditional only'}
                  for cap, values in sorted(scenarios.items())]
    preferred = sorted({item['preferred_candidate'] for item in best_by_cap})
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
    return {'schema': SCHEMA, 'status': ('historical_non_executable' if policy['mode'] == 'replay'
            else 'unverified_non_executable' if source['diagnostics'] else 'conditional_advice'),
        'input_sent': False, 'execution_eligible': False, 'source': source,
        'input_sha256': hashlib.sha256(encoded).hexdigest(), 'snapshot_id': snapshot,
        'policy': {**policy, 'scope': 'explicit heuristic/model assumptions; not an accepted match budget'},
        'observed_entities': entities, 'observed_offers': offers,
        'coverage': {'team_checked': team.get('checked'), 'team_fully_read': team.get('fully_read'),
            'unknown_slots': [copy.deepcopy(slot) for slot in slots if slot.get('status') not in ('empty', 'occupied')],
            'observed_capacity': copy.deepcopy(native_cap), 'observed_inventory': copy.deepcopy(semantic.get('inventory')),
            'global_overflow_checked': native_cap.get('overflow_checked'),
            'global_overflow_count': native_cap.get('overflow_count'),
            'verified_empty_bench_slots': copy.deepcopy(empty_bench), 'unscored_preserved_entities': unscored},
        'wallet': wallet, 'population': population, 'candidates': actions,
        'joint_economy': {'portfolios': portfolios, 'best_by_conditional_capacity': best_by_cap,
            'reserved_unscored_board_places': locked,
            'preferred_across_modeled_scenarios': preferred[0] if len(preferred) == 1 else None,
            'disabled_reasons': [] if portfolios else ['native_wallet_storage_score_or_policy_constraints_missing'],
            'scope': 'Exhaustive at most32 current-shop subsets, 35 additive name-family count states; no full-game optimality claim.'},
        'limitations': ['Static FrontPowerBase/BackPowerBase is an uncalibrated heuristic, not DPS or victory probability.',
            'Intervals cover listed catalogue variants/stars and hypothetical capacities, not unknown combat interactions.',
            'Unresolved holdings are preserved and explicitly unscored; comparison is not a complete roster valuation.',
            'Bonds, equipment, investment, trial effects, activated pool, XP and refresh value remain unmodeled.',
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
