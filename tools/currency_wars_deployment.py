"""Pure deployment proposals and observable effects; no controller or input.

Selection comes from the local adviser. Slot geometry comes from native state,
not an assistant's coordinates. Proposals remain non-executable until the
original owner/epoch/receipt guards accept them.
"""
from __future__ import annotations

import copy
import hashlib
import re

BINDING_KEYS = ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')


def _population(observation):
    match = re.fullmatch(r'(\d+)/(\d+)', str(observation.get('fields', {}).get('deployed') or ''))
    if not match or not 0 <= int(match[1]) <= int(match[2]) <= 10 or int(match[2]) == 0:
        raise ValueError('current_population_unknown')
    return tuple(map(int, match.groups()))


def _slots(observation, *, reviewed_destination=None):
    snapshot = observation.get('snapshot_id')
    team = observation.get('semantic', {}).get('team', {})
    slots = team.get('slots', [])
    expected = {('board', row, slot) for row, size in [('front', 4), ('back', 6)]
                for slot in range(1, size + 1)} | {('bench', 'bench', slot) for slot in range(1, 10)}
    result = {}
    if observation.get('page') != 'preparation' or team.get('snapshot_id') != snapshot:
        raise ValueError('native_preparation_team_unbound')
    for slot in slots:
        key = tuple(slot.get(k) for k in ('location', 'row', 'slot'))
        if key not in expected or key in result or slot.get('snapshot_id') != snapshot:
            raise ValueError('native_slot_set_unbound_or_duplicate')
        if slot.get('status') not in ('empty', 'occupied'):
            if slot.get('status') != 'unknown' or key != reviewed_destination:
                raise ValueError('slot_occupancy_unknown')
            result[key] = {**slot, 'status': 'occupied', 'occupancy_origin': 'supervising_agent'}
        else:
            result[key] = slot
    if set(result) != expected:
        raise ValueError('native_slot_set_incomplete')
    return result


def _identity(slot):
    return tuple(slot.get(k) for k in ('status', 'name', 'star', 'position'))


def _compatible_slot(old, new):
    return (old['status'] == new['status'] and all(old.get(key) is None or new.get(key) is None
            or old[key] == new[key] for key in ('name', 'star', 'position')))


def _occupied_resources(observation, *, read_review=None, reviewed_destination=None):
    """Full native occupancy and player resources; no named-team assertion."""
    from currency_wars_state_reader import native_slots
    slots = _slots(observation, reviewed_destination=reviewed_destination)
    definitions = {(s['location'], s['row'], s['slot']): s for s in native_slots()}
    contract = observation.get('read_contract', {})
    if (contract.get('version') != 2 or contract.get('effective_scope') != 'full'
            or contract.get('unread') != []
            or any(s.get('origin') != 'native_visual_state_reader'
                   or s.get('bounds') != definitions[key]['bounds']
                   or s.get('point') != definitions[key]['point'] for key, s in slots.items())):
        raise ValueError('occupied_deployment_requires_full_native_slot_geometry')
    population = _population(observation)
    if population[0] != sum(s['status'] == 'occupied' for k, s in slots.items() if k[0] == 'board'):
        raise ValueError('native_population_conflicts_with_board')
    hud = observation.get('semantic', {}).get('player_hud') or {}
    coins = observation.get('semantic', {}).get('coins') or {}
    if (hud.get('snapshot_id') != observation.get('snapshot_id')
            or hud.get('origin') != 'native_player_hud' or hud.get('actor') != 'player'
            or type(hud.get('level')) is not int or not 1 <= hud['level'] <= 10 or str(hud['level']) != str(observation['fields'].get('level'))
            or not isinstance(hud.get('xp'), list) or len(hud['xp']) != 2
            or any(type(n) is not int or n < 0 for n in hud['xp']) or hud['xp'][0] > hud['xp'][1]
            or read_review is None and (coins.get('snapshot_id') != observation.get('snapshot_id')
                or type(coins.get('value')) is not int or coins['value'] < 0
                or not .90 <= coins.get('confidence', 0) <= 1.
                or coins.get('bounds') != [1613, 892, 1687, 950])):
        raise ValueError('occupied_deployment_native_resources_unbound')
    return slots, population, (hud['level'], hud['xp'],
                               coins['value'] if read_review is None else read_review['coins'])


def validate_occupied_deployment_review(action, request):
    """Explicit current supervisor exposure READ; never a native identity."""
    review = action.get('source_card_review')
    keys = {'source', 'request_id', 'snapshot_id', 'match_id', 'resume_epoch',
            'capture_request_id', 'frame_id', 'row', 'slot', 'bounds', 'unobstructed', 'findings'}
    if (not isinstance(review, dict) or set(review) != keys
            or review.get('source') != 'supervising_agent' or review.get('unobstructed') is not True
            or review.get('row') != 'bench' or type(review.get('slot')) is not int
            or review.get('slot') != action.get('unit', {}).get('slot')
            or not isinstance(review.get('bounds'), list) or len(review['bounds']) != 4
            or any(type(v) is not int for v in review['bounds'])
            or not isinstance(review.get('findings'), str) or not 1 <= len(review['findings'].strip()) <= 1000
            or any(not isinstance(review.get(k), str) or not review[k] for k in BINDING_KEYS
                   + ('capture_request_id', 'frame_id'))):
        raise ValueError('current_supervised_source_card_exposure_required')
    if request is not None:
        if (any(review[k] != request.get(k) for k in BINDING_KEYS)
                or any(review[k] != request.get('observation', {}).get(k)
                       for k in ('snapshot_id', 'capture_request_id', 'frame_id'))):
            raise ValueError('supervised_source_card_exposure_not_current_request')
    return review


def occupied_deployment_facts(observation, action):
    slots, population, resources = _occupied_resources(observation)
    if (set(action) != {'type', 'reason', 'expected_page', 'unit', 'destination', 'source_card_review'}
            or action.get('type') != 'deploy_occupied_unit' or action.get('expected_page') != 'preparation'
            or not isinstance(action.get('reason'), str) or not 1 <= len(action['reason'].strip()) <= 1000
            or not isinstance(action.get('unit'), dict) or set(action['unit']) != {'slot'}
            or type(action['unit']['slot']) is not int or not 1 <= action['unit']['slot'] <= 9
            or not isinstance(action.get('destination'), dict) or set(action['destination']) != {'row', 'slot'}
            or action['destination'].get('row') not in ('front', 'back')
            or type(action['destination'].get('slot')) is not int):
        raise ValueError('occupied_deployment_action_shape_invalid')
    source = slots[('bench', 'bench', action['unit']['slot'])]
    target = slots.get(('board', action['destination']['row'], action['destination']['slot']))
    review = validate_occupied_deployment_review(action, None)
    if review['bounds'] != source['bounds']:
        raise ValueError('supervised_source_card_exposure_bounds_mismatch')
    if source['status'] != 'occupied' or not target or target['status'] != 'empty':
        raise ValueError('occupied_source_or_native_empty_destination_missing')
    if source.get('position') not in {'front': ('前台', '前后台'), 'back': ('后台', '前后台')}[action['destination']['row']]:
        raise ValueError('source_native_role_unknown_or_illegal')
    if population[0] >= population[1]:
        raise ValueError('population_full')
    return dict(source=copy.deepcopy(source), destination=copy.deepcopy(target),
                population=list(population), stage=observation['fields'].get('stage'), coins=resources[2])


def check_occupied_deployment(action, request, actual, current_png):
    """Reuse the audited current-card exposure and byte-continuity contract.

    The constructed click intent is passed only to the pure image checker; it
    is never published. The real executor computes one drag from native slots.
    """
    import cv2
    from currency_wars_unit_inspection import check_target, ANCHORS
    from currency_wars_slot_probe import SlotProbe
    from currency_wars_unit_inspection import _image
    validate_occupied_deployment_review(action, request)
    facts = same_deployment_preconditions(request['observation'], actual, action)
    source, target = facts['source'], facts['destination']
    exposure = check_target(dict(type='click_point', args=source['point'], expected_page='preparation',
        guard_texts=[a[0] for a in ANCHORS], reason=action['reason'], target_evidence=dict(
            control_id='owned_unit_details', snapshot_id=request['snapshot_id'], row='bench',
            slot=source['slot'], bounds=source['bounds'])), request, actual, current_png)
    probe = SlotProbe()
    if not probe.reader._load():
        raise ValueError('occupied_deployment_native_resources_missing')
    for observed, path in ((request['observation'], request['original_png']), (actual, current_png)):
        rgb = _image(path, observed['snapshot_id'])
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype('float32')
        overlays = probe.reader._overlays(rgb)
        src = probe._slot(rgb, gray, source, True, overlays)
        dst = probe._slot(rgb, gray, target, True, overlays)
        if (src['status'] != 'occupied' or src.get('position') != source['position']
                or dst['status'] != 'empty'):
            raise ValueError('current_card_role_or_empty_target_pixels_unproved')
    return dict(source_card=exposure, native_destination=copy.deepcopy(target),
                native_identity_inferred=False, input_sent=False)


def deployment_facts(observation, action):
    """Current named bench unit, legal empty destination and bounded capacity."""
    if isinstance(action, dict) and action.get('type') == 'deploy_occupied_unit':
        return occupied_deployment_facts(observation, action)
    slots = _slots(observation)
    deployed, maximum = _population(observation)
    if deployed != sum(s['status'] == 'occupied' for k, s in slots.items() if k[0] == 'board'):
        raise ValueError('native_population_conflicts_with_board')
    if deployed >= maximum:
        raise ValueError('population_full')
    if (not isinstance(action, dict) or set(action) != {'type', 'reason', 'expected_page', 'unit', 'destination'}
            or action.get('type') != 'deploy_unit' or action.get('expected_page') != 'preparation'
            or not isinstance(action.get('reason'), str) or not 1 <= len(action['reason']) <= 1000):
        raise ValueError('deployment_action_shape_invalid')
    unit, destination = action['unit'], action['destination']
    if (not isinstance(unit, dict) or set(unit) != {'slot', 'name', 'star', 'position'}
            or type(unit.get('slot')) is not int or not 1 <= unit['slot'] <= 9
            or not isinstance(destination, dict) or set(destination) != {'row', 'slot'}
            or type(destination.get('slot')) is not int):
        raise ValueError('deployment_identity_or_destination_invalid')
    source = slots[('bench', 'bench', unit['slot'])]
    target = slots.get(('board', destination['row'], destination['slot']))
    if (source['status'] != 'occupied' or any(source.get(k) != unit[k] for k in ('name', 'star', 'position'))
            or not isinstance(source.get('name'), str) or not source['name']
            or type(source.get('star')) is not int or not 1 <= source['star'] <= 5
            or not target or target['status'] != 'empty'):
        raise ValueError('current_source_or_empty_destination_not_proved')
    if source['position'] not in {'front': ('前台', '前后台'), 'back': ('后台', '前后台')}.get(destination['row'], ()):
        raise ValueError('unit_position_illegal')
    native = source.get('evidence', {}).get('native_unit_bank', {})
    if (not native.get('bank_sha256') or native.get('portrait', {}).get('matches') is not True
            or native.get('star', {}).get('matches') is not True
            or native.get('position', {}).get('matches') is not True):
        raise ValueError('current_source_identity_not_native_verified')
    coins = observation.get('semantic', {}).get('coins', {})
    if (coins.get('snapshot_id') != observation.get('snapshot_id')
            or type(coins.get('value')) is not int or coins['value'] < 0
            or not .90 <= coins.get('confidence', 0) <= 1.
            or coins.get('bounds') != [1613, 892, 1687, 950]):
        raise ValueError('native_wallet_not_bound')
    return {'source': copy.deepcopy(source), 'destination': copy.deepcopy(target),
            'population': [deployed, maximum], 'stage': observation['fields'].get('stage'),
            'coins': coins['value']}


def same_deployment_preconditions(before, fresh, action):
    """Compare relevant current facts, not animated decorative text boxes."""
    if action.get('type') == 'deploy_occupied_unit':
        a, b = deployment_facts(before, action), deployment_facts(fresh, action)
        old, unused_pop, old_resources = _occupied_resources(before)
        new, unused_pop, new_resources = _occupied_resources(fresh)
        if (a['population'] != b['population'] or a['stage'] != b['stage']
                or old_resources != new_resources or a['source']['position'] != b['source']['position']
                or any(not _compatible_slot(old[k], new[k]) for k in old)):
            raise ValueError('current_occupied_deployment_facts_changed')
        return b
    a, b = deployment_facts(before, action), deployment_facts(fresh, action)
    old, new = _slots(before), _slots(fresh)
    if (a['population'] != b['population'] or a['stage'] != b['stage'] or a['coins'] != b['coins']
            or before['fields'].get('level') != fresh['fields'].get('level')
            or any(_identity(old[k]) != _identity(new[k]) for k in old)):
        raise ValueError('current_deployment_facts_changed')
    return b


def propose_deployment(state, advice):
    result = {'schema': 'currency-wars-deployment-proposal/v1', 'status': 'blocked',
              'input_sent': False, 'execution_eligible': False, 'actions': [],
              'scope': 'One local-adviser selected named bench unit; original executor must independently accept.'}
    try:
        binding = state.get('binding', {})
        if (any(not binding.get(k) for k in BINDING_KEYS)
                or advice.get('source', {}).get('binding') != binding):
            raise ValueError('advice_and_state_binding_mismatch')
        if state.get('status') != 'observed' or state.get('source') != 'authenticated_runner_status':
            raise ValueError('current_authenticated_state_required')
        if (state.get('control_mode') != 'waiting_decision'
                or state.get('decision', {}).get('kind') != 'preparation_strategy'
                or state.get('preparation_checklist', {}).get('phase') != 'lineup_equipment'):
            raise ValueError('current_deployment_phase_required')
        economy_steps = [step for step in state.get('preparation_checklist', {}).get('steps', [])
                         if isinstance(step, dict) and step.get('id') == 'economy']
        if len(economy_steps) != 1 or economy_steps[0].get('completed') is not True:
            raise ValueError('completed_joint_economy_required_before_deployment')
        observation = {'snapshot_id': state['snapshot_id'], 'page': state['page'],
                       'fields': state['native_fields'], 'semantic': state['semantic']}
        slots = _slots(observation)
        _, capacity = _population(observation)
        joint = advice.get('joint_economy', {})
        # The adviser reserves unread occupied board units without scoring or
        # replacing them. Its capacity key counts only the scored component.
        reserved = joint.get('reserved_unscored_board_places', {'front': 0, 'back': 0})
        if (set(reserved) != {'front', 'back'}
                or any(type(n) is not int or n < 0 for n in reserved.values())
                or any(reserved[row] > sum(k[0] == 'board' and k[1] == row and s['status'] == 'occupied'
                       for k, s in slots.items()) for row in reserved)):
            raise ValueError('unscored_occupied_reservation_invalid')
        usable_capacity = capacity - sum(reserved.values())
        winners = [w for w in joint.get('best_by_conditional_capacity', [])
                   if w.get('usable_places_assumption') == usable_capacity]
        if len(winners) != 1:
            raise ValueError('capacity_matched_local_winner_missing')
        portfolios = [p for p in joint.get('portfolios', []) if p.get('id') == winners[0]['preferred_candidate']]
        if len(portfolios) != 1 or portfolios[0].get('purchase_refs'):
            raise ValueError('purchase_plan_must_complete_before_deployment')
        layouts = [l for l in portfolios[0].get('conditional_layouts', [])
                   if l.get('usable_places_assumption') == usable_capacity]
        if len(layouts) != 1:
            raise ValueError('local_layout_missing')
        entities = {e['entity_ref']: e['observed'] for e in advice.get('observed_entities', [])}
        for placement in layouts[0]['minimum_proxy_layout']:
            unit = entities.get(placement['entity_ref'])
            if not unit:
                raise ValueError('layout_entity_missing')
            if unit.get('location') == 'board':
                if unit.get('row') != placement['zone']:
                    raise ValueError('board_rearrangement_not_supported')
                continue
            empty = sorted((s for k, s in slots.items() if k[0] == 'board'
                            and k[1] == placement['zone'] and s['status'] == 'empty'), key=lambda s: s['slot'])
            if not empty:
                raise ValueError('legal_empty_destination_missing')
            action = {'type': 'deploy_unit', 'expected_page': 'preparation',
                      'unit': {k: unit[k] for k in ('slot', 'name', 'star', 'position')},
                      'destination': {'row': placement['zone'], 'slot': empty[0]['slot']},
                      'reason': 'Local bond-aware adviser selected this unit/zone; native state selected the first legal empty slot.'}
            deployment_facts(observation, action)
            result.update(status='proposed', binding=copy.deepcopy(binding), actions=[action],
                          selected_portfolio=portfolios[0]['id'], selected_entity_ref=placement['entity_ref'],
                          solver_search=copy.deepcopy(portfolios[0].get('deployment_search')))
            return result
        result.update(status='no_deployment_needed', binding=copy.deepcopy(binding))
    except (KeyError, TypeError, ValueError) as exc:
        result['reason'] = str(exc)
    return result


def verify_deployment_effect(before, after, action, *, read_review=None):
    """A causal transition, distinct from native OCR of a deployed identity.

    Caller must authenticate the one original input receipt and independent
    after capture and prove there was no intervening input. This function alone
    cannot authorize input or create a native visual identity claim.
    """
    try:
        facts = deployment_facts(before, action)
        source = ('bench', 'bench', action['unit']['slot'])
        destination = ('board', action['destination']['row'], action['destination']['slot'])
        reviewed = read_review is not None
        if reviewed:
            if (not isinstance(read_review, dict) or set(read_review) != {
                    'source', 'original_request_id', 'input_request_id', 'snapshot_id',
                    'capture_request_id', 'frame_id', 'destination', 'coins'}
                    or read_review.get('source') != 'supervising_agent'
                    or any(read_review.get(key) != after.get(key) for key in
                           ('snapshot_id', 'capture_request_id', 'frame_id'))
                    or read_review.get('destination') != {**action['destination'], 'status': 'occupied'}
                    or type(read_review.get('coins')) is not int or read_review['coins'] < 0
                    or after.get('semantic', {}).get('coins') is not None):
                raise ValueError('supervised_effect_read_binding_or_native_wallet_conflict')
            target = [slot for slot in after.get('semantic', {}).get('team', {}).get('slots', [])
                      if tuple(slot.get(k) for k in ('location', 'row', 'slot')) == destination]
            if len(target) != 1 or target[0].get('status') != 'unknown':
                raise ValueError('supervised_destination_must_be_original_native_unknown')
        old = _slots(before)
        new = _slots(after, reviewed_destination=destination if reviewed else None)
        after_coins = read_review['coins'] if reviewed else (after.get('semantic', {}).get('coins') or {}).get('value')
        if (not before.get('capture_request_id') or not after.get('capture_request_id')
                or not before.get('frame_id') or not after.get('frame_id')
                or before['capture_request_id'] == after['capture_request_id']
                or before.get('frame_id') == after.get('frame_id')
                or after['fields'].get('stage') != facts['stage']
                or after['fields'].get('level') != before['fields'].get('level')
                or before.get('semantic', {}).get('player_hud', {}).get('xp')
                    != after.get('semantic', {}).get('player_hud', {}).get('xp')
                or _population(after) != (facts['population'][0] + 1, facts['population'][1])
                or new[source]['status'] != 'empty' or new[destination]['status'] != 'occupied'
                or after_coins != facts['coins']
                or facts['coins'] is None
                or any((not _compatible_slot(old[k], new[k]) if action.get('type') == 'deploy_occupied_unit'
                        else _identity(old[k]) != _identity(new[k]))
                       for k in old if k not in (source, destination))):
            raise ValueError('deployment_observable_effect_not_complete')
        wallet = after.get('semantic', {}).get('coins') or {}
        if not reviewed and (wallet.get('snapshot_id') != after.get('snapshot_id')
                or not .90 <= wallet.get('confidence', 0) <= 1.
                or wallet.get('bounds') != [1613, 892, 1687, 950]):
            raise ValueError('after_wallet_not_native_bound')
        if action.get('type') == 'deploy_occupied_unit':
            unused_slots, unused_population, after_resources = _occupied_resources(
                after, read_review=read_review, reviewed_destination=destination if reviewed else None)
            unused_slots, unused_population, old_resources = _occupied_resources(before)
            if after_resources != old_resources:
                raise ValueError('deployment_observable_resources_changed')
            for key in ('name', 'star', 'position'):
                if (facts['source'].get(key) is not None and new[destination].get(key) is not None
                        and facts['source'][key] != new[destination][key]):
                    raise ValueError('destination_native_identity_conflicts')
            return {'outcome': 'observed_transition', 'native_destination_identity_verified': False,
                    'causal_identity': None, 'causal_card': {'location': 'bench', 'row': 'bench',
                        'slot': action['unit']['slot'], 'source_snapshot_id': before['snapshot_id']},
                    'origin': 'single_deployment_transition_requires_receipt_fence',
                    'destination': copy.deepcopy(action['destination']),
                    'effect_read_origin': 'supervising_agent' if reviewed else 'current_native_observation',
                    'native_observation_rewritten': False, 'source_exposure_origin': 'supervising_agent'}
        for key in ('name', 'star', 'position'):
            if new[destination].get(key) not in (None, action['unit'][key]):
                raise ValueError('destination_native_identity_conflicts')
        return {'outcome': 'observed_transition', 'native_destination_identity_verified':
                all(new[destination].get(k) == action['unit'][k] for k in ('name', 'star', 'position')),
                'causal_identity': copy.deepcopy(action['unit']), 'origin': 'single_deployment_transition_requires_receipt_fence',
                'destination': copy.deepcopy(action['destination']),
                'effect_read_origin': 'supervising_agent' if reviewed else 'current_native_observation',
                'native_observation_rewritten': False}
    except (KeyError, TypeError, ValueError) as exc:
        return {'outcome': 'unknown', 'reason': str(exc), 'retry_allowed': False}


def verify_archived_deployment(pending, before_png, after, after_png, receipts, origin, *, read_review=None):
    """Verify the ORIGINAL stopped-run receipts and saved independent after PNG.

    Input-free and write-free. Caller owns archive path/origin/CAS validation and
    must retain this proof when closing a durable pending record. No new request,
    actor, fresh-game identity, native card name, or retry is manufactured.
    """
    result = {'schema': 'currency-wars-deployment-archive-verification/v1', 'status': 'unknown',
              'input_sent': False, 'retry_allowed': False, 'request_id': pending.get('request_id')}
    try:
        before, rid = pending['before'], pending['input_request_id']
        prior = set(pending['prior_receipt_ids'])
        if (pending.get('publication_attempted') is not True or pending.get('outcome') != 'pending'
                or not prior <= receipts.keys() or before['capture_request_id'] not in prior
                or not isinstance(origin.get('run_id'), str) or not origin.get('chat_id')
                or any(receipt.get('origin') != origin or receipt.get('id') != key
                       or receipt.get('request', {}).get('id') != key
                       or receipt['request'].get('chat_id') != origin['chat_id']
                       for key, receipt in receipts.items())
                or hashlib.sha256(before_png).hexdigest() != before['snapshot_id']
                or hashlib.sha256(after_png).hexdigest() != after['snapshot_id']):
            raise ValueError('original_archive_scope_or_PNG_hash_invalid')
        for observation in (before, after):
            item = receipts[observation['capture_request_id']]
            meta = item.get('result', {}).get('observation') or {}
            if (meta.get('request_id') != observation['capture_request_id']
                    or any(meta.get(a) != observation[b] for a, b in (
                        ('snapshot_sha256', 'snapshot_id'), ('frame_id', 'frame_id'), ('captured_at', 'captured_at')))):
                raise ValueError('original_capture_receipt_and_native_frame_mismatch')
        if pending['action'].get('type') == 'deploy_occupied_unit':
            review = validate_occupied_deployment_review(pending['action'], None)
            source_receipt = receipts.get(review['capture_request_id'], {})
            meta = source_receipt.get('result', {}).get('observation') or {}
            if (review['request_id'] != pending['request_id'] or review['match_id'] != pending['match_id']
                    or review['resume_epoch'] != pending['resume_epoch']
                    or review['capture_request_id'] not in prior
                    or meta.get('snapshot_sha256') != review['snapshot_id']
                    or meta.get('frame_id') != review['frame_id']):
                raise ValueError('original_supervised_source_read_receipt_unbound')
        unit, destination = pending['action']['unit'], pending['action']['destination']
        old = _slots(before)
        src, dst = old[('bench', 'bench', unit['slot'])], old[('board', destination['row'], destination['slot'])]
        expected = [{'type': 'drag', 'args': [*src['point'], *dst['point']]},
                    {'type': 'wait', 'args': [.7]}]
        remaining = set(receipts) - prior
        if not {rid, after['capture_request_id']} <= remaining or rid == after['capture_request_id']:
            raise ValueError('original_input_or_independent_after_watermark_missing')
        for key in remaining:
            item, expected_actions = receipts[key], expected if key == rid else [{'type': 'observe', 'args': []}]
            request, response = item['request'], item.get('result') or {}
            if (request.get('kind') != 'actions' or request.get('handoff') is not False
                    or request.get('actions') != expected_actions or response.get('id') != key
                    or response.get('ok') is not True or response.get('completed') != expected_actions
                    or response.get('input_attempted') is not (key == rid)
                    or response.get('attempted_actions') != (expected[:1] if key == rid else [])):
                raise ValueError('original_archive_has_other_or_unknown_input')
        if read_review is not None and (not isinstance(read_review, dict)
                or read_review.get('original_request_id') != pending['request_id']
                or read_review.get('input_request_id') != rid):
            raise ValueError('supervised_effect_read_original_ID_mismatch')
        effect = verify_deployment_effect(before, after, pending['action'], read_review=read_review)
        if effect['outcome'] != 'observed_transition':
            raise ValueError(effect.get('reason', 'original_effect_unverified'))
        result.update(status='verified_original_effect', original_run_id=origin['run_id'],
            input_request_id=rid, original_after_capture_request_id=after['capture_request_id'],
            after_snapshot_id=after['snapshot_id'], effect=effect,
            receipt_ids_checked=sorted(remaining), native_identity_rewritten=False)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        result['reason'] = str(exc)
    return result
