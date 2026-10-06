"""Input-free goal audit and opportunity planning; proposals are not live proof.

Pattern: Unified Planning get_unsatisfied_goals (0c8092e), with explicit
preconditions/effects. No planner runtime or second desktop controller.
"""
from __future__ import annotations

import re


TASK_OPERATORS = (
    (r'特权赋予卡.*升级', 'upgrade_equipment', ['特权赋予卡'], '选择能提供特权赋予卡的奖励；有卡后给符合条件的进阶装备升级'),
    (r'穿戴.*(?:不同)?星徽', 'equip_emblem', ['星徽'], '选择尚未计入任务的星徽，给可穿戴角色使用并核进度'),
    (r'(?:投影仪.*复制|复制.*角色)', 'duplicate_unit', ['员工投影仪', '完美投影仪'], '优先获取投影仪并复制有明确阵容用途的角色'),
    (r'(?:合成|制作).*(?:装备|星徽|财富宝钻)', 'craft_equipment', [], '保留任务要求的合成材料，按实际配方合成并核进度'),
    (r'(?:激活|达成|上场|凑齐).*(?:羁绊|阵营|流派)', 'deploy_bond', [], '选覆盖该未完成羁绊的攻略，达到实际要求的档位再出战'),
    (r'(?:通关|完成|获胜).*(?:博弈|对局|挑战)', 'clear_match', [], '按任务指定模式和条件完成一局，以真实结算验收'),
)


def _strings(values):
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value.strip()))


def rank_guides(candidates, completed_goals, pending_targets=()):
    """Prefer pending targets; demote guides whose stated targets are all done.

    Unknown target status is never labelled unfinished. Original order is the
    tie-break; guide application still needs current mode and screen evidence.
    """
    completed, pending = set(_strings(completed_goals)), set(_strings(pending_targets))
    ranked = []
    for order, candidate in enumerate(candidates or []):
        if not isinstance(candidate, dict) or not isinstance(candidate.get('title'), str):
            continue
        stated = set(_strings(candidate.get('bonds', [])))
        if not stated:
            stated = {goal for goal in completed | pending if goal in candidate['title']}
        matched, done = sorted(stated & pending), sorted(stated & completed)
        exhausted = bool(stated) and stated <= completed
        ranked.append({**candidate, 'goal_preference': {'pending_targets': matched,
            'completed_targets': done, 'all_stated_targets_completed': exhausted,
            'other_targets_unverified': sorted(stated - completed - pending)},
            '_rank': (-len(matched), exhausted, len(done), order)})
    ranked.sort(key=lambda item: item['_rank'])
    for item in ranked:
        item.pop('_rank')
    return ranked


def _task_audit(task, snapshot_id, reviewed_source=None):
    if not isinstance(task, dict) or not isinstance(task.get('condition'), str) or not task['condition'].strip():
        return None
    proof = task.get('proof') if isinstance(task.get('proof'), dict) else {}
    live = bool(snapshot_id and proof.get('snapshot_id') == snapshot_id
                and proof.get('source') in ('observed_screen', 'supervising_agent'))
    reviewed = bool(reviewed_source and proof.get('snapshot_id') == reviewed_source
                    and proof.get('source') in ('observed_screen', 'supervising_agent'))
    planning_verified = live or reviewed
    met = task.get('condition_met') if planning_verified and type(task.get('condition_met')) is bool else None
    claimed = task.get('reward_claimed') if planning_verified and type(task.get('reward_claimed')) is bool else None
    state = 'unknown' if met is None else 'completed' if met and claimed else 'completed_unclaimed' if met else 'pending'
    result = {'id': task.get('id'), 'condition': task['condition'], 'state': state,
              'condition_met': met, 'reward_claimed': claimed, 'live_progress_verified': live,
              'planning_progress_verified': planning_verified,
              'progress': task.get('progress') if planning_verified else None,
              'progress_proof': proof if planning_verified else None,
              'progress_basis': 'current_snapshot' if live else 'accepted_current_node_review' if reviewed else 'unknown',
              'target_bonds': _strings(task.get('target_bonds', [])), 'operator': None,
              'required_resources': [], 'next_step': '读取当前条件进度，确认尚未完成再安排动作'}
    for pattern, operator, resources, step in TASK_OPERATORS:
        if re.search(pattern, task['condition']):
            result.update(operator=operator, required_resources=list(resources))
            if state == 'pending':
                result['next_step'] = step
            break
    if state == 'completed_unclaimed':
        result['next_step'] = '领取已完成奖励，不再重复执行达成动作'
    elif state == 'completed':
        result['next_step'] = '已完成且已领取，保留记录'
    return result


def progression_plan(knowledge, observed, guide_candidates=None, live_tasks=None,
                     reviewed_task_context=None, observation_scope=None):
    """Return unmet-goal intent and resource opportunities; never issue input."""
    policy = knowledge.get('progression', {})
    completed = _strings(policy.get('completed_goal_preferences', []))
    snapshot = observed.get('snapshot_id')
    context = reviewed_task_context if isinstance(reviewed_task_context, dict) else {}
    scope = observation_scope if isinstance(observation_scope, dict) else {}
    # Only the worker's accepted review from this exact match/node/epoch can
    # carry an earlier panel read into planning. Keep its original proof;
    # this is advisory intent, never a fresh progress or execution certificate.
    reviewed_source = context.get('source_snapshot_id')
    context_valid = (context.get('validated') is True and isinstance(reviewed_source, str)
        and bool(reviewed_source) and all(isinstance(scope.get(key), str) and scope[key]
            and context.get(key) == scope[key] for key in ('match_id', 'stage', 'resume_epoch')))
    tasks = (live_tasks if live_tasks is not None else observed.get('semantic', {}).get('tasks'))
    if tasks is None:
        tasks = context.get('tasks') if context_valid else policy.get('task_conditions', [])
    tasks = tasks if isinstance(tasks, list) else []
    audit = [value for task in tasks if (value := _task_audit(task, snapshot,
        reviewed_source if context_valid else None)) is not None]
    unmet = [task for task in audit if task['state'] == 'pending']
    pending_targets = _strings([goal for task in unmet for goal in task['target_bonds']])
    inventory = observed.get('semantic', {}).get('inventory', {})
    current_items = []
    if isinstance(inventory, dict) and inventory.get('snapshot_id') == snapshot:
        current_items = [item for item in inventory.get('items', []) if isinstance(item, dict)
                         and item.get('snapshot_id') == snapshot and item.get('verified') is True
                         and isinstance(item.get('name'), str)]
    opportunities = []
    for task in unmet:
        usable = [item['name'] for item in current_items if item['name'] in task['required_resources']]
        opportunities.append({'task_id': task['id'], 'operator': task['operator'],
            'seek_resources': task['required_resources'], 'verified_resources_available': usable,
            'next_step': task['next_step'], 'execute_ready': False,
            'preconditions_remaining': ['核目标物品／角色是否符合任务条件', '核当前输入前置和实际变化']})
    return {'origin': 'goal_intent_only', 'snapshot_id': snapshot,
            'completed_goal_preferences': completed, 'tasks': audit,
            'observation_needed': [task['id'] for task in audit if task['state'] == 'unknown'],
            'action_opportunities': opportunities,
            'guide_ranking': rank_guides(guide_candidates, completed, pending_targets),
            'rules': ['开局选择未完成目标，沿途主动完成其必要动作',
                      '条件达成与奖励已领分开记录', '动作预测不能代替当前进度和结算验收']}
