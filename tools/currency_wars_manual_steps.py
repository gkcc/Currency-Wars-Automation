"""Bounded supervised steps performed by the already owning Worker.

This is a mailbox and intent adapter, not an input implementation or controller.
The existing Worker, Entry ledger, broker, ManualPhase and resume CAS stay in use.
"""
from __future__ import annotations

import copy
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCHEMA = 'manual-worker-step/v1'
OPERATIONS = ('inspect', 'collect_rewards', 'recover_overlay', 'reviewed_plan')


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


def submit(run, owner, control, *, manual_id, step_id, operation, checkpoint_id=None,
           reply=None, wait_seconds=25):
    """Queue once, or read the SAME step. A timeout never creates another job."""
    r = _runner()
    run, path = Path(run), _path(run, step_id)
    if operation not in OPERATIONS or type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 25:
        raise ValueError('unsupported or unbounded manual step')
    if (operation == 'reviewed_plan') != isinstance(reply, dict):
        raise ValueError('only reviewed_plan accepts the existing structured decision reply')
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
                if operation == 'collect_rewards' and checkpoint.get('phase') != 'rewards':
                    raise ValueError('native reward loop belongs only to the rewards checkpoint')
            path.parent.mkdir(exist_ok=True)
            existing = list(path.parent.glob('*.json'))
            if len(existing) >= 128:
                raise ValueError('manual-step mailbox reached its bounded capacity')
            if any(r.entry.read_json(p).get('status') in ('queued', 'running') for p in existing):
                raise ValueError('a manual-step is already pending; inspect that original ID')
            item = dict(schema=SCHEMA, step_id=step_id, run_id=owner['run_id'], binding=binding,
                        payload=payload, status='queued', created_at=r.now(),
                        expires_at=(datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
                        input_resent=False)
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
                    or result.get('deployment_effect_pending') is True
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
            if item['payload']['operation'] == 'collect_rewards' and checkpoint.get('phase') != 'rewards':
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
        all_rewards_cleared=None, automatic_phase_completion=False, input_resent=False)
    if callable(getattr(worker, 'deployment_summary', None)):
        try:
            deployed = worker.deployment_summary()
            item['result'].update(deployment_result=deployed,
                deployment_effect_pending=bool(deployed and deployed['effect_pending']))
        except Exception as error:
            item['result']['deployment_effect_pending'] = True
            evidence_errors.append(_evidence_error('deployment_result', error))
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
    if operation != 'inspect' and (state['paused'] or state['input_halted'] or not state['game_foreground']):
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
        with worker.profile_span('manual_worker_step', operation='rules', business_step='manual_step'):
            if operation == 'reviewed_plan':
                reply = item['payload']['reply']
                actions = reply.get('actions', [])
                if reply.get('context_update') or not isinstance(actions, list) or len(actions) != 1:
                    raise ValueError('manual plan is one existing guarded action; budgets/reviews use the ordinary current decision')
                if unknown_receipts(worker.run, worker.c) and actions[0].get('type') != 'check_deployment':
                    raise ValueError('original input outcome is unknown; only inspect or emergency controls may continue')
                if not worker.state.get('decision_request'):
                    raise ValueError('manual plan has no current Worker request; inspect first')
                original_request = worker.state['decision_request']['request_id']
                worker.execute_plan(reply)
                # A bounded semantic step may already return one current ROOT
                # request. Do not clear it and add a second tool boundary.
                if (worker.state.get('decision_request') or {}).get('request_id') in (None, original_request):
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
