"""Small, input-free coaching rules; cached observations never become live proof."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PHASES = ('rewards', 'startup_guide', 'inventory_cleanup', 'economy', 'lineup_equipment', 'battle_acceptance')
PHASE_LABELS = dict(zip(PHASES, ('领奖领空', '创业指南', '清库存', '经济', '上场/装备', '出战验收')))


def reviewed_goals(goals, snapshot_id):
    """Normalize an accepted panel read for advisory planning, preserving unknowns."""
    normalized = []
    for index, goal in enumerate(goals):
        structured = isinstance(goal, dict) and isinstance(goal.get('condition'), str) and bool(goal['condition'].strip())
        value = goal if structured else {}
        proof = value.get('proof')
        bound = proof is None or (isinstance(proof, dict) and proof.get('snapshot_id') == snapshot_id
                                and proof.get('source') in ('supervising_agent', 'observed_screen'))
        trusted = structured and bound
        normalized.append({'id': value.get('id') if isinstance(value.get('id'), str) and value['id'].strip() else 'unknown-goal-' + str(index + 1),
            'condition': value.get('condition') if structured else goal if isinstance(goal, str) and goal.strip() else '任务条件尚未实读',
            'condition_met': value.get('condition_met') if trusted and type(value.get('condition_met')) is bool else None,
            'reward_claimed': value.get('reward_claimed') if trusted and type(value.get('reward_claimed')) is bool else None,
            'progress': value.get('progress') if trusted and isinstance(value.get('progress'), (str, int)) else None,
            'target_bonds': [item for item in value.get('target_bonds', []) if isinstance(item, str)]
                if trusted and isinstance(value.get('target_bonds'), list) else [],
            'proof': {'source': 'supervising_agent', 'snapshot_id': snapshot_id}})
    return normalized


def load_knowledge(path):
    try:
        data = Path(path).read_bytes()
        value = json.loads(data.decode('utf-8-sig'))
        if value.get('schema') != 'currency-wars-observed-knowledge/v1':
            raise ValueError('unknown knowledge schema')
        # Exclude historical progress, selections, costs and battle outcomes.
        roles = {name: {key: facts[key] for key in ('position', 'deployment', 'base_cost', 'bonds') if key in facts}
                 for name, facts in {**value.get('roles_observed', {}), **value.get('characters', {})}.items()}
        investments = {name: {key: facts[key] for key in ('effect', 'rarity', 'gifts') if key in facts}
                       for name, facts in value.get('investments', {}).items()}
        guide = value.get('applied_guide', value.get('guide', {}))
        progression = value.get('progression', {})
        if not isinstance(progression, dict):
            progression = {}
        preferences = progression.get('completed_goal_preferences', [])
        conditions = progression.get('task_conditions', [])
        progression = {
            'completed_goal_preferences': [item for item in preferences if isinstance(item, str) and item.strip()]
                if isinstance(preferences, list) else [],
            'task_conditions': [{key: item[key] for key in ('id', 'condition') if key in item}
                for item in conditions if isinstance(item, dict) and isinstance(item.get('condition'), str)]
                if isinstance(conditions, list) else []}
        return {'source': str(Path(path).resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
                'origin': 'historical_observed_knowledge', 'game_version': value.get('game_version'),
                'observed_date': value.get('observed_date'), 'live_proof': False,
                'version_verified': False, 'reuse_policy': value.get('reuse_policy', {}),
                'equipment': value.get('equipment', {}), 'bonds': value.get('bonds', {}),
                'roles': roles, 'investments': investments, 'guide': guide,
                'progression': progression,
                'startup_guide': {'entry_index': 2, 'entry': '创业指南',
                    'conditions': value.get('startup_guide', {}).get('conditions_pending', [])},
                'deployment_limits': {'front': 4, 'back': 6, 'total': 10},
                'user_rules': value.get('user_rules', []), 'error': None}
    except (OSError, ValueError, TypeError, AttributeError):
        return {'origin': 'historical_observed_knowledge', 'live_proof': False,
                'version_verified': False, 'error': '知识缓存不可读；新内容须实读'}


def guide_phase(guide, observed, source=None):
    """A retained, same-match live guide read plus real mode/level, not plane number."""
    fields = observed.get('fields', {})
    level, mode = fields.get('level'), fields.get('mode')
    label = guide.get('mode_label') if isinstance(guide, dict) else None
    cached = bool(source and source.get('origin') == 'cached_guide_with_live_application')
    if (not source or not isinstance(guide, dict) or not cached and guide.get('applied') is not True
            or not cached and guide.get('body_read') is not True or type(level) is not int
            or mode not in ('标准博弈', '超频博弈') or not isinstance(label, str) or mode not in label):
        return {'phase': None, 'reroll_allowed': False, 'origin': 'unknown',
                'reason': '须当前局已应用攻略正文、模式和实时等级；历史缓存不作搜牌授权'}
    candidates = []
    current_phase = None
    lines = guide.get('body_lines', []) if not cached else [guide.get('level_plan', '')]
    for line in lines:
        if not isinstance(line, str):
            continue
        for part in re.split(r'(前期|中期|后期)[：:]', line):
            if part in ('前期', '中期', '后期'):
                current_phase = {'前期': 'early', '中期': 'transition', '后期': 'core'}[part]
                continue
            for clause in re.split(r'[；;。]', part):
                if re.search(r'不(?:要|能)?搜牌|禁止搜牌|不要刷新', clause):
                    continue
                if any(int(value) == level for value in re.findall(r'(\d{1,2})\s*(?:级|本)\s*(?:搜牌|搜)', clause)):
                    candidates.append({'phase': current_phase or 'transition', 'text': line})
    if len({item['phase'] for item in candidates}) != 1:
        return {'phase': None, 'reroll_allowed': False, 'origin': 'live_guide_read',
                'proof': source, 'reason': '正文没有唯一匹配当前等级的搜牌指令'}
    return {'phase': candidates[0]['phase'], 'reroll_allowed': True,
            'origin': 'cached_guide_with_live_application' if cached else 'live_guide_read',
            'proof': source, 'mode': mode, 'level': level, 'basis': candidates[0]['text']}


def deployment_position(knowledge, name):
    """Return one cached deployment type; live facts never fill this contract."""
    roles = knowledge.get('roles') if isinstance(knowledge, dict) else None
    role = roles.get(name) if isinstance(roles, dict) and isinstance(name, str) and name.strip() else None
    if not isinstance(role, dict):
        raise ValueError('角色部署类型缓存缺失')
    values = [role[key] for key in ('position', 'deployment') if key in role]
    if not values or any(value not in ('前台', '后台', '前后台') for value in values):
        raise ValueError('角色部署类型缓存缺失或非法')
    if len(set(values)) != 1:
        raise ValueError('角色部署类型缓存字段冲突')
    return values[0]


def lineup_requirements(investments, team, knowledge):
    """Historical chosen_this_match flags are deliberately never consulted."""
    board = [unit for unit in team.get('units', []) if unit.get('location') == 'board']
    names = {unit.get('name') for unit in board}
    obligations = []
    for item in investments if isinstance(investments, list) else []:
        if item.get('name') == '飞光·传剑' and '同时在场' in item.get('effect', ''):
            obligations.append({'investment': item['name'], 'required_units': ['彦卿', '景元'],
                                'satisfied': team.get('checked') is True and {'彦卿', '景元'} <= names,
                                'next_step': '生成上场/人口/替换计划；下节点前核两人同时在场'})
    violations, unknown, occupied = [], [], set()
    counts = {'front': 0, 'back': 0}
    roles = knowledge.get('roles', {})
    for unit in board:
        if not isinstance(unit.get('name'), str) or not unit['name'].strip():
            violations.append({'reason': '场上角色实名缺失'})
        row, slot = unit.get('row'), unit.get('slot')
        if row not in counts or type(slot) is not int:
            unknown.append(unit.get('name'))
            continue
        limit = 4 if row == 'front' else 6
        if not 1 <= slot <= limit:
            violations.append({'name': unit.get('name'), 'reason': '槽位超出前4/后6'})
        if (row, slot) in occupied:
            violations.append({'name': unit.get('name'), 'reason': '重复占用同一槽位'})
        occupied.add((row, slot))
        counts[row] += 1
        position = unit.get('position')
        try:
            cached_position = deployment_position(knowledge, unit.get('name'))
        except ValueError:
            role = roles.get(unit.get('name')) if isinstance(roles, dict) else None
            if isinstance(role, dict) and any(role.get(key) is not None for key in ('position', 'deployment')):
                violations.append({'name': unit.get('name'), 'reason': '角色部署类型缓存非法或冲突，不能以当前声明覆盖'})
        else:
            if position not in (None, '') and position != cached_position:
                violations.append({'name': unit.get('name'), 'reason': '当前角色类型声明与已核缓存冲突；策略改写需鲜读'})
            position = cached_position
        if position not in ('前台', '后台', '前后台'):
            unknown.append(unit.get('name'))
        if position in ('前台', '后台') and row != ('front' if position == '前台' else 'back'):
            violations.append({'name': unit.get('name'), 'reason': '角色前后台限制冲突；策略改写需鲜读'})
    if counts['front'] > 4 or counts['back'] > 6 or len(board) > 10:
        violations.append({'reason': '场上人数超出前4/后6/总10'})
    return {'limits': {'front': 4, 'back': 6, 'total': 10}, 'investments': obligations,
            'position_violations': violations, 'unknown_positions': unknown,
            'needs_lineup_plan': any(not item['satisfied'] for item in obligations),
            'verified': team.get('checked') is True and not violations and not unknown}


def preparation_status(reviews):
    steps = [{'id': phase, 'label': PHASE_LABELS[phase],
              'completed': reviews.get(phase, {}).get('completed') is True,
              'proof': reviews.get(phase, {}).get('proof'),
              'origin': reviews.get(phase, {}).get('origin', 'unknown')}
             for phase in PHASES]
    pending = next((item for item in steps if not item['completed']), None)
    return {'phase': pending['id'] if pending else 'ready_for_battle', 'steps': steps,
            'next_step': pending['label'] if pending else '按既有单次审批出战',
            'startup_guide_entry': {'entry_index': 2, 'name': '创业指南'},
            'economy_allowed': all(reviews.get(key, {}).get('completed') is True for key in PHASES[:3]),
            'battle_ready': pending is None}


def reward_status(observed, review=None):
    """No absent-template inference: only a current reviewed full sweep is clear.

    The worker still authenticates the review's run, epoch and request through
    verified_source. This pure predicate never upgrades native confidence.
    """
    if observed.get('page') in ('investment', 'environment', 'supply', 'reward_overlay'):
        return 'pending'
    review = review if isinstance(review, dict) else {}
    proof, value = review.get('proof'), review.get('value')
    if (observed.get('page') != 'preparation' or not observed.get('snapshot_id')
            or not isinstance(proof, dict) or not isinstance(value, dict)
            or proof.get('source') != 'observed_screen'
            or proof.get('snapshot_id') != observed['snapshot_id']
            or value.get('reviewer') != 'supervising_agent'):
        return 'unknown'
    if value.get('all_claimed') is False:
        return 'pending'
    return ('clear' if value.get('all_claimed') is True
            and value.get('rescanned_after_claim') is True else 'unknown')


def reviewed_capacity(value):
    """A separate, explicit supervisor reading; never writes native team facts."""
    if not isinstance(value, dict) or type(value.get('bench_capacity')) is not int or value['bench_capacity'] != 9:
        raise ValueError('容量须独立实读9个备战席，不能由人口推定')
    slots = value.get('slots')
    if (not isinstance(slots, list) or len(slots) != 9 or any(not isinstance(slot, dict) for slot in slots)
            or any(type(slot.get('slot')) is not int for slot in slots)
            or {slot['slot'] for slot in slots} != set(range(1, 10))
            or any(slot.get('status') not in ('empty', 'occupied') for slot in slots)
            or value.get('overflow_checked') is not True
            or type(value.get('overflow_count')) is not int or not 0 <= value['overflow_count'] <= 64):
        raise ValueError('备战席或临时溢出未逐项回读；满人口不能代替库存检查')
    occupied = sum(slot['status'] == 'occupied' for slot in slots)
    return {'bench_capacity': 9, 'occupied': occupied, 'free_slots': 9 - occupied,
            'overflow_count': value['overflow_count'], 'slots': slots,
            'origin': 'supervising_agent'}


def inventory_mutation(action):
    kind, text = action.get('type'), action.get('text', '')
    if kind == 'click_text' and text in ('装备推荐', '装备追踪', '装备追踪中', '角色详情', '攻略', '阵容'):
        return False
    return (kind in ('drag', 'buy_shop') or kind == 'key' and action.get('args') == [46]
            or kind == 'click_text' and any(word in text for word in ('出售', '合成', '装备', '拆卸', '赋予', '复制')))


def action_effect(action, page=None):
    """Effect classes for invalidation, never an exemption from target guards."""
    text = action.get('text', '')
    if inventory_mutation(action):
        return 'inventory'
    location = page or action.get('expected_page')
    if (action.get('type') == 'buy_xp' or action.get('type') == 'key' and
            (action.get('args') == [68] or action.get('args') == [70] and location in ('preparation', 'shop'))
            or action.get('type') == 'click_text' and any(word in text for word in ('刷新', '购买经验'))):
        return 'economy'
    return 'navigation'
