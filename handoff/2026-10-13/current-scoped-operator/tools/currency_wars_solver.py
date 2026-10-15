"""Offline, source-bound economic proposals. No controller or input imports."""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
from pathlib import Path
import time

SCHEMA = 'currency-wars-solver/v1'

class NeedsReview(ValueError):
    pass

def need(ok, field):
    if not ok:
        raise NeedsReview(field)


def finite(value):
    if not math.isfinite(value):
        raise ArithmeticError('nonfinite_utility')
    return value


def number(value, field, integer=False, minimum=0, maximum=100000):
    need(type(value) in ((int,) if integer else (int, float))
         and minimum <= value <= maximum and math.isfinite(value), field)
    return value


def sourced(value, field):
    need(isinstance(value, dict) and isinstance(value.get('source'), str)
         and bool(value['source'].strip()), field + '.source')
    return value


def unit(value, field):
    sourced(value, field)
    for key in ('uid', 'role_id', 'name'):
        need(isinstance(value.get(key), str) and bool(value[key].strip()), field + '.' + key)
    number(value.get('cost'), field + '.cost', True, 1, 99)
    number(value.get('star'), field + '.star', True, 1, 3)
    need(isinstance(value.get('positions'), list) and value['positions']
         and set(value['positions']) <= {'front', 'back'}, field + '.positions')
    return copy.deepcopy(value)


def shop(value, field):
    need(isinstance(value, list) and len(value) == 5, field + '.five_slots')
    result = []
    for index, offer in enumerate(value):
        sourced(offer, field + '.' + str(index))
        need(offer.get('slot') == index, field + '.slot_order')
        if offer.get('unit') is None:
            need(offer.get('unavailable') is True, field + '.unknown_unit')
            continue
        result.append({'slot': index, 'price': number(offer.get('price'), field + '.price', True),
                       'unit': unit(offer['unit'], field + '.unit')})
    return result


def merged(units, identities=None):
    identities = identities if identities is not None else {'used': {item['uid'] for item in units}, 'assigned': {}}
    active = {item['uid'] for item in units}
    need(len(active) == len(units), 'merge.duplicate_input_uid')
    groups = {}
    for item in units:
        groups.setdefault((item['role_id'], item['star']), []).append(item)
    for star in (1, 2):
        for role in sorted({key[0] for key in groups}):
            group = groups.get((role, star), [])
            group.sort(key=lambda item: item['uid'])
            while len(group) >= 3:
                parents, group[:] = group[:3], group[3:]
                child = {**parents[0], 'star': star + 1}
                child['member_ids'] = sorted(itertools.chain.from_iterable(
                    item.get('member_ids', [item['uid']]) for item in parents))
                lineage = role, star + 1, tuple(child['member_ids'])
                if lineage not in identities['assigned']:
                    index = len(identities['assigned'])
                    while 'solver-merge:' + str(index) in identities['used']:
                        index += 1
                    identities['assigned'][lineage] = 'solver-merge:' + str(index)
                    identities['used'].add(identities['assigned'][lineage])
                child['uid'] = identities['assigned'][lineage]
                active.difference_update(item['uid'] for item in parents)
                need(child['uid'] not in active, 'merge.duplicate_intermediate_uid')
                active.add(child['uid'])
                groups.setdefault((role, star + 1), []).append(child)
    return sorted(itertools.chain.from_iterable(groups.values()), key=lambda item: item['uid'])


def position_score(data, item, position):
    cache, key = data.setdefault('_score_cache', {}), (item['role_id'], item['star'], item['cost'], position)
    if key in cache:
        return cache[key]
    parameters = data['scoring']['units'][item['role_id']][str(item['star'])]
    if position == 'bench':
        return parameters['bench']
    values = parameters[position]
    damage, utility = values['damage'], values['utility']
    for modifier in data['modifiers']:
        if modifier['status'] != 'known':
            continue  # Explicitly reported as an unresolved conditional assumption.
        effects = modifier['effects']
        if 'damage_multiplier_by_cost' in effects:
            damage = finite(damage * effects['damage_multiplier_by_cost'][str(item['cost'])])
        utility = finite(utility * effects.get('utility_multiplier_by_role', {}).get(item['role_id'], 1))
    cache[key] = finite(damage + utility)
    return cache[key]


def deployment(data, units, population, bench_capacity):
    """Additive assignment or explicitly supplied threshold-bond model."""
    if bench_capacity < 0 or len(units) > population + bench_capacity:
        return None
    model = data['scoring'].get('bond_model')
    if model is not None:
        from currency_wars_bond_model import SearchLimits, solve_deployment
        limits = model.get('search_limits', {})
        remaining = max(0., (data['_bond_deadline'] - time.perf_counter()) * 1000)
        proposal = solve_deployment(units, lambda item, zone: position_score(data, item, zone),
            population=population, front_capacity=data['capacity']['front'],
            back_capacity=data['capacity']['back'], bench_capacity=bench_capacity,
            bonds=model['bonds'], limits=SearchLimits(
                max_states=limits.get('max_states', 100_000),
                max_transitions=limits.get('max_transitions', 1_000_000), time_limit_ms=remaining))
        if proposal['approximate']:
            data.setdefault('_bond_unknown', set()).add('bond_model.search_limit')
        if proposal['best'] is None:
            return None
        return {**proposal['best'], 'search': proposal['search'], 'exact': proposal['exact']}
    need(len({item['uid'] for item in units}) == len(units), 'deployment.duplicate_uid')
    groups = {}
    for item in units:
        groups.setdefault(item['role_id'], []).append(item)
    baseline = finite(sum(position_score(data, item, 'bench') for item in units))
    states = {(0, 0): (baseline, ())}
    for role in sorted(groups):
        following = dict(states)  # Leave every copy of this role on the bench.
        for (front, back), (score, selected) in states.items():
            for item in groups[role]:
                for zone in sorted(set(item['positions'])):
                    counts = (front + (zone == 'front'), back + (zone == 'back'))
                    if (sum(counts) > population or counts[0] > data['capacity']['front']
                            or counts[1] > data['capacity']['back']):
                        continue
                    candidate = (finite(finite(score + position_score(data, item, zone))
                                        - position_score(data, item, 'bench')), selected + ((item['uid'], zone),))
                    if counts not in following or candidate[0] > following[counts][0]:
                        following[counts] = candidate
        states = following
    legal = [value for counts, value in states.items() if len(units) - sum(counts) <= bench_capacity]
    if not legal:
        return None
    score, selected = max(legal, key=lambda value: (value[0], value[1]))
    slots, placed = {'front': 0, 'back': 0}, {}
    for uid, zone in selected:
        placed[uid] = {'zone': zone, 'slot': slots[zone]}
        slots[zone] += 1
    bench = 0
    layout = []
    for item in units:
        location = placed.get(item['uid'])
        if location is None:
            location, bench = {'zone': 'bench', 'slot': bench}, bench + 1
        layout.append({**item, 'location': location})
    need(len({item['uid'] for item in layout}) == len(layout)
         and len({(item['location']['zone'], item['location']['slot']) for item in layout}) == len(layout),
         'deployment.unique_identity_and_slot')
    return {'estimated_lineup_score': score, 'units': layout}


def xp_options(data, initial, coins, budget):
    xp = data['xp']
    level, progress = initial
    options = [(0, level, progress)]
    if xp.get('enabled') is False:
        return options
    for count in range(1, data['bounds']['max_xp_purchases'] + 1):
        if count * xp['buy_cost'] > min(coins, budget) or level >= xp['horizon_level']:
            break
        progress += xp['buy_gain']
        while level < xp['horizon_level'] and progress >= xp['thresholds'][str(level)]:
            progress -= xp['thresholds'][str(level)]
            level += 1
        options.append((count, level, progress))
    return options


def choices(data, units, offers, initial, coins, budgets):
    subsets = {}
    for mask in range(1 << len(offers)):
        chosen = [offers[i] for i in range(len(offers)) if mask & (1 << i)]
        subsets[mask] = (merged(units + [offer['unit'] for offer in chosen], data['_merge_identities']),
                         sum(offer['price'] for offer in chosen))
    candidates = []
    cache = {}
    def layout(mask, population, capacity):
        key = mask, population, capacity
        if key not in cache:
            cache[key] = deployment(data, subsets[mask][0], population, capacity)
        return cache[key]
    for count, level, progress in xp_options(data, initial, coins, budgets['experience']):
        xp_cost = count * data['xp']['buy_cost'] if count else 0
        population = data['xp']['population_by_level'][str(level)]
        paths = {0: []}
        for mask, (_, purchase_cost) in subsets.items():
            remaining = coins - xp_cost - purchase_cost
            if purchase_cost > budgets['purchase'] or remaining < data['reserve']:
                continue
            arrangement = layout(mask, population, data['capacity']['bench'])
            if arrangement is None:
                continue
            if mask:
                # Conservative: make a real free bench slot before each purchase.
                for index, offer in enumerate(offers):
                    previous = mask ^ (1 << index)
                    if mask & (1 << index) and previous in paths:
                        staging = layout(previous, population, data['capacity']['bench'] - 1)
                        if staging is not None:
                            paths[mask] = paths[previous] + [{'type': 'buy', 'slot': offer['slot'],
                                'price': offer['price'], 'uid': offer['unit']['uid'],
                                'staging_deployment': [{key: item[key] for key in ('uid', 'role_id', 'star', 'location')}
                                                       for item in staging['units']]}]
                            break
                if mask not in paths:
                    continue
            actions = ([{'type': 'buy_xp', 'times': count, 'cost': xp_cost}] if count else []) + paths[mask]
            score = finite(finite(arrangement['estimated_lineup_score']
                                 + finite(remaining * data['scoring']['gold_weight']))
                           + finite(progress * data['scoring']['xp_weight']))
            candidates.append({'estimated_utility': score, 'actions': actions,
                'reason': '停止并保留资金' if not actions else '联合购牌/XP后比较合法部署估分与资金保留价值',
                'budget': {'purchase': purchase_cost, 'experience': xp_cost, 'refresh': 0},
                'coins_retained': remaining, 'level': level, 'xp': progress, 'population': population,
                'deployment': arrangement['units'], 'kind': 'current_shop', 'mask': mask,
                **({'bond_scores': arrangement['bonds'], 'deployment_search': arrangement['search'],
                    'deployment_exact': arrangement['exact']} if 'bonds' in arrangement else {})})
    return sorted(candidates, key=lambda value: (-value['estimated_utility'], sum(value['budget'].values()),
                                                value['budget']['experience'], value['mask']))


def validate(data):
    need(data.get('schema') == SCHEMA, 'schema')
    for key in ('match_id', 'request_id', 'snapshot_id'):
        need(isinstance(data.get('binding', {}).get(key), str) and data['binding'][key].strip(), 'binding.' + key)
    epoch = data['binding'].get('resume_epoch')
    need((type(epoch) is int and epoch >= 0) or (isinstance(epoch, str) and epoch.strip()), 'binding.resume_epoch')
    sourced(data, 'state')
    for key in ('rewards_cleared', 'sell_phase_cleared'):
        need(data.get('preconditions', {}).get(key) is True, 'preconditions.' + key)
    number(data.get('coins'), 'coins', True)
    number(data.get('reserve'), 'reserve', True, 0, data['coins'])
    for key in ('purchase', 'experience', 'refresh'):
        number(data.get('budgets', {}).get(key), 'budgets.' + key, True)
    for key, limit in (('max_xp_purchases', 8), ('max_refresh_prefixes', 8), ('max_refresh_samples', 8)):
        number(data.get('bounds', {}).get(key), 'bounds.' + key, True, 0, limit)
    capacity = sourced(data.get('capacity'), 'capacity')
    for key in ('front', 'back', 'bench'):
        number(capacity.get(key), 'capacity.' + key, True, 0, 16)
    xp = sourced(data.get('xp'), 'xp')
    need(type(xp.get('enabled', True)) is bool, 'xp.enabled')
    for key, low in (('level', 1), ('horizon_level', 1), ('current', 0)):
        number(xp.get(key), 'xp.' + key, True, low, 1000)
    if xp.get('enabled') is False:
        need(xp['horizon_level'] == xp['level'] and data['bounds']['max_xp_purchases'] == 0
             and data['budgets']['experience'] == 0 and not xp.get('thresholds'), 'xp.disabled_scope')
    else:
        for key in ('buy_cost', 'buy_gain'):
            number(xp.get(key), 'xp.' + key, True, 1, 1000)
        need(xp['level'] <= xp['horizon_level'] <= xp['level'] + 8 and xp.get('carry_excess') is True,
             'xp.sourced_carry_excess_model')
    for level in range(xp['level'], xp['horizon_level'] + 1):
        number(xp.get('population_by_level', {}).get(str(level)), 'xp.population_by_level', True, 0, 16)
        if level < xp['horizon_level']:
            number(xp.get('thresholds', {}).get(str(level)), 'xp.thresholds', True, 1)
    need(xp['level'] == xp['horizon_level'] or xp['current'] < xp['thresholds'][str(xp['level'])], 'xp.current')
    need(isinstance(data.get('units'), list) and len(data['units']) <= 24, 'units')
    data['units'] = [unit(value, 'units') for value in data['units']]
    need(type(data.get('shop_enabled', True)) is bool, 'shop.enabled')
    if data.get('shop_enabled') is False:
        need(data.get('shop') == [] and data['budgets']['purchase'] == 0
             and data['budgets']['refresh'] == 0, 'shop.disabled_scope')
        offers = []
    else:
        offers = shop(data.get('shop'), 'shop')
    refresh = data.get('refresh')
    samples = refresh.get('samples', []) if isinstance(refresh, dict) else []
    need(isinstance(samples, list) and len(samples) <= data['bounds']['max_refresh_samples'], 'refresh.samples_bound')
    all_units = data['units'] + [offer['unit'] for offer in offers]
    for sample in samples:
        number(sample.get('weight'), 'refresh.sample.weight', minimum=0, maximum=1)
        all_units.extend(offer['unit'] for offer in shop(sample.get('shop'), 'refresh.sample.shop'))
    data['_merge_identities'] = {'used': {item['uid'] for item in all_units}, 'assigned': {}}
    metadata = {}
    for item in all_units:
        identity = item['name'], item['cost'], sorted(set(item['positions']))
        need(item['role_id'] not in metadata or metadata[item['role_id']] == identity, 'units.role_metadata_conflict')
        metadata[item['role_id']] = identity
    need(len({value[0] for value in metadata.values()}) == len(metadata), 'units.name_role_bijection')
    ids = [item['uid'] for item in data['units'] + [offer['unit'] for offer in offers]]
    need(len(set(ids)) == len(ids), 'units.unique_uid')
    for sample in samples:
        sample_ids = [offer['unit']['uid'] for offer in shop(sample['shop'], 'refresh.sample.shop')]
        need(len(set(sample_ids)) == len(sample_ids) and not set(sample_ids) & set(ids), 'refresh.sample.unique_uid')
    scoring = sourced(data.get('scoring'), 'scoring')
    bond_model = scoring.get('bond_model')
    if bond_model is not None:
        sourced(bond_model, 'scoring.bond_model')
        need(set(bond_model) <= {'source', 'bonds', 'search_limits'}, 'bond_model.keys')
        limits = bond_model.get('search_limits', {})
        need(isinstance(limits, dict) and set(limits) <=
             {'max_states', 'max_transitions', 'time_limit_ms'}, 'bond_model.search_limits')
        number(limits.get('max_states', 100_000), 'bond_model.max_states', True, 1, 1_000_000)
        number(limits.get('max_transitions', 1_000_000), 'bond_model.max_transitions', True, 1, 100_000_000)
        milliseconds = number(limits.get('time_limit_ms', 500), 'bond_model.time_limit_ms', minimum=0, maximum=1000)
        data['_bond_deadline'] = time.perf_counter() + milliseconds / 1000
        from currency_wars_bond_model import solve_deployment
        # Validate the complete explicit model even if this state is infeasible.
        solve_deployment([], lambda item, zone: 0, population=0, front_capacity=0,
                         back_capacity=0, bench_capacity=0, bonds=bond_model.get('bonds'))
    for key in ('gold_weight', 'xp_weight'):
        number(scoring.get(key), 'scoring.' + key, minimum=-100000)
    for role, (_, _, positions) in metadata.items():
        for star in ('1', '2', '3'):
            values = scoring.get('units', {}).get(role, {}).get(star, {})
            number(values.get('bench'), 'scoring.' + role + '.' + star + '.bench', minimum=-100000)
            for position in positions:
                for key in ('damage', 'utility'):
                    number(values.get(position, {}).get(key), 'scoring.' + role + '.' + star + '.' + position + '.' + key, minimum=-100000)
    need(isinstance(data.get('modifiers'), list), 'modifiers.complete_list')
    unknown = []
    for modifier in data['modifiers']:
        sourced(modifier, 'modifier')
        need(isinstance(modifier.get('name'), str) and modifier['name'].strip(), 'modifier.name')
        if modifier.get('status') != 'known':
            unknown.append('modifier.' + modifier['name'] + ('.unmodeled_effect' if modifier.get('status') == 'web_known_unmodeled' else '.effect'))
            continue
        effects = modifier.get('effects')
        need(isinstance(effects, dict) and effects and set(effects) <=
             {'damage_multiplier_by_cost', 'utility_multiplier_by_role'}, 'modifier.supported_effects')
        if 'damage_multiplier_by_cost' in effects:
            for cost in {item['cost'] for item in all_units}:
                number(effects['damage_multiplier_by_cost'].get(str(cost)), 'modifier.damage_cost_multiplier')
        for value in effects.get('utility_multiplier_by_role', {}).values():
            number(value, 'modifier.utility_multiplier')
    return offers, refresh, unknown


def solve(original):
    started, data = time.perf_counter(), copy.deepcopy(original)
    result = {'schema': SCHEMA, 'binding': data.get('binding') if isinstance(data, dict) else None, 'status': 'needs_review',
              'proposal_only': True, 'effects_verified': False, 'input_sent': False,
              'score_kind': 'explicit_uncalibrated_estimate', 'unknown': [], 'candidates': []}
    try:
        need(isinstance(data, dict), 'input_object')
        data.pop('_score_cache', None)
        data.pop('_bond_unknown', None)
        offers, refresh, unknown = validate(data)
        candidates = choices(data, data['units'], offers, (data['xp']['level'], data['xp']['current']), data['coins'], data['budgets'])
        unknown.extend(sorted(data.get('_bond_unknown', set())))
        need(candidates, 'no_legal_budget_and_deployment_candidate')
        try:
            sourced(refresh, 'refresh')
            if refresh.get('enabled') is False:
                need(data['budgets']['refresh'] == 0 and not refresh.get('samples')
                     and data['bounds']['max_refresh_prefixes'] == 0
                     and data['bounds']['max_refresh_samples'] == 0, 'refresh.disabled_scope')
                result.update(status='needs_review' if unknown else 'proposed', unknown=sorted(set(unknown)),
                              candidates=candidates[:8], assumptions_source=data['scoring']['source'],
                              reasons=['本地计算已启用范围；XP/商店/刷新未启用的范围不生成动作。',
                                       '估分不是胜率；当前静态代理模型尚未校准完整战斗交互。'])
                result['runtime_ms'] = round((time.perf_counter() - started) * 1000, 3)
                return result
            need(refresh.get('distribution_kind') in ('explicit_distribution', 'empirical_samples'), 'refresh.distribution')
            need(refresh.get('samples') and abs(sum(x['weight'] for x in refresh['samples']) - 1) < 1e-9, 'refresh.weights_sum_one')
            cost = number(refresh.get('cost'), 'refresh.actual_cost', True)
            number(refresh.get('level'), 'refresh.distribution_level', True, 1, 1000)
            need(refresh.get('mode') in ('free', 'paid') and (refresh['mode'] != 'free' or
                 (cost == 0 and number(refresh.get('free_remaining'), 'refresh.free_remaining', True, 1))), 'refresh.mode')
            for prefix in [x for x in candidates if x['level'] == refresh['level']][:data['bounds']['max_refresh_prefixes']]:
                if prefix['level'] != refresh.get('level') or cost > data['budgets']['refresh']:
                    continue
                remaining = prefix['coins_retained'] - cost
                if remaining < data['reserve']:
                    continue
                budgets = {key: data['budgets'][key] - prefix['budget'][key] for key in data['budgets']}
                expected, outcomes = 0, []
                for sample in refresh['samples']:
                    leaf = choices(data, prefix['deployment'], shop(sample['shop'], 'refresh.sample.shop'),
                                   (prefix['level'], prefix['xp']), remaining, budgets)
                    need(leaf, 'refresh.no_legal_sample_outcome')
                    expected = finite(expected + finite(sample['weight'] * leaf[0]['estimated_utility']))
                    for action in leaf[0]['actions']:
                        action.pop('staging_deployment', None)  # Conditional outcomes are never dispatched.
                    outcomes.append({'weight': sample['weight'], 'proposal': leaf[0], 'total_budget':
                        {key: prefix['budget'][key] + leaf[0]['budget'][key] + (cost if key == 'refresh' else 0)
                         for key in budgets}})
                candidates.append({**prefix, 'kind': 'one_refresh_expected', 'estimated_utility': expected,
                    'actions': prefix['actions'] + [{'type': 'refresh', 'cost': cost}, {'type': 'observe_and_replan'}],
                    'budget': {**prefix['budget'], 'refresh': cost}, 'coins_retained': remaining,
                    'outcomes': outcomes, 'distribution_source': refresh['source']})
            if any(x['level'] != refresh['level'] for x in candidates if x['kind'] == 'current_shop'):
                unknown.append('refresh.distribution_at_other_candidate_levels')
        except NeedsReview as exc:
            unknown.append(str(exc))
        unknown.extend(sorted(data.get('_bond_unknown', set())))
        candidates.sort(key=lambda value: (-value['estimated_utility'], sum(value['budget'].values())))
        result.update(status='needs_review' if unknown else 'proposed', unknown=sorted(set(unknown)),
                      candidates=candidates[:8], assumptions_source=data['scoring']['source'],
                      reasons=['联合比较当前购牌、XP跨级人口和保留金币；刷新仅按明确分布估计。',
                               '评分不是胜率；未知词缀未应用；装备、完整羁绊和战斗交互另行处理。'])
    except (ArithmeticError, NeedsReview, KeyError, TypeError, AttributeError, ValueError) as exc:
        result.update(status='needs_review', unknown=[str(exc)], candidates=[])
    result['runtime_ms'] = round((time.perf_counter() - started) * 1000, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    try:
        need(args.input.stat().st_size <= 200000, 'input_size')
        result = solve(json.loads(args.input.read_text(encoding='utf-8-sig')))
        output = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (ArithmeticError, OSError, ValueError) as exc:
        output = json.dumps({'schema': SCHEMA, 'status': 'needs_review', 'proposal_only': True,
            'effects_verified': False, 'input_sent': False, 'candidates': [], 'unknown': [str(exc)]}, allow_nan=False)
    print(output)


if __name__ == '__main__':
    main()
