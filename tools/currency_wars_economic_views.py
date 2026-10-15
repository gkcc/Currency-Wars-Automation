"""Pure two-view comparison; original native snapshots are never relabelled.

The worker must authenticate source PNGs, terminal receipts and every intervening
request before calling compose_view. This module does not authorize input.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import re

SCHEMA = 'currency-wars-comparison-view/v1'


def _time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('view_time_missing_timezone')
    return result


def _slots(observation):
    team = observation.get('semantic', {}).get('team') or {}
    slots = team.get('slots')
    keys = {('board', 'front', n) for n in range(1, 5)} | {
        ('board', 'back', n) for n in range(1, 7)} | {('bench', 'bench', n) for n in range(1, 10)}
    if (team.get('snapshot_id') != observation.get('snapshot_id') or not isinstance(slots, list)
            or len(slots) != 19 or any(not isinstance(s, dict) for s in slots)
            or {tuple(s.get(k) for k in ('location', 'row', 'slot')) for s in slots} != keys
            or any(type(s.get('slot')) is not int for s in slots)
            or any(s.get('snapshot_id') != observation['snapshot_id'] for s in slots)):
        raise ValueError('view_native_slots_incomplete_or_wrong_scope')
    return slots


def _wallet(observation):
    coins = observation.get('semantic', {}).get('coins') or {}
    if (coins.get('snapshot_id') != observation.get('snapshot_id')
            or type(coins.get('value')) is not int or coins['value'] < 0
            or coins.get('bounds') != [1613, 892, 1687, 950]
            or type(coins.get('confidence')) not in (int, float)
            or not .90 <= coins['confidence'] <= 1.):
        raise ValueError('view_native_wallet_missing')
    return coins['value']


def _player(observation):
    hud = observation.get('semantic', {}).get('player_hud') or {}
    xp, level = hud.get('xp'), hud.get('level')
    if (hud.get('snapshot_id') != observation.get('snapshot_id')
            or hud.get('origin') != 'native_player_hud' or hud.get('actor') != 'player'
            or type(level) is not int or not 1 <= level <= 10
            or not isinstance(xp, list) or len(xp) != 2
            or any(type(n) is not int for n in xp) or not 0 <= xp[0] < xp[1]):
        raise ValueError('view_native_player_HUD_missing')
    return level, xp


def _bench(slots):
    return sorted((s.get('slot'), s.get('status'), s.get('name'), s.get('star'), s.get('position'))
                  for s in slots if s.get('location') == 'bench')


def compose_view(roster, current, binding, scope, navigation_receipt_ids, *, now=None):
    """Retain the closed shop roster behind a separately proven navigation fence."""
    now = now or datetime.now(timezone.utc)
    if (roster.get('page') != 'preparation' or current.get('page') != 'shop'
            or current.get('snapshot_id') != binding.get('snapshot_id')
            or scope.get('match_id') != binding.get('match_id')
            or scope.get('resume_epoch') != binding.get('resume_epoch')
            or not all(binding.get(k) for k in ('request_id', 'snapshot_id', 'match_id'))
            or not (type(binding.get('resume_epoch')) is int and binding['resume_epoch'] >= 0
                    or isinstance(binding.get('resume_epoch'), str) and bool(binding['resume_epoch']))
            or any(o.get('read_contract', {}).get('effective_scope') != 'full' for o in (roster, current))
            or any(o.get('fields', {}).get('stage') != scope.get('stage') for o in (roster, current))):
        raise ValueError('view_pair_scope_or_full_read_invalid')
    if (not isinstance(navigation_receipt_ids, list) or not navigation_receipt_ids
            or len(navigation_receipt_ids) > 32
            or any(not isinstance(rid, str) or not 1 <= len(rid) <= 100 for rid in navigation_receipt_ids)
            or len(set(navigation_receipt_ids)) != len(navigation_receipt_ids)):
        raise ValueError('view_navigation_fence_missing_or_unbounded')
    old_time, new_time = _time(roster['captured_at']), _time(current['captured_at'])
    if not 0 <= (now-new_time).total_seconds() <= 60 or not 0 <= (new_time-old_time).total_seconds() <= 180:
        raise ValueError('view_pair_stale_or_reversed')
    old_slots, new_slots = _slots(roster), _slots(current)
    if (any(s.get('status') not in ('empty', 'occupied') for s in old_slots)
            or any(s.get('status') not in ('empty', 'occupied') for s in new_slots if s.get('location') == 'bench')
            or _bench(old_slots) != _bench(new_slots)
            or _wallet(roster) != _wallet(current) or _player(roster) != _player(current)):
        raise ValueError('view_pair_roster_wallet_or_player_changed')
    population = roster.get('fields', {}).get('deployed')
    match = re.fullmatch(r'(\d{1,2})/(\d{1,2})', population or '')
    if (not match or not 0 <= int(match[1]) <= int(match[2]) <= 10
            or sum(s['location'] == 'board' and s['status'] == 'occupied' for s in old_slots) != int(match[1])):
        raise ValueError('view_pair_population_inconsistent')
    if current.get('fields', {}).get('deployed') not in (None, population):
        raise ValueError('view_pair_current_population_conflict')
    def provenance(observation):
        required = ('snapshot_id', 'capture_request_id', 'frame_id', 'captured_at')
        if any(not observation.get(k) for k in required):
            raise ValueError('view_capture_provenance_missing')
        return {**{k: observation[k] for k in required}, **copy.deepcopy(scope)}
    return {'schema': SCHEMA, 'origin': 'authenticated_runner_view_pair',
        'binding': copy.deepcopy(binding), 'team': copy.deepcopy(roster['semantic']['team']),
        'population': {'value': population, 'snapshot_id': roster['snapshot_id'],
                       'source': 'receipt_bound_preparation_view'},
        'roster_provenance': provenance(roster), 'current_provenance': provenance(current),
        'navigation_receipt_ids': list(navigation_receipt_ids), 'raw_native_unchanged': True}


ROSTER_READ_SCHEMA = 'currency-wars-supervised-roster-read/v1'


def validate_roster_read(review, observed, binding):
    """Independent human READ only; no raw native fields or action rights change."""
    keys={'schema','source','request_id','snapshot_id','match_id','resume_epoch',
          'capture_request_id','frame_id','stage','resources','slots','findings'}
    if (not isinstance(review,dict) or set(review)!=keys or review['schema']!=ROSTER_READ_SCHEMA
            or review['source']!='supervising_agent'
            or any(review[k]!=binding.get(k) for k in ('request_id','snapshot_id','match_id','resume_epoch'))
            or any(review[k]!=observed.get(k) for k in ('snapshot_id','capture_request_id','frame_id'))
            or observed.get('page')!='preparation' or review['stage']!=observed.get('fields',{}).get('stage')
            or observed.get('read_contract',{}).get('effective_scope')!='full'
            or not isinstance(review['findings'],str) or not 1<=len(review['findings'].strip())<=2000):
        raise ValueError('roster_read_original_scope_or_source_invalid')
    level,xp=_player(observed);population=observed.get('fields',{}).get('deployed')
    if review['resources']!={'coins':_wallet(observed),'level':level,'xp':xp,'population':population}:
        raise ValueError('roster_read_resources_conflict')
    slots=_slots(observed);rows=review['slots'];keys={'location','row','slot','status','name','star','trial','roi_sha256','unobstructed'}
    if (not isinstance(rows,list) or len(rows)!=19 or any(not isinstance(row,dict) or set(row)!=keys for row in rows)
            or len({tuple(row[k] for k in ('location','row','slot')) for row in rows})!=19):
        raise ValueError('roster_read_requires_exact_19_distinct_slots')
    result={};native={tuple(slot[k] for k in ('location','row','slot')):slot for slot in slots}
    for row in rows:
        address=tuple(row[k] for k in ('location','row','slot'));slot=native.get(address)
        if (slot is None or type(row['slot']) is not int or row['status']!=slot.get('status')
                or row['status'] not in ('occupied','empty') or row['unobstructed'] is not True
                or not re.fullmatch('[0-9a-f]{64}',str(row['roi_sha256']))
                or slot.get('origin')!='native_visual_state_reader'):
            raise ValueError('roster_read_native_occupancy_or_geometry_conflict')
        name,star=row['name'],row['star']
        if row['status']=='empty':
            if any(row[k] is not None for k in ('name','star','trial')):raise ValueError('roster_read_empty_not_named')
        elif (name is None)!=(star is None) or (name is not None and
                (not isinstance(name,str) or not 1<=len(name.strip())<=64 or type(star) is not int or not 1<=star<=4)):
            raise ValueError('roster_read_name_star_incomplete')
        if row['trial'] is not None and type(row['trial']) is not bool:raise ValueError('roster_read_trial_invalid')
        if any(slot.get(k) is not None and slot[k]!=row[k] for k in ('name','star')):
            raise ValueError('roster_read_conflicts_with_known_native_identity')
        result[address]=copy.deepcopy(row)
    pop=re.fullmatch(r'(\d+)/(\d+)',population or '')
    if not pop or sum(slot['location']=='board' and slot['status']=='occupied' for slot in slots)!=int(pop[1]):
        raise ValueError('roster_read_population_conflict')
    return result


def roster_read_projection(review, source, binding, scope):
    validate_roster_read(review,source,{k:review[k] for k in ('request_id','snapshot_id','match_id','resume_epoch')})
    if any(review[k]!=scope.get(k) for k in ('stage','match_id','resume_epoch')):
        raise ValueError('roster_read_projection_scope_changed')
    return {'schema':ROSTER_READ_SCHEMA,'source':'supervising_agent',
        'binding':copy.deepcopy(binding),'scope':copy.deepcopy(scope),'review':copy.deepcopy(review),
        'source_observation':{**{k:copy.deepcopy(source[k]) for k in
            ('snapshot_id','capture_request_id','frame_id','captured_at','page','fields','read_contract')},
            'semantic':{k:copy.deepcopy(source['semantic'][k]) for k in ('team','coins','player_hud')}},
        'usage':'comparison_scoring_only','native_unchanged':True,'execution_authority':False}



def continued_roster_resources(projection):
    """Check the public READ continuation envelope; Worker authenticates receipts."""
    source=projection['review']['resources'];continuation=projection.get('resource_continuation')
    if continuation is None:return source
    from currency_wars_economy import classify_effect
    effects=continuation.get('effects')
    if (continuation.get('source')!='verified_XP_effects_and_fresh_roster'
            or not isinstance(effects,list) or not 1<=len(effects)<=32
            or any(not isinstance(effect,dict) for effect in effects)
            or len({e.get('request_id') for e in effects})!=len(effects)):
        raise ValueError('roster_read_XP_continuation_invalid')
    previous={k:source[k] for k in ('coins','level','xp')}
    for effect in effects:
        if (not isinstance(effect.get('after'),dict) or type(effect.get('cost')) is not int
                or not isinstance(effect.get('model_values'),dict)
                or effect.get('before')!=previous or not re.fullmatch('[0-9a-f]{64}',str(effect.get('receipt_sha256')))
                or effect.get('actions')!=[{'type':'key','args':[70]},{'type':'wait','args':[0.7]}]
                or classify_effect('experience',{'values':effect['before']},{'values':effect['after']},
                    expected_cost=effect.get('cost'),model_values=effect.get('model_values'))['outcome']!='success'):
            raise ValueError('roster_read_XP_resource_chain_conflict')
        previous=effect['after']
    current=continuation['current_resources']
    if any(current.get(k)!=previous[k] for k in ('coins','level','xp')):
        raise ValueError('roster_read_XP_current_resources_conflict')
    old=re.fullmatch(r'(\d+)/(\d+)',source['population']);new=re.fullmatch(r'(\d+)/(\d+)',current.get('population',''))
    if not old or not new or old[1]!=new[1] or not 0<=int(new[2])-int(old[2])<=previous['level']-source['level']:
        raise ValueError('roster_read_XP_population_conflict')
    return current
