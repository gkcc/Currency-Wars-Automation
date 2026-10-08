"""Bounded local workflow, explicit strategy plans, one safety broker."""
from __future__ import annotations

import argparse
import contextlib
import copy
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
import currency_wars_manual_steps as manual_steps
import currency_wars_manual_stage as manual_stage
from currency_wars_source_guard import activity, verify_runtime_sources
from currency_wars_perception import (Perception, clean, find_text, hash_distance, GOLD_HUD,
                                     valid_population_counts, READ_CONTRACT_VERSION)
from currency_wars_shop_reader import purchase_slot
from currency_wars_state_reader import native_slots
import currency_wars_coaching as coaching
import currency_wars_economy as economy
from currency_wars_progression import progression_plan
from currency_wars_profile import ProfileRecorder, read_events, summarize_events, write_report
from currency_wars_visual_guards import (stable_semantic_plan, stable_semantic_target, navigation_target,
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


def full_observation(observed):
    contract = observed.get('read_contract')
    # Old saved observations and default-full protocol fixtures predate scope.
    # Explicitly unread data never qualifies through that compatibility path.
    if any((observed.get('semantic', {}).get(key) or {}).get('status') == 'not_read'
           for key in ('team', 'inventory', 'player_hud')):
        return False
    return contract is None or (contract.get('version') == READ_CONTRACT_VERSION
        and contract.get('effective_scope') == 'full' and not contract.get('unread'))


def optional(path):
    try:
        return entry.read_json(path)
    except FileNotFoundError:
        return None


BUSINESS_SCHEMA = 'currency-wars-business/v1'
BUSINESS_REVIEW_PAGES = ('preparation', 'shop', 'settlement', 'settlement_grade', 'lobby', 'opponents',
    'environment', 'investment', 'supply', 'reward_overlay', 'node_result', 'boss_result', 'plane_intro',
    'guide', 'unit_gear', 'investment_summary', 'update_notice') + tuple(name for name, unused in PANELS)


def durable_records(state):
    """Only this project's original, owner-bound replay directory is durable."""
    records = Path(state.get('journal_file', '')).parent.resolve()
    if records.parent != (PROJECT / 'debug').resolve() or not records.is_dir():
        raise ValueError('业务记录不在本项目原始回放目录')
    owner = entry.read_json(records / 'owner.json')
    for key in ('chat_id', 'run_id', 'runner_pid', 'runner_creation_id'):
        if owner.get(key) != state.get(key):
            raise ValueError('业务记录与原运行身份不一致：' + key)
    return records


def load_business(path, chat_id):
    path = Path(path).resolve()
    if path.parent.parent != (PROJECT / 'debug').resolve() or not path.name.startswith('business-'):
        raise ValueError('业务检查点路径不属于本项目')
    value = entry.read_json(path)
    if (value.get('schema') != BUSINESS_SCHEMA
            or not isinstance(value.get('match_id'), str) or not value['match_id']
            or type(value.get('revision')) is not int or not isinstance(value.get('leases'), list)
            or not value['leases'] or len(value['leases']) > 128):
        raise ValueError('业务检查点身份/版本/有界租期历史无效')
    if value['leases'][-1].get('chat_id') != chat_id:
        raise ValueError('业务当前归属会话不匹配')
    return path, value


def old_lease_exit(state, control):
    """Probe exact original identities; never terminate a PID discovered here."""
    def probe(pid, creation, label):
        if type(pid) is not int or pid <= 0 or not str(creation).isdecimal() or int(creation) <= 0:
            raise ValueError('旧' + label + '缺少PID及创建身份')
        found = control.process_probe(pid, str(creation))
        if found.get('state') not in ('absent', 'exited', 'reused'):
            raise RuntimeError('旧' + label + '仍在运行或退出未知，禁止新租期')
        return {**found, 'expected_creation_id': str(creation)}
    worker = probe(state.get('runner_pid'), state.get('runner_creation_id'), 'worker')
    run = Path(state.get('run_dir', ''))
    identity = optional(run / 'broker-process.json') if run.is_dir() else None
    retained = state.get('broker_identity') or {}
    if identity:
        if retained and (retained.get('pid') != identity.get('pid')
                or str(retained.get('creation_id')) != str(identity.get('creation_id'))):
            raise ValueError('原broker持久创建身份冲突，不能选择较方便的退出证据')
        # These are the same owner fields used by entry.owned_broker_identity.
        runtime_owner = entry.read_json(run / 'runner-owner.json')
        if any(runtime_owner.get(k) != state.get(k) for k in ('run_id', 'runner_pid', 'runner_creation_id')):
            raise ValueError('旧broker运行目录归属不符')
        if (identity.get('chat_id') != runtime_owner.get('chat_id')
                or not secrets.compare_digest(str(identity.get('run_token', '')), str(runtime_owner.get('run_token', '')))):
            raise ValueError('旧broker持久身份不属于原owner')
        broker = probe(identity.get('pid'), identity.get('creation_id'), 'broker')
    else:
        evidence = state.get('exit_evidence', {}).get('broker', {})
        if retained:
            # Known original identity outranks a contradictory display/start
            # sentinel. A missing runtime never erases its exit obligation.
            broker = probe(retained.get('pid'), retained.get('creation_id'), 'broker')
        elif evidence.get('state') in ('not_launched', 'launch_failed'):
            if (evidence.get('run_id') != state.get('run_id')
                    or evidence.get('worker_pid') != state.get('runner_pid')
                    or str(evidence.get('worker_creation_id')) != str(state.get('runner_creation_id'))
                    or evidence.get('launch_id') != state.get('launch_id')
                    or evidence.get('identity_observed') is not False
                    or evidence['state'] == 'not_launched' and evidence.get('launch_attempted') is not False
                    or evidence['state'] == 'launch_failed' and (evidence.get('launch_attempted') is not True
                        or type(evidence.get('launch_exit_code')) is not int or evidence['launch_exit_code'] == 0)):
                raise ValueError('旧broker未启动/失败证据不完整')
            broker = evidence
        else:
            broker = probe(evidence.get('pid'), evidence.get('expected_creation_id'), 'broker')
    return {'worker': worker, 'broker': broker}


def archive_business_receipt(records, owner, item, control):
    """Retain the original request and receipt, without transporting its token."""
    rid = item.get('id')
    if (not isinstance(rid, str) or not 1 <= len(rid) <= 100
            or not isinstance(item.get('request'), dict) or item['request'].get('id') != rid
            or item['request'].get('chat_id') != owner['chat_id']):
        raise ValueError('待归档原请求身份不符')
    directory = records / 'business-receipts'
    directory.mkdir(exist_ok=True)
    path = directory / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
    archived = {**redact(item), 'origin': {key: owner[key]
                for key in ('run_id', 'chat_id', 'runner_pid', 'runner_creation_id')}}
    previous = optional(path)
    if previous:
        if previous['origin'] != archived['origin'] or previous['request'] != archived['request']:
            raise ValueError('原请求归档身份冲突，不能覆盖')
        if previous.get('result') is not None and previous != archived:
            raise ValueError('原终态收据不可改写')
    if previous != archived:
        control.write_json(path, archived)
    return path


def archive_business_run(state, control):
    """Run before scratch creation/cleanup, including after a worker crash."""
    records = durable_records(state)
    run = Path(state['run_dir'])
    if run.is_dir():
        owner = entry.read_json(run / 'runner-owner.json')
        if any(owner.get(k) != state.get(k) for k in ('run_id', 'chat_id', 'runner_pid', 'runner_creation_id')):
            raise ValueError('原运行目录身份变化，不能归档其他run')
        paths = list((run / 'request-ledger').glob('*.json'))
        if len(paths) > 4096:
            raise ValueError('业务原收据超出有界容量，保留运行目录')
        for path in paths:
            archive_business_receipt(records, owner, entry.read_json(path), control)
        capacity = optional(run / 'reward-capacity.json')
        if capacity:
            control.write_json(records / 'business-reward-capacity.json', redact(capacity))
        reward = optional(run / 'reward-step.json')
        if reward:
            control.write_json(records / 'business-reward-step.json', redact(reward))
        last_result = optional(run / 'result.json')
        if last_result:
            # A broker can finish before the caller folds this reply into its
            # ledger. Preserve it separately; do not invent a completed ledger.
            control.write_json(records / 'business-last-result.json',
                {'origin_run_id': state['run_id'], 'result': redact(last_result)})
    control.write_json(records / 'business-archive.json', {'run_id': state['run_id'],
        'archived_at': now(), 'runtime_present': run.is_dir(), 'missing_evidence_is_unknown': True,
        'evidence_scope': 'original_requests_and_receipts; no transport_frame_copy',
        'manual_records_directory': str(records), 'runtime_only_sources_may_be_missing': True})
    return records


def pending_business_requests(business):
    """Every historical unresolved identity stays visible; no new-run alias."""
    pending = {}
    for lease in business['leases']:
        records = durable_records(lease)
        for path in (records / 'business-receipts').glob('*.json'):
            item = entry.read_json(path)
            if item['id'] in lease.get('entry_receipt_watermark', []):
                continue  # Earlier business in this same bounded process.
            request = item['request']
            if item.get('result') is None or manual_receipt_state(item)['unknown_input']:
                key = lease['run_id'] + ':' + item['id']
                pending[key] = {'origin_run_id': lease['run_id'], 'request_id': item['id'],
                    'receipt_file': str(path), 'outcome': 'unknown', 'amount': None}
        capacity = optional(records / 'business-reward-capacity.json')
        if capacity and capacity.get('status') not in ('verified', 'refused', 'superseded') and capacity.get('input_request_id'):
            rid = capacity['input_request_id']
            pending[lease['run_id'] + ':' + rid] = {'origin_run_id': lease['run_id'], 'request_id': rid,
                'outcome': 'unknown', 'kind': 'reward_capacity', 'amount': None,
                'record_file': str(records / 'business-reward-capacity.json')}
        for path in records.glob('reward-step-*.json'):
            reward = entry.read_json(path)
            if (reward.get('request_id') in lease.get('entry_receipt_watermark', [])
                    or reward.get('match_id') != business['match_id']):
                continue  # Keep old evidence without assigning it to a later match.
            if reward.get('status') not in ('verified', 'refused') and reward.get('request_id'):
                rid = reward['request_id']
                pending[lease['run_id'] + ':' + rid] = {'origin_run_id': lease['run_id'], 'request_id': rid,
                    'outcome': 'unknown', 'kind': 'reward', 'amount': None, 'record_file': str(path)}
    for stage, stored in business.get('economy', {}).items():
        item = stored['ledger'].get('pending')
        if item and item.get('request_id'):
            pending[stored['run_id'] + ':' + item['request_id']] = {
                'origin_run_id': stored['run_id'], 'request_id': item['request_id'], 'outcome': 'unknown',
                'kind': item.get('kind'), 'stage': stage, 'amount': None,
                'planned_cost_not_actual': item.get('cost'), 'record_file': stored['record_file'],
                'receipt_available': (Path(stored['record_file']).parent / 'business-receipts' /
                    (hashlib.sha256(item['request_id'].encode()).hexdigest() + '.json')).is_file()}
        for old in stored['ledger'].get('prior_run_unknown', []):
            pending[old['origin_run_id'] + ':' + old['request_id']] = old
    return [pending[key] for key in sorted(pending)]


def prepare_business_start(state, control, target_chat_id=None):
    pointer = state.get('business', {}).get('checkpoint')
    if pointer:
        path, business = load_business(pointer, state['chat_id'])
        previous = business['leases'][-1]
        if any(previous.get(k) != state.get(k) for k in ('run_id', 'runner_pid', 'runner_creation_id')):
            raise ValueError('当前运行与业务归属已变化；不迟到续接')
        discovered_identity, retained_identity = state.get('broker_identity'), previous.get('broker_identity')
        if discovered_identity and retained_identity and (
                discovered_identity.get('pid') != retained_identity.get('pid')
                or str(discovered_identity.get('creation_id')) != str(retained_identity.get('creation_id'))):
            raise ValueError('CURRENT与业务保留的原broker创建身份冲突')
        state = {**state, 'broker_identity': retained_identity or discovered_identity}
    exits = old_lease_exit(state, control)
    records = archive_business_run(state, control)  # Before stale scratch sweeping.
    if pointer:
        if business['leases'][-1]['run_id'] != state['run_id'] or business['match_id'] != state['match_id']:
            raise ValueError('当前运行与业务归属已变化；不迟到续接')
    else:
        # Upgrade a pre-B007 replay conservatively. Old UI completion is not a
        # verified whole-match result and never closes this imported business.
        if not isinstance(state.get('match_id'), str) or not state['match_id']:
            raise ValueError('旧运行缺少真实match身份，不能默认为新局')
        path = records / ('business-' + state['match_id'] + '.json')
        business = {'schema': BUSINESS_SCHEMA, 'chat_id': state['chat_id'], 'match_id': state['match_id'],
            'revision': 0, 'status': 'active', 'leases': [business_lease(state)], 'economy': {}}
    # The per-node write precedes the business summary. Recover that atomic
    # ledger after a crash between these writes instead of losing real spent.
    for candidate in records.glob('economy-*.json'):
        value = entry.read_json(candidate)
        if value.get('run_id') == state['run_id'] and value.get('match_id') == state['match_id']:
            if value.get('schema') != economy.SCHEMA or not canonical_stage(value.get('stage')) or not isinstance(value.get('ledger'), dict):
                raise ValueError('原节点经济台账无效，不将已有花费清零')
            business['economy'][value['stage']] = {**value, 'record_file': str(candidate)}
    with file_lock(path.parent, 'business.lock'):
        if pointer and entry.read_json(path)['revision'] != business['revision']:
            raise ValueError('业务检查点CAS已变化')
        business['leases'][-1]['exit_evidence'] = exits
        if state.get('broker_identity'):
            # The child's pre-scratch check reads this lease independently of
            # CURRENT; do not lose an identity known only by discovery.
            business['leases'][-1]['broker_identity'] = copy.deepcopy(state['broker_identity'])
        business['revision'] += 1
        control.write_json(path, business)
    return {'checkpoint': str(path), 'revision': business['revision'], 'previous_run_id': state['run_id'],
            'previous_chat_id': state['chat_id'], 'target_chat_id': target_chat_id or state['chat_id']}


def business_lease(state):
    return {key: redact(state[key]) for key in ('chat_id', 'run_id', 'runner_pid', 'runner_creation_id',
        'launch_id', 'run_dir', 'journal_file', 'control_mode', 'exit_evidence', 'broker_identity',
        'entry_receipt_watermark') if key in state}


def retain_stop_business(state, owner):
    """Keep authenticated durable facts when runtime cleanup leaves a display shell."""
    retained = optional(CURRENT)
    if retained is None:
        return state  # Missing facts stay missing; never reconstruct a replay path.
    identity = ('chat_id', 'run_id', 'runner_pid', 'runner_creation_id')
    if any(retained.get(key) != owner.get(key) for key in identity):
        raise ValueError('停止记录已属于另一owner/run/创建身份；不覆盖CURRENT')
    merged = {**retained, **state}
    # The state channel can be older than the final CURRENT written after the
    # scratch context exits. Neither may silently replace durable identities.
    for key in ('launch_id', 'run_dir', 'journal_file', 'match_id', 'broker_identity'):
        if key in retained and key in state and retained[key] != state[key]:
            raise ValueError('停止状态与原业务字段冲突：' + key)
    if retained.get('journal_file'):
        durable_records(retained)
    pointer = retained.get('business', {}).get('checkpoint')
    if pointer:
        state_pointer = state.get('business', {}).get('checkpoint')
        if state_pointer and state_pointer != pointer:
            raise ValueError('停止状态与原业务检查点冲突')
        unused, business = load_business(pointer, owner['chat_id'])
        lease = business['leases'][-1]
        if any(lease.get(key) != owner.get(key) for key in identity):
            raise ValueError('停止时业务当前租期已变化；不采用旧或外来lease')
        durable_records(lease)
        for key in ('launch_id', 'run_dir', 'journal_file', 'broker_identity', 'entry_receipt_watermark'):
            if key in lease:
                if key in merged and merged[key] != lease[key]:
                    raise ValueError('停止状态与业务lease字段冲突：' + key)
                merged[key] = copy.deepcopy(lease[key])
        if merged.get('match_id', business['match_id']) != business['match_id']:
            raise ValueError('停止状态与业务match身份冲突')
        merged['match_id'] = business['match_id']
        merged['business'] = copy.deepcopy(retained['business'])
    if 'cleanup' in retained:
        merged['cleanup'] = copy.deepcopy(retained['cleanup'])
    # Current process probes/control mode win over old lease display values.
    return merged


class BattleConfirmationRequired(ValueError):
    pass


class ManualReviewDeferred(ValueError):
    """The identity is intact; a new current observation/review is still needed."""


class RewardEffectPending(ValueError):
    """Delivery is known; only the bounded read-only effect check may continue."""


class GuardedSubmission:
    """Delegate unchanged Entry semantics; guard only its locked publication."""
    def __init__(self, control, guard, publication_span=None):
        self.control, self.guard, self.publication_span = control, guard, publication_span
        self.publication_attempted = False
        self.business_guarded_submission = True

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
        with self.publication_span() if self.publication_span else contextlib.nullcontext():
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
            if values in ([27], [68], [70]):
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
    open_population = bool(count and valid_population_counts(int(count[1]), int(count[2]))
                           and int(count[1]) < int(count[2]))
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
    # Ranking describes useful gaps; only Worker.economic_policy may turn them
    # into spendable actions after the current budget and dependencies exist.
    return {'phase': 'equipment_and_lineup_check' if missing else 'improve_current_power',
            'needs_user_guidance': missing, 'inspection_actions': actions,
            'purchase_candidates': candidates, 'economic_actions': economic_actions,
            'recommended_actions': actions[:1] or [value['action'] for value in candidates[:1]] or economic_actions[:1],
            'operating_rules': rules, 'strategy_phase': phase_evidence,
            'reroll_allowed': phase_evidence['reroll_allowed'] and phase not in rules.get('no_reroll_phases', []),
            'economy_policy': '现成缺口购买→免费刷新并处理缺口→有停止条件的付费搜牌→人口经验；共享本节点实花预算，标准常规保50、关键缺口可明确例外',
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
    marker = artifacts.read_marker(run, root=run.parent)
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


def manual_receipt_state(receipt):
    """Classify delivery evidence, independently of any business postcondition.

    Empty completed is not zero input. A dispatched action may fail after
    SendInput, and handoff itself may send Alt. Never reconstruct those facts
    from a screenshot or an exception's text.
    """
    request, result = receipt.get('request'), receipt.get('result')
    state = {'request_id': receipt.get('id'), 'state': 'unknown', 'unknown_input': True,
             'input_attempted': result.get('input_attempted') if isinstance(result, dict) else None}
    if (not isinstance(request, dict) or not isinstance(result, dict) or type(result.get('ok')) is not bool
            or request.get('id') != receipt.get('id') or result.get('id') != receipt.get('id')):
        return state
    completed = result.get('completed')
    if request.get('kind') == 'resume':
        if (request.get('handoff') is True and 'expected_pause_id' in request
                and (request['expected_pause_id'] is None or isinstance(request['expected_pause_id'], str))
                and 'actions' not in request and result.get('ok') is True
                and result.get('resumed') is True and completed == []):
            return {**state, 'state': 'control', 'unknown_input': False}
        return state
    actions = request.get('actions')
    if (request.get('kind') != 'actions' or not isinstance(actions, list) or not actions
            or any(not isinstance(action, dict) or action.get('type') not in ('observe', 'wait', 'click', 'key', 'drag', 'scroll')
                   or not isinstance(action.get('args'), list) for action in actions)
            or not isinstance(completed, list) or completed != actions[:len(completed)]):
        return state
    physical = lambda values: [action for action in values if action['type'] not in ('observe', 'wait')]
    done, planned = physical(completed), physical(actions)
    attempted = result.get('attempted_actions')
    if result.get('input_attempted') is False and (done or request.get('handoff') is True or attempted not in (None, [])):
        return state
    if (request.get('handoff') is not True and not done
            and (all(action['type'] == 'observe' for action in actions)
                 or result.get('input_attempted') is False and attempted == [])):
        if result.get('input_attempted') is not True and attempted in (None, []):
            return {**state, 'state': 'zero_input', 'unknown_input': False}
    if completed == actions and result.get('ok') is True:
        if attempted is not None and attempted != planned:
            return state
        return {**state, 'state': 'completed', 'unknown_input': False}
    # A failed guard after a completed prefix can leave the next action wholly
    # unattempted. An extra attempted action makes its effect unknown instead.
    if (result.get('ok') is False and done and attempted == done
            and result.get('input_attempted') is True and request.get('handoff') is not True):
        return {**state, 'state': 'partial' if completed != actions else 'completed', 'unknown_input': False}
    return state


def _manual_records(run, owner):
    state = entry.read_json(run / 'runner-state.json')
    records = Path(state['journal_file']).parent
    if (state.get('run_id') != owner['run_id'] or state.get('chat_id') != owner['chat_id']
            or records.resolve().parent != (PROJECT / 'debug').resolve()
            or entry.read_json(records / 'owner.json').get('run_id') != owner['run_id']):
        raise ValueError('人工结果持久证据目录不属于当前run')
    return records, state


def _manual_save(run, records, control, item):
    target = run / 'manual-results' / (item['checkpoint_id'] + '.json')
    control.write_json(target, item)
    control.write_json(records / ('manual-' + item['checkpoint_id'] + '.json'), redact(item))


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
    if pending:
        # A timed-out original client may have left its exact late result only
        # in result.json. Fold that result under the SAME mutex before a new
        # observation replaces the shared notification. Never rewrite a known
        # result, request payload, or issue another input.
        with entry.submission_lease(control, timeout=min(2, max(0, end-time.monotonic()))) as leased:
            for rid in pending:
                path = run / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
                stored = entry.read_json(path)
                current = await_existing_receipt(run, leased, rid, 0)
                if stored.get('request') != current.get('request'):
                    raise ValueError('原请求在对账期间改变；不覆盖')
                if stored.get('result') is None:
                    leased.write_json(path, current)
                elif stored != current:
                    raise ValueError('原终态回执冲突；不覆盖')
    return reconciled


def _manual_binding(run, owner, control, manual_id, *, resolve_stage=True):
    manual = manual_state(run)
    records, state = _manual_records(run, owner)
    if (not manual or manual.get('manual_id') != manual_id
            or state.get('run_id') != owner['run_id'] or state.get('chat_id') != owner['chat_id']
            or (run / 'runner-stop').exists() or (run / 'broker-stop').exists()):
        raise ValueError('当前接管/所属run已改变；人工结果保持未知')
    stage = canonical_stage(state.get('preparation_stage'))
    if not stage or not state.get('match_id'):
        raise ValueError('没有当前局/节点绑定；人工结果保持未知')
    binding = {'run_id': owner['run_id'], 'match_id': state['match_id'], 'stage': stage,
               'manual_id': manual_id, 'old_epoch': (optional(run / 'runner-resume-epoch.json') or {}).get('id')}
    if resolve_stage:
        binding = manual_stage.resolve_binding(run, owner, control, binding)
    return binding, records


def _manual_capture(run, owner, control, binding, records, label, reader):
    rid = uuid.uuid4().hex
    # Existing broker observe is read-only, including while paused. This API
    # never focuses, resumes or performs a desktop input.
    target = records / (label + '-' + rid + '.png')
    with entry.submission_lease(control) as leased:
        result = entry.request(leased, 'actions', ['observe'], rid, False)
        if not result.get('ok') or not result.get('observation'):
            raise entry.ObservationUnavailable('人工结果缺少同请求的新观察；仅补观察，不重发输入')
        # Keep the transaction watermark at this exact observation. The image
        # is immutable, but a newer input must not be hidden in its watermark.
        if (optional(run / 'result.json') or {}).get('id') != rid:
            raise ValueError('人工观察后已有新请求；重新观察，不继承旧水位')
        data = entry.observation_frame(run, result).read_bytes()
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
    try:
        entry.release_observation(run, result)
    except (OSError, ValueError):
        pass  # A release failure preserves evidence; it never repeats input.
    return {'receipt_id': rid, 'observed_at': now(), 'snapshot_id': digest,
            'captured_at': result['observation']['captured_at'], 'frame_id': result['observation']['frame_id'],
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
    item = {'schema': 'manual-preparation-result/v1', 'receipt_protocol': 2,
            'checkpoint_id': checkpoint_id, 'binding': binding,
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
            or review.get('stage') != binding['stage'] or type(review.get('completed')) is not bool
            or review.get('reviewer') != 'supervising_agent'
            or not isinstance(review.get('findings'), str) or not review['findings'].strip()):
        raise ValueError('人工结果须监督助手逐阶段实际复核')
    reported = review.get('outcome', 'success' if review['completed'] else 'unknown')
    if (reported not in ('success', 'no_effect', 'partial', 'unknown')
            or review['completed'] != (reported == 'success')):
        raise ValueError('人工结果须区分成功/零效果/部分/未知，只有成功可请求完成阶段')
    receipts = []
    end = time.monotonic() + 5
    paths = list((run / 'request-ledger').glob('*.json'))
    if len(paths) > 4096:
        raise ValueError('回执数量超出有界容量')
    ledger = {value['id']: value for value in (entry.read_json(path) for path in paths)}
    actual_ids = set(ledger) - set(item['prior_receipt_ids'])
    if len(actual_ids) > 128:
        raise ValueError('单个人工checkpoint超过128张回执；保持未知，不截断证据')
    if set(input_receipt_ids) - actual_ids:
        raise ValueError('所列输入缺少checkpoint之后的原回执；不能把缺失当零输入')
    unresolved = []
    for rid in sorted(actual_ids):
        try:
            receipts.append(await_existing_receipt(run, control, rid, end - time.monotonic()))
        except TimeoutError:
            receipts.append(ledger[rid])
            unresolved.append(rid)
    states = [manual_receipt_state(receipt) for receipt in receipts]
    # Observe-only receipts and positive zero-input refusals may be discovered
    # during reconciliation. Every possible input/control request must still
    # be declared, including failed and partially executed requests.
    omitted = [value['request_id'] for value in states
               if value['request_id'] not in input_receipt_ids and value['state'] != 'zero_input']
    if omitted:
        raise ValueError('checkpoint之后有未列出的可能输入回执；人工结果保持PENDING')
    after, capture_error = None, None
    if unresolved:
        capture_error = '既有请求仍未返回终态：' + ','.join(unresolved)
    else:
        try:
            _drain_manual_receipts(run, control)
            after = _manual_capture(run, owner, control, binding, records, 'manual-' + checkpoint_id + '-after', reader or Perception())
        except (entry.ObservationUnavailable, OSError, TimeoutError) as exc:
            capture_error = str(exc)
    current = entry.read_json(path)
    latest_binding, _ = _manual_binding(run, owner, control, binding['manual_id'])
    if current != item or current.get('status') != 'pending' or latest_binding != binding:
        raise ValueError('人工结果提交前checkpoint状态/交接身份已变；未覆盖')
    if after and set(after['receipt_watermark']) - set(item['prior_receipt_ids']) != actual_ids | {after['receipt_id']}:
        raise ValueError('人工后帧之前有未列出的回执；保持PENDING')
    unknown = any(value['unknown_input'] for value in states)
    # A no-effect claim names the actual dynamic fields compared, not the PNG
    # hash. This is evidence about those fields, never all game side effects.
    fields = review.get('effect_fields', [])
    comparisons = {}
    if (after and isinstance(fields, list) and 1 <= len(fields) <= 5
            and all(field in ('coins', 'xp', 'level', 'deployed', 'hp') for field in fields)):
        for field in fields:
            before_value = item['before']['observation'].get('fields', {}).get(field)
            after_value = after['observation'].get('fields', {}).get(field)
            comparisons[field] = {'before': before_value, 'after': after_value,
                                  'unchanged': before_value is not None and before_value == after_value}
    if capture_error or unknown:
        outcome = 'unknown'
    elif reported == 'success':
        outcome = 'success'
    elif reported == 'no_effect' and (states and all(value['state'] == 'zero_input' for value in states)
                                     or comparisons and all(value['unchanged'] for value in comparisons.values())):
        outcome = 'no_effect'
    elif reported == 'partial' and any(value['state'] in ('completed', 'partial') for value in states):
        outcome = 'partial'
    else:
        outcome = 'unknown'
    updated = {**item, 'status': 'completed' if outcome == 'success' else 'pending',
        'review': review, 'reported_outcome': reported, 'outcome': outcome,
        'input_receipts': redact(receipts), 'receipt_states': states,
        'effect_observations': comparisons, 'effect_scope': 'listed_dynamic_fields_only', 'input_resent': False,
        'native_automation_gate_passed': False, 'needs_fresh_revalidation': True}
    if after:
        updated.update(after=after, image_changed=item['before']['snapshot_id'] != after['snapshot_id'])
        updated.pop('observation_error', None)
    else:
        updated.pop('after', None)
        updated.pop('image_changed', None)
        updated['observation_error'] = capture_error
    _manual_save(run, records, control, updated)
    return updated


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


def verified_resume_event(run, owner, control, epoch):
    """Read the exact successful CAS/epoch commit, not any successful resume."""
    rid = epoch.get('id')
    if not isinstance(rid, str) or epoch.get('resume_event') != rid:
        raise ValueError('恢复缺少绑定原CAS的持久事件；当前监督复核，不继承旧完成')
    target = run / 'resume-events' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
    event = entry.read_json(target)
    guard = parse_resume_guard(event.get('guard'))
    receipt = await_existing_receipt(run, control, rid, 0)
    state = control.status()
    if (event.get('schema') != 'manual-resume-event/v1' or event.get('run_id') != owner['run_id']
            or event.get('new_epoch') != rid or event.get('old_epoch') != epoch.get('previous_epoch')
            or event.get('recorded_at') != epoch.get('time')
            or event.get('consumed_manual_ids') != guard['pending_manual_ids']
            or epoch.get('consumed_manual_id') not in guard['pending_manual_ids']
            or not set(guard['pending_manual_ids']).issubset(epoch.get('consumed_manual_ids', []))
            or guard['run_id'] != owner['run_id'] or guard['resume_epoch'] != epoch.get('previous_epoch')
            or guard['broker_pid'] != state.get('broker_pid')
            or guard['broker_creation_id'] != str(state.get('broker_creation_time'))
            or manual_receipt_state(receipt)['state'] != 'control'
            or receipt['request'].get('expected_pause_id') != guard['broker_pause_id']
            or redact(receipt) != event.get('receipt')):
        raise ValueError('恢复事件的请求/CAS/进程/前后epoch身份不符')
    return event, receipt


def explicit_resume(run, owner, control, rid, expected_manual_id=None, expected_broker_pause_id=entry.UNSET,
                    expected_guard=entry.UNSET):
    if not isinstance(rid, str) or not 1 <= len(rid) <= 100:
        raise ValueError('恢复请求身份无效')
    event_path = run / 'resume-events' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
    if event_path.exists():
        return {'ok': False, 'resumed': False, 'guard_matched': False,
                'error': '恢复请求已有持久事件；只对账原结果，不重发交接'}
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
            except entry.SubmissionQueueTimeout:
                # Only acquisition failed; no request/ledger was published.
                # Retain the original five-second resume deadline and CAS.
                continue
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
            # All old strategy replies become invalid across explicit handoff.
            old = optional(run / 'runner-resume-epoch.json') or {}
            consumed_ids = sorted(set(old.get('consumed_manual_ids', [])) | captured_ids)
            records, observed_state = _manual_records(run, owner)
            receipt = await_existing_receipt(run, control, rid, 0)
            committed_at = now()
            event = {'schema': 'manual-resume-event/v1', 'run_id': owner['run_id'],
                'match_id': observed_state.get('match_id'), 'stage': observed_state.get('preparation_stage'),
                'old_epoch': captured_resume_epoch, 'new_epoch': rid,
                'guard': captured_guard, 'consumed_manual_ids': sorted(captured_ids),
                'receipt': redact(receipt), 'recorded_at': committed_at, 'input_resent': False}
            try:
                event_path.parent.mkdir(exist_ok=True)
                if event_path.exists():
                    raise ValueError('恢复事件已存在；不覆盖原CAS')
                control.write_json(event_path, event)
                control.write_json(records / ('resume-' + hashlib.sha256(rid.encode()).hexdigest() + '.json'), event)
                control.write_json(run / 'runner-resume-epoch.json', {'id': rid, 'time': committed_at,
                    'previous_epoch': old.get('id'), 'consumed_manual_id': epoch,
                    'consumed_manual_ids': consumed_ids, 'resume_event': rid})
                (run / 'runner-manual.json').unlink(missing_ok=True)
            except Exception:
                control.pause('恢复结果持久记录未完成；只对账，不重发交接')
                raise
        return {**result, 'guard_matched': True, 'resume_epoch': rid, 'consumed_manual_ids': consumed_ids}
    finally:
        (run / 'runner-resuming.json').unlink(missing_ok=True)


def start_cli(args):
    with activity(PROJECT, 'start') as lease:
        reviewed_runtime_sources(args.chat_id)
        return _start_cli(args, lease)


def reviewed_runtime_sources(chat_id):
    """Normal startup consumes the same review and bytes as native GUI ready.

    Emergency pause/takeover/stop deliberately do not depend on this gate.
    A source activity lease serializes updates; it is not a byte approval.
    """
    ready = entry.read_json(PROJECT / 'docs/RUNNER_READY.json', limit=200_000)
    review = ready.get('independent_review', {})
    if (ready.get('ready') is not True or ready.get('owner') != 'currency-wars-runner'
            or ready.get('chat_id') != chat_id or not isinstance(review, dict)
            or review.get('status') != 'PASS' or not review.get('reviewer_chat_id')
            or not review.get('reviewed_at')):
        raise ValueError('启动须有归属一致的独立审查及完整来源记录')
    return verify_runtime_sources(PROJECT, ready)


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
        continuation = None
        if discovered:
            probe = control.process_probe(discovered['runner_pid'], discovered['runner_creation_id'])
            if probe['state'] == 'unknown':
                raise RuntimeError('旧worker退出未知，禁止启动第二个')
            if probe['state'] == 'running':
                if discovered.get('chat_id') != args.chat_id:
                    raise RuntimeError('另一会话所属worker仍在运行，不启动第二控制器')
                owner = entry.read_json(Path(discovered['run_dir'], 'runner-owner.json'))
                run, owner, binding, c = load(discovered['run_dir'], args.chat_id, owner['run_token'])
                return envelope(current_state(run, owner, c))
            continuation = prepare_business_start(discovered, control, args.chat_id)
        inherited = getattr(args, 'runtime_location_json', None)
        runtime_location = input_bridge.runtime_location(entry.PINNED,
            inherited=json.loads(inherited) if inherited is not None else None)
        launch = uuid.uuid4().hex
        command = [sys.executable, '-B', '-X', 'utf8', str(SELF), '_worker',
                   '--chat-id', args.chat_id, '--launch-id', launch,
                   '--runtime-location-json', json.dumps(runtime_location),
                   '--max-seconds', str(args.max_seconds), '--max-matches', str(args.max_matches)]
        if continuation:
            command.extend(['--business-resume-json', json.dumps(continuation, ensure_ascii=False)])
        if args.continue_matches:
            command.append('--continue-matches')
        if getattr(args, 'profile', False):
            command.append('--profile')
        if getattr(args, 'profile_comparison_key', None):
            command.extend(['--profile-comparison-key', args.profile_comparison_key])
        lease.children([], complete=False)
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                                 env=input_bridge.runtime_child_environment(entry.PINNED, runtime_location))
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
    if request.get('kind') == 'business_resume' and (len(actions) != 1
            or actions[0].get('type') != 'finish_preparation_review'
            or set(reply.get('context_update', {})) != {'business_resume'}):
        raise ValueError('跨租期承接只允许独立当前业务复核，禁止附带旧阶段或游戏输入')
    if 'reward_capacity' in reply.get('context_update', {}) and (
            len(actions) != 1 or actions[0].get('type') != 'finish_preparation_review'):
        raise ValueError('腾位后回读必须独立无输入复核，之后重新规划领奖')
    allowed = {'click_text', 'click_point', 'buy_shop', 'buy_xp', 'key', 'drag', 'scroll', 'finish_preparation_review',
               'finish_inspection', 'confirm_match_result'}
    for action in actions:
        if not isinstance(action, dict) or action.get('type') not in allowed:
            raise ValueError('未知语义动作')
        if not isinstance(action.get('reason'), str) or not 1 <= len(action['reason']) <= 1000:
            raise ValueError('每个动作须有具体中文理由')
        if action['type'] not in ('finish_inspection', 'confirm_match_result', 'finish_preparation_review') and not action.get('expected_page'):
            raise ValueError('游戏输入须指定实际页面前置条件')
        if action['type'] == 'click_text' and (not isinstance(action.get('text'), str)
                or not clean(action['text']) or type(action.get('exact', True)) is not bool):
            raise ValueError('文字点击须有非空目标及布尔exact，不能用空子串或隐式类型匹配')
        if action['type'] in ('click_point', 'drag', 'scroll', 'key'):
            if not isinstance(action.get('guard_texts'), list) or not action['guard_texts']:
                raise ValueError('坐标/按键动作须提供新画面文字守卫')
        if action['type'] in ('click_point', 'drag', 'scroll'):
            proof = action.get('target_evidence')
            if not isinstance(proof, dict) or proof.get('snapshot_id') != request['snapshot_id']:
                raise ValueError('坐标动作须绑定本次原始PNG的目标ROI证据')
            if (proof.get('source') == 'supervising_agent'
                    and proof.get('control_id') != PREPARATION_GUIDE_CONTROL):
                raise ValueError('主管导航ROI声明仅限第二创业指南，不能转入普通坐标路径')
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
        page = request.get('observation', {}).get('page')
        if (page == 'environment' and action['type'] in ('click_text', 'click_point')
                and not (action['type'] == 'click_text' and clean(action.get('text', '')) == '确认')
                and len(actions) != 1):
            raise ValueError('环境选卡须独立单动作；确认必须使用选择后的新请求和当前帧')
        if page in economy.PREPARATION_PAGES and action.get('type') == 'key' and action.get('args') == [69]:
            raise ValueError('备战/商店经验键已实机核为F70，E69不再作为经验或导航输入')
        if economy.economic_action(action, page) and len(actions) != 1:
            raise ValueError('经济动作须一笔一回验；有限连续执行由同一预算逐新帧规划')
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
    """One native start button; its stable content excludes the search box's bottom edge."""
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
    # ROOT's retained pair differs only on y=1019, the search box's last row.
    # Keep every other pixel exact; do not crop each OCR box independently.
    content_roi = (1360, 930, 1810, 1019)
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
        if label == '开始「货币战争」' and any(
                not (content_roi[0] <= box[0] < box[2] <= content_roi[2]
                     and content_roi[1] <= box[1] < box[3] <= content_roi[3]) for box in boxes):
            return False  # The whole current label, hence its click center, must be inside.
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
            return old.crop(content_roi).convert('RGB').tobytes() == fresh.crop(content_roi).convert('RGB').tobytes()
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
        # Use the same full-card/section/confirmation contract as the real
        # consumer; the old RGB-percentage fallback must not bypass it.
        return stable_semantic_target(action, request, actual, current_png)
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
                      'runtime_location': getattr(args, 'runtime_location',
                          {'schema': 1, 'source': 'standalone', 'runtime_root': str(run.parent),
                           'installation_id': None}),
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
                        'reward_capacity': None,
                        'economy_plan': None,
                        'business_resume': None,
                        'unknown_fields': ['guide', 'guide_tracking', 'investments', 'environment',
                                           'team', 'bonds', 'gear', 'tasks', 'hp', 'coins', 'xp']}
        self.last_observation = None
        self.frame_path = None
        self.frame_result = None
        self.last_epoch = None
        self.wait_started = None
        self.wait_page = None
        self.broker_launcher = None
        self.bridge_launch = None
        self.children = []
        self.records = PROJECT / 'debug' / ('runner-' + args.chat_id[:8] + '-' + marker['run_id'][:12])
        self.records.mkdir(exist_ok=False)
        self.profile = ProfileRecorder(self.records, run_id=self.owner['run_id'],
            enabled=getattr(args, 'profile', False) or os.environ.get('CW_PROFILE') == '1',
            source_sha=hashlib.sha256(SELF.read_bytes()).hexdigest(),
            comparison_key=getattr(args, 'profile_comparison_key', None))
        self.profile_wait_id = None
        self.profile_wait_kind = None
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
        self.initialize_business(getattr(args, 'business_resume_json', None))
        self.publish()

    def initialize_business(self, continuation=None):
        self.business_needs_review = bool(continuation)
        self.business_unknown = []
        if continuation:
            contract = json.loads(continuation) if isinstance(continuation, str) else continuation
            if contract.get('target_chat_id') != self.owner['chat_id']:
                raise ValueError('业务启动合同不属于当前授权会话')
            self.business_path, self.business = load_business(contract['checkpoint'], contract.get('previous_chat_id'))
            previous = self.business['leases'][-1]
            if (contract.get('revision') != self.business['revision']
                    or contract.get('previous_run_id') != previous['run_id'] or len(self.business['leases']) >= 128):
                raise ValueError('跨租期业务CAS/有界历史已改变，不启动迟到worker')
            old_lease_exit(previous, self.c)
            self.business_unknown = pending_business_requests(self.business)
            self.active_match_id = self.business['match_id']
            self.business_previous_run = previous['run_id']
            self.business_previous_chat = previous['chat_id']
            self.business_previous_observation = copy.deepcopy(self.business.get('last_observed'))
            self.match_result_confirmed = self.business['status'] == 'completed'
            if self.match_result_confirmed:
                self.consumed_match_results.add(self.active_match_id)
        else:
            self.business_path = self.records / ('business-' + self.active_match_id + '.json')
            self.business = {'schema': BUSINESS_SCHEMA, 'chat_id': self.owner['chat_id'],
                'match_id': self.active_match_id, 'revision': 0, 'status': 'active', 'leases': [], 'economy': {}}
            self.business_previous_run = None
            self.business_previous_chat = None
            self.business_previous_observation = None
        self.business_revision = self.business['revision']
        self.business_lease_watermark = self.economy_receipt_watermark()
        # Claim is a bounded CAS, not permission to act in the old game state.
        with file_lock(self.business_path.parent, 'business.lock'):
            current = optional(self.business_path)
            if current is not None and (current['revision'] != self.business_revision
                    or current['leases'][-1]['run_id'] != self.business_previous_run):
                raise ValueError('业务归属CAS不匹配')
            self.business['leases'].append(business_lease({**self.state,
                'journal_file': str(self.records / 'journal.jsonl'),
                'entry_receipt_watermark': self.business_lease_watermark}))
            self.business['revision'] += 1
            self.c.write_json(self.business_path, self.business)
            self.business_revision = self.business['revision']

    def save_business(self):
        if not hasattr(self, 'business'):
            return  # Existing focused fixtures are deliberately input-only.
        state = {**self.state, 'journal_file': str(self.records / 'journal.jsonl'),
                 'entry_receipt_watermark': self.business_lease_watermark}
        identity = optional(self.run / 'broker-process.json')
        if identity:
            state['broker_identity'] = {key: identity[key] for key in ('pid', 'creation_id')}
        proposed = copy.deepcopy(self.business)
        proposed['leases'][-1] = business_lease(state)
        observed = self.last_observation or {}
        if observed:
            proposed['last_observed'] = {'origin_run_id': self.owner['run_id'],
                **{key: observed.get(key) for key in ('snapshot_id', 'capture_request_id', 'captured_at', 'page', 'fields')}}
        with file_lock(self.business_path.parent, 'business.lock'):
            current = entry.read_json(self.business_path)
            latest = current['leases'][-1]
            if (current['revision'] != self.business_revision
                    or any(latest.get(k) != self.owner.get(k) for k in ('chat_id', 'run_id', 'runner_pid', 'runner_creation_id'))):
                raise RuntimeError('旧租期业务CAS已失效，禁止覆盖新owner')
            if proposed != current:
                proposed['revision'] += 1
                self.c.write_json(self.business_path, proposed)
            self.business, self.business_revision = proposed, proposed['revision']
        self.state['business'] = {'checkpoint': str(self.business_path), 'match_id': self.active_match_id,
            'status': self.business['status'], 'resume_review_required': self.business_needs_review,
            'previous_run_id': self.business_previous_run, 'previous_chat_id': self.business_previous_chat,
            'unresolved_requests': self.business_unknown,
            'continuation_required': self.business['status'] != 'completed',
            'awaiting_next_lease': self.business['status'] != 'completed' and self.state.get('control_mode') in TERMINAL,
            'all_rewards_completed': False}

    def review_business_resume(self, record):
        request, actual = self.state.get('decision_request') or {}, self.last_observation or {}
        if not self.business_needs_review or request.get('kind') != 'business_resume':
            raise ValueError('没有当前跨租期业务承接请求')
        source = self.verified_source(record.get('proof', {}), 180)
        capture = await_existing_receipt(self.run, self.c, actual.get('capture_request_id'), 0)
        if (capture['request'].get('kind') != 'actions' or capture['request'].get('handoff') is not False
                or capture['request'].get('actions') != [{'type': 'observe', 'args': []}]
                or capture.get('result', {}).get('observation', {}).get('snapshot_sha256') != actual.get('snapshot_id')):
            raise ValueError('承接当前帧须来自本run实际只读观察收据')
        since_request = set(self.economy_receipt_watermark()) - set(request.get('business_receipt_watermark', []))
        if 'business_receipt_watermark' not in request or actual['capture_request_id'] not in since_request:
            raise ValueError('业务承接缺少本请求后新增观察水位')
        for rid in since_request:
            item = await_existing_receipt(self.run, self.c, rid, 0)
            delivery = manual_receipt_state(item)
            if (item['request'].get('kind') != 'actions' or item['request'].get('handoff') is not False
                    or any(a.get('type') not in ('observe', 'wait') for a in item['request'].get('actions', []))
                    or delivery['unknown_input'] or delivery['state'] not in ('zero_input', 'completed')
                    or item['result'].get('input_attempted') not in (None, False)
                    or item['result'].get('attempted_actions') not in (None, [])):
                raise ValueError('业务承接原请求后存在输入/未知结果；需新的当前复核，不重发')
        value = record.get('value', {})
        expected_unknown = sorted(item['origin_run_id'] + ':' + item['request_id'] for item in self.business_unknown)
        if (record['proof'].get('snapshot_id') != request.get('snapshot_id')
                or actual.get('page') != source.get('page')
                or hashlib.sha256(Path(request['original_png']).read_bytes()).hexdigest() != request.get('snapshot_id')
                or not source.get('capture_request_id')
                or record['proof'].get('capture_request_id') != source['capture_request_id']
                or source['capture_request_id'] != request['observation'].get('capture_request_id')
                or value.get('reviewer') != 'supervising_agent'
                or value.get('previous_run_id') != self.business_previous_run
                or value.get('previous_chat_id') != self.business_previous_chat
                or value.get('current_chat_id') != self.owner['chat_id']
                or value.get('match_id') != self.active_match_id
                or value.get('unresolved_requests') != expected_unknown
                or value.get('prior_outcomes_remain_unknown') is not True
                or value.get('remaining_policy') != 'fresh_reviews_and_current_balance'
                or not isinstance(value.get('continuity_basis'), str) or len(value['continuity_basis'].strip()) < 12):
            raise ValueError('承接须为当前proof、原业务身份、完整未定请求及明确同局依据')
        reading = value.get('current_state', {})
        if reading.get('page') != actual.get('page'):
            raise ValueError('业务承接页面已变化，需重读')
        if actual.get('page') in economy.PREPARATION_PAGES:
            if (not isinstance(reading.get('roster'), list) or not reading['roster']
                    or not all(isinstance(name, str) and name.strip() for name in reading['roster'])
                    or not isinstance(reading.get('selected_strategy'), str) or not reading['selected_strategy'].strip()):
                raise ValueError('备战承接须独立实读当前阵容与所选策略，PID/节点相同不能证明同局')
            deployed = re.fullmatch(r'([0-9]+)/([0-9]+)', str(reading.get('deployed', '')))
            native_deployed = re.fullmatch(r'([0-9]+)/([0-9]+)', str(actual.get('fields', {}).get('deployed') or ''))
            native_level = actual.get('fields', {}).get('level')
            native_coins = actual.get('semantic', {}).get('coins', {})
            if (not canonical_stage(reading.get('stage'))
                    or any(canonical_stage(observation.get('fields', {}).get('stage')) not in (None, reading['stage'])
                           for observation in (source, actual))
                    or type(reading.get('coins')) is not int or reading['coins'] < 0
                    or type(reading.get('level')) is not int or not 1 <= reading['level'] <= 10
                    or not isinstance(reading.get('xp'), list) or len(reading['xp']) != 2
                    or any(type(n) is not int or n < 0 for n in reading['xp'])
                    or not 0 <= reading['xp'][0] < reading['xp'][1]
                    or not deployed or not 0 <= int(deployed[1]) <= int(deployed[2]) or int(deployed[2]) <= 0
                    or native_deployed and 0 <= int(native_deployed[1]) <= int(native_deployed[2]) and int(native_deployed[2]) > 0
                        and deployed.groups() != native_deployed.groups()
                    or str(native_level).isdigit() and 1 <= int(native_level) <= 10 and int(native_level) != reading['level']
                    or native_coins.get('bounds') == GOLD_HUD and type(native_coins.get('value')) is int
                        and .15 <= native_coins.get('currency_icon_gold_fraction', 0) <= 1.
                        and .90 <= native_coins.get('confidence', 1.) <= 1.
                        and native_coins['value'] != reading['coins']):
                raise ValueError('承接缺少当前节点/金币/等级/经验/人口独立读数')
        elif actual.get('page') in BUSINESS_REVIEW_PAGES:
            labels = reading.get('visible_labels')
            if (not isinstance(labels, list) or not labels or len(labels) > 32
                    or any(not isinstance(label, str) or not find_text(source.get('rows', []), label, exact=True)
                           for label in labels)):
                raise ValueError('非备战页须列出本请求当前画面实际可见文字，不要求看不到的旧阵容')
        else:
            raise ValueError('当前页不足以核实整局归属；只补观察，不能输入或猜同局')
        if value.get('disposition') not in ('same_match', 'new_match'):
            raise ValueError('须明确同一局或已另开新局')
        if value['disposition'] == 'new_match' and actual['page'] not in (
                'lobby', 'preparation', 'shop', 'opponents', 'environment', 'investment'):
            raise ValueError('另开新局须在实际setup/备战确认，或大厅登记意图；不能将新局结果归到旧ID')
        if (value['disposition'] == 'same_match' and actual['page'] == 'lobby'
                and self.business['status'] != 'completed'):
            raise ValueError('大厅不能证明原局已结算；可明确另开业务并保留旧局未决')
        status = self.c.status()
        if (manual_state(self.run) or self.epoch() != request['resume_epoch']
                or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()
                or status['paused'] or status['input_halted'] or not status['ready']):
            raise ValueError('新的暂停/停止/epoch优先，业务承接未提交')
        review_path = self.records / (request['request_id'] + '-business-resume.json')
        self.c.write_json(review_path, {'origin_run_id': self.owner['run_id'], 'record': record,
            'previous_business': str(self.business_path), 'unknown_requests': self.business_unknown,
            'current_capture_request_id': actual['capture_request_id'], 'current_snapshot_id': actual['snapshot_id'],
            'observation_only_receipt_ids': sorted(since_request),
            'input_resent': False, 'recorded_at': now()})
        self.business['last_resume_review'] = str(review_path)
        self.business_needs_review = False
        self.preparation_reviews, self.preparation_scope, self.economy_binding = {}, None, None
        self.economy_read_cache = None
        self.strategy_reads, self.live_mode, self.cached_guide_binding = {}, None, None
        if value['disposition'] == 'new_match':
            if self.business['status'] != 'completed':
                self.business['status'] = 'unresolved'
            self.save_business()
            if actual['page'] in ('preparation', 'shop', 'opponents', 'environment', 'investment'):
                self.active_match_id = uuid.uuid4().hex
                self.match_result_confirmed = False
                self.initialize_business()
            # At the lobby this is only an explicit intent. The existing
            # new_match -> actual setup transition will allocate its new ID.
        self.context = {key: None for key in self.context}
        self.history = {}
        self.save_business()

    def publish(self, **updates):
        self.state.update(updates)
        self.save_business()
        self.profile_context()
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

    def profile_context(self):
        profile = getattr(self, 'profile', None)
        if profile is None or not profile.enabled:
            return
        observed = self.last_observation or {}
        stage = canonical_stage(observed.get('fields', {}).get('stage')) or self.last_preparation_stage
        mode, page = self.state.get('control_mode'), observed.get('page')
        if mode in ('manual', 'halted', 'stopping'):
            phase = 'recovery'
        elif page == 'battle':
            phase = 'battle'
        elif page in ('node_result', 'boss_result', 'settlement', 'settlement_grade', 'plane_intro'):
            phase = 'settlement'
        elif stage and page != 'unknown':
            reviews = getattr(self, 'preparation_reviews', {})
            if self.preparation_scope != (self.active_match_id, stage, self.epoch()):
                reviews = {}
            phase = coaching.preparation_status(reviews)['phase']
            if phase == 'ready_for_battle':
                phase = 'battle_acceptance'
        else:
            phase = 'unknown'
        profile.set_context(match_id=self.active_match_id, stage=stage,
                            resume_epoch=self.epoch(), phase=phase)
        # Polling/approval wait has an actual open interval. A child capture or
        # OCR span is subtracted rather than added to the same wall time.
        waiting = 'manual_wait' if mode in ('manual', 'halted') else (
            'supervisor_wait' if mode == 'waiting_decision' else None)
        identity = (waiting, self.active_match_id, stage, self.epoch(),
                    (self.state.get('decision_request') or {}).get('request_id')) if waiting else None
        if identity != self.profile_wait_kind:
            profile.end_span(self.profile_wait_id)
            self.profile_wait_kind = identity
            self.profile_wait_id = profile.start_span('controller_wait', operation=waiting,
                request_id=identity[-1]) if waiting else None

    def profile_span(self, name, *, operation, request_id=None, snapshot_id=None, business_step=None):
        profile = getattr(self, 'profile', None)
        if profile is None or not profile.enabled:
            return contextlib.nullcontext()
        return profile.span(name, operation=operation,
            parent_id=profile.current_span_id or getattr(self, 'profile_wait_id', None),
            request_id=request_id, snapshot_id=snapshot_id, business_step=business_step)

    def profile_broker_result(self, result, parent_id):
        profile = getattr(self, 'profile', None)
        if profile is None or not profile.enabled:
            return
        intervals = result.get('profile_intervals')
        if not isinstance(intervals, list) or len(intervals) > 32:
            return  # Old/missing timing evidence remains unknown round-trip time.
        for interval in intervals:
            if not isinstance(interval, dict):
                continue
            profile.record_interval(interval.get('name', 'broker_step'),
                start_ns=interval.get('start_ns'), end_ns=interval.get('end_ns'),
                operation=interval.get('operation'), parent_id=parent_id,
                request_id=result.get('id'), receipt_id=result.get('id'))

    def profile_queue_wait(self, value):
        profile = getattr(self, 'profile', None)
        if profile is not None and profile.enabled and not value.get('lease_reused'):
            profile.record_interval('entry_queue_wait', start_ns=value.get('start_ns'),
                                    end_ns=value.get('end_ns'), operation='queue_wait',
                                    parent_id=profile.current_span_id, timing_source='entry_submission_lease')

    def finish_profile(self):
        profile = getattr(self, 'profile', None)
        if profile is None or profile.path is None:
            return
        try:
            profile.end_span(self.profile_wait_id)
            profile.close(complete=False)
            events, issues = read_events([profile.path])
            report = summarize_events(events, issues)
            paths = write_report(report, self.records / 'profile-summary')
            self.state['profile'] = {'events': str(profile.path),
                'reports': {kind: str(path) for kind, path in paths.items()},
                'issues': len(report['issues']), 'error': profile.error,
                'live_automation_verified': False}
        except (OSError, ValueError, TypeError) as exc:
            self.state['profile'] = {'events': str(profile.path), 'error': str(exc)}

    def log(self, value):
        item = {'time': now(), 'run_id': self.owner['run_id'], **redact(value)}
        with (self.records / 'journal.jsonl').open('a', encoding='utf8') as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + '\n')
            stream.flush()

    def save_frame(self, rid, label, source=None):
        source = source or self.frame_path
        if source is None or not source.exists() or self.evidence_count >= 1000:
            return None
        from PIL import Image
        target = self.records / (rid + '-' + label + '.jpg')
        with self.profile_span('evidence_image', operation='capture', request_id=rid):
            with Image.open(source) as image:
                image.convert('RGB').save(target, quality=72)
        self.evidence_count += 1
        return str(target)

    def register(self, pid, creation):
        item = {'pid': int(pid), 'process_identity': 'windows:' + str(creation)}
        self.children.append(item)
        artifacts.protect_children(self.run, self.children, root=self.run.parent, complete=False)

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
            artifacts.protect_children(self.run, self.children, root=self.run.parent, complete=False)
            if own['integrity_rid'] < game['integrity_rid']:
                self.bridge_launch = input_bridge.prepare_launch(self.run, self.owner, entry.PINNED)
                input_bridge.dispatch(self.bridge_launch)
            else:
                self.broker_launcher = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                            env=input_bridge.runtime_child_environment(entry.PINNED, self.owner['runtime_location']))
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

    def manual_input_blocked(self):
        return bool(manual_state(self.run)) and not manual_steps.active(self)

    def guard_command_intent(self, tokens, action, observed, *, request_id=None):
        """Classify the actual target, then verify the already authorized intent."""
        physical = [token for token in tokens if token.split(':', 1)[0] in ('click', 'key', 'drag', 'scroll')]
        if not physical:
            return
        if getattr(self, 'manual_stage_blocked', None):
            remaining = set(manual_steps.unknown_receipts(self.run, self.c)) & set(self.manual_stage_blocked)
            if remaining:
                raise ValueError('新节点仍有原输入pending/unknown；只核原ID，不发送后续输入')
            self.manual_stage_blocked = []
        if len(physical) != 1:
            raise ValueError('每次只发布一个有当前意图的物理目标；批次须逐步回读')
        page = observed.get('page')
        retained_effects = getattr(self, 'manual_stage_business_pending', [])
        if retained_effects:
            retained_effects = manual_stage.pending_remaining(retained_effects, run=self.run,
                owner=self.owner, control=self.c, records=self.records)
            self.manual_stage_business_pending = retained_effects
        if retained_effects and not (page == 'reward_overlay' and physical == ['key:27']):
            raise ValueError('原节点业务效果仍pending；完整completed不能清费用/领奖效果，只核原来源')
        if page not in ('preparation', 'shop', 'unit_gear'):
            return
        if not isinstance(action, dict):
            raise ValueError('备战输入缺语义意图；不用原始坐标或快捷键绕过准备顺序')
        for (match, unused_stage), ledger in getattr(self, 'economy_ledgers', {}).items():
            old = ledger.get('pending')
            if (match == self.active_match_id and old
                    and not (old is getattr(self, 'economy_inflight', None)
                             and old.get('request_id') == request_id
                             and old.get('publication_attempted') is False)):
                raise ValueError('原经济业务效果仍pending；不因阶段变化开始另一笔输入')
        reward = getattr(self, 'reward_inflight', None)
        if action.get('purpose') == 'local_reward':
            if (not reward or action.get('type') != 'click_point'
                    or action.get('args') != reward.get('target', {}).get('center')
                    or physical != ['click:' + ':'.join(map(str, action['args']))]
                    or reward.get('kind') not in ('close_shop', 'blue_orb', 'gray_orb')):
                raise ValueError('本地领奖意图没有当前单控件pending；旧球坐标不发布')
            return self.guard_reward_step(reward, observed)
        if action.get('purpose') == 'reward_capacity':
            pending = self.pending_reward_capacity()
            value = self.reward_capacity_action(action, observed, pixels=True)
            if (not pending or pending.get('input_request_id') != request_id
                    or pending.get('tokens') != tokens or pending.get('status') != 'unverified'
                    or pending.get('resume_epoch') != self.epoch()
                    or pending.get('before_snapshot_id') != observed.get('snapshot_id')
                    or pending.get('before') != value or self.preparation_checklist(observed)['phase'] != 'rewards'):
                raise ValueError('领奖腾位只允许本次已核且尚未发布的单一出售意图')
            return
        kind = economy.economic_action(action, page)
        resolved = None
        point = list(map(float, physical[0].split(':')[1:])) if physical[0].startswith('click:') else None
        if action.get('type') == 'click_text':
            if action.get('exact', True) is not True:
                raise ValueError('备战文字目标须完整精确标签；不以购买/D/数字子串绕经济控件')
            resolved = find_text(observed.get('rows', []), action.get('text'), action.get('bounds'), exact=True)
            if resolved is None:
                raise ValueError('当前完整文字目标缺失或不唯一')
            box = resolved['box']
            if point is not None and point != [(box[0]+box[2])/2, (box[1]+box[3])/2]:
                raise ValueError('实际点击与当前文字目标中心不一致')
            text = clean(resolved['text'])
            if '刷新' in text:
                kind = 'refresh'
            elif '购买经验' in text:
                kind = 'experience'
            if point is None and physical != [{'refresh': 'key:68', 'experience': 'key:70'}.get(kind)]:
                raise ValueError('文字意图只有已核经济快捷键可转换，其他非点击目标拒绝')
        if point is not None:
            from currency_wars_refresh_offer import WIDGET_BOUNDS
            x, y = point
            def inside(bounds):
                return bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]
            # Full native widget surfaces, including shortcut/number/background;
            # these are veto regions, never positive click authorization.
            if page == 'shop' and inside(WIDGET_BOUNDS):
                kind = 'refresh'
            elif inside([210, 800, 390, 1020]):
                kind = 'experience'
            # ShopReader._rectangles supports the upper native five-card row
            # (y 40..105, height 235..300), also visible in retained q00/f00.
            elif page == 'shop' and (inside([340, 35, 1715, 410]) or any(
                    slot.get('bounds') and inside([slot['bounds'][0], slot['bounds'][1],
                        slot['bounds'][0]+slot['bounds'][2], slot['bounds'][1]+slot['bounds'][3]])
                    for slot in (observed.get('shop') or {}).get('slots', []))):
                kind = 'purchase'
        status = self.preparation_checklist(observed)
        if kind:
            if not status['economy_allowed']:
                raise ValueError('奖励/创业指南/清库存未核，经济控件及其背景均不发布')
            scan = observed.get('semantic', {}).get('rewards', {})
            if scan.get('snapshot_id') == observed.get('snapshot_id') and (
                    scan.get('targets') or scan.get('uncertain') or scan.get('interaction_required')):
                raise ValueError('当前奖励可见、待选或未知；旧完成项不能授权经济')
            pending = getattr(self, 'economy_inflight', None)
            stage = canonical_stage(observed.get('fields', {}).get('stage'))
            ledger = self.economy_ledger(stage)
            permit = (pending or {}).get('authorization', {})
            normalized = self.c.validate_actions([{'type': p[0], 'args': p[1:]}
                                                  for p in (token.split(':') for token in tokens)])
            if (not pending or pending.get('request_id') != request_id or not request_id
                    or pending.get('publication_attempted') is not False
                    or pending.get('kind') != kind or ledger.get('pending') != pending
                    or pending.get('resume_epoch') != self.epoch()
                    or pending.get('before', {}).get('stage') != stage
                    or pending.get('broker_actions') != normalized
                    or permit.get('action') != action or permit.get('page') != page
                    or any(permit.get(key) != observed.get(key) for key in
                           ('snapshot_id', 'capture_request_id', 'frame_id'))
                    or permit.get('budget_revision') != ledger.get('revision')
                    or permit.get('policy') != ledger.get('policy') or permit.get('spent') != ledger.get('spent')
                    or (getattr(self, 'economy_binding', None) or {}).get('scope') != (self.active_match_id, stage, self.epoch())):
                raise ValueError('经济控件仅接受本次已核且尚未发布的意图/预算/原价/pending；不能用普通点击旁路')
            # Do not ask economic_policy to select again: this exact first
            # publication owns its pending, which correctly excludes new work.
            return
        if status['phase'] == 'rewards' and action.get('type') == 'click_text' and (
                page != 'shop' or not resolved or clean(resolved['text']) != '收起'):
            raise ValueError('奖励未空；只允许当前收店/原生领奖或必要恢复，不点未知备战文字')
        guide_navigation = action.get('target_evidence', {}).get('control_id') == PREPARATION_GUIDE_CONTROL
        loot_pickup = action.get('target_evidence', {}).get('control_id') in _NATIVE_LOOT_PICKUP_PROFILES
        self.guard_preparation_action(action, observed, loot_pickup=loot_pickup,
                                      verified_navigation=guide_navigation)
        if guide_navigation:
            # This runs under Entry's publication lock, after the fresh frame
            # was read. The fixed-control exception still checks its pixels.
            self.guard_startup_navigation(action, self.state.get('decision_request') or {}, observed)
        if action.get('type') == 'click_point' and not (guide_navigation or loot_pickup
                or action.get('purpose') == 'reward_capacity'):
            self.check_target_roi(action, self.state.get('decision_request') or {}, observed)

    def command(self, tokens, reason, expected_page=None, postcondition=None, action=None, *, read_scope='full'):
        if getattr(self, 'business_needs_review', False):
            raise RuntimeError('新租期尚未用当前帧确认业务归属，不发布游戏输入')
        page = (self.last_observation or {}).get('page')
        if (expected_page is not None and page != expected_page
                or action and action.get('expected_page') not in (None, page)):
            raise ValueError('输入前置页面与当前帧不符；只回读重定位，未提交')
        if page in economy.PREPARATION_PAGES:
            keys = {float(token.split(':')[1]) for token in tokens if token.startswith('key:')}
            if 69 in keys:
                raise ValueError('备战/商店不发布旧经验E69')
            if keys & {68, 70} and not getattr(self, 'economy_inflight', None):
                raise ValueError('D刷新/F经验须由已核统一预算逐笔发布')
        if time.monotonic() >= self.deadline:
            raise RuntimeError('本次worker总期限已到，未发布动作')
        if self.manual_input_blocked() or (self.run / 'runner-stop').exists():
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
                        or current.get('page') != observed.get('page')
                        or expected_page is not None and current.get('page') != expected_page
                        or canonical_stage(current.get('fields', {}).get('stage')) != canonical_stage(observed.get('fields', {}).get('stage'))):
                    raise BattleConfirmationRequired('needs_user_confirmation：发布前请求/画面/节点/交接身份已改变，未提交')
                if request and datetime.now(timezone.utc) >= datetime.fromisoformat(request['deadline_at']):
                    raise BattleConfirmationRequired('needs_user_confirmation：发布前战略请求期限已到，未提交')
                status = self.c.status()
                if (not status['ready'] or status['paused'] or status['input_halted'] or not status['game_foreground']
                        or self.manual_input_blocked() or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()):
                    raise RuntimeError('发布前健康/前台/接管/停止检查拒绝，未提交')
                self.guard_command_intent(tokens, action, current, request_id=value['id'])
                if planned_inputs:
                    pending_reward = self.pending_reward_step(current)
                    first_reward = (reward is not None and pending_reward is not None
                        and pending_reward.get('step_id') == reward['step_id']
                        and pending_reward.get('request_id') == rid
                        and pending_reward.get('publication_attempted') is False)
                    if pending_reward and not first_reward:
                        original = await_existing_receipt(self.run, self.c, pending_reward['request_id'], 0)
                        delivery = manual_receipt_state(original)
                        if (delivery['unknown_input'] or delivery['state'] != 'completed'
                                or pending_reward.get('before', {}).get('stage') != canonical_stage(current.get('fields', {}).get('stage'))):
                            raise ValueError('原领奖输入交付仍未确认；主管回复也不能发布后续物理输入')
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
                if transaction is not None:
                    transaction['publication_attempted'] = True
                    transaction['broker_actions'] = value['actions']
                    self.save_economy_ledger(transaction['before']['stage'])
                if reward is not None:
                    self.guard_reward_step(reward, current)
                    reward.update(publication_attempted=True, broker_actions=value['actions'])
                    self.save_reward_step(reward)
                if navigation is not None:
                    navigation.update(publication_attempted=True, broker_actions=value['actions'])
                    self.save_startup_navigation(navigation)
                if hasattr(self, 'business'):
                    self.save_business()  # Original lease CAS still owns this publication.
                    # This is an intent before the actual publisher. A crash
                    # here is unknown, never proof of input or zero effect.
                    archive_business_receipt(self.records, self.owner,
                        {'id': value['id'], 'request': value, 'result': None}, self.c)

        rid = uuid.uuid4().hex
        if action and action.get('purpose') == 'reward_capacity':
            pending = self.pending_reward_capacity()
            if not pending or pending.get('input_request_id'):
                raise ValueError('领奖腾位没有独立未发布记录；不重发出售')
            pending.update(input_request_id=rid, tokens=tokens)
            self.c.write_json(self.run / 'reward-capacity.json', pending)
            self.c.write_json(self.records / (rid + '-reward-capacity.json'), pending)
        transaction = getattr(self, 'economy_inflight', None)
        if transaction is not None:
            transaction['request_id'] = rid
            self.save_economy_ledger(transaction['before']['stage'])
        reward = getattr(self, 'reward_inflight', None)
        if reward is not None:
            reward['request_id'] = rid
            self.save_reward_step(reward)
        navigation = getattr(self, 'startup_navigation_inflight', None)
        if navigation is not None:
            navigation['input_request_id'] = rid
            self.save_startup_navigation(navigation)
        self.worker_request_ids = getattr(self, 'worker_request_ids', set())
        self.worker_request_ids.add(rid)
        before = self.save_frame(rid, 'before')
        self.log({'event': 'decision', 'decision_id': rid, 'tokens': tokens, 'reason': reason,
                  'expected_page': expected_page, 'expected_change': postcondition,
                  'observation': self.last_observation, 'before_evidence': before})
        started = time.perf_counter()
        guarded = GuardedSubmission(self.c, publication_guard,
            lambda: self.profile_span('publish_request', operation='publication', request_id=rid))
        try:
            with self.profile_span('broker_roundtrip', operation='tool_roundtrip', request_id=rid) as timing:
                result = entry.request(guarded, 'actions', tokens, rid, False, submit_deadline=submit_deadline,
                                       queue_wait=self.profile_queue_wait)
                self.profile_broker_result(result, timing)
        except Exception as exc:
            if action and action.get('purpose') == 'reward_capacity' and not guarded.publication_attempted:
                pending = self.pending_reward_capacity()
                if pending and pending.get('input_request_id') == rid:
                    pending.update(status='refused', request_published=False, error=str(exc))
                    self.c.write_json(self.run / 'reward-capacity.json', pending)
                    self.c.write_json(self.records / (rid + '-reward-capacity.json'), pending)
            if transaction is not None:
                transaction['publication_attempted'] = guarded.publication_attempted
                transaction['error'] = str(exc)
                self.save_economy_ledger(transaction['before']['stage'])
            if reward is not None:
                reward.update(publication_attempted=guarded.publication_attempted, error=str(exc))
                self.save_reward_step(reward)
            if navigation is not None:
                navigation.update(publication_attempted=guarded.publication_attempted, error=str(exc),
                    status='pending' if guarded.publication_attempted else 'refused',
                    outcome='unknown' if guarded.publication_attempted else 'zero_input')
                self.save_startup_navigation(navigation)
            self.log({'event': 'actual_result', 'decision_id': rid, 'request_id': rid,
                      'classification': 'control_outcome_unverified' if guarded.publication_attempted else 'control_submission_refused',
                      'request_published': None if guarded.publication_attempted else False, 'error': str(exc),
                      'input_resent': False, 'after_evidence': None})
            if isinstance(exc, entry.SubmissionDeadlineExpired) and approval is not None and not guarded.publication_attempted:
                raise BattleConfirmationRequired('needs_user_confirmation：战斗批准在发布前过期，已消费且不恢复') from exc
            raise
        finally:
            if hasattr(self, 'business'):
                path = self.run / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
                original = optional(path)
                if original:
                    archive_business_receipt(self.records, self.owner, original, self.c)
        # Persist the input receipt even if its subsequent observation failed.
        # A new read-only observation must never resubmit these input tokens.
        if transaction is not None:
            transaction['publication_attempted'] = guarded.publication_attempted
            transaction['receipt'] = redact(result)
            self.save_economy_ledger(transaction['before']['stage'])
        if reward is not None:
            reward.update(publication_attempted=guarded.publication_attempted, receipt=redact(result))
            self.save_reward_step(reward)
        if navigation is not None:
            navigation.update(publication_attempted=guarded.publication_attempted)
            self.save_startup_navigation(navigation)
        self.c.write_json(self.records / (rid + '-result.json'), redact(result))
        after = None
        try:
            with self.profile_span('result_frame_validation', operation='capture', request_id=rid):
                frame = entry.observation_frame(self.run, result)
            after = self.save_frame(rid, 'after-original', frame)
        except entry.ObservationUnavailable as exc:
            self.log({'event': 'observation_unavailable', 'request_id': rid,
                      'reason': str(exc), 'input_resent': False})
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
        try:
            observed = self.read_frame(result, scope=read_scope)
        except entry.ObservationUnavailable:
            # This fallback is part of the SAME reward's two-read budget.
            # Original input delivery must be known before a successor frame
            # replaces the broker notification; observe never repeats input.
            observed = (self.observe_reward_result(reward) if reward is not None
                        else self.observe_startup_navigation(navigation) if navigation is not None
                        else self.observe(scope=read_scope))
        self.log({'event': 'actual_result', 'decision_id': rid, 'page': observed['page'],
                  'fields': observed['fields'], 'snapshot_id': observed['snapshot_id'],
                  'after_evidence': after, 'expected_change': postcondition,
                  'classification': 'observed_after_input', 'outcome_confirmed': False})
        return observed

    def read_frame(self, result, *, scope='full', force=False, reuse_primary=False):
        with self.profile_span('frame_validation', operation='capture', request_id=result.get('id')):
            frame = entry.observation_frame(self.run, result)
        try:
            with self.profile_span('perception', operation='perception', request_id=result.get('id'),
                                   snapshot_id=result['observation']['snapshot_sha256']) as timing:
                options = {'force': force, 'scope': scope}
                if reuse_primary:
                    options['reuse_primary'] = True
                observed = (self.perception.read(frame) if scope == 'full' and not force and not reuse_primary else
                            self.perception.read(frame, **options))
                profile = getattr(self, 'profile', None)
                if profile is not None and profile.enabled:
                    profile.record_perception_timing(observed.get('read_timing'), parent_id=timing,
                        request_id=result['id'], snapshot_id=observed['snapshot_id'])
        except (OSError, SyntaxError) as exc:
            raise entry.ObservationUnavailable('本请求图像读取失败；只允许重新观察') from exc
        if observed.get('snapshot_id') != result['observation']['snapshot_sha256']:
            raise entry.ObservationUnavailable('读取结果与本请求完整帧摘要不符')
        previous = getattr(self, 'frame_result', None)
        self.frame_path, self.frame_result = frame, result
        if previous is not None and previous['observation']['frame_id'] != result['observation']['frame_id']:
            try:
                entry.release_observation(self.run, previous)
            except (OSError, ValueError) as exc:
                self.log({'event': 'frame_release_deferred', 'request_id': previous.get('id'),
                          'reason': str(exc), 'input_resent': False})
        observed['capture_request_id'] = result['id']
        observed['frame_id'] = result['observation']['frame_id']
        observed['captured_at'] = result['observation']['captured_at']
        self.last_observation = observed
        self.state['statistics']['ocr_ms'] += observed['elapsed_ms']
        self.log({'event': 'perception_read', 'request_id': result['id'], 'snapshot_id': observed['snapshot_id'],
                  'read_contract': observed.get('read_contract'), 'read_timing': observed.get('read_timing'),
                  'elapsed_ms': observed['elapsed_ms']})
        self.state['observation'] = {k: v for k, v in observed.items() if k != 'rows'}
        if observed['page'] in ('preparation', 'shop') and canonical_stage(observed.get('fields', {}).get('stage')):
            self.last_preparation_stage = observed['fields']['stage']
        for key in ('guide', 'guide_tracking', 'team', 'gear', 'bonds', 'xp'):
            fact = observed.get('semantic', {}).get(key)
            if isinstance(fact, dict) and fact.get('status') != 'not_read':
                self.strategy_reads[key] = {'value': copy.deepcopy(fact), 'snapshot_id': observed['snapshot_id'],
                    'match_id': self.active_match_id, 'resume_epoch': self.epoch(), 'observed_at': now(),
                    'read_contract': copy.deepcopy(observed.get('read_contract'))}
        self.profile_context()
        return observed

    def ensure_full_observation(self, observed):
        if full_observation(observed):
            return observed
        current, result = self.last_observation or {}, getattr(self, 'frame_result', None)
        if (not isinstance(result, dict)
                or any(observed.get(key) != current.get(key) for key in
                       ('snapshot_id', 'capture_request_id', 'frame_id', 'captured_at'))
                or observed.get('capture_request_id') != result.get('id')
                or observed.get('frame_id') != result.get('observation', {}).get('frame_id')
                or observed.get('snapshot_id') != result.get('observation', {}).get('snapshot_sha256')):
            raise entry.ObservationUnavailable('完整读取须当前同请求不可变帧，不能升级旧scope或补旧阵容')
        # Revalidate the same current request, then rebuild full semantics.
        # Only its raw primary OCR may be reused; force=True remains a real
        # reread. No new capture or old semantic/request identity is supplied.
        full = self.read_frame(result, scope='full', reuse_primary=True)
        if not full_observation(full):
            raise entry.ObservationUnavailable('当前请求完整读取契约未满足')
        return full

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
            self.economy_read_cache = None
            if effect == 'inventory':
                self.economy_binding = None
                self.context['economy_plan'] = None
                for ledger in getattr(self, 'economy_ledgers', {}).values():
                    ledger['shop_complete'] = False
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
        if manual_record is not None and (phase != manual_record.get('phase') or value != manual_record.get('review')):
            raise ValueError('人工阶段与原监督复核内容不符；新复核须绑定当前请求单独提交')
        unverified = ManualReviewDeferred if manual_record is not None else ValueError
        if manual_record is not None and phase == 'rewards':
            # There is no full-field native reward-clear scanner. A historical
            # boolean cannot acquire a new proof merely by changing its epoch.
            raise ManualReviewDeferred('历史领奖标记须当前请求监督重新扫场；不复制all_claimed到新帧')
        if panel_source is not None and phase == 'startup_guide':
            if panel_source['manual_later_mutations']:
                raise ManualReviewDeferred('指南之后有已记录输入，当前任务进度须重新读取')
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
        if phase == 'rewards':
            if self.pending_reward_step(self.last_observation) or self.pending_reward_capacity():
                raise ValueError('原领奖/腾位效果仍待验；全场复核不能覆盖原pending')
            for observation in (source, self.last_observation):
                scan = observation.get('semantic', {}).get('rewards') or {}
                if (scan.get('snapshot_id') == observation.get('snapshot_id')
                        and (scan.get('targets') or scan.get('interaction_required'))):
                    raise ValueError('当前原生观察仍有奖励目标/待选奖励，不能关闭领奖阶段')
            if coaching.reward_status(source, record) != 'clear':
                raise ValueError('领奖须关闭商店后重新扫全场，奖励球及待选奖励确认领空')
        startup_title = find_text(source['rows'], '创业指南', exact=True) if phase == 'startup_guide' else None
        if phase == 'startup_guide' and (value.get('entry_index') != 2 or value.get('rewards_claimed') is not True
                or not isinstance(value.get('goals'), list)
                or not startup_title or startup_title.get('confidence', 0) < .90):
            raise unverified('须实读第二入口创业指南、当前章节目标并领空已完成奖励')
        if phase in ('inventory_cleanup', 'economy', 'lineup_equipment', 'battle_acceptance') and source['page'] not in ('preparation', 'shop'):
            raise unverified('库存/经济/阵容验收须回到当前备战或商店鲜帧')
        if manual_record is not None and phase == 'inventory_cleanup':
            def inventory_identity(observation):
                from currency_wars_state_reader import native_slots
                team = observation.get('semantic', {}).get('team') or {}
                slots = team.get('slots')
                expected = {(slot['location'], slot['row'], slot['slot']) for slot in native_slots()}
                identities = [(slot.get('location'), slot.get('row'), slot.get('slot')) for slot in slots
                              if isinstance(slot, dict)] if isinstance(slots, list) else []
                if (team.get('snapshot_id') != observation.get('snapshot_id') or not isinstance(slots, list)
                        or len(slots) != len(expected) or len(identities) != len(expected) or set(identities) != expected
                        or any(slot.get('status') not in ('empty', 'occupied')
                               or slot.get('status') == 'occupied' and (not slot.get('name') or slot.get('star') is None)
                               for slot in slots)):
                    raise ManualReviewDeferred('当前完整库存姓名/星级/空位仍未知，须当前监督复核')
                capacity = team.get('capacity') or {}
                if (capacity.get('snapshot_id') != observation.get('snapshot_id')
                        or capacity.get('overflow_checked') is not True
                        or type(capacity.get('overflow_count')) is not int or capacity['overflow_count'] != 0):
                    raise ManualReviewDeferred('临时溢出须独立确认为空；19个固定槽不代表全部库存')
                return sorted((slot.get('row'), slot.get('slot'), slot.get('status'), slot.get('name'), slot.get('star'))
                              for slot in slots)
            if inventory_identity(fresh_source) != inventory_identity(panel_source):
                raise ManualReviewDeferred('人工整理后的库存已变化；按当前库存重验，不继承旧清理标记')
        if manual_record is not None and phase == 'economy':
            current_fields = fresh_source.get('fields', {})
            if (type(current_fields.get('coins')) is not int or current_fields['coins'] < 0
                    or type(current_fields.get('level')) is not int
                    or not re.fullmatch(r'[0-9]+/[0-9]+', current_fields.get('xp') or '')
                    or any(current_fields.get(key) != panel_source.get('fields', {}).get(key)
                           for key in ('coins', 'level', 'xp'))):
                raise ManualReviewDeferred('当前金币/等级/经验缺失或已变化；须按新资源复核经济')
        if phase == 'economy':
            economic = self.economic_policy(self.last_observation)
            if not self.economy_complete(economic):
                raise unverified('经济完成须当前新epoch/新帧缺口、免费次数、付费停止与经验剩余计划均解决，原交易效果待验时不能复制完成标记')
            value = {**value, 'economic_snapshot_id': economic['observation']['snapshot_id'],
                     'economic_values': economic['observation']['values'], 'actual_spent': economic['spent'],
                     'budget_revision': self.economy_binding['plan']['revision']}
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
                board_identity = lambda value: sorted((unit.get('row'), unit.get('slot'), unit.get('name'),
                    unit.get('star', unit.get('stars')))
                    for unit in value.get('units', []) if unit.get('location') == 'board')
                fresh_requirements = coaching.lineup_requirements(investments, native_team, self.knowledge)
                if (native_team.get('checked') is not True
                        or board_identity(native_team) != board_identity(team)
                        or fresh_requirements['verified'] is not True or fresh_requirements['needs_lineup_plan']):
                    raise ManualReviewDeferred('新交接鲜帧的场上实名/星级/槽位未完整匹配；阵容仍待当前监督复核')
                gear = fresh_source.get('semantic', {}).get('gear') or {}
                if (gear.get('snapshot_id') != fresh_source.get('snapshot_id') or gear.get('checked') is not True
                        or gear.get('scope') == 'guide_recommendation_only' or not isinstance(gear.get('equipped'), list)):
                    raise ManualReviewDeferred('当前穿戴未实读；旧gear_checked不能替代新帧装备验收')
            if (not requirements['verified'] or requirements['needs_lineup_plan'] or not population
                    or board_count != int(population[1])):
                raise ValueError('前4/后6、角色位置或已选投资同时上场条件未满足')
            value = {**value, 'lineup_requirements': requirements}
        if phase == 'battle_acceptance':
            deployed = source.get('fields', {}).get('deployed')
            match = re.fullmatch(r'([0-9]+)/([0-9]+)', deployed or '')
            if (not match or not valid_population_counts(int(match[1]), int(match[2]))
                    or int(match[1]) != int(match[2])):
                raise unverified('出战验收须当前完整人口读数合法且已满；玩家等级上限不能代替人口域')
        goals, task_source = None, None
        if phase == 'startup_guide':
            actual = panel_source or source
            actual_snapshot = actual.get('snapshot_id', record['proof']['snapshot_id'])
            goals = coaching.reviewed_goals(value['goals'], actual_snapshot)
            task_source = {'snapshot_id': actual_snapshot,
                'evidence_file': manual_record['after']['evidence_file'] if manual_record else record['proof']['evidence_file'],
                'observed_at': actual['observed_at'], 'match_id': self.active_match_id,
                'stage': stage, 'resume_epoch': self.epoch(), 'source': 'validated_preparation_review'}
            if manual_record is not None:
                task_source.update(observation_epoch=manual_record['binding']['old_epoch'],
                                   revalidated_at=fresh_source['observed_at'])
        mode = value.get('mode')
        if mode in ('标准博弈', '超频博弈'):
            self.live_mode = {'value': mode, 'match_id': self.active_match_id, 'origin': 'supervising_agent',
                              'proof': record['proof']}
        self.preparation_reviews[phase] = {**value, 'proof': record['proof'], 'origin': 'supervising_agent',
                                           'observed_at': source['observed_at']}
        if phase == 'startup_guide':
            self.preparation_reviews[phase].update(goals=goals, task_source=task_source)
            rewards = self.preparation_reviews.get('rewards') or {}
            if manual_record is None or rewards.get('proof', {}).get('snapshot_id') != record['proof']['snapshot_id']:
                self.preparation_reviews.pop('rewards', None)
                self.log({'event': 'guide_requires_current_reward_rescan', 'stage': stage,
                          'input_resent': False, 'reason': '指南领取可能新增掉落，保留指南结果并重扫奖励'})
        self.context['preparation_review'] = {'value': self.preparation_reviews, 'origin': 'supervising_agent'}
        self.node_progress = getattr(self, 'node_progress', 0) + 1
        self.log({'event': 'preparation_phase_verified', 'phase': phase, 'stage': stage,
                  'origin': 'supervising_agent', 'proof': record['proof']})

    def verified_manual_source(self, item, fresh):
        epoch = optional(self.run / 'runner-resume-epoch.json') or {}
        binding = item.get('binding', {})
        status = self.c.status()
        if (item.get('schema') != 'manual-preparation-result/v1' or item.get('receipt_protocol') != 2
                or item.get('status') != 'completed' or item.get('outcome') != 'success'
                or binding.get('run_id') != self.owner['run_id'] or binding.get('match_id') != self.active_match_id
                or binding.get('stage') != self.preparation_scope[1]
                or binding.get('manual_id') != epoch.get('consumed_manual_id')
                or binding.get('old_epoch') != epoch.get('previous_epoch')
                or binding.get('old_epoch') == self.epoch() or manual_state(self.run)
                or not status.get('ready') or status.get('paused') or status.get('input_halted')
                or status.get('game_foreground') is not True
                or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()):
            raise ValueError('人工trace不属于当前局/节点/新交接代次')
        actual_stage = canonical_stage(fresh.get('fields', {}).get('stage'))
        if actual_stage and actual_stage != binding['stage']:
            raise ValueError('人工trace与实际当前节点冲突')
        if not actual_stage or fresh.get('preparation_stage') != binding['stage']:
            raise ManualReviewDeferred('当前帧尚无实际节点HUD；回到同节点新帧再复核，不伪造节点')
        event, resume = verified_resume_event(self.run, self.owner, self.c, epoch)
        bridge = getattr(self, 'manual_stage_verified', None) or {}
        bridged = (bridge.get('resume_epoch') == self.epoch()
            and bridge.get('from_stage') == event.get('stage') and bridge.get('stage') == binding['stage']
            and getattr(self, 'manual_stage_consumed', None) == (self.epoch(), bridge.get('bridge_id')))
        if event.get('match_id') != binding['match_id'] or event.get('stage') != binding['stage'] and not bridged:
            raise ValueError('恢复CAS事件的对局/节点与人工trace冲突')
        capture_id = fresh.get('capture_request_id')
        if not capture_id:
            raise ManualReviewDeferred('当前请求缺少新epoch捕获收据；先补当前观察')
        capture = await_existing_receipt(self.run, self.c, capture_id, 0)
        observation = capture['result'].get('observation') or {}
        if (capture['request'].get('actions') != [{'type': 'observe', 'args': []}]
                or capture['request'].get('handoff') is True or capture['result'].get('ok') is not True
                or observation.get('frame_protocol') != 1 or observation.get('request_id') != capture_id
                or observation.get('frame_id') != fresh.get('frame_id')
                or observation.get('snapshot_sha256') != fresh.get('snapshot_id')
                or observation.get('captured_at') != fresh.get('captured_at')):
            raise ValueError('新交接观察的请求/帧/摘要身份不符')
        times = [datetime.fromisoformat(item[key]['observed_at']) for key in ('before', 'after')]
        resumed = datetime.fromisoformat(epoch['time'])
        fresh_time = datetime.fromisoformat(fresh['observed_at'])
        captured = datetime.fromisoformat(fresh['captured_at'])
        if (any(value.tzinfo is None for value in [*times, resumed, captured, fresh_time])
                or not times[0] <= times[1] <= resumed <= captured <= fresh_time
                or (fresh_time - times[1]).total_seconds() > 3600):
            raise ValueError('人工trace时序/期限不符；重新观察')
        fence = self.verify_manual_mutation_fence(item)
        saved_ids = [saved.get('id') for saved in item.get('input_receipts', [])]
        if (item.get('prior_receipt_ids') != item['before'].get('receipt_watermark')
                or len(set(saved_ids)) != len(saved_ids)
                or set(item['after'].get('receipt_watermark', [])) - set(item['prior_receipt_ids'])
                   != set(saved_ids) | {item['after']['receipt_id']}):
            raise ValueError('人工输入归档与前后回执watermark不符')
        for saved in item.get('input_receipts', []):
            receipt = await_existing_receipt(self.run, self.c, saved['id'], 0)
            if redact(receipt) != saved or manual_receipt_state(receipt)['unknown_input']:
                raise ValueError('人工原收据改变或仍含未知输入；不继承阶段，不重发')
        for key in ('before', 'after'):
            frame = item[key]
            path = Path(frame['evidence_file'])
            if path.resolve().parent != self.records.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != frame['snapshot_id']:
                raise ValueError('人工前后原帧缺失/改变')
            receipt = await_existing_receipt(self.run, self.c, frame['receipt_id'], 0)
            observation = receipt['result'].get('observation') or {}
            if (receipt['request'].get('actions') != [{'type': 'observe', 'args': []}]
                    or receipt['request'].get('handoff') is True or receipt['result'].get('ok') is not True
                    or observation.get('frame_protocol') != 1 or observation.get('request_id') != frame['receipt_id']
                    or observation.get('snapshot_sha256') != frame['snapshot_id']
                    or observation.get('frame_id') != frame.get('frame_id')
                    or observation.get('captured_at') != frame.get('captured_at')):
                raise ValueError('人工观察回执未确认')
        source = self.perception.read(Path(item['after']['evidence_file']))
        stage = canonical_stage(source.get('fields', {}).get('stage'))
        if stage and stage != binding['stage']:
            raise ValueError('人工后帧节点已改变')
        return {**source, 'preparation_stage': binding['stage'], 'observed_at': item['after']['observed_at'],
                'manual_later_mutations': fence['later_mutations']}

    def verify_manual_mutation_fence(self, item):
        watermark = item.get('after', {}).get('receipt_watermark')
        if (not isinstance(watermark, list) or len(watermark) > 4096
                or any(not isinstance(rid, str) for rid in watermark) or len(set(watermark)) != len(watermark)):
            raise ValueError('人工阶段缺少确切回执watermark；保持未知')
        allowed = set()
        phase_index = coaching.PHASES.index(item['phase'])
        for path in (self.run / 'manual-results').glob('*.json'):
            later = entry.read_json(path)
            if (later.get('binding') == item['binding'] and later.get('status') == 'completed'
                    and later.get('phase') in coaching.PHASES[phase_index + 1:]):
                for saved in later.get('input_receipts', []):
                    current = await_existing_receipt(self.run, self.c, saved['id'], 0)
                    if redact(current) == saved and not manual_receipt_state(current)['unknown_input']:
                        allowed.add(saved['id'])
        known = set(watermark)
        found, mutations = set(), []
        for index, path in enumerate((self.run / 'request-ledger').glob('*.json')):
            if index >= 4096:
                raise ValueError('回执fence超出有界容量')
            raw = entry.read_json(path)
            rid = raw.get('id')
            found.add(rid)
            if rid in known:
                continue
            receipt = await_existing_receipt(self.run, self.c, rid, 0)
            request, result = receipt['request'], receipt['result']
            if request.get('kind') == 'resume' and rid == self.epoch():
                verified_resume_event(self.run, self.owner, self.c, optional(self.run / 'runner-resume-epoch.json') or {})
                continue
            delivery = manual_receipt_state(receipt)
            if delivery['unknown_input']:
                raise ValueError('阶段之后的请求含未知输入；不继承旧完成，不重发')
            if delivery['state'] == 'zero_input':
                continue
            if delivery['state'] == 'control' and rid in allowed:
                continue
            if request.get('kind') != 'actions' or not isinstance(request.get('actions'), list):
                raise ValueError('阶段之后出现不属于恢复的请求；人工结果未知')
            mutation = (request.get('handoff') is True
                        or any(action.get('type') not in ('observe', 'wait') for action in request['actions']))
            if mutation and (item['phase'] in ('lineup_equipment', 'battle_acceptance') or rid not in allowed):
                raise ValueError('人工阶段之后存在未覆盖的输入变化；旧准备结果不继承')
            if mutation:
                mutations.append(rid)
        if known - found:
            raise ValueError('人工阶段的原回执watermark有缺失；不继承旧完成')
        return {'later_mutations': sorted(mutations)}

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

    def consume_manual_stage_bridge(self, observed):
        bridge = optional(self.run / manual_stage.FILE)
        identity = (self.epoch(), (bridge or {}).get('bridge_id'))
        if (not bridge or manual_state(self.run)
                or getattr(self, 'manual_stage_consumed', None) == identity):
            return False
        verified = manual_stage.verify_for_resume(self, observed)
        if verified is None:
            return False
        self.last_preparation_stage = verified['stage']
        self.preparation_scope = (self.active_match_id, verified['stage'], self.epoch())
        self.preparation_reviews, self.strategy_reads = {}, {}
        self.economy_binding, self.economy_read_cache, self.cached_guide_binding = None, None, None
        for key in ('preparation_review', 'economy_plan', 'team', 'gear', 'bonds', 'tasks', 'coins', 'xp'):
            self.context[key] = None
        self.state['decision_request'] = None
        self.manual_stage_blocked = verified['delivery_blocked_ids']
        self.manual_stage_business_pending = verified['business_pending']
        self.manual_stage_verified = verified
        self.manual_stage_consumed = identity
        (self.run / 'runner-battle-approval.json').unlink(missing_ok=True)
        self.log({'event': 'manual_current_stage_adopted', **verified,
                  'next_phase': 'rewards', 'spending_and_pending_preserved': True})
        self.publish(reason='已核当前节点桥；保留原花费/待验，重新从领奖开始')
        return True

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
        deferred = getattr(self, 'manual_results_deferred', {})
        changed = False
        for phase in coaching.PHASES:
            current = self.preparation_reviews.get(phase) or {}
            if (current.get('completed') is True
                    and current.get('proof', {}).get('source') == 'observed_screen'
                    and current.get('proof', {}).get('resume_epoch') == self.epoch()):
                # A new ordinary supervising review can advance this phase
                # even when its historical trace is pending or hard-rejected.
                continue
            candidates = [item for item in items if item.get('phase') == phase
                and item.get('status') == 'completed' and item.get('binding', {}).get('manual_id') == epoch.get('consumed_manual_id')]
            if not candidates:
                break
            if len(candidates) != 1:
                raise ValueError('人工阶段trace重复；保持未知')
            item = candidates[0]
            identity = (self.epoch(), item['checkpoint_id'])
            if identity in used:
                break  # A subsequently invalidated phase needs a current review.
            if identity in rejected:
                break
            request_key = request.get('request_id') or (request.get('snapshot_id'),
                request.get('observation', {}).get('capture_request_id'))
            if deferred.get(identity) == request_key:
                break  # No repeated OCR of the same immutable decision frame.
            if self.preparation_checklist(self.last_observation)['phase'] != phase:
                break
            proof = {'source': 'observed_screen', 'snapshot_id': request['snapshot_id'],
                     'evidence_file': request['evidence_file'], 'resume_epoch': self.epoch()}
            try:
                fresh_data = Path(request.get('original_png') or request['evidence_file']).read_bytes()
                if hashlib.sha256(fresh_data).hexdigest() != request['snapshot_id']:
                    raise ValueError('新交接的原始PNG与当前请求不符；人工结果保持未知')
                self.review_preparation({'value': item['review'], 'proof': proof}, manual_record=item)
            except (ManualReviewDeferred, entry.ObservationUnavailable, FileNotFoundError, TimeoutError) as exc:
                deferred[identity] = request_key
                self.manual_results_deferred = deferred
                self.log({'event': 'manual_phase_deferred', 'phase': phase,
                    'checkpoint_id': item['checkpoint_id'], 'new_epoch': self.epoch(),
                    'request_id': request.get('request_id'), 'error': str(exc), 'input_resent': False,
                    'next_step': '当前请求补必要观察或监督复核；保留已验阶段，不重新接管'})
                break
            except ValueError as exc:
                rejected.add(identity)
                self.manual_results_rejected = rejected
                self.log({'event': 'manual_phase_conflict', 'phase': phase,
                    'checkpoint_id': item['checkpoint_id'], 'new_epoch': self.epoch(),
                    'error': str(exc), 'input_resent': False,
                    'next_step': '旧trace不继承；允许当前请求独立复核未完成阶段'})
                break
            fresh_source = self.history[request['snapshot_id']]
            fresh_id = fresh_source['capture_request_id']
            fresh_path = self.records / ('manual-' + item['checkpoint_id'] + '-reobserved-'
                + hashlib.sha256((self.epoch() + fresh_id).encode()).hexdigest()[:16] + '.png')
            with fresh_path.open('xb') as stream:
                stream.write(fresh_data)
            resume_event, resume_receipt = verified_resume_event(self.run, self.owner, self.c, epoch)
            item['reconciliation'] = {'run_id': self.owner['run_id'], 'match_id': self.active_match_id,
                'stage': self.preparation_scope[1], 'manual_id': item['binding']['manual_id'],
                'new_epoch': self.epoch(), 'fresh_proof': proof, 'fresh_original_png': str(fresh_path),
                'fresh_receipt': redact(await_existing_receipt(self.run, self.c, fresh_id, 0)),
                'resume_receipt': redact(resume_receipt), 'resume_event': resume_event,
                'reconciled_at': now(), 'input_resent': False}
            self.c.write_json(self.records / ('manual-' + item['checkpoint_id'] + '.json'), redact(item))
            self.preparation_reviews[phase]['manual_trace'] = {'checkpoint_id': item['checkpoint_id'],
                'manual_id': item['binding']['manual_id'], 'before': item['before']['evidence_file'],
                'after': item['after']['evidence_file'], 'outcome': item['outcome'],
                'image_changed': item.get('image_changed'), 'new_epoch': self.epoch()}
            used.add(identity)
            self.manual_results_consumed = used
            deferred.pop(identity, None)
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

    @staticmethod
    def reward_coins(observed):
        value = observed.get('semantic', {}).get('coins', {})
        if (value.get('bounds') == GOLD_HUD and type(value.get('value')) is int and value['value'] >= 0
                and .15 <= value.get('currency_icon_gold_fraction', 0) <= 1.):
            return value['value']
        return None

    def save_reward_step(self, pending):
        self.c.write_json(self.records / ('reward-step-' + pending['step_id'] + '.json'), redact(pending))
        self.c.write_json(self.run / 'reward-step.json', redact(pending))

    def pending_reward_step(self, observed):
        pending = optional(self.run / 'reward-step.json')
        if (pending and pending.get('status') not in ('verified', 'refused')
                and pending.get('match_id') == self.active_match_id):
            if manual_steps.reward_continuation(self.run, self.owner, self.c, self.records, pending):
                return None  # Only this historical blocker; no input/phase authority.
            return pending
        return None

    def recover_reward(self, evidence):
        """One explicit current-state recovery through the owning manual Worker.

        Do not append an automatic verification frame, refund a retry, infer
        the old fee, or turn the old unknown outcome into a success. There is
        exactly one new read-only capture under the existing Entry lease.
        """
        if not manual_steps.active(self):
            raise ValueError('reward recovery requires the current ManualPhase')
        context = self.manual_step_context
        request = copy.deepcopy(self.state.get('decision_request') or {})
        pending = optional(self.run / 'reward-step.json') or {}
        binding = context['binding']
        identity = manual_steps.validate_reward_recovery(evidence, request, binding,
            context['payload']['checkpoint_id'], pending)
        if context.get('reward_recovery_intent') != identity or self.pending_reward_capacity():
            raise ValueError('reward recovery intent differs or capacity is still pending')
        path = self.records / ('reward-continuation-' + pending['step_id'] + '.json')
        if path.exists():
            raise ValueError('original reward already has a recovery record; never replay it')
        source_digest = manual_steps._digest(pending)

        def guard():
            if (not manual_steps.active(self) or time.monotonic() >= self.deadline
                    or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()
                    or self.active_match_id != binding['match_id'] or self.epoch() != binding['old_epoch']
                    or manual_steps._digest(optional(self.run / 'reward-step.json') or {}) != source_digest
                    or self.state.get('decision_request') != request
                    or not self.c.status().get('game_foreground')):
                raise ValueError('reward recovery stopped or owner/request/epoch/pending changed')
            manual_steps.validate_reward_recovery(evidence, request, binding,
                context['payload']['checkpoint_id'], pending)

        with self.observation_input_lease():
            guard()
            # Unlike the old delta-attribution fence, a proved control handoff
            # is permitted here. No later game input may be hidden in recovery.
            original = manual_steps._recovery_original(self.run, self.c, self.records, pending)
            if pending['resume_epoch'] != self.epoch():
                event, unused = verified_resume_event(self.run, self.owner, self.c,
                    optional(self.run / 'runner-resume-epoch.json') or {})
                if (event.get('old_epoch') != pending['resume_epoch']
                        or event.get('match_id') != self.active_match_id or event.get('stage') != binding['stage']):
                    raise ValueError('reward recovery requires the exact original single-hop resume event')
            reviewed = request['observation']
            manual_steps._bound_reward_frame(self.run, self.c, reviewed, request['original_png'])
            prior = pending.get('prior_receipt_ids')
            watermark = self.economy_receipt_watermark()
            if (not isinstance(prior, list) or len(set(prior)) != len(prior)
                    or not set(prior).issubset(watermark) or pending['request_id'] not in watermark):
                raise ValueError('reward recovery lacks the complete original receipt watermark')
            for rid in set(watermark) - set(prior) - {pending['request_id']}:
                item = await_existing_receipt(self.run, self.c, rid, 0)
                delivery, req, result = manual_receipt_state(item), item['request'], item['result']
                if delivery['state'] == 'control' and not delivery['unknown_input']:
                    continue  # Existing exact pause-id resume receipt, not an orb effect.
                if (delivery['unknown_input'] or req.get('kind') != 'actions'
                        or req.get('handoff') is not False or not req.get('actions')
                        or any(a['type'] not in ('observe', 'wait') for a in req['actions'])
                        or result.get('input_attempted') not in (None, False)
                        or result.get('attempted_actions') not in (None, [])):
                    raise ValueError('later input/unknown delivery prevents single-reward current recovery')
            before_coins = pending['before'].get('coins')
            reviewed_coins = self.reward_coins(reviewed)
            if (type(before_coins) is not int or reviewed_coins is None or reviewed_coins < before_coins):
                raise ValueError('reward recovery current native coins unknown or below original; no fee inference')
            from currency_wars_rewards import target_effect, stable_absence
            before_data = Path(pending['before_png']).read_bytes()
            reviewed_data = Path(request['original_png']).read_bytes()
            reviewed_effect = target_effect(pending['before']['observation'], reviewed,
                before_data, reviewed_data, pending['target'])
            if reviewed_effect.get('state') != 'absent':
                raise ValueError('reviewed current target is not exposed/absent; inspect current page first')
            guard()
            with self.profile_span('reward_supervisor_recovery', operation='rules', business_step=pending['kind'],
                                   request_id=pending['request_id']):
                fresh = self.observe(scope='rewards', max_attempts=1)
            guard()
            if (fresh.get('capture_request_id') in watermark
                    or set(self.economy_receipt_watermark()) != set(watermark) | {fresh.get('capture_request_id')}
                    or (optional(self.run / 'result.json') or {}).get('id') != fresh.get('capture_request_id')):
                raise ValueError('reward recovery current capture watermark changed')
            manual_steps._bound_reward_frame(self.run, self.c, fresh, self.frame_path, read_only=True)
            data = self.frame_path.read_bytes()
            current_effect = target_effect(pending['before']['observation'], fresh,
                before_data, data, pending['target'])
            coins = self.reward_coins(fresh)
            if (coins != reviewed_coins or current_effect.get('state') != 'absent'
                    or not stable_absence(reviewed, fresh, reviewed_data, data, pending['target'])):
                raise ValueError('reward recovery current page/target/coins is not stable; zero input, history unknown')
            current_path = self.records / ('reward-continuation-' + pending['step_id'] + '-'
                + context['step_id'] + '-current.png')
            with current_path.open('xb') as stream:
                stream.write(data)
            continuation = dict(schema='supervised-reward-continuation/v1', status='authorized',
                source='supervising_agent', binding=copy.deepcopy(binding), evidence=copy.deepcopy(evidence),
                reward_step_id=pending['step_id'], original_request_id=pending['request_id'],
                manual_step_id=context['step_id'], source_record_sha256=source_digest,
                original_receipt_sha256=manual_steps._digest(redact(original)),
                reviewed=dict(png=request['original_png'], observation=reviewed, effect_evidence=reviewed_effect),
                current=dict(png=str(current_path), observation=copy.deepcopy(fresh), effect_evidence=current_effect),
                current_coins=coins, receipt_watermark=self.economy_receipt_watermark(),
                prior_outcome_remains_unknown=True, all_rewards_cleared=None, input_resent=False, recorded_at=now())
            guard()
            with path.open('x', encoding='utf-8') as stream:
                json.dump(redact(continuation), stream, ensure_ascii=False, indent=2)
            self.preparation_scope = (self.active_match_id, binding['stage'], self.epoch())
            self.preparation_reviews = {}
            self.economy_binding = None
            self.context['economy_plan'] = None
            self.log({'event': 'reward_current_recovery', 'request_id': pending['request_id'],
                'manual_step_id': context['step_id'], 'current_snapshot_id': fresh['snapshot_id'],
                'historical_outcome': 'unknown', 'all_rewards_cleared': None, 'input_resent': False})
        return continuation

    def guard_reward_step(self, pending, observed):
        """Same submission lock as command; this does not make screen/input atomic."""
        before, latest = pending['before'], optional(self.run / 'result.json') or {}
        stored = optional(self.run / 'reward-step.json') or {}
        frame = (getattr(self, 'frame_result', None) or {}).get('observation', {})
        if (stored.get('step_id') != pending['step_id'] or stored.get('request_id') != pending['request_id']
                or any(stored.get(key) != pending.get(key) for key in ('before', 'target', 'match_id', 'resume_epoch',
                    'kind', 'source', 'roi_evidence', 'manual_step_id'))
                or pending.get('publication_attempted') is not False
                or pending['match_id'] != self.active_match_id or not pending['resume_epoch']
                or pending['resume_epoch'] != self.epoch()
                or self.preparation_checklist(observed)['phase'] != 'rewards'
                or canonical_stage(observed.get('fields', {}).get('stage')) != before['stage']
                or any(observed.get(key) != before['observation'].get(key)
                       for key in ('page', 'snapshot_id', 'capture_request_id', 'frame_id'))
                or latest.get('id') != observed.get('capture_request_id')
                or frame.get('request_id') != observed.get('capture_request_id')
                or frame.get('frame_id') != observed.get('frame_id')
                or frame.get('snapshot_sha256') != observed.get('snapshot_id')
                or hashlib.sha256(self.frame_path.read_bytes()).hexdigest() != observed['snapshot_id']
                or hashlib.sha256(Path(pending['before_png']).read_bytes()).hexdigest() != observed['snapshot_id']
                or self.pending_reward_capacity()):
            raise ValueError('领奖发布前帧/阶段/epoch/原pending已变化，未提交')
        if pending.get('source') == 'supervising_agent':
            from currency_wars_rewards import stable_reward_target
            context = getattr(self, 'manual_step_context', None) or {}
            evidence = pending.get('roi_evidence')
            request = self.state.get('decision_request') or {}
            if (not manual_steps.active(self) or context.get('step_id') != pending.get('manual_step_id')
                    or context.get('payload', {}).get('reply') != {'reward_roi': evidence}):
                raise ValueError('单球标注没有当前ManualPhase单次意图，未提交')
            manual_steps.validate_reward_roi(evidence, request, context['binding'],
                                             context['payload']['checkpoint_id'])
            if (any(pending['target'].get(key) != evidence.get(key) for key in ('kind', 'bounds', 'center'))
                    or not stable_reward_target(request['observation'], observed,
                        Path(request['original_png']).read_bytes(), self.frame_path.read_bytes(), pending['target'])):
                raise ValueError('主管单球的原帧/当前目标暴露或位置改变，未提交')

    def reward_delivery(self, pending):
        """The original one-input receipt, never a replacement or inferred result."""
        if (pending.get('match_id') != self.active_match_id or pending.get('resume_epoch') != self.epoch()
                or not pending.get('request_id') or pending.get('publication_attempted') is not True):
            raise ValueError('领奖原请求/match/epoch未决，保持pending且不重发')
        self.verify_economy_fence(pending)
        item = await_existing_receipt(self.run, self.c, pending['request_id'], 0)
        delivery = manual_receipt_state(item)
        if (delivery['unknown_input'] or delivery['state'] != 'completed'
                or item['result'].get('ok') is not True
                or len([a for a in item['request']['actions'] if a['type'] not in ('wait', 'observe')]) != 1):
            raise ValueError('领奖原回执不是一笔完整确定输入，保持pending且不重发')
        return item

    def observe_reward_result(self, pending):
        """Spend one persistent read-only attempt, including a missing result frame."""
        with self.c.submission_lock():
            self.reward_delivery(pending)  # BEFORE any successor capture.
            stored = optional(self.run / 'reward-step.json') or {}
            if (stored.get('step_id') != pending.get('step_id')
                    or stored.get('request_id') != pending.get('request_id')
                    or stored.get('verification_reads', 0) != pending.get('verification_reads', 0)
                    or pending.get('verification_reads', 0) >= 2 or time.monotonic() >= self.deadline
                    or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()):
                raise ValueError('领奖两次只读核效预算/停止期限已到；原输入保持待验，不重发')
            pending['verification_reads'] = pending.get('verification_reads', 0) + 1
            self.save_reward_step(pending)  # Failed reads/crashes never refund the budget.
            # Let transient page/context settle inside the same bounded job.
            # No extra capture or retry is added, and a stop interrupts the wait.
            with self.profile_span('reward_effect_wait', operation='rules', business_step=pending['kind'],
                                   request_id=pending['request_id']):
                until = time.monotonic() + (.25 if pending['verification_reads'] == 1 else .5)
                while True:
                    if (time.monotonic() >= self.deadline or self.epoch() != pending['resume_epoch']
                            or self.manual_input_blocked() or (self.run / 'runner-stop').exists()
                            or (self.run / 'broker-stop').exists()):
                        raise ValueError('领奖核效等待被停止/交接/期限中止；已花只读额度不退回')
                    remaining = until - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(.05, remaining))
            with self.profile_span('reward_effect_observe', operation='rules', business_step=pending['kind'],
                                   request_id=pending['request_id']):
                return self.observe(scope='rewards', max_attempts=1)

    def verify_reward_effect(self, pending, observed):
        """A receipt and stable visual result are separate obligations."""
        while True:
            try:
                self.reconcile_reward_step(pending, observed)
                return self.last_observation
            except RewardEffectPending as exc:
                pending['reason'] = str(exc)
                self.save_reward_step(pending)
                observed = self.observe_reward_result(pending)

    def reconcile_reward_step(self, pending, observed):
        """Verify one control effect. No absent-orb inference clears a phase."""
        with self.c.submission_lock():
            if pending['match_id'] != self.active_match_id or pending['resume_epoch'] != self.epoch():
                raise ValueError('领奖后页面/节点/epoch变化，原效果待复核')
            item = self.reward_delivery(pending)
            frame = self.frame_result.get('observation', {})
            latest = optional(self.run / 'result.json') or {}
            if (latest.get('id') != observed.get('capture_request_id')
                    or frame.get('request_id') != observed.get('capture_request_id')
                    or frame.get('frame_id') != observed.get('frame_id')
                    or frame.get('snapshot_sha256') != observed['snapshot_id']
                    or observed.get('capture_request_id') in pending['prior_receipt_ids']
                    or observed.get('frame_id') == pending['before']['observation'].get('frame_id')
                    or hashlib.sha256(self.frame_path.read_bytes()).hexdigest() != observed['snapshot_id']):
                raise ValueError('领奖后图不是当前不可变同请求帧，保持pending')
            capture = await_existing_receipt(self.run, self.c, observed['capture_request_id'], 0)
            descriptor = (capture.get('result') or {}).get('observation') or {}
            if (descriptor.get('request_id') != observed['capture_request_id']
                    or descriptor.get('frame_id') != observed['frame_id']
                    or descriptor.get('snapshot_sha256') != observed['snapshot_id']):
                raise ValueError('领奖后图与其原生回执帧身份不同；保持pending')
            if observed['capture_request_id'] != pending['request_id'] and (
                    any(a.get('type') not in ('observe', 'wait') for a in capture['request'].get('actions', []))
                    or manual_receipt_state(capture)['unknown_input']):
                raise ValueError('领奖后图不属于原输入或明确只读后继，保持pending')
            path = self.records / ('reward-step-' + pending['step_id'] + '-after-'
                + hashlib.sha256(observed['capture_request_id'].encode()).hexdigest()[:16] + '-'
                + observed['snapshot_id'][:16] + '.png')
            data = self.frame_path.read_bytes()
            if path.exists():
                if path.read_bytes() != data:
                    raise ValueError('领奖不可变后图已存在且字节冲突；不覆盖')
            else:
                with path.open('xb') as stream:
                    stream.write(data)
            after_coins, before_coins = self.reward_coins(observed), pending['before']['coins']
            scan = observed.get('semantic', {}).get('rewards', {})
            pending.update(after_png=str(path), after_snapshot_id=observed['snapshot_id'],
                           after_capture_request_id=observed.get('capture_request_id'),
                           after_coins=after_coins, receipt=redact(item['result']))
            frames = pending.setdefault('verification_frames', [])
            if not any(value['capture_request_id'] == observed['capture_request_id'] for value in frames):
                if len(frames) >= 3:
                    raise ValueError('领奖后图有界归档已满；不重置核效预算')
                frames.append({'capture_request_id': observed['capture_request_id'], 'frame_id': observed['frame_id'],
                    'snapshot_id': observed['snapshot_id'], 'png': str(path),
                    'relation': 'original_input' if observed['capture_request_id'] == pending['request_id']
                        else 'read_only_successor'})
            self.save_reward_step(pending)
            stage = canonical_stage(observed.get('fields', {}).get('stage'))
            if stage is not None and stage != pending['before']['stage']:
                raise ValueError('领奖后页面/节点变化，废弃本批后续目标')
            if stage is None or observed.get('page') == 'unknown':
                raise RewardEffectPending('领奖后当前节点/页面未知；只读有界核效')
            if before_coins is None:
                raise ValueError('原领奖前金币来源缺失，不能用后图补造免费输入资格')
            if after_coins is None:
                raise RewardEffectPending('领奖后金币暂未知；只读有界补读，不能记0实花或重发')
            if after_coins < before_coins:
                raise ValueError('免费领奖/收店后金币下降，不能记0实花或继续')
            if observed.get('page') != 'preparation' or scan.get('interaction_required'):
                raise ValueError('领奖后有选择/遮挡或扫描未知；只回传当前证据')
            if pending['kind'] == 'close_shop':
                if (after_coins != before_coins or scan.get('snapshot_id') != observed['snapshot_id']
                        or scan.get('scanned') is not True or scan.get('uncertain')
                        or scan.get('area_fully_visible') is not True):
                    raise ValueError('收店后金币/暴露页面未核，不能归为纯导航')
                effect = 'shop_collapsed'
            else:
                from currency_wars_rewards import target_effect, stable_absence
                evidence = target_effect(pending['before']['observation'], observed,
                    Path(pending['before_png']).read_bytes(), data, pending['target'])
                frames[-1]['effect_evidence'] = evidence
                previous = pending.pop('absence_candidate', None)
                if evidence.get('state') != 'absent':
                    self.save_reward_step(pending)
                    raise RewardEffectPending('单目标仍可见/移动/暴露未知；只读有界核效：' + str(evidence.get('reason')))
                if (not previous or not stable_absence(previous['observation'], observed,
                        Path(previous['png']).read_bytes(), data, pending['target'])):
                    pending['absence_candidate'] = {'png': str(path), 'observation': copy.deepcopy(observed)}
                    self.save_reward_step(pending)
                    raise RewardEffectPending('单目标消失尚缺第二张独立稳定后图；不重发点击')
                pending['effect_contract'] = 'single_exposed_orb_absent_two_frames/v1'
                effect = 'one_visible_orb_removed'
            pending.update(status='verified', outcome=effect, observed_coin_delta=after_coins-before_coins,
                           all_rewards_cleared=None, verified_at=now())
            pending.pop('reason', None)
            self.save_reward_step(pending)
        self.node_progress = getattr(self, 'node_progress', 0) + 1
        self.log({'event': 'reward_control_effect', 'business_step': pending['kind'],
                  'request_id': pending['request_id'], 'stage': pending['before']['stage'],
                  'before_snapshot_id': pending['before']['observation']['snapshot_id'],
                  'after_snapshot_id': observed['snapshot_id'], 'outcome': effect,
                  'all_rewards_cleared': None, 'input_resent': False})

    def advance_rewards(self, observed):
        with self.profile_span('reward_scan', operation='rules', business_step='reward_scan',
                               snapshot_id=observed.get('snapshot_id')):
            return self._advance_rewards(observed)

    def collect_reward_roi(self, evidence):
        """One explicitly supervised current ROI through the existing reward intent.

        Native targets/unknown are preserved. This is not an arbitrary-point
        plan, a permanent location profile, or permission for a second input.
        """
        if not manual_steps.active(self):
            raise ValueError('主管单球救援只接受当前Worker的manual-step/ManualPhase')
        context = self.manual_step_context
        request = self.state.get('decision_request') or {}
        manual_steps.validate_reward_roi(evidence, request, context['binding'],
                                         context['payload']['checkpoint_id'])
        if self.pending_reward_step(self.last_observation) or self.pending_reward_capacity():
            raise ValueError('已有领奖输入/效果pending；只核原ID，不发布第二球')
        target = {key: copy.deepcopy(evidence[key]) for key in ('kind', 'bounds', 'center')}
        target.update(source='supervising_agent', template_id='supervised_current_roi',
                      snapshot_id=evidence['snapshot_id'])
        old_png = Path(request['original_png']).read_bytes()
        with self.observation_input_lease():
            if manual_steps.unknown_receipts(self.run, self.c):
                raise ValueError('原输入交付未知；先对账原ID，不以新帧代替交付')
            fresh = self.observe(scope='rewards')
            from currency_wars_rewards import stable_reward_target
            if not stable_reward_target(request['observation'], fresh, old_png,
                                        self.frame_path.read_bytes(), target):
                manual_steps._ask_current(self)
                raise ValueError('主管单球的页面/布局/暴露/位置或来源已变；废弃旧坐标，零输入')
            if self.reward_coins(fresh) is None:
                manual_steps._ask_current(self)
                raise ValueError('当前领奖金币HUD未知，不能核免费动作；零输入')
            # The explicit pending rewards checkpoint restarts only the
            # preparation reviews, never a budget, strategy, or battle approval.
            self.preparation_scope = (self.active_match_id, evidence['stage'], self.epoch())
            self.preparation_reviews = {}
            self.economy_binding = None
            self.context['economy_plan'] = None
            target['snapshot_id'] = fresh['snapshot_id']
            return self._execute_reward_target(fresh, target, roi_evidence=evidence)

    @contextlib.contextmanager
    def observation_input_lease(self):
        original = self.c
        with entry.submission_lease(original, submit_deadline=self.deadline,
                                    queue_wait=self.profile_queue_wait) as leased:
            self.c = leased
            try:
                yield
            finally:
                self.c = original

    def _advance_rewards(self, observed):
        if self.preparation_checklist(observed)['phase'] != 'rewards':
            return False
        for unused in range(8):
            with self.observation_input_lease():
                following = self._advance_reward_once(observed)
            if following is None:
                return True
            observed = following
        return True

    def _advance_reward_once(self, observed):
        pending = self.pending_reward_step(observed)
        if pending:
            self.ask(observed, 'reward_result', '原单次领奖效果仍待复核；交付确定后可用manual-step recover_reward'
                     '核当前继续资格，历史unknown与原预算保留，不重发。',
                     choices={'pending': pending}, category='exception', business_step=pending['kind'])
            return None
        if self.pending_reward_capacity():
            self.ask(observed, 'reward_result', '先按已提交腾位的原收据核容量，当前不发新的领奖输入。',
                     category='exception', business_step='reward_capacity')
            return None
        scan = observed.get('semantic', {}).get('rewards', {})
        if scan.get('interaction_required'):
            self.ask(observed, 'reward_selection', '当前出现奖励相关选择/推荐提示，先核当前选项与策略联动。',
                     choices={'rewards': scan}, category='strategy', business_step='reward_selection')
            return None
        close = find_text(observed.get('rows', []), '收起', (1554, 943, 1694, 1020), exact=True)
        is_close = observed.get('page') == 'shop' and close and close.get('confidence', 0) >= .90
        if not is_close and (observed.get('page') != 'preparation'
                or scan.get('snapshot_id') != observed['snapshot_id'] or not scan.get('input_allowed')):
            self.ask(observed, 'preparation_strategy', '当前限定奖励区域未有可继续的确定控件；须全场领奖复核，不能按未见球判领空。',
                     choices={'rewards': scan}, category='capability', business_step='reward_scan')
            return None
        if self.reward_coins(observed) is None or not canonical_stage(observed.get('fields', {}).get('stage')):
            self.ask(observed, 'reward_result', '收店/免费领奖缺当前节点或绑定HUD的金币，无法核异常扣费。',
                     category='capability', business_step='reward_scan')
            return None
        # Keep the SAME Entry lease from this fresh observation to the one
        # input and original-result check. The handoff's own frame is never
        # taken as a layout-stability proof.
        old_png = self.frame_path.read_bytes()
        previous = copy.deepcopy(observed)
        fresh = self.observe(scope='rewards')
        if (fresh.get('page') != observed.get('page')
                or fresh.get('fields', {}).get('stage') != observed.get('fields', {}).get('stage')):
            self.log({'event': 'reward_relocate', 'reason': 'page_or_stage_changed', 'input_sent': False})
            return None
        from currency_wars_rewards import stable_layout
        if not stable_layout(previous, fresh, old_png, self.frame_path.read_bytes()):
            self.log({'event': 'reward_layout_unstable', 'input_sent': False,
                      'request_id': fresh.get('capture_request_id'),
                      'reason': 'target_anchor_or_visible_semantics_changed'})
            self.publish(reason='奖励布局尚未稳定；停止本批，以当前新帧继续核目标')
            return None
        observed = fresh
        scan = observed.get('semantic', {}).get('rewards', {})
        close = find_text(observed.get('rows', []), '收起', (1554, 943, 1694, 1020), exact=True)
        if is_close:
            if not close or close.get('confidence', 0) < .90:
                return None
            box = close['box']
            target = {'kind': 'close_shop', 'bounds': box,
                      'center': [(box[0]+box[2])/2, (box[1]+box[3])/2]}
        elif not scan.get('input_allowed') or scan.get('snapshot_id') != observed['snapshot_id']:
            return self.last_observation
        else:
            target = scan['targets'][0]
        coins = self.reward_coins(observed)
        if coins is None:
            return self.last_observation
        return self._execute_reward_target(observed, target)

    def _execute_reward_target(self, observed, target, *, roi_evidence=None):
        """One physical target, one immutable receipt, at most two effect reads."""
        coins = self.reward_coins(observed)
        if coins is None:
            raise ValueError('当前绑定金币未知，未提交领奖')
        step_id = uuid.uuid4().hex
        before_png = self.records / ('reward-step-' + step_id + '-before.png')
        data = self.frame_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != observed.get('snapshot_id'):
            raise ValueError('当前领奖源PNG字节不符，未提交')
        with before_png.open('xb') as stream:
            stream.write(data)
        pending = {'step_id': step_id, 'kind': target['kind'], 'match_id': self.active_match_id,
                   'resume_epoch': self.epoch(), 'status': 'pending', 'outcome': 'unknown',
                   'request_id': None, 'publication_attempted': False, 'target': target,
                   'source': 'supervising_agent' if roi_evidence is not None else 'native_reader',
                   'roi_evidence': copy.deepcopy(roi_evidence),
                   'manual_step_id': (getattr(self, 'manual_step_context', None) or {}).get('step_id'),
                   'verification_reads': 0, 'verification_frames': [],
                   'before_png': str(before_png), 'prior_receipt_ids': self.economy_receipt_watermark(),
                   'before': {'stage': canonical_stage(observed['fields']['stage']), 'coins': coins,
                              'observation': copy.deepcopy(observed)}, 'all_rewards_cleared': None}
        self.save_reward_step(pending)
        self.reward_inflight = pending
        try:
            with self.profile_span('reward_control', operation='rules', business_step=target['kind'],
                                   snapshot_id=observed['snapshot_id']):
                point = target['center']
                after = self.command([f'click:{point[0]}:{point[1]}', 'wait:0.7'],
                    '当前单个收店/奖励球控件；稳定后图验证后再定位下一目标', observed['page'],
                    '单控件效果，不代表全部领空', action={'type': 'click_point', 'purpose': 'local_reward',
                        'args': point, 'expected_page': observed['page']}, read_scope='rewards')
                if target['kind'] in ('blue_orb', 'gray_orb'):
                    self.invalidate_preparation('inventory')
                self.verify_reward_effect(pending, after)
        except (ValueError, RuntimeError, OSError) as exc:
            if pending.get('publication_attempted') and target['kind'] in ('blue_orb', 'gray_orb'):
                self.invalidate_preparation('inventory')
            pending['reason'] = str(exc)
            if pending.get('publication_attempted') is False:
                pending.update(status='refused', outcome='not_published')
            self.save_reward_step(pending)
            after = self.last_observation or observed
            selection = (after.get('page') in ('investment', 'environment', 'supply', 'reward_overlay')
                         or after.get('semantic', {}).get('rewards', {}).get('interaction_required'))
            if selection:
                try:
                    delivery = manual_receipt_state(await_existing_receipt(self.run, self.c, pending['request_id'], 0))
                    selection = not delivery['unknown_input'] and delivery['state'] == 'completed'
                except (ValueError, RuntimeError, OSError, TypeError):
                    selection = False
            self.state['decision_request'] = None
            self.ask(after, 'reward_selection' if selection else 'reward_result', str(exc),
                     choices={'pending': pending}, category='strategy' if selection else 'exception',
                     business_step=target['kind'])
            return None
        finally:
            self.reward_inflight = None
        return self.last_observation
    def pending_reward_capacity(self):
        record = optional(self.run / 'reward-capacity.json') if getattr(self, 'run', None) else None
        if record and record.get('run_id') != self.owner['run_id']:
            raise ValueError('领奖腾位记录不属于当前run')
        return record if record and record.get('status') not in ('verified', 'refused', 'superseded') else None

    def capacity_source(self, record, actual):
        """Authenticate a separate supervisor read; do not overwrite native facts."""
        if not isinstance(record, dict) or not isinstance(record.get('proof'), dict):
            raise ValueError('库存容量须当前请求主管实读proof')
        source = self.verified_source(record['proof'], 180)
        value, request = record.get('value'), self.state.get('decision_request') or {}
        if (not isinstance(value, dict) or value.get('reviewer') != 'supervising_agent'
                or not value.get('findings') or value.get('stage') != canonical_stage(actual.get('fields', {}).get('stage'))
                or source.get('page') != 'preparation' or actual.get('page') != 'preparation'
                or source.get('fields', {}).get('stage') != value['stage']
                or record['proof'].get('snapshot_id') != request.get('snapshot_id')
                or request.get('match_id') != self.active_match_id
                or request.get('resume_epoch') != self.epoch()
                or type(value.get('coins')) is not int or value['coins'] < 0):
            raise ValueError('库存/金币proof须本局本节点当前请求；人口不作库存证据')
        capacity = coaching.reviewed_capacity(value.get('inventory'))
        slots = {slot['slot']: slot for slot in capacity['slots']}
        for observed in (request['observation'], actual):
            team = observed.get('semantic', {}).get('team', {})
            for slot in team.get('slots', []):
                if (slot.get('location') != 'bench' or slot.get('snapshot_id') != observed.get('snapshot_id')
                        or slot.get('status') not in ('empty', 'occupied')):
                    continue
                declared = slots.get(slot.get('slot'), {})
                if declared.get('status') != slot['status']:
                    raise ValueError('主管库存读数与当前原生备战席冲突')
                for key in ('name', 'star'):
                    if slot.get(key) is not None and declared.get(key) is not None and slot[key] != declared[key]:
                        raise ValueError('可售对象实名/星级与原生读数冲突')
            coins = observed.get('semantic', {}).get('coins', {})
            if (coins.get('bounds') == GOLD_HUD and type(coins.get('value')) is int
                    and coins.get('currency_icon_gold_fraction', 0) >= .15
                    and coins.get('confidence', 1.) >= .90 and coins['value'] != value['coins']):
                raise ValueError('主管金币读数与当前原生HUD冲突')
        return value, capacity

    def capacity_rois(self, request, actual, bounds):
        """Keep same-request human readings fresh using exact relevant PNG regions."""
        from io import BytesIO
        from PIL import Image
        old_bytes, fresh_bytes = Path(request['original_png']).read_bytes(), self.frame_path.read_bytes()
        if (hashlib.sha256(old_bytes).hexdigest() != request['snapshot_id']
                or hashlib.sha256(fresh_bytes).hexdigest() != actual.get('snapshot_id')):
            raise ValueError('领奖腾位原图/新图身份不符')
        with Image.open(BytesIO(old_bytes)) as old, Image.open(BytesIO(fresh_bytes)) as fresh:
            old.load()
            fresh.load()
            if old.format != 'PNG' or fresh.format != 'PNG' or old.size != (1920, 1080) or fresh.size != old.size:
                raise ValueError('领奖腾位只支持当前原生1920×1080完整帧')
            for box in bounds:
                if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                        or not 0 <= box[0] < box[2] <= 1920 or not 0 <= box[1] < box[3] <= 1080):
                    raise ValueError('领奖腾位实读ROI缺失或无效')
                if old.crop(box).convert('RGB').tobytes() != fresh.crop(box).convert('RGB').tobytes():
                    raise ValueError('领奖腾位相关库存/钱/目标已变化；只重新观察')

    def reward_capacity_action(self, action, actual, *, pixels=False):
        request = self.state.get('decision_request') or {}
        proof = action.get('target_evidence') or {}
        value, capacity = self.capacity_source(action.get('capacity_review'), actual)
        sale, blocked = value.get('sale', {}), value.get('blocked_reward', {})
        if not isinstance(sale, dict) or not isinstance(blocked, dict):
            raise ValueError('腾位须明确当前被阻塞奖励和已核可售对象')
        native = next((slot for slot in native_slots() if slot['location'] == 'bench'
                       and slot['slot'] == sale.get('slot')), None)
        selected = next((slot for slot in capacity['slots'] if slot['slot'] == sale.get('slot')), {})
        if (action.get('type') != 'drag' or action.get('purpose') != 'reward_capacity'
                or action.get('expected_page') != 'preparation' or proof.get('control_id') != 'reward_capacity_sale'
                or proof.get('snapshot_id') != request.get('snapshot_id') or not native
                or capacity['free_slots'] != 0 or capacity['overflow_count'] != 0
                or blocked.get('pending') is not True or blocked.get('blocked_by_capacity') is not True
                or blocked.get('kind') != 'unit_reward' or not blocked.get('findings')
                or sale.get('not_required') is not True or not sale.get('reason')
                or sale.get('control_verified') is not True or sale.get('control_text') != '出售'
                or type(sale.get('sale_value')) is not int or sale['sale_value'] <= 0
                or not isinstance(sale.get('name'), str) or not sale['name']
                or type(sale.get('star')) is not int or sale['star'] not in (1, 2, 3)
                or selected.get('name') != sale['name'] or selected.get('star') != sale['star']):
            raise ValueError('仅支持真实满9席且无临时溢出阻塞角色奖励时，单个明确可售对象腾位')
        args, target = action.get('args', []), sale.get('control_bounds')
        box = native['bounds']
        if (len(args) != 4 or any(type(v) not in (int, float) for v in args)
                or not isinstance(target, list) or len(target) != 4
                or any(type(v) is not int for v in target)
                or not box[0] <= args[0] < box[2] or not box[1] <= args[1] < box[3]
                or not target[0] <= args[2] < target[2] or not target[1] <= args[3] < target[3]
                or target[0] <= args[0] < target[2] and target[1] <= args[1] < target[3]):
            raise ValueError('腾位必须从已核单个备战席拖到已核出售控件，任意拖动不是出售')
        if pixels:
            self.capacity_rois(request, actual, [slot['bounds'] for slot in native_slots() if slot['location'] == 'bench']
                + [GOLD_HUD, value['inventory'].get('overflow_bounds'), blocked.get('bounds'), target])
        return value

    def begin_reward_capacity(self, value, action, actual):
        if self.pending_reward_capacity():
            raise ValueError('上一腾位动作未对账；不重发')
        request = self.state['decision_request']
        watermark = [entry.read_json(path)['id'] for path in (self.run / 'request-ledger').glob('*.json')]
        if len(watermark) > 4096:
            raise ValueError('腾位回执水位超出有界容量')
        record = {'run_id': self.owner['run_id'], 'match_id': self.active_match_id,
            'resume_epoch': self.epoch(), 'stage': value['stage'], 'created_at': now(), 'status': 'unverified',
            'plan_request_id': request['request_id'], 'before_snapshot_id': actual['snapshot_id'],
            'before': value, 'proof': action['capacity_review']['proof'], 'watermark': watermark}
        self.c.write_json(self.run / 'reward-capacity.json', record)
        self.context['reward_capacity'] = record

    def review_reward_capacity(self, record):
        pending = self.pending_reward_capacity()
        request = self.state.get('decision_request') or {}
        value, capacity = self.capacity_source(record, self.last_observation)
        if (not pending or pending.get('match_id') != self.active_match_id
                or pending.get('stage') != value['stage'] or not pending.get('input_request_id')
                or value.get('input_request_id') != pending['input_request_id']
                or request.get('request_id') == pending['plan_request_id']
                or request.get('observation', {}).get('capture_request_id') in pending['watermark']
                or not request.get('observation', {}).get('captured_at')
                or datetime.fromisoformat(request['observation']['captured_at']) <= datetime.fromisoformat(pending['created_at'])
                or datetime.fromisoformat(request['created_at']) <= datetime.fromisoformat(pending['created_at'])):
            raise ValueError('腾位复核须绑定原输入收据与本节点新请求/新epoch证据；不重发旧动作')
        resume_id, resume_event = None, None
        if pending.get('resume_epoch') != self.epoch():
            # B003 owns the existing CAS/event verifier. Until that capability
            # is installed this older branch stays conservative across epochs.
            verifier = globals().get('verified_resume_event')
            if not callable(verifier):
                raise ValueError('新epoch须B003真实恢复事件校验；保留腾位记录，不重发')
            epoch = optional(self.run / 'runner-resume-epoch.json') or {}
            resume_event, resumed = verifier(self.run, self.owner, self.c, epoch)
            if (value.get('resolution') != 'manual_reconciled' and resume_event.get('old_epoch') != pending['resume_epoch']
                    or resume_event.get('new_epoch') != self.epoch()
                    or resume_event.get('match_id') != self.active_match_id or resume_event.get('stage') != value['stage']
                    or value.get('resume_event_id') != resumed['id']
                    or not datetime.fromisoformat(pending['created_at']) <= datetime.fromisoformat(epoch['time'])
                    <= datetime.fromisoformat(request['observation']['captured_at'])):
                raise ValueError('腾位新epoch复核须确切旧新代次/CAS事件/当前帧时序，不继承旧完成项')
            resume_id = resumed['id']
        receipt = await_existing_receipt(self.run, self.c, pending['input_request_id'], 0)
        current_frame = request['observation']
        capture_id = current_frame.get('capture_request_id')
        if not isinstance(capture_id, str):
            raise ValueError('腾位后新帧没有实际捕获请求身份')
        capture = await_existing_receipt(self.run, self.c, capture_id, 0)
        observed = capture['result'].get('observation') or {}
        if (capture_id != pending['input_request_id'] and capture['request'].get('actions') != [{'type': 'observe', 'args': []}]
                or capture['request'].get('handoff') is not False
                or observed.get('request_id') != capture_id or observed.get('frame_protocol') != 1
                or observed.get('snapshot_sha256') != current_frame.get('snapshot_id')
                or observed.get('frame_id') != current_frame.get('frame_id')
                or observed.get('captured_at') != current_frame.get('captured_at')):
            raise ValueError('腾位后当前帧与原捕获收据不匹配')
        expected = self.c.validate_actions([{'type': parts[0], 'args': parts[1:]}
            for parts in (token.split(':') for token in pending['tokens'])])
        if (receipt['request'].get('kind') != 'actions' or receipt['request'].get('handoff') is not False
                or receipt['request'].get('actions') != expected):
            raise ValueError('腾位收据不是原始出售请求；保持未知，不重发')
        classifier = globals().get('manual_receipt_state')
        if callable(classifier):
            delivery = classifier(receipt)
        else:
            result = receipt['result']
            # B001 legacy complete receipts are accepted only without
            # contradictory input-attempt evidence. B003 owns richer outcomes.
            complete = (result.get('ok') is True and result.get('completed') == expected
                and result.get('input_attempted') is not False
                and result.get('attempted_actions') in (None, [expected[0]]))
            delivery = {'state': 'completed' if complete else 'unknown', 'unknown_input': not complete}
        manual = None
        if value.get('resolution') == 'manual_reconciled':
            checkpoint = value.get('manual_checkpoint_id')
            if not callable(classifier) or not resume_event or not isinstance(checkpoint, str) or not re.fullmatch(r'[0-9a-f]{32}', checkpoint):
                raise ValueError('人工整理替代旧腾位须B003完整checkpoint与已校验恢复事件')
            manual = entry.read_json(self.run / 'manual-results' / (checkpoint + '.json'))
            if (manual.get('checkpoint_id') != checkpoint or manual.get('phase') not in ('rewards', 'inventory_cleanup')
                    or pending['input_request_id'] not in manual.get('prior_receipt_ids', [])
                    or datetime.fromisoformat(manual['before']['observed_at']) < datetime.fromisoformat(pending['created_at'])):
                raise ValueError('人工整理须在原腾位之后，明确覆盖当前库存；不能普通context覆盖pending')
            self.preparation_checklist(self.last_observation)
            source = self.verified_source(record['proof'], 180)
            fresh = {**source, **{key: request['observation'].get(key)
                     for key in ('capture_request_id', 'frame_id', 'captured_at')}}
            self.verified_manual_source(manual, fresh)
        elif delivery['unknown_input'] or delivery['state'] != 'completed':
            raise ValueError('腾位原输入未完整确认；业务效果未知，不重复出售')
        for index, path in enumerate((self.run / 'request-ledger').glob('*.json')):
            if index >= 4096:
                raise ValueError('腾位回执数量超出有界容量')
            item = entry.read_json(path)
            if item['id'] in pending['watermark'] or item['id'] in (pending['input_request_id'], resume_id):
                continue
            if manual and item['id'] in manual['after']['receipt_watermark']:
                continue  # B003 verified the whole intervening manual trace.
            item = await_existing_receipt(self.run, self.c, item['id'], 0)
            if (item['request'].get('kind') != 'actions' or item['request'].get('handoff') is not False
                    or any(a.get('type') != 'observe' for a in item['request'].get('actions', []))):
                raise ValueError('腾位后另有输入或交接，不能把当前差额归给原出售')
        before = pending['before']
        sold = next(slot for slot in capacity['slots'] if slot['slot'] == before['sale']['slot'])
        if (capacity['overflow_count'] != 0 or not manual and (capacity['occupied'] != 8 or sold['status'] != 'empty'
                or value['coins'] != before['coins'] + before['sale']['sale_value'])):
            raise ValueError('腾位后空槽/独立溢出/金币差額未同时核实，保留未知；不重发')
        self.capacity_rois(request, self.last_observation,
            [slot['bounds'] for slot in native_slots() if slot['location'] == 'bench']
            + [GOLD_HUD, value['inventory'].get('overflow_bounds')])
        pending.update(status='superseded' if manual else 'verified', after=record, receipt=redact(receipt),
                       delivery=delivery, sale_outcome='unknown' if manual else 'success',
                       manual_checkpoint_id=manual['checkpoint_id'] if manual else None,
                       resume_event=resume_event, verified_at=now())
        self.c.write_json(self.run / 'reward-capacity.json', pending)
        self.c.write_json(self.records / (pending['input_request_id'] + '-reward-capacity.json'), pending)
        self.context['reward_capacity'] = pending
        self.preparation_reviews.pop('rewards', None)
        self.invalidate_preparation('inventory')
        self.log({'event': 'reward_capacity_reconciled', 'input_request_id': pending['input_request_id'],
                  'sale_outcome': pending['sale_outcome'], 'resolution': pending['status'],
                  'snapshot_id': request['snapshot_id'], 'next_phase': 'rewards', 'input_resent': False})

    def economy_ledger(self, stage):
        if not canonical_stage(stage):
            raise ValueError('经济预算缺少当前真实节点')
        if not hasattr(self, 'economy_ledgers'):
            self.economy_ledgers = {}
        key = (self.active_match_id, stage)
        if key not in self.economy_ledgers:
            stored = optional(self.economy_ledger_path(stage))
            if stored is not None and (stored.get('run_id') != self.owner['run_id']
                    or stored.get('match_id') != self.active_match_id or stored.get('stage') != stage
                    or stored.get('schema') != economy.SCHEMA or not isinstance(stored.get('ledger'), dict)):
                raise ValueError('已有经济台账身份不符，不能清零覆盖')
            if stored:
                inherited = stored['ledger']
            else:
                inherited = economy.new_ledger()
                prior = getattr(self, 'business', {}).get('economy', {}).get(stage)
                if prior and prior['run_id'] != self.owner['run_id']:
                    old = prior['ledger']
                    for field in ('spent', 'paid_refreshes', 'critical_spent', 'purchased', 'revision'):
                        inherited[field] = copy.deepcopy(old[field])
                    inherited['carried_from'] = {'run_id': prior['run_id'], 'record_file': prior['record_file']}
                if getattr(self, 'business_previous_run', None):
                    inherited['prior_run_unknown'] = copy.deepcopy(getattr(self, 'business_unknown', []))
                    inherited['requires_current_budget'] = True
            self.economy_ledgers[key] = inherited
            # Shop/ROI readings never survive a process restart as fresh facts.
            self.economy_ledgers[key]['shop_complete'] = False
        return self.economy_ledgers[key]

    def economy_ledger_path(self, stage):
        identity = hashlib.sha256((self.active_match_id + ':' + stage).encode()).hexdigest()[:24]
        return self.records / ('economy-' + identity + '.json')

    def save_economy_ledger(self, stage, ledger=None):
        value = {'schema': economy.SCHEMA,
            'run_id': self.owner['run_id'], 'match_id': self.active_match_id, 'stage': stage,
            'ledger': ledger if ledger is not None else self.economy_ledger(stage)}
        self.c.write_json(self.economy_ledger_path(stage), value)
        if hasattr(self, 'business'):
            self.business['economy'][stage] = {**copy.deepcopy(value), 'record_file': str(self.economy_ledger_path(stage))}
            self.save_business()

    def economy_receipt_watermark(self):
        paths = list((self.run / 'request-ledger').glob('*.json'))
        if len(paths) > 4096:
            raise ValueError('经济回执fence超过有界容量')
        return [entry.read_json(path)['id'] for path in paths]

    def verify_economy_fence(self, pending):
        receipt = await_existing_receipt(self.run, self.c, pending['request_id'], 0)
        if (receipt['request'].get('kind') != 'actions' or receipt['request'].get('handoff') is not False
                or receipt['request'].get('actions') != pending['broker_actions']):
            raise ValueError('待验经济收据与原始动作不符，不重发')
        allowed_resume = None
        if pending['resume_epoch'] != self.epoch():
            verifier = globals().get('verified_resume_event')
            if not callable(verifier):
                raise ValueError('跨epoch经济补证须先合入B003精确恢复事件核验；旧待验请求不重发')
            event, resume = verifier(self.run, self.owner, self.c, optional(self.run / 'runner-resume-epoch.json') or {})
            if (event.get('old_epoch') != pending['resume_epoch'] or event.get('new_epoch') != self.epoch()
                    or event.get('run_id') != self.owner['run_id'] or event.get('match_id') != self.active_match_id
                    or canonical_stage(event.get('stage')) != pending['before']['stage']
                    or canonical_stage((self.last_observation or {}).get('fields', {}).get('stage')) != pending['before']['stage']):
                raise ValueError('经济待验交易不属于精确单跳恢复事件')
            allowed_resume = resume['id']
        for rid in set(self.economy_receipt_watermark()) - set(pending['prior_receipt_ids']) - {pending['request_id']}:
            other = await_existing_receipt(self.run, self.c, rid, 0)
            request, result = other['request'], other['result']
            if request.get('kind') == 'resume' and rid == allowed_resume:
                continue
            actions = request.get('actions', [])
            # Use B003's actual delivery classification, including unknown
            # input; an observe label never overrides contradictory evidence.
            delivery = manual_receipt_state(other)
            unchanged = (not delivery['unknown_input']
                         and delivery['state'] in ('zero_input', 'completed'))
            attempted = result.get('input_attempted')
            if (request.get('kind') != 'actions' or request.get('handoff') is not False or not actions
                    or any(action.get('type') not in ('wait', 'observe') for action in actions)
                    or attempted is not None and attempted is not False
                    or result.get('attempted_actions') not in (None, []) or not unchanged):
                raise ValueError('经济后帧之前出现未覆盖的其他输入，差额不能归属于原交易')
        pending['receipt'] = redact(receipt['result'])

    def archive_economy_after(self, pending, actual, *, persist=True):
        data = self.frame_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != actual['snapshot_id']:
            raise ValueError('经济交易后帧身份变化；保留原请求待验，不重发')
        target = self.records / (pending['request_id'] + '-economy-after-' + actual['snapshot_id'][:16] + '.png')
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError('原经济交易后帧已存在，不覆盖')
        else:
            with target.open('xb') as stream:
                stream.write(data)
        pending.update(after_png=str(target), after_snapshot_id=actual['snapshot_id'])
        pending['after_page'] = actual.get('page')
        pending['after_read_contract'] = copy.deepcopy(actual.get('read_contract'))
        if 'refresh_offer' in (actual.get('semantic') or {}):
            pending['after_refresh_offer'] = copy.deepcopy(actual['semantic']['refresh_offer'])
        else:
            pending.pop('after_refresh_offer', None)
        if persist:
            self.save_economy_ledger(pending['before']['stage'])

    def read_economy_fields(self, bound, image, actual, *, native_required=False,
                            ignored_legacy_refresh_fields=()):
        import currency_wars_refresh_offer as refresh_reader
        semantic = actual.get('semantic') or {}
        has_native = 'refresh_offer' in semantic
        native_required = native_required or refresh_reader.expects_offer(actual)
        ignored = set(ignored_legacy_refresh_fields)
        if native_required:
            ignored.update(name for name in economy.LEGACY_REFRESH_FIELDS if bound.get(name))
            bound = {key: value for key, value in bound.items() if key not in economy.LEGACY_REFRESH_FIELDS}
        reading = economy.observe_fields(bound, image, getattr(self.perception, 'engine', None))
        if native_required:
            offer = (refresh_reader.consume_offer(semantic['refresh_offer'], image,
                     actual['snapshot_id'], actual.get('page')) if has_native else None)
            for name in economy.LEGACY_REFRESH_FIELDS:
                reading['values'].pop(name, None)
                reading['evidence'].pop(name, None)
            reading['unknown'] = [name for name in reading['unknown'] if name not in economy.LEGACY_REFRESH_FIELDS]
            reading['values']['refresh_offer'] = offer
            if offer is None:
                reading['unknown'].append('refresh_offer')
            reading['evidence']['refresh_offer'] = {
                'source': 'current_native_refresh_offer' if has_native else 'native_refresh_offer_missing',
                'snapshot_id': actual['snapshot_id'], 'page': actual.get('page')}
            reading['evidence']['ignored_legacy_refresh_fields'] = sorted(ignored)
        return reading

    def economy_observation(self, actual, binding=None):
        from PIL import Image
        import currency_wars_refresh_offer as refresh_reader
        binding = binding or getattr(self, 'economy_binding', None)
        stage = canonical_stage(actual.get('fields', {}).get('stage'))
        if (not binding or binding['scope'] != (self.active_match_id, stage, self.epoch())
                or actual.get('page') not in economy.PREPARATION_PAGES):
            raise ValueError('本节点/当前epoch尚无已核经济读数和统一预算')
        contract = actual.get('read_contract') or {}
        semantic = actual.get('semantic') or {}
        has_native = 'refresh_offer' in semantic
        if refresh_reader.expects_offer(actual):
            # Upgraded observations close the legacy path for this binding;
            # a later missing native key is unknown, not an old numeric ROI.
            binding['native_refresh_offer'] = True
        # Same PNG bytes can be rederived under a newer field contract. Its
        # presence and exact source must be validated before a cache can match.
        native_key = json.dumps(semantic['refresh_offer'], sort_keys=True, ensure_ascii=False,
                                allow_nan=False) if has_native else None
        cache_key = (actual['snapshot_id'], binding['snapshot_id'], contract.get('version'),
                     contract.get('requested_scope'), contract.get('effective_scope'), actual.get('page'),
                     binding.get('native_refresh_offer', False), has_native, native_key)
        data = self.frame_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != actual['snapshot_id']:
            raise ValueError('经济回读不属于本请求不可变原帧')
        cached = getattr(self, 'economy_read_cache', None)
        if cached and cached[0] == cache_key:
            return cached[1]
        from io import BytesIO
        with Image.open(BytesIO(data)) as image:
            image.load()
            reading = self.read_economy_fields(binding['fields'], image, actual,
                native_required=binding.get('native_refresh_offer', False),
                ignored_legacy_refresh_fields=binding.get('ignored_legacy_refresh_fields', ()))
        native = actual.get('semantic', {}).get('coins') or {}
        if (native.get('bounds') == GOLD_HUD and type(native.get('value')) is int and native['value'] >= 0
                and .15 <= native.get('currency_icon_gold_fraction', 0) <= 1.
                and .90 <= native.get('confidence', 1.) <= 1.):
            if 'coins' in reading['values'] and native['value'] != reading['values']['coins']:
                reading['values'].pop('coins')
                reading['unknown'].append('coins')
            else:
                reading['values']['coins'] = native['value']
                reading['unknown'] = [key for key in reading['unknown'] if key != 'coins']
                reading['evidence']['coins'] = {'source': 'current_native_gold_hud', 'bounds': GOLD_HUD}
        shop, complete = [], actual.get('page') == 'shop'
        native_shop = actual.get('shop') or {}
        if actual.get('page') == 'shop':
            geometry = native_shop.get('page', {}).get('geometry', {})
            scores = geometry.get('frame_scores', [])
            complete = (native_shop.get('schema') == 'currency-wars-shop-observation/v1'
                        and native_shop.get('input', {}).get('sha256') == actual['snapshot_id']
                        and native_shop.get('input', {}).get('size') == [1920, 1080]
                        and native_shop.get('input', {}).get('format') == 'PNG'
                        and native_shop.get('page', {}).get('reliable_open_shop') is True
                        and len(scores) == 5 and all(type(value) in (int, float) and .60 <= value <= 1. for value in scores)
                        and geometry.get('overlays') == [])
            for slot_id in range(1, 6):
                slot = purchase_slot(native_shop, slot_id, actual['snapshot_id'])
                if slot:
                    shop.append({key: slot[key] for key in ('slot', 'name', 'cost')} | {'status': 'recognized'})
                    continue
                found = [slot for slot in native_shop.get('slots', []) if slot.get('slot') == slot_id]
                if len(found) == 1 and found[0].get('status') == 'empty':
                    shop.append({'slot': slot_id, 'status': 'empty', 'name': None, 'cost': None})
                else:
                    complete = False
            ledger = self.economy_ledger(stage)
            if complete:
                ledger['last_shop'], ledger['shop_epoch'] = shop, self.epoch()
                ledger['shop_complete'] = True
            else:
                ledger['shop_complete'] = False
        else:
            ledger = self.economy_ledger(stage)
            complete = ledger.get('shop_complete') is True and ledger.get('shop_epoch') == self.epoch()
            shop = ledger.get('last_shop', []) if complete else []
        result = {**reading, 'snapshot_id': actual['snapshot_id'], 'stage': stage,
                  'shop': shop, 'shop_complete': complete, 'source': 'explicit_economic_observation'}
        self.economy_read_cache = cache_key, result
        return result

    def accept_economy_plan(self, record):
        # Budget replacement and acknowledged pending reconciliation commit
        # under the same existing input submission fence.
        with self.c.submission_lock():
            return self._accept_economy_plan(record)

    def _accept_economy_plan(self, record):
        from io import BytesIO
        from PIL import Image
        import currency_wars_refresh_offer as refresh_reader
        if getattr(self, 'business_needs_review', False):
            raise ValueError('先以当前帧复核跨租期业务归属，再提交独立当前经济预算')
        if not isinstance(record, dict) or not isinstance(record.get('proof'), dict):
            raise ValueError('经济计划须绑定当前请求实际原帧proof')
        source = self.verified_source(record['proof'], 180)
        request, plan = self.state.get('decision_request') or {}, record.get('value')
        actual = self.last_observation
        stage = canonical_stage(actual.get('fields', {}).get('stage'))
        if (not isinstance(plan, dict) or record['proof'].get('snapshot_id') != request.get('snapshot_id')
                or source.get('page') not in economy.PREPARATION_PAGES or plan.get('stage') != stage
                or canonical_stage(source.get('fields', {}).get('stage')) != stage
                or source.get('match_id') != self.active_match_id):
            raise ValueError('经济计划须来自本局、本节点、当前epoch和当前请求的完整备战/商店原帧')
        mode = getattr(self, 'live_mode', None)
        if mode and mode['match_id'] == self.active_match_id and plan.get('mode') != mode['value']:
            raise ValueError('经济模式与本局已确认模式冲突')
        data = Path(request['original_png']).read_bytes()
        if hashlib.sha256(data).hexdigest() != request['snapshot_id']:
            raise ValueError('经济原始PNG身份变化')
        source_semantic = source.get('semantic') or {}
        native_refresh_offer = refresh_reader.expects_offer(source) or refresh_reader.expects_offer(actual)
        supplied_fields = plan.get('fields')
        ignored_refresh_fields = []
        with Image.open(BytesIO(data)) as image:
            image.load()
            if native_refresh_offer and isinstance(supplied_fields, dict):
                # An explicit read in the same current domain may not disagree
                # with the native widget. The other domain is simply absent.
                if 'refresh_offer' in source_semantic:
                    offer = refresh_reader.consume_offer(source_semantic['refresh_offer'], image,
                                                          request['snapshot_id'], source.get('page'))
                    if offer is not None:
                        name, key = ('free_refreshes', 'free_remaining') if offer['mode'] == 'free' else ('refresh_cost', 'paid_cost')
                        supplied = supplied_fields.get(name)
                        if supplied is not None and (not isinstance(supplied, dict)
                                or not economy.valid_field(name, supplied.get('value')) or supplied['value'] != offer[key]):
                            raise ValueError('本请求明确刷新读数与当前控件同域事实冲突：' + name)
                ignored_refresh_fields = [name for name in economy.LEGACY_REFRESH_FIELDS if supplied_fields.get(name) is not None]
                supplied_fields = {key: value for key, value in supplied_fields.items() if key not in economy.LEGACY_REFRESH_FIELDS}
            fields = economy.bind_fields(supplied_fields, image, gold_bounds=GOLD_HUD)
        binding = {'scope': (self.active_match_id, stage, self.epoch()), 'snapshot_id': request['snapshot_id'],
                   'fields': fields, 'plan': plan, 'proof': record['proof'],
                   'native_refresh_offer': native_refresh_offer, 'ignored_legacy_refresh_fields': ignored_refresh_fields}
        self.economy_read_cache = None
        observed = self.economy_observation(actual, binding)
        ledger = self.economy_ledger(stage)
        if ledger.get('requires_current_budget'):
            unknown = sorted(item['origin_run_id'] + ':' + item['request_id']
                             for item in ledger.get('prior_run_unknown', []))
            if (plan.get('prior_run_unknown_requests') != unknown
                    or plan.get('prior_run_budget_policy') != 'current_balance_after_unknown_inputs'
                    or type(plan.get('revision')) is not int or plan['revision'] <= ledger['revision']):
                raise ValueError('跨租期原未定支出仍未知；须列齐请求并用当前余额明确新剩余预算')
        # A proven target slot may be bought even if unrelated slots are
        # unknown. Refresh, experience and completion still require full shop.
        rounds = plan.get('reserve', {}).get('remaining_interest_rounds')
        if rounds is not None:
            proof = plan.get('reserve', {}).get('rounds_evidence', {})
            basis = self.verified_source(proof.get('proof', {}), 180)
            if (proof.get('remaining_interest_rounds') != rounds
                    or proof.get('proof', {}).get('snapshot_id') != request['snapshot_id']
                    or basis.get('preparation_stage') != stage
                    or basis.get('page') not in economy.PREPARATION_PAGES):
                raise ValueError('结息来源须绑定本局、本节点、当前epoch与请求原帧')
            origin = proof.get('source', 'observed_screen')
            if origin == 'supervisor_confirmation':
                # A current supervisor confirmation is an explicit policy
                # source, not invented OCR. Keep its original reference and
                # statement in the unchanged budget/receipt journal.
                if (rounds != 0 or proof.get('reviewer') != 'supervising_agent'
                        or proof.get('request_id') != request.get('request_id')
                        or proof.get('match_id') != self.active_match_id
                        or proof.get('stage') != stage or proof.get('mode') != plan.get('mode')
                        or proof.get('confirmation_source') not in ('user_confirmation', 'reviewed_rule')
                        or any(not isinstance(proof.get(key), str) or not proof[key].strip()
                               for key in ('reference', 'statement'))):
                    raise ValueError('末关确认须为当前请求明确的最后备战、模式、来源引用和确认原文；不能只填节点')
            elif origin == 'observed_screen':
                reading = proof.get('reading')
                row = find_text(basis.get('rows', []), reading, proof.get('bounds'), exact=True) if isinstance(reading, str) else None
                if (not isinstance(reading, str)
                        or not re.search(r'(?:剩余|还有)\s*' + str(rounds) + r'\s*(?:次|个)?(?:结息|利息结算|回合)', reading)
                        or not row or row.get('confidence', 0) < .90):
                    raise ValueError('屏幕结息来源须有对应完整ROI与实际读数；不可编造不存在的控件')
            else:
                raise ValueError('未知结息来源；保留未知，不从节点字符串推导')
        proposed = copy.deepcopy(ledger)
        pending = proposed.get('pending')
        if pending:
            if plan.get('resolve_request_id') != pending.get('request_id'):
                raise ValueError('先按上次真实请求回执和后帧补读经济结果，不能重置待验交易')
            if not pending.get('after_png'):
                self.verify_economy_fence(pending)
                # No economic or other mutating input has followed the original
                # request; this new read-only frame may repair missing evidence.
                self.archive_economy_after(pending, actual, persist=False)
                pending.update(after_shop=observed['shop'], after_shop_complete=observed['shop_complete'])
            if not pending.get('receipt'):
                self.verify_economy_fence(pending)
            old_data = Path(pending['after_png']).read_bytes()
            if hashlib.sha256(old_data).hexdigest() != pending['after_snapshot_id']:
                raise ValueError('待验交易后帧身份变化')
            with Image.open(BytesIO(old_data)) as image:
                image.load()
                archived = {'snapshot_id': pending['after_snapshot_id'], 'page': pending.get('after_page'),
                            'read_contract': pending.get('after_read_contract'), 'semantic': {}}
                if 'after_refresh_offer' in pending:
                    archived['semantic']['refresh_offer'] = pending['after_refresh_offer']
                after = self.read_economy_fields(fields, image, archived,
                    native_required='refresh_offer' in pending['before'].get('values', {}),
                    ignored_legacy_refresh_fields=ignored_refresh_fields)
            if actual['snapshot_id'] == pending['after_snapshot_id'] and 'refresh_offer' in observed['values']:
                saved_offer, current_offer = after['values'].get('refresh_offer'), observed['values']['refresh_offer']
                if saved_offer is not None and current_offer is not None and saved_offer != current_offer:
                    raise ValueError('原交易后帧刷新事实与同帧当前读取冲突，保持pending')
                if current_offer is not None and saved_offer is None:
                    after['values']['refresh_offer'] = copy.deepcopy(current_offer)
                    after['unknown'] = [name for name in after['unknown'] if name not in (*economy.LEGACY_REFRESH_FIELDS, 'refresh_offer')]
                    for name in economy.LEGACY_REFRESH_FIELDS:
                        after['values'].pop(name, None)
                        after['evidence'].pop(name, None)
                    after['evidence']['refresh_offer'] = copy.deepcopy(observed['evidence']['refresh_offer'])
            after.update(shop=pending.get('after_shop', []), shop_complete=pending.get('after_shop_complete', False))
            outcome = economy.classify_effect(pending['kind'], pending['before'], after,
                expected_cost=pending['cost'], target_level=plan.get('experience', {}).get('target_level'), slot=pending.get('slot'))
            if outcome['outcome'] != 'success' and actual['snapshot_id'] != pending['after_snapshot_id']:
                self.verify_economy_fence(pending)
                outcome = economy.classify_effect(pending['kind'], pending['before'], observed,
                    expected_cost=pending['cost'], target_level=plan.get('experience', {}).get('target_level'), slot=pending.get('slot'))
                if outcome['outcome'] != 'unknown':
                    self.archive_economy_after(pending, actual, persist=False)
                    pending.update(after_shop=observed['shop'], after_shop_complete=observed['shop_complete'])
            if outcome['outcome'] == 'unknown':
                raise ValueError('该笔原始后帧仍不能证明实际经济效果；不重发已发布输入')
            self._apply_economy_result(proposed, pending, outcome, acknowledged=True, persist=False)
        details = economy.validate_budget(plan, observed, proposed)
        if plan['budget']['refresh']:
            # Existing role costs help reserve enough for a found card; the
            # actual purchase still requires its new-frame native slot price.
            costs = [self.knowledge.get('roles', {}).get(name, {}).get('base_cost') for name in plan['paid_search']['targets']]
            required_reserve = max(cost if type(cost) is int and 1 <= cost <= 5 else 5 for cost in costs)
            if plan['paid_search']['purchase_reserve'] < required_reserve:
                raise ValueError('付费搜牌留资不足以购买目标；未知费用按现有1–5费槽位上限预留5')
        proposed['revision'], proposed['budget'] = plan['revision'], dict(plan['budget'])
        if proposed.get('requires_current_budget'):
            proposed['requires_current_budget'] = False
            proposed['current_balance_budget_proof'] = copy.deepcopy(record['proof'])
        proposed['policy'] = {key: copy.deepcopy(plan[key]) for key in economy.POLICY_FIELDS}
        self.save_economy_ledger(stage, proposed)
        ledger.clear()
        ledger.update(proposed)
        self.economy_binding = binding
        self.context['economy_plan'] = {'value': plan, 'proof': record['proof'],
                                      'origin': 'supervising_agent', 'scope': binding['scope']}
        self.log({'event': 'economic_budget_verified', 'stage': stage, 'snapshot_id': observed['snapshot_id'],
                  'revision': plan['revision'], 'budget': plan['budget'], 'actual_spent': ledger['spent'], **details})

    def economic_policy(self, actual):
        try:
            observation = self.economy_observation(actual)
            binding = self.economy_binding
            plan, ledger = binding['plan'], self.economy_ledger(observation['stage'])
            targets = {item['name']: item for item in plan['targets']}
            gaps = [slot for slot in observation['shop'] if slot.get('name') in targets
                    and ledger['purchased'].get(slot['name'], 0) < targets[slot['name']]['copies']]
            strategy_observation, facts = self.preparation_inputs(actual)
            strategy_observation['fields']['level'] = observation['values'].get('level')
            guide = strategy_observation.get('semantic', {}).get('guide') or facts.get('guide') or {}
            phase = coaching.guide_phase(guide, strategy_observation, facts.get('guide_proof'))
            permission = {'allowed': phase['reroll_allowed'] and phase['phase'] not in
                          guide.get('operating_rules', {}).get('no_reroll_phases', []), 'phase': phase['phase']}
            status = economy.dependencies(plan, observation, ledger, gaps, shop_complete=observation['shop_complete'],
                                          guide_permission=permission)
            actions = []
            if not ledger['pending'] and status['phase'] == 'purchase' and actual['page'] == 'shop':
                for slot in gaps:
                    target = targets[slot['name']]
                    economy.require_spending('purchase', slot['cost'], plan, observation, ledger, critical=target['critical'])
                    actions.append({'type': 'buy_shop', 'slot': slot['slot'], 'name': slot['name'], 'cost': slot['cost'],
                        'expected_page': 'shop', 'reason': target['reason']})
                    break
            elif not ledger['pending'] and status['phase'] in ('free_refresh', 'paid_search') and actual['page'] == 'shop':
                cost = economy.refresh_cost(observation['values'])
                if status['phase'] == 'free_refresh' and cost == 0 or status['paid_search']['allowed']:
                    economy.require_spending('refresh', cost, plan, observation, ledger)
                    actions.append({'type': 'key', 'args': [economy.REFRESH_KEY], 'guard_texts': ['刷新'],
                                    'expected_page': 'shop', 'reason': status['reason']})
            elif not ledger['pending'] and status['experience_allowed']:
                values = observation['values']
                if values.get('level', 10) < plan['experience']['target_level'] and plan['budget']['experience'] > ledger['spent']['experience']:
                    cost = values.get('xp_cost')
                    if cost is not None and ledger['spent']['experience'] + cost <= plan['budget']['experience']:
                        economy.require_spending('experience', cost, plan, observation, ledger,
                                                 critical=plan['experience'].get('critical') is True)
                        actions.append({'type': 'buy_xp', 'count': 1, 'expected_page': actual['page'],
                                        'reason': plan['experience']['reason']})
            values = observation['values']
            xp_remaining = plan['budget']['experience'] - ledger['spent']['experience']
            experience_resolved = (values.get('level', 0) >= plan['experience']['target_level'] or xp_remaining == 0
                or values.get('xp_cost') is not None and xp_remaining < values['xp_cost'])
            return {'available': True, 'dependencies': status, 'observation': observation,
                    'actions': actions, 'pending': ledger['pending'], 'budget': plan['budget'], 'spent': dict(ledger['spent']),
                    'experience_resolved': experience_resolved, 'guide_permission': permission}
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return {'available': False, 'actions': [], 'reason': str(exc)}

    def require_economic_action(self, action, actual):
        kind = economy.economic_action(action, actual.get('page'))
        if not kind:
            return None
        policy = self.economic_policy(actual)
        if not policy['available']:
            raise ValueError('经济动作缺少当前可核预算：' + policy['reason'])
        observation, plan = policy['observation'], self.economy_binding['plan']
        ledger = self.economy_ledger(observation['stage'])
        missing = set(observation['unknown']) & economy.required_fields(kind, observation['values'])
        if missing:
            raise ValueError('本动作必要经济数字区当前未可靠回读：' + '、'.join(sorted(missing)))
        candidates = policy['actions']
        candidate = next((item for item in candidates if economy.economic_action(item, actual['page']) == kind
            and (kind != 'purchase' or all(item.get(key) == action.get(key) for key in ('slot', 'name', 'cost')))), None)
        if candidate is None:
            raise ValueError('经济依赖或预算拒绝本动作：' + policy['dependencies']['reason'])
        cost = (action['cost'] if kind == 'purchase' else observation['values']['xp_cost'] if kind == 'experience'
                else economy.refresh_cost(observation['values']))
        return {'kind': kind, 'cost': cost, 'observation': observation, 'plan': plan, 'ledger': ledger}

    def apply_economy_result(self, ledger, pending, result, *, acknowledged=False, persist=True):
        with self.c.submission_lock():
            return self._apply_economy_result(ledger, pending, result, acknowledged=acknowledged, persist=persist)

    def _apply_economy_result(self, ledger, pending, result, *, acknowledged=False, persist=True):
        outcome, spent = result['outcome'], result.get('observed_spent')
        if outcome == 'success' or acknowledged and outcome in ('zero', 'partial'):
            self.verify_economy_fence(pending)
            original = await_existing_receipt(self.run, self.c, pending['request_id'], 0)
            delivery = manual_receipt_state(original)
            allowed = (not delivery['unknown_input'] and (
                delivery['state'] == 'completed' and (acknowledged or original['result'].get('ok') is True)
                or acknowledged and delivery['state'] == 'partial'
                or acknowledged and delivery['state'] == 'zero_input' and outcome == 'zero' and spent == 0))
            if not allowed:
                raise ValueError('经济原completed/输入交付不确定；资源差额不能代替原收据，保持pending')
            if type(spent) is not int or not 0 <= spent <= pending['before']['values']['coins']:
                raise ValueError('经济效果的实花无法归属原交易，保持待验')
            ledger['spent'][pending['kind']] += spent
            floor = pending['reserve_coins']
            ledger['critical_spent'] += max(0, min(spent, floor - (pending['before']['values']['coins'] - spent)))
            if pending['kind'] == 'refresh' and spent:
                ledger['paid_refreshes'] += 1
            if outcome == 'success' and pending['kind'] == 'purchase':
                ledger['purchased'][pending['name']] = ledger['purchased'].get(pending['name'], 0) + 1
            ledger['pending'] = None
        if persist:
            self.save_economy_ledger(pending['before']['stage'], ledger)
        self.log({'event': 'economic_effect_verified', 'request_id': pending.get('request_id'),
                  'stage': pending['before']['stage'], 'kind': pending['kind'], **result,
                  'actual_spent': dict(ledger['spent']), 'input_resent': False})

    def execute_economic_action(self, action, actual, request):
        with self.profile_span('economic_action', operation='rules',
                               business_step=economy.economic_action(action, actual.get('page')),
                               snapshot_id=actual.get('snapshot_id')):
            return self._execute_economic_action(action, actual, request)

    def _execute_economic_action(self, action, actual, request):
        if economy.economic_action(action, actual.get('page')) == 'purchase':
            actual = self.ensure_full_observation(actual)
        info = self.require_economic_action(action, actual)
        kind, ledger = info['kind'], info['ledger']
        if kind == 'purchase':
            slot = purchase_slot(actual.get('shop') or {}, action.get('slot'), actual['snapshot_id'])
            old = purchase_slot(request['observation'].get('shop') or {}, action.get('slot'), request['snapshot_id'])
            if (not slot or not old or request.get('match_id') != self.active_match_id
                    or any(slot[key] != old[key] for key in ('slot', 'name', 'cost', 'bounds', 'position'))):
                raise ValueError('购牌仍须同请求原/新帧单槽实名实价一致')
            self.verify_purchase_frame(request, actual)
            tokens = ['click:' + ':'.join(map(str, slot['position'])), 'wait:0.5']
        else:
            tokens = ['key:' + str(economy.XP_KEY if kind == 'experience' else economy.REFRESH_KEY), 'wait:0.7']
        pending = {'kind': kind, 'cost': info['cost'], 'before': info['observation'], 'name': action.get('name'),
                   'slot': action.get('slot'), 'request_id': None, 'publication_attempted': False,
                   'reserve_coins': info['plan']['reserve']['coins'], 'resume_epoch': self.epoch(),
                   'prior_receipt_ids': self.economy_receipt_watermark(),
                   'authorization': {'action': copy.deepcopy(action), 'page': actual['page'],
                       **{key: actual.get(key) for key in ('snapshot_id', 'capture_request_id', 'frame_id')},
                       'budget_revision': ledger['revision'], 'policy': copy.deepcopy(ledger.get('policy')),
                       'spent': copy.deepcopy(ledger['spent'])},
                   'broker_actions': self.c.validate_actions([
                       {'type': parts[0], 'args': parts[1:]} for parts in (token.split(':') for token in tokens)])}
        ledger['pending'], self.economy_inflight = pending, pending
        try:
            after = self.command(tokens, action['reason'], actual['page'], '按同请求后帧核实际资源差额', action=action,
                                 read_scope='full' if kind == 'purchase' else 'economy')
        except Exception:
            if pending['publication_attempted'] is False:
                ledger['pending'] = None
            self.save_economy_ledger(info['observation']['stage'], ledger)
            raise
        finally:
            self.economy_inflight = None
        self.economy_read_cache = None
        self.archive_economy_after(pending, after)
        observed = self.economy_observation(after)
        pending.update(after_shop=observed['shop'], after_shop_complete=observed['shop_complete'])
        outcome = economy.classify_effect(kind, info['observation'], observed, expected_cost=info['cost'],
            target_level=info['plan']['experience']['target_level'], slot=action.get('slot'))
        try:
            self.apply_economy_result(ledger, pending, outcome)
        except ValueError as exc:
            outcome = {'outcome': 'unknown', 'observed_spent': None, 'reason': str(exc)}
            self.apply_economy_result(ledger, pending, outcome)
        # Only explicitly planned useful purchases occur here; the completed
        # initial unwanted-card cleanup remains valid. Dynamic team proof does not.
        self.invalidate_preparation('economy')
        self.record_node_progress(actual, after)
        if outcome['outcome'] != 'success':
            self.state['decision_request'] = None
            self.ask(after, 'economy_result', '本笔经济效果为' + outcome['outcome'] + '，原请求' + str(pending['request_id'])
                     + '；补读原后帧或给明确剩余计划，不重发已发布输入。', choices={'pending': pending, 'outcome': outcome},
                     category='exception', business_step=kind)
            return False
        return True

    @staticmethod
    def economy_complete(policy):
        """The same completion conditions for local execution and ROOT review."""
        return bool(policy['available'] and not policy.get('pending') and not policy.get('actions')
            and policy.get('experience_resolved')
            and not set(policy['observation']['unknown']) & economy.required_fields('review', policy['observation']['values'])
            and policy['dependencies']['experience_allowed'])

    def finish_local_economy(self, observed, policy):
        """Close only an already authorized budget; do not invent a human review."""
        # Share Entry's single publication lock, as _manual_capture does.
        # A later request cannot hide behind the still-valid old frame bytes.
        with self.c.submission_lock():
            if (optional(self.run / 'result.json') or {}).get('id') != observed.get('capture_request_id'):
                return False
            for rid in self.economy_receipt_watermark():
                path = self.run / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
                if optional(path).get('result') is None:
                    return False
            if self.preparation_checklist(observed)['phase'] != 'economy' or not self.economy_complete(policy):
                return False
            scope = (self.active_match_id, canonical_stage(observed.get('fields', {}).get('stage')), self.epoch())
            binding = getattr(self, 'economy_binding', None)
            if (not binding or binding['scope'] != scope or observed is not self.last_observation
                    or policy['observation']['snapshot_id'] != observed['snapshot_id']
                    or getattr(self, 'business_needs_review', False)
                    or manual_state(self.run) or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()):
                return False
            status = self.c.status()
            if not status['ready'] or status['paused'] or status['input_halted'] or not status['game_foreground']:
                return False
            # A current image alone is not an input outcome. Retain its actual
            # terminal receipt, and require the original economic pending to be
            # resolved by the existing per-transaction reconciliation first.
            receipt = await_existing_receipt(self.run, self.c, observed['capture_request_id'], 0)
            result = receipt['result']
            delivery = manual_receipt_state(receipt)
            frame = result.get('observation') or {}
            if (delivery['unknown_input'] or receipt['request'].get('handoff') is not False
                    or result.get('ok') is not True or frame.get('request_id') != observed['capture_request_id']
                    or frame.get('frame_id') != observed.get('frame_id')
                    or frame.get('snapshot_sha256') != observed['snapshot_id']
                    or frame.get('captured_at') != observed.get('captured_at')):
                return False
            payload = self.frame_path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != observed['snapshot_id']:
                raise entry.ObservationUnavailable('经济完成的当前原帧改变，保持待验')
            path = self.records / ('economy-complete-' + observed['snapshot_id'] + '.png')
            if path.exists():
                if path.read_bytes() != payload:
                    raise ValueError('经济完成证据已存在但字节不同；不覆盖')
            else:
                with path.open('xb') as stream:
                    stream.write(payload)
            proof = {'source': 'local_economy_execution', 'snapshot_id': observed['snapshot_id'],
                     'evidence_file': str(path), 'capture_request_id': observed['capture_request_id'],
                     'frame_id': observed['frame_id'], 'resume_epoch': scope[2], 'match_id': scope[0]}
            self.preparation_reviews['economy'] = {'phase': 'economy', 'completed': True,
                'stage': scope[1], 'origin': 'local_economy_execution', 'proof': proof, 'observed_at': now(),
                'budget_revision': binding['plan']['revision'], 'budget_proof': copy.deepcopy(binding['proof']),
                'economic_snapshot_id': observed['snapshot_id'], 'economic_values': copy.deepcopy(policy['observation']['values']),
                'actual_spent': dict(policy['spent']), 'completion_receipt': redact(receipt),
                'findings': '已核当前预算的缺口、免费次数、付费停止条件与经验剩余计划均已解决；布阵和出战仍待独立验收'}
            self.node_progress = getattr(self, 'node_progress', 0) + 1
            self.log({'event': 'preparation_phase_verified', 'phase': 'economy', 'stage': scope[1],
                      'origin': 'local_economy_execution', 'proof': proof})
            return True

    def profile_economy_flow(self, observed, *, flow_id, origin, point, reason, phase,
                             policy=None, policy_observed=None, completed_actions=0, returned=None,
                             completion_attempted=False, completion_accepted=None, error_type=None):
        """Describe an already evaluated path without observing or deciding again."""
        profile = getattr(self, 'profile', None)
        if profile is None or not profile.enabled:
            return None
        try:
            flow_id = flow_id or uuid.uuid4().hex
            stage, epoch = canonical_stage(observed.get('fields', {}).get('stage')), self.epoch()
            context = {'match_id': self.active_match_id, 'stage': stage, 'resume_epoch': epoch}
            binding = getattr(self, 'economy_binding', None)
            ledger_stage = ((policy or {}).get('observation') or {}).get('stage') or stage
            ledgers = getattr(self, 'economy_ledgers', {})
            ledger_key = (self.active_match_id, ledger_stage)
            ledger = ledgers.get(ledger_key, {})
            pending = ledger.get('pending')
            details = None
            if policy is not None:
                dependency = policy.get('dependencies') or {}
                reading = policy.get('observation') or {}
                actions = policy.get('actions') or []
                action = actions[0] if actions else None
                kind = (pending or policy.get('pending') or {}).get('kind')
                if kind is None and action:
                    kind = economy.economic_action(action, (policy_observed or observed).get('page'))
                if kind is None:
                    kind = {'purchase': 'purchase', 'free_refresh': 'refresh', 'paid_search': 'refresh',
                            'experience': 'review' if policy.get('experience_resolved') else 'experience'}.get(
                                dependency.get('phase'))
                required = (economy.required_fields(kind, reading.get('values', {}))
                            if policy.get('available') is True and kind in
                                ('purchase', 'refresh', 'experience', 'review') else None)
                details = {'available': policy.get('available'), 'reason': policy.get('reason'),
                    'snapshot_id': (policy_observed or {}).get('snapshot_id'),
                    'economic_snapshot_id': reading.get('snapshot_id'),
                    'capture_request_id': (policy_observed or {}).get('capture_request_id'),
                    'frame_id': (policy_observed or {}).get('frame_id'),
                    'dependency_phase': dependency.get('phase'), 'dependency_reason': dependency.get('reason'),
                    'required_for': kind if required is not None else None,
                    'required_fields': sorted(required) if required is not None else None,
                    'missing_required_fields': sorted(required & set(reading.get('unknown', [])))
                        if required is not None else None,
                    'shop_complete': reading.get('shop_complete'),
                    'next_action': action.get('type') if action else None,
                    'experience_resolved': policy.get('experience_resolved')}
            profile.record_economy_flow(flow_id=flow_id, context=context,
                point=point, origin=origin, reason=reason, phase=phase,
                page=observed.get('page'), snapshot_id=observed.get('snapshot_id'),
                capture_request_id=observed.get('capture_request_id'), frame_id=observed.get('frame_id'),
                captured_at=observed.get('captured_at'), completed_actions=completed_actions, returned=returned,
                budget={'present': binding is not None, 'scope': binding.get('scope') if binding else None,
                        'scope_matches': binding.get('scope') == (self.active_match_id, stage, epoch) if binding else None,
                        'revision': binding.get('plan', {}).get('revision') if binding else None},
                policy=details, ledger_loaded=ledger_key in ledgers,
                pending=({'stage': ledger_stage, **{key: pending.get(key) for key in
                    ('request_id', 'kind', 'outcome', 'publication_attempted')}} if pending else None),
                completion_attempted=completion_attempted, completion_accepted=completion_accepted,
                completion_predicate=self.economy_complete(policy) if completion_attempted else None,
                completion_guard_detail=None, error_type=error_type)
            return flow_id
        except Exception as exc:
            # Diagnostics after an input must never turn a completed action
            # into a caller-visible failure or authorize its re-entry.
            profile.enabled = False
            with contextlib.suppress(Exception):
                profile._disable(exc)
            return None

    def advance_economy(self, observed, *, origin='direct_call'):
        profile = getattr(self, 'profile', None)
        tracing = profile is not None and profile.enabled
        flow_id, policy, policy_observed, phase = None, None, None, None
        completed_actions = 0
        try:
            phase = self.preparation_checklist(observed)['phase']
            if tracing:
                flow_id = self.profile_economy_flow(observed, flow_id=None, origin=origin,
                    point='entry', reason='entered', phase=phase)
            if phase != 'economy':
                if flow_id:
                    self.profile_economy_flow(observed, flow_id=flow_id, origin=origin, point='exit',
                        reason='phase_not_economy', phase=phase, returned=False)
                return False
            for unused in range(8):
                policy = self.economic_policy(observed)
                policy_observed = observed
                if not policy['available']:
                    if flow_id:
                        self.profile_economy_flow(observed, flow_id=flow_id, origin=origin, point='exit',
                            reason='policy_unavailable', phase=phase, policy=policy, policy_observed=policy_observed,
                            completed_actions=completed_actions, returned=False)
                    return False
                if (not full_observation(observed) and (not policy['actions']
                        or policy['actions'][0].get('type') == 'buy_shop')):
                    observed = self.ensure_full_observation(observed)
                    policy = self.economic_policy(observed)
                    policy_observed = observed
                    if not policy['available']:
                        if flow_id:
                            self.profile_economy_flow(observed, flow_id=flow_id, origin=origin, point='exit',
                                reason='upgraded_policy_unavailable', phase=phase, policy=policy,
                                policy_observed=policy_observed, completed_actions=completed_actions, returned=False)
                        return False
                if not policy['actions']:
                    completed = self.finish_local_economy(observed, policy)
                    if flow_id:
                        self.profile_economy_flow(observed, flow_id=flow_id, origin=origin, point='exit',
                            reason='economy_completed' if completed else 'no_actions', phase=phase,
                            policy=policy, policy_observed=policy_observed, completed_actions=completed_actions,
                            returned=completed, completion_attempted=True, completion_accepted=completed)
                    return completed
                action = policy['actions'][0]
                self.guard_preparation_action(action, observed)
                request = {'match_id': self.active_match_id, 'snapshot_id': observed['snapshot_id'],
                           'observation': observed, 'original_png': str(self.frame_path)}
                if not self.execute_economic_action(action, observed, request):
                    if flow_id:
                        self.profile_economy_flow(getattr(self, 'last_observation', None) or observed, flow_id=flow_id,
                            origin=origin, point='exit', reason='transaction_return', phase=phase,
                            policy=policy, policy_observed=policy_observed,
                            completed_actions=completed_actions, returned=True)
                    return True
                if flow_id:
                    completed_actions += 1
                observed = self.last_observation
            if flow_id:
                self.profile_economy_flow(observed, flow_id=flow_id, origin=origin, point='exit',
                    reason='step_limit', phase=phase, policy=policy, policy_observed=policy_observed,
                    completed_actions=completed_actions, returned=True)
            return True  # A later bounded tick may continue only from its new frame.
        except BaseException as exc:
            if flow_id:
                self.profile_economy_flow(getattr(self, 'last_observation', None) or observed, flow_id=flow_id,
                    origin=origin, point='exit', reason='exception', phase=phase, policy=policy,
                    policy_observed=policy_observed, completed_actions=completed_actions,
                    error_type=type(exc).__name__)
            raise

    def guard_preparation_action(self, action, actual, *, loot_pickup=False, verified_navigation=False):
        if self.pending_reward_capacity():
            raise ValueError('腾位已尝试；先按原收据回读空位/钱/溢出，不再发送输入')
        if action.get('purpose') == 'reward_capacity' or action.get('target_evidence', {}).get('control_id') == 'reward_capacity_sale':
            if self.preparation_checklist(actual)['phase'] != 'rewards':
                raise ValueError('领奖容量例外仅属于当前领奖阶段')
            return self.reward_capacity_action(action, actual)
        if actual.get('page') not in ('preparation', 'shop', 'unit_gear'):
            return
        status = self.preparation_checklist(actual)
        reviews = self.preparation_reviews
        if action.get('type') == 'key' and action.get('args') == [69]:
            raise ValueError('当前备战经验键为F70，拒绝旧E69绕过经济守卫')
        if verified_navigation:
            if status['phase'] != 'startup_guide' or reviews.get('rewards', {}).get('completed') is not True:
                raise ValueError('须先领奖领空，再进入第二创业指南；固定图标不跳过准备顺序')
            return
        if battle_input(actual, action):
            if not status['battle_ready']:
                raise ValueError('出战前准备清单未完成：' + status['next_step'])
            population = re.fullmatch(r'([0-9]+)/([0-9]+)', actual.get('fields', {}).get('deployed') or '')
            if (not population or not valid_population_counts(int(population[1]), int(population[2]))
                    or int(population[1]) != int(population[2])):
                raise ValueError('当前出战鲜帧人口未满/未知；旧验收不能批准变化后的阵容')
            return
        effect = coaching.action_effect(action, actual['page'])
        if effect == 'economy' or action.get('type') == 'buy_shop':
            if not status['economy_allowed']:
                raise ValueError('奖励/创业指南/清库存未核，不执行购买、刷新或升级')
            self.require_economic_action(action, actual)
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
        if action.get('type') == 'key' and action.get('args') not in ([27], [68], [70], [46]):
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

    def preparation_inputs(self, observed):
        facts = {}
        for key, record in self.strategy_reads.items():
            if key not in ('guide', 'guide_tracking') and (not full_observation(observed)
                    or (observed.get('semantic', {}).get(key) or {}).get('status') == 'not_read'
                    or key == 'team' and record.get('snapshot_id') != observed.get('snapshot_id')):
                continue
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
        return phase_observation, facts

    def preparation_policy(self, observed):
        observed = self.ensure_full_observation(observed)
        phase_observation, facts = self.preparation_inputs(observed)
        decision = preparation_decision(phase_observation, facts)
        task_context, observation_scope = self.reviewed_task_context(observed)
        decision['progression_plan'] = progression_plan(self.knowledge, observed,
            guide_candidates=observed.get('semantic', {}).get('guide_candidates'),
            reviewed_task_context=task_context, observation_scope=observation_scope)
        checklist = self.preparation_checklist(observed)
        decision['preparation_checklist'] = checklist
        economic = self.economic_policy(observed)
        decision['economic_execution'] = economic
        decision['economic_actions'] = economic['actions'] if checklist['economy_allowed'] else []
        if checklist['phase'] == 'economy' and not economic['available']:
            decision['needs_user_guidance'].insert(0, economic['reason'] + '；提交当前请求context_update.economy_plan')
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
        decision['recommended_actions'] = (decision['economic_actions'][:1] if checklist['phase'] == 'economy'
                                           else decision['inspection_actions'][:1])
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

    def observe(self, *, scope='full', max_attempts=2):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 2:
            raise ValueError('observation retry count must be bounded to one or two')
        for attempt in range(max_attempts):
            rid = uuid.uuid4().hex
            self.worker_request_ids = getattr(self, 'worker_request_ids', set())
            self.worker_request_ids.add(rid)
            with self.profile_span('observe_roundtrip', operation='tool_roundtrip', request_id=rid) as timing:
                result = entry.request(self.c, 'actions', ['observe'], rid, False, queue_wait=self.profile_queue_wait)
                self.profile_broker_result(result, timing)
            self.state['statistics']['local_observations'] += 1
            try:
                if not result.get('ok'):
                    raise entry.ObservationUnavailable('只读截图没有同请求的新鲜回帧')
                return self.read_frame(result, scope=scope)
            except entry.ObservationUnavailable as exc:
                self.log({'event': 'observation_unavailable', 'request_id': rid,
                          'attempt': attempt + 1, 'reason': str(exc), 'input_resent': False})
                if attempt + 1 == max_attempts or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists():
                    raise

    def click_text(self, observed, label, reason, exact=True, bounds=None):
        current = self.last_observation or {}
        if (any(observed.get(key) != current.get(key)
                for key in ('snapshot_id', 'page', 'capture_request_id', 'frame_id'))
                or canonical_stage(observed.get('fields', {}).get('stage'))
                    != canonical_stage(current.get('fields', {}).get('stage'))):
            raise ValueError('文字入口仍引用旧帧/页面/节点；只回读重定位，未提交')
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

    def ask(self, observed, kind, reason, choices=None, *, category='unclassified', business_step=None):
        observed = self.ensure_full_observation(observed)
        old = self.state.get('decision_request')
        if (old and old['snapshot_id'] == observed['snapshot_id'] and old['resume_epoch'] == self.epoch()
                and full_observation(old.get('observation', {}))
                and (old.get('observation', {}).get('read_contract') or {}).get('version')
                    == (observed.get('read_contract') or {}).get('version')):
            return
        rid = uuid.uuid4().hex
        evidence = self.save_frame(rid, 'strategy')
        # This frame outlives its transport receipt and the runtime directory.
        # Keep the exact PNG for delayed review; JPEG is only a display copy.
        original_png = self.records / (rid + '-strategy-original.png')
        payload = self.frame_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != observed['snapshot_id']:
            raise entry.ObservationUnavailable('战略请求原帧摘要不符')
        with original_png.open('xb') as stream:
            stream.write(payload)
        self.history[observed['snapshot_id']] = {'snapshot_id': observed['snapshot_id'], 'evidence_file': evidence,
            'observed_at': now(), 'page': observed['page'], 'rows': copy.deepcopy(observed['rows']), 'match_id': self.active_match_id,
            'resume_epoch': self.epoch(), 'semantic': copy.deepcopy(observed.get('semantic', {})), 'fields': copy.deepcopy(observed.get('fields', {})),
            'read_contract': copy.deepcopy(observed.get('read_contract')),
            'capture_request_id': observed.get('capture_request_id'), 'frame_id': observed.get('frame_id'),
            'captured_at': observed.get('captured_at'),
            'preparation_stage': canonical_stage(observed.get('fields', {}).get('stage')) or self.last_preparation_stage}
        if len(self.history) > 256:
            self.history.pop(next(iter(self.history)))
        request = {'request_id': rid, 'snapshot_id': observed['snapshot_id'], 'kind': kind,
                   'reason': reason, 'observation': copy.deepcopy(observed), 'context': self.context,
                   'static_knowledge': self.knowledge,
                   'knowledge_boundary': 'static_knowledge仅历史参考；动态交易/库存/任务/站位必须当前局鲜帧proof',
                   'inspection_results': self.inspections, 'choices': choices,
                   'resume_epoch': self.epoch(), 'match_id': self.active_match_id,
                   'evidence_file': evidence, 'original_png': str(original_png), 'created_at': now(),
                   'deadline_at': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                   'allowed_action_types': ['click_text', 'click_point', 'buy_shop', 'buy_xp', 'key', 'drag', 'scroll',
                                            'finish_inspection', 'confirm_match_result', 'finish_preparation_review'],
                   'reply_path': str(self.run / 'decision-reply.json')}
        request['preparation_checklist'] = self.preparation_checklist(observed)
        request['return_reason'] = {'category': category, 'business_step': business_step or request['preparation_checklist']['phase'],
                                    'reason': reason, 'origin': 'native_worker'}
        if kind == 'business_resume':
            request['business_receipt_watermark'] = self.economy_receipt_watermark()
        request['reward_capacity_pending'] = self.pending_reward_capacity()
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
        profile = getattr(self, 'profile', None)
        if profile is not None and profile.enabled:
            self.profile_context()
            profile.record_return(category=category, business_step=request['return_reason']['business_step'],
                                  reason=reason, request_id=rid, snapshot_id=observed['snapshot_id'])
        self.log({'event': 'strategy_request', 'request': request})
        self.publish(control_mode='waiting_decision', phase=kind, reason=reason, decision_request=request)

    def guard_startup_navigation(self, action, request, actual):
        """One current fixed control; a supervisor identity is never native OCR."""
        proof = action.get('target_evidence') or {}
        if (request.get('match_id') != self.active_match_id or request.get('resume_epoch') != self.epoch()
                or request != self.state.get('decision_request')):
            raise ValueError('创业指南导航的当前请求/局/代次已变')
        validate_plan({'request_id': request.get('request_id'), 'snapshot_id': request.get('snapshot_id'),
                       'resume_epoch': self.epoch(), 'actions': [action]}, request, self.epoch())
        if 'source' in proof:
            context = getattr(self, 'manual_step_context', None) or {}
            if (proof.get('source') != 'supervising_agent' or not manual_steps.active(self)
                    or context.get('payload', {}).get('operation') != 'reviewed_plan'
                    or context['payload'].get('reply', {}).get('actions') != [action]):
                raise ValueError('主管图标标注只接受当前同Worker reviewed_plan，不接受普通decide或裸坐标')
            intent = manual_steps.validate_guide_roi(action, request, context['binding'],
                                                    context['payload']['checkpoint_id'])
            if intent != context.get('guide_roi_intent'):
                raise ValueError('主管导航标注与已排队原意图不符')
        # Reuse the existing same-owner immutable frame/receipt verifier. It
        # reads saved bytes only; no new screenshot or OCR is requested here.
        manual_steps._bound_reward_frame(self.run, self.c, request['observation'], request['original_png'])
        manual_steps._bound_reward_frame(self.run, self.c, actual, self.frame_path)
        diagnostic = {}
        if not stable_preparation_icon_target(action, request, actual, self.frame_path, diagnostic):
            self.log({'event': 'startup_navigation_guard_rejected', 'request_id': request['request_id'],
                      'diagnostic': diagnostic, 'input_sent': False})
            raise ValueError('创业指南当前页面/唯一图标/局部暴露或来源未通过，零输入')
        return diagnostic

    def save_startup_navigation(self, value):
        value['record_file'] = str(self.records / ('guide-navigation-' + value['request_id'] + '.json'))
        value.update(pending=value['status'] == 'pending', input_resent=False,
                     automatic_phase_completion=False, expected_title='创业指南')
        self.c.write_json(Path(value['record_file']), value)
        self.c.write_json(self.run / 'startup-navigation.json', value)
        self.startup_navigation_result = copy.deepcopy(value)
        self.state['startup_navigation_result'] = copy.deepcopy(value)

    def check_startup_navigation_attempt(self, action, request):
        path = self.records / ('guide-navigation-' + request['request_id'] + '.json')
        if path.exists():
            raise ValueError('本次创业指南请求已有原尝试；读取原记录，不重发')
        previous = optional(self.run / 'startup-navigation.json') or {}
        visited, sources = set(), []
        while previous and previous.get('match_id') == self.active_match_id:
            rid = previous.get('request_id')
            if (not isinstance(rid, str) or not re.fullmatch(r'[0-9a-f]{32}', rid)
                    or rid in visited or len(visited) >= 128
                    or previous.get('schema') != 'startup-guide-navigation/v1'
                    or previous.get('run_id') != self.owner['run_id']
                    or previous.get('record_file') != str(self.records / ('guide-navigation-' + rid + '.json'))
                    or optional(self.records / ('guide-navigation-' + rid + '.json')) != previous):
                raise ValueError('原导航未决链的归属/归档/边界不符；不绕过pending')
            visited.add(rid)
            source = previous.get('source_frame') or {}
            if any(not isinstance(source.get(key), str) or not source[key]
                   for key in ('snapshot_id', 'capture_request_id', 'frame_id')):
                raise ValueError('原导航未决链缺少原帧身份')
            source_png = Path(previous.get('source_png', ''))
            source_receipt = await_existing_receipt(self.run, self.c, source['capture_request_id'], 0)
            result = source_receipt.get('result') or {}
            frame = result.get('observation') or {}
            if (source_png.resolve().parent != self.records.resolve()
                    or hashlib.sha256(source_png.read_bytes()).hexdigest() != source['snapshot_id']
                    or result.get('ok') is not True or manual_receipt_state(source_receipt)['unknown_input']
                    or frame.get('frame_protocol') != 1
                    or frame.get('request_id') != source_receipt.get('id')
                    or source_receipt.get('id') != source['capture_request_id']
                    or frame.get('frame_id') != source['frame_id']
                    or frame.get('snapshot_sha256') != source['snapshot_id']):
                raise ValueError('原导航未决链的不可变源帧/原回执不符')
            sources.append(source)
            if previous.get('status') == 'pending':
                # This also refuses missing/unknown original delivery. Never
                # turn an unfinished prepublication attempt into a retry.
                self.startup_navigation_delivery(previous)
                if (action.get('target_evidence', {}).get('source') != 'supervising_agent'
                        or any(request.get('observation', {}).get(key) == old[key]
                               for old in sources for key in ('capture_request_id', 'frame_id'))):
                    raise ValueError('原导航后继未知；只读/当前ManualPhase复核，不能自动再点原图标')
                return rid
            if previous.get('status') == 'observed' and previous.get('outcome') == 'expected_title_observed':
                return None  # Current title evidence, not completion of startup_guide.
            if (previous.get('status') != 'refused' or previous.get('outcome') != 'zero_input'
                    or previous.get('publication_attempted') is not False):
                raise ValueError('原导航尝试终态不明；保留未决，不再点击')
            # A refused successor did not discharge the older obligation.
            # Validate its zero-input receipt when an Entry ID was allocated.
            input_id = previous.get('input_request_id')
            if input_id is not None:
                refused = await_existing_receipt(self.run, self.c, input_id, 0)
                state = manual_receipt_state(refused)
                if state['state'] != 'zero_input' or state['unknown_input']:
                    raise ValueError('导航拒绝的原交付仍未确定；不能跳过未决链')
            prior = previous.get('prior_unknown_navigation_request_id')
            if prior is None:
                return None
            if not isinstance(prior, str) or not re.fullmatch(r'[0-9a-f]{32}', prior):
                raise ValueError('原导航未决链接无效')
            previous = optional(self.records / ('guide-navigation-' + prior + '.json'))
            if not previous or previous.get('request_id') != prior or previous.get('match_id') != self.active_match_id:
                raise ValueError('原导航未决归档缺失或跨局；不能清除主管要求')
        return None

    def retain_startup_frame(self, request_id, label, observed):
        # save_frame is a display JPEG. Result evidence needs these exact PNG
        # bytes before read_frame releases its preceding transport frame.
        data = self.frame_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != observed.get('snapshot_id'):
            raise entry.ObservationUnavailable('导航证据帧字节已变，保持未知')
        path = self.records / (request_id + '-guide-' + label + '.png')
        with path.open('xb') as stream:
            stream.write(data)
        return {**{key: observed.get(key) for key in
                   ('snapshot_id', 'capture_request_id', 'frame_id', 'captured_at', 'page')}, 'png': str(path)}

    def begin_startup_navigation(self, action, request, actual):
        prior = self.check_startup_navigation_attempt(action, request)
        value = dict(schema='startup-guide-navigation/v1', run_id=self.owner['run_id'],
            match_id=self.active_match_id, stage=canonical_stage(actual.get('fields', {}).get('stage')),
            resume_epoch=self.epoch(), request_id=request['request_id'], deadline_at=request['deadline_at'],
            source=action['target_evidence'].get('source', 'native_visual_guard'),
            source_frame={key: request['observation'].get(key) for key in
                          ('snapshot_id', 'capture_request_id', 'frame_id')},
            source_png=request['original_png'], target_evidence=copy.deepcopy(action['target_evidence']),
            manual_step_id=(getattr(self, 'manual_step_context', None) or {}).get('step_id'),
            prior_unknown_navigation_request_id=prior, input_request_id=None,
            publication_attempted=False, status='pending', outcome='unknown',
            verification_reads=0, frames=[], created_at=now())
        value['validated_before'] = self.retain_startup_frame(request['request_id'], 'before', actual)
        self.save_startup_navigation(value)
        self.startup_navigation_inflight = value
        return value

    def startup_navigation_delivery(self, value):
        rid = value.get('input_request_id')
        if not rid or value.get('publication_attempted') is not True:
            raise ValueError('导航尚无已发布原输入；不能用后图推定交付')
        receipt = await_existing_receipt(self.run, self.c, rid, 0)
        request, result = receipt.get('request') or {}, receipt.get('result') or {}
        if (request.get('actions') != value.get('broker_actions') or request.get('kind') != 'actions'
                or request.get('handoff') is not False or result.get('ok') is not True
                or manual_receipt_state(receipt)['state'] != 'completed'
                or [item for item in request.get('actions', []) if item.get('type') not in ('wait', 'observe')]
                    != [{'type': 'click', 'args': list(PREPARATION_GUIDE_POINT)}]):
            raise ValueError('导航原单点击交付未核；先对账原ID，不重发或用新图覆盖')
        return receipt

    def observe_startup_navigation(self, value):
        """At most two persisted read-only attempts, including missing-frame fallback."""
        self.startup_navigation_delivery(value)
        if value['verification_reads'] >= 2:
            raise ValueError('导航两次只读预算已用；保留未知，由当前ManualPhase复核')
        value['verification_reads'] += 1
        self.save_startup_navigation(value)  # Spend before waiting/capture, even on failure.
        end = time.monotonic() + (.25 if value['verification_reads'] == 1 else .5)
        with self.profile_span('startup_navigation_wait', operation='rules', business_step='startup_guide'):
            while True:
                if (self.epoch() != value['resume_epoch'] or self.active_match_id != value['match_id']
                        or self.manual_input_blocked() or (self.run / 'runner-stop').exists()
                        or (self.run / 'broker-stop').exists() or time.monotonic() >= self.deadline
                        or datetime.now(timezone.utc) >= datetime.fromisoformat(value['deadline_at'])):
                    raise ValueError('导航核效的局/代次/期限/停止条件已变；不再观察或输入')
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(.05, remaining))
        return self.observe(max_attempts=1)

    def finish_startup_navigation(self, value):
        self.startup_navigation_delivery(value)
        while True:
            if (self.epoch() != value['resume_epoch'] or self.active_match_id != value['match_id']
                    or self.manual_input_blocked() or (self.run / 'runner-stop').exists()
                    or (self.run / 'broker-stop').exists() or time.monotonic() >= self.deadline
                    or datetime.now(timezone.utc) >= datetime.fromisoformat(value['deadline_at'])):
                raise ValueError('导航后效来源已跨局/代次/期限或停止；保留未知，不继承新页面')
            observed = self.last_observation or {}
            manual_steps._bound_reward_frame(self.run, self.c, observed, self.frame_path)
            if observed.get('capture_request_id') != value['input_request_id']:
                manual_steps._bound_reward_frame(self.run, self.c, observed, self.frame_path, read_only=True)
            stage = canonical_stage(observed.get('fields', {}).get('stage'))
            title = find_text(observed.get('rows', []), '创业指南', exact=True)
            title = title if title and title.get('confidence', 0) >= .90 else None
            # Existing startup review consumes this exact native title. Page
            # 'guide' means a different guide-detail reader, so never rewrite
            # observation.page or claim a complete panel from that enum.
            after = self.retain_startup_frame(value['request_id'], 'result-' + str(len(value['frames'])), observed)
            after.update(stage=stage, expected_title_row=copy.deepcopy(title))
            value['frames'].append(after)
            changed = stage is not None and stage != value['stage']
            if title and not changed:
                value.update(status='observed', outcome='expected_title_observed',
                             page_result='requires_current_startup_review')
                self.save_startup_navigation(value)
                return
            if changed or observed.get('page') not in ('preparation', 'unknown', 'guide', 'lobby'):
                value['reason'] = 'successor_page_or_stage_changed'
                self.save_startup_navigation(value)
                return
            if value['verification_reads'] >= 2:
                value['reason'] = 'expected_title_unknown_after_bounded_reads'
                self.save_startup_navigation(value)
                return
            self.save_startup_navigation(value)
            self.observe_startup_navigation(value)

    def execute_plan(self, reply):
        actions = reply.get('actions', []) if isinstance(reply, dict) else []
        if any(isinstance(action, dict) and action.get('target_evidence', {}).get('control_id')
               == PREPARATION_GUIDE_CONTROL for action in actions):
            request = self.state.get('decision_request') or {}
            validate_plan(reply, request, self.epoch())
            self.check_startup_navigation_attempt(actions[0], request)
            if manual_steps.unknown_receipts(self.run, self.c):
                raise ValueError('原输入交付未知；先对账，不用导航新观察替换原结果')
            with self.observation_input_lease():
                return self._execute_plan(reply)
        return self._execute_plan(reply)

    def _execute_plan(self, reply):
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
        guide_navigation_verified = False
        if (len(reply['actions']) == 1 and reply['actions'][0].get('target_evidence', {}).get('control_id')
                == PREPARATION_GUIDE_CONTROL):
            self.guard_startup_navigation(reply['actions'][0], request, actual)
            guide_navigation_verified = True
        lobby_navigation_verified = False
        if (original.get('page') == 'lobby'
                and any(action.get('type') == 'click_text'
                        and (clean(action.get('text', '')) == '开始「货币战争」'
                             or action.get('exact') is False and clean(action.get('text', ''))
                             and clean(action['text']) in '开始「货币战争」')
                        for action in reply['actions'])):
            lobby_navigation_verified = stable_lobby_entry_navigation(reply, request, actual, self.frame_path)
            if not lobby_navigation_verified:
                raise ValueError('大厅开始按钮的原生锚点、稳定内容或来源已变，拒绝旧计划')
            self.log({'event': 'local_navigation_guard_matched', 'request_id': request['request_id'],
                      'control_id': 'lobby_start', 'snapshot_id': actual['snapshot_id'], 'input_sent': False})
        local_navigation = any(navigation_target(action, request) is not None for action in reply['actions'])
        navigation_verified = False
        if local_navigation:
            navigation_verified = stable_semantic_plan(reply, request, actual, self.frame_path)
            if not navigation_verified:
                raise ValueError('导航目标/锚点/页面语义或局部暴露已变或未知，拒绝旧计划')
            # Always validate these controls, including a small global delta:
            # moving a small button must not bypass the local target contract.
            self.log({'event': 'local_navigation_guard_matched', 'request_id': request['request_id'],
                      'control_id': navigation_target(reply['actions'][0], request),
                      'snapshot_id': actual['snapshot_id'], 'input_sent': False})
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
                    or not stable_initial_free_lineup_navigation(reply, request, actual, self.frame_path)):
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
                    or not stable_native_loot_pickup(reply, request, actual, self.frame_path)):
                raise ValueError('战利品须为本局原生备战的单个固定实圈点击')
            loot_key = (self.active_match_id, actual['fields']['stage'], reply['actions'][0]['target_evidence']['control_id'])
            if loot_key in self.loot_pickup_attempted:
                raise ValueError('本局本节点该固定战利品已尝试，不重发')
        if request.get('kind') != 'business_resume' and hash_distance(actual['fingerprint'], original['fingerprint']) > .10:
            if not (navigation_verified or stable_world_menu_navigation(reply, request, actual)
                    or stable_phone_guide_navigation(reply, request, actual)
                    or stable_peace_guide_tab_navigation(reply, request, actual)
                    or stable_advantages_navigation(reply, request, actual)
                    or stable_inspection_completion(reply, request, actual)
                    or lobby_navigation_verified
                    or stable_standard_entry_navigation(reply, request, actual, self.frame_path)
                    or stable_plane_intro_navigation(reply, request, actual, self.frame_path)
                    or stable_semantic_plan(reply, request, actual, self.frame_path)
                    or guide_navigation_verified):
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
            if self.manual_input_blocked() or self.epoch() != request['resume_epoch']:
                raise RuntimeError('手动接管使未执行计划失效')
            actual = self.last_observation
            kind = action['type']
            if kind == 'finish_preparation_review':
                if (not any(key in reply.get('context_update', {}) for key in ('preparation_review', 'guide_reference', 'reward_capacity', 'economy_plan', 'business_resume'))
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
                if hasattr(self, 'business'):
                    self.business['status'] = 'completed'
                    self.business['settlement'] = {'result': copy.deepcopy(result), 'origin_run_id': self.owner['run_id'],
                        'request_id': request['request_id'], 'snapshot_id': actual['snapshot_id'],
                        'evidence_file': request['evidence_file'], 'confirmed_at': now()}
                    self.save_business()
                self.panel_index, self.panel_state = 0, 'enter'
                self.inspections = {}
                self.log({'event': 'match_result_verified', 'result': result})
                continue
            if actual['page'] != action['expected_page']:
                raise ValueError('计划前置页面变化；后续动作停止')
            guide_navigation = (kind == 'click_point'
                and action.get('target_evidence', {}).get('control_id') == PREPARATION_GUIDE_CONTROL)
            if guide_navigation:
                self.guard_startup_navigation(action, request, actual)
            capacity_review = self.guard_preparation_action(action, actual, loot_pickup=loot_pickup,
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
                if kind == 'key' and action.get('args') not in ([27], [68], [70]):
                    raise ValueError('商店键盘不作为未经单槽核验的购买入口')
            if economy.economic_action(action, actual['page']):
                self.require_strategy_context(actual)
                if not self.execute_economic_action(action, actual, request):
                    return
                continue
            if kind == 'click_text':
                plan = self.preparation_policy(actual)
                if any(candidate.get('text') == action['text'] for candidate in plan['inspection_actions']):
                    semantic = actual.get('semantic', {})
                    inspection_unit = (semantic.get('unit_preview') or {}).get('name') or (semantic.get('gear') or {}).get('unit_name')
                    self.inspection_attempted.add((self.active_match_id,
                        canonical_stage(actual.get('fields', {}).get('stage')) or self.last_preparation_stage, action['text'], inspection_unit))
                self.check_reroll(action, actual)
                self.click_text(actual, action['text'], action['reason'], action.get('exact', True), action.get('bounds'))
            elif kind in ('buy_shop', 'buy_xp'):
                raise ValueError('购买/经验只允许在已核备战或商店经济路径执行')
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
                if action.get('purpose') == 'reward_capacity':
                    self.begin_reward_capacity(capacity_review, action, actual)
                if guide_navigation:
                    navigation = self.begin_startup_navigation(action, request, actual)
                    try:
                        self.command([command + ':' + ':'.join(map(str, values)), 'wait:0.7'],
                                     action['reason'], actual['page'], '创业指南', action=action)
                        self.finish_startup_navigation(navigation)
                    except Exception as error:
                        navigation['error'] = str(error)
                        if not navigation['publication_attempted']:
                            navigation.update(status='refused', outcome='zero_input')
                        self.save_startup_navigation(navigation)
                        raise
                    finally:
                        self.startup_navigation_inflight = None
                    self.invalidate_preparation('navigation')
                    self.log({'event': 'startup_navigation_result', 'request_id': request['request_id'],
                              'result': copy.deepcopy(navigation), 'input_resent': False})
                    self.publish(control_mode='auto', decision_request=None, reason=None)
                    self.ask(self.last_observation, 'preparation_strategy'
                        if self.last_observation['page'] in ('preparation', 'shop') else 'unknown_page',
                        '已提交一次第二图标导航；当前实读创业指南标题后仍须核章节/奖励。'
                        if navigation['outcome'] == 'expected_title_observed'
                        else '原导航后继仍未知；原输入不重发，按当前ManualPhase/新鲜帧复核。',
                        choices={'startup_navigation': copy.deepcopy(navigation)},
                        category='exception', business_step='startup_guide')
                    return
                self.command([command + ':' + ':'.join(map(str, values)), 'wait:0.7'], action['reason'], actual['page'], action.get('expected_change'), action=action)
                if action.get('purpose') == 'reward_capacity':
                    self.preparation_reviews.pop('rewards', None)
                    self.invalidate_preparation('inventory')
                    # Native overflow has no reliable reader yet. Ask one
                    # current proof of capacity/money through the same worker;
                    # never repeat the drag or continue an economic batch.
                    self.state['decision_request'] = None
                    self.ask(self.last_observation, 'preparation_strategy',
                             '单次腾位已提交；按reward_capacity_pending原收据复核空槽/钱/独立溢出，随后立即领奖')
                    return
                if loot_pickup and (self.last_observation.get('page') != 'preparation'
                                    or self.last_observation.get('fields', {}).get('stage') != loot_key[1]):
                    raise ValueError('战利品点击后出现模态或页面/节点变化；停止，不重发')
                if loot_pickup:
                    if not native_loot_circle_disappeared(loot_key[2], self.frame_path,
                                                        self.last_observation.get('snapshot_id')):
                        raise ValueError('战利品点击后未核实该圈消失；停止，不重发')
                    self.node_progress = getattr(self, 'node_progress', 0) + 1
                    self.log({'event': 'verified_loot_pickup', 'match_id': self.active_match_id,
                              'stage': loot_key[1], 'control_id': loot_key[2],
                              'snapshot_id': self.last_observation['snapshot_id'], 'retry_allowed': False})
            effect = coaching.action_effect(action, actual['page'])
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
            if hasattr(self, 'business'):
                if self.business['status'] != 'completed':
                    self.business['status'] = 'unresolved'
                self.save_business()
            self.active_match_id = uuid.uuid4().hex
            if hasattr(self, 'business'):
                self.initialize_business()
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
        if proof.get('control_id') == 'reward_capacity_sale':
            self.reward_capacity_action(action, actual, pixels=True)
            return
        if proof.get('control_id') == PREPARATION_GUIDE_CONTROL:
            self.guard_startup_navigation(action, request, actual)
            return
        if proof.get('control_id') in tuple(_NATIVE_LOOT_PICKUP_PROFILES):
            if (request.get('match_id') != self.active_match_id
                    or not stable_native_loot_pickup({'snapshot_id': request['snapshot_id'], 'actions': [action]},
                        request, actual, self.frame_path)):
                raise ValueError('固定战利品圈的完整轮廓/原生备战守卫未通过')
            return
        if proof.get('control_id') in tuple(_FREE_LINEUP_PROFILES):
            if (request.get('match_id') != self.active_match_id
                    or not stable_initial_free_lineup_navigation(
                        {'snapshot_id': request['snapshot_id'], 'actions': [action]}, request, actual, self.frame_path)):
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
        if actual['page'] == 'environment':
            # The confirmation/page/other cards are preconditions even when
            # the selected card alone happens to be pixel-identical.
            if not stable_semantic_target(action, request, actual, self.frame_path):
                raise ValueError('环境卡完整语义、结构、确认外观或来源未核实；不发布输入')
            return
        current_png = self.frame_path
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
                if not stable_environment_card_animation(action, request, actual, self.frame_path):
                    diagnostic = {}
                    if (not stable_supply_card_animation(action, request, actual, self.frame_path, diagnostic)
                            and not stable_semantic_target(action, request, actual, self.frame_path)):
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
                            for label, path in (('original', reference), ('current', self.frame_path)):
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
        if not full_observation(source):
            raise ValueError('context观察出处须完整读取，窄scope不能补全场策略事实')
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(source['observed_at'])).total_seconds()
        if not 0 <= age <= lifetime:
            raise ValueError('context出处超过有效期限')
        return source

    def update_context(self, updates):
        if not isinstance(updates, dict) or any(key not in self.context for key in updates):
            raise ValueError('未知战略context字段')
        for key, record in updates.items():
            if key == 'business_resume':
                self.review_business_resume(record)
                continue
            if key == 'unknown_fields':
                continue
            if key == 'preparation_review':
                self.review_preparation(record)
                continue
            if key == 'reward_capacity':
                self.review_reward_capacity(record)
                continue
            if key == 'economy_plan':
                self.accept_economy_plan(record)
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
        observed = self.economy_observation(actual)
        if 'coins' not in observed['values']:
            raise ValueError('新鲜金币HUD无法可信读取，停止购买')
        if not self.preparation_checklist(actual)['economy_allowed']:
            raise ValueError('经济动作前须按顺序核领奖领空→第二入口创业指南→清库存；当前未完成')
        self.log({'event': 'live_coins_roi', 'value': observed['values']['coins'],
                  **observed['evidence']['coins'], 'snapshot_id': actual['snapshot_id']})

    def verify_purchase_frame(self, request, actual):
        from io import BytesIO
        from PIL import Image
        for path, snapshot in ((request['original_png'], request['snapshot_id']),
                               (self.frame_path, actual['snapshot_id'])):
            data = Path(path).read_bytes()
            if hashlib.sha256(data).hexdigest() != snapshot:
                raise ValueError('购买原/鲜PNG字节身份不符，未发布输入')
            with Image.open(BytesIO(data)) as image:
                if image.format != 'PNG' or image.size != (1920, 1080):
                    raise ValueError('购买须完整1920×1080原生PNG')

    def require_xp_cost(self, actual):
        self.require_strategy_context(actual)
        return self.require_economic_action({'type': 'buy_xp', 'count': 1}, actual)

    def check_reroll(self, action, actual):
        if actual.get('page') in economy.PREPARATION_PAGES and action.get('type') == 'key' and action.get('args') == [69]:
            raise ValueError('经验仅F70；旧E69不发布')
        if economy.economic_action(action, actual.get('page')):
            self.require_strategy_context(actual)
            return self.require_economic_action(action, actual)

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
        if (request['kind'] == 'business_resume' and not (self.run / 'decision-reply.json').exists()
                and time.monotonic() >= getattr(self, 'business_refresh_at', 0.)):
            self.business_refresh_at = time.monotonic() + 3
            observed = self.observe()
            if (observed['page'] != request['observation']['page']
                    or canonical_stage(observed.get('fields', {}).get('stage')) not in
                        (None, canonical_stage(request['observation'].get('fields', {}).get('stage')))):
                with file_lock(self.run, 'decision-submit.lock'):
                    if (manual_state(self.run) or self.epoch() != request['resume_epoch']
                            or (self.run / 'runner-stop').exists() or (self.run / 'broker-stop').exists()):
                        return False
                    stale = optional(self.run / 'decision-reply.json')
                    if stale:
                        self.log({'event': 'business_reply_retired', 'reply': redact(stale), 'input_sent': False})
                        (self.run / 'decision-reply.json').unlink()
                    self.publish(decision_request=None, control_mode='auto', reason='业务复核页面已变，废弃旧请求后重读')
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
                with self.profile_span('execute_plan', operation='decision', request_id=request['request_id']):
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
        try:
            if self.consume_manual_stage_bridge(observed):
                observed = self.last_observation
        except manual_stage.StageObservationDeferred as exc:
            bridge = optional(self.run / manual_stage.FILE) or {}
            identity = (self.epoch(), bridge.get('bridge_id'))
            attempts = getattr(self, 'manual_stage_observe_attempts', {})
            attempts[identity] = attempts.get(identity, 0) + 1
            self.manual_stage_observe_attempts = attempts
            self.log({'event': 'manual_stage_observation_deferred', 'attempt': attempts[identity],
                      'reason': str(exc), 'input_sent': False, 'takeover_required': False})
            if attempts[identity] <= 2:
                self.publish(reason='节点桥当前HUD尚未稳定；只补当前观察，不重新接管')
            else:
                self.ask(observed, 'manual_stage_review', '两次补观察后仍未核当前节点；保留原桥/待验，核当前HUD。',
                         category='capability', business_step='manual_stage')
            return
        if getattr(self, 'business_needs_review', False):
            if observed.get('page') not in BUSINESS_REVIEW_PAGES:
                self.publish(phase='续接只读等待可核页面', decision_request=None,
                             reason='当前战斗/未知过渡页不发输入，沿原硬期限等待新帧')
                time.sleep(min(2, max(0, self.deadline - time.monotonic())))
                return
            self.ask(observed, 'business_resume',
                '新有界租期尚未证明同一局。实读当前局面并说明原局连续性；原请求不重发，'
                '原未知结果继续未知，需逐阶段当前复核与当前余额新预算。',
                choices={'checkpoint': str(self.business_path), 'previous_run_id': self.business_previous_run,
                         'previous_chat_id': self.business_previous_chat, 'current_chat_id': self.owner['chat_id'],
                         'unresolved_requests': self.business_unknown, 'previous_status': self.business['status'],
                         'previous_observation_reference': self.business_previous_observation})
            return
        pending = self.pending_reward_step(observed)
        if pending:
            try:
                delivery = manual_receipt_state(await_existing_receipt(self.run, self.c, pending['request_id'], 0))
                known = not delivery['unknown_input'] and delivery['state'] == 'completed'
            except (ValueError, RuntimeError, OSError, TypeError):
                known = False
            if not known:
                self.ask(observed, 'reward_result', '原领奖输入交付仍未确认，不能先关闭模态或发送选择。',
                         choices={'pending': pending}, category='exception', business_step=pending['kind'])
                return
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
                self.advance_rewards(observed)
                return
            if shop_key is not None:
                self.shop_stages.add(shop_key)
            if checklist['phase'] == 'economy' and self.advance_economy(observed, origin='tick_shop'):
                return
            observed = self.last_observation or observed
            self.ask(observed, 'shop_strategy',
                     '当前先完成：' + checklist['next_step'] + '；第二入口创业指南优先，动态复核须当前帧proof。'
                     if not checklist['economy_allowed'] else
                     '先当前缺口、免费刷新与付费搜牌停止条件，再分人口经验；当前请求可提交economy_plan，一份预算逐笔新帧回验。'
                     if (observed.get('shop') or {}).get('ok') is True
                     else '商店仍有未知槽位/推荐标记；独立确认实名、实价和完整槽框的目标可单次购买，未知目标继续等待检查。')
        elif page == 'preparation':
            if self.preparation_checklist(observed)['phase'] == 'rewards':
                self.advance_rewards(observed)
                return
            policy = self.preparation_policy(observed)
            checklist = policy['preparation_checklist']
            if checklist['phase'] == 'economy' and self.advance_economy(observed, origin='tick_preparation'):
                return
            observed = self.last_observation or observed
            if not checklist['economy_allowed']:
                self.ask(observed, 'preparation_strategy',
                         '按顺序处理当前待办：' + checklist['next_step'] + '。领奖后重新扫全场；第二入口创业指南读当前目标并领奖；清库存后再统一经济。'
                         '可用当前帧proof提交supervising_agent复核，未知项回传，缓存不代替进度。')
            elif checklist['phase'] != 'economy' and policy['inspection_actions']:
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
            except entry.ObservationUnavailable as exc:
                self.log({'event': 'passive_observe_unavailable', 'reason': str(exc),
                          'input_sent': False, 'input_resent': False})
            except (entry.SubmissionQueueTimeout, entry.SubmissionDeadlineExpired) as exc:
                self.log({'event': 'passive_observe_deferred', 'reason': str(exc),
                          'request_published': False, 'input_sent': False})
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
                                 reason=(manual or {}).get('reason') or status.get('reason'))
                    # ROOT's explicit bounded mailbox uses THIS Worker. No
                    # periodic passive capture competes with a manual phase.
                    # Pause/stop still precede input; inspect remains read-only.
                    if manual:
                        manual_steps.process(self)
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
                try:
                    observed = self.observe()
                except entry.ObservationUnavailable as exc:
                    self.pause_internal('有界只读观察未取得完整帧：' + str(exc))
                    last_capture = time.monotonic()
                    continue
                last_capture = time.monotonic()
                try:
                    with self.profile_span('tick', operation='decision', snapshot_id=observed.get('snapshot_id')):
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
        # Failure retains the standard runtime: archive success is required
        # before children are declared complete and scratch can be removed.
        if hasattr(self, 'business'):
            archive_business_run({**self.state, 'journal_file': str(self.records / 'journal.jsonl')}, self.c)
        final_mode = self.state['control_mode'] if self.state['control_mode'] in ('completed', 'failed') else 'stopped'
        self.broker_activity_at = -float('inf')  # Preserve all final receipts before the runtime is removed.
        self.publish(control_mode=final_mode, exit_evidence=evidence, reason=self.state.get('reason'),
                     cleanup={'directory': str(self.run), 'removed': False, 'pending_finally': True})
        self.log({'event': 'owned_shutdown', 'exit_evidence': evidence})
        # A successful receipt copy alone is insufficient: the original broker
        # creation identity/final business state must survive runtime disposal.
        artifacts.protect_children(self.run, self.children, root=self.run.parent, complete=True)


def worker_cli(args):
    with activity(PROJECT, 'runner'):
        reviewed_runtime_sources(args.chat_id)
        return _worker_cli(args)


def _worker_cli(args):
    inherited = getattr(args, 'runtime_location_json', None)
    args.runtime_location = input_bridge.runtime_location(entry.PINNED,
        inherited=json.loads(inherited) if inherited is not None else None)
    runtime_root = Path(args.runtime_location['runtime_root'])
    control = entry.backend()
    if not 60 <= args.max_seconds <= 7200 or not 1 <= args.max_matches <= 20:
        raise ValueError('worker仍须遵守60–7200秒及1–20局硬上限')
    if args.max_matches > 1 and not args.continue_matches:
        raise ValueError('worker多局仍须明确continue-matches')
    discovered = optional(CURRENT)
    continuation = getattr(args, 'business_resume_json', None)
    if discovered:
        if not continuation:
            raise RuntimeError('已有业务运行记录；新租期须由公开start核旧双进程退出后承接')
        contract = json.loads(continuation)
        if contract.get('target_chat_id') != args.chat_id:
            raise ValueError('新worker业务合同不属于当前授权会话')
        unused, business = load_business(contract['checkpoint'], contract.get('previous_chat_id'))
        previous = business['leases'][-1]
        if (contract.get('revision') != business['revision'] or contract.get('previous_run_id') != previous['run_id']
                or previous['run_id'] != discovered.get('run_id')):
            raise ValueError('新worker业务启动CAS不符')
        old_lease_exit(previous, control)
        archive_business_run(previous, control)  # Before scratch_directory may sweep the old root.
    elif continuation:
        raise ValueError('业务启动缺少当前发现记录，不采用孤立恢复合同')
    artifact_chat = os.environ.get('CODEX_THREAD_ID', 'unbound')
    purpose = 'currency-wars-runner-' + artifact_chat[:8].lower()
    worker, run = None, None
    try:
        with artifacts.scratch_directory(purpose, root=runtime_root) as run:
            marker = artifacts.read_marker(run, root=runtime_root)
            worker = Worker(args, run, control, marker)
            try:
                worker.run_loop()
            except Exception as exc:
                worker.publish(control_mode='failed', reason=str(exc))
                worker.log({'event': 'worker_failure', 'error': str(exc)})
            finally:
                try:
                    worker.shutdown()
                finally:
                    worker.finish_profile()
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
    if args.command in ('manual-stage', 'manual-step'):
        reviewed_runtime_sources(args.chat_id)
    run, owner, binding, control = load(args.run_dir, args.chat_id, args.run_token, emergency=emergency)
    if args.command == 'status':
        return envelope(current_state(run, owner, control), command_id=rid)
    if args.command == 'manual-stage':
        item = manual_stage.create(run, owner, control, args.manual_id)
        return {'ok': True, 'bridge_id': item['bridge_id'], 'from_stage': item['from_stage'],
                'to_stage': item['to_stage'], 'blocked_ids': item['blocked_ids'],
                'scope_only': True, 'completed_phases': [], 'all_rewards_cleared': None,
                'input_resent': False, 'input_authorized': False}
    if args.command == 'manual-step':
        reply = entry.read_json(Path(args.reply_file).absolute(), limit=100_000) if args.reply_file else None
        return manual_steps.submit(run, owner, control, manual_id=args.manual_id,
            step_id=args.request_id, operation=args.operation, checkpoint_id=args.checkpoint_id,
            reply=reply)
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
                'receipt_states': item['receipt_states'], 'needs_fresh_revalidation': True,
                'input_resent': False, 'before': item['before']['evidence_file'],
                'after': (item.get('after') or {}).get('evidence_file'),
                'observation_error': item.get('observation_error')}
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
                projection_error = None
                try:
                    state = retain_stop_business(state, owner)
                except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                    # Optional persistence must not interrupt the authenticated
                    # stop/exit probes. Do not overwrite the rejected source.
                    projection_error = exc
                broker = state['broker']['broker_state']
                retained_identity = state.get('broker_identity')
                if retained_identity:
                    if (type(retained_identity.get('pid')) is not int or retained_identity['pid'] <= 0
                            or not str(retained_identity.get('creation_id')).isdecimal()
                            or int(retained_identity['creation_id']) <= 0):
                        raise ValueError('停止时原broker缺少有效PID及创建身份')
                    observed_pid = state['broker'].get('broker_pid')
                    observed_creation = state['broker'].get('broker_creation_time')
                    if observed_pid is not None and (observed_pid != retained_identity.get('pid')
                            or str(observed_creation) != str(retained_identity.get('creation_id'))):
                        raise ValueError('停止时原broker创建身份冲突；不采用展示终态')
                    broker = entry.exit_probe(control, retained_identity['pid'], retained_identity['creation_id'])
                    state['broker'] = {**state['broker'], 'broker_state': broker,
                        'broker_pid': retained_identity['pid'],
                        'broker_creation_time': retained_identity['creation_id']}
                if broker['state'] == 'unknown':
                    if (state['broker'].get('broker_pid') is not None
                            or retained_identity is not None
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
                if projection_error is not None:
                    raise projection_error
                control.write_json(CURRENT, state)
                return envelope(state, command_id=rid)
            if worker['state'] == 'unknown':
                raise RuntimeError('worker退出证据未知；未宣称停止')
            time.sleep(.05)
        raise TimeoutError('停止请求已锁定，但25秒内退出未确认；不启动新worker')
    raise ValueError('未知命令')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'status', 'pause', 'takeover', 'resume', 'stop', 'decide', 'approve-battle', 'manual-checkpoint', 'manual-result', 'manual-stage', 'manual-step', '_worker'])
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--run-dir')
    parser.add_argument('--run-token')
    parser.add_argument('--max-seconds', type=int, default=7200)
    parser.add_argument('--max-matches', type=int, default=1)
    parser.add_argument('--continue-matches', action='store_true')
    parser.add_argument('--profile', action='store_true', help='记录本节点真实阶段/操作耗时，退出时生成 JSON/CSV/HTML')
    parser.add_argument('--profile-comparison-key', help='本机明确指定的同口径场景标识；不从单次结果推定可比')
    parser.add_argument('--business-resume-json', help=argparse.SUPPRESS)
    parser.add_argument('--handoff', action='store_true')
    parser.add_argument('--resume-guard-json')
    parser.add_argument('--reply-file')
    parser.add_argument('--reason', default='')
    parser.add_argument('--launch-id')
    parser.add_argument('--runtime-location-json', help=argparse.SUPPRESS)
    parser.add_argument('--request-id')
    parser.add_argument('--snapshot-id')
    parser.add_argument('--stage')
    parser.add_argument('--resume-epoch')
    parser.add_argument('--manual-id')
    parser.add_argument('--phase', choices=coaching.PHASES)
    parser.add_argument('--checkpoint-id')
    parser.add_argument('--operation', choices=manual_steps.OPERATIONS)
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
