"""Bounded supervised steps performed by the already owning Worker.

This is a mailbox and intent adapter, not an input implementation or controller.
The existing Worker, Entry ledger, broker, ManualPhase and resume CAS stay in use.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCHEMA = 'manual-worker-step/v1'
OPERATIONS = ('inspect', 'collect_rewards', 'recover_reward', 'recover_overlay', 'reviewed_plan')


def _runner():
    import currency_wars_runner
    return currency_wars_runner


def _path(run, step_id):
    if not isinstance(step_id, str) or not re.fullmatch(r'[0-9a-f]{32}', step_id):
        raise ValueError('manual-step requires a stable 32-hex request ID')
    return Path(run) / 'manual-steps' / (step_id + '.json')


def _checkpoint(run, binding, checkpoint_id):
    r = _runner()
    if not isinstance(checkpoint_id, str) or not re.fullmatch(r'[0-9a-f]{32}', checkpoint_id):
        raise ValueError('physical manual-step requires its pending ManualPhase checkpoint')
    item = r.entry.read_json(Path(run) / 'manual-results' / (checkpoint_id + '.json'))
    if item.get('binding') != binding or item.get('status') != 'pending':
        raise ValueError('manual checkpoint changed or already finished; no manual input')
    return item


def _reward_roi(operation, reply):
    if operation == 'collect_rewards' and reply is not None:
        if not isinstance(reply, dict) or set(reply) != {'reward_roi'} or not isinstance(reply['reward_roi'], dict):
            raise ValueError('collect_rewards accepts only one explicit reward_roi annotation')
        return reply['reward_roi']
    return None


def validate_reward_roi(evidence, request, binding, checkpoint_id):
    """Authenticate one supervisor target; never replace native reader facts.

    The caller supplies its STILL outstanding request, including after a fresh
    observation. Publication repeats this check against that same request.
    Pixel visibility and the current layout remain the Worker's responsibility.
    """
    from currency_wars_rewards import SCAN_BOUNDS
    keys = {'source', 'request_id', 'snapshot_id', 'capture_request_id', 'frame_id',
            'page', 'match_id', 'stage', 'resume_epoch', 'checkpoint_id', 'deadline_at',
            'kind', 'bounds', 'center', 'findings'}
    if (not isinstance(evidence, dict) or set(evidence) != keys
            or not isinstance(request, dict) or not isinstance(binding, dict)
            or evidence.get('source') != 'supervising_agent'
            or evidence.get('kind') not in ('blue_orb', 'gray_orb')
            or not isinstance(evidence.get('findings'), str) or not 1 <= len(evidence['findings'].strip()) <= 1000):
        raise ValueError('single reward ROI needs a separate bounded supervisor annotation')
    original = request.get('observation')
    if not isinstance(original, dict):
        raise ValueError('single reward ROI lacks its original Worker observation')
    for key in ('request_id', 'snapshot_id', 'match_id', 'resume_epoch', 'deadline_at'):
        if not isinstance(evidence[key], str) or not evidence[key] or evidence[key] != request.get(key):
            raise ValueError('single reward ROI differs from the outstanding request: ' + key)
    if (request.get('kind') != 'preparation_strategy'
            or evidence['page'] != 'preparation' or original.get('page') != 'preparation'
            or original.get('snapshot_id') != evidence['snapshot_id']
            or evidence['match_id'] != binding.get('match_id')
            or evidence['resume_epoch'] != binding.get('old_epoch')
            or evidence['stage'] != binding.get('stage')
            or evidence['stage'] != original.get('fields', {}).get('stage')
            or evidence['checkpoint_id'] != checkpoint_id
            or not isinstance(checkpoint_id, str) or not re.fullmatch(r'[0-9a-f]{32}', checkpoint_id)
            or request.get('preparation_checklist', {}).get('phase') != 'rewards'):
        raise ValueError('single reward ROI is not this match/stage/epoch rewards checkpoint')
    for key in ('capture_request_id', 'frame_id'):
        if (not isinstance(evidence[key], str) or not evidence[key]
                or evidence[key] != original.get(key)):
            raise ValueError('single reward ROI lacks its original immutable frame identity')
    try:
        deadline = datetime.fromisoformat(evidence['deadline_at'])
        if deadline.tzinfo is None or datetime.now(timezone.utc) >= deadline:
            raise ValueError('single reward ROI request expired')
    except (TypeError, ValueError) as error:
        raise ValueError('single reward ROI request deadline is invalid or expired') from error
    bounds, center = evidence['bounds'], evidence['center']
    if (not isinstance(bounds, list) or len(bounds) != 4 or any(type(v) is not int for v in bounds)
            or not isinstance(center, list) or len(center) != 2 or any(type(v) is not int for v in center)
            or not SCAN_BOUNDS[0] <= bounds[0] < bounds[2] <= SCAN_BOUNDS[2]
            or not SCAN_BOUNDS[1] <= bounds[1] < bounds[3] <= SCAN_BOUNDS[3]
            or center != [(bounds[0]+bounds[2])//2, (bounds[1]+bounds[3])//2]):
        raise ValueError('single reward ROI and center must stay inside the supported reward region')
    try:
        path = Path(request['original_png'])
        if (not path.is_file() or path.stat().st_size > 25_000_000
                or hashlib.sha256(path.read_bytes()).hexdigest() != evidence['snapshot_id']):
            raise ValueError('single reward ROI original PNG bytes changed or are missing')
    except (KeyError, OSError, TypeError) as error:
        raise ValueError('single reward ROI original PNG bytes are unavailable') from error
    # Narrative edits or an extended deadline never create another intent.
    identity = {key: evidence[key] for key in sorted(keys - {'findings', 'deadline_at'})}
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def validate_reward_recovery(evidence, request, binding, checkpoint_id, pending):
    """A current supervisor disposition, never a replacement native outcome."""
    keys = {'source', 'request_id', 'snapshot_id', 'capture_request_id', 'frame_id',
            'page', 'match_id', 'stage', 'resume_epoch', 'checkpoint_id', 'deadline_at',
            'reward_step_id', 'input_request_id', 'prior_outcome_remains_unknown',
            'target_now_absent', 'continue_current_rewards', 'findings'}
    if (not isinstance(evidence, dict) or set(evidence) != keys
            or evidence.get('source') != 'supervising_agent'
            or any(evidence.get(key) is not True for key in
                   ('prior_outcome_remains_unknown', 'target_now_absent', 'continue_current_rewards'))
            or not isinstance(evidence.get('findings'), str) or not 1 <= len(evidence['findings'].strip()) <= 1000
            or not isinstance(request, dict) or not isinstance(pending, dict)):
        raise ValueError('reward recovery needs an explicit current supervisor disposition; history remains unknown')
    original = request.get('observation') or {}
    if (request.get('kind') not in ('reward_result', 'preparation_strategy')
            or request.get('preparation_checklist', {}).get('phase') != 'rewards'
            or any(not isinstance(evidence.get(key), str) or not evidence[key]
                   or evidence[key] != request.get(key) for key in
                   ('request_id', 'snapshot_id', 'match_id', 'resume_epoch', 'deadline_at'))
            or evidence['page'] != 'preparation' or original.get('page') != 'preparation'
            or any(evidence[key] != original.get(key) for key in ('snapshot_id', 'capture_request_id', 'frame_id'))
            or any(not isinstance(evidence[key], str) or not evidence[key] for key in ('capture_request_id', 'frame_id'))
            or evidence['match_id'] != binding.get('match_id')
            or evidence['resume_epoch'] != binding.get('old_epoch')
            or evidence['stage'] != binding.get('stage') or evidence['stage'] != original.get('fields', {}).get('stage')
            or evidence['checkpoint_id'] != checkpoint_id
            or pending.get('match_id') != evidence['match_id']
            or pending.get('before', {}).get('stage') != evidence['stage']
            or evidence['reward_step_id'] != pending.get('step_id')
            or evidence['input_request_id'] != pending.get('request_id')
            or pending.get('status') != 'pending' or pending.get('outcome') != 'unknown'
            or pending.get('kind') not in ('blue_orb', 'gray_orb')
            or pending.get('publication_attempted') is not True):
        raise ValueError('reward recovery request/frame/match/stage/epoch/checkpoint/original step differs')
    deadline = datetime.fromisoformat(evidence['deadline_at'])
    if deadline.tzinfo is None or datetime.now(timezone.utc) >= deadline:
        raise ValueError('reward recovery deadline expired')
    path = Path(request.get('original_png', ''))
    if not path.is_file() or path.stat().st_size > 25_000_000 or hashlib.sha256(path.read_bytes()).hexdigest() != evidence['snapshot_id']:
        raise ValueError('reward recovery current source PNG missing or changed')
    return _digest({key: evidence[key] for key in sorted(keys - {'findings', 'deadline_at'})})


def _recovery_reply(operation, reply):
    if operation != 'recover_reward':
        return None
    if not isinstance(reply, dict) or set(reply) != {'reward_recovery'}:
        raise ValueError('recover_reward accepts only reward_recovery; it has no game actions')
    return reply['reward_recovery']


def _bound_reward_frame(run, control, observed, path, *, read_only=False):
    """Authenticate saved native frame provenance without capture or OCR."""
    r = _runner()
    receipt = r.await_existing_receipt(run, control, observed.get('capture_request_id'), 0)
    result, request = receipt.get('result') or {}, receipt.get('request') or {}
    frame = result.get('observation') or {}
    if (result.get('ok') is not True or r.manual_receipt_state(receipt)['unknown_input']
            or frame.get('frame_protocol') != 1 or frame.get('request_id') != receipt['id']
            or frame.get('frame_id') != observed.get('frame_id')
            or frame.get('snapshot_sha256') != observed.get('snapshot_id')
            or frame.get('captured_at') != observed.get('captured_at')
            or hashlib.sha256(Path(path).read_bytes()).hexdigest() != observed.get('snapshot_id')
            or read_only and (request.get('handoff') is not False
                or request.get('actions') != [{'type': 'observe', 'args': []}])):
        raise ValueError('reward recovery immutable frame/receipt/source differs')
    return receipt


def _recovery_original(run, control, records, pending):
    r = _runner()
    archive = Path(records) / ('reward-step-' + pending['step_id'] + '.json')
    if r.entry.read_json(archive) != pending:
        raise ValueError('original reward pending/archive differs; no replacement history')
    before_path = Path(pending['before_png'])
    if before_path.resolve().parent != Path(records).resolve():
        raise ValueError('original reward PNG is outside its owned records')
    _bound_reward_frame(run, control, pending['before']['observation'], before_path)
    receipt = r.await_existing_receipt(run, control, pending['request_id'], 0)
    request, result = receipt.get('request') or {}, receipt.get('result') or {}
    physical = [a for a in request.get('actions', []) if a.get('type') not in ('wait', 'observe')]
    if (r.manual_receipt_state(receipt)['state'] != 'completed' or result.get('ok') is not True
            or request.get('kind') != 'actions' or request.get('handoff') is not False
            or request.get('actions') != pending.get('broker_actions')
            or physical != [{'type': 'click', 'args': pending['target']['center']}]):
        raise ValueError('original reward receipt is not the exact completed single input; never resend')
    return receipt


def _prepare_recovery_record(run, control, records, pending, evidence):
    """Retain a failed read-only report before an explicit NEW current review.

    A returned/unfinished attempt is never replayed. This only frees a failed
    recovery-report slot; no original input or automatic read budget changes.
    """
    r = _runner()
    path = Path(records) / ('reward-continuation-' + pending['step_id'] + '.json')
    if not path.exists():
        return
    value = r.entry.read_json(path)
    job = r.entry.read_json(_path(run, value.get('manual_step_id')))
    result = job.get('result') or {}
    if (job.get('status') != 'refused' or job.get('payload', {}).get('operation') != 'recover_reward'
            or job['payload'].get('reply') != {'reward_recovery': value.get('evidence')}
            or value.get('source_record_sha256') != _digest(pending)
            or result.get('receipt_watermark_verified') is not True
            or result.get('receipt_delivery_verified') is not True
            or result.get('input_receipt_ids') != [] or unknown_receipts(run, control)
            or any(evidence.get(key) == value.get('evidence', {}).get(key)
                   for key in ('request_id', 'capture_request_id', 'frame_id'))):
        raise ValueError('original reward has a returned/unfinished recovery; inspect its original manual step')
    archive = path.with_name(path.stem + '-failed-' + job['step_id'] + '.json')
    if archive.exists():
        raise ValueError('failed recovery evidence already exists; do not overwrite')
    path.rename(archive)


def reward_continuation(run, owner, control, records, pending):
    """A durable disposition of one old blocker, NOT current input authority.

    Later epochs/nodes still require all ordinary fresh guards. The old step
    remains pending in the business archive. This record cannot authorize its
    old coordinates, infer its fee, or inherit a completed rewards phase.
    """
    r = _runner()
    path = Path(records) / ('reward-continuation-' + str(pending.get('step_id')) + '.json')
    if not path.exists():
        return None
    try:
        value = r.entry.read_json(path)
        binding = value.get('binding') or {}
        job = r.entry.read_json(_path(run, value['manual_step_id']))
        outcome = (job.get('result') or {}).get('reward_recovery') or {}
        checkpoint = r.entry.read_json(Path(run) / 'manual-results' / (value['evidence']['checkpoint_id'] + '.json'))
        if (value.get('schema') != 'supervised-reward-continuation/v1' or value.get('status') != 'authorized'
                or value.get('source') != 'supervising_agent' or value.get('prior_outcome_remains_unknown') is not True
                or value.get('all_rewards_cleared') is not None or value.get('input_resent') is not False
                or binding.get('run_id') != owner['run_id'] or binding.get('match_id') != pending.get('match_id')
                or binding.get('stage') != pending.get('before', {}).get('stage')
                or value.get('source_record_sha256') != _digest(pending)
                or value.get('reward_step_id') != pending.get('step_id')
                or value.get('original_request_id') != pending.get('request_id')
                or job.get('status') != 'returned' or job.get('binding') != binding
                or job.get('payload', {}).get('operation') != 'recover_reward'
                or job['payload'].get('reply') != {'reward_recovery': value.get('evidence')}
                or checkpoint.get('binding') != binding or checkpoint.get('phase') != 'rewards'
                or checkpoint.get('status') not in ('pending', 'completed')
                or outcome.get('record_sha256') != hashlib.sha256(path.read_bytes()).hexdigest()
                or outcome.get('continuation_allowed') is not True
                or outcome.get('prior_outcome_remains_unknown') is not True
                or (job.get('result') or {}).get('receipt_watermark_verified') is not True
                or job['result'].get('receipt_delivery_verified') is not True
                or job['result'].get('input_receipt_ids') != []
                or job['result'].get('evidence_errors') or job['result'].get('prior_unknown_receipt_ids')
                or job.get('finalization_errors')):
            raise ValueError('reward continuation source/manual intent differs')
        receipt = _recovery_original(run, control, records, pending)
        if _digest(r.redact(receipt)) != value.get('original_receipt_sha256'):
            raise ValueError('reward continuation original receipt changed')
        for key in ('reviewed', 'current'):
            frame = value[key]
            if Path(frame['png']).resolve().parent != Path(records).resolve():
                raise ValueError('reward continuation frame is outside owned records')
            _bound_reward_frame(run, control, frame['observation'], frame['png'], read_only=key == 'current')
        return value
    except (OSError, ValueError, KeyError, TypeError, AttributeError, TimeoutError):
        return None  # Missing/changed evidence restores the original blocker.


def submit(run, owner, control, *, manual_id, step_id, operation, checkpoint_id=None,
           reply=None, wait_seconds=25):
    """Queue once, or read the SAME step. A timeout never creates another job."""
    r = _runner()
    run, path = Path(run), _path(run, step_id)
    if operation not in OPERATIONS or type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 25:
        raise ValueError('unsupported or unbounded manual step')
    annotation = _reward_roi(operation, reply)
    recovery = _recovery_reply(operation, reply)
    if (operation == 'reviewed_plan' and not isinstance(reply, dict)
            or operation not in ('reviewed_plan', 'collect_rewards', 'recover_reward') and reply is not None):
        raise ValueError('only reviewed_plan, annotated collect_rewards or recover_reward accepts a reply')
    payload = dict(operation=operation, checkpoint_id=checkpoint_id, reply=reply, manual_id=manual_id)
    with r.file_lock(run, 'manual-checkpoint.lock', timeout=2):
        if path.exists():
            item = r.entry.read_json(path)
            if item.get('payload') != payload or item.get('run_id') != owner['run_id']:
                raise ValueError('manual-step ID reused with another intent')
        else:
            binding, unused_records = r._manual_binding(run, owner, control, manual_id)
            if operation != 'inspect':
                checkpoint = _checkpoint(run, binding, checkpoint_id)
                if operation in ('collect_rewards', 'recover_reward') and checkpoint.get('phase') != 'rewards':
                    raise ValueError('native reward loop belongs only to the rewards checkpoint')
            path.parent.mkdir(exist_ok=True)
            existing = list(path.parent.glob('*.json'))
            if len(existing) >= 128:
                raise ValueError('manual-step mailbox reached its bounded capacity')
            if any(r.entry.read_json(p).get('status') in ('queued', 'running') for p in existing):
                raise ValueError('a manual-step is already pending; inspect that original ID')
            intent, request = None, None
            if annotation is not None:
                unused_records, state = r._manual_records(run, owner)
                request = state.get('decision_request')
                intent = validate_reward_roi(annotation, request, binding, checkpoint_id)
                if any(r.entry.read_json(p).get('reward_roi_intent') == intent for p in existing):
                    raise ValueError('single reward ROI was already submitted; read its original step, never replay')
            if recovery is not None:
                records, state = r._manual_records(run, owner)
                request = state.get('decision_request')
                pending = r.entry.read_json(run / 'reward-step.json')
                intent = validate_reward_recovery(recovery, request, binding, checkpoint_id, pending)
                _prepare_recovery_record(run, control, records, pending, recovery)
            expires = datetime.now(timezone.utc) + timedelta(seconds=60)
            if request is not None:
                expires = min(expires, datetime.fromisoformat(request['deadline_at']))
            item = dict(schema=SCHEMA, step_id=step_id, run_id=owner['run_id'], binding=binding,
                        payload=payload, status='queued', created_at=r.now(),
                        expires_at=expires.isoformat(),
                        input_resent=False)
            if intent is not None:
                item['reward_recovery_intent' if recovery is not None else 'reward_roi_intent'] = intent
            control.write_json(path, item)
    end = time.monotonic() + wait_seconds
    while True:
        item = r.entry.read_json(path)
        if item.get('status') not in ('queued', 'running') or time.monotonic() >= end:
            return summary(item)
        time.sleep(.05)


def summary(item):
    result = item.get('result') or {}
    return dict(ok=item.get('status') != 'refused', step_id=item['step_id'],
                status=item['status'], input_resent=False,
                result=item.get('result'), error=item.get('error'),
                pending=item.get('status') in ('queued', 'running')
                    or result.get('receipt_watermark_verified') is False
                    or result.get('receipt_delivery_verified') is False
                    or ((result.get('reward_step') or {}).get('pending') is True
                        and (result.get('reward_recovery') or {}).get('continuation_allowed') is not True)
                    or bool(result.get('prior_unknown_receipt_ids'))
                    or bool(item.get('finalization_errors'))
                    or any(value.get('unknown_input') for value in result.get('receipt_states', [])),
                next_step='same step ID only; no replay of a queued/running or published action')


def active(worker):
    """An explicit, live manual lease; never clear or silently consume pause."""
    r = _runner()
    item = getattr(worker, 'manual_step_context', None)
    if not isinstance(item, dict):
        return False
    try:
        current = r.entry.read_json(_path(worker.run, item['step_id']))
        binding, unused_records = r._manual_binding(worker.run, worker.owner, worker.c,
                                                   item['binding']['manual_id'])
        if item['payload']['operation'] != 'inspect':
            checkpoint = _checkpoint(worker.run, binding, item['payload']['checkpoint_id'])
            if item['payload']['operation'] in ('collect_rewards', 'recover_reward') and checkpoint.get('phase') != 'rewards':
                return False
        return (current.get('status') == 'running' and current.get('binding') == item['binding'] == binding
                and current.get('payload') == item['payload']
                and binding['match_id'] == worker.active_match_id
                and binding['old_epoch'] == worker.epoch()
                and datetime.now(timezone.utc) < datetime.fromisoformat(item['expires_at']))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def unknown_receipts(run, control):
    """Do not lose an old pending/unknown input merely by changing HUD stage."""
    r = _runner()
    paths = list((Path(run) / 'request-ledger').glob('*.json'))
    if len(paths) > 4096:
        raise ValueError('receipt bound exceeded; no truncated pending check')
    unknown = []
    for path in paths:
        receipt = r.entry.read_json(path)
        try:
            receipt = r.await_existing_receipt(Path(run), control, receipt['id'], 0)
            if r.manual_receipt_state(receipt)['unknown_input']:
                unknown.append(receipt['id'])
        except TimeoutError:
            unknown.append(receipt['id'])
    return sorted(unknown)


def _ask_current(worker):
    observed = worker.last_observation
    page = observed['page']
    kind = page + '_strategy' if page in ('investment', 'environment', 'supply', 'opponents', 'guide', 'unit_gear') else (
        'preparation_strategy' if page in ('preparation', 'shop') else 'unknown_page')
    worker.state['decision_request'] = None
    worker.ask(observed, kind, '同一Worker的当前手操例外帧；一次受检计划或返回原生重复流程，不提交旧坐标批次。',
               category='exception', business_step='manual_step')


def _watermark(worker):
    values = worker.economy_receipt_watermark()
    if (not isinstance(values, list) or len(values) > 4096
            or any(not isinstance(value, str) or not 1 <= len(value) <= 100 for value in values)
            or len(set(values)) != len(values)):
        raise ValueError('manual-step receipt watermark is incomplete or invalid')
    return set(values)


def _evidence_error(stage, error, *, request_id=None):
    value = dict(stage=stage, error_type=type(error).__name__)
    if request_id is not None:
        value['request_id'] = request_id
    return value


def _finish_result(worker, item, before_ids, evidence_errors):
    """Preserve partial evidence without turning failed enumeration into zero."""
    r = _runner()
    after_ids = None
    try:
        after_ids = _watermark(worker)
        if before_ids is not None and not before_ids.issubset(after_ids):
            raise ValueError('manual-step prior receipt disappeared from final watermark')
    except Exception as error:
        evidence_errors.append(_evidence_error('after_watermark', error))
        after_ids = None
    verified = before_ids is not None and after_ids is not None
    ids = sorted(after_ids - before_ids) if verified else []
    receipt_states = []
    for rid in ids:
        try:
            receipt = r.await_existing_receipt(worker.run, worker.c, rid, 0)
            state = r.manual_receipt_state(receipt)
            if (not isinstance(state, dict) or state.get('request_id') != rid
                    or state.get('state') not in ('zero_input', 'control', 'completed', 'partial', 'unknown')
                    or type(state.get('unknown_input')) is not bool):
                raise ValueError('manual-step receipt classification is invalid')
            receipt_states.append(state)
        except Exception as error:
            receipt_states.append(dict(request_id=rid,
                state='pending' if isinstance(error, TimeoutError) else 'unknown', unknown_input=True))
            evidence_errors.append(_evidence_error('receipt', error, request_id=rid))
    reward_step = None
    try:
        pending = r.optional(worker.run / 'reward-step.json')
        if pending and (pending.get('manual_step_id') == item['step_id']
                        or item['payload']['operation'] == 'recover_reward'):
            reward_step = {key: pending.get(key) for key in
                ('step_id', 'status', 'outcome', 'request_id', 'source', 'after_snapshot_id', 'verification_reads')}
            reward_step.update(pending=pending.get('status') not in ('verified', 'refused'),
                               all_rewards_cleared=None,
                               record_file=str(worker.records / ('reward-step-' + str(pending.get('step_id')) + '.json')))
    except Exception as error:
        evidence_errors.append(_evidence_error('reward_step', error))
        reward_step = dict(status='unknown', pending=True, all_rewards_cleared=None)
    observed = worker.last_observation if isinstance(worker.last_observation, dict) else {}
    request = worker.state.get('decision_request') or {}
    request = request if isinstance(request, dict) else {}
    fields = observed.get('fields') if isinstance(observed.get('fields'), dict) else {}
    delivery_verified = verified and all(not state['unknown_input'] for state in receipt_states)
    item['result'] = dict(receipt_states=receipt_states, receipt_watermark_verified=verified,
        receipt_delivery_verified=delivery_verified, evidence_errors=evidence_errors,
        # null explicitly says the input ID set is unavailable, not empty.
        input_receipt_ids=[state['request_id'] for state in receipt_states if state['state'] != 'zero_input']
            if verified else None,
        snapshot_id=observed.get('snapshot_id'), capture_request_id=observed.get('capture_request_id'),
        frame_id=observed.get('frame_id'), page=observed.get('page'), stage=fields.get('stage'),
        decision_request_id=request.get('request_id'), decision_kind=request.get('kind'),
        all_rewards_cleared=None, automatic_phase_completion=False, input_resent=False,
        reward_step=reward_step, prior_unknown_receipt_ids=item.get('prior_unknown_receipt_ids', []))
    if item['payload']['operation'] == 'recover_reward':
        try:
            evidence = item['payload']['reply']['reward_recovery']
            path = worker.records / ('reward-continuation-' + evidence['reward_step_id'] + '.json')
            continuation = r.optional(path) or {}
            if continuation.get('manual_step_id') == item['step_id'] and item.get('status') == 'returned':
                item['result']['reward_recovery'] = dict(continuation_allowed=True,
                    prior_outcome_remains_unknown=True, original_request_id=evidence['input_request_id'],
                    reward_step_id=evidence['reward_step_id'], record_file=str(path),
                    record_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    current_snapshot_id=continuation['current']['observation']['snapshot_id'],
                    current_coins=continuation['current_coins'], input_resent=False)
        except Exception as error:
            evidence_errors.append(_evidence_error('reward_recovery', error))
    if evidence_errors and item.get('status') == 'returned':
        item.update(status='refused', error='manual-step evidence is incomplete; inspect this original step ID')


def _save_result(worker, path, item, primary_error):
    """Try both result locations; notification/archiving never erases the cause."""
    r = _runner()
    archive = worker.records / ('manual-step-' + item['step_id'] + '.json')
    saved, failures = [], []

    def failed(stage, error):
        failures.append(_evidence_error(stage, error))
        item['finalization_errors'] = failures
        if item.get('status') == 'returned':
            item.update(status='refused', error='manual-step result finalization is incomplete; do not replay')

    for stage, target in (('mailbox', path), ('archive', archive)):
        try:
            worker.c.write_json(target, r.redact(item) if target == archive else item)
            saved.append(target)
        except Exception as error:
            failed(stage, error)
    try:
        worker.publish(control_mode='manual', phase='有界手操步骤已回传，等待当前复核或明确继续',
                       reason=item.get('error'))
    except Exception as error:
        failed('notification', error)
    if failures:
        # One bounded metadata update carries secondary failures to every
        # location that worked. It never recreates a queued step or input.
        for target in saved:
            try:
                worker.c.write_json(target, r.redact(item) if target == archive else item)
            except Exception as error:
                failed('finalization_update', error)
        if not saved:
            # Both writes failed; the original running marker remains a no-
            # replay fence. Keep the real operation error in the raised chain.
            message = 'manual-step result could not be saved; original step remains pending; never replay'
            if primary_error is not None:
                message = str(primary_error) + '; ' + message
            raise RuntimeError(message) from primary_error


def process(worker):
    """Called ONLY by the existing Worker's manual branch. No extra process."""
    r = _runner()
    directory = Path(worker.run) / 'manual-steps'
    paths = list(directory.glob('*.json'))
    if len(paths) > 128:
        raise ValueError('manual-step mailbox exceeded capacity')
    queued = [(p, r.entry.read_json(p)) for p in paths]
    queued = [(p, value) for p, value in queued if value.get('status') == 'queued']
    if not queued:
        return False
    if len(queued) != 1:
        raise ValueError('more than one manual step queued; no guessed ordering')
    path, item = queued[0]
    # The caller may inspect while paused. A physical step waits for the
    # existing explicit broker handoff; this helper never performs one.
    operation = item['payload']['operation']
    state = worker.c.status()
    if operation not in ('inspect', 'recover_reward') and (state['paused'] or state['input_halted'] or not state['game_foreground']):
        if datetime.now(timezone.utc) < datetime.fromisoformat(item['expires_at']):
            return False
    with r.file_lock(worker.run, 'manual-checkpoint.lock', timeout=2):
        current = r.entry.read_json(path)
        if current != item:
            return False
        if datetime.now(timezone.utc) >= datetime.fromisoformat(item['expires_at']):
            item.update(status='refused', error='manual-step deadline expired before execution')
            worker.c.write_json(path, item)
            return True
        binding, unused_records = r._manual_binding(worker.run, worker.owner, worker.c,
                                                   item['binding']['manual_id'])
        if binding != item['binding'] or binding['match_id'] != worker.active_match_id:
            item.update(status='refused', error='manual identity changed before execution')
            worker.c.write_json(path, item)
            return True
        if operation != 'inspect':
            _checkpoint(worker.run, binding, item['payload']['checkpoint_id'])
        item.update(status='running', started_at=r.now())
        worker.c.write_json(path, item)  # Claimed BEFORE any capture or input.
    before_ids, primary_error, evidence_errors = None, None, []
    execution_stage = 'prior_receipts'
    worker.manual_step_context = copy.deepcopy(item)
    try:
        # Reconcile the original receipt before any new capture can replace
        # its shared result notification. This wait never sends input.
        r._drain_manual_receipts(worker.run, worker.c)
        execution_stage = 'before_watermark'
        before_ids = _watermark(worker)
        execution_stage = 'execution'
        if operation in ('collect_rewards', 'recover_reward'):
            blocked = unknown_receipts(worker.run, worker.c)
            if blocked:
                item['prior_unknown_receipt_ids'] = blocked
                raise ValueError('original reward input outcome is unknown; reconcile its ID before any new capture')
        with worker.profile_span('manual_worker_step', operation='rules', business_step='manual_step'):
            if operation == 'reviewed_plan':
                reply = item['payload']['reply']
                actions = reply.get('actions', [])
                if reply.get('context_update') or not isinstance(actions, list) or len(actions) != 1:
                    raise ValueError('manual plan is one existing guarded action; budgets/reviews use the ordinary current decision')
                if unknown_receipts(worker.run, worker.c):
                    raise ValueError('original input outcome is unknown; only inspect or emergency controls may continue')
                if not worker.state.get('decision_request'):
                    raise ValueError('manual plan has no current Worker request; inspect first')
                worker.execute_plan(reply)
                _ask_current(worker)
            elif operation == 'recover_reward':
                worker.recover_reward(item['payload']['reply']['reward_recovery'])
                _ask_current(worker)
            elif operation == 'collect_rewards' and _reward_roi(operation, item['payload']['reply']) is not None:
                evidence = item['payload']['reply']['reward_roi']
                request = worker.state.get('decision_request')
                intent = validate_reward_roi(evidence, request, binding, item['payload']['checkpoint_id'])
                if intent != item.get('reward_roi_intent'):
                    raise ValueError('single reward ROI intent changed before execution')
                # The Worker holds its existing observe/input lease and checks
                # this original source again immediately before publication.
                worker.collect_reward_roi(evidence)
                if worker.state.get('decision_request') == request:
                    _ask_current(worker)
            else:
                worker.observe(scope='rewards' if operation == 'collect_rewards' else 'full')
                if operation != 'inspect':
                    if unknown_receipts(worker.run, worker.c):
                        raise ValueError('original input outcome is unknown; only inspect or emergency controls may continue')
                    if operation == 'collect_rewards':
                        if r.canonical_stage(worker.last_observation.get('fields', {}).get('stage')) != binding['stage']:
                            raise ValueError('native stage changed; use explicit current-stage bridge, no old reward coordinates')
                        worker.preparation_scope = (worker.active_match_id, binding['stage'], worker.epoch())
                        worker.preparation_reviews = {}
                        worker.economy_binding = None
                        worker.context['economy_plan'] = None
                        worker.state['decision_request'] = None
                        worker.advance_rewards(worker.last_observation)
                    elif operation == 'recover_overlay':
                        if worker.last_observation['page'] != 'reward_overlay':
                            raise ValueError('recovery is only the currently recognized reward overlay; unknown target returned')
                        worker.command(['key:27', 'wait:0.7'], '单次关闭当前奖励模态并回读',
                                       'reward_overlay', action={'type': 'key', 'args': [27],
                                       'expected_page': 'reward_overlay', 'purpose': 'recovery'})
                        _ask_current(worker)
                else:
                    _ask_current(worker)
        item.update(status='returned', finished_at=r.now())
    except Exception as exc:
        # This is a step result, NEVER proof that its physical input was zero.
        primary_error = exc
        if execution_stage != 'execution':
            evidence_errors.append(_evidence_error(execution_stage, exc))
        item.update(status='refused', error=str(exc), finished_at=r.now())
    finally:
        worker.manual_step_context = None
        _finish_result(worker, item, before_ids, evidence_errors)
        _save_result(worker, path, item, primary_error)
    return True
