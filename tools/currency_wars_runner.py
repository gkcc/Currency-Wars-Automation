"""Bounded local workflow, explicit strategy plans, one safety broker."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import psutil

import currency_wars_broker_entry as entry
import currency_wars_input_bridge as input_bridge
from currency_wars_source_guard import activity
from currency_wars_perception import Perception, clean, find_text, hash_distance, GOLD_HUD
from currency_wars_shop_reader import purchase_slot
import currency_wars_coaching as coaching
from currency_wars_progression import progression_plan
from currency_wars_visual_guards import (stable_semantic_plan, stable_semantic_target,
    stable_preparation_icon_target, PREPARATION_GUIDE_CONTROL,
    PREPARATION_GUIDE_BOUNDS, PREPARATION_GUIDE_POINT)

artifacts = entry.artifacts
PROJECT = Path(__file__).resolve().parent.parent
CURRENT = PROJECT / 'docs' / 'CURRENT_RUNNER.json'
COACHING_POLICY = PROJECT / 'docs' / 'COACHING_POLICY.json'
SELF = Path(__file__).resolve()
TERMINAL = {'stopped', 'completed', 'failed'}
UAC_READY_TIMEOUT_SECONDS = 15 * 60
PANELS = [('bonds', '羁绊链路'), ('income', '预期收益'),
          ('promotion', '晋升等级'), ('advantages', '优势布局')]


def now():
    return datetime.now(timezone.utc).isoformat()


def optional(path):
    try:
        return entry.read_json(path)
    except FileNotFoundError:
        return None


class BattleConfirmationRequired(ValueError):
    pass


class GuardedSubmission:
    """Delegate unchanged Entry semantics; guard only its locked publication."""
    def __init__(self, control, guard):
        self.control, self.guard = control, guard
        self.publication_attempted = False

    def __getattr__(self, name):
        return getattr(self.control, name)

    def publish_request(self, value):
        try:
            self.guard(value)
        except Exception:
            # Entry created this new receipt under its original submission_lock;
            # the real publisher has not been called. Never erase a result or
            # clean up after a possibly published request.
            receipt = Path(self.control.ROOT, 'request-ledger', hashlib.sha256(value['id'].encode()).hexdigest() + '.json')
            recorded = entry.read_json(receipt)
            if recorded.get('id') == value['id'] and recorded.get('request') == value and recorded.get('result') is None:
                receipt.unlink()
            raise
        self.publication_attempted = True
        return self.control.publish_request(value)


def coaching_policy():
    """The GUI changes this durable policy; malformed/missing policy fails closed."""
    try:
        data = COACHING_POLICY.read_bytes()
        policy = json.loads(data.decode('utf-8-sig'))
        if (type(policy.get('protocol_version')) is not int or policy.get('protocol_version') != 1
                or type(policy.get('enabled')) is not bool or type(policy.get('require_battle_confirmation')) is not bool
                or policy['enabled'] and policy['require_battle_confirmation'] is not True):
            raise ValueError('带教策略格式无效')
        return {**policy, 'valid': True,
                'require_battle_confirmation': policy['enabled'] and policy['require_battle_confirmation'],
                'revision': str(policy.get('updated_at_ms')) + ':' + hashlib.sha256(data).hexdigest()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {'enabled': True, 'require_battle_confirmation': True, 'valid': False, 'revision': None}


def canonical_stage(value):
    return value if isinstance(value, str) and re.fullmatch(r'[1-3]-[1-9]', value) else None


def battle_stage(request):
    return canonical_stage(request.get('observation', {}).get('fields', {}).get('stage')) or canonical_stage(request.get('battle_stage'))


def battle_input(observed, action=None, tokens=()):
    """Actual native button/modal hits, never planner booleans or ID exemptions."""
    page = observed.get('page')
    action = action or {}
    if page not in ('preparation', 'shop', 'unknown'):
        return False
    rows = observed.get('rows', [])
    body = ''.join(clean(row.get('text', '')) for row in rows if row.get('confidence', 0) >= .90)
    modal = (('出战' in body or '战斗' in body)
             and any(word in body for word in ('人数', '上场', '角色数量'))
             and any(word in body for word in ('不足', '未满', '少于', '是否')))
    # Full visible native CTA in the retained 1920x1080 prep frame, not a
    # caller-provided ROI. The unique actual caption must lie inside it.
    native_box = [1654, 710, 1920, 792]
    native = find_text(rows, '出战', native_box, True) if page in ('preparation', 'shop') else None
    header_box = [220, 35, 355, 105] if page == 'shop' else [390, 20, 555, 105]
    header = find_text(rows, '备战阶段', header_box, True) if page in ('preparation', 'shop') else None
    boxes = []
    if (native and header and native['confidence'] >= .90 and header['confidence'] >= .90
            and all(bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                    and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]
                    for row, bounds in ((native, native_box), (header, header_box)))):
        boxes.append(native_box)
    if page == 'unknown':
        for label in ('出战', '进入战斗', '开始战斗', '确认出战'):
            row = find_text(rows, label, exact=True)
            if row and row['confidence'] >= .90:
                boxes.append(row['box'])
    if modal:
        for label in ('确认', '确定', '出战', '进入战斗', '继续出战', '确认出战'):
            row = find_text(rows, label, exact=True)
            if row and row['confidence'] >= .90:
                boxes.append(row['box'])
    inputs = []
    if tokens:
        for token in tokens:
            values = token.split(':')
            if values[0] in ('click', 'drag', 'key'):
                inputs.append((values[0], [float(value) for value in values[1:]]))
    elif action.get('type') == 'click_text':
        row = find_text(rows, action.get('text', ''), action.get('bounds'), action.get('exact', True))
        if not row:
            raise ValueError('needs_user_guidance：文字目标尚未可靠实读，不能判定其输入性质')
        inputs.append(('click', [(row['box'][0]+row['box'][2])/2, (row['box'][1]+row['box'][3])/2]))
    elif action.get('type') in ('click_point', 'drag', 'key'):
        inputs.append(({'click_point': 'click', 'drag': 'drag', 'key': 'key'}[action['type']], action.get('args', [])))

    def intersects(values, box):
        if len(values) == 2:
            return box[0] <= values[0] < box[2] and box[1] <= values[1] < box[3]
        if len(values) != 4:
            raise ValueError('输入端点尚未核实')
        low, high = 0., 1.
        for axis in (0, 1):
            delta = values[axis+2] - values[axis]
            if delta == 0:
                if not box[axis] <= values[axis] <= box[axis+2]:
                    return False
            else:
                left, right = sorted(((box[axis]-values[axis])/delta, (box[axis+2]-values[axis])/delta))
                low, high = max(low, left), min(high, right)
                if low > high:
                    return False
        return True

    for kind, values in inputs:
        if kind == 'key':
            if values in ([27], [68], [69], [70]):
                continue  # Ordinary exit/shop/experience navigation keeps its guards.
            if boxes:
                return True
            if page in ('preparation', 'shop') or modal:
                raise ValueError('needs_user_guidance：战斗按钮/确认模态未实读，按键性质未知')
        elif boxes:
            if any(intersects(values, box) for box in boxes):
                return True
            if modal:
                raise ValueError('needs_user_guidance：人数确认模态中的坐标目标未可靠实读')
        elif page in ('preparation', 'shop') or modal:
            raise ValueError('needs_user_guidance：战斗按钮/确认模态未实读，坐标性质未知')
    return False


def battle_approval(run, owner, request, epoch, policy):
    approval = optional(Path(run) / 'runner-battle-approval.json')
    if not policy['valid'] or not isinstance(approval, dict) or approval.get('source') != 'explicit_user_approval':
        raise BattleConfirmationRequired('needs_user_confirmation：带教模式须由用户明确批准本次战斗')
    binding = {'run_id': owner['run_id'], 'request_id': request.get('request_id'),
               'snapshot_id': request.get('snapshot_id'), 'stage': battle_stage(request),
               'resume_epoch': epoch, 'match_id': request.get('match_id'), 'policy_revision': policy['revision']}
    if (not binding['stage'] or any(approval.get(key) != value for key, value in binding.items())
            or request.get('resume_epoch') != epoch):
        raise BattleConfirmationRequired('needs_user_confirmation：战斗批准的节点/请求/交接代次/带教修订已失效')
    try:
        current = datetime.now(timezone.utc)
        expires = datetime.fromisoformat(approval['expires_at'])
        created = datetime.fromisoformat(approval['created_at'])
        deadline = datetime.fromisoformat(request['deadline_at'])
        valid_time = created <= current < expires <= deadline and (expires - created).total_seconds() <= 60
    except (ValueError, TypeError, KeyError):
        valid_time = False
    if (not valid_time or (Path(run) / ('battle-approval-consumed-' + request['request_id'] + '.json')).exists()):
        raise BattleConfirmationRequired('needs_user_confirmation：批准已过期或消费；不能重用或重发')
    return approval


def preparation_decision(observed, facts):
    """Rank only locally read facts; missing stars, traits or inventory stay unknown."""
    semantic = observed.get('semantic', {})
    guide = semantic.get('guide') or facts.get('guide') or {}
    team = semantic.get('team') or facts.get('team') or {}
    gear = semantic.get('gear') or facts.get('gear') or {}
    targets = guide.get('targets', {}) if guide.get('targets_read') is True else {}
    rules = guide.get('operating_rules', {})
    missing = []
    if not team.get('checked'):
        missing.append('场上/板凳实名、星级与对子尚未完整实读')
    if gear.get('inventory_checked') is not True:
        missing.append('真实库存、场上适配角色及已装备状态尚未检查；攻略推荐不等于库存')
    actions = []
    preview = semantic.get('unit_preview', {})
    preferred = targets.get('tracking', []) + targets.get('early', []) + targets.get('core', [])
    if (preview.get('source') == 'guide' and preview.get('snapshot_id') == observed.get('snapshot_id')
            and preview.get('name') in preferred and isinstance(preview.get('recommendation_action'), dict)):
        actions.append(preview['recommendation_action'])
    if observed.get('page') == 'preparation' and missing:
        for label in ('装备追踪中', '装备追踪', '攻略', '阵容', '角色详情'):
            found = find_text(observed.get('rows', []), label, exact=True)
            if found and found['confidence'] >= .90:
                actions.append({'type': 'click_text', 'text': label, 'exact': True, 'bounds': found['box'],
                    'expected_page': 'preparation', 'reason': '先检查攻略装备目标、可用库存与当前阵容，不能默认已经装好'})
                break
    if gear.get('inventory_checked') is True and team.get('checked') is True:
        board_ids = {unit.get('id') for unit in team.get('units', []) if unit.get('location') == 'board'}
        for item in gear.get('inventory', []):
            equip = item.get('equip_action')
            if (item.get('available') is True and isinstance(equip, dict)
                    and set(item.get('compatible_unit_ids', [])) & board_ids):
                actions.insert(0, equip)
                break
    candidates = []
    coin_fact = semantic.get('coins', {})
    coins = coin_fact.get('value') if coin_fact.get('bounds') == GOLD_HUD else None
    units = team.get('units', []) if team.get('checked') is True else []
    owned = [unit.get('name') for unit in units if isinstance(unit.get('name'), str)]
    deployed = observed.get('fields', {}).get('deployed')
    count = re.fullmatch(r'([0-9]+)/([0-9]+)', deployed or '')
    open_population = bool(count and 0 <= int(count[1]) < int(count[2]) <= 12)
    traits = semantic.get('purchase_units', [])
    bonds = semantic.get('bonds') or facts.get('bonds') or {}
    for slot_id in range(1, 6):
        slot = purchase_slot(observed.get('shop') or {}, slot_id, observed.get('snapshot_id'))
        if slot is None or type(coins) is not int or coins < slot['cost']:
            continue
        name = slot['name']
        same = [unit for unit in units if unit.get('name') == name]
        explicit = next((unit for unit in traits if unit.get('name') == name), {})
        applicable = explicit.get('applicable') is True or name in sum((targets.get(key, []) for key in ('early', 'transition', 'core')), [])
        rank, reason = None, None
        if any(unit.get('upgrade_copies_needed') == 1 and type(unit.get('upgrade_copies_needed')) is int for unit in same):
            rank, reason = 0, '实读对子可立即升星，先提高当下战力'
        elif owned.count(name) == 1 and applicable and any(type(unit.get('stars')) is int and unit['stars'] == 1 for unit in same):
            rank, reason = 1, '已有单张成对子且当前阵容/攻略适用'
        elif open_population and applicable and (explicit.get('can_deploy') is True or team.get('checked') is True and name not in owned):
            rank, reason = 2, '空人口优先可上场的实读过渡/攻略角色'
        else:
            contribution = set(explicit.get('bonds', []))
            threshold = [bond for bond in bonds.get('items', []) if bond.get('name') in contribution
                         and type(bond.get('active_count')) is int and type(bond.get('next_threshold')) is int
                         and bond['active_count'] + 1 == bond['next_threshold']]
            if threshold:
                rank, reason = 3, '实读角色补足小羁绊阈值'
            elif canonical_stage(observed.get('fields', {}).get('stage')) and observed['fields']['stage'].startswith('1-') and any(
                    bond.get('name') in contribution and bond.get('kind') in ('economy', 'growth') for bond in bonds.get('items', [])):
                rank, reason = 4, '前期建立已实读经济/成长羁绊，避免机械囤钱'
            elif name in targets.get('early', []):
                rank, reason = 5, '已应用攻略正文明确的前期目标'
            elif name in targets.get('transition', []):
                rank, reason = 6, '已应用攻略正文明确的过渡目标'
            elif name in targets.get('core', []):
                rank, reason = 7, '已应用攻略正文明确的核心/长线目标'
        if rank is not None:
            candidates.append({'priority': rank, 'basis': reason, 'action': {'type': 'buy_shop',
                'slot': slot_id, 'name': name, 'cost': slot['cost'], 'expected_page': 'shop', 'reason': reason}})
    candidates.sort(key=lambda value: (value['priority'], value['action']['cost'], value['action']['slot']))
    phase_evidence = coaching.guide_phase(guide, observed, facts.get('guide_proof'))
    phase = phase_evidence['phase']
    economic_actions = []
    xp = semantic.get('xp', {})
    full_population = bool(count and 1 <= int(count[1]) == int(count[2]) <= 12)
    stage = canonical_stage(observed.get('fields', {}).get('stage'))
    due_level = any(canonical_stage(value.get('stage')) and stage and stage >= value['stage']
                    and type(value.get('level')) is int and type(observed.get('fields', {}).get('level')) is int
                    and observed['fields']['level'] < value['level'] for value in rules.get('level_deadlines', []))
    if (not candidates and not missing and full_population and (due_level or team.get('power_weak') is True)
            and xp.get('snapshot_id') == observed.get('snapshot_id') and type(xp.get('buy_cost')) is int
            and 0 < xp['buy_cost'] <= (coins if type(coins) is int else -1)
            and type(xp.get('buy_gain')) is int and xp['buy_gain'] > 0):
        economic_actions.append({'type': 'buy_xp', 'count': 1, 'expected_page': observed['page'],
                                 'reason': '人口已满且有实读战力缺口/攻略等级期限，按实际经验费用单次升人口后回读'})
    reroll = semantic.get('reroll', {})
    refresh = find_text(observed.get('rows', []), '刷新', exact=True)
    if (not candidates and not economic_actions and not missing and phase is not None and phase not in rules.get('no_reroll_phases', [])
            and refresh and refresh['confidence'] >= .90 and reroll.get('power_improvement_needed') is True
            and reroll.get('snapshot_id') == observed.get('snapshot_id') and type(reroll.get('cost')) is int
            and 0 < reroll['cost'] <= (coins if type(coins) is int else -1)):
        economic_actions.append({'type': 'click_text', 'text': '刷新', 'exact': True, 'bounds': refresh['box'],
                                 'expected_page': observed['page'], 'reason': '没有现成可买提升且实读战力不足，按实价有限刷新一次'})
    return {'phase': 'equipment_and_lineup_check' if missing else 'improve_current_power',
            'needs_user_guidance': missing, 'inspection_actions': actions,
            'purchase_candidates': candidates, 'economic_actions': economic_actions,
            'recommended_actions': actions[:1] or [value['action'] for value in candidates[:1]] or economic_actions[:1],
            'operating_rules': rules, 'strategy_phase': phase_evidence,
            'reroll_allowed': phase_evidence['reroll_allowed'] and phase not in rules.get('no_reroll_phases', []),
            'economy_policy': '对子/上场/小羁绊/早期经济成长优先于长线囤钱；未知经验费用或刷新收益时不盲花',
            'unknown_fields': ['未实读的星级、装备兼容性、羁绊贡献、经验费用与刷新收益不推造']}


def redact(value):
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()
                if key not in ('run_token', 'gui_token')}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def decode_launcher_output(payload):
    """Windows PowerShell may use the active ANSI code page on its pipes."""
    for encoding in ('utf-8-sig', 'mbcs'):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            pass
    return payload.decode('utf-8', errors='replace')


def envelope(state, ok=True, command_id=None, error=None):
    result = {'ok': bool(ok), 'command_id': command_id or uuid.uuid4().hex, 'state': redact(state)}
    if 'exit_evidence' in state:
        result['exit_evidence'] = state['exit_evidence']
    if error:
        result['error'] = str(error)
    return result


@contextlib.contextmanager
def file_lock(run, name='runner-control.lock', timeout=1):
    path = Path(run, name)
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError('运行状态忙；未修改锁或重发动作')
            time.sleep(.02)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def load(run, chat, token, emergency=False):
    control = entry.backend()
    if emergency:
        run, broker_owner = entry.authenticate(run, chat, token, control)
        binding = None
    else:
        run, broker_owner, binding = entry.load(run, chat, token, control)
    owner = entry.read_json(run / 'runner-owner.json')
    marker = artifacts.read_marker(run)
    if (owner.get('owner') != 'currency-wars-runner' or owner.get('chat_id') != chat
            or not secrets.compare_digest(str(owner.get('run_token', '')), str(token))
            or owner.get('run_id') != marker['run_id']
            or owner.get('artifact_chat_id') != marker.get('session_hint', {}).get('id')
            or owner.get('runner_pid') != marker['pid']
            or 'windows:' + owner.get('runner_creation_id', '') != marker['process_identity']):
        raise ValueError('执行器归属、标准标记或创建身份不匹配')
    if emergency:
        try:
            control.EMERGENCY_BROKER_IDENTITY = entry.owned_broker_identity(control, run)
        except (OSError, ValueError):
            # Corrupt optional identity does not prevent the authenticated stop
            # flag. It prevents any positive exit/ACK claim until identified.
            control.EMERGENCY_BROKER_IDENTITY = None
    return run, owner, binding, control


def current_state(run, owner, control, emergency=False):
    try:
        state = entry.read_json(run / 'runner-state.json')
    except (OSError, ValueError):
        if not emergency:
            raise
        state = {**redact(owner), 'protocol_version': 1, 'run_dir': str(run),
                 'control_mode': 'manual', 'state_sequence': 0, 'heartbeat_at': now(),
                 'reason': '应急状态通道；展示记录不可读', 'decision_request': None}
    if (state.get('run_id') != owner['run_id'] or state.get('runner_pid') != owner['runner_pid']
            or state.get('runner_creation_id') != owner['runner_creation_id']):
        if not emergency:
            raise ValueError('当前状态不属于所属worker代次')
        state = {**redact(owner), 'protocol_version': 1, 'run_dir': str(run),
                 'control_mode': 'manual', 'state_sequence': 0, 'heartbeat_at': now(),
                 'reason': '应急通道未采用不匹配展示记录', 'decision_request': None}
    state['broker'] = entry.emergency_status(control, run) if emergency else control.status()
    probe = entry.exit_probe(control, owner['runner_pid'], owner['runner_creation_id'])
    state['worker_state'] = probe
    try:
        manual = manual_state(run)
    except Exception:
        if not emergency:
            raise
        manual = {'reason': '手动记录不可读；仍已请求broker持续暂停'}
    if manual:
        state['control_mode'], state['reason'] = 'manual', manual['reason']
    elif state['broker']['input_halted']:
        state['control_mode'], state['reason'] = 'halted', state['broker']['reason']
    if probe['state'] in ('absent', 'exited', 'reused'):
        state['control_mode'] = 'stopped'
        state['exit_evidence'] = {'worker': probe, 'broker': state['broker']['broker_state']}
    if probe['state'] == 'running':
        try:
            state['resume_guard'] = resume_guard_snapshot(run, owner, control, status=state['broker'])
            state.pop('resume_guard_error', None)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            if not emergency:
                raise
            # A broken optional resume proof never blocks emergency release.
            state['resume_guard'] = None
            state['resume_guard_error'] = str(exc)[:200]
    return state


def latch_manual(run, reason, command_id):
    if not isinstance(reason, str) or not 1 <= len(reason) <= 200:
        raise ValueError('手动原因须为1–200字符')
    # Priority immutable intents never wait for the ordinary state lock.
    intents = run / 'manual-intents'
    intents.mkdir(exist_ok=True)
    value = {'manual_id': command_id, 'reason': reason, 'time': now(), 'priority_ns': time.perf_counter_ns()}
    target = intents / (command_id + '.json')
    if target.exists():
        raise ValueError('重复手动请求ID，不覆盖原意图')
    entry.backend().write_json(target, value)
    # Display compatibility only; immutable intents are authoritative.
    try:
        entry.backend().write_json(run / 'runner-manual.json', value)
    except OSError:
        pass
    return value


def pending_manual_intents(run):
    directory = run / 'manual-intents'
    entries = list(directory.glob('*.json')) if directory.exists() else []
    if len(entries) > 2000:
        raise RuntimeError('手动请求数量超出有界容量；保持锁定')
    if not entries:
        fallback = optional(run / 'runner-manual.json')
        if fallback is not None and (not fallback.get('manual_id') or not fallback.get('reason')):
            raise RuntimeError('手动锁记录损坏，停止输入')
        return [fallback] if fallback else []
    intents = [entry.read_json(path) for path in entries]
    consumed = optional(run / 'runner-resume-epoch.json') or {}
    ignored = set(consumed.get('consumed_manual_ids', []))
    return [value for value in intents if value['manual_id'] not in ignored]


def manual_state(run):
    pending = pending_manual_intents(run)
    return max(pending, key=lambda value: (value.get('priority_ns', 0), value['manual_id'])) if pending else None


def await_existing_receipt(run, control, rid, timeout=5):
    """Reconcile one already published ID. Never submit, publish or resend."""
    target = run / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
    end = time.monotonic() + min(5, max(0, timeout))
    while True:
        receipt = optional(target)
        if (not receipt or receipt.get('id') != rid or not isinstance(receipt.get('request'), dict)
                or receipt['request'].get('id') != rid
                or receipt['request'].get('chat_id') != control.OWNER['chat_id']
                or receipt['request'].get('run_token') != control.OWNER['run_token']):
            raise ValueError('既有回执身份不符；不重发输入')
        result = receipt.get('result')
        if result is None:
            # A takeover can finish before Entry's submitting caller folds the
            # exact broker result into its ledger. Only this ID can reconcile.
            candidate = optional(run / 'result.json')
            if candidate and candidate.get('id') == rid:
                result = candidate
        if result is not None:
            if not isinstance(result, dict) or result.get('id') != rid:
                raise ValueError('结果身份不符；不重发输入')
            return {**receipt, 'result': result}
        if time.monotonic() >= end:
            raise TimeoutError('既有请求结果仍PENDING/UNKNOWN；不重发输入')
        time.sleep(.05)


def _drain_manual_receipts(run, control):
    pending = []
    for index, path in enumerate((run / 'request-ledger').glob('*.json')):
        if index >= 4096:
            raise ValueError('回执数量超出有界容量')
        receipt = entry.read_json(path)
        if receipt.get('result') is None:
            pending.append(receipt['id'])
    end = time.monotonic() + 5
    reconciled = [await_existing_receipt(run, control, rid, end - time.monotonic()) for rid in pending]
    # The original submitting caller may still hold its Entry lock while
    # folding that exact outcome. Await its release rather than racing a new
    # request into it or retrying any old game input.
    while pending and (run / 'client-submit.lock').exists():
        if time.monotonic() >= end:
            raise TimeoutError('确切既有结果已知，但原提交尚未结束；保持手动，不重发')
        for rid in pending:
            await_existing_receipt(run, control, rid, 0)
        time.sleep(.05)
    return reconciled


def _manual_binding(run, owner, control, manual_id):
    manual = manual_state(run)
    state = entry.read_json(run / 'runner-state.json')
    if (not manual or manual.get('manual_id') != manual_id
            or state.get('run_id') != owner['run_id'] or state.get('chat_id') != owner['chat_id']
            or (run / 'runner-stop').exists() or (run / 'broker-stop').exists()):
        raise ValueError('当前接管/所属run已改变；人工结果保持未知')
    stage = canonical_stage(state.get('preparation_stage'))
    if not stage or not state.get('match_id'):
        raise ValueError('没有当前局/节点绑定；人工结果保持未知')
    records = Path(state['journal_file']).parent
    if (records.resolve().parent != (PROJECT / 'debug').resolve()
            or entry.read_json(records / 'owner.json').get('run_id') != owner['run_id']):
        raise ValueError('人工结果持久证据目录不属于当前run')
    return {'run_id': owner['run_id'], 'match_id': state['match_id'], 'stage': stage,
            'manual_id': manual_id, 'old_epoch': (optional(run / 'runner-resume-epoch.json') or {}).get('id')}, records


def _manual_capture(run, owner, control, binding, records, label, reader):
    rid = uuid.uuid4().hex
    # Existing broker observe is read-only, including while paused. This API
    # never focuses, resumes or performs a desktop input.
    result = entry.request(control, 'actions', ['observe'], rid, False)
    if not result.get('ok') or not result.get('observation'):
        raise ValueError('人工结果缺少同请求的新观察')
    target = records / (label + '-' + rid + '.png')
    with control.submission_lock():
        if (optional(run / 'result.json') or {}).get('id') != rid:
            raise ValueError('人工观察已被另一请求替换；保持未知')
        data = (run / 'game-preview.png').read_bytes()
        watermark = [entry.read_json(path)['id'] for path in (run / 'request-ledger').glob('*.json')]
        if len(watermark) > 4096:
            raise ValueError('人工观察回执数量超出有界容量')
    with target.open('xb') as stream:
        stream.write(data)
    observed = reader.read(target)
    digest = hashlib.sha256(data).hexdigest()
    if observed.get('snapshot_id') != digest:
        raise ValueError('人工观察原帧身份不符')
    stage = canonical_stage(observed.get('fields', {}).get('stage'))
    if stage and stage != binding['stage']:
        raise ValueError('人工操作已改变节点；旧节点完成项不继承')
    current, _ = _manual_binding(run, owner, control, binding['manual_id'])
    if current != binding:
        raise ValueError('观察期间发生新接管/新局；结果保持未知')
    return {'receipt_id': rid, 'observed_at': now(), 'snapshot_id': digest,
            'evidence_file': str(target), 'observation': observed, 'receipt_watermark': watermark}


def begin_manual_phase(run, owner, control, manual_id, phase, *, reader=None):
    """Checkpoint before root's bounded inputs through the SAME broker."""
    with file_lock(run, 'manual-checkpoint.lock', timeout=5):
        return _begin_manual_phase(run, owner, control, manual_id, phase, reader=reader)


def _begin_manual_phase(run, owner, control, manual_id, phase, *, reader=None):
    if phase not in coaching.PHASES:
        raise ValueError('未知人工准备阶段')
    binding, records = _manual_binding(run, owner, control, manual_id)
    directory = run / 'manual-results'
    directory.mkdir(exist_ok=True)
    existing = [entry.read_json(path) for path in directory.glob('*.json')]
    completed = {item['phase'] for item in existing if item.get('binding') == binding and item.get('status') == 'completed'}
    expected = next((key for key in coaching.PHASES if key not in completed), None)
    if phase != expected or any(item.get('binding') == binding and item.get('status') == 'pending' for item in existing):
        raise ValueError('人工阶段须按准备顺序且只有一个在途checkpoint')
    reconciled = _drain_manual_receipts(run, control)
    checkpoint_id = uuid.uuid4().hex
    before = _manual_capture(run, owner, control, binding, records, 'manual-' + checkpoint_id + '-before', reader or Perception())
    item = {'schema': 'manual-preparation-result/v1', 'checkpoint_id': checkpoint_id, 'binding': binding,
            'phase': phase, 'status': 'pending', 'before': before, 'reconciled_receipts': redact(reconciled),
            'prior_receipt_ids': before['receipt_watermark']}
    current, _ = _manual_binding(run, owner, control, manual_id)
    if current != binding:
        raise ValueError('人工checkpoint发布前交接身份已变')
    control.write_json(directory / (checkpoint_id + '.json'), item)
    control.write_json(records / ('manual-' + checkpoint_id + '.json'), redact(item))
    return item


def finish_manual_phase(run, owner, control, checkpoint_id, input_receipt_ids, review, *, reader=None):
    """Store known outcomes + actual before/after PNG; no game input/resume."""
    with file_lock(run, 'manual-checkpoint.lock', timeout=5):
        return _finish_manual_phase(run, owner, control, checkpoint_id, input_receipt_ids, review, reader=reader)


def _finish_manual_phase(run, owner, control, checkpoint_id, input_receipt_ids, review, *, reader=None):
    if not isinstance(checkpoint_id, str) or not re.fullmatch(r'[0-9a-f]{32}', checkpoint_id):
        raise ValueError('人工checkpoint身份无效')
    path = run / 'manual-results' / (checkpoint_id + '.json')
    item = entry.read_json(path)
    binding, records = _manual_binding(run, owner, control, item['binding']['manual_id'])
    if item['binding'] != binding or item.get('status') != 'pending':
        raise ValueError('人工checkpoint已失效/已消费')
    if (not isinstance(input_receipt_ids, list) or len(input_receipt_ids) > 64
            or any(not isinstance(rid, str) for rid in input_receipt_ids)
            or len(set(input_receipt_ids)) != len(input_receipt_ids)
            or any(rid in item['prior_receipt_ids'] for rid in input_receipt_ids)):
        raise ValueError('人工输入须为checkpoint之后的唯一真实回执')
    if (not isinstance(review, dict) or review.get('phase') != item['phase']
            or review.get('stage') != binding['stage'] or review.get('completed') is not True
            or review.get('reviewer') != 'supervising_agent'
            or not isinstance(review.get('findings'), str) or not review['findings'].strip()):
        raise ValueError('人工结果须监督助手逐阶段实际复核')
    receipts = []
    end = time.monotonic() + 5
    for rid in input_receipt_ids:
        receipt = await_existing_receipt(run, control, rid, end - time.monotonic())
        result, request = receipt['result'], receipt['request']
        if (request.get('kind') != 'actions' or result.get('ok') is not True
                or result.get('completed') != request.get('actions') or not result.get('observation')):
            raise ValueError('人工输入回执缺失/部分完成/失败；保持PENDING，不重发')
        receipts.append(receipt)
    # An omitted concurrent request is also unknown. Capturing a later frame
    # cannot silently certify that omitted request's outcome.
    _drain_manual_receipts(run, control)
    actual_ids = {entry.read_json(p)['id'] for p in (run / 'request-ledger').glob('*.json')}
    if actual_ids - set(item['prior_receipt_ids']) != set(input_receipt_ids):
        raise ValueError('checkpoint之后有未列出的回执；人工结果保持PENDING')
    after = _manual_capture(run, owner, control, binding, records, 'manual-' + checkpoint_id + '-after', reader or Perception())
    current = entry.read_json(path)
    latest_binding, _ = _manual_binding(run, owner, control, binding['manual_id'])
    if current != item or current.get('status') != 'pending' or latest_binding != binding:
        raise ValueError('人工结果提交前checkpoint状态/交接身份已变；未覆盖')
    if set(after['receipt_watermark']) - set(item['prior_receipt_ids']) != set(input_receipt_ids) | {after['receipt_id']}:
        raise ValueError('人工后帧之前有未列出的回执；保持PENDING')
    item.update(status='completed', after=after, review=review, input_receipts=redact(receipts),
                outcome='verified_change' if item['before']['snapshot_id'] != after['snapshot_id'] else 'verified_no_change')
    control.write_json(path, item)
    control.write_json(records / ('manual-' + checkpoint_id + '.json'), redact(item))
    return item


def resume_guard_snapshot(run, owner, control, *, status=None, pending=None):
    status = control.status() if status is None else status
    pending = pending_manual_intents(run) if pending is None else pending
    epoch = optional(run / 'runner-resume-epoch.json') or {}
    return {'run_id': owner['run_id'], 'broker_pid': status.get('broker_pid'),
            'broker_creation_id': str(status['broker_creation_time']) if status.get('broker_creation_time') is not None else None,
            'pending_manual_ids': sorted(value['manual_id'] for value in pending),
            'broker_pause_id': status.get('pause_id'), 'resume_epoch': epoch.get('id')}


def parse_resume_guard(value):
    keys = {'run_id', 'broker_pid', 'broker_creation_id', 'pending_manual_ids', 'broker_pause_id', 'resume_epoch'}
    if isinstance(value, dict) and keys - set(value):
        raise ValueError('resume_guard缺字段：' + ','.join(sorted(keys - set(value))) + '；缺失不能作为null')
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('恢复须提供按钮时绑定的完整resume_guard，缺字段不能作为null')
    for key in ('run_id', 'broker_creation_id'):
        if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 100:
            raise ValueError('恢复身份字段无效')
    if type(value['broker_pid']) is not int or not 0 < value['broker_pid'] < 2**32:
        raise ValueError('恢复broker PID无效')
    if not value['broker_creation_id'].isdigit() or int(value['broker_creation_id']) <= 0:
        raise ValueError('恢复broker创建身份无效')
    ids = value['pending_manual_ids']
    if (not isinstance(ids, list) or len(ids) > 2000
            or any(not isinstance(item, str) or not 1 <= len(item) <= 100 for item in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('恢复须绑定完整且不重复的手动意图集合')
    for key in ('broker_pause_id', 'resume_epoch'):
        if value[key] is not None and (not isinstance(value[key], str) or not 1 <= len(value[key]) <= 100):
            raise ValueError('恢复pause/epoch须为明确字符串或null')
    return {**value, 'pending_manual_ids': sorted(ids)}


def resume_guard_rejection(expected, actual):
    reasons = {'run_id': '运行代次已变', 'broker_pid': 'broker进程身份已变',
               'broker_creation_id': 'broker创建身份已变', 'pending_manual_ids': '手动意图完整集合已变',
               'broker_pause_id': 'broker暂停代次已变', 'resume_epoch': '此前恢复交接epoch已变'}
    for key, reason in reasons.items():
        if expected[key] != actual[key]:
            return {'ok': False, 'resumed': False, 'guard_matched': False,
                    'mismatch_field': key, 'reason_kind': 'resume_guard_' + key + '_changed',
                    'error': reason + '；未派发迟到的恢复，保持手动'}
    return None


def explicit_resume(run, owner, control, rid, expected_manual_id=None, expected_broker_pause_id=entry.UNSET,
                    expected_guard=entry.UNSET):
    if expected_guard is not entry.UNSET:
        expected_guard = parse_resume_guard(expected_guard)
    elif expected_manual_id is None or expected_broker_pause_id is entry.UNSET:
        return {'ok': False, 'resumed': False, 'guard_matched': False,
                'error': '普通继续缺少按钮时绑定的resume_guard；未派发恢复'}
    with file_lock(run):
        manual = manual_state(run)
        pending = pending_manual_intents(run)
        captured_ids = {value['manual_id'] for value in pending}
        epoch = manual.get('manual_id') if manual else None
        status = control.status()
        broker_pause = status.get('pause_id')
        captured_resume_epoch = (optional(run / 'runner-resume-epoch.json') or {}).get('id')
        if (run / 'runner-resuming.json').exists():
            return {'ok': False, 'resumed': False, 'guard_matched': False,
                    'reason_kind': 'resume_already_in_progress', 'error': '已有一次恢复在执行；未重复派发'}
        captured_guard = resume_guard_snapshot(run, owner, control, status=status, pending=pending)
        if expected_guard is not entry.UNSET:
            rejected = resume_guard_rejection(expected_guard, captured_guard)
            if rejected:
                return rejected
        if ((expected_manual_id is not None and (epoch != expected_manual_id or captured_ids != {expected_manual_id}))
                or (expected_broker_pause_id is not entry.UNSET and broker_pause != expected_broker_pause_id)):
            return {'ok': False, 'resumed': False, 'guard_matched': False,
                    'error': '新手动请求优先；没有初始handoff'}
        control.write_json(run / 'runner-resuming.json', {'id': rid, 'manual_id': epoch, 'time': now()})
    try:
        # A priority intent can be published without the ordinary lock. Check
        # again before dispatch; the existing broker first-read pause CAS still
        # protects any later pause, rather than replacing it with a new value.
        submit_deadline = time.monotonic() + 5
        while True:
            remaining = submit_deadline - time.monotonic()
            if remaining <= 0:
                return {'ok': False, 'resumed': False, 'guard_matched': False,
                        'reason_kind': 'resume_submit_busy_timeout',
                        'error': '恢复提交锁等待5秒已到；未派发恢复，保持手动'}
            with file_lock(run, timeout=min(1, remaining)):
                if (run / 'runner-stop').exists() or (run / 'broker-stop').exists():
                    return {'ok': False, 'resumed': False, 'guard_matched': False,
                            'reason_kind': 'resume_cancelled_by_stop',
                            'error': '停止请求优先；未派发恢复'}
                rejected = resume_guard_rejection(captured_guard, resume_guard_snapshot(run, owner, control))
                if rejected:
                    return rejected
            if time.monotonic() >= submit_deadline:
                continue
            try:
                result = entry.request(control, 'resume', [], rid, True, expected_pause_id=broker_pause,
                                       submit_deadline=submit_deadline)
            except entry.SubmissionDeadlineExpired:
                return {'ok': False, 'resumed': False, 'guard_matched': False,
                        'reason_kind': 'resume_submit_busy_timeout',
                        'error': '恢复提交锁等待5秒已到；未派发恢复，保持手动'}
            except RuntimeError as exc:
                # Only the O_EXCL submission lock failure precedes ledger and request publication.
                if str(exc) != 'another request is pending; no concurrent submission':
                    raise
                time.sleep(min(.05, max(0, submit_deadline - time.monotonic())))
            else:
                break
        with file_lock(run):
            latest = manual_state(run)
            if ((latest.get('manual_id') if latest else None) != epoch
                    or {value['manual_id'] for value in pending_manual_intents(run)} != captured_ids
                    or (optional(run / 'runner-resume-epoch.json') or {}).get('id') != captured_resume_epoch):
                # A later takeover wins even if the original handoff finished.
                control.pause('恢复期间发生新手动接管')
                raise RuntimeError('新的手动接管优先，恢复未解锁')
            if not result.get('ok') or not result.get('resumed'):
                raise RuntimeError(result.get('error', '同broker恢复未确认'))
            (run / 'runner-manual.json').unlink(missing_ok=True)
            # All old strategy replies become invalid across explicit handoff.
            old = optional(run / 'runner-resume-epoch.json') or {}
            consumed_ids = sorted(set(old.get('consumed_manual_ids', [])) | captured_ids)
            control.write_json(run / 'runner-resume-epoch.json', {'id': rid, 'time': now(), 'previous_epoch': old.get('id'),
                'consumed_manual_id': epoch,
                'consumed_manual_ids': consumed_ids})
        return {**result, 'guard_matched': True, 'resume_epoch': rid, 'consumed_manual_ids': consumed_ids}
    finally:
        (run / 'runner-resuming.json').unlink(missing_ok=True)


def start_cli(args):
    with activity(PROJECT, 'start') as lease:
        return _start_cli(args, lease)


def registered_worker_identity(value, launch, chat_id, child_pid, child_identity, control):
    """Bind the published worker to our exact Popen, including one venv hop."""
    if value.get('launch_id') != launch or value.get('chat_id') != chat_id:
        raise RuntimeError('worker发布的启动/会话身份不匹配')
    pid, creation = value.get('runner_pid'), value.get('runner_creation_id')
    if type(pid) is not int or not isinstance(creation, str) or not creation.isdecimal():
        raise RuntimeError('worker创建身份格式无效')
    if artifacts.process_identity(child_pid) != ('active', child_identity):
        raise RuntimeError('本次Popen创建身份已变化或未知')
    if control.process_probe(pid, creation)['state'] != 'running':
        raise RuntimeError('worker没有保持实际存活')
    if pid != child_pid:
        process, wrapper = psutil.Process(pid), psutil.Process(child_pid)
        normalized = lambda path: os.path.normcase(os.path.abspath(path))
        if (process.ppid() != child_pid
                or normalized(process.exe()) != normalized(getattr(sys, '_base_executable', sys.executable))
                or normalized(wrapper.exe()) != normalized(sys.executable)):
            raise RuntimeError('worker不是本次Python转发器的实际直属子进程')
    if (artifacts.process_identity(child_pid) != ('active', child_identity)
            or control.process_probe(pid, creation)['state'] != 'running'):
        raise RuntimeError('worker转移前的创建身份已变化或未知')
    return pid, 'windows:' + creation


def _start_cli(args, lease):
    if not args.chat_id or len(args.chat_id) > 100:
        raise ValueError('真实root chat必须明确传入')
    if not 60 <= args.max_seconds <= 7200 or not 1 <= args.max_matches <= 20:
        raise ValueError('时限须60–7200秒，局数须1–20')
    if args.max_matches > 1 and not args.continue_matches:
        raise ValueError('多局必须明确启用continue-matches')
    control = entry.backend()
    name = 'Local\\CurrencyWarsRunnerLaunch-' + hashlib.sha256(args.chat_id.encode()).hexdigest()[:24]
    control.C.set_last_error(0)
    mutex = control.k.CreateMutexW(None, True, name)
    if not mutex or control.C.get_last_error() == 183:
        if mutex:
            control.k.CloseHandle(mutex)
        raise RuntimeError('另一个Start正在处理，不重复启动')
    child = None
    try:
        discovered = optional(CURRENT)
        if discovered and discovered.get('chat_id') == args.chat_id:
            probe = control.process_probe(discovered['runner_pid'], discovered['runner_creation_id'])
            if probe['state'] == 'unknown':
                raise RuntimeError('旧worker退出未知，禁止启动第二个')
            if probe['state'] == 'running':
                owner = entry.read_json(Path(discovered['run_dir'], 'runner-owner.json'))
                run, owner, binding, c = load(discovered['run_dir'], args.chat_id, owner['run_token'])
                return envelope(current_state(run, owner, c))
        launch = uuid.uuid4().hex
        command = [sys.executable, '-B', '-X', 'utf8', str(SELF), '_worker',
                   '--chat-id', args.chat_id, '--launch-id', launch,
                   '--max-seconds', str(args.max_seconds), '--max-matches', str(args.max_matches)]
        if args.continue_matches:
            command.append('--continue-matches')
        lease.children([], complete=False)
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        child_state, child_identity = artifacts.process_identity(child.pid)
        if child_state != 'active' or child_identity is None:
            raise RuntimeError('worker创建身份未知；保留原始启动归属')
        lease.children([{'pid': child.pid, 'process_identity': child_identity}], complete=False)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            value = optional(CURRENT)
            if value and value.get('launch_id') == launch:
                worker_pid, worker_identity = registered_worker_identity(
                    value, launch, args.chat_id, child.pid, child_identity, control)
                lease.transfer_to_registered_child(worker_pid, worker_identity, 'runner')
                return envelope(value)
            if child.poll() is not None:
                raise RuntimeError('worker启动失败；没有自动恢复或游戏输入')
            time.sleep(.05)
        # Popen is the exact newly-owned process handle, not an inferred PID.
        child.terminate()
        child.wait(timeout=5)
        raise TimeoutError('worker未发布可绑定身份；已终止本次owned启动进程')
    finally:
        control.k.ReleaseMutex(mutex)
        control.k.CloseHandle(mutex)


def validate_plan(reply, request, epoch):
    if not isinstance(reply, dict) or reply.get('request_id') != request['request_id']:
        raise ValueError('回答request_id不匹配')
    if reply.get('snapshot_id') != request['snapshot_id'] or reply.get('resume_epoch') != epoch:
        raise ValueError('回答画面或手动代次已过期')
    if datetime.now(timezone.utc) > datetime.fromisoformat(request['deadline_at']):
        raise ValueError('战略请求已超过有限期限')
    actions = reply.get('actions')
    if not isinstance(actions, list) or not 1 <= len(actions) <= 8:
        raise ValueError('计划须含1–8个有限语义动作')
    allowed = {'click_text', 'click_point', 'buy_shop', 'buy_xp', 'key', 'drag', 'scroll', 'finish_preparation_review',
               'finish_inspection', 'confirm_match_result'}
    for action in actions:
        if not isinstance(action, dict) or action.get('type') not in allowed:
            raise ValueError('未知语义动作')
        if not isinstance(action.get('reason'), str) or not 1 <= len(action['reason']) <= 1000:
            raise ValueError('每个动作须有具体中文理由')
        if action['type'] not in ('finish_inspection', 'confirm_match_result', 'finish_preparation_review') and not action.get('expected_page'):
            raise ValueError('游戏输入须指定实际页面前置条件')
        if action['type'] in ('click_point', 'drag', 'scroll', 'key'):
            if not isinstance(action.get('guard_texts'), list) or not action['guard_texts']:
                raise ValueError('坐标/按键动作须提供新画面文字守卫')
        if action['type'] in ('click_point', 'drag', 'scroll'):
            proof = action.get('target_evidence')
            if not isinstance(proof, dict) or proof.get('snapshot_id') != request['snapshot_id']:
                raise ValueError('坐标动作须绑定本次原始PNG的目标ROI证据')
            box = proof.get('bounds')
            if (not isinstance(box, list) or len(box) != 4 or any(type(x) is not int for x in box)
                    or not 0 <= box[0] < box[2] <= 1920 or not 0 <= box[1] < box[3] <= 1080):
                raise ValueError('目标ROI边界无效')
            if proof.get('control_id') == PREPARATION_GUIDE_CONTROL and (
                    action['type'] != 'click_point' or len(actions) != 1
                    or request.get('kind') != 'preparation_strategy'
                    or action.get('expected_page') != 'preparation'
                    or box != PREPARATION_GUIDE_BOUNDS or action.get('args') != PREPARATION_GUIDE_POINT
                    or any(type(value) is not int for value in action.get('args', []))):
                raise ValueError('创业指南图标仅允许固定导航单动作；提交前仍须双帧视觉验证')
        if action['type'] == 'finish_inspection' and action.get('panel') not in dict(PANELS):
            raise ValueError('仅可核实当前领奖/优势面板')
        if action['type'] == 'confirm_match_result' and (request['kind'] != 'settlement_verify'
                or not isinstance(action.get('result'), dict)):
            raise ValueError('仅真实整局结算请求可确认match结果')
        if action['type'] == 'buy_xp' and (type(action.get('count')) is not int or not 1 <= action['count'] <= 5):
            raise ValueError('单计划经验次数须1–5')
    if any(coaching.inventory_mutation(action) for action in actions) and len(actions) != 1:
        raise ValueError('库存改变须独立单动作；重新定位后再操作下一件，禁止沿用旧坐标')
    if (len(actions) != 1 and any(action['type'] == 'click_point'
            and action.get('expected_page') in ('preparation', 'shop', 'unit_gear') for action in actions)):
        raise ValueError('备战库存区域坐标动作须单次回读，不在同批沿用可能重排的坐标')
    if any(action['type'] == 'buy_xp' and action['count'] != 1 for action in actions):
        raise ValueError('经验购买须一次一笔，回读新资源后重新决策')
    if sum(action.get('count', 1) if action['type'] == 'buy_xp' else 1 for action in actions) > 16:
        raise ValueError('单战略计划底层操作预算超过16')
    return reply


def _stable_navigation_anchor(original, actual, label, bounds):
    matches = []
    for observation in (original, actual):
        found = []
        for row in observation.get('rows', []):
            box, confidence = row.get('box'), row.get('confidence')
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(edge) is not int for edge in box)
                    or type(confidence) not in (int, float) or not .90 <= confidence <= 1.
                    or clean(row.get('text', '')).strip('·•「」『』') != label):
                continue
            if (bounds[0] <= box[0] < box[2] <= bounds[2]
                    and bounds[1] <= box[1] < box[3] <= bounds[3]):
                found.append(box)
        if len(found) != 1:
            return False
        matches.append(found[0])
    return all(abs(before - after) <= 12 for before, after in zip(*matches))


def stable_world_menu_navigation(reply, request, actual):
    """Eligibility only: one Escape menu key with two stable, locally read world HUD anchors."""
    original = request.get('observation', {})
    actions = reply.get('actions')
    if (request.get('kind') != 'unknown_page'
            or original.get('page') != 'unknown' or actual.get('page') != 'unknown'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    action = actions[0]
    if (not isinstance(action, dict) or action.get('type') != 'key'
            or action.get('expected_page') != 'unknown'
            or action.get('args') != [27]
            or type(action['args'][0]) is not int):
        return False

    # Only these labels/1920x1080 HUD locations were read from the current
    # original world frame; UID/Enter or text elsewhere cannot establish it.
    return (_stable_navigation_anchor(original, actual, '开拓之尾号', (20, 0, 330, 100))
            and (_stable_navigation_anchor(original, actual, '差分宇宙', (1080, 520, 1440, 710))
                 or _stable_navigation_anchor(original, actual, '参加航线会议，决定列车的下一站', (20, 260, 500, 410))))


def stable_phone_guide_navigation(reply, request, actual):
    """Eligibility only: the one observed phone-menu guide tile, never generic unknown clicks."""
    original = request.get('observation', {})
    actions = reply.get('actions')
    if (request.get('kind') != 'unknown_page'
            or original.get('page') != 'unknown' or actual.get('page') != 'unknown'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or reply.get('snapshot_id') != request.get('snapshot_id')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    action = actions[0]
    if (not isinstance(action, dict) or action.get('type') != 'click_text'
            or action.get('text') != '指南' or action.get('exact') is not True
            or action.get('expected_page') != 'unknown'):
        return False
    bounds = action.get('bounds')
    if (not isinstance(bounds, list) or bounds != [1260, 710, 1405, 830]
            or any(type(edge) is not int for edge in bounds)):
        return False
    return all(_stable_navigation_anchor(original, actual, label, roi) for label, roi in (
        ('指南', (1280, 775, 1400, 825)),
        ('联机玩法', (1410, 770, 1560, 830)),
        ('教学目录', (1660, 770, 1810, 830)),
    ))


def stable_peace_guide_tab_navigation(reply, request, actual):
    """Eligibility only: three fixed guide icons bound to their observed source pages."""
    original = request.get('observation', {})
    actions = reply.get('actions')
    if (request.get('kind') != 'unknown_page'
            or original.get('page') != 'unknown' or actual.get('page') != 'unknown'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or reply.get('snapshot_id') != request.get('snapshot_id')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    action = actions[0]
    if (not isinstance(action, dict) or action.get('type') != 'click_point'
            or action.get('expected_page') != 'unknown'):
        return False
    proof = action.get('target_evidence')
    if (not isinstance(proof, dict)
            or proof.get('snapshot_id') != request.get('snapshot_id')):
        return False
    if proof.get('control_id') == 'peace_guide_tab_4':
        point, roi, caption, detail_label, detail_roi = [720, 212], [670, 174, 785, 251], '生存索引', '规则说明', (1080, 25, 1280, 110)
    elif proof.get('control_id') == 'peace_guide_tab_5':
        point, roi, caption, detail_label, detail_roi = [840, 212], [790, 174, 900, 251], '逐光捡金', '规则说明', (1600, 25, 1800, 110)
    elif proof.get('control_id') == 'peace_guide_cosmic_strife_tab':
        point, roi, caption, detail_label, detail_roi = [600, 212], [550, 174, 650, 251], '开拓历程', '第一幕·雅利洛-VI', (280, 415, 610, 490)
    else:
        return False
    if action.get('args') != point or any(type(value) is not int for value in action['args']):
        return False
    bounds = proof.get('bounds')
    if (not isinstance(bounds, list) or bounds != roi
            or any(type(edge) is not int for edge in bounds)):
        return False
    return all(_stable_navigation_anchor(original, actual, label, roi) for label, roi in (
        ('星际和平指南', (80, 25, 280, 80)),
        (caption, (80, 60, 260, 115)),
        (detail_label, detail_roi),
    ))


def stable_advantages_navigation(reply, request, actual):
    """Eligibility only: fixed native tabs or one guarded exit from advantages."""
    original = request.get('observation', {})
    actions = reply.get('actions')
    if (original.get('page') != 'advantages' or actual.get('page') != 'advantages'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or reply.get('snapshot_id') != request.get('snapshot_id')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    action = actions[0]
    if not isinstance(action, dict) or action.get('expected_page') != 'advantages':
        return False
    if action.get('type') == 'click_text':
        tabs = {'常驻优势': [560, 90, 960, 155], '赛季优势': [965, 90, 1340, 155]}
        text, bounds = action.get('text'), action.get('bounds')
        if (request.get('kind') != 'post_match_advantages' or action.get('exact') is not True
                or not isinstance(text, str) or text not in tabs
                or not isinstance(bounds, list) or bounds != tabs[text]
                or any(type(edge) is not int for edge in bounds)):
            return False
    elif action.get('type') == 'key':
        if (request.get('kind') not in ('unknown_page', 'post_match_advantages')
                or action.get('args') != [27] or type(action['args'][0]) is not int
                or action.get('guard_texts') != ['货币战争', '优势布局', '常驻优势', '赛季优势']):
            return False
    else:
        return False
    return all(_stable_navigation_anchor(original, actual, label, roi) for label, roi in (
        ('货币战争', (80, 20, 240, 80)),
        ('优势布局', (80, 55, 250, 110)),
        ('常驻优势', (600, 90, 960, 160)),
        ('赛季优势', (965, 90, 1340, 160)),
    ))


def stable_inspection_completion(reply, request, actual):
    """Eligibility only: record this request's panel evidence without any input."""
    original = request.get('observation', {})
    actions = reply.get('actions')
    if (original.get('snapshot_id') != request.get('snapshot_id')
            or reply.get('snapshot_id') != request.get('snapshot_id')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    action = actions[0]
    if not isinstance(action, dict) or action.get('type') != 'finish_inspection':
        return False
    panel, result = action.get('panel'), action.get('result')
    if panel not in ('bonds', 'income', 'promotion', 'advantages'):
        return False
    return (request.get('kind') == 'post_match_' + panel
            and original.get('page') == panel and actual.get('page') == panel
            and isinstance(result, dict) and isinstance(request.get('evidence_file'), str)
            and bool(request['evidence_file']) and result.get('evidence') == request['evidence_file'])


def stable_lobby_entry_navigation(reply, request, actual, current_png):
    """Eligibility only: one native start button with exact retained pixels."""
    original = request.get('observation', {})
    actions = reply.get('actions')
    if (request.get('kind') != 'new_match'
            or original.get('page') != 'lobby' or actual.get('page') != 'lobby'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or reply.get('snapshot_id') != request.get('snapshot_id')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    action = actions[0]
    if (not isinstance(action, dict) or action.get('type') != 'click_text'
            or action.get('text') != '开始「货币战争」' or action.get('exact') is not True
            or action.get('expected_page') != 'lobby'):
        return False
    button_roi = (1360, 930, 1810, 1020)
    bounds = action.get('bounds')
    if (not isinstance(bounds, list) or bounds != list(button_roi)
            or any(type(edge) is not int for edge in bounds)):
        return False
    # Keep the complete button text, including its closing native quote.
    for label, roi in (
            ('货币战争', (35, 70, 220, 125)),
            ('零和博弈', (35, 105, 330, 180)),
            ('创业指南', (80, 240, 270, 310)),
            ('晋升等级', (1450, 270, 1750, 330)),
            ('开始「货币战争」', button_roi)):
        boxes = []
        for observation in (original, actual):
            found = []
            for row in observation.get('rows', []):
                box, confidence = row.get('box'), row.get('confidence')
                if (not isinstance(box, list) or len(box) != 4
                        or any(type(edge) is not int for edge in box)
                        or type(confidence) not in (int, float) or not .90 <= confidence <= 1.
                        or clean(row.get('text', '')) != label):
                    continue
                if (roi[0] <= box[0] < box[2] <= roi[2]
                        and roi[1] <= box[1] < box[3] <= roi[3]):
                    found.append(box)
            if len(found) != 1:
                return False
            boxes.append(found[0])
        if any(abs(before - after) > 12 for before, after in zip(*boxes)):
            return False
    try:
        from io import BytesIO
        from PIL import Image
        original_bytes = Path(request.get('original_png')).read_bytes()
        actual_bytes = Path(current_png).read_bytes()
        if (hashlib.sha256(original_bytes).hexdigest() != request.get('snapshot_id')
                or hashlib.sha256(actual_bytes).hexdigest() != actual.get('snapshot_id')):
            return False
        with Image.open(BytesIO(original_bytes)) as old, Image.open(BytesIO(actual_bytes)) as fresh:
            if old.format != 'PNG' or fresh.format != 'PNG' or old.size != (1920, 1080) or fresh.size != (1920, 1080):
                return False
            return old.crop(button_roi).convert('RGB').tobytes() == fresh.crop(button_roi).convert('RGB').tobytes()
    except (OSError, ValueError, TypeError):
        return False


def stable_standard_entry_navigation(reply, request, actual, current_png):
    """Eligibility only: the fixed standard-mode entry with exact text masks."""
    original = request.get('observation', {})
    actions = reply.get('actions')
    if (request.get('kind') != 'unknown_page'
            or original.get('page') != 'unknown' or actual.get('page') != 'unknown'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or reply.get('snapshot_id') != request.get('snapshot_id')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    action = actions[0]
    if (not isinstance(action, dict) or action.get('type') != 'click_text'
            or action.get('text') != '进入标准博弈' or action.get('exact') is not True
            or action.get('expected_page') != 'unknown'):
        return False
    button_roi = (1400, 910, 1885, 1010)
    inner_roi = (1545, 937, 1740, 985)
    bounds = action.get('bounds')
    if (not isinstance(bounds, list) or bounds != list(button_roi)
            or any(type(edge) is not int for edge in bounds)):
        return False
    for label, roi in (
            ('货币战争', (80, 20, 240, 85)),
            ('标准博弈', (570, 120, 1000, 190)),
            ('通关可获得积分', (590, 435, 820, 500)),
            ('通关可获得晋升点', (590, 495, 875, 565)),
            ('进入标准博弈', button_roi)):
        if not _stable_navigation_anchor(original, actual, label, roi):
            return False
    rank_roi = (75, 285, 470, 345)
    ranks = []
    for observation in (original, actual):
        found = []
        for row in observation.get('rows', []):
            box, confidence = row.get('box'), row.get('confidence')
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(edge) is not int for edge in box)
                    or type(confidence) not in (int, float) or not .90 <= confidence <= 1.
                    or not clean(row.get('text', '')).startswith('当前职级')):
                continue
            if (rank_roi[0] <= box[0] < box[2] <= rank_roi[2]
                    and rank_roi[1] <= box[1] < box[3] <= rank_roi[3]):
                found.append(row)
        if len(found) != 1:
            return False
        ranks.append(found[0])
    rank_label = clean(ranks[0]['text'])
    if (not rank_label[len('当前职级'):].lstrip(':：')
            or clean(ranks[1]['text']) != rank_label or ranks[0]['box'] != ranks[1]['box']
            or not _stable_navigation_anchor(original, actual, rank_label, rank_roi)):
        return False
    for observation in (original, actual):
        target = find_text(observation.get('rows', []), '进入标准博弈', button_roi, exact=True)
        if target is None or target['confidence'] < .90:
            return False
        box = target['box']
        if not (inner_roi[0] <= box[0] < box[2] <= inner_roi[2]
                and inner_roi[1] <= box[1] < box[3] <= inner_roi[3]):
            return False
    try:
        from io import BytesIO
        from PIL import Image
        original_bytes = Path(request.get('original_png')).read_bytes()
        actual_bytes = Path(current_png).read_bytes()
        if (hashlib.sha256(original_bytes).hexdigest() != request.get('snapshot_id')
                or hashlib.sha256(actual_bytes).hexdigest() != actual.get('snapshot_id')):
            return False
        with Image.open(BytesIO(original_bytes)) as old, Image.open(BytesIO(actual_bytes)) as fresh:
            if old.format != 'PNG' or fresh.format != 'PNG' or old.size != (1920, 1080) or fresh.size != (1920, 1080):
                return False
            def masks(image):
                rgb = image.crop(inner_roi).convert('RGB').tobytes()
                pixels = list(zip(rgb[::3], rgb[1::3], rgb[2::3]))
                black = bytes(r < 80 and g < 80 and b < 80 for r, g, b in pixels)
                white = bytes(r > 200 and g > 200 and b > 200 for r, g, b in pixels)
                return black, white

            old_black, old_white = masks(old)
            fresh_black, fresh_white = masks(fresh)
            return (old_black == fresh_black and old_white == fresh_white
                    and .02 <= sum(old_black) / len(old_black) <= .35
                    and .55 <= sum(old_white) / len(old_white) <= .98)
    except (OSError, ValueError, TypeError):
        return False


def stable_plane_intro_navigation(reply, request, actual, current_png):
    """Eligibility only: the fixed continue prompt on the first plane introduction."""
    original = request.get('observation', {})
    actions = reply.get('actions')
    if (request.get('kind') != 'unknown_page'
            or original.get('page') != 'unknown' or actual.get('page') != 'unknown'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or reply.get('snapshot_id') != request.get('snapshot_id')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    action = actions[0]
    if (not isinstance(action, dict) or action.get('type') != 'click_text'
            or action.get('text') != '点击空白处继续' or action.get('exact') is not True
            or action.get('expected_page') != 'unknown'
            or action.get('guard_texts') != ['点击空白处继续', '位面']):
        return False
    bounds = action.get('bounds')
    if (not isinstance(bounds, list) or bounds != [810, 928, 1110, 995]
            or any(type(edge) is not int for edge in bounds)):
        return False
    if not all(_stable_navigation_anchor(original, actual, label, roi) for label, roi in (
            ('1', (350, 350, 520, 540)),
            ('2', (900, 405, 1020, 535)),
            ('3', (1415, 405, 1545, 535)),
            ('点击空白处继续', (845, 925, 1090, 995)))):
        return False
    # This prompt pulses with the background; retain exact frame provenance
    # and fixed OCR anchors without treating its brightness as a stable pixel.
    try:
        from io import BytesIO
        from PIL import Image
        original_bytes = Path(request.get('original_png')).read_bytes()
        actual_bytes = Path(current_png).read_bytes()
        if (hashlib.sha256(original_bytes).hexdigest() != request.get('snapshot_id')
                or hashlib.sha256(actual_bytes).hexdigest() != actual.get('snapshot_id')):
            return False
        with Image.open(BytesIO(original_bytes)) as old, Image.open(BytesIO(actual_bytes)) as fresh:
            return (old.format == fresh.format == 'PNG'
                    and old.size == fresh.size == (1920, 1080))
    except (OSError, ValueError, TypeError):
        return False


def stable_tracking_selector_portrait_navigation(action, request, actual):
    """Eligibility only: view the fixed first portrait's details, without asserting its name."""
    original = request.get('observation', {})
    if (request.get('kind') != 'guide_strategy'
            or original.get('page') != 'guide' or actual.get('page') != 'guide'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or action.get('type') != 'click_point' or action.get('expected_page') != 'guide'
            or action.get('args') != [625, 432]
            or any(type(value) is not int for value in action['args'])):
        return False
    proof = action.get('target_evidence')
    if (not isinstance(proof, dict)
            or proof.get('control_id') != 'gear_tracking_recommendation_portrait_1'
            or proof.get('snapshot_id') != request.get('snapshot_id') or 'text' in proof
            or not isinstance(proof.get('bounds'), list) or proof['bounds'] != [583, 394, 667, 501]
            or any(type(edge) is not int for edge in proof['bounds'])):
        return False
    for label, bounds in (
            ('装备追踪', (540, 240, 710, 305)),
            ('当前攻略包含角色装备推荐，请选择要追踪装备的角色', (690, 315, 1230, 365)),
            ('优选装备', (670, 385, 840, 445)),
            ('暂未获取', (575, 460, 680, 505))):
        matched = []
        for observation in (original, actual):
            found = []
            for row in observation.get('rows', []):
                box, confidence = row.get('box'), row.get('confidence')
                if (not isinstance(box, list) or len(box) != 4
                        or any(type(edge) is not int for edge in box)
                        or type(confidence) not in (int, float) or not .90 <= confidence <= 1.
                        or clean(row.get('text', '')) != label):
                    continue
                if (bounds[0] <= box[0] < box[2] <= bounds[2]
                        and bounds[1] <= box[1] < box[3] <= bounds[3]):
                    found.append(box)
            if len(found) != 1:
                return False
            matched.append(found[0])
        if any(abs(before - after) > 2 for before, after in zip(*matched)):
            return False
    return True


_FREE_LINEUP_PROFILES = {
    'prep_stage_1_1_bench_4_to_front_1': {
        'stage': '1-1', 'deployed': '0/3', 'args': [812, 910, 745, 400],
        'source_bounds': [756, 845, 870, 979], 'destination_bounds': [674, 326, 814, 472],
        'stage_bounds': (420, 60, 520, 105), 'exact_count_box': True,
        'source_png': PROJECT / 'tools' / 'free_lineup_resources' / 'free-lineup-7c44-bridge-driver-source4-original.png',
        'source_png_sha': '841d14f13fa0cc6499d026372a5b094a4a252ed5d503ddbf02b7971579d5e0b9',
        'source_rgb_sha': '8fce993509ad52b84559659ea864be049f1b74f854b62ba568c3ac68c1a2abec',
        'source_max': 2, 'empty_png': None,
        'polygon': ((690, 330), (809, 330), (802, 467), (679, 467)),
        'support_count': 496,
        'support_sha': '5855ff55c024e955a87a42b34913bfac28d8a7eac885eeb8580fb67e1460ac80',
    },
    'prep_stage_1_2_bench_1_to_front_2': {
        'stage': '1-2', 'deployed': '1/3', 'args': [440, 910, 886, 400],
        'source_bounds': [383, 845, 497, 979], 'destination_bounds': [820, 326, 955, 472],
        'stage_bounds': (420, 50, 520, 105), 'exact_count_box': False,
        'source_png': PROJECT / 'tools' / 'free_lineup_resources' / 'free-lineup-stage1-2-bridge-driver-source1-original.png',
        'source_png_sha': 'ab2c6e484f12470bacb1c5d1de1928b0e572f4ed6f6b895c0777e6526ab25494',
        'source_rgb_sha': '573a24e9c5c5f5df2ca09e4d14f8853f222ca1fd55000817e385d5027417d655',
        'source_max': 3,
        'empty_png': PROJECT / 'tools' / 'free_lineup_resources' / 'free-lineup-stage1-2-bridge-driver-front2_empty-original.png',
        'empty_png_sha': '4b03534d2c2cf57e75d820397eaba24f8927bd8fd10ed592e4fb1da1185b9b4d',
        'empty_rgb_sha': 'bb33cbd55d1a81b5f93e9d781ebc106a3d4ac07325b2203bfe87c391b0569258',
        'polygon': ((830, 330), (949, 330), (949, 467), (824, 467)),
        'support_count': 498,
        'support_sha': 'f02d9affc8575d51a2c7589e3b6a0b7f0c92d77889d051bf22bc06271d9ef441',
    },
    'prep_stage_1_2_bench_3_to_back_1': {
        'stage': '1-2', 'deployed': '2/3', 'args': [687, 910, 605, 670],
        'source_bounds': [630, 845, 745, 979], 'destination_bounds': [532, 596, 678, 743],
        'stage_bounds': (420, 50, 520, 105), 'exact_count_box': False,
        'source_png': PROJECT / 'tools' / 'free_lineup_resources' / 'stage12-source3-real-2-of-3-fresh.png',
        'source_png_sha': '00830e17e252214d36195e5869d3af86a1624fa8dca46b73d75df34500ba164e',
        'source_rgb_sha': '0ff6255f2f51fb8d20739909a499a64b52526d418829266fb8b5fc2b4db40b4f',
        'source_max': 6,
        'empty_png': PROJECT / 'tools' / 'free_lineup_resources' / 'free-lineup-stage1-2-back1-complete-band-original.png',
        'empty_png_sha': '3da49f6483f84eed11b31f33cb4bf797b840ceb165e36a74ebe731fb9c7f459e',
        'empty_rgb_sha': '7c2c88890c664d044bbd08e367e525a2cf5b230c57f1ceb0df908798cae6a96f',
        'polygon': ((551, 601), (673, 601), (664, 738), (535, 738)),
        'support_count': 505,
        'support_sha': 'cc62ef8b3203b9cbd031318060d68e5f08587cc56c90c70b74355cee96e20413',
    },
    'prep_stage_1_6_bench_1_to_back_2': {
        'stage': '1-6', 'deployed': '3/4', 'hp': '81', 'args': [440, 910, 750, 670],
        'source_bounds': [383, 845, 497, 979], 'destination_bounds': [675, 596, 824, 743],
        'stage_bounds': (420, 50, 520, 105), 'exact_count_box': False,
        'source_png': PROJECT / 'tools' / 'free_lineup_resources' / 'free-lineup-stage1-6-bench1-original.png',
        'source_png_sha': 'd21eb562bbdf1c758740472c81ebfa864758be9e1777417bb6335a9e2b87c3f1',
        'source_rgb_sha': '0f2f75eea7fde8863dcee724420546a910c5b34b4a045ac3f73de6db6ee76836',
        'source_max': 2,
        'empty_png': PROJECT / 'tools' / 'free_lineup_resources' / 'free-lineup-stage1-6-back2-empty-original.png',
        'empty_png_sha': '1cd8f7d7e387eb1125b819cd36b865023bbb2677c09c6564af8d3bf9e3c6e93a',
        'empty_rgb_sha': '3242416e15dd2c468d6f0371850f96d3be2f84f77f880d2cce9faa5aeca62ed9',
        'polygon': ((690, 601), (812, 601), (806, 738), (679, 738)),
        'support_count': 503,
        'support_sha': '758dff56f63fd58f70055a1a1723f6f5e57de1e160ea3e6ceebed7a6762d31e0',
    },
    'prep_stage_1_6_bench_4_to_back_2': {
        'stage': '1-6', 'deployed': '3/4', 'hp': '81', 'args': [812, 910, 750, 670],
        'source_bounds': [756, 845, 870, 979], 'destination_bounds': [675, 596, 824, 743],
        'stage_bounds': (420, 50, 520, 105), 'exact_count_box': False,
        'source_png': PROJECT / 'tools' / 'free_lineup_resources' / 'free-lineup-stage1-6-bench4-original.png',
        'source_png_sha': '7cb4c42d4781b430d75b8a9347e30a83b5e122a969d190cc5f200f3453880bfc',
        'source_rgb_sha': '49842b39ae7c82c16bffd4c17d0cc76d7e731d4a419b9efceb7b4e3ce7e5d8a9',
        'source_max': 3,
        'empty_png': PROJECT / 'tools' / 'free_lineup_resources' / 'free-lineup-stage1-6-back2-empty-original.png',
        'empty_png_sha': '1cd8f7d7e387eb1125b819cd36b865023bbb2677c09c6564af8d3bf9e3c6e93a',
        'empty_rgb_sha': '3242416e15dd2c468d6f0371850f96d3be2f84f77f880d2cce9faa5aeca62ed9',
        'polygon': ((690, 601), (812, 601), (806, 738), (679, 738)),
        'support_count': 503,
        'support_sha': '758dff56f63fd58f70055a1a1723f6f5e57de1e160ea3e6ceebed7a6762d31e0',
    },
    'prep_stage_1_8_bench_3_to_front_3': {
        'stage': '1-8', 'deployed': '4/5', 'hp': '75', 'args': [687, 910, 1030, 400],
        'count_min_y': 204, 'count_top_drift': 6,
        'source_bounds': [630, 845, 745, 979], 'destination_bounds': [964, 326, 1106, 473],
        'stage_bounds': (420, 50, 520, 105), 'exact_count_box': False,
        'source_png': PROJECT / 'tools' / 'free_lineup_resources' / 'free-lineup-stage1-8-bench3-original.png',
        'source_png_sha': '09a65ae55062c3f99662ec3d3d7f0cb52e7ba90ee47e288b794bf30b7852505e',
        'source_rgb_sha': '2e9c9068149529832708bc7e9942ee233590e7ee25db607821deafd7afe65a6c',
        'source_max': 3,
        'empty_png': PROJECT / 'tools' / 'free_lineup_resources' / 'free-lineup-stage1-8-front3-empty-original.png',
        'empty_png_sha': '8b0ab9deff466e87914624e04d3bf1e4bfdb653c5d1ad4bc2d9a363d85dc8c5b',
        'empty_rgb_sha': '83b91308b80188258258ae4766636b945097000304f2c84a2a92d827781d9b84',
        'polygon': ((972, 330), (1092, 330), (1099, 467), (970, 467)),
        'support_count': 503,
        'support_sha': '277eb89578e53c8449a7bc6625316c19fd5c5025245c13d0663a9dfbaef28f97',
    },
}


def stable_initial_free_lineup_navigation(reply, request, actual, current_png):
    """Only calibrated, unnamed bench-to-empty-cell profiles are eligible."""
    original = request.get('observation', {})
    actions = reply.get('actions')
    if (request.get('kind') != 'preparation_strategy'
            or original.get('page') != 'preparation' or actual.get('page') != 'preparation'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or reply.get('snapshot_id') != request.get('snapshot_id')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    action = actions[0]
    if (not isinstance(action, dict) or action.get('type') != 'drag'
            or action.get('expected_page') != 'preparation'
            or not isinstance(action.get('args'), list)
            or any(type(value) is not int for value in action['args'])):
        return False
    proof = action.get('target_evidence')
    if (not isinstance(proof, dict)
            or set(proof) != {'control_id', 'snapshot_id', 'bounds', 'destination_bounds'}
            or not isinstance(proof['control_id'], str) or proof['control_id'] not in _FREE_LINEUP_PROFILES
            or proof['snapshot_id'] != request.get('snapshot_id')
            or not isinstance(proof['bounds'], list) or not isinstance(proof['destination_bounds'], list)
            or any(type(edge) is not int for edge in proof['bounds'] + proof['destination_bounds'])):
        return False
    profile = _FREE_LINEUP_PROFILES[proof['control_id']]
    if (action['args'] != profile['args'] or proof['bounds'] != profile['source_bounds']
            or proof['destination_bounds'] != profile['destination_bounds']):
        return False
    count_boxes = []
    for observation in (original, actual):
        fields = observation.get('fields', {})
        if fields.get('stage') != profile['stage'] or fields.get('deployed') != profile['deployed']:
            return False
        counts = []
        for row in observation.get('rows', []):
            box, confidence = row.get('box'), row.get('confidence')
            if (clean(row.get('text', '')) not in (profile['deployed'], 'i' + profile['deployed'])
                    or not isinstance(box, list) or len(box) != 4 or any(type(edge) is not int for edge in box)
                    or type(confidence) not in (int, float) or not .90 <= confidence <= 1.):
                continue
            if (box == [846, 210, 1029, 280] if profile['exact_count_box'] else
                    846 <= box[0] < box[2] <= 1029 and profile.get('count_min_y', 210) <= box[1] < box[3] <= 280):
                counts.append(box)
        if len(counts) != 1:
            return False
        count_boxes.append(counts[0])
    if any(abs(a - b) > (profile.get('count_top_drift', 2) if edge == 1 else 2)
            for edge, (a, b) in enumerate(zip(*count_boxes))):
        return False
    for label, bounds in (
            ('备战阶段', (410, 20, 540, 65)), (profile['stage'], profile['stage_bounds']),
            (profile.get('hp', '100'), (1400, 45, 1500, 105)), ('出战', (1760, 710, 1875, 790)),
            ('商店', (1575, 950, 1675, 1020))):
        matched = []
        for observation in (original, actual):
            found = []
            for row in observation.get('rows', []):
                box, confidence = row.get('box'), row.get('confidence')
                if (not isinstance(box, list) or len(box) != 4
                        or any(type(edge) is not int for edge in box)
                        or type(confidence) not in (int, float) or not .90 <= confidence <= 1.
                        or clean(row.get('text', '')) != label):
                    continue
                if (bounds[0] <= box[0] < box[2] <= bounds[2]
                        and bounds[1] <= box[1] < box[3] <= bounds[3]):
                    found.append(box)
            if len(found) != 1:
                return False
            matched.append(found[0])
        if any(abs(a - b) > 2 for a, b in zip(*matched)):
            return False
    try:
        from io import BytesIO
        from PIL import Image, ImageChops
        old_bytes = Path(request.get('original_png')).read_bytes()
        fresh_bytes = Path(current_png).read_bytes()
        if (hashlib.sha256(old_bytes).hexdigest() != request.get('snapshot_id')
                or hashlib.sha256(fresh_bytes).hexdigest() != actual.get('snapshot_id')):
            return False

        def load_template(path, png_sha, rgb_sha, bounds):
            payload = Path(path).read_bytes()
            if hashlib.sha256(payload).hexdigest() != png_sha:
                raise ValueError('固定模板PNG已变更')
            with Image.open(BytesIO(payload)) as image:
                if image.format != 'PNG' or image.size != (bounds[2] - bounds[0], bounds[3] - bounds[1]):
                    raise ValueError('固定模板格式/完整尺寸不符')
                rgb = image.convert('RGB')
                if hashlib.sha256(rgb.tobytes()).hexdigest() != rgb_sha:
                    raise ValueError('固定模板RGB已变更')
                return rgb

        template = load_template(profile['source_png'], profile['source_png_sha'], profile['source_rgb_sha'], profile['source_bounds'])
        with Image.open(BytesIO(old_bytes)) as old, Image.open(BytesIO(fresh_bytes)) as fresh:
            if (old.format != 'PNG' or fresh.format != 'PNG'
                    or old.size != (1920, 1080) or fresh.size != (1920, 1080)):
                return False
            source = [image.crop(profile['source_bounds']).convert('RGB') for image in (old, fresh)]
            # Full occupied tiles: the original profile retains max2; only the
            # stage1-2 templates admit measured max3/max4 quantization respectively.
            for before, after in ((source[0], template), (source[1], template), tuple(source)):
                delta = ImageChops.difference(before, after)
                red, green, blue = delta.split()
                maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
                pixels = template.width * template.height
                if (sum(maximum[profile['source_max'] + 1:]) or (pixels - maximum[0]) / pixels > .02
                        or sum((i % 256) * count for i, count in enumerate(delta.histogram())) / (3 * pixels) > .02):
                    return False

            if profile['empty_png'] is not None:
                empty = load_template(profile['empty_png'], profile['empty_png_sha'], profile['empty_rgb_sha'], profile['destination_bounds'])
                destination = [image.crop(profile['destination_bounds']).convert('RGB') for image in (old, fresh)]
                # Edges alone also pass an occupied negative. Both full target
                # tiles and their pair must match the genuine empty template.
                for before, after in ((destination[0], empty), (destination[1], empty), tuple(destination)):
                    delta = ImageChops.difference(before, after)
                    red, green, blue = delta.split()
                    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
                    pixels = empty.width * empty.height
                    if (sum(maximum[97:]) or sum(maximum[33:]) / pixels > .10
                            or sum((i % 256) * count for i, count in enumerate(delta.histogram())) / (3 * pixels) > 8):
                        return False

            def fixed_empty_support(image):
                rgb = image.convert('RGB')
                data = rgb.load()
                support = bytearray()
                top_left, top_right, bottom_right, bottom_left = profile['polygon']
                # Code-owned polygons with +/-3px normal bands; no registration,
                # blur, or threshold search. The original 496 samples are unchanged.
                for axis, samples, edge in (
                        ('y', range(top_left[0] + 3, top_right[0] - 2), lambda position: top_left[1]),
                        ('y', range(bottom_left[0] + 3, bottom_right[0] - 2), lambda position: bottom_left[1]),
                        ('x', range(top_left[1] + 3, bottom_left[1] - 2), lambda position: round(top_left[0] + (bottom_left[0] - top_left[0]) * (position - top_left[1]) / (bottom_left[1] - top_left[1]))),
                        ('x', range(top_right[1] + 3, bottom_right[1] - 2), lambda position: round(top_right[0] + (bottom_right[0] - top_right[0]) * (position - top_right[1]) / (bottom_right[1] - top_right[1])))):
                    for position in samples:
                        peaks = []
                        for offset in range(-3, 4):
                            ridge = edge(position) + offset
                            if axis == 'y':
                                first, second = data[position, ridge - 1], data[position, ridge + 1]
                            else:
                                first, second = data[ridge - 1, position], data[ridge + 1, position]
                            peaks.append(max(abs(a - b) for a, b in zip(first, second)))
                        support.append(max(peaks) >= 32)
                return bytes(support)

            old_support, fresh_support = fixed_empty_support(old), fixed_empty_support(fresh)
            # Initial vacancy still requires 0/3. Other profiles additionally require
            # both complete target tiles to match the pinned empty template.
            return (len(old_support) == len(fresh_support) == profile['support_count'] and old_support == fresh_support
                    and hashlib.sha256(old_support).hexdigest() == profile['support_sha'])
    except (OSError, ValueError, TypeError):
        return False


_NATIVE_LOOT_PICKUP_PROFILES = {
    'native_preparation_loot_blue_1': {
        'control_id': 'native_preparation_loot_blue_1', 'args': [1397, 287],
        'bounds': [1310, 250, 1608, 465], 'circle_bounds': [1360, 250, 1434, 324],
        'template': PROJECT / 'tools' / 'loot_resources' / 'native-preparation-blue-loot-1.png',
        'png_sha': '0dd17e4f7b5b4dbc0ef70b633cb600a94a469cb8f6e352e5ee18ce36f75bf5ce',
        'rgb_sha': '2ce0c9c635306a81ef970af0d242eb60a278a941e4a4211a0975c95cd2f2b4aa',
    },
    'native_preparation_loot_blue_2': {
        'control_id': 'native_preparation_loot_blue_2', 'args': [1541, 321],
        'bounds': [1308, 250, 1612, 468], 'circle_bounds': [1504, 284, 1578, 358],
        'template': PROJECT / 'tools' / 'loot_resources' / 'native-preparation-blue-loot-2.png',
        'png_sha': 'a16d1d6edfdb359d1f1a77d2299c35a5027ee7bab638a56adf02d1d1eeeaebfa',
        'rgb_sha': 'de3fb56b07e6d4d220ea5c42083ef00fd4906af2252873bf832dee58d21ecd92',
    },
    'native_preparation_loot_blue_3': {
        'control_id': 'native_preparation_loot_blue_3', 'args': [1363, 406],
        'bounds': [1308, 250, 1612, 468], 'circle_bounds': [1326, 369, 1400, 443],
        'template': PROJECT / 'tools' / 'loot_resources' / 'native-preparation-blue-loot-3.png',
        'png_sha': '70512c3cbe6e9d54e9b45d4cde930be828ca284b197ba2da2768f6813a9d8217',
        'rgb_sha': 'cb1815193745761f3b2b7ce35f595ad5257ccd54ad22b24c896240aaf10b048a',
    },
    'native_preparation_loot_blue_4': {
        'control_id': 'native_preparation_loot_blue_4', 'args': [1573, 387],
        'bounds': [1308, 250, 1612, 468], 'circle_bounds': [1536, 350, 1610, 424],
        'template': PROJECT / 'tools' / 'loot_resources' / 'native-preparation-blue-loot-4.png',
        'png_sha': '42b2448b166fe72c5cc04897cc5cff509041eb0353f655fee32bb4a3f6f05fe2',
        'rgb_sha': 'b6395e61f7755d8617e6a9cd20e1826206822719feeaf91a5ad135c5cbe0d4b4',
    },
    'native_preparation_loot_gray_1': {
        'control_id': 'native_preparation_loot_gray_1', 'args': [1333, 286],
        'bounds': [1308, 250, 1612, 468], 'circle_bounds': [1309, 262, 1357, 310],
        'template': PROJECT / 'tools' / 'loot_resources' / 'native-preparation-gray-loot-1.png',
        'png_sha': '55189cc98c57af7b61d204e04eb585f9f6c01541d763c9eaa10d3bb8da5c7a9a',
        'rgb_sha': '115b91090e496a78eae4aa2e5e6e1f205aa2863cbafaa315a774571e1f6d519a',
    },
    'native_preparation_loot_gray_2': {
        'control_id': 'native_preparation_loot_gray_2', 'args': [1472, 300],
        'bounds': [1308, 250, 1612, 468], 'circle_bounds': [1448, 276, 1496, 324],
        'template': PROJECT / 'tools' / 'loot_resources' / 'native-preparation-gray-loot-2.png',
        'png_sha': '2a08574a99f0a7a6728649857c97f578f84681aae859e6baa4bf4a9cfba3a69d',
        'rgb_sha': 'e49bf54a536e2984c8f7b9b5e94a4bc72f8ef7af1c6657475df477a24d83d7e9',
    },
    'native_preparation_loot_gray_3': {
        'control_id': 'native_preparation_loot_gray_3', 'args': [1348, 333],
        'bounds': [1308, 250, 1612, 468], 'circle_bounds': [1324, 309, 1372, 357],
        'template': PROJECT / 'tools' / 'loot_resources' / 'native-preparation-gray-loot-3.png',
        'png_sha': '9fca5185f585e7e7603cba7cb0e84ea903807b893c796f77857fb5eac37dd92f',
        'rgb_sha': 'a66136cc40e9a2be40bfc00f531c6c1882d5542ba42802033e9e130c13a0b2ca',
    },
    'native_preparation_loot_gray_4': {
        'control_id': 'native_preparation_loot_gray_4', 'args': [1433, 336],
        'bounds': [1308, 250, 1612, 468], 'circle_bounds': [1409, 312, 1457, 360],
        'template': PROJECT / 'tools' / 'loot_resources' / 'native-preparation-gray-loot-4.png',
        'png_sha': 'da1d843f88f4cce21f7b66c8377c83c11f4e9c1a3275035a77a0b5a99a63ca9b',
        'rgb_sha': 'c6bb1e5dcdf43ab396bde20aa6cf046d424fa868832ca83e24a7d4811cb121ec',
    },
    'native_preparation_loot_gray_5': {
        'control_id': 'native_preparation_loot_gray_5', 'args': [1483, 404],
        'bounds': [1308, 250, 1612, 468], 'circle_bounds': [1459, 380, 1507, 428],
        'template': PROJECT / 'tools' / 'loot_resources' / 'native-preparation-gray-loot-5.png',
        'png_sha': '2f070cf878d38a3f33a90b3938c72be0052e0e0bd6360e5b66831adfe1a6b360',
        'rgb_sha': 'e66a685f308897726ec7f692ed120a51a6ebb85fbd2fdc812ce2328ba6dc1bc0',
    },
    'native_preparation_loot_gray_6': {
        'control_id': 'native_preparation_loot_gray_6', 'args': [1511, 442],
        'bounds': [1308, 250, 1612, 468], 'circle_bounds': [1487, 418, 1535, 466],
        'template': PROJECT / 'tools' / 'loot_resources' / 'native-preparation-gray-loot-6.png',
        'png_sha': 'eeba590ffbef8d79558fe8ba65c6073379eccaf2b3cd69ee4b920627062de26e',
        'rgb_sha': 'e0ecf154808ad18bf158c67eab6317f439bc26fd9054e38cc24ac89fca160dc6',
    },
}


def _native_loot_circle_ncc(frame, template, bounds):
    """Compare the complete fixed circle with the retained shape, without registration."""
    import numpy as np
    reference = np.asarray(template.convert('L'), dtype=np.float64)
    reference = reference - reference.mean()
    candidate = np.asarray(frame.crop(bounds).convert('L'), dtype=np.float64)
    candidate = candidate - candidate.mean()
    denominator = float(np.sqrt((candidate * candidate).sum() * (reference * reference).sum()))
    return None if denominator == 0 else float((candidate * reference).sum() / denominator)


def native_loot_circle_disappeared(control_id, current_png, snapshot_id):
    """Only an identified current PNG and the pinned complete circle can prove progress."""
    profile = _NATIVE_LOOT_PICKUP_PROFILES.get(control_id) if isinstance(control_id, str) else None
    if profile is None:
        return False
    try:
        from io import BytesIO
        from PIL import Image
        current_bytes, payload = Path(current_png).read_bytes(), profile['template'].read_bytes()
        if (hashlib.sha256(current_bytes).hexdigest() != snapshot_id
                or hashlib.sha256(payload).hexdigest() != profile['png_sha']):
            return False
        with Image.open(BytesIO(payload)) as template, Image.open(BytesIO(current_bytes)) as current:
            if (template.format != 'PNG' or template.size != (profile['circle_bounds'][2] - profile['circle_bounds'][0],
                                                            profile['circle_bounds'][3] - profile['circle_bounds'][1])
                    or current.format != 'PNG' or current.size != (1920, 1080)
                    or hashlib.sha256(template.convert('RGB').tobytes()).hexdigest() != profile['rgb_sha']):
                return False
            score = _native_loot_circle_ncc(current, template, profile['circle_bounds'])
            return score is not None and score < .90
    except (OSError, ValueError, TypeError, KeyError):
        return False


def stable_native_loot_pickup(reply, request, actual, current_png):
    """One fixed free pickup per plan, proved by its retained complete circle shape."""
    import re
    original = request.get('observation', {})
    actions = reply.get('actions')
    if (request.get('kind') != 'preparation_strategy'
            or original.get('page') != 'preparation' or actual.get('page') != 'preparation'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or reply.get('snapshot_id') != request.get('snapshot_id')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    action = actions[0]
    proof = action.get('target_evidence') if isinstance(action, dict) else None
    control_id = proof.get('control_id') if isinstance(proof, dict) else None
    profile = _NATIVE_LOOT_PICKUP_PROFILES.get(control_id) if isinstance(control_id, str) else None
    if (profile is None or not isinstance(action, dict) or action.get('type') != 'click_point'
            or action.get('expected_page') != 'preparation'
            or not isinstance(action.get('args'), list) or action['args'] != profile['args']
            or any(type(value) is not int for value in action['args'])
            or not isinstance(proof, dict) or set(proof) != {'control_id', 'snapshot_id', 'bounds'}
            or proof.get('control_id') != profile['control_id']
            or proof.get('snapshot_id') != request.get('snapshot_id')
            or proof.get('bounds') != profile['bounds']):
        return False
    stage = original.get('fields', {}).get('stage')
    if (not isinstance(stage, str) or re.fullmatch(r'[1-3]-[1-9]', stage) is None
            or actual.get('fields', {}).get('stage') != stage):
        return False
    for label, bounds in (('备战阶段', (410, 20, 540, 65)), (stage, (420, 50, 520, 105)),
                          ('出战', (1760, 710, 1875, 790)), ('商店', (1575, 950, 1675, 1020))):
        if not _stable_navigation_anchor(original, actual, label, bounds):
            return False
        matched = []
        for observation in (original, actual):
            found = [row['box'] for row in observation.get('rows', [])
                     if isinstance(row.get('box'), list) and len(row['box']) == 4
                     and all(type(edge) is int for edge in row['box'])
                     and type(row.get('confidence')) in (int, float) and .90 <= row['confidence'] <= 1.
                     and clean(row.get('text', '')).strip('·•「」『』') == label
                     and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                     and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
            if len(found) != 1:
                return False
            matched.append(found[0])
        if any(abs(a - b) > 2 for a, b in zip(*matched)):
            return False
    for field, pattern, bounds in (
            ('hp', r'(?:100|[1-9]?[0-9])', (1400, 45, 1500, 105)),
            ('deployed', r'i?([0-9]{1,2}/[0-9]{1,2})', (846, 210, 1029, 280))):
        boxes = []
        for observation in (original, actual):
            found = []
            for row in observation.get('rows', []):
                box, confidence = row.get('box'), row.get('confidence')
                if (not isinstance(box, list) or len(box) != 4 or any(type(edge) is not int for edge in box)
                        or type(confidence) not in (int, float) or not .90 <= confidence <= 1.
                        or not (bounds[0] <= box[0] < box[2] <= bounds[2]
                                and bounds[1] <= box[1] < box[3] <= bounds[3])):
                    continue
                match = re.fullmatch(pattern, clean(row.get('text', '')))
                if match is not None:
                    found.append((box, match))
            if len(found) != 1:
                return False
            if field == 'deployed':
                count = found[0][1].group(1)
                deployed, capacity = map(int, count.split('/'))
                if (not 0 <= deployed <= capacity <= 12 or capacity < 1
                        or observation.get('fields', {}).get('deployed') != count):
                    return False
            boxes.append(found[0][0])
        if any(abs(a - b) > 2 for a, b in zip(*boxes)):
            return False
    try:
        from io import BytesIO
        import numpy as np
        from PIL import Image
        if hash_distance(original['fingerprint'], actual['fingerprint']) > .10:
            return False
        old_bytes, fresh_bytes = Path(request['original_png']).read_bytes(), Path(current_png).read_bytes()
        payload = profile['template'].read_bytes()
        if (hashlib.sha256(old_bytes).hexdigest() != request['snapshot_id']
                or hashlib.sha256(fresh_bytes).hexdigest() != actual.get('snapshot_id')
                or hashlib.sha256(payload).hexdigest() != profile['png_sha']):
            return False
        with Image.open(BytesIO(payload)) as template, Image.open(BytesIO(old_bytes)) as old, Image.open(BytesIO(fresh_bytes)) as fresh:
            if (template.format != 'PNG' or template.size != (profile['circle_bounds'][2] - profile['circle_bounds'][0],
                                                                  profile['circle_bounds'][3] - profile['circle_bounds'][1])
                    or old.format != 'PNG' or fresh.format != 'PNG'
                    or old.size != (1920, 1080) or fresh.size != (1920, 1080)
                    or hashlib.sha256(template.convert('RGB').tobytes()).hexdigest() != profile['rgb_sha']):
                return False
            for frame in (old, fresh):
                score = _native_loot_circle_ncc(frame, template, profile['circle_bounds'])
                if score is None or score < .90:
                    return False
            return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def stable_environment_card_animation(action, request, actual, current_png):
    """Eligibility only: native environment/investment title selection with bounded full-card animation."""
    original = request.get('observation', {})
    page = original.get('page')
    if page == 'environment' and request.get('kind') == 'environment_strategy':
        layout = ((207, 198, 671, 868), (727, 198, 1193, 868), (1253, 198, 1717, 868))
        header = ('投资环境', (860, 55, 1070, 145))
    elif page == 'investment' and request.get('kind') == 'investment_strategy':
        layout = ((257, 196, 665, 816), (757, 196, 1165, 816), (1257, 196, 1665, 816))
        header = ('请选择投资策略', (850, 55, 1070, 145))
    else:
        return False
    if (actual.get('page') != page
            or original.get('snapshot_id') != request.get('snapshot_id')
            or action.get('type') != 'click_text' or action.get('exact') is not True
            or action.get('expected_page') != page):
        return False
    semantic = original.get('semantic', {})
    options = semantic.get('options')
    if (semantic != actual.get('semantic') or not isinstance(options, list) or len(options) != 3):
        return False
    for index, (option, roi) in enumerate(zip(options, layout), 1):
        if (not isinstance(option, dict) or type(option.get('card_index')) is not int
                or option['card_index'] != index or option.get('bounds') != list(roi)
                or not isinstance(option.get('title'), str) or not clean(option['title'])
                or not isinstance(option.get('effect_lines'), list) or not option['effect_lines']
                or any(not isinstance(line, str) or not clean(line) for line in option['effect_lines'])):
            return False
    if len({clean(option['title']) for option in options}) != 3:
        return False
    proof = action.get('target_evidence', {})
    index = proof.get('card_index')
    if (type(index) is not int or not 1 <= index <= 3
            or proof.get('snapshot_id') != request.get('snapshot_id')):
        return False
    selected, roi = options[index - 1], layout[index - 1]
    if (proof.get('bounds') != list(roi) or proof.get('text') != selected['title']
            or proof.get('effect_lines') != selected['effect_lines']
            or action.get('text') != selected['title']):
        return False

    def stable_row(label, bounds):
        matched = []
        for observation in (original, actual):
            found = []
            for row in observation.get('rows', []):
                box, confidence = row.get('box'), row.get('confidence')
                if (not isinstance(box, list) or len(box) != 4
                        or any(type(edge) is not int for edge in box)
                        or type(confidence) not in (int, float) or not .90 <= confidence <= 1.
                        or clean(row.get('text', '')) != clean(label)):
                    continue
                if (bounds[0] <= box[0] < box[2] <= bounds[2]
                        and bounds[1] <= box[1] < box[3] <= bounds[3]):
                    found.append(box)
            if len(found) != 1:
                return None
            matched.append(found[0])
        return matched if all(abs(a - b) <= 2 for a, b in zip(*matched)) else None

    for label, bounds in (header, ('确认', (880, 935, 1040, 1030))):
        if stable_row(label, bounds) is None:
            return False
    for option, bounds in zip(options, layout):
        for label in [option['title'], *option['effect_lines']]:
            if stable_row(label, bounds) is None:
                return False
    title_box = stable_row(selected['title'], roi)[1]
    point = [(title_box[0] + title_box[2]) / 2, (title_box[1] + title_box[3]) / 2]
    if action.get('args') != point:
        return False
    try:
        from io import BytesIO
        from PIL import Image, ImageChops
        original_bytes = Path(request.get('original_png')).read_bytes()
        actual_bytes = Path(current_png).read_bytes()
        if (hashlib.sha256(original_bytes).hexdigest() != request.get('snapshot_id')
                or hashlib.sha256(actual_bytes).hexdigest() != actual.get('snapshot_id')):
            return False
        with Image.open(BytesIO(original_bytes)) as old, Image.open(BytesIO(actual_bytes)) as fresh:
            if old.format != 'PNG' or fresh.format != 'PNG' or old.size != (1920, 1080) or fresh.size != (1920, 1080):
                return False
            delta = ImageChops.difference(old.crop(roi).convert('RGB'), fresh.crop(roi).convert('RGB'))
            red, green, blue = delta.split()
            histogram = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
            pixels = (roi[2] - roi[0]) * (roi[3] - roi[1])
            return ((pixels - histogram[0]) / pixels <= .10
                    and sum((i % 256) * count for i, count in enumerate(delta.histogram())) / (3 * pixels) <= 1.
                    and sum(histogram[33:]) / pixels <= .01)
    except (OSError, ValueError, TypeError):
        return False


def stable_supply_card_animation(action, request, actual, current_png, diagnostic=None):
    """Only the retained native five-card supply title selection permits animation."""
    details = {} if diagnostic is None else diagnostic

    def rejected(stage, **facts):
        details.update(stage=stage, **facts)
        return False

    original = request.get('observation', {})
    layout = ((84, 292, 419, 783), (439, 292, 774, 783), (793, 292, 1129, 783),
              (1147, 292, 1482, 783), (1501, 292, 1836, 783))
    if (request.get('kind') != 'supply_strategy' or original.get('page') != 'supply'
            or actual.get('page') != 'supply'
            or original.get('snapshot_id') != request.get('snapshot_id')
            or action.get('type') != 'click_text' or action.get('exact') is not True
            or action.get('expected_page') != 'supply'):
        return rejected('request_action_contract')
    semantic = original.get('semantic', {})
    options = semantic.get('options')
    if (semantic != actual.get('semantic') or not isinstance(options, list) or len(options) != 5):
        return rejected('semantic_contract')
    for index, (option, roi) in enumerate(zip(options, layout), 1):
        if (not isinstance(option, dict) or set(option) != {'card_index', 'bounds', 'title', 'effect_lines'}
                or type(option.get('card_index')) is not int or option['card_index'] != index
                or option.get('bounds') != list(roi)
                or not isinstance(option.get('title'), str) or not clean(option['title'])
                or not isinstance(option.get('effect_lines'), list) or not option['effect_lines']
                or any(not isinstance(line, str) or not clean(line) for line in option['effect_lines'])):
            return rejected('card_schema', card_index=index)
    if len({clean(option['title']) for option in options}) != 5:
        return rejected('title_uniqueness')
    proof = action.get('target_evidence', {})
    index = proof.get('card_index')
    if (type(index) is not int or not 1 <= index <= 5
            or proof.get('snapshot_id') != request.get('snapshot_id')):
        return rejected('proof_identity')
    selected, roi = options[index - 1], layout[index - 1]
    if (proof.get('bounds') != list(roi) or proof.get('text') != selected['title']
            or proof.get('effect_lines') != selected['effect_lines']
            or action.get('text') != selected['title']):
        return rejected('selected_proof')

    def stable_row(label, bounds, minimum=.90):
        matched = []
        candidates = []
        for observation in (original, actual):
            found = []
            candidates.append([{'box': row.get('box'), 'confidence': row.get('confidence'),
                                'box_type': type(row.get('box')).__name__,
                                'edge_types': ([type(edge).__name__ for edge in row['box']]
                                               if isinstance(row.get('box'), (list, tuple)) else None),
                                'confidence_type': type(row.get('confidence')).__name__}
                               for row in observation.get('rows', [])
                               if clean(row.get('text', '')) == clean(label)])
            for row in observation.get('rows', []):
                box, confidence = row.get('box'), row.get('confidence')
                if (not isinstance(box, list) or len(box) != 4
                        or any(type(edge) is not int for edge in box)
                        or type(confidence) not in (int, float) or not minimum <= confidence <= 1.
                        or clean(row.get('text', '')) != clean(label)):
                    continue
                if (bounds[0] <= box[0] < box[2] <= bounds[2]
                        and bounds[1] <= box[1] < box[3] <= bounds[3]):
                    found.append(box)
            if len(found) != 1:
                details.setdefault('anchors', []).append({'label': label, 'bounds': list(bounds),
                    'minimum': minimum, 'candidates': candidates, 'unique_match_counts': [len(found)]})
                return None
            matched.append(found[0])
        stable = all(abs(a - b) <= 2 for a, b in zip(*matched))
        details.setdefault('anchors', []).append({'label': label, 'bounds': list(bounds),
            'minimum': minimum, 'candidates': candidates, 'matched_boxes': matched, 'stable': stable})
        return matched if stable else None

    for label, bounds in (('补给阶段', (800, 120, 1120, 190)),
                          ('确认', (1580, 950, 1810, 1025))):
        if stable_row(label, bounds) is None:
            return rejected('header_anchor', label=label)
    for card_index, (option, bounds) in enumerate(zip(options, layout), 1):
        title_bounds = (bounds[0]+28, 540, bounds[2]-28, 600)
        if stable_row(option['title'], title_bounds, .90 if card_index == index else .78) is None:
            return rejected('card_title_anchor', card_index=card_index, label=option['title'])
        for label in option['effect_lines']:
            if stable_row(label, bounds) is None:
                return rejected('effect_anchor', card_index=card_index, label=label)
    title_box = stable_row(selected['title'], (roi[0]+28, 540, roi[2]-28, 600))[1]
    point = [(title_box[0] + title_box[2]) / 2, (title_box[1] + title_box[3]) / 2]
    if action.get('args') != point:
        return rejected('point_identity', expected_point=point)
    try:
        from io import BytesIO
        from PIL import Image, ImageChops
        original_bytes = Path(request.get('original_png')).read_bytes()
        actual_bytes = Path(current_png).read_bytes()
        details['frames'] = {'original_expected_sha256': request.get('snapshot_id'),
                             'original_sha256': hashlib.sha256(original_bytes).hexdigest(),
                             'actual_expected_sha256': actual.get('snapshot_id'),
                             'actual_sha256': hashlib.sha256(actual_bytes).hexdigest()}
        if (hashlib.sha256(original_bytes).hexdigest() != request.get('snapshot_id')
                or hashlib.sha256(actual_bytes).hexdigest() != actual.get('snapshot_id')):
            return rejected('frame_sha')
        with Image.open(BytesIO(original_bytes)) as old, Image.open(BytesIO(actual_bytes)) as fresh:
            if old.format != 'PNG' or fresh.format != 'PNG' or old.size != (1920, 1080) or fresh.size != (1920, 1080):
                return rejected('frame_format', original_format=old.format, actual_format=fresh.format,
                                original_size=list(old.size), actual_size=list(fresh.size))
            delta = ImageChops.difference(old.crop(roi).convert('RGB'), fresh.crop(roi).convert('RGB'))
            red, green, blue = delta.split()
            histogram = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
            pixels = (roi[2] - roi[0]) * (roi[3] - roi[1])
            metrics = {'changed_fraction': (pixels - histogram[0]) / pixels,
                       'mean_abs_rgb': sum((i % 256) * count for i, count in enumerate(delta.histogram())) / (3 * pixels),
                       'over32_fraction': sum(histogram[33:]) / pixels}
            details['pixels'] = metrics
            if not (metrics['changed_fraction'] <= .10 and metrics['mean_abs_rgb'] <= 1.
                    and metrics['over32_fraction'] <= .01):
                return rejected('pixel_threshold')
            details['stage'] = 'accepted'
            return True
    except (OSError, ValueError, TypeError) as exc:
        return rejected('frame_io', error_type=type(exc).__name__, errno=getattr(exc, 'errno', None),
                        winerror=getattr(exc, 'winerror', None))


def broker_activity(ledger, worker_request_ids):
    """Count completed broker actions, including callers outside this worker.

    Outside-worker attribution does not identify a human or supervising agent.
    Missing/incomplete receipts remain unknown and never certify automation.
    """
    counts = {'script_inputs': 0, 'external_inputs': 0, 'observations': 0,
              'pending_requests': 0, 'unreadable_receipts': 0, 'coverage': 'complete'}
    counts['completed_inputs_observed'] = 0
    files = []
    try:
        if not Path(ledger).is_dir():
            counts['coverage'] = 'partial'
            counts['reason'] = 'receipt_directory_unavailable'
            return counts
        for path in Path(ledger).glob('*.json'):
            if len(files) == 4096:
                counts['coverage'] = 'partial'
                break
            files.append(path)
    except OSError:
        counts['coverage'] = 'partial'
        counts['reason'] = 'receipt_listing_incomplete'
    for path in files:
        try:
            item = entry.read_json(path)
            rid, request, result = item.get('id'), item.get('request'), item.get('result')
            if not isinstance(rid, str) or not isinstance(request, dict) or request.get('id') != rid:
                raise ValueError('unbound receipt')
            if result is None:
                counts['pending_requests'] += 1
                continue
            completed = result.get('completed') if isinstance(result, dict) else None
            if (result.get('id') != rid or not isinstance(completed, list)
                    or any(not isinstance(action, dict) for action in completed)):
                raise ValueError('unbound result')
            key = 'script_inputs' if rid in worker_request_ids else 'external_inputs'
            counts[key] += sum(action.get('type') in ('click', 'key', 'drag', 'scroll') for action in completed)
            counts['observations'] += sum(action.get('type') == 'observe' for action in completed)
        except (OSError, ValueError, TypeError, AttributeError):
            counts['unreadable_receipts'] += 1
    if counts['pending_requests'] or counts['unreadable_receipts']:
        counts['coverage'] = 'partial'
    counts['completed_inputs_observed'] = counts['script_inputs'] + counts['external_inputs']
    return counts


class Worker:
    def __init__(self, args, run, control, marker):
        self.args, self.run, self.c, self.marker = args, run, control, marker
        self.started = time.monotonic()
        self.deadline = self.started + args.max_seconds
        self.token = secrets.token_hex(24)
        identity = control.process_probe(os.getpid())
        if identity['state'] != 'running':
            raise RuntimeError('worker真实创建身份未核实')
        self.owner = {'owner': 'currency-wars-runner', 'chat_id': args.chat_id,
                      'run_id': marker['run_id'], 'run_token': self.token,
                      'artifact_chat_id': marker.get('session_hint', {}).get('id'),
                      'runner_pid': os.getpid(), 'runner_creation_id': str(identity['creation_id']),
                      'launch_id': args.launch_id, 'created_at': now()}
        self.c.ROOT = str(run)
        self.c.OWNER = {'owner': 'currency-wars-control', 'chat_id': args.chat_id, 'run_token': self.token,
                        'artifact_run_id': marker['run_id'], 'artifact_chat_id': self.owner['artifact_chat_id'],
                        'started': now(), 'source': str(entry.SOURCE)}
        self.state = {**redact(self.owner), 'protocol_version': 1, 'run_dir': str(run),
                      'state_sequence': 0, 'control_mode': 'starting', 'heartbeat_at': now(),
                      'phase': '定位当前窗口', 'reason': None, 'broker': {}, 'decision_request': None,
                      'statistics': {'local_inputs': 0, 'local_observations': 0, 'decisions': 0,
                                     'failures': 0, 'retries': 0, 'matches_confirmed': 0,
                                     'ocr_ms': 0., 'broker_ms': 0.},
                      'last_command': {'kind': 'start', 'id': args.launch_id},
                      'capabilities': {'local_llm_configured': False, 'strategy': 'structured agent replies',
                                       'max_matches': args.max_matches, 'max_seconds': args.max_seconds,
                                       'all_rewards_completed': False}}
        self.perception = Perception()
        self.panel_index, self.panel_state = 0, 'enter'
        self.claim_count, self.scroll_count = 0, 0
        self.inspections = {}
        self.context = {'guide': None, 'guide_tracking': None, 'investments': None,
                        'environment': None, 'team': None, 'bonds': None, 'gear': None,
                        'tasks': None, 'hp': None, 'coins': None, 'xp': None, 'preparation_review': None, 'guide_reference': None,
                        'unknown_fields': ['guide', 'guide_tracking', 'investments', 'environment',
                                           'team', 'bonds', 'gear', 'tasks', 'hp', 'coins', 'xp']}
        self.last_observation = None
        self.last_epoch = None
        self.wait_started = None
        self.wait_page = None
        self.broker_launcher = None
        self.bridge_launch = None
        self.children = []
        self.records = PROJECT / 'debug' / ('runner-' + args.chat_id[:8] + '-' + marker['run_id'][:12])
        self.records.mkdir(exist_ok=False)
        self.c.write_json(self.records / 'owner.json', {**redact(self.owner), 'deliverable': 'requested replay log',
                         'controller_sha256': entry.PINNED, 'runner_sha256': hashlib.sha256(SELF.read_bytes()).hexdigest()})
        self.evidence_count = 0
        self.world_entry_attempted = False
        self.consumed_match_results = set()
        self.active_match_id = uuid.uuid4().hex
        self.shop_stages = set()
        self.free_lineup_attempted = set()
        self.node_result_attempted = set()
        self.loot_pickup_attempted = set()
        self.strategy_reads = {}
        self.knowledge = coaching.load_knowledge(PROJECT / 'docs' / 'GAME_KNOWLEDGE.json')
        self.preparation_reviews = {}
        self.preparation_scope = None
        self.live_mode = None
        self.cached_guide_binding = None
        self.inspection_attempted = set()
        self.reroll_attempted = set()
        self.last_preparation_stage = None
        self.empty_transition_request = None
        self.empty_transition_attempts = 0
        self.empty_transition_next = 0.
        self.match_result_confirmed = False
        self.history = {}
        self.node_key, self.node_attempts, self.node_started = None, 0, None
        self.node_last_page, self.node_consecutive = None, 0
        self.node_progress = 0
        self.node_progress_seen = 0
        self.worker_request_ids = set()
        self.broker_activity_at = 0.
        self.c.write_json(run / 'runner-owner.json', self.owner)
        self.c.write_json(run / 'owner.json', self.c.OWNER)
        # Bind and latch before publishing discovery. GUI can safely pause the
        # initial run immediately; startup never overwrites that later intent.
        hwnd, pid, rect = self.c.win()
        game_identity = self.c.process_probe(pid)
        if game_identity['state'] != 'running':
            raise RuntimeError('初始游戏创建身份不可确认')
        self.c.BINDING = {'pid': pid, 'creation_id': game_identity['creation_id'], 'hwnd': int(hwnd),
                          'rect': rect, 'game_integrity': self.c.integrity(pid)}
        self.c.write_json(run / 'binding.json', self.c.BINDING)
        self.initial_broker_pause_id = self.c.latch_pause('新本地worker初始安全暂停')['pause_id']
        self.initial_manual_id = uuid.uuid4().hex
        latch_manual(run, '初始化；等待唯一broker与一次受控交接', self.initial_manual_id)
        self.publish()

    def publish(self, **updates):
        self.state.update(updates)
        self.state['match_id'] = self.active_match_id
        self.state['preparation_stage'] = self.last_preparation_stage
        if time.monotonic() - getattr(self, 'broker_activity_at', 0.) >= 15:
            self.state['statistics']['broker_activity'] = broker_activity(
                self.run / 'request-ledger', getattr(self, 'worker_request_ids', set()))
            self.broker_activity_at = time.monotonic()
        self.state['state_sequence'] += 1
        self.state['heartbeat_at'] = now()
        self.state['elapsed_seconds'] = round(time.monotonic() - self.started, 2)
        self.state['journal_file'] = str(self.records / 'journal.jsonl')
        self.c.write_json(self.run / 'runner-state.json', redact(self.state))
        self.c.write_json(CURRENT, redact(self.state))

    def log(self, value):
        item = {'time': now(), 'run_id': self.owner['run_id'], **redact(value)}
        with (self.records / 'journal.jsonl').open('a', encoding='utf8') as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + '\n')
            stream.flush()

    def save_frame(self, rid, label):
        source = self.run / 'game-preview.png'
        if not source.exists() or self.evidence_count >= 1000:
            return None
        from PIL import Image
        target = self.records / (rid + '-' + label + '.jpg')
        with Image.open(source) as image:
            image.convert('RGB').save(target, quality=72)
        self.evidence_count += 1
        return str(target)

    def register(self, pid, creation):
        item = {'pid': int(pid), 'process_identity': 'windows:' + str(creation)}
        self.children.append(item)
        artifacts.protect_children(self.run, self.children, root=artifacts.default_root(), complete=False)

    def startup(self):
        access = artifacts.prepare_elevated_ipc_access(self.run, root=self.run.parent,
                                                     expected_run_id=self.owner['run_id'])
        self.log({'event': 'ipc_directory_access', **access})
        hwnd, pid, rect = self.c.win()
        identity, game, own = self.c.process_probe(pid), self.c.integrity(pid), self.c.integrity(os.getpid())
        if identity['state'] != 'running' or 'integrity_rid' not in game or 'integrity_rid' not in own:
            raise RuntimeError('实际游戏权限/身份不能确认')
        binding = {'pid': pid, 'creation_id': identity['creation_id'], 'hwnd': int(hwnd),
                   'rect': rect, 'game_integrity': game}
        self.c.BINDING = binding
        self.c.write_json(self.run / 'binding.json', binding)
        self.log({'event': 'binding', 'game': binding})
        # Both launch and child acquire the same original per-game mutex.
        mutex = self.c.claim_start_mutex(binding)
        launch_args = subprocess.list2cmdline(['-B', '-X', 'utf8', str(Path(entry.__file__).resolve()), 'serve',
                       '--run-dir', str(self.run), '--chat-id', self.args.chat_id, '--run-token', self.token])
        quote = lambda value: "'" + value.replace("'", "''") + "'"
        command = ('Start-Process -FilePath ' + quote(sys.executable) + ' -ArgumentList ' + quote(launch_args)
                   + ' -WindowStyle Hidden -PassThru | Select-Object Id | ConvertTo-Json -Compress')
        try:
            # Unknown late children must protect the directory even before a
            # Popen handle or creation identity becomes available.
            artifacts.protect_children(self.run, self.children, root=artifacts.default_root(), complete=False)
            if own['integrity_rid'] < game['integrity_rid']:
                self.bridge_launch = input_bridge.prepare_launch(self.run, self.owner, entry.PINNED)
                input_bridge.dispatch(self.bridge_launch)
            else:
                self.broker_launcher = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW)
                probe = self.c.process_probe(self.broker_launcher.pid)
                if probe['state'] != 'running':
                    raise RuntimeError('本次owned启动器身份未确认')
                self.register(probe['pid'], probe['creation_id'])
        finally:
            self.c.k.ReleaseMutex(mutex)
            self.c.k.CloseHandle(mutex)
        self.publish(phase='等待已安装输入组件就绪' if self.bridge_launch else '等待同权限控制器就绪',
                     reason='使用已安装的固定输入权限；执行器重启无需重新授权' if self.bridge_launch else '仅启动本次所属控制器')
        end = min(self.deadline, time.monotonic() + (60 if self.bridge_launch else UAC_READY_TIMEOUT_SECONDS))
        while time.monotonic() < end:
            if (self.run / 'runner-stop').exists():
                raise RuntimeError('启动期间用户停止')
            startup_error = optional(self.run / 'broker-start-error.json')
            if startup_error:
                raise RuntimeError('所属输入服务启动失败：' + str(startup_error.get('error', '未知启动错误')))
            if (self.run / 'broker-ready.json').exists():
                state = self.c.status()
                if state['ready'] and state['acknowledged']:
                    self.register(state['broker_pid'], state['broker_creation_time'])
                    self.publish(broker=state)
                    break
            if self.broker_launcher and self.broker_launcher.poll() is not None and self.broker_launcher.returncode:
                error = decode_launcher_output(self.broker_launcher.stderr.read()).replace(self.token, '<redacted>')[-600:]
                raise RuntimeError('Windows启动拒绝/失败：' + error)
            self.publish()
            time.sleep(.25)
        else:
            raise TimeoutError('本次控制器就绪期限已到；保留当前游戏，不重复启动或请求授权')
        # Record the actual Start-Process/venv redirector identity while it is
        # still attributable to this launch, never infer ownership at Stop.
        if self.broker_launcher:
            self.broker_launcher.wait(timeout=5)
            launcher_output = decode_launcher_output(self.broker_launcher.stdout.read())
            if launcher_output.strip():
                launch_pid = int(json.loads(launcher_output)['Id'])
                launch_probe = self.c.process_probe(launch_pid)
                if launch_probe['state'] == 'running':
                    self.register(launch_pid, launch_probe['creation_id'])
            self.broker_launcher.stdout.close()
            self.broker_launcher.stderr.close()
        if self.bridge_launch:
            self.publish(input_bridge={'mode':'installed_task',
                         'installation_id':self.bridge_launch['config']['installation_id'],
                         'instance_id':self.bridge_launch['instance_id'], 'new_uac_requested':False})
        # Start only consumes its own initial pause. A newer GUI/physical
        # takeover after Start blocks automatic restoration.
        initial = manual_state(self.run)
        if initial and initial['manual_id'] == self.initial_manual_id:
            restored = explicit_resume(self.run, self.owner, self.c, uuid.uuid4().hex,
                expected_manual_id=self.initial_manual_id, expected_broker_pause_id=self.initial_broker_pause_id)
            self.publish(control_mode='auto' if restored.get('resumed') else 'manual',
                         phase='读取当前真实页面', reason=restored.get('error'), broker=self.c.status())
        else:
            self.publish(control_mode='manual', phase='控制器已连接，等待明确继续', reason='新手动接管优先')

    def command(self, tokens, reason, expected_page=None, postcondition=None, action=None):
        if time.monotonic() >= self.deadline:
            raise RuntimeError('本次worker总期限已到，未发布动作')
        if manual_state(self.run) or (self.run / 'runner-stop').exists():
            raise RuntimeError('持续手动/停止锁，未发布游戏动作')
        state = self.c.status()
        if not state['ready'] or state['paused'] or state['input_halted'] or not state['game_foreground']:
            raise RuntimeError('游戏输入健康检查未通过')
        planned_inputs = sum(token.split(':', 1)[0] in ('click', 'key', 'drag', 'scroll') for token in tokens)
        if self.state['statistics']['local_inputs'] + planned_inputs > 1200:
            raise RuntimeError('本次worker已到1200次硬输入上限，未发布动作')
        captured_epoch, captured_match = self.epoch(), self.active_match_id
        observed = self.last_observation or {}
        request = self.state.get('decision_request') or {}
        binding = {key: request.get(key) for key in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')}
        captured_stage = battle_stage(request)
        approval = None
        submit_deadline = self.deadline
        policy = coaching_policy()
        if policy['require_battle_confirmation'] and battle_input(observed, action, tokens):
            with file_lock(self.run, 'decision-submit.lock'):
                current_request = self.state.get('decision_request') or {}
                if not request or any(current_request.get(key) != value for key, value in binding.items()):
                    raise BattleConfirmationRequired('needs_user_confirmation：直接战斗输入缺少当前请求，先呈现真实页面给用户')
                validate_plan({'request_id': request.get('request_id'), 'snapshot_id': request.get('snapshot_id'),
                    'resume_epoch': captured_epoch, 'actions': [action or {}]}, request, captured_epoch)
                if (manual_state(self.run) or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()
                        or self.epoch() != captured_epoch or self.active_match_id != captured_match
                        or request.get('match_id') != captured_match
                        or canonical_stage(observed.get('fields', {}).get('stage')) not in (None, captured_stage)):
                    raise BattleConfirmationRequired('needs_user_confirmation：停止/接管或战斗节点已改变')
                policy = coaching_policy()
                approval = battle_approval(self.run, self.owner, request, captured_epoch, policy)
                remaining = (datetime.fromisoformat(approval['expires_at']) - datetime.now(timezone.utc)).total_seconds()
                submit_deadline = min(submit_deadline, time.monotonic() + remaining)
                ledger = self.run / ('battle-approval-consumed-' + request['request_id'] + '.json')
                descriptor = os.open(ledger, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                    json.dump({**approval, 'consumed_at': now(), 'input_resent': False}, stream, ensure_ascii=False)
                (self.run / 'runner-battle-approval.json').unlink(missing_ok=True)
                self.log({'event': 'battle_approval_consumed', 'approval_id': approval['approval_id'],
                          'request_id': request['request_id'], 'stage': approval['stage'], 'input_sent': False})

        def publication_guard(value):
            # Entry already holds the real submission_lock here. Frame/log IO
            # is complete, but the genuine publisher has not yet been called.
            with file_lock(self.run, 'decision-submit.lock'):
                current_request = self.state.get('decision_request') or {}
                current = self.last_observation or {}
                if (time.monotonic() >= self.deadline or self.epoch() != captured_epoch
                        or self.active_match_id != captured_match
                        or any(current_request.get(key) != item for key, item in binding.items())
                        or battle_stage(current_request) != captured_stage
                        or current.get('snapshot_id') != observed.get('snapshot_id')
                        or canonical_stage(current.get('fields', {}).get('stage')) != canonical_stage(observed.get('fields', {}).get('stage'))):
                    raise BattleConfirmationRequired('needs_user_confirmation：发布前请求/画面/节点/交接身份已改变，未提交')
                if request and datetime.now(timezone.utc) >= datetime.fromisoformat(request['deadline_at']):
                    raise BattleConfirmationRequired('needs_user_confirmation：发布前战略请求期限已到，未提交')
                status = self.c.status()
                if (not status['ready'] or status['paused'] or status['input_halted'] or not status['game_foreground']
                        or manual_state(self.run) or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()):
                    raise RuntimeError('发布前健康/前台/接管/停止检查拒绝，未提交')
                latest = coaching_policy()
                if approval and latest['revision'] != approval['policy_revision']:
                    raise BattleConfirmationRequired('needs_user_confirmation：发布前带教修订已改变，批准不恢复或重用')
                if latest['require_battle_confirmation'] and battle_input(observed, action, tokens):
                    if (not latest['valid'] or approval is None or not captured_stage
                            or binding['match_id'] != captured_match or binding['resume_epoch'] != captured_epoch
                            or canonical_stage(observed.get('fields', {}).get('stage')) not in (None, captured_stage)
                            or any(approval.get(key) != item for key, item in binding.items())
                            or approval.get('stage') != captured_stage or approval.get('policy_revision') != latest['revision']
                            or datetime.now(timezone.utc) >= datetime.fromisoformat(approval['expires_at'])):
                        raise BattleConfirmationRequired('needs_user_confirmation：发布前无本请求有效战斗批准，未提交')
                if time.monotonic() >= submit_deadline:
                    raise entry.SubmissionDeadlineExpired('submission deadline expired before publication; no request published')

        rid = uuid.uuid4().hex
        self.worker_request_ids = getattr(self, 'worker_request_ids', set())
        self.worker_request_ids.add(rid)
        before = self.save_frame(rid, 'before')
        self.log({'event': 'decision', 'decision_id': rid, 'tokens': tokens, 'reason': reason,
                  'expected_page': expected_page, 'expected_change': postcondition,
                  'observation': self.last_observation, 'before_evidence': before})
        started = time.perf_counter()
        guarded = GuardedSubmission(self.c, publication_guard)
        try:
            result = entry.request(guarded, 'actions', tokens, rid, False, submit_deadline=submit_deadline)
        except Exception as exc:
            self.log({'event': 'actual_result', 'decision_id': rid, 'request_id': rid,
                      'classification': 'control_outcome_unverified' if guarded.publication_attempted else 'control_submission_refused',
                      'request_published': None if guarded.publication_attempted else False, 'error': str(exc),
                      'input_resent': False, 'after_evidence': None})
            if isinstance(exc, entry.SubmissionDeadlineExpired) and approval is not None and not guarded.publication_attempted:
                raise BattleConfirmationRequired('needs_user_confirmation：战斗批准在发布前过期，已消费且不恢复') from exc
            raise
        # Preserve the exact result and image BEFORE OCR or another request.
        after = self.save_frame(rid, 'after-original') if result.get('observation') else None
        self.c.write_json(self.records / (rid + '-result.json'), redact(result))
        elapsed = (time.perf_counter() - started) * 1000
        completed = result.get('completed', [])
        self.state['statistics']['local_inputs'] += sum(a['type'] in ('click', 'key', 'drag', 'scroll') for a in completed)
        self.state['statistics']['broker_ms'] += round(elapsed, 2)
        self.log({'event': 'actual_return_pending_analysis', 'decision_id': rid, 'request_id': rid,
                  'ok': result.get('ok'), 'completed': completed, 'elapsed_ms': round(elapsed, 2),
                  'after_evidence': after, 'new_frame': bool(result.get('observation')),
                  'classification': None if result.get('ok') else 'control_guard_halt', 'error': result.get('error')})
        if not result.get('ok'):
            raise RuntimeError(result.get('error', 'broker拒绝动作'))
        observed = self.read_frame()
        self.log({'event': 'actual_result', 'decision_id': rid, 'page': observed['page'],
                  'fields': observed['fields'], 'snapshot_id': observed['snapshot_id'],
                  'after_evidence': after, 'expected_change': postcondition,
                  'classification': 'observed_after_input', 'outcome_confirmed': False})
        return observed

    def read_frame(self):
        observed = self.perception.read(self.run / 'game-preview.png')
        self.last_observation = observed
        self.state['statistics']['ocr_ms'] += observed['elapsed_ms']
        self.state['observation'] = {k: v for k, v in observed.items() if k != 'rows'}
        if observed['page'] in ('preparation', 'shop') and canonical_stage(observed.get('fields', {}).get('stage')):
            self.last_preparation_stage = observed['fields']['stage']
        for key in ('guide', 'guide_tracking', 'team', 'gear', 'bonds', 'xp'):
            fact = observed.get('semantic', {}).get(key)
            if isinstance(fact, dict):
                self.strategy_reads[key] = {'value': fact, 'snapshot_id': observed['snapshot_id'],
                    'match_id': self.active_match_id, 'resume_epoch': self.epoch(), 'observed_at': now()}
        return observed

    def preparation_checklist(self, observed):
        # Reuse UFO's explicit re-observe/return-to-supervisor idea inside the
        # existing broker workflow; this is not a second desktop controller.
        stage = canonical_stage(observed.get('fields', {}).get('stage')) or self.last_preparation_stage
        scope = (self.active_match_id, stage, self.epoch())
        if scope != getattr(self, 'preparation_scope', None):
            self.preparation_scope, self.preparation_reviews = scope, {}
        reviews = getattr(self, 'preparation_reviews', {})
        return coaching.preparation_status(reviews)

    def invalidate_preparation(self, effect):
        reviews = getattr(self, 'preparation_reviews', {})
        keys = ('inventory_cleanup', 'economy', 'lineup_equipment', 'battle_acceptance') if effect == 'inventory' else (
            'economy', 'lineup_equipment', 'battle_acceptance') if effect == 'economy' else ()
        for key in keys:
            reviews.pop(key, None)
        if keys:
            for key in ('team', 'gear', 'bonds', 'xp'):
                self.strategy_reads.pop(key, None)
                self.context[key] = None
            self.log({'event': 'preparation_reviews_invalidated', 'effect': effect, 'phases': list(keys)})

    def review_preparation(self, record, *, manual_record=None):
        if not isinstance(record, dict) or not isinstance(record.get('proof'), dict):
            raise ValueError('准备复核须有当前局真实原帧proof')
        source = self.verified_source(record['proof'], 180)
        fresh_source = source
        panel_source = None
        if manual_record is not None:
            # New-epoch screen proof remains mandatory. Historical manual
            # evidence is an explicit trace, never copied into history/epoch.
            panel_source = self.verified_manual_source(manual_record, source)
        value = record.get('value')
        if not isinstance(value, dict):
            raise ValueError('准备复核缺少结构化value')
        phase = value.get('phase')
        if panel_source is not None and phase in ('rewards', 'startup_guide'):
            source = panel_source
        status = self.preparation_checklist(self.last_observation)
        stage = self.preparation_scope[1]
        request = self.state.get('decision_request') or {}
        if (value.get('reviewer') != 'supervising_agent' or value.get('completed') is not True
                or phase not in coaching.PHASES or value.get('stage') != stage or not stage
                or source.get('preparation_stage') != stage
                or record['proof'].get('snapshot_id') != request.get('snapshot_id')
                or phase != status['phase'] or not isinstance(value.get('findings'), str) or not value['findings'].strip()):
            raise ValueError('复核须按当前节点准备顺序、当前请求鲜帧，由监督助手写明实际检查结果')
        if phase == 'rewards' and (source['page'] != 'preparation' or value.get('all_claimed') is not True
                or value.get('rescanned_after_claim') is not True):
            raise ValueError('领奖须关闭商店后重新扫全场，奖励球及待选奖励确认领空')
        startup_title = find_text(source['rows'], '创业指南', exact=True) if phase == 'startup_guide' else None
        if phase == 'startup_guide' and (value.get('entry_index') != 2 or value.get('rewards_claimed') is not True
                or not isinstance(value.get('goals'), list)
                or not startup_title or startup_title.get('confidence', 0) < .90):
            raise ValueError('须实读第二入口创业指南、当前章节目标并领空已完成奖励')
        if phase in ('inventory_cleanup', 'economy', 'lineup_equipment', 'battle_acceptance') and source['page'] not in ('preparation', 'shop'):
            raise ValueError('库存/经济/阵容验收须回到当前备战或商店鲜帧')
        if phase == 'lineup_equipment':
            team = value.get('team')
            investments = value.get('investments')
            if (not isinstance(team, dict) or team.get('checked') is not True or value.get('gear_checked') is not True
                    or value.get('investments_checked') is not True or not isinstance(investments, list)
                    or any(not isinstance(item, dict) or not isinstance(item.get('name'), str)
                           or not isinstance(item.get('effect'), str) for item in investments)):
                raise ValueError('上场/装备须逐角色实名位置与穿戴、合成机会实读')
            known = self.live_investments()
            if known and investments != known:
                raise ValueError('监督复核投资与本局已实读策略不符，先复核冲突')
            requirements = coaching.lineup_requirements(investments, team, self.knowledge)
            population = re.fullmatch(r'([0-9]+)/([0-9]+)', source.get('fields', {}).get('deployed') or '')
            board_count = sum(unit.get('location') == 'board' for unit in team.get('units', []))
            if manual_record is not None:
                native_team = fresh_source.get('semantic', {}).get('team') or {}
                board_identity = lambda value: sorted((unit.get('row'), unit.get('slot'), unit.get('name'))
                    for unit in value.get('units', []) if unit.get('location') == 'board')
                fresh_requirements = coaching.lineup_requirements(investments, native_team, self.knowledge)
                if (native_team.get('checked') is not True
                        or board_identity(native_team) != board_identity(team)
                        or fresh_requirements['verified'] is not True or fresh_requirements['needs_lineup_plan']):
                    raise ValueError('新交接鲜帧的场上实名/槽位未完整匹配；阵容仍待当前监督复核')
            if (not requirements['verified'] or requirements['needs_lineup_plan'] or not population
                    or board_count != int(population[1])):
                raise ValueError('前4/后6、角色位置或已选投资同时上场条件未满足')
            value = {**value, 'lineup_requirements': requirements}
        if phase == 'battle_acceptance':
            deployed = source.get('fields', {}).get('deployed')
            match = re.fullmatch(r'([0-9]+)/([0-9]+)', deployed or '')
            if not match or not 1 <= int(match[1]) == int(match[2]) <= 10:
                raise ValueError('出战验收须实际人口已满且不超过10，不能用文字批准补造')
        mode = value.get('mode')
        if mode in ('标准博弈', '超频博弈'):
            self.live_mode = {'value': mode, 'match_id': self.active_match_id, 'origin': 'supervising_agent',
                              'proof': record['proof']}
        self.preparation_reviews[phase] = {**value, 'proof': record['proof'], 'origin': 'supervising_agent',
                                           'observed_at': source['observed_at']}
        if phase == 'startup_guide':
            actual = panel_source or source
            actual_snapshot = actual.get('snapshot_id', record['proof']['snapshot_id'])
            self.preparation_reviews[phase]['goals'] = coaching.reviewed_goals(value['goals'], actual_snapshot)
            self.preparation_reviews[phase]['task_source'] = {
                'snapshot_id': actual_snapshot,
                'evidence_file': manual_record['after']['evidence_file'] if manual_record else record['proof']['evidence_file'],
                'observed_at': actual['observed_at'], 'match_id': self.active_match_id,
                'stage': stage, 'resume_epoch': self.epoch(), 'source': 'validated_preparation_review'}
        self.context['preparation_review'] = {'value': self.preparation_reviews, 'origin': 'supervising_agent'}
        self.node_progress = getattr(self, 'node_progress', 0) + 1
        self.log({'event': 'preparation_phase_verified', 'phase': phase, 'stage': stage,
                  'origin': 'supervising_agent', 'proof': record['proof']})

    def verified_manual_source(self, item, fresh):
        epoch = optional(self.run / 'runner-resume-epoch.json') or {}
        binding = item.get('binding', {})
        if (item.get('schema') != 'manual-preparation-result/v1' or item.get('status') != 'completed'
                or binding.get('run_id') != self.owner['run_id'] or binding.get('match_id') != self.active_match_id
                or binding.get('stage') != self.preparation_scope[1]
                or binding.get('manual_id') != epoch.get('consumed_manual_id')
                or binding.get('old_epoch') != epoch.get('previous_epoch')
                or binding.get('old_epoch') == self.epoch() or manual_state(self.run)
                or fresh.get('preparation_stage') != binding.get('stage')
                or canonical_stage(fresh.get('fields', {}).get('stage')) != binding.get('stage')
                or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()):
            raise ValueError('人工trace不属于当前局/节点/新交接代次')
        resume = await_existing_receipt(self.run, self.c, self.epoch(), 0)
        if (resume['request'].get('kind') != 'resume' or resume['result'].get('ok') is not True
                or resume['result'].get('resumed') is not True):
            raise ValueError('同broker恢复回执未确认；人工结果保持未知')
        times = [datetime.fromisoformat(item[key]['observed_at']) for key in ('before', 'after')]
        resumed = datetime.fromisoformat(epoch['time'])
        fresh_time = datetime.fromisoformat(fresh['observed_at'])
        if not times[0] <= times[1] <= resumed <= fresh_time or (fresh_time - times[1]).total_seconds() > 3600:
            raise ValueError('人工trace时序/期限不符；重新观察')
        self.verify_manual_mutation_fence(item)
        for saved in item.get('input_receipts', []):
            receipt = await_existing_receipt(self.run, self.c, saved['id'], 0)
            if (receipt['result'].get('ok') is not True
                    or receipt['result'].get('completed') != receipt['request'].get('actions')
                    or not receipt['result'].get('observation')):
                raise ValueError('人工输入回执不再完整；不重发')
        for key in ('before', 'after'):
            frame = item[key]
            path = Path(frame['evidence_file'])
            if path.resolve().parent != self.records.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != frame['snapshot_id']:
                raise ValueError('人工前后原帧缺失/改变')
            receipt = await_existing_receipt(self.run, self.c, frame['receipt_id'], 0)
            if (receipt['request'].get('actions') != [{'type': 'observe', 'args': []}]
                    or receipt['result'].get('ok') is not True or not receipt['result'].get('observation')):
                raise ValueError('人工观察回执未确认')
        source = self.perception.read(Path(item['after']['evidence_file']))
        stage = canonical_stage(source.get('fields', {}).get('stage'))
        if stage and stage != binding['stage']:
            raise ValueError('人工后帧节点已改变')
        return {**source, 'preparation_stage': binding['stage'], 'observed_at': item['after']['observed_at']}

    def verify_manual_mutation_fence(self, item):
        watermark = item.get('after', {}).get('receipt_watermark')
        if not isinstance(watermark, list):
            raise ValueError('人工阶段缺少确切回执watermark；保持未知')
        allowed = set()
        phase_index = coaching.PHASES.index(item['phase'])
        for path in (self.run / 'manual-results').glob('*.json'):
            later = entry.read_json(path)
            if (later.get('binding') == item['binding'] and later.get('status') == 'completed'
                    and later.get('phase') in coaching.PHASES[phase_index + 1:]):
                allowed.update(saved['id'] for saved in later.get('input_receipts', []))
        known = set(watermark)
        for index, path in enumerate((self.run / 'request-ledger').glob('*.json')):
            if index >= 4096:
                raise ValueError('回执fence超出有界容量')
            raw = entry.read_json(path)
            rid = raw.get('id')
            if rid in known:
                continue
            receipt = await_existing_receipt(self.run, self.c, rid, 0)
            request, result = receipt['request'], receipt['result']
            if request.get('kind') == 'resume' and rid == self.epoch():
                continue
            if request.get('kind') != 'actions' or not isinstance(request.get('actions'), list):
                raise ValueError('阶段之后出现不属于恢复的请求；人工结果未知')
            mutation = any(action.get('type') not in ('observe', 'wait') for action in request['actions'])
            if mutation and (item['phase'] in ('lineup_equipment', 'battle_acceptance') or rid not in allowed):
                raise ValueError('人工阶段之后存在未覆盖的输入变化；旧准备结果不继承')
            if (result.get('ok') is not True or result.get('completed') != request['actions']
                    or not result.get('observation')):
                raise ValueError('阶段之后的请求仍未完整确认；不重发')

    def reviewed_task_context(self, observed):
        stage = canonical_stage(observed.get('fields', {}).get('stage')) or self.last_preparation_stage
        scope = {'match_id': self.active_match_id, 'stage': stage, 'resume_epoch': self.epoch()}
        review = getattr(self, 'preparation_reviews', {}).get('startup_guide') or {}
        source = review.get('task_source') or {}
        if (review.get('completed') is not True or source.get('source') != 'validated_preparation_review'
                or any(source.get(key) != value for key, value in scope.items())):
            return None, scope
        return {'tasks': review.get('goals', []), 'source_snapshot_id': source['snapshot_id'],
                **scope, 'validated': True}, scope

    def consume_manual_results(self):
        request = self.state.get('decision_request') or {}
        if not request or request.get('resume_epoch') != self.epoch() or manual_state(self.run):
            return False
        epoch = optional(self.run / 'runner-resume-epoch.json') or {}
        directory = self.run / 'manual-results'
        items = [entry.read_json(path) for path in directory.glob('*.json')]
        self.preparation_checklist(self.last_observation)
        used = getattr(self, 'manual_results_consumed', set())
        rejected = getattr(self, 'manual_results_rejected', set())
        changed = False
        for phase in coaching.PHASES:
            candidates = [item for item in items if item.get('phase') == phase
                and item.get('status') == 'completed' and item.get('binding', {}).get('manual_id') == epoch.get('consumed_manual_id')]
            if not candidates:
                break
            if len(candidates) != 1:
                raise ValueError('人工阶段trace重复；保持未知')
            item = candidates[0]
            identity = (self.epoch(), item['checkpoint_id'])
            if identity in used:
                continue
            if identity in rejected:
                break
            if self.preparation_checklist(self.last_observation)['phase'] != phase:
                break
            proof = {'source': 'observed_screen', 'snapshot_id': request['snapshot_id'],
                     'evidence_file': request['evidence_file'], 'resume_epoch': self.epoch()}
            fresh_data = Path(request.get('original_png') or request['evidence_file']).read_bytes()
            if hashlib.sha256(fresh_data).hexdigest() != request['snapshot_id']:
                raise ValueError('新交接的原始PNG与当前请求不符；人工结果保持未知')
            try:
                self.review_preparation({'value': item['review'], 'proof': proof}, manual_record=item)
            except ValueError as exc:
                # An incomplete visual read needs a current supervising review,
                # not a new takeover that erases already accepted phases.
                # Keep this exact trace unaccepted and never retry its input.
                rejected.add(identity)
                self.manual_results_rejected = rejected
                self.log({'event': 'manual_phase_unverified', 'phase': phase,
                    'checkpoint_id': item['checkpoint_id'], 'new_epoch': self.epoch(),
                    'error': str(exc), 'input_resent': False, 'next_step': '当前请求鲜帧复核未完成阶段'})
                break
            fresh_path = self.records / ('manual-' + item['checkpoint_id'] + '-reobserved-' + hashlib.sha256(self.epoch().encode()).hexdigest()[:12] + '.png')
            with fresh_path.open('wb') as stream:
                stream.write(fresh_data)
            item['reconciliation'] = {'run_id': self.owner['run_id'], 'match_id': self.active_match_id,
                'stage': self.preparation_scope[1], 'manual_id': item['binding']['manual_id'],
                'new_epoch': self.epoch(), 'fresh_proof': proof, 'fresh_original_png': str(fresh_path),
                'resume_receipt': redact(await_existing_receipt(self.run, self.c, self.epoch(), 0)),
                'reconciled_at': now(), 'input_resent': False}
            self.c.write_json(self.records / ('manual-' + item['checkpoint_id'] + '.json'), redact(item))
            self.preparation_reviews[phase]['manual_trace'] = {'checkpoint_id': item['checkpoint_id'],
                'manual_id': item['binding']['manual_id'], 'before': item['before']['evidence_file'],
                'after': item['after']['evidence_file'], 'outcome': item['outcome'], 'new_epoch': self.epoch()}
            used.add(identity)
            self.manual_results_consumed = used
            self.log({'event': 'manual_phase_reconciled', 'phase': phase, 'checkpoint_id': item['checkpoint_id'],
                      'manual_id': item['binding']['manual_id'], 'new_epoch': self.epoch(),
                      'proof': proof, 'outcome': item['outcome'], 'input_resent': False})
            changed = True
        if changed:
            self.context['preparation_review'] = {'value': self.preparation_reviews, 'origin': 'supervising_agent'}
            self.publish(decision_request=None, control_mode='auto', reason='已核对人工结果；下一周期重新观察剩余阶段')
        return changed

    def live_investments(self):
        record = self.context.get('investments') or {}
        if record.get('match_id') == self.active_match_id:
            return record.get('value', [])
        return getattr(self, 'preparation_reviews', {}).get('lineup_equipment', {}).get('investments', [])

    def guard_preparation_action(self, action, actual, *, loot_pickup=False, verified_navigation=False):
        if actual.get('page') not in ('preparation', 'shop', 'unit_gear'):
            return
        status = self.preparation_checklist(actual)
        reviews = self.preparation_reviews
        if verified_navigation:
            if status['phase'] != 'startup_guide' or reviews.get('rewards', {}).get('completed') is not True:
                raise ValueError('须先领奖领空，再进入第二创业指南；固定图标不跳过准备顺序')
            return
        if battle_input(actual, action):
            if not status['battle_ready']:
                raise ValueError('出战前准备清单未完成：' + status['next_step'])
            population = re.fullmatch(r'([0-9]+)/([0-9]+)', actual.get('fields', {}).get('deployed') or '')
            if not population or not 1 <= int(population[1]) == int(population[2]) <= 10:
                raise ValueError('当前出战鲜帧人口未满/未知；旧验收不能批准变化后的阵容')
            return
        effect = coaching.action_effect(action)
        if effect == 'economy' or action.get('type') == 'buy_shop':
            if not status['economy_allowed']:
                raise ValueError('奖励/创业指南/清库存未核，不执行购买、刷新或升级')
        elif effect == 'inventory':
            if not all(reviews.get(key, {}).get('completed') is True for key in ('rewards', 'startup_guide')):
                raise ValueError('奖励未领空或第二入口创业指南未核，禁止出售/装备等库存改变')
        elif action.get('type') == 'click_point' and not loot_pickup:
            if reviews.get('rewards', {}).get('completed') is not True:
                raise ValueError('奖励未确认领空；未知备战坐标动作须先回传，不能花钱')
            # A point cannot bypass the semantic refresh/XP guards.
            point = action.get('args', [])
            if len(point) >= 2:
                for label in ('刷新', '购买经验', '出售'):
                    target = find_text(actual.get('rows', []), label, exact=label != '购买经验')
                    if target and target['box'][0] <= point[0] <= target['box'][2] and target['box'][1] <= point[1] <= target['box'][3]:
                        raise ValueError('经济按钮必须用语义动作和实价守卫，不能以坐标绕过：' + label)
        if action.get('type') == 'key' and action.get('args') not in ([27], [68], [69], [46]):
            if not status['economy_allowed']:
                raise ValueError('准备清单未完成，不允许未知快捷键改变资源')

    def record_node_progress(self, before, after):
        if not isinstance(before, dict) or not isinstance(after, dict):
            return
        def coins(observed):
            evidence = observed.get('semantic', {}).get('coins', {})
            value = evidence.get('value')
            if (observed.get('page') in ('preparation', 'shop') and type(value) is int and value >= 0
                    and evidence.get('bounds') == GOLD_HUD
                    and .15 <= evidence.get('currency_icon_gold_fraction', 0) <= 1.
                    and .90 <= evidence.get('confidence', 1.) <= 1.):
                return value
            return None

        def shop(observed):
            evidence = observed.get('shop') or {}
            if (observed.get('page') != 'shop' or evidence.get('ok') is not True
                    or evidence.get('status') != 'ok'
                    or evidence.get('input', {}).get('sha256') != observed.get('snapshot_id')):
                return None
            slots = evidence.get('slots', [])
            if len(slots) != 5 or {slot.get('slot') for slot in slots} != {1, 2, 3, 4, 5}:
                return None
            values = []
            for slot in slots:
                if slot.get('status') == 'empty':
                    values.append((slot['slot'], None, None))
                elif (slot.get('status') == 'recognized' and isinstance(slot.get('name'), str)
                        and slot['name'] and type(slot.get('cost')) is int and slot['cost'] > 0):
                    values.append((slot['slot'], slot['name'], slot['cost']))
                else:
                    return None
            return sorted(values)

        old_coins, new_coins = coins(before), coins(after)
        old_shop, new_shop = shop(before), shop(after)
        pages = (before.get('page'), after.get('page'))
        changed = []
        if pages[0] != pages[1] and all(isinstance(page, str) and page not in ('', 'unknown') for page in pages):
            changed.append('recognized_page')
        if old_coins is not None and new_coins is not None and old_coins != new_coins:
            changed.append('native_coins')
        if old_shop is not None and new_shop is not None and old_shop != new_shop:
            changed.append('complete_shop')
        # Plain OCR fields, missing facts and partial inventories are not proof
        # of progress. Reviews and verified loot removals have explicit events.
        if changed:
            self.node_progress = getattr(self, 'node_progress', 0) + 1
            self.log({'event': 'node_observable_progress', 'page_before': before.get('page'),
                      'page_after': after.get('page'), 'snapshot_id': after.get('snapshot_id'), 'verified_changes': changed})

    def preparation_policy(self, observed):
        facts = {}
        for key, record in self.strategy_reads.items():
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(record['observed_at'])).total_seconds()
            if (record['match_id'] == self.active_match_id and record['resume_epoch'] == self.epoch()
                    and 0 <= age <= (3600 if key in ('guide', 'guide_tracking') else 180)):
                facts[key] = record['value']
                if key == 'guide':
                    facts['guide_proof'] = {field: record.get(field) for field in
                        ('snapshot_id', 'match_id', 'resume_epoch', 'observed_at')}
        binding = getattr(self, 'cached_guide_binding', None)
        if (not facts.get('guide') and binding and binding['match_id'] == self.active_match_id
                and binding['resume_epoch'] == self.epoch()):
            facts['guide'] = {**self.knowledge['guide'], 'mode_label': binding['value']['mode']}
            facts['guide_proof'] = {'origin': 'cached_guide_with_live_application',
                'cache_source': self.knowledge['source'], 'cache_sha256': self.knowledge['sha256'],
                'application_proof': binding['proof'], 'match_id': self.active_match_id,
                'resume_epoch': self.epoch(), 'game_version_verified': False}
        phase_fields = dict(observed.get('fields', {}))
        raw_level = phase_fields.get('level')
        if isinstance(raw_level, str) and re.fullmatch(r'[1-9]|10', raw_level):
            phase_fields['level'] = int(raw_level)
        phase_observation = {**observed, 'fields': phase_fields}
        if self.live_mode and self.live_mode['match_id'] == self.active_match_id:
            phase_observation['fields']['mode'] = self.live_mode['value']
        decision = preparation_decision(phase_observation, facts)
        task_context, observation_scope = self.reviewed_task_context(observed)
        decision['progression_plan'] = progression_plan(self.knowledge, observed,
            guide_candidates=observed.get('semantic', {}).get('guide_candidates'),
            reviewed_task_context=task_context, observation_scope=observation_scope)
        checklist = self.preparation_checklist(observed)
        decision['preparation_checklist'] = checklist
        native_team = observed.get('semantic', {}).get('team') or {}
        retained_team = facts.get('team') or {}
        team = native_team if native_team.get('checked') is True else retained_team if retained_team.get('checked') is True else native_team
        decision['lineup_requirements'] = coaching.lineup_requirements(self.live_investments(), team, self.knowledge)
        if not checklist['economy_allowed']:
            decision['purchase_candidates'], decision['economic_actions'] = [], []
            decision['reroll_allowed'] = False
            decision['phase'] = checklist['phase']
            decision['needs_user_guidance'].insert(0, '当前待核：' + checklist['next_step'])
        stage = canonical_stage(observed.get('fields', {}).get('stage')) or self.last_preparation_stage
        semantic = observed.get('semantic', {})
        inspection_unit = (semantic.get('unit_preview') or {}).get('name') or (semantic.get('gear') or {}).get('unit_name')
        decision['inspection_actions'] = [action for action in decision['inspection_actions']
            if (self.active_match_id, stage, action.get('text'), inspection_unit) not in self.inspection_attempted]
        decision['recommended_actions'] = decision['inspection_actions'][:1] or [item['action'] for item in decision['purchase_candidates'][:1]]
        if not decision['recommended_actions']:
            decision['recommended_actions'] = decision['economic_actions'][:1]
        if checklist['phase'] in ('rewards', 'startup_guide', 'inventory_cleanup'):
            decision['recommended_actions'] = []
        if checklist['phase'] == 'startup_guide' and observed.get('page') == 'preparation':
            decision['recommended_actions'] = [{'type': 'click_point',
                'args': list(PREPARATION_GUIDE_POINT), 'expected_page': 'preparation',
                'guard_texts': ['备战阶段', '出战', '商店'],
                'target_evidence': {'snapshot_id': observed['snapshot_id'],
                    'bounds': list(PREPARATION_GUIDE_BOUNDS), 'control_id': PREPARATION_GUIDE_CONTROL},
                'reason': '奖励已领空，进入第二创业指南核对章节任务；执行前仍须双帧图标验证'}]
        return decision

    def observe(self):
        rid = uuid.uuid4().hex
        self.worker_request_ids = getattr(self, 'worker_request_ids', set())
        self.worker_request_ids.add(rid)
        result = entry.request(self.c, 'actions', ['observe'], rid, False)
        if not result.get('ok') or not result.get('observation'):
            raise RuntimeError('只读截图没有同请求的新鲜回帧')
        self.state['statistics']['local_observations'] += 1
        return self.read_frame()

    def click_text(self, observed, label, reason, exact=True, bounds=None):
        found = find_text(observed['rows'], label, bounds, exact)
        if found is None:
            raise ValueError('文字按钮缺失/不唯一/置信不足：' + label)
        box = found['box']
        return self.command([f'click:{(box[0]+box[2])/2}:{(box[1]+box[3])/2}', 'wait:0.7'],
                            reason, observed['page'], '点击已识别“' + label + '”后重新识别页面',
                            action={'type': 'click_text', 'text': label, 'exact': exact,
                                    'bounds': bounds, 'expected_page': observed['page'], 'reason': reason})

    def epoch(self):
        return (optional(self.run / 'runner-resume-epoch.json') or {}).get('id')

    def ask(self, observed, kind, reason, choices=None):
        old = self.state.get('decision_request')
        if old and old['snapshot_id'] == observed['snapshot_id'] and old['resume_epoch'] == self.epoch():
            return
        rid = uuid.uuid4().hex
        evidence = self.save_frame(rid, 'strategy')
        import shutil
        shutil.copyfile(self.run / 'game-preview.png', self.run / 'request-original.png')
        self.history[observed['snapshot_id']] = {'snapshot_id': observed['snapshot_id'], 'evidence_file': evidence,
            'observed_at': now(), 'page': observed['page'], 'rows': observed['rows'], 'match_id': self.active_match_id,
            'resume_epoch': self.epoch(), 'semantic': observed.get('semantic', {}), 'fields': observed.get('fields', {}),
            'preparation_stage': canonical_stage(observed.get('fields', {}).get('stage')) or self.last_preparation_stage}
        if len(self.history) > 256:
            self.history.pop(next(iter(self.history)))
        request = {'request_id': rid, 'snapshot_id': observed['snapshot_id'], 'kind': kind,
                   'reason': reason, 'observation': observed, 'context': self.context,
                   'static_knowledge': self.knowledge,
                   'knowledge_boundary': 'static_knowledge仅历史参考；动态交易/库存/任务/站位必须当前局鲜帧proof',
                   'inspection_results': self.inspections, 'choices': choices,
                   'resume_epoch': self.epoch(), 'match_id': self.active_match_id,
                   'evidence_file': evidence, 'original_png': str(self.run / 'request-original.png'), 'created_at': now(),
                   'deadline_at': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                   'allowed_action_types': ['click_text', 'click_point', 'buy_shop', 'buy_xp', 'key', 'drag', 'scroll',
                                            'finish_inspection', 'confirm_match_result', 'finish_preparation_review'],
                   'reply_path': str(self.run / 'decision-reply.json')}
        request['preparation_checklist'] = self.preparation_checklist(observed)
        task_context, observation_scope = self.reviewed_task_context(observed)
        request['progression_plan'] = progression_plan(self.knowledge, observed,
            guide_candidates=choices if kind == 'guide_strategy' and isinstance(choices, list)
                else observed.get('semantic', {}).get('guide_candidates'),
            reviewed_task_context=task_context, observation_scope=observation_scope)
        if kind in ('preparation_strategy', 'shop_strategy', 'guide_strategy', 'unit_gear_strategy'):
            request['preparation_decision'] = self.preparation_policy(observed)
        request['battle_stage'] = canonical_stage(observed.get('fields', {}).get('stage')) or self.last_preparation_stage
        request['coaching'] = coaching_policy()
        self.c.write_json(self.run / 'decision-request.json', request)
        self.state['statistics']['decisions'] += 1
        self.log({'event': 'strategy_request', 'request': request})
        self.publish(control_mode='waiting_decision', phase=kind, reason=reason, decision_request=request)

    def execute_plan(self, reply):
        request = self.state['decision_request']
        validate_plan(reply, request, self.epoch())
        policy = coaching_policy()
        if policy['require_battle_confirmation']:
            battle_actions = [action for action in reply['actions'] if battle_input(request['observation'], action)]
            if battle_actions:
                if len(reply['actions']) != 1:
                    raise BattleConfirmationRequired('needs_user_confirmation：战斗进入或人数提示确认须单独一个动作')
                battle_approval(self.run, self.owner, request, self.epoch(), policy)
        # First compare the actual current screen, not the stored old image.
        actual = self.observe()
        original = request['observation']
        if actual['page'] != original['page']:
            raise ValueError('战略回答到达时页面已变，拒绝旧计划')
        if any(isinstance(action.get('target_evidence'), dict)
               and action['target_evidence'].get('control_id') in ('peace_guide_tab_4', 'peace_guide_tab_5', 'peace_guide_cosmic_strife_tab')
               for action in reply['actions']):
            if not stable_peace_guide_tab_navigation(reply, request, actual):
                raise ValueError('指南图标须为对应已核页面的单一固定导航动作')
        free_lineup = any(isinstance(action.get('target_evidence'), dict)
                          and action['target_evidence'].get('control_id') in tuple(_FREE_LINEUP_PROFILES)
                          for action in reply['actions'])
        free_lineup_key = None
        if free_lineup:
            if (request.get('match_id') != self.active_match_id
                    or not stable_initial_free_lineup_navigation(reply, request, actual, self.run / 'game-preview.png')):
                self.log({'event': 'free_lineup_guard_rejected', 'request_id': request['request_id'],
                          'request_snapshot_id': request['snapshot_id'], 'actual_snapshot_id': actual.get('snapshot_id'),
                          'request_match_id': request.get('match_id'), 'active_match_id': self.active_match_id,
                          'control_ids': [action['target_evidence']['control_id'] for action in reply['actions']
                                          if isinstance(action.get('target_evidence'), dict)
                                          and action['target_evidence'].get('control_id') in tuple(_FREE_LINEUP_PROFILES)],
                          'original_observation': {key: original.get(key) for key in ('snapshot_id', 'page', 'fields', 'rows')},
                          'actual_observation': {key: actual.get(key) for key in ('snapshot_id', 'page', 'fields', 'rows')}})
                raise ValueError('无花费上场须为本局单次固定双ROI拖动')
            free_lineup_key = (self.active_match_id, reply['actions'][0]['target_evidence']['control_id'])
            if free_lineup_key in self.free_lineup_attempted:
                raise ValueError('本局该固定无花费上场已尝试，不重发')
        loot_pickup = any(isinstance(action.get('target_evidence'), dict)
                          and action['target_evidence'].get('control_id') in tuple(_NATIVE_LOOT_PICKUP_PROFILES)
                          for action in reply['actions'])
        loot_key = None
        if loot_pickup:
            if (request.get('match_id') != self.active_match_id
                    or not stable_native_loot_pickup(reply, request, actual, self.run / 'game-preview.png')):
                raise ValueError('战利品须为本局原生备战的单个固定实圈点击')
            loot_key = (self.active_match_id, actual['fields']['stage'], reply['actions'][0]['target_evidence']['control_id'])
            if loot_key in self.loot_pickup_attempted:
                raise ValueError('本局本节点该固定战利品已尝试，不重发')
        if hash_distance(actual['fingerprint'], original['fingerprint']) > .10:
            if not (stable_world_menu_navigation(reply, request, actual)
                    or stable_phone_guide_navigation(reply, request, actual)
                    or stable_peace_guide_tab_navigation(reply, request, actual)
                    or stable_advantages_navigation(reply, request, actual)
                    or stable_inspection_completion(reply, request, actual)
                    or stable_lobby_entry_navigation(reply, request, actual, self.run / 'game-preview.png')
                    or stable_standard_entry_navigation(reply, request, actual, self.run / 'game-preview.png')
                    or stable_plane_intro_navigation(reply, request, actual, self.run / 'game-preview.png')
                    or stable_semantic_plan(reply, request, actual, self.run / 'game-preview.png')
                    or len(reply['actions']) == 1 and stable_preparation_icon_target(
                        reply['actions'][0], request, actual, self.run / 'game-preview.png')):
                raise ValueError('战略回答到达时页面已变，拒绝旧计划')
            # OCR and anchor matching never replace the original request,
            # deadline, resume epoch, or exact source-frame identity checks.
            validate_plan(reply, request, self.epoch())
            if hashlib.sha256(Path(request['original_png']).read_bytes()).hexdigest() != request['snapshot_id']:
                raise ValueError('本次请求原始帧已更换，拒绝菜单导航')
            self.log({'event': 'navigation_guard_matched', 'request_id': request['request_id'],
                      'snapshot_id': actual['snapshot_id'], 'input_sent': False})
        self.update_context(reply.get('context_update', {}))
        for action in reply['actions']:
            if manual_state(self.run) or self.epoch() != request['resume_epoch']:
                raise RuntimeError('手动接管使未执行计划失效')
            actual = self.last_observation
            kind = action['type']
            if kind == 'finish_preparation_review':
                if (not any(key in reply.get('context_update', {}) for key in ('preparation_review', 'guide_reference'))
                        or len(reply['actions']) != 1):
                    raise ValueError('准备复核须独立无输入动作及当前帧context_update.preparation_review')
                continue
            if kind == 'finish_inspection':
                if not isinstance(action.get('result'), dict) or not action['result'].get('evidence'):
                    raise ValueError('核查完成须有实读结果和证据，不以点击结束冒称领奖')
                if (self.panel_index >= len(PANELS) or action['panel'] != PANELS[self.panel_index][0]
                        or action['result']['evidence'] != request['evidence_file']
                        or request['observation']['page'] != action['panel']):
                    raise ValueError('核查必须属于当前顺序面板及本次真实请求证据')
                self.inspections[action['panel']] = action['result']
                self.log({'event': 'inspection_result', **action})
                if action['panel'] == PANELS[self.panel_index][0]:
                    self.panel_state = 'return'
                continue
            if kind == 'confirm_match_result':
                result = action['result']
                settlement = actual.get('semantic', {}).get('settlement')
                if isinstance(settlement, dict) or any(key in result for key in ('rating', 'hp', 'materials', 'promotion_points', 'tier', 'promotion_level')):
                    mapping = {'mode': 'mode', 'outcome': 'outcome', 'rating': 'rating', 'hp': 'hp',
                               'materials': 'material_reward', 'promotion_points': 'promotion_points'}
                    if (not isinstance(settlement, dict) or settlement.get('snapshot_id') != actual.get('snapshot_id')
                            or any(result.get(key) != settlement.get(field) or settlement.get(field) is None
                                   for key, field in mapping.items())
                            or any(type(result.get(key)) is not int for key in ('hp', 'materials', 'promotion_points'))
                            or any(key in result and result[key] != settlement.get(key) for key in ('tier', 'promotion_level'))):
                        raise ValueError('评级/血量/奖励/晋升须逐项等于当前结算鲜帧事实；未知不能补造')
                if (actual['page'] != 'settlement' or result.get('evidence') != request['evidence_file']
                        or self.match_result_confirmed or self.active_match_id in self.consumed_match_results
                        or result.get('mode') not in ('标准博弈', '超频博弈')
                        or result.get('outcome') not in ('对局胜利', '对局失败', '对局结束')
                        or not any(result['mode'] in r['text'] for r in actual['rows'])
                        or not any(result['outcome'] in r['text'] for r in actual['rows'])):
                    raise ValueError('缺少当前新鲜整局结算证据或已计数，拒绝虚报通关')
                self.consumed_match_results.add(self.active_match_id)
                self.match_result_confirmed = True
                self.state['statistics']['matches_confirmed'] += 1
                self.state['last_match_result'] = result
                self.panel_index, self.panel_state = 0, 'enter'
                self.inspections = {}
                self.log({'event': 'match_result_verified', 'result': result})
                continue
            if actual['page'] != action['expected_page']:
                raise ValueError('计划前置页面变化；后续动作停止')
            guide_navigation = (kind == 'click_point'
                and action.get('target_evidence', {}).get('control_id') == PREPARATION_GUIDE_CONTROL)
            if guide_navigation and not stable_preparation_icon_target(
                    action, request, actual, self.run / 'game-preview.png'):
                raise ValueError('第二创业指南图标的双帧定位/页面/遮挡守卫拒绝，未提交')
            self.guard_preparation_action(action, actual, loot_pickup=loot_pickup,
                                          verified_navigation=guide_navigation)
            for label in action.get('guard_texts', []):
                if not any(clean(label) in clean(row['text']) and row['confidence'] >= .78 for row in actual['rows']):
                    raise ValueError('新画面缺少计划守卫：' + label)
            if kind in ('click_point', 'drag', 'scroll'):
                self.check_target_roi(action, request, actual)
            if actual['page'] in ('investment', 'environment', 'supply'):
                if kind == 'key' and action.get('args') != [27]:
                    raise ValueError('选项页只允许退出键；选项须完整卡片证据')
                if kind == 'click_text' and action.get('text') not in ('确认', '返回备战界面', '攻略', '图例'):
                    target = find_text(actual['rows'], action['text'], action.get('bounds'), action.get('exact', True))
                    if not target:
                        raise ValueError('战略文字目标缺失/不唯一')
                    point = [(target['box'][0]+target['box'][2])/2, (target['box'][1]+target['box'][3])/2]
                    self.check_target_roi({**action, 'args': point}, request, actual)
            if actual['page'] == 'shop' and kind != 'buy_shop':
                point = action.get('args', [])[:2] if kind == 'click_point' else []
                if kind == 'click_text':
                    target = find_text(actual['rows'], action['text'], action.get('bounds'), action.get('exact', True))
                    if target:
                        point = [(target['box'][0]+target['box'][2])/2, (target['box'][1]+target['box'][3])/2]
                if point and any(slot.get('bounds') and slot['bounds'][0] <= point[0] < slot['bounds'][0] + slot['bounds'][2]
                                 and slot['bounds'][1] <= point[1] < slot['bounds'][1] + slot['bounds'][3]
                                 for slot in (actual.get('shop') or {}).get('slots', [])):
                    raise ValueError('商店卡片购买必须使用buy_shop实名/实价/单槽守卫，不以普通点击绕过')
                if kind == 'key' and action.get('args') not in ([27], [68], [69]):
                    raise ValueError('商店键盘不作为未经单槽核验的购买入口')
            if (kind in ('buy_shop', 'buy_xp')
                    or (kind == 'key' and action.get('args') in ([68], [69]))
                    or (kind == 'click_text' and '购买经验' in action.get('text', ''))):
                self.require_strategy_context(actual)
            if kind == 'click_text':
                plan = self.preparation_policy(actual)
                if any(candidate.get('text') == action['text'] for candidate in plan['inspection_actions']):
                    semantic = actual.get('semantic', {})
                    inspection_unit = (semantic.get('unit_preview') or {}).get('name') or (semantic.get('gear') or {}).get('unit_name')
                    self.inspection_attempted.add((self.active_match_id,
                        canonical_stage(actual.get('fields', {}).get('stage')) or self.last_preparation_stage, action['text'], inspection_unit))
                self.check_reroll(action, actual)
                self.click_text(actual, action['text'], action['reason'], action.get('exact', True), action.get('bounds'))
            elif kind == 'buy_shop':
                if len(reply['actions']) != 1 or request.get('match_id') != self.active_match_id:
                    raise ValueError('单槽购买须为本局一个动作，回读后再决定下一次')
                old_slot = purchase_slot(original.get('shop') or {}, action.get('slot'), request['snapshot_id'])
                slot = purchase_slot(actual.get('shop') or {}, action.get('slot'), actual['snapshot_id'])
                if (not old_slot or not slot or type(action.get('cost')) is not int
                        or slot['name'] != action.get('name') or slot['cost'] != action.get('cost')
                        or any(old_slot[key] != slot[key] for key in ('slot', 'name', 'cost', 'bounds', 'position'))
                        or not canonical_stage(actual.get('fields', {}).get('stage'))
                        or actual['fields']['stage'] != original.get('fields', {}).get('stage')):
                    raise ValueError('目标单槽实名/实价/完整槽框/节点与原鲜帧计划不符')
                self.verify_purchase_frame(request, actual)
                if actual['semantic']['coins']['value'] < slot['cost']:
                    raise ValueError('新鲜实读金币不足，未提交购买')
                x, y = slot['position']
                self.command([f'click:{x}:{y}', 'wait:0.5'], action['reason'], 'shop', {'bought': slot['name']})
            elif kind == 'buy_xp':
                for unused in range(action['count']):
                    actual = self.last_observation
                    self.require_xp_cost(actual)
                    self.click_text(actual, '购买经验', action['reason'], False)
                    if actual['fields']['level'] != self.last_observation['fields']['level']:
                        break
            else:
                values = action.get('args', [])
                command = {'click_point': 'click', 'key': 'key', 'drag': 'drag', 'scroll': 'scroll'}[kind]
                self.c.validate_actions([{'type': command, 'args': values}])
                self.check_reroll(action, actual)
                if free_lineup:
                    validate_plan(reply, request, self.epoch())
                    self.free_lineup_attempted.add(free_lineup_key)
                    self.log({'event': 'free_lineup_attempted', 'match_id': self.active_match_id,
                              'control_id': free_lineup_key[1],
                              'request_id': request['request_id'], 'input_sent': False, 'retry_allowed': False})
                if loot_pickup:
                    validate_plan(reply, request, self.epoch())
                    self.loot_pickup_attempted.add(loot_key)
                    self.log({'event': 'loot_pickup_attempted', 'match_id': self.active_match_id,
                              'stage': loot_key[1], 'control_id': loot_key[2], 'request_id': request['request_id'],
                              'input_sent': False, 'retry_allowed': False})
                self.command([command + ':' + ':'.join(map(str, values)), 'wait:0.7'], action['reason'], actual['page'], action.get('expected_change'), action=action)
                if loot_pickup and (self.last_observation.get('page') != 'preparation'
                                    or self.last_observation.get('fields', {}).get('stage') != loot_key[1]):
                    raise ValueError('战利品点击后出现模态或页面/节点变化；停止，不重发')
                if loot_pickup:
                    if not native_loot_circle_disappeared(loot_key[2], self.run / 'game-preview.png',
                                                        self.last_observation.get('snapshot_id')):
                        raise ValueError('战利品点击后未核实该圈消失；停止，不重发')
                    self.node_progress = getattr(self, 'node_progress', 0) + 1
                    self.log({'event': 'verified_loot_pickup', 'match_id': self.active_match_id,
                              'stage': loot_key[1], 'control_id': loot_key[2],
                              'snapshot_id': self.last_observation['snapshot_id'], 'retry_allowed': False})
            effect = coaching.action_effect(action)
            if guide_navigation:
                effect = 'navigation'
            elif kind == 'click_point' and not loot_pickup and actual['page'] in ('preparation', 'shop'):
                effect = 'inventory'  # Unknown point effects cannot preserve old inventory/lineup proof.
            self.invalidate_preparation(effect)
            if loot_pickup or actual['page'] in ('investment', 'environment', 'supply'):
                self.preparation_reviews.clear()
            self.record_node_progress(actual, self.last_observation)
        if request['kind'] == 'new_match' and self.last_observation['page'] in ('opponents', 'environment', 'investment'):
            # A new game identity is admitted only at a real setup screen,
            # never merely because a caller supplied a different request ID.
            self.active_match_id = uuid.uuid4().hex
            self.shop_stages.clear()
            self.free_lineup_attempted.clear()
            self.node_result_attempted.clear()
            self.loot_pickup_attempted.clear()
            self.strategy_reads.clear()
            self.inspection_attempted.clear()
            self.reroll_attempted.clear()
            self.last_preparation_stage = None
            self.match_result_confirmed = False
            self.context = {key: None for key in self.context}
            self.preparation_reviews, self.preparation_scope, self.live_mode, self.cached_guide_binding = {}, None, None, None
        self.log({'event': 'plan_consumed', 'request_id': request['request_id'], 'actions': len(reply['actions'])})
        self.publish(control_mode='auto', decision_request=None, reason=None, needs_user_confirmation=False)

    def check_target_roi(self, action, request, actual):
        proof = action.get('target_evidence', {})
        if proof.get('snapshot_id') != request['snapshot_id'] or self.epoch() != request['resume_epoch']:
            raise ValueError('目标ROI的画面/交接代次不符')
        reference = Path(request['original_png'])
        if hashlib.sha256(reference.read_bytes()).hexdigest() != request['snapshot_id']:
            raise ValueError('本次请求原始帧已更换，拒绝坐标计划')
        if proof.get('control_id') == PREPARATION_GUIDE_CONTROL:
            if (request.get('match_id') != self.active_match_id
                    or not stable_preparation_icon_target(action, request, actual, self.run / 'game-preview.png')):
                raise ValueError('固定第二创业指南导航图标的双帧守卫未通过')
            return
        if proof.get('control_id') in tuple(_NATIVE_LOOT_PICKUP_PROFILES):
            if (request.get('match_id') != self.active_match_id
                    or not stable_native_loot_pickup({'snapshot_id': request['snapshot_id'], 'actions': [action]},
                        request, actual, self.run / 'game-preview.png')):
                raise ValueError('固定战利品圈的完整轮廓/原生备战守卫未通过')
            return
        if proof.get('control_id') in tuple(_FREE_LINEUP_PROFILES):
            if (request.get('match_id') != self.active_match_id
                    or not stable_initial_free_lineup_navigation(
                        {'snapshot_id': request['snapshot_id'], 'actions': [action]}, request, actual, self.run / 'game-preview.png')):
                raise ValueError('无花费上场的完整源Tile/固定空格双ROI未通过')
            return
        from PIL import Image
        box = proof.get('bounds')
        tracking_portrait = stable_tracking_selector_portrait_navigation(action, request, actual)
        if actual['page'] in ('investment', 'environment', 'supply'):
            options = request['observation'].get('semantic', {}).get('options', [])
            selected = next((o for o in options if o['card_index'] == proof.get('card_index')), None)
            fresh = next((o for o in actual.get('semantic', {}).get('options', [])
                          if o['card_index'] == proof.get('card_index')), None)
            if (not selected or not fresh or box != selected['bounds'] or fresh != selected
                    or proof.get('text') != selected['title']
                    or proof.get('effect_lines') != selected['effect_lines']):
                raise ValueError('选项必须绑定本地完整卡片、标题和全部效果，调用者ROI不足以证明')
            box = selected['bounds']
        elif not (stable_peace_guide_tab_navigation(
                {'snapshot_id': request['snapshot_id'], 'actions': [action]}, request, actual)
                or tracking_portrait):
            label = proof.get('text')
            old_text = find_text(request['observation']['rows'], label, exact=True) if isinstance(label, str) else None
            fresh_text = find_text(actual['rows'], label, exact=True) if isinstance(label, str) else None
            if (not old_text or not fresh_text or not isinstance(box, list) or len(box) != 4
                    or any(r['box'][0] < box[0] or r['box'][1] < box[1] or r['box'][2] > box[2]
                           or r['box'][3] > box[3] for r in (old_text, fresh_text))):
                raise ValueError('普通坐标ROI须包含原帧与新帧完整唯一文字目标')
        values = action.get('args', [])
        if len(values) < 2 or not (box[0] <= values[0] < box[2] and box[1] <= values[1] < box[3]):
            raise ValueError('输入点不在指定目标ROI内')
        if action['type'] == 'drag' and not (box[0] <= values[2] < box[2] and box[1] <= values[3] < box[3]):
            raise ValueError('拖动终点也须在同一已核目标区域')
        current_png = self.run / 'game-preview.png'
        if tracking_portrait:
            from io import BytesIO
            current_bytes = current_png.read_bytes()
            if hashlib.sha256(current_bytes).hexdigest() != actual.get('snapshot_id'):
                raise ValueError('追踪头像的新帧字节身份不符')
            current_png = BytesIO(current_bytes)
        with Image.open(reference) as old, Image.open(current_png) as fresh:
            if tracking_portrait and (old.format != 'PNG' or fresh.format != 'PNG'
                                      or old.size != (1920, 1080) or fresh.size != (1920, 1080)):
                raise ValueError('追踪头像须为完整1920×1080原生PNG')
            if old.crop(box).convert('RGB').tobytes() != fresh.crop(box).convert('RGB').tobytes():
                if not stable_environment_card_animation(action, request, actual, self.run / 'game-preview.png'):
                    diagnostic = {}
                    if (not stable_supply_card_animation(action, request, actual, self.run / 'game-preview.png', diagnostic)
                            and not stable_semantic_target(action, request, actual, self.run / 'game-preview.png')):
                        if request.get('kind') == 'supply_strategy' and actual.get('page') == 'supply':
                            from PIL import ImageChops
                            delta = ImageChops.difference(old.crop(box).convert('RGB'), fresh.crop(box).convert('RGB'))
                            red, green, blue = delta.split()
                            histogram = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
                            pixels = (box[2] - box[0]) * (box[3] - box[1])
                            compared_pixels = {'bounds': box, 'changed_fraction': (pixels - histogram[0]) / pixels,
                                'mean_abs_rgb': sum((i % 256) * count for i, count in enumerate(delta.histogram())) / (3 * pixels),
                                'over32_fraction': sum(histogram[33:]) / pixels}
                            frames = {}
                            for label, path in (('original', reference), ('current', self.run / 'game-preview.png')):
                                try:
                                    frames[label] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                                except OSError as exc:
                                    frames[label] = {'error_type': type(exc).__name__, 'errno': exc.errno,
                                                     'winerror': getattr(exc, 'winerror', None)}
                            self.log({'event': 'supply_card_animation_rejected',
                                      'request': {key: request.get(key) for key in ('request_id', 'snapshot_id', 'kind',
                                          'resume_epoch', 'match_id', 'original_png', 'created_at', 'deadline_at', 'observation')},
                                      'action': action, 'actual': actual, 'eligibility': diagnostic,
                                      'compared_target_pixels': compared_pixels, 'post_rejection_frame_hashes': frames,
                                      'input_sent': False, 'retry_allowed': False})
                        raise ValueError('目标ROI实际已变；全屏dHash近似不能批准旧选项')
        expected_text = proof.get('text')
        if expected_text and not find_text(actual['rows'], expected_text, box, exact=True):
            raise ValueError('目标ROI中的新鲜选项文字不匹配')

    def verified_source(self, proof, lifetime=3600):
        source = self.history.get(proof.get('snapshot_id'))
        if (proof.get('source') != 'observed_screen' or not source
                or proof.get('evidence_file') != source['evidence_file']
                or source['match_id'] != self.active_match_id
                or proof.get('resume_epoch') != self.epoch() or source.get('resume_epoch') != self.epoch()):
            raise ValueError('context观察出处、当前局或交接代次未核实')
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(source['observed_at'])).total_seconds()
        if not 0 <= age <= lifetime:
            raise ValueError('context出处超过有效期限')
        return source

    def update_context(self, updates):
        if not isinstance(updates, dict) or any(key not in self.context for key in updates):
            raise ValueError('未知战略context字段')
        for key, record in updates.items():
            if key == 'unknown_fields':
                continue
            if key == 'preparation_review':
                self.review_preparation(record)
                continue
            if key == 'guide_reference':
                if not isinstance(record, dict) or not isinstance(record.get('proof'), dict):
                    raise ValueError('缓存攻略引用须当前应用状态鲜帧proof')
                source = self.verified_source(record['proof'], 180)
                value = record.get('value')
                cached = self.knowledge.get('guide', {})
                request = self.state.get('decision_request') or {}
                if (not isinstance(value, dict) or value.get('reviewer') != 'supervising_agent'
                        or value.get('title') != cached.get('title') or value.get('cache_sha256') != self.knowledge.get('sha256')
                        or value.get('applied') is not True or value.get('no_conflict') is not True
                        or value.get('no_version_update') is not True or value.get('mode') not in ('标准博弈', '超频博弈')
                        or not value.get('compatibility_reason')
                        or record['proof'].get('snapshot_id') != request.get('snapshot_id')
                        or not (find_text(source['rows'], cached.get('title', ''), exact=True)
                                or clean(source.get('semantic', {}).get('guide', {}).get('title', '')) == clean(cached.get('title', '')))
                        or not any(clean(row.get('text', '')) in ('取消应用', '已应用', '攻略已应用')
                                   and row.get('confidence', 0) >= .90 for row in source['rows'])):
                    raise ValueError('须鲜读当前已应用攻略title、模式适配与缓存sha；冲突/更新须只重读受影响正文')
                binding = {**record, 'match_id': self.active_match_id, 'resume_epoch': self.epoch(),
                           'origin': 'cached_guide_with_live_application'}
                self.cached_guide_binding, self.context[key] = binding, binding
                self.live_mode = {'value': value['mode'], 'match_id': self.active_match_id,
                                  'origin': 'supervising_agent', 'proof': record['proof']}
                continue
            if key not in ('guide', 'guide_tracking', 'investments', 'coins', 'environment', 'team', 'gear', 'bonds', 'xp'):
                raise ValueError('v1尚无可信本地字段读取器，保留unknown：' + key)
            if not isinstance(record, dict) or 'value' not in record or not isinstance(record.get('proof'), dict):
                raise ValueError('context须带实际值及出处proof，非空字符串不足以批准操作')
            proof = record['proof']
            lifetime = 180 if key in ('coins', 'team', 'gear', 'bonds', 'xp') else 3600
            source = self.verified_source(proof, lifetime)
            facts = source.get('semantic', {}).get(key)
            value = record['value']
            normalized = value
            verified_proofs = [proof]
            if key == 'guide':
                if (not isinstance(value, dict) or not facts or clean(value.get('title')) != clean(facts['title'])
                        or value.get('applied') is not True or value.get('body_read') is not True
                        or value.get('body_lines') != facts['body_lines']
                        or value.get('mode_label') != facts['mode_label']
                        or not isinstance(value.get('compatibility'), dict)
                        or value['compatibility'].get('status') != 'inferred'
                        or not value['compatibility'].get('reason')
                        or not isinstance(value['compatibility'].get('investment_names'), list)):
                    raise ValueError('攻略原文/取消应用/实读正文及适用标签须来自该帧；兼容性只标有据推论')
                normalized = {**facts, 'compatibility': value['compatibility']}
            elif key == 'guide_tracking':
                if not isinstance(value, dict) or value.get('enabled') is not True or not 1 <= len(value.get('units', [])) <= 3:
                    raise ValueError('追踪须含1–3个实际角色及各自实名/取消状态证据')
                normalized_units = []
                per_unit = proof.get('unit_sources', {})
                for name in value['units']:
                    unit_proof = per_unit.get(name, proof)
                    unit_source = self.verified_source(unit_proof)
                    unit_facts = unit_source.get('semantic', {}).get('guide_tracking')
                    if (not unit_facts or not unit_facts.get('enabled')
                            or clean(name).casefold() not in [clean(n).casefold() for n in unit_facts['units']]):
                        raise ValueError('追踪角色实名或该角色攻略推荐/取消状态未获本地证实：' + str(name))
                    normalized_units.append(unit_facts['units'][0])
                    verified_proofs.append(unit_proof)
                if len(set(normalized_units)) != len(normalized_units):
                    raise ValueError('追踪角色列表重复')
                normalized = {'enabled': True, 'units': normalized_units}
            elif key == 'investments':
                if (not isinstance(value, list) or not facts or len(value) != len(facts)
                        or any(not isinstance(item, dict) or clean(item.get('name')) != clean(observed['name'])
                               or clean(item.get('effect')) != clean(observed['effect'])
                               for item, observed in zip(value, facts))):
                    raise ValueError('已选投策须逐项等于本地摘要面板的完整名称/效果；选择页与泛锚点不足')
                normalized = facts
            elif key == 'environment':
                if (not isinstance(value, dict) or not facts or clean(value.get('name')) != clean(facts['name'])
                        or clean(value.get('effect')) != clean(facts['effect'])):
                    raise ValueError('已选环境须等于当前选中摘要面板名称/效果')
                normalized = facts
            elif key == 'coins':
                if (type(value) is not int or not facts or value != facts['value'] or proof.get('bounds') != GOLD_HUD):
                    raise ValueError('金币须来自固定金额HUD、货币图标和经验/商店标签，不能任取数字')
            elif key in ('team', 'gear', 'bonds', 'xp'):
                if not isinstance(facts, dict) or value != facts:
                    raise ValueError('阵容/装备/羁绊/经验须逐字段等于本地实际语义；不能补造库存、星级或坐标')
            self.context[key] = {**record, 'value': normalized, 'verified_observed_at': source['observed_at'],
                'verified_proofs': verified_proofs, 'match_id': self.active_match_id, 'lifetime_seconds': lifetime}

    def require_strategy_context(self, actual):
        coin_fact = actual.get('semantic', {}).get('coins')
        if (not isinstance(coin_fact, dict) or coin_fact.get('bounds') != GOLD_HUD
                or type(coin_fact.get('value')) is not int or not 0 <= coin_fact['value'] <= 1_000_000):
            raise ValueError('新鲜金币HUD无法可信读取，停止购买')
        if not self.preparation_checklist(actual)['economy_allowed']:
            raise ValueError('经济动作前须按顺序核领奖领空→第二入口创业指南→清库存；当前未完成')
        if isinstance(self.context.get('coins'), dict):
            self.context['coins']['value'] = coin_fact['value']
        self.log({'event': 'live_coins_roi', **coin_fact, 'snapshot_id': actual['snapshot_id']})

    def verify_purchase_frame(self, request, actual):
        from io import BytesIO
        from PIL import Image
        for path, snapshot in ((request['original_png'], request['snapshot_id']),
                               (self.run / 'game-preview.png', actual['snapshot_id'])):
            data = Path(path).read_bytes()
            if hashlib.sha256(data).hexdigest() != snapshot:
                raise ValueError('购买原/鲜PNG字节身份不符，未发布输入')
            with Image.open(BytesIO(data)) as image:
                if image.format != 'PNG' or image.size != (1920, 1080):
                    raise ValueError('购买须完整1920×1080原生PNG')

    def require_xp_cost(self, actual):
        self.require_strategy_context(actual)
        xp = actual.get('semantic', {}).get('xp', {})
        cost = xp.get('buy_cost')
        if (type(cost) is not int or cost <= 0 or actual['semantic']['coins']['value'] < cost
                or xp.get('snapshot_id') != actual['snapshot_id']):
            raise ValueError('经验购买费用/当前经验未实读或金币不足，先检查，未提交')

    def check_reroll(self, action, actual):
        if action.get('type') == 'key' and action.get('args') == [69] or action.get('type') == 'click_text' and '购买经验' in action.get('text', ''):
            self.require_xp_cost(actual)
        if not (action.get('type') == 'key' and action.get('args') == [68]
                or action.get('type') == 'click_text' and clean(action.get('text', '')) == '刷新'):
            return
        decision = self.preparation_policy(actual)
        if not decision['reroll_allowed']:
            raise ValueError('实读攻略禁止当前阶段搜牌/刷新，未提交')
        stage = canonical_stage(actual.get('fields', {}).get('stage'))
        reroll = actual.get('semantic', {}).get('reroll', {})
        cost = reroll.get('cost')
        self.require_strategy_context(actual)
        if (not stage or type(cost) is not int or cost <= 0 or reroll.get('snapshot_id') != actual['snapshot_id']
                or actual['semantic']['coins']['value'] < cost or reroll.get('power_improvement_needed') is not True):
            raise ValueError('刷新费用/战力缺口未实读，不盲刷')
        key = (self.active_match_id, stage)
        if key in self.reroll_attempted:
            raise ValueError('本节点刷新已尝试；不因新请求或未知结果重复花钱')
        self.reroll_attempted.add(key)

    def node_guard(self, observed):
        page = observed['page']
        in_panel = self.panel_index < len(PANELS) and page in ('lobby', 'reward_overlay', PANELS[self.panel_index][0])
        key = ('panel', self.panel_index, self.panel_state) if in_panel else (page, observed['fields'].get('stage'))
        if key != self.node_key:
            self.node_key, self.node_attempts, self.node_started = key, 0, time.monotonic()
        progress = getattr(self, 'node_progress', 0)
        if progress != getattr(self, 'node_progress_seen', 0):
            self.node_attempts, self.node_consecutive = 0, 0
            # Budget measures time without verified progress. A completed
            # review or observed resource/board change starts the next bounded
            # step; merely waiting or another screenshot cannot reset it.
            self.node_started = time.monotonic()
            self.node_progress_seen = progress
        if page != getattr(self, 'node_last_page', None):
            self.node_last_page, self.node_consecutive = page, 0
        self.node_consecutive += 1
        maximum, seconds = (70, 180) if in_panel else (30, 240) if page == 'battle' else (6, 180) if page == 'guide' else (6, 60)
        if (self.node_attempts >= maximum or time.monotonic() - self.node_started >= seconds
                or (page != 'battle' and self.node_consecutive > 6)):
            self.pause_internal(f'本地节点{key}超过{maximum}次无进展/{seconds}秒有界预算，停止重复输入')
            return False
        self.node_attempts += 1
        return True

    def account_manual_wait(self, iteration_started):
        paused_seconds = max(0, time.monotonic() - iteration_started)
        # Manual pause and passive strategy waiting consume no active node budget.
        # The hard worker deadline, attempts and decision expiry stay intact.
        for name in ('node_started', 'wait_started'):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, value + paused_seconds)

    def retire_unknown_request(self, request, observed):
        """Retire only a real known-page transition, or an empty frame becoming readable."""
        if (request['kind'] != 'unknown_page' or request['observation'].get('page') != 'unknown'
                or not observed or not observed.get('rows')
                or not isinstance(observed.get('page'), str) or not observed['page']
                or (observed.get('page') == 'unknown' and request['observation'].get('rows') != [])):
            return False
        with file_lock(self.run, 'decision-submit.lock'):
            active = self.state.get('decision_request')
            status = self.c.status()
            if (not active or active['request_id'] != request['request_id']
                    or active['resume_epoch'] != request['resume_epoch'] or self.epoch() != request['resume_epoch']
                    or manual_state(self.run) or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()
                    or not status['ready'] or status['paused'] or status['input_halted'] or not status['game_foreground']
                    or time.monotonic() >= self.deadline):
                return False
            if datetime.now(timezone.utc) > datetime.fromisoformat(request['deadline_at']):
                raise RuntimeError('未知过渡帧请求15分钟期限已到；不续请求')
            stale_reply = optional(self.run / 'decision-reply.json')
            if stale_reply:
                self.log({'event': 'unknown_transition_reply_rejected', 'request_id': request['request_id'],
                          'reply_request_id': stale_reply.get('request_id'),
                          'reply': redact(stale_reply), 'input_sent': False})
                (self.run / 'decision-reply.json').unlink()
            self.log({'event': 'unknown_transition_request_retired', 'request_id': request['request_id'],
                      'snapshot_id': observed['snapshot_id'], 'page': observed['page'],
                      'resume_epoch': request['resume_epoch'], 'input_sent': False})
            self.publish(decision_request=None, control_mode='auto', reason='未知过渡帧请求已废弃；下一周期重新读取当前页')
        return True

    def tick_decision(self):
        request = self.state['decision_request']
        if request['resume_epoch'] != self.epoch():
            self.publish(decision_request=None, control_mode='auto', reason='新交接后废弃旧战略请求')
            return False
        if datetime.now(timezone.utc) > datetime.fromisoformat(request['deadline_at']):
            self.pause_internal('战略等待15分钟期限已到；没有无限未知循环')
            return False
        if self.consume_manual_results():
            return False
        if request['kind'] == 'unknown_page' and request['observation'].get('page') == 'unknown':
            # Reuse actual known pages from the existing 90-second passive capture.
            # Unknown animation alone never renews a nonempty original request.
            if self.retire_unknown_request(request, self.last_observation):
                return False
            if self.empty_transition_request != request['request_id']:
                self.empty_transition_request = request['request_id']
                self.empty_transition_attempts = 0
                self.empty_transition_next = time.monotonic() + 2
            if self.empty_transition_attempts < 3 and time.monotonic() >= self.empty_transition_next:
                status = self.c.status()
                if (manual_state(self.run) or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()
                        or not status['ready'] or status['paused'] or status['input_halted'] or not status['game_foreground']
                        or self.epoch() != request['resume_epoch'] or time.monotonic() >= self.deadline):
                    return False
                self.empty_transition_attempts += 1
                self.empty_transition_next = time.monotonic() + 2
                try:
                    observed = self.observe()
                except RuntimeError as exc:
                    if str(exc) != 'another request is pending; no concurrent submission':
                        raise
                    self.log({'event': 'unknown_transition_observe_deferred', 'request_id': request['request_id'],
                              'attempt': self.empty_transition_attempts, 'request_published': False, 'input_sent': False})
                    observed = None
                if self.retire_unknown_request(request, observed):
                    return False
            if request['observation'].get('rows') == []:
                self.publish(reason='空过渡帧只读复查预算已耗尽；保持等待' if self.empty_transition_attempts >= 3
                             else '空过渡帧，等待下一次有界只读复查')
                time.sleep(.25)
                return False
        reply = optional(self.run / 'decision-reply.json')
        if reply:
            (self.run / 'decision-reply.json').unlink()
            input_count = self.state['statistics']['local_inputs']
            try:
                self.execute_plan(reply)
            except BattleConfirmationRequired as exc:
                if self.state['statistics']['local_inputs'] != input_count:
                    raise RuntimeError('计划已有输入后缺少战斗批准；停止，不能重发旧计划') from exc
                self.log({'event': 'needs_user_confirmation', 'request_id': request['request_id'],
                          'error': str(exc), 'input_resent': False})
                self.publish(control_mode='waiting_decision', reason=str(exc), needs_user_confirmation=True)
                return False
            return True
        else:
            # Passive observe refreshes the original broker idle lease, with
            # zero SetForeground/SendInput. Never resumes a manual pause.
            self.publish()
            time.sleep(.25)
            return False

    def pause_internal(self, reason):
        self.state['decision_request'] = None
        (self.run / 'runner-battle-approval.json').unlink(missing_ok=True)
        try:
            latch_manual(self.run, reason[:200], uuid.uuid4().hex)
        finally:
            result = self.c.pause(reason[:200])
        self.state['statistics']['failures'] += 1
        self.publish(control_mode='halted', reason=reason, broker=result)
        self.log({'event': 'input_halt', 'reason': reason, 'broker': result})

    def tick(self, observed):
        if not self.node_guard(observed):
            return
        page = observed['page']
        shop_key = None
        if page in ('shop', 'preparation'):
            stage = observed.get('fields', {}).get('stage')
            if isinstance(stage, str):
                stage = stage.replace('－', '-')
                if len(stage) == 3 and stage[0] in '123' and stage[1] == '-' and stage[2] in '123456789':
                    shop_key = (self.active_match_id, stage)
        if page != self.wait_page:
            self.wait_page, self.wait_started = page, time.monotonic()
        elapsed = time.monotonic() - self.wait_started
        if page == 'world_entry':
            if self.world_entry_attempted:
                self.ask(observed, 'world_entry_unverified', 'F已执行一次但仍停在大世界；停止重复按F，核当前真实提示。')
            else:
                self.world_entry_attempted = True
                self.command(['key:70', 'wait:1.5'], '新画面同时识别朝露公馆与货币战争原生F交互，进入活动', page, '活动主界面')
        elif page == 'update_notice':
            self.command(['key:27', 'wait:0.7'], '仅关闭实读赛季扩充说明模态后回读活动页', page, '活动主界面')
        elif page == 'reward_overlay':
            # Modal close is a separate layer, never concatenated with claims.
            self.command(['key:27', 'wait:0.7'], '仅关闭已识别获得物品/奖励模态后回读', page, '原领奖页')
        elif self.panel_index < len(PANELS) and page == 'lobby':
            if self.panel_state == 'return':
                self.panel_index += 1
                self.panel_state, self.claim_count, self.scroll_count = 'enter', 0, 0
                if self.panel_index == len(PANELS):
                    self.publish(phase='补领奖及优势核查完成', inspection_results=self.inspections)
                    return
            label = PANELS[self.panel_index][1]
            self.click_text(observed, label, '按固定结算清单进入“' + label + '”，保留当前账户配置')
            self.panel_state = 'inspect'
        elif self.panel_index < len(PANELS) and page == PANELS[self.panel_index][0]:
            if self.panel_state == 'return':
                self.command(['key:27', 'wait:0.7'], '当前面板已实读核查，返回主界面执行下一项', page, 'lobby')
                return
            claim = find_text(observed['rows'], '一键领取') or find_text(observed['rows'], '领取', (300, 200, 1920, 1050))
            if claim and self.claim_count < 30:
                box = claim['box']
                after = self.command([f'click:{(box[0]+box[2])/2}:{(box[1]+box[3])/2}', 'wait:0.7'],
                             '实际识别领取按钮；领取已完成奖励，不改变任务或开启新局', page, '领取回执/按钮状态变化')
                self.claim_count += 1
                if after['page'] == page and hash_distance(after['fingerprint'], observed['fingerprint']) < .003:
                    self.ask(after, 'claim_no_change', '本次领取未观察到页面变化，不能把灰按钮当可领取；停止重复点击，实读状态。')
            else:
                self.ask(observed, 'post_match_' + page,
                         '请核当前面板剩余红叹号/可领条目、页签与滚动覆盖；优势布局按可用等价钻钞正常补强。结果须实读，不编数量。')
        elif self.panel_index >= len(PANELS) and page == 'lobby':
            if self.state['statistics']['matches_confirmed'] >= self.args.max_matches:
                self.publish(control_mode='completed', phase='本次有界流程结束', reason='真实整局结算及局后清单已核；全活动目标未完成')
            else:
                self.ask(observed, 'new_match', '补领奖/优势清单核完，按用户标准博弈当前等级开局；保留已有奖励选项。')
        elif page == 'shop':
            checklist = self.preparation_checklist(observed)
            if checklist['phase'] == 'rewards':
                close = find_text(observed['rows'], '收起', exact=True)
                if close:
                    self.click_text(observed, '收起', '自动展开商店时先收起，回读全场奖励；未确认领空不花钱')
                else:
                    self.ask(observed, 'shop_strategy', '先关闭自动展开商店并扫场领奖领空；当前禁止购买、出售、刷新和升级。')
                return
            if shop_key is not None:
                self.shop_stages.add(shop_key)
            self.ask(observed, 'shop_strategy',
                     '当前先完成：' + checklist['next_step'] + '；第二入口创业指南优先，动态复核须当前帧proof。'
                     if not checklist['economy_allowed'] else
                     '优先装备利用、对子升星、过渡上场、小羁绊与早期经济成长，再考虑攻略长线；实读金币与目标槽后有限购买，不机械囤50。'
                     if (observed.get('shop') or {}).get('ok') is True
                     else '商店仍有未知槽位/推荐标记；独立确认实名、实价和完整槽框的目标可单次购买，未知目标继续等待检查。')
        elif page == 'preparation':
            policy = self.preparation_policy(observed)
            checklist = policy['preparation_checklist']
            if not checklist['economy_allowed']:
                self.ask(observed, 'preparation_strategy',
                         '按顺序处理当前待办：' + checklist['next_step'] + '。领奖后重新扫全场；第二入口创业指南读当前目标并领奖；清库存后再统一经济。'
                         '可用当前帧proof提交supervising_agent复核，未知项回传，缓存不代替进度。')
            elif policy['inspection_actions']:
                self.ask(observed, 'preparation_strategy', '先按已选攻略检查追踪、可用装备与场上阵容；攻略推荐不是库存，也不默认装备已生效。')
            elif shop_key is None:
                self.ask(observed, 'preparation_strategy', '当前备战节点stage缺失或异常，停止自动开店；先核真实节点与角色/装备。')
            elif shop_key in self.shop_stages:
                self.ask(observed, 'preparation_strategy', '本局本节点商店已进入或尝试打开，保持关闭以核角色/装备与指南追踪；不自动重开，不明结果先核。')
            else:
                button = find_text(observed['rows'], '商店')
                if button:
                    # Consume the stage before publication; failure or an unknown
                    # outcome never authorizes another automatic opening.
                    self.shop_stages.add(shop_key)
                    self.click_text(observed, '商店', '本局本节点只自动打开商店一次，先完整读店再决策；不把关店过渡当可领奖')
                else:
                    self.ask(observed, 'preparation_strategy', '须完整读店、Aha可合成/前后台、装备/指南追踪，核可战阵容后一次计划出战。')
        elif page == 'node_result':
            stage = observed.get('fields', {}).get('stage')
            if (not isinstance(stage, str) or len(stage) != 3
                    or stage[0] not in '123' or stage[1] != '-' or stage[2] not in '123456789'):
                self.ask(observed, 'node_result_unverified', '节点结果stage缺失或异常，不自动继续；先核真实节点。')
            elif (self.active_match_id, stage) in self.node_result_attempted:
                self.ask(observed, 'node_result_unverified', '本局此节点继续已尝试但仍在结果页，不撤销记录或重试；先核实际结果。')
            else:
                self.node_result_attempted.add((self.active_match_id, stage))
                after = self.click_text(observed, '继续挑战', '当前原生节点结果只继续一次，不计整局完成或自动开新局',
                                        bounds=(880, 850, 1045, 935))
                if after['page'] == 'node_result':
                    self.ask(after, 'node_result_unverified', '单次继续后仍在节点结果页，停止重复点击；先核实际状态。')
        elif page == 'boss_result':
            stage = canonical_stage(observed.get('fields', {}).get('stage'))
            key = (self.active_match_id, 'boss_result', stage)
            if stage != '3-7':
                self.ask(observed, 'boss_result_unverified', '首领结果缺少原生3-7节点，不自动前往结算。')
            elif key in self.node_result_attempted:
                self.ask(observed, 'boss_result_unverified', '本局首领前往结算已尝试一次；先核实际状态，不重复点击。')
            else:
                self.node_result_attempted.add(key)
                after = self.click_text(observed, '前往结算', '原生3-7首领结果只前往结算一次；整局仍待正式结算鲜帧核实', exact=True)
                if after['page'] == 'boss_result':
                    self.ask(after, 'boss_result_unverified', '单次前往结算后仍在首领结果页，保持待核，不计整局完成。')
        elif page == 'battle':
            if elapsed > 240:
                self.pause_internal('战斗等待超过240秒，未编写胜负')
            else:
                self.publish(phase='本地等待自然战斗结算', reason=None)
                # Frozen broker wait checks pause/focus/gamepad repeatedly.
                self.command(['wait:8'], '已识别战斗中，8秒有界守卫等待，不逐帧在线推理', page, '新节点或真实结算')
        elif page == 'settlement_grade':
            self.click_text(observed, '下一步', '已识别整轮评价/当前职级/职级晋升，进入正式整局奖励信息页再确认，不以SSS单字样计数')
        elif page == 'settlement':
            self.ask(observed, 'settlement_verify', '实读本次标准/超频、SSS/胜负、HP、晋升与奖励；验证后返回主界面局后清单，不能当全活动完成。')
        elif page in ('environment', 'investment', 'opponents', 'supply', 'guide', 'unit_gear'):
            if page == 'investment':
                self.context['investments'] = None
            self.ask(observed, page + '_strategy', '新战略分岔：实读全部选项/代价，黄色小书优先，结合未完成目标选攻略，核投资与所选正文/追踪匹配后给有限计划。')
        else:
            self.ask(observed, 'unknown_page', '当前页本地未可靠识别，停止点击；请由现有代理读原帧给有限守卫计划，不能盲点或重复试局。')

    def run_loop(self):
        self.startup()
        awake = self.c.k.SetThreadExecutionState(0x80000003)
        if not awake:
            self.log({'event': 'awake_unverified', 'error': ctypes.get_last_error()})
        else:
            self.state['wake_lease'] = 'thread-bound; revoked in finally; no power plan change'
        last_capture = 0

        def capture_passive():
            nonlocal last_capture
            try:
                self.observe()
            except RuntimeError as exc:
                # submission_lock rejected before this request's publication.
                if str(exc) != 'another request is pending; no concurrent submission':
                    raise
                self.log({'event': 'passive_observe_deferred', 'reason': str(exc),
                          'request_published': False, 'input_sent': False})
            last_capture = time.monotonic()

        try:
            while time.monotonic() < self.deadline and self.state['control_mode'] not in TERMINAL:
                iteration_started = time.monotonic()
                if (self.run / 'runner-stop').exists():
                    self.publish(control_mode='stopping', reason='用户停止，不自动重启')
                    break
                status = self.c.status()
                self.state['broker'] = status
                if not status['ready']:
                    raise RuntimeError('所属broker已退出/未知；没有自动新控制器或重发')
                manual = manual_state(self.run)
                if manual or status['paused'] or status['input_halted']:
                    self.publish(control_mode='manual' if manual or status['paused'] else 'halted',
                                 phase='已暂停，等待继续自动',
                                 reason=(manual or {}).get('reason') or status.get('reason'),
                                 decision_request=None)
                    if time.monotonic() - last_capture > 90:
                        capture_passive()
                    time.sleep(.25)
                    self.account_manual_wait(iteration_started)
                    continue
                if not status['game_foreground']:
                    self.pause_internal('批次外前台丢失，保持手动，不自动抢回')
                    continue
                if self.state.get('decision_request'):
                    if time.monotonic() - last_capture > 90:
                        capture_passive()
                    try:
                        if not self.tick_decision():
                            self.account_manual_wait(iteration_started)
                    except Exception as exc:
                        self.pause_internal(str(exc))
                    continue
                self.publish(control_mode='auto', phase='读取当前真实页面', reason=None)
                observed = self.observe()
                last_capture = time.monotonic()
                try:
                    self.tick(observed)
                except Exception as exc:
                    self.pause_internal(str(exc))
                time.sleep(.25)
            if time.monotonic() >= self.deadline:
                self.publish(control_mode='stopping', reason='本次显式总时限到期，不无限续跑')
        finally:
            self.c.k.SetThreadExecutionState(0x80000000)

    def shutdown(self):
        self.state['decision_request'] = None
        (self.run / 'runner-battle-approval.json').unlink(missing_ok=True)
        # A prewritten stop also prevents a late, still-pending UAC launch from
        # issuing input. Unknown late children keep the standard directory.
        (self.run / 'broker-stop').touch()
        evidence = {'broker': {'state': 'unknown'}, 'worker': {'state': 'exiting'}}
        identity = optional(self.run / 'broker-process.json')
        if identity:
            result = entry.stop(self.c, self.run, self.args.chat_id, self.token)
            evidence['broker'] = result['exit_evidence']
        elif self.bridge_launch is not None:
            evidence['broker'] = {**input_bridge.cancel_pending(self.bridge_launch),
                'run_id':self.owner['run_id'], 'worker_pid':self.owner['runner_pid'],
                'worker_creation_id':self.owner['runner_creation_id'], 'launch_id':self.owner['launch_id']}
        elif self.broker_launcher is None:
            evidence['broker'] = {'state': 'not_launched', 'launch_attempted': False, 'identity_observed': False,
                'run_id': self.owner['run_id'], 'worker_pid': self.owner['runner_pid'],
                'worker_creation_id': self.owner['runner_creation_id'], 'launch_id': self.owner['launch_id']}
        elif self.broker_launcher.poll() is None:
            # Cancel exactly our outstanding launch process; no unrelated PID.
            self.broker_launcher.terminate()
            self.broker_launcher.wait(timeout=5)
            raise RuntimeError('UAC启动结果未核实；停止启动器，保留目录防晚到broker')
        elif self.broker_launcher.returncode:
            evidence['broker'] = {'state': 'launch_failed', 'launch_attempted': True, 'identity_observed': False,
                'launch_exit_code': self.broker_launcher.returncode, 'run_id': self.owner['run_id'],
                'worker_pid': self.owner['runner_pid'], 'worker_creation_id': self.owner['runner_creation_id'],
                'launch_id': self.owner['launch_id']}
        else:
            raise RuntimeError('启动器结束但broker身份缺失；保留目录，不假称退出')
        if self.broker_launcher:
            self.broker_launcher.wait(timeout=5)
            for pipe in (self.broker_launcher.stdout, self.broker_launcher.stderr):
                if not pipe.closed:
                    pipe.close()
        # Give recorded venv redirectors a short exit grace, checking creation.
        end = time.monotonic() + 5
        while any(self.c.process_probe(child['pid'], child['process_identity'].split(':', 1)[1])['state']
                  not in ('absent', 'exited', 'reused') for child in self.children):
            if time.monotonic() >= end:
                raise RuntimeError('owned子进程退出未确认，保留标准运行目录')
            time.sleep(.05)
        artifacts.protect_children(self.run, self.children, root=artifacts.default_root(), complete=True)
        final_mode = self.state['control_mode'] if self.state['control_mode'] in ('completed', 'failed') else 'stopped'
        self.broker_activity_at = -float('inf')  # Preserve all final receipts before the runtime is removed.
        self.publish(control_mode=final_mode, exit_evidence=evidence, reason=self.state.get('reason'),
                     cleanup={'directory': str(self.run), 'removed': False, 'pending_finally': True})
        self.log({'event': 'owned_shutdown', 'exit_evidence': evidence})


def worker_cli(args):
    with activity(PROJECT, 'runner'):
        return _worker_cli(args)


def _worker_cli(args):
    control = entry.backend()
    artifact_chat = os.environ.get('CODEX_THREAD_ID', 'unbound')
    purpose = 'currency-wars-runner-' + artifact_chat[:8].lower()
    worker, run = None, None
    try:
        with artifacts.scratch_directory(purpose, root=artifacts.default_root()) as run:
            marker = artifacts.read_marker(run)
            worker = Worker(args, run, control, marker)
            try:
                worker.run_loop()
            except Exception as exc:
                worker.publish(control_mode='failed', reason=str(exc))
                worker.log({'event': 'worker_failure', 'error': str(exc)})
            finally:
                worker.shutdown()
        if worker:
            worker.state['cleanup'] = {'directory': str(run), 'removed': not run.exists(), 'pending_finally': False}
            worker.state['state_sequence'] += 1
            worker.state['heartbeat_at'] = now()
            # Compact final discovery survives the disposed runtime.
            control.write_json(CURRENT, redact(worker.state))
    except Exception as exc:
        if worker:
            primary_error = worker.state.get('reason') if worker.state.get('control_mode') == 'failed' else None
            worker.state.update(control_mode='failed', reason=primary_error or str(exc),
                                cleanup={'directory': str(run), 'removed': bool(run and not run.exists()),
                                         'denied_or_unverified': str(exc)})
            control.write_json(CURRENT, redact(worker.state))
        raise


def command_cli(args):
    rid = uuid.uuid4().hex
    emergency = args.command in ('pause', 'takeover', 'stop')
    run, owner, binding, control = load(args.run_dir, args.chat_id, args.run_token, emergency=emergency)
    if args.command == 'status':
        return envelope(current_state(run, owner, control), command_id=rid)
    if args.command == 'manual-checkpoint':
        item = begin_manual_phase(run, owner, control, args.manual_id, args.phase)
        return {'ok': True, 'checkpoint_id': item['checkpoint_id'], 'phase': item['phase'],
                'status': item['status'], 'binding': item['binding'], 'before': item['before']['evidence_file']}
    if args.command == 'manual-result':
        review = entry.read_json(Path(args.reply_file).absolute(), limit=100_000)
        item = finish_manual_phase(run, owner, control, args.checkpoint_id,
                                   json.loads(args.input_receipt_ids_json), review)
        return {'ok': True, 'checkpoint_id': item['checkpoint_id'], 'phase': item['phase'],
                'status': item['status'], 'outcome': item['outcome'], 'binding': item['binding'],
                'before': item['before']['evidence_file'], 'after': item['after']['evidence_file']}
    if args.command in ('pause', 'takeover'):
        intent_error = None
        try:
            latch_manual(run, args.reason or ('用户手动接管' if args.command == 'takeover' else '用户暂停'), rid)
        except Exception as exc:
            intent_error = str(exc)
        # Emergency broker pause is attempted even if the display/intention
        # channel failed; never falsely ACK a missing persistent runner intent.
        result = entry.emergency_pause(control, run, args.reason or '用户手动暂停')
        (run / 'runner-battle-approval.json').unlink(missing_ok=True)
        state = current_state(run, owner, control, emergency=True)
        state['broker'], state['last_command'] = result, {'id': rid, 'kind': args.command}
        return envelope(state, result.get('ok') and intent_error is None, rid, intent_error or result.get('error'))
    if args.command == 'resume':
        if not args.handoff:
            raise ValueError('继续必须明确handoff，禁止隐式自动恢复')
        raw_guard = getattr(args, 'resume_guard_json', None)
        guard = None
        try:
            if not isinstance(raw_guard, str) or not 1 <= len(raw_guard) <= 200_000:
                raise ValueError('普通继续必须明确提供--resume-guard-json，禁止自动捕获最新意图')
            guard = parse_resume_guard(json.loads(raw_guard))
        except (ValueError, TypeError) as exc:
            result = {'ok': False, 'resumed': False, 'guard_matched': False,
                      'reason_kind': 'invalid_resume_guard', 'error': str(exc)}
        else:
            result = explicit_resume(run, owner, control, rid, expected_guard=guard)
        state = current_state(run, owner, control)
        state['last_command'] = {'id': rid, 'kind': 'resume'}
        if result.get('resumed') and (manual_state(run) or state['broker']['paused'] or state['broker']['input_halted']):
            control.pause('恢复回执前新手动意图优先')
            result = {**result, 'ok': False, 'resumed': False, 'error': '恢复回执前发生新接管；保持手动'}
        state['control_mode'] = 'auto' if result.get('resumed') else 'manual'
        reply = envelope(state, result.get('resumed'), rid, result.get('error'))
        reply.update(resumed=bool(result.get('resumed')), guard_matched=bool(result.get('guard_matched')))
        reply['resume_result'] = {'resumed': bool(result.get('resumed')),
            'guard_matched': bool(result.get('guard_matched')), 'requested_guard': guard,
            'consumed_manual_ids': result.get('consumed_manual_ids', []),
            'resume_epoch': result.get('resume_epoch'), 'reason_kind': result.get('reason_kind'),
            'mismatch_field': result.get('mismatch_field'), 'error': result.get('error')}
        return reply
    if args.command == 'approve-battle':
        # This command is for an explicit human approval (GUI or the user's
        # chat instruction relayed by Codex); a planner reply never issues it.
        with file_lock(run, 'decision-submit.lock'):
            state = current_state(run, owner, control)
            request = state.get('decision_request') or {}
            policy = coaching_policy()
            epoch = (optional(run / 'runner-resume-epoch.json') or {}).get('id')
            broker = state['broker']
            if not policy['valid'] or not policy['require_battle_confirmation']:
                raise ValueError('当前没有有效开启的带教战斗确认策略；未产生授权')
            if (state['control_mode'] != 'waiting_decision' or manual_state(run)
                    or (run / 'runner-stop').exists() or (run / 'broker-stop').exists()
                    or not broker['ready'] or broker['paused'] or broker['input_halted'] or not broker['game_foreground']):
                raise ValueError('战斗批准须当前等待请求、健康前台且无暂停/停止；批准不自动恢复')
            if (args.request_id != request.get('request_id') or args.snapshot_id != request.get('snapshot_id')
                    or args.stage != battle_stage(request) or not canonical_stage(args.stage)
                    or args.resume_epoch != epoch or request.get('resume_epoch') != epoch
                    or not isinstance(args.reason, str) or not 1 <= len(args.reason.strip()) <= 1000):
                raise ValueError('用户批准须逐项匹配本run当前请求/快照/节点/epoch，且有明确批准理由')
            if (run / ('battle-approval-consumed-' + request['request_id'] + '.json')).exists():
                raise ValueError('本请求战斗授权已消费，不能补发或重复点击；先核真实结果')
            if (run / 'decision-reply.json').exists():
                raise ValueError('已有待消费计划；不向并发计划补写战斗批准')
            current = datetime.now(timezone.utc)
            deadline = datetime.fromisoformat(request['deadline_at'])
            if current >= deadline or hashlib.sha256(Path(request['original_png']).read_bytes()).hexdigest() != request['snapshot_id']:
                raise ValueError('当前请求期限/原始帧失效，未授权旧页面')
            approval = {'approval_id': rid, 'run_id': owner['run_id'], 'match_id': request['match_id'],
                'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'], 'stage': args.stage,
                'resume_epoch': epoch, 'policy_revision': policy['revision'], 'created_at': current.isoformat(),
                'expires_at': min(deadline, current + timedelta(seconds=60)).isoformat(),
                'source': 'explicit_user_approval', 'reason': args.reason.strip()}
            control.write_json(run / 'runner-battle-approval.json', approval)
        result = envelope(state, command_id=rid)
        result.update(battle_approval=approval, input_sent=False, resumed=False)
        return result
    if args.command == 'decide':
        state = current_state(run, owner, control)
        if state['control_mode'] != 'waiting_decision' or manual_state(run):
            raise ValueError('未在可接受战略回答状态；手动锁优先')
        reply = entry.read_json(Path(args.reply_file).absolute(), limit=100_000)
        validate_plan(reply, state['decision_request'], (optional(run / 'runner-resume-epoch.json') or {}).get('id'))
        with file_lock(run, 'decision-submit.lock'):
            # Refresh uses the same lock; a CLI that captured the old state
            # before retirement cannot publish that old reply afterwards.
            state = current_state(run, owner, control)
            if (state['control_mode'] != 'waiting_decision' or manual_state(run)
                    or (run / 'runner-stop').exists() or (run / 'broker-stop').exists()):
                raise ValueError('战略请求已失效/暂停/停止；未提交旧回答')
            validate_plan(reply, state['decision_request'], (optional(run / 'runner-resume-epoch.json') or {}).get('id'))
            policy = coaching_policy()
            if policy['require_battle_confirmation']:
                battle_actions = [action for action in reply['actions'] if battle_input(state['decision_request']['observation'], action)]
            else:
                battle_actions = []
            if battle_actions:
                try:
                    if len(reply['actions']) != 1:
                        raise BattleConfirmationRequired('needs_user_confirmation：进入战斗须为独立单动作计划')
                    battle_approval(run, owner, state['decision_request'],
                        (optional(run / 'runner-resume-epoch.json') or {}).get('id'), policy)
                except BattleConfirmationRequired as exc:
                    result = envelope(state, False, rid, str(exc))
                    result.update(needs_user_confirmation=True, reason_kind='needs_user_confirmation', input_sent=False)
                    return result
            if (run / 'decision-reply.json').exists():
                raise ValueError('已有待消费回答，不覆盖')
            control.write_json(run / 'decision-reply.json', reply)
        return envelope(state, command_id=rid)
    if args.command == 'stop':
        # The input broker stop flag has absolute priority over all ordinary
        # state locks and optional manual display updates.
        (run / 'broker-stop').touch()
        (run / 'runner-stop').touch()
        (run / 'runner-battle-approval.json').unlink(missing_ok=True)
        try:
            latch_manual(run, '用户停止；不自动重启', rid)
        except Exception:
            pass  # The real stop still must be verified below.
        end = time.monotonic() + 25
        while time.monotonic() < end:
            worker = entry.exit_probe(control, owner['runner_pid'], owner['runner_creation_id'])
            if worker['state'] in ('absent', 'exited', 'reused'):
                state = current_state(run, owner, control, emergency=True)
                broker = state['broker']['broker_state']
                if broker['state'] == 'unknown':
                    if (state['broker'].get('broker_pid') is not None
                            or getattr(control, 'EMERGENCY_BROKER_IDENTITY', None) is not None):
                        raise RuntimeError('已知所属broker退出证据未知；保留停止/手动锁，不采用展示终态替代原身份')
                    # A never-launched broker can only be certified by the
                    # exact worker's final record, not by a missing ready file.
                    try:
                        final = optional(CURRENT)
                    except (OSError, ValueError):
                        final = None
                    if (final and final.get('run_id') == owner['run_id']
                            and final.get('runner_pid') == owner['runner_pid']
                            and final.get('runner_creation_id') == owner['runner_creation_id']):
                        candidate = final.get('exit_evidence', {}).get('broker')
                        if not isinstance(candidate, dict) or candidate.get('state') not in ('not_launched', 'launch_failed'):
                            raise RuntimeError('没有broker身份时只接受同worker/run的完整启动事实，不采用展示退出字串')
                        broker = candidate
                if broker['state'] not in ('absent', 'exited', 'reused', 'launch_failed', 'not_launched'):
                    raise RuntimeError('worker退出但broker退出仍未核实')
                if broker['state'] in ('not_launched', 'launch_failed'):
                    if (broker.get('run_id') != owner['run_id'] or broker.get('worker_pid') != owner['runner_pid']
                            or broker.get('worker_creation_id') != owner['runner_creation_id']
                            or broker.get('launch_id') != owner['launch_id'] or broker.get('identity_observed') is not False
                            or (broker['state'] == 'not_launched' and broker.get('launch_attempted') is not False)
                            or (broker['state'] == 'launch_failed' and (broker.get('launch_attempted') is not True
                                or type(broker.get('launch_exit_code')) is not int or broker['launch_exit_code'] == 0))):
                        raise RuntimeError('没有broker身份的启动终态须关联同worker/run且有实际启动事实')
                state['control_mode'] = 'stopped'
                state['exit_evidence'] = {'worker': worker, 'broker': broker}
                control.write_json(CURRENT, state)
                return envelope(state, command_id=rid)
            if worker['state'] == 'unknown':
                raise RuntimeError('worker退出证据未知；未宣称停止')
            time.sleep(.05)
        raise TimeoutError('停止请求已锁定，但25秒内退出未确认；不启动新worker')
    raise ValueError('未知命令')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'status', 'pause', 'takeover', 'resume', 'stop', 'decide', 'approve-battle', 'manual-checkpoint', 'manual-result', '_worker'])
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--run-dir')
    parser.add_argument('--run-token')
    parser.add_argument('--max-seconds', type=int, default=7200)
    parser.add_argument('--max-matches', type=int, default=1)
    parser.add_argument('--continue-matches', action='store_true')
    parser.add_argument('--handoff', action='store_true')
    parser.add_argument('--resume-guard-json')
    parser.add_argument('--reply-file')
    parser.add_argument('--reason', default='')
    parser.add_argument('--launch-id')
    parser.add_argument('--request-id')
    parser.add_argument('--snapshot-id')
    parser.add_argument('--stage')
    parser.add_argument('--resume-epoch')
    parser.add_argument('--manual-id')
    parser.add_argument('--phase', choices=coaching.PHASES)
    parser.add_argument('--checkpoint-id')
    parser.add_argument('--input-receipt-ids-json', default='[]')
    args = parser.parse_args()
    if args.command == '_worker':
        worker_cli(args)
        return
    if args.command == 'start':
        result = start_cli(args)
    else:
        if not args.run_dir or not args.run_token:
            raise ValueError('运行命令须有独占run与token')
        result = command_cli(args)
    print(json.dumps(result, ensure_ascii=False))
    if not result['ok']:
        raise SystemExit(2)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok': False, 'command_id': uuid.uuid4().hex, 'state': {}, 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
