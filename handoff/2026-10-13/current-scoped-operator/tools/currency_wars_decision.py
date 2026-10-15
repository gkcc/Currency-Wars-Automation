"""Read-only catalogue-backed decisions. No controller, capture, network or model API."""
from __future__ import annotations

import argparse
import copy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

import currency_wars_database as catalogue_db
from currency_wars_solver import NeedsReview, need, number, solve

SCHEMA = 'currency-wars-catalogue-decision/v1'
DEFAULT_DATABASE = Path(__file__).resolve().parent.parent / 'data/currency_wars/catalog.sqlite3'
POSITIONS = {'前台': ['front'], '后台': ['back'], '前后台': ['front', 'back']}


class Catalogue:
    def __init__(self, path=DEFAULT_DATABASE):
        self.path, self._stamp, self._cache = Path(path), None, {}
        self._compiled_rules = None
        self._compiled_bond_proxy = None
        self._economy_config = None
        self._economic_pool = None

    def _refresh(self):
        st = self.path.stat()
        stamp = st.st_mtime_ns, st.st_size
        if stamp != self._stamp:
            self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
            with closing(catalogue_db.open_db(self.path)) as db:
                self.metadata = {r['key']: json.loads(r['value_json']) for r in db.execute('SELECT * FROM metadata')}
            self._cache.clear()
            self._compiled_rules = None
            self._compiled_bond_proxy = None
            self._economy_config = None
            self._economic_pool = None
            self._stamp = stamp

    def _rulepack(self):
        self._refresh()
        if self._compiled_rules is None:
            from currency_wars_rules import compile_catalogue
            with closing(catalogue_db.open_db(self.path)) as db:
                compiled = compile_catalogue(db, self.digest)
            need(hashlib.sha256(self.path.read_bytes()).hexdigest() == self.digest,
                 'catalogue.changed_during_rule_compilation')
            self._compiled_rules = compiled
        return self._compiled_rules

    def rules(self, name=None, kind=None):
        """Compiled components; unfiltered queries return coverage, not a huge dump."""
        need(kind is None or kind in catalogue_db.KINDS, 'catalogue.kind')
        need(name is None or (isinstance(name, str) and 0 < len(name.strip()) <= 150), 'catalogue.name')
        pack = self._rulepack()
        if name is not None and kind is not None:
            selected_keys = {kind + ':' + str(row['source_key'])
                             for row in self.lookup(name, kind, star=None)['records']
                             if row['availability'] == 'current'}
            selected = [row for row in pack['rules'] if row['key'] in selected_keys]
        elif name is not None:
            selected = [row for row in pack['rules'] if
                        catalogue_db.normalize_name(row['name']) == catalogue_db.normalize_name(name)]
        elif kind is not None:
            selected = [row for row in pack['rules'] if row['kind'] == kind]
        else:
            selected = []
        return {k: copy.deepcopy(v) for k, v in pack.items() if k != 'rules'} | {
            'status': 'coverage' if name is None and kind is None else 'found' if selected else 'not_found',
            'rules': copy.deepcopy(selected)}

    def bond_scenario(self, deployed_names):
        from currency_wars_rules import evaluate_bonds
        return evaluate_bonds(self._rulepack(), deployed_names)

    def investment_scenario(self, names, event, counters=None):
        from currency_wars_rules import evaluate_investment_events
        return evaluate_investment_events(self._rulepack(), names, event, counters)

    def bond_proxy_model(self):
        self._refresh()
        if self._compiled_bond_proxy is None:
            from currency_wars_bond_proxy import compile_model
            self._compiled_bond_proxy = compile_model(self._rulepack())
        return copy.deepcopy(self._compiled_bond_proxy)

    def economy_config(self):
        """Released alternatives, never a claim that a match uses one profile."""
        self._refresh()
        if self._economy_config is None:
            with closing(catalogue_db.open_db(self.path)) as db:
                rows = catalogue_db.records(db, kind='economy_rules', state='current', full=True)
            values = {row['source_key']: copy.deepcopy(row['data']) for row in rows}
            self._economy_config = {'database_sha256': self.digest,
                'constants': values.get('constants', {}),
                'profiles': {name: values.get(name, {}).get('value', [])
                             for name in ('player_levels', 'level_v2')},
                'sources': values.get('sources', {}).get('value', []),
                'effective_profile_verified': False}
            need(hashlib.sha256(self.path.read_bytes()).hexdigest() == self.digest,
                 'catalogue.changed_during_economy_compilation')
        return copy.deepcopy(self._economy_config)

    def economic_prior_pool(self):
        """Conservative released ordinary candidates; active runtime pool unknown."""
        self._refresh()
        if self._economic_pool is None:
            with closing(catalogue_db.open_db(self.path)) as db:
                rows = catalogue_db.records(db, kind='characters', state='current', full=True)
            included, excluded = [], []
            for row in rows:
                data = row['data']
                pool = data.get('pool_status') or {}
                reasons = []
                if row.get('availability') != 'current' or pool.get('is_in_pool') is not True:
                    reasons.append('not_current_released_pool_member')
                if pool.get('is_expert') or pool.get('module_bans') or data.get('switch_conditions'):
                    reasons.append('special_or_conditional_pool')
                if pool.get('excluded_from_initial_role_selection'):
                    reasons.append('initial_pool_excluded_conservative_prior')
                effects = [e for e in data.get('star_effects', []) if e.get('star') == 1]
                positions = POSITIONS.get(data.get('position'), [])
                powers = {z: effects[0].get('FrontPowerBase' if z == 'front' else 'BackPowerBase')
                          for z in positions} if len(effects) == 1 else {}
                price = (data.get('prices_by_star') or {}).get('BuyGoldStar1')
                if not positions or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                                        for v in powers.values()) or type(price) is not int or price < 1:
                    reasons.append('one_star_power_or_price_missing')
                if reasons:
                    excluded.append({'role_id': str(data.get('id')), 'reasons': reasons})
                else:
                    included.append({'role_id': str(data['id']), 'name': row['name'],
                        'rarity': row['cost'], 'price': price, 'powers': powers,
                        'source': copy.deepcopy(data.get('source')), 'availability': 'current'})
            self._economic_pool = {'database_sha256': self.digest, 'cards': included,
                'excluded': excluded, 'runtime_pool_verified': False,
                'scope': 'Released conservative ordinary pool; uniform within rarity is a declared prior.'}
            need(hashlib.sha256(self.path.read_bytes()).hexdigest() == self.digest,
                 'catalogue.changed_during_economic_pool')
        return copy.deepcopy(self._economic_pool)

    def lookup(self, name, kind, cost=None, star=None):
        need(kind in catalogue_db.KINDS, 'catalogue.kind')
        need(isinstance(name, str) and 0 < len(name.strip()) <= 150, 'catalogue.name')
        if cost is not None:
            number(cost, 'catalogue.cost', True, 1, 99)
        if star is not None:
            number(star, 'catalogue.star', True, 1, 4)
        self._refresh()
        key = catalogue_db.normalize_name(name), kind, cost
        if key not in self._cache:
            with closing(catalogue_db.open_db(self.path)) as db:
                rows = catalogue_db.records(db, name=name, kind=kind, cost=cost, full=True)
            preferred = [r for r in rows if r['availability'] == 'current']
            self._cache[key] = preferred or rows
        rows = copy.deepcopy(self._cache[key])
        for row in rows:
            row.pop('source', None)  # Keep per-record provenance; do not repeat the whole import manifest.
            row['data'] = catalogue_db.compact_data(row['data'], star)
        return {'schema': 'currency-wars-catalogue-query/v1', 'status': 'found' if rows else 'not_found',
                'input_sent': False, 'database_sha256': self.digest, 'metadata': copy.deepcopy(self.metadata),
                'records': rows, 'scope': 'Static definitions only; no current ownership, price or enabled-pool claim.'}

    def query(self, name, kind, cost=None, star=1, detail=False):
        need(type(detail) is bool, 'catalogue.detail')
        result = self.lookup(name, kind, cost, star)
        for row in result['records']:
            data = row['data']
            if not detail:
                row['data'] = {k: data[k] for k in ('id', 'name', 'cost', 'position', 'bonds', 'pool_status',
                    'prices_by_star', 'effects', 'activation_type', 'thresholds', 'recipes', 'source') if k in data}
                if 'star_effects' in data:
                    row['data']['star_effects'] = [{k: s[k] for k in ('star', 'FrontPowerBase', 'BackPowerBase',
                        'front_summary', 'back_summary') if k in s} for s in data['star_effects']]
                if 'thresholds' in row['data']:
                    row['data']['thresholds'] = [{k: t[k] for k in ('members', 'quality', 'effects') if k in t}
                                               for t in row['data']['thresholds']]
        return result

    def character(self, name, cost=None):
        result = self.lookup(name, 'characters', cost)
        rows = [r for r in result['records'] if r['availability'] == 'current']
        need(len(rows) == 1, 'catalogue.character.' + name + '.missing_or_ambiguous_current_variant')
        return rows[0]


def utc(value):
    need(isinstance(value, str), 'freshness.timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(parsed.tzinfo is not None, 'freshness.timezone')
    return parsed.astimezone(timezone.utc)


def bound_proof(proof, binding, *, same_snapshot=False):
    return (isinstance(proof, dict) and proof.get('source') == 'observed_screen'
            and proof.get('resume_epoch') == binding['resume_epoch']
            and isinstance(proof.get('evidence_file'), str) and bool(proof['evidence_file'])
            and isinstance(proof.get('snapshot_id'), str) and bool(proof['snapshot_id'])
            and proof.get('match_id', binding['match_id']) == binding['match_id']
            and (not same_snapshot or proof['snapshot_id'] == binding['snapshot_id']))


def prepare(state, catalogue, policy=None, now=None):
    """Construct only supported scopes; missing dynamic facts never come from defaults."""
    need(isinstance(state, dict) and state.get('schema') == 'currency-wars-mcp/v1', 'state.schema')
    need(state.get('status') == 'observed' and state.get('source') == 'authenticated_runner_status',
         'state.current_authenticated_observation')
    policy = {} if policy is None else policy
    need(isinstance(policy, dict) and set(policy) <= {'gold_weight', 'bench_weight', 'reserve', 'purchase_budget', 'target_names', 'bond_model'}, 'policy.keys')
    binding = state.get('binding') or {}
    for key in ('request_id', 'snapshot_id', 'match_id'):
        need(isinstance(binding.get(key), str) and bool(binding[key]), 'binding.' + key)
    epoch = binding.get('resume_epoch')
    need((type(epoch) is int and epoch >= 0) or (isinstance(epoch, str) and bool(epoch)), 'binding.resume_epoch')
    snap = binding['snapshot_id']
    need(snap == state.get('snapshot_id') == (state.get('observation') or {}).get('snapshot_id')
         and binding['match_id'] == state.get('match_id'), 'binding.atomic_snapshot_and_match')
    current = now or datetime.now(timezone.utc)
    captured = utc((state.get('freshness') or {}).get('captured_at'))
    need(0 <= (current - captured).total_seconds() <= 60, 'freshness.capture_age')
    deadline = utc((state.get('freshness') or {}).get('request_deadline'))
    need(current < deadline, 'freshness.request_deadline')
    need((state.get('observation', {}).get('read_contract') or {}).get('effective_scope') == 'full',
         'state.full_read_contract')
    need(state.get('page') in ('shop', 'preparation'), 'state.preparation_page')
    checklist = state.get('preparation_checklist') or {}
    need(checklist.get('economy_allowed') is True, 'preconditions.economy_allowed')
    steps = checklist.get('steps') or []
    for phase in ('rewards', 'inventory_cleanup'):
        matches = [s for s in steps if isinstance(s, dict) and s.get('id') == phase]
        need(len(matches) == 1 and matches[0].get('completed') is True
             and bound_proof(matches[0].get('proof'), binding), 'preconditions.' + phase + '.accepted_proof')
    fields, semantic = state.get('fields') or {}, state.get('semantic') or {}
    def known(name, normalized=False):
        field = fields.get(name) or {}
        need(field.get('known') is True, 'fields.' + name + '.known')
        return field.get('normalized_value') if normalized else field.get('value')
    coins = number(known('coins', True), 'coins', True)
    coin_proof = (fields['coins'].get('proof') or {})
    need(coin_proof.get('snapshot_id') == snap and coin_proof.get('value') == coins, 'coins.current_proof')
    level = number(known('player_level'), 'level', True, 1, 1000)
    xp = known('xp')
    need(isinstance(xp, list) and len(xp) == 2 and all(type(v) is int for v in xp)
         and 0 <= xp[0] < xp[1], 'xp.current_progress')
    population = re.fullmatch(r'(\d+)/(\d+)', known('population') or '')
    need(population is not None, 'population.current')
    deployed, maximum = map(int, population.groups())
    need(0 <= deployed <= maximum <= 16, 'population.range')
    player = semantic.get('player_hud') or {}
    need(player.get('actor') == 'player' and player.get('origin') == 'native_player_hud'
         and player.get('snapshot_id') == snap and player.get('level') == level and player.get('xp') == xp,
         'player_hud.atomic')
    team = semantic.get('team') or {}
    capacity = team.get('capacity') or {}
    need(team.get('checked') is True and team.get('fully_read') is True
         and team.get('snapshot_id') == snap and not team.get('unknown_slots'), 'team.complete_current_read')
    need(capacity.get('snapshot_id') == snap and capacity.get('bench_checked') is True
         and capacity.get('bench_capacity') == 9 and capacity.get('overflow_checked') is True
         and type(capacity.get('overflow_count')) is int and capacity['overflow_count'] == 0,
         'team.bench_and_overflow_complete')
    definitions = {('board', 'front', s) for s in range(1, 5)} | {
        ('board', 'back', s) for s in range(1, 7)} | {('bench', 'bench', s) for s in range(1, 10)}
    slots = team.get('slots') or []
    need(len(slots) == len(definitions) and {tuple(s.get(k) for k in ('location', 'row', 'slot')) for s in slots} == definitions
         and all(s.get('snapshot_id') == snap and s.get('status') in ('empty', 'occupied') for s in slots),
         'team.all_native_slots')
    occupied = [s for s in slots if s['status'] == 'occupied']
    units_read = team.get('units') or []
    need(len(units_read) == len(occupied) and sum(s['location'] == 'board' for s in occupied) == deployed
         and capacity.get('occupied') == sum(s['location'] == 'bench' for s in occupied), 'team.counts')
    need(sorted((s.get('location'), s.get('row'), s.get('slot'), s.get('name'), s.get('star')) for s in occupied)
         == sorted((s.get('location'), s.get('row'), s.get('slot'), s.get('name'), s.get('star')) for s in units_read),
         'team.units_match_slots')
    gold_weight = number(policy.get('gold_weight', 20), 'policy.gold_weight', minimum=0, maximum=10000)
    bench_weight = number(policy.get('bench_weight', .15), 'policy.bench_weight', minimum=0, maximum=1)
    targets = policy.get('target_names', [])
    need(isinstance(targets, list) and len(targets) <= 24 and all(isinstance(n, str) and n.strip() for n in targets), 'policy.target_names')
    targets = {catalogue_db.normalize_name(n) for n in targets}
    catalogue._refresh()
    catalogue_digest = catalogue.digest
    scoring, selected = {}, {}
    def resolve(name, star, uid, cost=None, observed_position=None):
        number(star, 'unit.star', True, 1, 3)
        row = catalogue.character(name, cost)
        data = row['data']
        positions = POSITIONS.get(data.get('position'))
        need(positions and (observed_position is None or POSITIONS.get(observed_position) == positions), 'unit.position.' + name)
        role = str(data['id']) + ':' + str(row['cost'])
        selected[role] = row
        values = {}
        boost = 1.25 if catalogue_db.normalize_name(name) in targets else 1.
        for rank in (1, 2, 3):
            matches = [s for s in data.get('star_effects', []) if s.get('star') == rank]
            need(len(matches) == 1, 'catalogue.star_power.' + name)
            params = {}
            for zone in positions:
                power = number(matches[0].get('FrontPowerBase' if zone == 'front' else 'BackPowerBase'), 'catalogue.power.' + name)
                params[zone] = {'damage': 0, 'utility': power * boost}
            params['bench'] = max(v['utility'] for v in params.values()) * bench_weight
            values[str(rank)] = params
        scoring[role] = values
        return {'uid': uid, 'role_id': role, 'name': row['name'], 'cost': row['cost'], 'star': star,
                'positions': positions, 'source': 'current_snapshot:' + snap + ';catalogue:' + catalogue.digest}
    units = []
    for s in units_read:
        need(s.get('snapshot_id') == snap and s.get('status') == 'occupied', 'unit.current_snapshot')
        uid = 'native:' + snap + ':' + s['row'] + ':' + str(s['slot'])
        units.append(resolve(s.get('name'), s.get('star'), uid, s.get('cost'), s.get('position')))
    limitations = ['catalogue_power_is_uncalibrated_proxy_not_damage_or_win_probability',
                   'bond_equipment_investment_interactions_not_scored', 'experience_purchase_model_not_verified',
                   'refresh_distribution_not_verified']
    offers, shop_enabled = [], False
    terms = semantic.get('shop_terms') or {}
    if (state.get('page') == 'shop' and terms.get('snapshot_id') == snap and terms.get('checked') is True
            and terms.get('ordinary_one_star') is True and terms.get('base_prices_unchanged') is True):
        from currency_wars_shop_contract import purchase_slot
        shop_read = state.get('shop') or {}
        geometry = shop_read.get('page', {}).get('geometry') or {}
        frame_scores = geometry.get('frame_scores') or []
        need(shop_read.get('schema') == 'currency-wars-shop-observation/v1'
             and shop_read.get('input', {}).get('sha256') == snap
             and shop_read.get('input', {}).get('size') == [1920, 1080]
             and shop_read.get('input', {}).get('format') == 'PNG'
             and shop_read.get('page', {}).get('reliable_open_shop') is True
             and geometry.get('overlays') == [] and len(frame_scores) == 5
             and all(type(v) in (int, float) and .6 <= v <= 1 for v in frame_scores), 'shop.complete_geometry')
        for native_slot in range(1, 6):
            found = [s for s in shop_read.get('slots', []) if s.get('slot') == native_slot]
            need(len(found) == 1, 'shop.slot_unique')
            slot = purchase_slot(shop_read, native_slot, snap)
            if slot is not None:
                item = resolve(slot['name'], 1, 'shop:' + snap + ':' + str(native_slot), slot['cost'])
                offers.append({'slot': native_slot - 1, 'unit': item, 'price': slot['cost'], 'source': 'native_shop:' + snap})
            else:
                need(found[0].get('status') == 'empty' and shop_read.get('input', {}).get('sha256') == snap
                     and shop_read.get('page', {}).get('reliable_open_shop') is True, 'shop.unknown_is_not_empty')
                offers.append({'slot': native_slot - 1, 'unit': None, 'unavailable': True, 'source': 'native_shop:' + snap})
        shop_enabled = True
    else:
        limitations.append('current_shop_price_star_or_complete_read_not_verified')
    environment = semantic.get('environment') or {}
    modifiers = []
    if environment.get('checked') is True and environment.get('snapshot_id') == snap and isinstance(environment.get('affixes'), list):
        for item in environment['affixes']:
            name = item.get('name') if isinstance(item, dict) else item
            need(isinstance(name, str) and name.strip(), 'environment.affix_name')
            modifiers.append({'name': name, 'source': 'current_environment:' + snap, 'status': 'web_known_unmodeled'})
    else:
        modifiers.append({'name': 'current_environment_not_fully_read', 'source': 'current_state:' + snap, 'status': 'unknown'})
    reserve = number(policy.get('reserve', 0), 'policy.reserve', True, 0, coins)
    purchase_budget = number(policy.get('purchase_budget', coins - reserve), 'policy.purchase_budget', True, 0, coins)
    data = {'schema': 'currency-wars-solver/v1', 'binding': copy.deepcopy(binding), 'source': 'authenticated_mcp_snapshot:' + snap,
            'preconditions': {'rewards_cleared': True, 'sell_phase_cleared': True}, 'coins': coins, 'reserve': reserve,
            'budgets': {'purchase': purchase_budget if shop_enabled else 0, 'experience': 0, 'refresh': 0},
            'bounds': {'max_xp_purchases': 0, 'max_refresh_prefixes': 0, 'max_refresh_samples': 0},
            'capacity': {'front': 4, 'back': 6, 'bench': 9, 'source': 'complete_native_slots:' + snap},
            'xp': {'enabled': False, 'level': level, 'current': xp[0], 'horizon_level': level,
                   'thresholds': {}, 'population_by_level': {str(level): maximum}, 'source': 'native_player_hud:' + snap},
            'units': units, 'shop_enabled': shop_enabled, 'shop': offers,
            'refresh': {'enabled': False, 'source': 'no_verified_current_distribution'},
            'modifiers': modifiers, 'scoring': {'source': 'published_GridFightRoleStar_power_proxy;policy_weights_are_heuristics',
                                             'gold_weight': gold_weight, 'xp_weight': 0, 'units': scoring}}
    if 'bond_model' in policy:
        data['scoring']['bond_model'] = copy.deepcopy(policy['bond_model'])
        limitations.append('explicit_policy_bond_utilities_not_calibrated_combat_or_verified_current_modifiers')
    catalogue._refresh()
    need(catalogue.digest == catalogue_digest, 'catalogue.changed_during_decision')
    return data, {'database_sha256': catalogue.digest, 'metadata': copy.deepcopy(catalogue.metadata),
                  'scopes': {'deployment': True, 'shop_purchase': shop_enabled, 'experience': False, 'refresh': False},
                  'limitations': limitations, 'resolved_characters': [
                      {'name': r['name'], 'cost': r['cost'], 'entity_id': r['id'], 'source': r['data'].get('source')}
                      for r in selected.values()]}


def propose(state, catalogue=None, policy=None, now=None):
    start = time.perf_counter()
    result = {'schema': SCHEMA, 'status': 'needs_review', 'binding': state.get('binding') if isinstance(state, dict) else None,
              'input_sent': False, 'effects_verified': False, 'auto_dispatch_allowed': False,
              'model_api_calls': 0, 'candidates': [], 'unknown': []}
    try:
        inputs, context = prepare(state, catalogue or Catalogue(), policy, now)
        proposal = solve(inputs)
        result.update(status=proposal['status'], candidates=proposal['candidates'], unknown=proposal['unknown'],
                      catalogue=context, solver_input=inputs, solver=proposal)
    except (NeedsReview, OSError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError, sqlite3.Error) as exc:
        result['unknown'] = [str(exc)]
    result['runtime_ms'] = round((time.perf_counter() - start) * 1000, 3)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('state', type=Path)
    p.add_argument('--database', type=Path, default=DEFAULT_DATABASE)
    p.add_argument('--policy', type=Path)
    args = p.parse_args()
    try:
        need(args.state.stat().st_size <= 2_000_000, 'state.size')
        state = json.loads(args.state.read_text(encoding='utf-8-sig'))
        policy = json.loads(args.policy.read_text(encoding='utf-8-sig')) if args.policy else None
        result = propose(state, Catalogue(args.database), policy)
        output = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (OSError, ValueError, ArithmeticError) as exc:
        output = json.dumps({'schema': SCHEMA, 'status': 'needs_review', 'input_sent': False,
                             'candidates': [], 'unknown': [str(exc)]}, allow_nan=False)
    print(output)


if __name__ == '__main__':
    main()
