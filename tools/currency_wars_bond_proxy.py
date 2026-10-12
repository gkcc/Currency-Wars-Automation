"""Source-backed bond features and bounded conditional layout comparison.

The score is a declared heuristic, not a damage formula or current activation.
Only base catalogue membership and released raw property components are used.
References identify hypothetical choices, never physical UIDs or input plans.
"""
from __future__ import annotations

import copy
import math

from currency_wars_database import normalize_name

PROPERTY_WEIGHTS = {
    'ExtraFrontPowerAddedRatio1': .5, 'ExtraBackPowerAddedRatio1': .5,
    'ExtraAllDamageTypeAddedRatio1': 1., 'ExtraSpeedAddedRatio1': .5,
    'ExtraHPAddedRatio1': .25, 'ExtraShieldAddedRatio': .25,
    'ExtraHealAddedRatio': .25, 'ExtraLuckChance': .75, 'ExtraLuckDamage': .25,
    'ExtraInsertDamageAddedRatio1': .5, 'ExtraUltraDamageAddedRatio1': .5,
    'ExtraDOTDamageAddedRatio1': .5,
}


def compile_model(pack):
    bonds, excluded = [], []
    for rule in pack['rules']:
        if rule['kind'] != 'bonds':
            continue
        thresholds = []
        for tier in rule.get('thresholds', []):
            coeff, unused = {}, []
            for scope in ('trait_members', 'whole_team'):
                values = tier['property_components'][scope]
                coeff[scope] = math.fsum(item['value'] * PROPERTY_WEIGHTS[item['property']]
                    for item in values if item['property'] in PROPERTY_WEIGHTS)
                unused.extend(item['property'] for item in values if item['property'] not in PROPERTY_WEIGHTS)
            thresholds.append({'members': tier['members'], **coeff,
                'source': copy.deepcopy(tier['source']), 'unscored_properties': sorted(set(unused))})
        invalid = any(issue in rule['unresolved'] for issue in
                      ('duplicate_member_threshold', 'invalid_member_threshold'))
        if (rule.get('activation_type') != 'GreaterEqualThan' or invalid or not thresholds
                or not any(t['trait_members'] or t['whole_team'] for t in thresholds)):
            excluded.append({'name': rule['name'], 'reason': 'no_supported_raw_property_feature_or_activation'})
            continue
        bonds.append({'key': rule['key'], 'name': rule['name'], 'thresholds': thresholds,
                      'source': copy.deepcopy(rule['source'])})
    families = {}
    for rule in pack['rules']:
        if rule['kind'] == 'characters':
            families.setdefault(normalize_name(rule['name']), []).append(set(rule.get('bonds', [])))
    membership = {}
    for name, variants in families.items():
        minimum, maximum = set.intersection(*variants), set.union(*variants)
        membership[name] = {'minimum': [int(b['name'] in minimum) for b in bonds],
                            'maximum': [int(b['name'] in maximum) for b in bonds]}
    return {'schema': 'currency-wars-bond-property-proxy/v1',
            'database_sha256': pack['database_sha256'], 'bonds': bonds,
            'membership': membership, 'excluded_bonds': excluded,
            'property_weights': copy.deepcopy(PROPERTY_WEIGHTS),
            'formula': 'reference_power * (base_members * highest_tier_member_feature + deployed * highest_tier_team_feature)',
            'current_effects_verified': False, 'complete_combat_model': False,
            'scope': 'Base-only raw-property feature model; scripts, dynamic membership and stacking unmodeled.'}


def _bonus(model, key, endpoint, scale):
    front, back, minimum, maximum = key
    result = 0.
    for index, bond in enumerate(model['bonds']):
        possibilities = []
        for count in range(minimum[index], maximum[index] + 1):
            eligible = [tier for tier in bond['thresholds'] if tier['members'] <= count]
            tier = eligible[-1] if eligible else None
            possibilities.append(0. if tier is None else
                count * tier['trait_members'] + (front+back) * tier['whole_team'])
        result += (min(possibilities) if endpoint == 'minimum' else max(possibilities)) * scale
    return result


def restrict_model(model, families):
    """Remove only features structurally unreachable in the modeled choices."""
    families = set(families)
    available = [model['membership'][name] for name in families if name in model['membership']]
    indexes = [index for index, bond in enumerate(model['bonds'])
               if sum(member['maximum'][index] for member in available) >= bond['thresholds'][0]['members']]
    result = copy.deepcopy(model)
    result['structurally_unreachable_in_modeled_choices'] = [bond['name'] for index,bond in enumerate(model['bonds'])
                                                           if index not in indexes]
    result['bonds'] = [result['bonds'][index] for index in indexes]
    result['membership'] = {name: {endpoint: [member[endpoint][index] for index in indexes]
        for endpoint in ('minimum', 'maximum')} for name,member in result['membership'].items()}
    return result


def layouts(items, model, *, front_capacity=4, back_capacity=6, scale=100.,
            max_states=1024, budget=None):
    """DP retains both endpoint incumbents at identical counts and bond vectors.

    Limits produce explicitly approximate feasible incumbents, never optimality
    proof. Baseline includes every scored holding on bench, including groups not
    visited before a work limit. No merging or synthetic physical identities.
    """
    if (type(max_states) is not int or not 35 <= max_states <= 8192
            or type(scale) not in (int, float) or not math.isfinite(scale) or scale < 0):
        raise ValueError('invalid bond layout limits/scale')
    budget = {'remaining': 100000} if budget is None else budget
    if type(budget.get('remaining')) is not int or budget['remaining'] < 0:
        raise ValueError('invalid bond transition budget')
    groups, baseline = {}, [0., 0.]
    for item in items:
        benches = tuple(max(power[endpoint] for power in item['powers'].values()) * item['bench_weight']
                        for endpoint in ('minimum', 'maximum'))
        baseline = [baseline[i] + benches[i] for i in range(2)]
        groups.setdefault(item['family'], []).append((item, benches))
    zero = (0,) * len(model['bonds'])
    states = {(0, 0, zero, zero): ((baseline[0], ()), (baseline[1], ()))}
    pruned, exhausted, transitions, peak = False, False, 0, 1
    bonus_cache = {}
    def endpoints(key, value):
        if key not in bonus_cache:
            bonus_cache[key] = tuple(_bonus(model, key, endpoint, scale) for endpoint in ('minimum', 'maximum'))
        return tuple(value[i][0] + bonus_cache[key][i] for i in range(2))
    for family in sorted(groups):
        if not budget['remaining']:
            exhausted = True
            break
        member = model['membership'].get(family)
        if member is None:
            raise ValueError('missing source-backed bond membership: ' + family)
        following = dict(states)
        stop = False
        for key, previous in states.items():
            for item, benches in groups[family]:
                for zone, power in sorted(item['powers'].items()):
                    if not budget['remaining']:
                        exhausted, stop = True, True
                        break
                    budget['remaining'] -= 1
                    transitions += 1
                    front, back = key[0] + (zone == 'front'), key[1] + (zone == 'back')
                    if front > front_capacity or back > back_capacity:
                        continue
                    next_key = (front, back,
                        tuple(a+b for a,b in zip(key[2], member['minimum'])),
                        tuple(a+b for a,b in zip(key[3], member['maximum'])))
                    choices = tuple((previous[i][0] + power[endpoint] - benches[i],
                                     previous[i][1] + ((item['ref'], zone),))
                                    for i, endpoint in enumerate(('minimum', 'maximum')))
                    old = following.get(next_key)
                    following[next_key] = choices if old is None else tuple(max(old[i], choices[i]) for i in range(2))
                if stop:
                    break
            if stop:
                break
        peak = max(peak, len(following))
        if len(following) > max_states:
            pruned = True
            scores = {key: endpoints(key, value) for key, value in following.items()}
            # Keep each conditional capacity's incumbents, including cap0 hold.
            keep = set()
            for cap in range(11):
                allowed = [key for key in following if key[0]+key[1] <= cap]
                for index in range(2):
                    keep.add(max(allowed, key=lambda key: (scores[key][index], following[key][index][1], key)))
            ordered = sorted(following, key=lambda key: (scores[key][0], scores[key][1], key), reverse=True)
            for key in ordered:
                if len(keep) >= max_states:
                    break
                keep.add(key)
            states = {key: following[key] for key in ordered if key in keep}
        else:
            states = following
        if stop:
            break
    scores = {key: endpoints(key, value) for key, value in states.items()}
    result = {}
    for index, endpoint in enumerate(('minimum', 'maximum')):
        result[endpoint] = {cap: max((scores[key][index], value[index][1])
                             for key, value in states.items() if key[0]+key[1] <= cap)
                           for cap in range(11)}
    result['search'] = {'algorithm': 'paired_endpoint_bond_count_vector_dp',
        'exact_for_declared_model': not pruned and not exhausted,
        'state_pruning_used': pruned, 'transition_limit_reached': exhausted,
        'transitions': transitions, 'peak_states_before_pruning': peak,
        'retained_states': len(states), 'max_states': max_states,
        'scope': 'Conditional scored-component incumbents; not full combat or execution permission.'}
    return result
