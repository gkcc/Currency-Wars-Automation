"""Bounded local workflow, explicit strategy plans, one safety broker."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import currency_wars_broker_entry as entry
from currency_wars_source_guard import activity
from currency_wars_perception import Perception, clean, find_text, hash_distance, GOLD_HUD

artifacts = entry.artifacts
PROJECT = Path(__file__).resolve().parent.parent
CURRENT = PROJECT / 'docs' / 'CURRENT_RUNNER.json'
SELF = Path(__file__).resolve()
TERMINAL = {'stopped', 'completed', 'failed'}
PANELS = [('bonds', '羁绊链路'), ('income', '预期收益'),
          ('promotion', '晋升等级'), ('advantages', '优势布局')]


def now():
    return datetime.now(timezone.utc).isoformat()


def optional(path):
    try:
        return entry.read_json(path)
    except FileNotFoundError:
        return None


def redact(value):
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()
                if key not in ('run_token', 'gui_token')}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


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
        with file_lock(run):
            rejected = resume_guard_rejection(captured_guard, resume_guard_snapshot(run, owner, control))
            if rejected:
                return rejected
        result = entry.request(control, 'resume', [], rid, True, expected_pause_id=broker_pause)
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
            control.write_json(run / 'runner-resume-epoch.json', {'id': rid, 'time': now(),
                'consumed_manual_id': epoch,
                'consumed_manual_ids': consumed_ids})
        return {**result, 'guard_matched': True, 'resume_epoch': rid, 'consumed_manual_ids': consumed_ids}
    finally:
        (run / 'runner-resuming.json').unlink(missing_ok=True)


def start_cli(args):
    with activity(PROJECT, 'start') as lease:
        return _start_cli(args, lease)


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
                probe = control.process_probe(value['runner_pid'], value['runner_creation_id'])
                if probe['state'] != 'running':
                    raise RuntimeError('worker没有保持实际存活')
                lease.transfer_to_registered_child(child.pid, child_identity, 'runner')
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
    allowed = {'click_text', 'click_point', 'buy_shop', 'buy_xp', 'key', 'drag', 'scroll',
               'finish_inspection', 'confirm_match_result'}
    for action in actions:
        if not isinstance(action, dict) or action.get('type') not in allowed:
            raise ValueError('未知语义动作')
        if not isinstance(action.get('reason'), str) or not 1 <= len(action['reason']) <= 1000:
            raise ValueError('每个动作须有具体中文理由')
        if action['type'] not in ('finish_inspection', 'confirm_match_result') and not action.get('expected_page'):
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
        if action['type'] == 'finish_inspection' and action.get('panel') not in dict(PANELS):
            raise ValueError('仅可核实当前领奖/优势面板')
        if action['type'] == 'confirm_match_result' and (request['kind'] != 'settlement_verify'
                or not isinstance(action.get('result'), dict)):
            raise ValueError('仅真实整局结算请求可确认match结果')
        if action['type'] == 'buy_xp' and (type(action.get('count')) is not int or not 1 <= action['count'] <= 5):
            raise ValueError('单计划经验次数须1–5')
    if sum(action.get('count', 1) if action['type'] == 'buy_xp' else 1 for action in actions) > 16:
        raise ValueError('单战略计划底层操作预算超过16')
    return reply


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
                        'tasks': None, 'hp': None, 'coins': None, 'xp': None,
                        'unknown_fields': ['guide', 'guide_tracking', 'investments', 'environment',
                                           'team', 'bonds', 'gear', 'tasks', 'hp', 'coins', 'xp']}
        self.last_observation = None
        self.last_epoch = None
        self.wait_started = None
        self.wait_page = None
        self.broker_launcher = None
        self.children = []
        self.records = PROJECT / 'debug' / ('runner-' + args.chat_id[:8] + '-' + marker['run_id'][:12])
        self.records.mkdir(exist_ok=False)
        self.c.write_json(self.records / 'owner.json', {**redact(self.owner), 'deliverable': 'requested replay log',
                         'controller_sha256': entry.PINNED, 'runner_sha256': hashlib.sha256(SELF.read_bytes()).hexdigest()})
        self.evidence_count = 0
        self.world_entry_attempted = False
        self.consumed_match_results = set()
        self.active_match_id = uuid.uuid4().hex
        self.match_result_confirmed = False
        self.history = {}
        self.node_key, self.node_attempts, self.node_started = None, 0, None
        self.node_last_page, self.node_consecutive = None, 0
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
        verb = ' -Verb RunAs' if own['integrity_rid'] < game['integrity_rid'] else ''
        command = ('Start-Process -FilePath ' + quote(sys.executable) + ' -ArgumentList ' + quote(launch_args)
                   + verb + ' -WindowStyle Hidden -PassThru | Select-Object Id | ConvertTo-Json -Compress')
        try:
            # Unknown late children must protect the directory even before a
            # Popen handle or creation identity becomes available.
            artifacts.protect_children(self.run, self.children, root=artifacts.default_root(), complete=False)
            self.broker_launcher = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf8',
                        creationflags=subprocess.CREATE_NO_WINDOW)
            probe = self.c.process_probe(self.broker_launcher.pid)
            if probe['state'] != 'running':
                raise RuntimeError('本次owned启动器身份未确认')
            self.register(probe['pid'], probe['creation_id'])
        finally:
            self.c.k.ReleaseMutex(mutex)
            self.c.k.CloseHandle(mutex)
        self.publish(phase='等待Windows正常UAC/控制器就绪', reason='仅用户可确认安全桌面；不重复启动')
        end = min(self.deadline, time.monotonic() + 120)
        while time.monotonic() < end:
            if (self.run / 'runner-stop').exists():
                raise RuntimeError('启动期间用户停止')
            if (self.run / 'broker-ready.json').exists():
                state = self.c.status()
                if state['ready'] and state['acknowledged']:
                    self.register(state['broker_pid'], state['broker_creation_time'])
                    self.publish(broker=state)
                    break
            if self.broker_launcher.poll() is not None and self.broker_launcher.returncode:
                error = self.broker_launcher.stderr.read()[-600:]
                raise RuntimeError('Windows启动拒绝/失败：' + error)
            self.publish()
            time.sleep(.25)
        else:
            raise TimeoutError('正常UAC/ready在120秒内未完成；保留当前游戏，不重复弹窗')
        # Record the actual Start-Process/venv redirector identity while it is
        # still attributable to this launch, never infer ownership at Stop.
        self.broker_launcher.wait(timeout=5)
        launcher_output = self.broker_launcher.stdout.read()
        if launcher_output.strip():
            launch_pid = int(json.loads(launcher_output)['Id'])
            launch_probe = self.c.process_probe(launch_pid)
            if launch_probe['state'] == 'running':
                self.register(launch_pid, launch_probe['creation_id'])
        self.broker_launcher.stdout.close()
        self.broker_launcher.stderr.close()
        # Start only consumes its own initial pause. A newer GUI/physical
        # takeover after Start blocks automatic restoration.
        initial = manual_state(self.run)
        if initial and initial['manual_id'] == self.initial_manual_id:
            restored = explicit_resume(self.run, self.owner, self.c, uuid.uuid4().hex,
                expected_manual_id=self.initial_manual_id, expected_broker_pause_id=self.initial_broker_pause_id)
            self.publish(control_mode='auto' if restored.get('resumed') else 'manual',
                         phase='读取当前真实页面', reason=restored.get('error'), broker=self.c.status())
        else:
            self.publish(control_mode='manual', reason='新手动接管优先')

    def command(self, tokens, reason, expected_page=None, postcondition=None):
        if time.monotonic() >= self.deadline:
            raise RuntimeError('本次worker总期限已到，未发布动作')
        if manual_state(self.run) or (self.run / 'runner-stop').exists():
            raise RuntimeError('持续手动/停止锁，未发布游戏动作')
        state = self.c.status()
        if not state['ready'] or state['paused'] or state['input_halted'] or not state['game_foreground']:
            raise RuntimeError('游戏输入健康检查未通过')
        rid = uuid.uuid4().hex
        before = self.save_frame(rid, 'before')
        self.log({'event': 'decision', 'decision_id': rid, 'tokens': tokens, 'reason': reason,
                  'expected_page': expected_page, 'expected_change': postcondition,
                  'observation': self.last_observation, 'before_evidence': before})
        started = time.perf_counter()
        try:
            result = entry.request(self.c, 'actions', tokens, rid, False)
        except Exception as exc:
            self.log({'event': 'actual_result', 'decision_id': rid, 'request_id': rid,
                      'classification': 'control_outcome_unverified', 'error': str(exc),
                      'input_resent': False, 'after_evidence': None})
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
        return observed

    def observe(self):
        rid = uuid.uuid4().hex
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
                            reason, observed['page'], '点击已识别“' + label + '”后重新识别页面')

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
            'resume_epoch': self.epoch(), 'semantic': observed.get('semantic', {})}
        if len(self.history) > 256:
            self.history.pop(next(iter(self.history)))
        request = {'request_id': rid, 'snapshot_id': observed['snapshot_id'], 'kind': kind,
                   'reason': reason, 'observation': observed, 'context': self.context,
                   'inspection_results': self.inspections, 'choices': choices,
                   'resume_epoch': self.epoch(), 'match_id': self.active_match_id,
                   'evidence_file': evidence, 'original_png': str(self.run / 'request-original.png'), 'created_at': now(),
                   'deadline_at': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
                   'allowed_action_types': ['click_text', 'click_point', 'buy_shop', 'buy_xp', 'key', 'drag', 'scroll',
                                            'finish_inspection', 'confirm_match_result'],
                   'reply_path': str(self.run / 'decision-reply.json')}
        self.c.write_json(self.run / 'decision-request.json', request)
        self.state['statistics']['decisions'] += 1
        self.log({'event': 'strategy_request', 'request': request})
        self.publish(control_mode='waiting_decision', phase=kind, reason=reason, decision_request=request)

    def execute_plan(self, reply):
        request = self.state['decision_request']
        validate_plan(reply, request, self.epoch())
        # First compare the actual current screen, not the stored old image.
        actual = self.observe()
        original = request['observation']
        if actual['page'] != original['page'] or hash_distance(actual['fingerprint'], original['fingerprint']) > .10:
            raise ValueError('战略回答到达时页面已变，拒绝旧计划')
        self.update_context(reply.get('context_update', {}))
        for action in reply['actions']:
            if manual_state(self.run) or self.epoch() != request['resume_epoch']:
                raise RuntimeError('手动接管使未执行计划失效')
            actual = self.last_observation
            kind = action['type']
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
            if (kind in ('buy_shop', 'buy_xp') or (actual['page'] == 'shop'
                    and not (kind == 'click_text' and action.get('text') == '收起'))
                    or (kind == 'key' and action.get('args') in ([68], [69]))
                    or (kind == 'click_text' and '购买经验' in action.get('text', ''))):
                self.require_strategy_context(actual)
            if kind == 'click_text':
                self.click_text(actual, action['text'], action['reason'], action.get('exact', True), action.get('bounds'))
            elif kind == 'buy_shop':
                shop = actual.get('shop')
                if not shop or not shop['ok']:
                    raise ValueError('未完整读取五槽，不执行购买')
                slot = next((s for s in shop['slots'] if s['slot'] == action['slot']), None)
                if not slot or slot['status'] != 'recognized' or slot['name'] != action['name'] or slot['cost'] != action['cost']:
                    raise ValueError('目标槽/角色/实价与计划不符')
                x, y = slot['position']
                self.command([f'click:{x}:{y}', 'wait:0.5'], action['reason'], 'shop', {'bought': slot['name']})
            elif kind == 'buy_xp':
                for unused in range(action['count']):
                    actual = self.last_observation
                    self.click_text(actual, '购买经验', action['reason'], False)
                    if actual['fields']['level'] != self.last_observation['fields']['level']:
                        break
            else:
                values = action.get('args', [])
                command = {'click_point': 'click', 'key': 'key', 'drag': 'drag', 'scroll': 'scroll'}[kind]
                self.c.validate_actions([{'type': command, 'args': values}])
                self.command([command + ':' + ':'.join(map(str, values)), 'wait:0.7'], action['reason'], actual['page'], action.get('expected_change'))
        if request['kind'] == 'new_match' and self.last_observation['page'] in ('opponents', 'environment', 'investment'):
            # A new game identity is admitted only at a real setup screen,
            # never merely because a caller supplied a different request ID.
            self.active_match_id = uuid.uuid4().hex
            self.match_result_confirmed = False
            self.context = {key: None for key in self.context}
        self.log({'event': 'plan_consumed', 'request_id': request['request_id'], 'actions': len(reply['actions'])})
        self.publish(control_mode='auto', decision_request=None, reason=None)

    def check_target_roi(self, action, request, actual):
        proof = action.get('target_evidence', {})
        if proof.get('snapshot_id') != request['snapshot_id'] or self.epoch() != request['resume_epoch']:
            raise ValueError('目标ROI的画面/交接代次不符')
        reference = Path(request['original_png'])
        if hashlib.sha256(reference.read_bytes()).hexdigest() != request['snapshot_id']:
            raise ValueError('本次请求原始帧已更换，拒绝坐标计划')
        from PIL import Image
        box = proof.get('bounds')
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
        else:
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
        with Image.open(reference) as old, Image.open(self.run / 'game-preview.png') as fresh:
            if old.crop(box).convert('RGB').tobytes() != fresh.crop(box).convert('RGB').tobytes():
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
            if key not in ('guide', 'guide_tracking', 'investments', 'coins', 'environment'):
                raise ValueError('v1尚无可信本地字段读取器，保留unknown：' + key)
            if not isinstance(record, dict) or 'value' not in record or not isinstance(record.get('proof'), dict):
                raise ValueError('context须带实际值及出处proof，非空字符串不足以批准操作')
            proof = record['proof']
            lifetime = 180 if key == 'coins' else 3600
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
            self.context[key] = {**record, 'value': normalized, 'verified_observed_at': source['observed_at'],
                'verified_proofs': verified_proofs, 'match_id': self.active_match_id, 'lifetime_seconds': lifetime}

    def require_strategy_context(self, actual):
        for key in ('guide', 'guide_tracking', 'investments', 'coins'):
            record = self.context.get(key)
            if not isinstance(record, dict) or record.get('match_id') != self.active_match_id:
                raise ValueError('购买前缺少实读且带出处的' + key + '，先补核')
            proof = record.get('proof', {})
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(record['verified_observed_at'])).total_seconds()
            if proof.get('resume_epoch') != self.epoch() or not 0 <= age <= record['lifetime_seconds']:
                raise ValueError('购买前战略context已过期/接管后须重核：' + key)
            for verified in record.get('verified_proofs', []):
                self.verified_source(verified, record['lifetime_seconds'])
        guide = self.context['guide']['value']
        investments = self.context['investments']['value']
        if guide['compatibility']['investment_names'] != [item['name'] for item in investments]:
            raise ValueError('攻略兼容推论未针对当前已选全部投资策略；先重核')
        coin_fact = actual.get('semantic', {}).get('coins')
        if not coin_fact:
            raise ValueError('新鲜金币HUD无法可信读取，停止购买')
        self.context['coins']['value'] = coin_fact['value']
        self.log({'event': 'live_coins_roi', **coin_fact, 'snapshot_id': actual['snapshot_id']})

    def node_guard(self, observed):
        page = observed['page']
        in_panel = self.panel_index < len(PANELS) and page in ('lobby', 'reward_overlay', PANELS[self.panel_index][0])
        key = ('panel', self.panel_index, self.panel_state) if in_panel else (page, observed['fields'].get('stage'))
        if key != self.node_key:
            self.node_key, self.node_attempts, self.node_started = key, 0, time.monotonic()
        if page != getattr(self, 'node_last_page', None):
            self.node_last_page, self.node_consecutive = page, 0
        self.node_consecutive += 1
        maximum, seconds = (70, 180) if in_panel else (30, 240) if page == 'battle' else (6, 60)
        if (self.node_attempts >= maximum or time.monotonic() - self.node_started >= seconds
                or (page != 'battle' and self.node_consecutive > 6)):
            self.pause_internal(f'本地节点{key}超过{maximum}次/{seconds}秒有界预算，停止重复输入')
            return False
        self.node_attempts += 1
        return True

    def tick_decision(self):
        request = self.state['decision_request']
        if request['resume_epoch'] != self.epoch():
            self.publish(decision_request=None, control_mode='auto', reason='新交接后废弃旧战略请求')
            return
        if datetime.now(timezone.utc) > datetime.fromisoformat(request['deadline_at']):
            self.pause_internal('战略等待15分钟期限已到；没有无限未知循环')
            return
        reply = optional(self.run / 'decision-reply.json')
        if reply:
            (self.run / 'decision-reply.json').unlink()
            self.execute_plan(reply)
        else:
            # Passive observe refreshes the original broker idle lease, with
            # zero SetForeground/SendInput. Never resumes a manual pause.
            self.publish()
            time.sleep(.25)

    def pause_internal(self, reason):
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
        if page != self.wait_page:
            self.wait_page, self.wait_started = page, time.monotonic()
        elapsed = time.monotonic() - self.wait_started
        if page == 'world_entry':
            if self.world_entry_attempted:
                self.ask(observed, 'world_entry_unverified', 'F已执行一次但仍停在大世界；停止重复按F，核当前真实提示。')
            else:
                self.world_entry_attempted = True
                self.command(['key:70', 'wait:1.5'], '新画面同时识别朝露公馆与货币战争原生F交互，进入活动', page, '活动主界面')
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
            self.ask(observed, 'shop_strategy', '完整五槽新商店已本地读取；结合实读投资、攻略与追踪判断购买/经验/刷新，50利息只参考。')
        elif page == 'preparation':
            # Open the real shop before strategy; grey/transition != shop open.
            button = find_text(observed['rows'], '商店')
            if button:
                self.click_text(observed, '商店', '每回合先完整读店，再决策；不把关店过渡当可领奖')
            else:
                self.ask(observed, 'preparation_strategy', '须完整读店、Aha可合成/前后台、装备/指南追踪，核可战阵容后一次计划出战。')
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
        elif page in ('environment', 'investment', 'opponents', 'supply', 'guide'):
            if page == 'investment':
                self.context['investments'] = None
            self.ask(observed, page + '_strategy', '新战略分岔：实读全部选项/代价，黄色小书优先，核投资与第一推荐正文/追踪匹配后给有限计划。')
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
        try:
            while time.monotonic() < self.deadline and self.state['control_mode'] not in TERMINAL:
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
                                 reason=(manual or {}).get('reason') or status.get('reason'))
                    if time.monotonic() - last_capture > 90:
                        self.observe()
                        last_capture = time.monotonic()
                    time.sleep(.25)
                    continue
                if not status['game_foreground']:
                    self.pause_internal('批次外前台丢失，保持手动，不自动抢回')
                    continue
                if self.state.get('decision_request'):
                    if time.monotonic() - last_capture > 90:
                        self.observe()
                        last_capture = time.monotonic()
                    try:
                        self.tick_decision()
                    except Exception as exc:
                        self.pause_internal(str(exc))
                    continue
                self.publish(control_mode='auto', reason=None)
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
        # A prewritten stop also prevents a late, still-pending UAC launch from
        # issuing input. Unknown late children keep the standard directory.
        (self.run / 'broker-stop').touch()
        evidence = {'broker': {'state': 'unknown'}, 'worker': {'state': 'exiting'}}
        identity = optional(self.run / 'broker-process.json')
        if identity:
            result = entry.stop(self.c, self.run, self.args.chat_id, self.token)
            evidence['broker'] = result['exit_evidence']
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
            worker.state.update(control_mode='failed', reason=str(exc),
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
    if args.command in ('pause', 'takeover'):
        intent_error = None
        try:
            latch_manual(run, args.reason or ('用户手动接管' if args.command == 'takeover' else '用户暂停'), rid)
        except Exception as exc:
            intent_error = str(exc)
        # Emergency broker pause is attempted even if the display/intention
        # channel failed; never falsely ACK a missing persistent runner intent.
        result = entry.emergency_pause(control, run, args.reason or '用户手动暂停')
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
    if args.command == 'decide':
        state = current_state(run, owner, control)
        if state['control_mode'] != 'waiting_decision' or manual_state(run):
            raise ValueError('未在可接受战略回答状态；手动锁优先')
        reply = entry.read_json(Path(args.reply_file).absolute(), limit=100_000)
        validate_plan(reply, state['decision_request'], (optional(run / 'runner-resume-epoch.json') or {}).get('id'))
        with file_lock(run, 'decision-submit.lock'):
            if (run / 'decision-reply.json').exists():
                raise ValueError('已有待消费回答，不覆盖')
            control.write_json(run / 'decision-reply.json', reply)
        return envelope(state, command_id=rid)
    if args.command == 'stop':
        # The input broker stop flag has absolute priority over all ordinary
        # state locks and optional manual display updates.
        (run / 'broker-stop').touch()
        (run / 'runner-stop').touch()
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
    parser.add_argument('command', choices=['start', 'status', 'pause', 'takeover', 'resume', 'stop', 'decide', '_worker'])
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
