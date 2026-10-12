"""Bounded offline deployment with explicitly supplied, verified bond semantics.

The objective is an uncalibrated heuristic, never DPS or a combat prediction.
Only ``distinct_role_in_allowed_positions`` counting is supported. The caller
must first resolve canonical role identities, availability, merged physical UIDs,
and any emblem/investment/conditional rules. This module does not read the DB or
infer those rules. An explicit empty bond list requests the additive model;
missing or incomplete bond definitions raise BondModelError, not zero utility.

``position_score(unit, zone)`` must supply a finite independent score for bench
and every legal front/back zone. Every physical unit stays in the returned
layout, including unselected copies on the real bench. Only one copy of each
role may deploy; no UID, star, or merge lineage is synthesized here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
import math
import time
from typing import Any


SCHEMA = 'currency-wars-bond-deployment/v1'
COUNT_SEMANTICS = 'distinct_role_in_allowed_positions'
ZONES = ('front', 'back')


class BondModelError(ValueError):
    """Incomplete, unsupported, inconsistent, or nonfinite model input."""


@dataclass(frozen=True)
class SearchLimits:
    """Per-frontier and work limits; time is cooperative, including setup.

    At most two frontiers coexist, each with at most max_states entries.
    max_transitions counts attempted deployment options, excluding bench skips.
    A callback cannot be preempted; validation and a small feasibility seed run
    before the first deadline check. No beam or silent state dropping is used.
    """

    max_states: int = 100_000
    max_transitions: int = 1_000_000
    time_limit_ms: float = 500.0


@dataclass(frozen=True)
class _Bond:
    id: str
    source: str
    utility_source: str
    members: dict[str, frozenset[str]]
    thresholds: tuple[tuple[int, float], ...]

    def utility(self, count: int) -> tuple[int | None, float]:
        # The highest attained count replaces all lower tiers, even if its
        # supplied utility is lower. Tiers are never added together.
        for threshold, utility in reversed(self.thresholds):
            if count >= threshold:
                return threshold, utility
        return None, 0.0


@dataclass(frozen=True)
class _Option:
    uid: str
    zone: str
    gain: float
    counts: tuple[int, ...]


def _require(ok: bool, field: str) -> None:
    if not ok:
        raise BondModelError(field)


def _text(value: Any, field: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), field)
    return value


def _integer(value: Any, field: str, lower: int, upper: int) -> int:
    _require(type(value) is int and lower <= value <= upper, field)
    return value


def _finite(value: Any, field: str) -> float:
    _require(type(value) in (int, float), field)
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise BondModelError(field + '.nonfinite') from exc
    _require(math.isfinite(result), field + '.nonfinite')
    return result


def _sum(values: Sequence[float], field: str) -> float:
    try:
        return _finite(math.fsum(values), field)
    except OverflowError as exc:
        raise BondModelError(field + '.nonfinite') from exc


def _positions(value: Any, field: str) -> frozenset[str]:
    _require(isinstance(value, (list, tuple)) and bool(value), field)
    _require(all(isinstance(zone, str) and zone in ZONES for zone in value), field)
    _require(len(set(value)) == len(value), field + '.duplicate')
    return frozenset(value)


def _validate_bonds(values: Any) -> tuple[_Bond, ...]:
    _require(isinstance(values, (list, tuple)) and len(values) <= 64, 'bonds.explicit_list')
    result, ids = [], set()
    allowed = {'id', 'source', 'utility_source', 'count_semantics',
               'semantics_verified', 'members', 'thresholds'}
    for index, value in enumerate(values):
        field = f'bonds[{index}]'
        _require(isinstance(value, Mapping), field)
        _require(set(value) <= allowed, field + '.unsupported_fields')
        identity = _text(value.get('id'), field + '.id')
        _require(identity not in ids, field + '.duplicate_id')
        ids.add(identity)
        source = _text(value.get('source'), field + '.source')
        utility_source = _text(value.get('utility_source'), field + '.utility_source')
        _require(value.get('count_semantics') == COUNT_SEMANTICS,
                 field + '.unsupported_count_semantics')
        _require(value.get('semantics_verified') is True, field + '.semantics_unverified')
        rows = value.get('members')
        _require(isinstance(rows, (list, tuple)) and 0 < len(rows) <= 512, field + '.members')
        members = {}
        for row in rows:
            _require(isinstance(row, Mapping) and set(row) == {'role_id', 'positions'},
                     field + '.member_fields')
            role = _text(row.get('role_id'), field + '.member.role_id')
            _require(role not in members, field + '.duplicate_member_role')
            members[role] = _positions(row.get('positions'), field + '.member.positions')
        rows = value.get('thresholds')
        _require(isinstance(rows, (list, tuple)) and 0 < len(rows) <= 64, field + '.thresholds')
        thresholds = {}
        for row in rows:
            _require(isinstance(row, Mapping) and set(row) == {'count', 'utility'},
                     field + '.threshold_fields')
            count = _integer(row.get('count'), field + '.threshold.count', 1, 1_000_000)
            _require(count not in thresholds, field + '.duplicate_threshold')
            thresholds[count] = _finite(row.get('utility'), field + '.threshold.utility')
        result.append(_Bond(identity, source, utility_source, members,
                            tuple(sorted(thresholds.items()))))
    return tuple(sorted(result, key=lambda item: item.id))


def solve_deployment(
    units: Sequence[Mapping[str, Any]],
    position_score: Callable[[dict[str, Any], str], float],
    *,
    population: int,
    front_capacity: int,
    back_capacity: int,
    bench_capacity: int,
    bonds: Sequence[Mapping[str, Any]],
    limits: SearchLimits | None = None,
) -> dict[str, Any]:
    """Maximize independent scores plus each bond's highest attained tier.

    DP dominance is applied only at identical (front, back, bond-count-vector).
    Counts saturate at each bond's final threshold; actual counts are recounted
    for the output. With no bonds this reduces to the independent assignment DP.
    Ties use lexicographically smaller (uid, zone) tuples in canonical role order.

    Returns ``optimal`` only after the complete DP, or ``infeasible`` after a
    structural proof. On any search limit, ``bounded`` explicitly marks a legal
    incumbent approximate and its optimality unproven. A deterministic maximum
    cardinality seed ensures a legal incumbent whenever the input is feasible.
    The deadline is cooperative, not a hard process/callback execution timeout.
    """
    started = time.perf_counter()
    _require(callable(position_score), 'position_score.callback')
    _integer(population, 'population', 0, 64)
    _integer(front_capacity, 'front_capacity', 0, 64)
    _integer(back_capacity, 'back_capacity', 0, 64)
    _integer(bench_capacity, 'bench_capacity', 0, 64)
    _require(isinstance(units, (list, tuple)) and len(units) <= 64, 'units.physical_list')
    limits = SearchLimits() if limits is None else limits
    _require(isinstance(limits, SearchLimits), 'limits')
    _integer(limits.max_states, 'limits.max_states', 1, 1_000_000)
    _integer(limits.max_transitions, 'limits.max_transitions', 1, 100_000_000)
    deadline_ms = _finite(limits.time_limit_ms, 'limits.time_limit_ms')
    _require(0 <= deadline_ms <= 60_000, 'limits.time_limit_ms')
    definitions = _validate_bonds(bonds)
    caps = tuple(bond.thresholds[-1][0] for bond in definitions)
    physical, seen, names, role_names = [], set(), {}, {}
    for index, value in enumerate(units):
        field = f'units[{index}]'
        _require(isinstance(value, Mapping), field)
        item = deepcopy(dict(value))
        uid = _text(item.get('uid'), field + '.uid')
        role = _text(item.get('role_id'), field + '.role_id')
        _require(uid not in seen, field + '.duplicate_uid')
        seen.add(uid)
        _positions(item.get('positions'), field + '.positions')
        if 'name' in item:
            name = _text(item['name'], field + '.name')
            _require(name not in names or names[name] == role, field + '.name_role_conflict')
            _require(role not in role_names or role_names[role] == name, field + '.role_name_conflict')
            names[name], role_names[role] = role, name
        physical.append(item)

    # Each physical copy contributes its bench value until selected. Within
    # one role and zone, count effects are identical, so only the best gain is
    # needed. This is safe even with different stars and physical UID scores.
    grouped: dict[str, dict[str, _Option]] = {}
    bench_scores = []

    def evaluate(item: dict[str, Any], zone: str) -> float:
        field = f"score.{item['uid']}.{zone}"
        try:
            value = position_score(deepcopy(item), zone)
        except Exception as exc:
            raise BondModelError(field + '.unavailable') from exc
        return _finite(value, field)

    for item in sorted(physical, key=lambda row: row['uid']):
        uid, role = item['uid'], item['role_id']
        bench_score = evaluate(item, 'bench')
        bench_scores.append(bench_score)
        options = grouped.setdefault(role, {})
        for zone in sorted(item['positions']):
            score = evaluate(item, zone)
            gain = _finite(score - bench_score, f'score.{uid}.{zone}.gain')
            counts = tuple(int(zone in bond.members.get(role, ())) for bond in definitions)
            option = _Option(uid, zone, gain, counts)
            previous = options.get(zone)
            if previous is None or (-gain, uid) < (-previous.gain, previous.uid):
                options[zone] = option
    baseline = _sum(bench_scores, 'score.bench_sum')
    roles = sorted(grouped)
    groups = [tuple(grouped[role][zone] for zone in sorted(grouped[role])) for role in roles]
    required = max(0, len(physical) - bench_capacity)
    search = {'algorithm': 'full_count_vector_dp' if definitions else 'additive_dp',
              'limits': asdict(limits), 'limit_reached': None, 'processed_roles': 0,
              'total_roles': len(roles), 'peak_states': 1, 'transitions': 0}

    def finish(status: str, best: dict[str, Any] | None) -> dict[str, Any]:
        search['runtime_ms'] = round((time.perf_counter() - started) * 1000, 3)
        return {'schema': SCHEMA, 'status': status, 'exact': status != 'bounded',
                'approximate': status == 'bounded', 'feasible': best is not None,
                'score_kind': 'heuristic_not_dps', 'model_scope': 'supplied_verified_bonds_only',
                'optimality': ('unproven_search_limit' if status == 'bounded' else
                               'proven_infeasible' if status == 'infeasible' else
                               'proven_for_supplied_heuristic'),
                'best': best, 'search': search}

    # Single-zone roles first, then flexible roles: for two zones this achieves
    # maximum cardinality. Truncating the chosen legal set to population keeps
    # it legal. If it cannot empty the bench sufficiently, no assignment can.
    remaining = {'front': front_capacity, 'back': back_capacity}
    seed: dict[str, _Option] = {}
    for flexible in (False, True):
        for role, options in zip(roles, groups):
            if (len(options) == 2) != flexible:
                continue
            eligible = [option for option in options if remaining[option.zone] > 0]
            if eligible and len(seed) < population:
                chosen = min(eligible, key=lambda option: (-option.gain, option.uid, option.zone))
                seed[role] = chosen
                remaining[chosen.zone] -= 1
    if len(seed) < required:
        return finish('infeasible', None)

    def objective(score: float, counts: tuple[int, ...]) -> float:
        utility = _sum([bond.utility(count)[1] for bond, count in zip(definitions, counts)],
                       'score.bond_sum')
        return _finite(score + utility, 'score.total')

    seed_score, seed_counts = baseline, [0] * len(definitions)
    for role in sorted(seed):
        option = seed[role]
        seed_score = _finite(seed_score + option.gain, 'score.independent_sum')
        seed_counts = [old + gain for old, gain in zip(seed_counts, option.counts)]
    selected = tuple((seed[role].uid, seed[role].zone) for role in sorted(seed))
    incumbent = (objective(seed_score, tuple(seed_counts)), seed_score, selected)

    def consider(score: float, selected: tuple[tuple[str, str], ...],
                 counts: tuple[int, ...]) -> None:
        nonlocal incumbent
        if len(selected) >= required:
            total = objective(score, counts)
            if total > incumbent[0] or (total == incumbent[0] and selected < incumbent[2]):
                incumbent = (total, score, selected)

    zero_counts = (0,) * len(definitions)
    consider(baseline, (), zero_counts)
    states = {(0, 0, zero_counts): (baseline, ())}
    truncated = None
    for options in groups:
        if (time.perf_counter() - started) * 1000 >= deadline_ms:
            truncated = 'time_limit_ms'
            break
        following = dict(states)  # Leave every copy of this role on the bench.
        for (front, back, counts), (score, selected) in states.items():
            for option in options:
                if search['transitions'] >= limits.max_transitions:
                    truncated = 'max_transitions'
                    break
                if (time.perf_counter() - started) * 1000 >= deadline_ms:
                    truncated = 'time_limit_ms'
                    break
                search['transitions'] += 1
                nf, nb = front + (option.zone == 'front'), back + (option.zone == 'back')
                if nf + nb > population or nf > front_capacity or nb > back_capacity:
                    continue
                next_counts = tuple(min(cap, count + gain)
                                    for cap, count, gain in zip(caps, counts, option.counts))
                key = nf, nb, next_counts
                candidate_score = _finite(score + option.gain, 'score.independent_sum')
                candidate_selected = selected + ((option.uid, option.zone),)
                previous = following.get(key)
                if previous is not None and (candidate_score < previous[0] or
                        (candidate_score == previous[0] and candidate_selected >= previous[1])):
                    continue
                consider(candidate_score, candidate_selected, next_counts)
                if previous is None and len(following) >= limits.max_states:
                    truncated = 'max_states'
                    break
                following[key] = candidate_score, candidate_selected
                search['peak_states'] = max(search['peak_states'], len(following))
            if truncated:
                break
        if truncated:
            break
        states = following
        search['processed_roles'] += 1
    search['limit_reached'] = truncated

    total, independent, selected = incumbent
    locations, slots = {}, {'front': 0, 'back': 0, 'bench': 0}
    for uid, zone in selected:
        locations[uid] = {'zone': zone, 'slot': slots[zone]}
        slots[zone] += 1
    layout = []
    for item in physical:
        location = locations.get(item['uid'])
        if location is None:
            location = {'zone': 'bench', 'slot': slots['bench']}
            slots['bench'] += 1
        layout.append({**item, 'location': location})
    summaries = []
    for bond in definitions:
        counted = sorted(item['role_id'] for item in layout
                         if item['location']['zone'] in bond.members.get(item['role_id'], ()))
        threshold, utility = bond.utility(len(counted))
        summaries.append({'id': bond.id, 'count': len(counted), 'counted_role_ids': counted,
                          'active_threshold': threshold, 'heuristic_utility': utility,
                          'source': bond.source, 'utility_source': bond.utility_source,
                          'count_semantics': COUNT_SEMANTICS})
    bond_utility = _sum([item['heuristic_utility'] for item in summaries], 'score.bond_sum')
    best = {'estimated_lineup_score': total, 'independent_score': independent,
            'bond_utility': bond_utility, 'units': layout, 'bonds': summaries}
    return finish('bounded' if truncated else 'optimal', best)
