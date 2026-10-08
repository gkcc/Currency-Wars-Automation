"""Explicit, read-only manual stage rebinding through the existing broker.

A bridge identifies the currently observed node. It does not establish any
historical action effect, clear rewards, approve input, or resume the worker.
Original receipts, unknown outcomes and economic spending remain untouched.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import uuid


SCHEMA = 'manual-stage-bridge/v1'
FILE = 'manual-stage-bridge.json'
LIMIT = 4096
IDENTITY_KEYS = ('run_id', 'match_id', 'manual_id', 'old_epoch')
HUD = {
    'preparation': ((410, 20, 550, 66), (410, 50, 550, 110)),
    'shop': ((220, 40, 365, 92), (225, 80, 355, 140)),
}


class StageObservationDeferred(ValueError):
    """The authenticated new-epoch frame needs another read, not a takeover."""
    can_reobserve = True
    input_authorized = False


def _runner():
    import currency_wars_runner
    return currency_wars_runner


def _native_stage(observed):
    r = _runner()
    page = observed.get('page')
    stage = r.canonical_stage(observed.get('fields', {}).get('stage'))
    if page not in HUD or not stage:
        raise ValueError('节点桥须当前备战/商店原生节点HUD；未知或过渡页不能重绑定')
    anchors = []
    for text, region in zip(('备战阶段', stage), HUD[page]):
        matches = []
        for row in observed.get('rows', []):
            if not isinstance(row, dict):
                continue
            box, confidence = row.get('box'), row.get('confidence')
            if (isinstance(row.get('text'), str) and r.clean(row['text']) == text
                    and type(confidence) in (int, float) and .90 <= confidence <= 1
                    and isinstance(box, (list, tuple)) and len(box) == 4
                    and all(type(value) in (int, float) and math.isfinite(value) for value in box)
                    and region[0] <= box[0] < box[2] <= region[2]
                    and region[1] <= box[1] < box[3] <= region[3]):
                matches.append({'text': text, 'box': list(box), 'confidence': confidence})
        if len(matches) != 1:
            raise ValueError('节点桥缺少当前唯一高置信原生标题/节点锚点')
        anchors.extend(matches)
    return {'page': page, 'stage': stage, 'anchors': anchors}


def _ledger(run, control):
    r = _runner()
    paths = list((Path(run) / 'request-ledger').glob('*.json'))
    if len(paths) > LIMIT:
        raise ValueError('节点桥回执超过有界容量；不截断未知请求')
    receipts, states = {}, {}
    for path in paths:
        raw = r.entry.read_json(path)
        rid = raw.get('id')
        if (not isinstance(rid, str) or not rid
                or path.name != hashlib.sha256(rid.encode()).hexdigest() + '.json'
                or rid in receipts):
            raise ValueError('节点桥原回执路径/身份不符')
        try:
            current = r.await_existing_receipt(run, control, rid, 0)
        except TimeoutError:
            current = raw
        # await_existing_receipt validates ownership even for pending entries.
        receipts[rid] = r.redact(current)
        states[rid] = r.manual_receipt_state(current)
    return receipts, states


def _blocked(states):
    return sorted(rid for rid, state in states.items() if state['unknown_input'])


def business_pending(run, owner, control, match_id, records=None, *, retained=(), receipts=None):
    """Read unresolved *business effects* across nodes of this owned match.

    A completed delivery does not clear these records. Retained bridge entries
    remain unresolved even if their old source disappears or changes; only the
    runner's separate business reconciliation may discharge that obligation.
    This function never edits a ledger or guesses an actual expenditure.
    """
    r = _runner()
    run = Path(run)
    if records is None:
        records, unused = r._manual_records(run, owner)
    records = Path(records)
    if receipts is None:
        receipts, unused = _ledger(run, control)
    paths = [('economy', path) for path in records.glob('economy-*.json')]
    paths += [('reward_step', path) for path in records.glob('reward-step-*.json')]
    paths += [('deployment', path) for path in records.glob('deployment-step-*.json')]
    paths += [(kind, path) for kind, path in (
        ('reward_step', run / 'reward-step.json'), ('reward_capacity', run / 'reward-capacity.json'),
        ('deployment', run / 'deployment-step.json'))
        if path.exists()]
    if len(paths) > LIMIT or not isinstance(retained, (list, tuple)) or len(retained) > LIMIT:
        raise ValueError('节点桥业务待验记录超出有界容量；不截断')
    found = {}

    def add(value):
        if (not isinstance(value, dict) or value.get('origin_run_id') != owner['run_id']
                or value.get('match_id') != match_id or value.get('effect_pending') is not True
                or value.get('source_kind') not in ('economy', 'reward_step', 'reward_capacity', 'deployment')
                or not isinstance(value.get('record_files'), list)):
            raise ValueError('节点桥保留的业务待验归属/结构不符')
        identity = tuple(value.get(key) for key in ('source_kind', 'origin_run_id', 'match_id',
            'request_id', 'kind', 'stage', 'resume_epoch', 'step_id'))
        if identity in found:
            saved = found[identity]
            saved['record_files'] = sorted(set(saved['record_files']) | set(value['record_files']))
            saved['retained_from_bridge'] |= value.get('retained_from_bridge', False)
        else:
            found[identity] = {**value, 'record_files': list(value['record_files']),
                              'retained_from_bridge': value.get('retained_from_bridge', False)}

    for item in retained:
        add({**item, 'retained_from_bridge': True})
    for source_kind, path in paths:
        data = path.read_bytes()
        value = json.loads(data.decode('utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('节点桥业务记录结构损坏')
        if not isinstance(value.get('match_id'), str) or not value['match_id']:
            raise ValueError('节点桥业务记录缺少所属局；不能当没有pending')
        if value.get('match_id') != match_id:
            continue  # An old match does not become an obligation of this one.
        if value.get('run_id', owner['run_id']) != owner['run_id']:
            raise ValueError('节点桥业务待验记录不属于当前run')
        if source_kind == 'economy':
            stage = r.canonical_stage(value.get('stage'))
            expected = 'economy-' + hashlib.sha256((match_id + ':' + str(stage)).encode()).hexdigest()[:24] + '.json'
            if (value.get('schema') != r.economy.SCHEMA or not stage or path.name != expected
                    or not isinstance(value.get('ledger'), dict)):
                raise ValueError('节点桥经济台账的schema/节点/路径不符')
            pending = value['ledger'].get('pending')
            if pending is None:
                continue
            if not isinstance(pending, dict):
                raise ValueError('节点桥经济pending结构不符')
            kind, rid = pending.get('kind'), pending.get('request_id')
            spent_before = value['ledger'].get('spent')
        elif source_kind == 'deployment':
            stage = r.canonical_stage(value.get('stage'))
            step_id = value.get('step_id')
            if (value.get('schema') != 'currency-wars-deployment-step/v1' or not stage
                    or not isinstance(step_id, str) or not 1 <= len(step_id) <= 100
                    or value.get('kind') != 'deploy_unit' or value.get('run_id') != owner['run_id']
                    or path.is_symlink() or (path != run / 'deployment-step.json'
                        and path.name != 'deployment-step-' + step_id + '.json')):
                raise ValueError('节点桥部署记录的schema/节点/步骤/路径不符')
            # A claimed terminal status is not proof. Scan pointer and archive
            # together, then discharge only their bound original result below.
            pending, kind, rid, spent_before = value, 'deploy_unit', value.get('request_id'), None
        else:
            final = ('verified', 'refused', 'superseded') if source_kind == 'reward_capacity' else ('verified', 'refused')
            if value.get('status') in final:
                continue
            pending = value
            stage = r.canonical_stage(value.get('stage') if source_kind == 'reward_capacity'
                                      else value.get('before', {}).get('stage'))
            kind = 'reward_capacity' if source_kind == 'reward_capacity' else value.get('kind')
            rid = value.get('input_request_id') if source_kind == 'reward_capacity' else value.get('request_id')
            spent_before = None
        receipt = receipts.get(rid)
        request = receipt.get('request') if receipt else None
        delivery = r.manual_receipt_state(receipt) if receipt else None
        add({'source_kind': source_kind, 'origin_run_id': owner['run_id'], 'match_id': match_id,
            'request_id': rid, 'kind': kind, 'stage': stage, 'resume_epoch': pending.get('resume_epoch'),
            'step_id': pending.get('step_id'), 'effect_pending': True,
            'publication_attempted': pending.get('publication_attempted', pending.get('request_published')),
            'planned_cost_not_actual': pending.get('cost'), 'record_files': [str(path)],
            'record_sha256': hashlib.sha256(data).hexdigest(), 'receipt_available': receipt is not None,
            'receipt_sha256': _json_sha(receipt) if receipt is not None else None,
            'request_sha256': hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if request is not None else None,
            'request_actions_match': request.get('actions') == pending.get('broker_actions')
                if request is not None and 'broker_actions' in pending else None,
            'delivery_state': delivery['state'] if delivery else 'missing',
            'business_outcome': pending.get('outcome', 'unverified'), 'spent_before': spent_before})
    values = list(found.values())
    deployments = pending_remaining([item for item in values if item['source_kind'] == 'deployment'],
        run=run, owner=owner, control=control, records=records)
    return sorted([item for item in values if item['source_kind'] != 'deployment'] + deployments,
        key=lambda item: tuple(str(item.get(key)) for key in
            ('source_kind', 'stage', 'request_id', 'kind', 'step_id')))


def _business_ids(items):
    return {item['request_id'] for item in items if isinstance(item.get('request_id'), str)}


def _json_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _recent_business_events(records):
    """Only the bounded tail is needed; a missing old event leaves its debt."""
    path = records / 'journal.jsonl'
    if not path.exists():
        return []
    with path.open('rb') as stream:
        start = max(0, path.stat().st_size - 8 * 1024 * 1024)
        stream.seek(start)
        if start:
            stream.readline()  # Drop only a potentially partial first line.
        data = stream.read(8 * 1024 * 1024)
    events = []
    for line in data.splitlines():
        try:
            item = json.loads(line)
            if isinstance(item, dict) and item.get('event') in ('economic_effect_verified', 'reward_capacity_reconciled'):
                events.append(item)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue  # An incomplete write supplies no resolution proof.
    return events


def _deployment_result(values, item, receipt, delivery, *, run, control, records):
    """Consume the Worker's saved rule result and its original frame bindings.

    Re-evaluate the persisted native reading with the production rule; do not
    start another perception pass or input. A changed pointer never replaces
    the original deployment archive.
    """
    r = _runner()
    actions = receipt['request'].get('actions')
    if not all(value.get('receipt') == receipt['result'] and value.get('broker_actions') == actions
               for value in values):
        return False
    if all(value.get('status') == 'refused' and value.get('publication_attempted') is False
           for value in values):
        return delivery['state'] == 'zero_input'
    if (delivery['state'] != 'completed' or receipt['result'].get('ok') is not True
            or receipt['request'].get('handoff') is not False
            or not isinstance(actions, list)
            or sum(action.get('type') == 'drag' for action in actions) != 1
            or any(action.get('type') not in ('drag', 'wait') for action in actions)
            or not all(value.get('status') == 'verified' and value.get('outcome') == 'deployed'
                and value.get('publication_attempted') is True
                and value.get('receipt') == receipt['result'] and value.get('broker_actions') == actions
                for value in values)):
        return False
    bindings = ('spec', 'before', 'before_png', 'before_snapshot_id', 'before_capture_request_id',
        'before_frame_id', 'after_png', 'after_snapshot_id', 'after_capture_request_id', 'after_frame_id',
        'after_read', 'after_result')
    if (not isinstance(values[0].get('spec'), dict) or not values[0]['spec']
            or any(any(value.get(key) != values[0].get(key) for key in bindings) for value in values[1:])):
        return False
    for value in values:
        import currency_wars_deployment as deployment
        after_read, prior = value.get('after_read'), value.get('before')
        if (not isinstance(prior, dict) or prior.get('plan') != value['spec']
                or not isinstance(after_read, dict)
                or any(after_read.get(key) != value.get('after_' + target) for key, target in
                    (('snapshot_id', 'snapshot_id'), ('capture_request_id', 'capture_request_id'),
                     ('frame_id', 'frame_id')))):
            return False
        result = deployment.after(after_read, value['spec'], prior)
        if result.get('status') != 'verified' or result != value.get('after_result'):
            return False
        for prefix in ('before', 'after'):
            path = Path(value.get(prefix + '_png', ''))
            digest = value.get(prefix + '_snapshot_id')
            if path.is_symlink() or path.parent.resolve() != records.resolve():
                return False
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != digest:
                return False
            from PIL import Image
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != 'PNG' or image.size != (1920, 1080):
                    return False
                image.verify()
            capture_id = value.get(prefix + '_capture_request_id')
            captured = r.redact(r.await_existing_receipt(run, control, capture_id, 0))
            frame = (captured.get('result') or {}).get('observation') or {}
            captured_state = r.manual_receipt_state(captured)
            if (frame.get('frame_protocol') != 1 or frame.get('request_id') != capture_id
                    or frame.get('frame_id') != value.get(prefix + '_frame_id')
                    or not frame.get('frame_id') or frame.get('snapshot_sha256') != digest
                    or captured_state['unknown_input'] or captured['result'].get('ok') is not True
                    or captured['request'].get('handoff') is not False):
                return False
            if prefix == 'before':
                if (capture_id == item['request_id'] or not isinstance(value.get('before'), dict)
                        or not {'snapshot_id', 'stage', 'occupied', 'capacity', 'source', 'target', 'layout'}
                            <= set(value['before'])
                        or value['before'].get('snapshot_id') != digest
                        or r.canonical_stage(value['before'].get('stage')) != item['stage']):
                    return False
            elif capture_id == item['request_id']:
                if captured != receipt:
                    return False
            elif (captured['request'].get('actions') != [{'type': 'observe', 'args': []}]
                    or captured_state['state'] != 'zero_input'):
                return False
    return True


def pending_remaining(entries, *, run, owner, control, records=None):
    """Recheck only retained sources, allowing their actual business closure.

    Completion receipts alone never discharge a debt. Economy needs its exact
    effect journal and cumulative spending; rewards need the same step's
    verified postframe. Missing/reused sources keep the original entry.
    """
    r = _runner()
    run = Path(run)
    if records is None:
        records, unused = r._manual_records(run, owner)
    records = Path(records)
    if not isinstance(entries, list) or len(entries) > LIMIT:
        raise ValueError('节点桥待验小集无效或超限')
    remaining, events = [], None
    for item in entries:
        if (not isinstance(item, dict) or item.get('origin_run_id') != owner['run_id']
                or item.get('effect_pending') is not True or not isinstance(item.get('record_files'), list)
                or not 1 <= len(item['record_files']) <= 8):
            raise ValueError('节点桥待验原归属/固定来源无效')
        try:
            receipt = r.await_existing_receipt(run, control, item.get('request_id'), 0)
            receipt = r.redact(receipt)
            delivery = r.manual_receipt_state(receipt)
            if (not item.get('receipt_sha256') or _json_sha(receipt) != item['receipt_sha256']
                    or _json_sha(receipt['request']) != item.get('request_sha256') or delivery['unknown_input']):
                raise ValueError('原输入回执缺失、改变或仍未知')
            values = []
            filenames = list(item['record_files'])
            if item['source_kind'] == 'deployment':
                archive = records / ('deployment-step-' + str(item.get('step_id')) + '.json')
                pointer = run / 'deployment-step.json'
                filenames = sorted(set(filenames) | {str(archive)}
                    | ({str(pointer)} if pointer.exists() else set()))
                if len(filenames) > 8:
                    raise ValueError('部署结果固定来源超限')
            for filename in filenames:
                path = Path(filename)
                if (path.parent.resolve() not in (records.resolve(), run.resolve())
                        or item['source_kind'] == 'deployment' and path.is_symlink()):
                    raise ValueError('原业务来源不属于当前记录目录')
                value = r.entry.read_json(path)
                rid = value.get('input_request_id') if item['source_kind'] == 'reward_capacity' else value.get('request_id')
                if (item['source_kind'] == 'reward_step' and path == run / 'reward-step.json'
                        and rid != item['request_id'] and len(item['record_files']) > 1):
                    continue  # A new pointer cannot erase the original archive.
                if (item['source_kind'] == 'deployment' and path == run / 'deployment-step.json'
                        and rid != item['request_id'] and value.get('step_id') != item.get('step_id')):
                    continue  # The exact original archive remains mandatory.
                stage = value.get('stage') if item['source_kind'] != 'reward_step' else value.get('before', {}).get('stage')
                if (value.get('run_id', owner['run_id']) != owner['run_id']
                        or value.get('match_id') != item['match_id'] or r.canonical_stage(stage) != item['stage']):
                    raise ValueError('原业务记录局/节点已改变')
                if item['source_kind'] != 'economy' and (
                        rid != item['request_id'] or value.get('resume_epoch') != item['resume_epoch']
                        or item['source_kind'] in ('reward_step', 'deployment') and
                            (value.get('step_id') != item.get('step_id') or value.get('kind') != item['kind'])):
                    raise ValueError('原业务请求/动作类型/步骤身份改变')
                if (item['source_kind'] == 'deployment' and
                        (value.get('schema') != 'currency-wars-deployment-step/v1'
                         or path != run / 'deployment-step.json'
                            and path != records / ('deployment-step-' + item['step_id'] + '.json'))):
                    raise ValueError('部署结果原始schema/归档路径不符')
                values.append(value)
            if not values:
                raise ValueError('没有原业务终态来源')
            resolved = False
            if item['source_kind'] == 'economy' and len(values) == 1:
                value = values[0]
                ledger, before = value.get('ledger') or {}, item.get('spent_before')
                if (value.get('schema') == r.economy.SCHEMA and ledger.get('pending', 'missing') is None
                        and isinstance(before, dict) and item['kind'] in ('purchase', 'refresh', 'experience')):
                    if events is None:
                        events = _recent_business_events(records)
                    for event in events:
                        spent, total = event.get('observed_spent'), event.get('actual_spent')
                        if (event.get('event') == 'economic_effect_verified' and event.get('run_id') == owner['run_id']
                                and event.get('request_id') == item['request_id'] and event.get('stage') == item['stage']
                                and event.get('kind') == item['kind'] and event.get('outcome') in ('success', 'zero', 'partial')
                                and event.get('input_resent') is False and type(spent) is int and spent >= 0
                                and (event['outcome'] != 'zero' or spent == 0)
                                and isinstance(total, dict) and total == ledger.get('spent')
                                and set(before) == set(total) == {'purchase', 'refresh', 'experience'}
                                and all(type(before[k]) is int and type(total[k]) is int and before[k] >= 0
                                    and total[k] == before[k] + (spent if k == item['kind'] else 0) for k in before)):
                            resolved = True
            elif item['source_kind'] == 'reward_step':
                # A same-ID pointer still pending (or reused for another step)
                # vetoes an older verified archive. Never merge their effects.
                if all(value.get('status') == 'refused' and value.get('publication_attempted') is False for value in values):
                    resolved = delivery['state'] == 'zero_input'
                elif all(value.get('status') == 'verified' and value.get('outcome') ==
                         ('shop_collapsed' if item['kind'] == 'close_shop' else 'one_visible_orb_removed')
                         and value.get('all_rewards_cleared') is None
                         and value.get('receipt') == receipt['result'] for value in values):
                    resolved = delivery['state'] == 'completed'
                    for value in values:
                        path = Path(value.get('after_png', ''))
                        after = r.await_existing_receipt(run, control, value.get('after_capture_request_id'), 0)
                        frame = (after.get('result') or {}).get('observation') or {}
                        resolved &= (path.parent.resolve() == records.resolve()
                            and hashlib.sha256(path.read_bytes()).hexdigest() == value.get('after_snapshot_id')
                            and frame.get('frame_protocol') == 1 and frame.get('request_id') == after['id']
                            and frame.get('snapshot_sha256') == value.get('after_snapshot_id')
                            and not r.manual_receipt_state(after)['unknown_input'])
            elif item['source_kind'] == 'reward_capacity' and len(values) == 1:
                value = values[0]
                if (value.get('status') in ('verified', 'superseded') and value.get('receipt') == receipt
                        and isinstance(value.get('after'), dict)):
                    if events is None:
                        events = _recent_business_events(records)
                    resolved = any(event.get('event') == 'reward_capacity_reconciled'
                        and event.get('run_id') == owner['run_id'] and event.get('input_request_id') == item['request_id']
                        and event.get('resolution') == value['status'] and event.get('sale_outcome') == value.get('sale_outcome')
                        and event.get('snapshot_id') == value['after'].get('proof', {}).get('snapshot_id')
                        and event.get('input_resent') is False for event in events)
            elif item['source_kind'] == 'deployment':
                resolved = _deployment_result(values, item, receipt, delivery,
                    run=run, control=control, records=records)
            if not resolved:
                raise ValueError('缺少原请求的已核业务终态；交付完成不能代替效果')
        except (OSError, ValueError, TimeoutError, TypeError, KeyError, AttributeError, SyntaxError):
            remaining.append(item)
    return remaining


def _capture(run, control, records, reader, label):
    r = _runner()
    rid = uuid.uuid4().hex
    with r.entry.submission_lease(control) as leased:
        result = r.entry.request(leased, 'actions', ['observe'], rid, False)
        if not result.get('ok') or not result.get('observation'):
            raise r.entry.ObservationUnavailable('节点桥纯观察缺少同请求不可变原帧；不重发输入')
        if (r.optional(Path(run) / 'result.json') or {}).get('id') != rid:
            raise ValueError('节点桥观察后已有新请求；不能沿用旧水位')
        data = r.entry.observation_frame(run, result).read_bytes()
        receipts, states = _ledger(run, leased)
    target = records / (label + '-' + rid + '.png')
    with target.open('xb') as stream:
        stream.write(data)
    try:
        # The existing narrow reward scope includes native HUD/page OCR.
        # Stage continuity does not require a full shop or roster read.
        observed = reader.read(target, scope='rewards')
        digest = hashlib.sha256(data).hexdigest()
        if observed.get('snapshot_id') != digest:
            raise ValueError('节点桥读取与原PNG摘要不符')
        native = _native_stage(observed)
        frame = result['observation']
        return {'receipt_id': rid, 'frame_id': frame['frame_id'], 'snapshot_id': digest,
                'captured_at': frame['captured_at'], 'observed_at': r.now(),
                'evidence_file': str(target), 'native': native,
                'receipt_watermark': sorted(receipts)}, receipts, states
    finally:
        try:
            r.entry.release_observation(run, result)
        except (OSError, ValueError):
            pass  # The archived original stays available; no input is resent.


def _same_layout(first, second):
    if (first['receipt_id'] == second['receipt_id'] or first['frame_id'] == second['frame_id']
            or first['native']['page'] != second['native']['page']
            or first['native']['stage'] != second['native']['stage']
            or any(a['text'] != b['text'] or any(abs(x-y) > 2 for x, y in zip(a['box'], b['box']))
                   for a, b in zip(first['native']['anchors'], second['native']['anchors']))):
        raise ValueError('节点桥两次当前观察的页面/节点/锚点尚未稳定；停止，不发布输入')
    start, end = (datetime.fromisoformat(item['captured_at']) for item in (first, second))
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError('节点桥两张原帧时序不符')


def _save_new(path, value):
    import json
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def _verify_capture(frame, records, receipts, states):
    path = Path(frame['evidence_file'])
    receipt = receipts.get(frame['receipt_id'], {})
    request, result = receipt.get('request', {}), receipt.get('result') or {}
    observation = result.get('observation') or {}
    if (path.parent.resolve() != records.resolve()
            or hashlib.sha256(path.read_bytes()).hexdigest() != frame['snapshot_id']
            or request.get('actions') != [{'type': 'observe', 'args': []}]
            or request.get('handoff') is not False or result.get('ok') is not True
            or states.get(frame['receipt_id'], {}).get('state') != 'zero_input'
            or observation.get('frame_protocol') != 1
            or observation.get('request_id') != frame['receipt_id']
            or observation.get('frame_id') != frame['frame_id']
            or observation.get('captured_at') != frame['captured_at']
            or observation.get('snapshot_sha256') != frame['snapshot_id']):
        raise ValueError('节点桥原观察的PNG/请求/不可变帧身份不符')


def create(run, owner, control, manual_id, reader=None):
    """Record two current native HUD observations; no phase/input approval."""
    r = _runner()
    run = Path(run)
    with r.file_lock(run, 'manual-checkpoint.lock', timeout=5):
        binding, records = r._manual_binding(run, owner, control, manual_id, resolve_stage=False)
        previous = r.optional(run / FILE)
        # An unfinished publisher may still own result.json. Do not overwrite
        # its only late result with a new observation. The existing drain folds
        # only the exact original terminal result before releasing its lease.
        r._drain_manual_receipts(run, control)
        before, before_states = _ledger(run, control)
        if any(receipt.get('result') is None for receipt in before.values()):
            raise TimeoutError('节点桥仍有非终态原请求；保持pending，不发新观察')
        before_business = business_pending(run, owner, control, binding['match_id'], records, receipts=before)
        bridge_id = uuid.uuid4().hex
        reader = reader or r.Perception()
        first, unused, unused_states = _capture(run, control, records, reader, 'manual-stage-' + bridge_id + '-first')
        second, receipts, states = _capture(run, control, records, reader, 'manual-stage-' + bridge_id + '-second')
        _same_layout(first, second)
        new_stage = second['native']['stage']
        if tuple(map(int, new_stage.split('-'))) < tuple(map(int, binding['stage'].split('-'))):
            raise ValueError('较早节点不能当同局前进；需明确核对新局归属')
        if set(before) - set(receipts):
            raise ValueError('节点桥期间原回执缺失；不重绑定')
        added = set(receipts) - set(before)
        if any(states[rid]['state'] != 'zero_input' for rid in added):
            raise ValueError('节点桥两次观察之间出现可能输入；重新核对当前边界')
        for rid, saved in before.items():
            current = receipts[rid]
            if (saved['request'] != current['request']
                    or saved.get('result') is not None and saved != current):
                raise ValueError('节点桥期间原请求/已知结果改变；保持原未知')
        current, current_records = r._manual_binding(run, owner, control, manual_id, resolve_stage=False)
        if current != binding or current_records != records or r.optional(run / FILE) != previous:
            raise ValueError('节点桥提交前局/接管/旧节点/CAS已变；未覆盖')
        pending_business = business_pending(run, owner, control, binding['match_id'], records,
                                           retained=before_business, receipts=receipts)
        record = {'schema': SCHEMA, 'bridge_id': bridge_id, 'binding': binding,
            'from_stage': binding['stage'], 'to_stage': new_stage, 'created_at': r.now(),
            'observations': [first, second], 'prior_receipt_ids': sorted(before),
            'receipt_watermark': sorted(receipts), 'receipts': receipts,
            'receipt_states': states, 'initial_receipt_states': before_states,
            'business_pending': pending_business,
            'blocked_ids': sorted(set(_blocked(before_states)) | set(_blocked(states)) | _business_ids(pending_business)),
            'status': 'scope_rebound',
            'historical_transition_attribution': 'unverified', 'input_authorized': False,
            'completed_phases': [], 'all_rewards_cleared': None, 'input_resent': False}
        record['archive_file'] = str(records / ('manual-stage-' + bridge_id + '.json'))
        _save_new(Path(record['archive_file']), record)
        control.write_json(run / FILE, record)
        return record


def _verify_record(run, owner, control, records):
    r = _runner()
    bridge = r.optional(Path(run) / FILE)
    if bridge is None:
        return None
    if (bridge.get('schema') != SCHEMA or bridge.get('binding', {}).get('run_id') != owner['run_id']
            or bridge.get('from_stage') != bridge.get('binding', {}).get('stage')
            or not r.canonical_stage(bridge.get('to_stage'))
            or bridge.get('status') != 'scope_rebound' or bridge.get('input_authorized') is not False
            or bridge.get('completed_phases') != [] or bridge.get('all_rewards_cleared') is not None
            or bridge.get('historical_transition_attribution') != 'unverified'
            or not isinstance(bridge.get('business_pending'), list)):
        raise ValueError('节点桥结构/来源/作用域不符；不能作为完成批准')
    archive = Path(bridge.get('archive_file', ''))
    if archive.parent.resolve() != records.resolve() or r.entry.read_json(archive) != bridge:
        raise ValueError('节点桥持久原记录缺失/改变')
    frames = bridge.get('observations')
    if not isinstance(frames, list) or len(frames) != 2:
        raise ValueError('节点桥缺少两次独立当前观察')
    _same_layout(*frames)
    for frame in frames:
        native = frame['native']
        if _native_stage({'page': native['page'], 'fields': {'stage': native['stage']},
                          'rows': native['anchors']}) != native:
            raise ValueError('节点桥归档原生HUD锚点不完整')
    receipts, states = _ledger(run, control)
    if set(bridge.get('receipt_watermark', [])) != set(bridge.get('receipts', {})):
        raise ValueError('节点桥原水位和归档回执不符')
    if set(bridge['receipt_watermark']) - set(receipts):
        raise ValueError('节点桥原回执水位有缺失；不能忘记原pending')
    expected_blocked = sorted(set(_blocked(bridge.get('initial_receipt_states', {})))
                              | set(_blocked(bridge.get('receipt_states', {})))
                              | _business_ids(bridge['business_pending']))
    if bridge.get('blocked_ids') != expected_blocked:
        raise ValueError('节点桥原未知请求清单不符；不能丢弃原pending')
    for rid, saved in bridge['receipts'].items():
        current = receipts[rid]
        if (saved.get('request') != current.get('request')
                or saved.get('result') is not None and saved != current):
            raise ValueError('节点桥原请求/已知结果字节改变')
    for frame in frames:
        _verify_capture(frame, records, receipts, states)
        if frame['native']['stage'] != bridge['to_stage']:
            raise ValueError('节点桥原观察节点与目标节点不符')
    return bridge, receipts, states


def resolve_binding(run, owner, control, base_binding):
    """Resolve a manual checkpoint's current stage; never authorize its input."""
    r = _runner()
    saved = r.optional(Path(run) / FILE)
    if saved is None:
        return base_binding
    if any(base_binding.get(key) != saved.get('binding', {}).get(key) for key in IDENTITY_KEYS):
        return base_binding  # An unrelated historical bridge is not a live dependency.
    records, unused = r._manual_records(run, owner)
    bridge, unused_receipts, unused_states = _verify_record(run, owner, control, records)
    if base_binding.get('stage') not in (bridge['from_stage'], bridge['to_stage']):
        raise ValueError('节点桥与runner当前节点冲突；先观察当前边界')
    return {**base_binding, 'stage': bridge['to_stage']}


def _manual_covered_receipts(worker, bridge, receipts, states):
    """Admit only the complete original trace of a finished ManualPhase.

    This admits its known inputs to the scope fence. It does not import the
    phase's completion review into the worker's new epoch.
    """
    r = _runner()
    allowed = set()
    paths = list((worker.run / 'manual-results').glob('*.json'))
    if len(paths) > LIMIT:
        raise ValueError('节点桥人工阶段记录超出有界容量')
    expected = {**bridge['binding'], 'stage': bridge['to_stage']}
    for path in paths:
        item = r.entry.read_json(path)
        if item.get('binding') != expected or item.get('status') != 'completed':
            continue
        checkpoint = item.get('checkpoint_id')
        if (not isinstance(checkpoint, str) or path.name != checkpoint + '.json'
                or item.get('schema') != 'manual-preparation-result/v1'
                or item.get('receipt_protocol') != 2 or item.get('outcome') != 'success'
                or item.get('phase') not in r.coaching.PHASES
                or item.get('review', {}).get('phase') != item['phase']
                or item.get('review', {}).get('stage') != bridge['to_stage']
                or item.get('review', {}).get('completed') is not True
                or item.get('review', {}).get('reviewer') != 'supervising_agent'):
            raise ValueError('节点桥人工阶段来源/完成记录不符')
        archive = worker.records / ('manual-' + checkpoint + '.json')
        if r.entry.read_json(archive) != r.redact(item):
            raise ValueError('节点桥人工阶段持久记录缺失/改变')
        before, after = item.get('before', {}), item.get('after', {})
        _verify_capture(before, worker.records, receipts, states)
        _verify_capture(after, worker.records, receipts, states)
        prior = item.get('prior_receipt_ids')
        saved = item.get('input_receipts')
        if (not isinstance(prior, list) or prior != before.get('receipt_watermark')
                or not set(bridge['receipt_watermark']).issubset(prior)
                or not isinstance(saved, list) or len(saved) > 128
                or len({value.get('id') for value in saved}) != len(saved)
                or set(after.get('receipt_watermark', [])) - set(prior)
                    != {value.get('id') for value in saved} | {after['receipt_id']}):
            raise ValueError('节点桥人工阶段完整输入清单/前后水位不符')
        ordered = [datetime.fromisoformat(value) for value in
                   (before['captured_at'], before['observed_at'], after['captured_at'], after['observed_at'])]
        if any(value.tzinfo is None for value in ordered) or ordered != sorted(ordered):
            raise ValueError('节点桥人工阶段前后原帧时序不符')
        for value in saved:
            rid = value.get('id')
            if value != receipts.get(rid) or states.get(rid, {}).get('unknown_input') is not False:
                raise ValueError('节点桥人工原回执改变或含未知输入；不能用完成标记洗白')
            allowed.add(rid)
    return allowed


def verify_for_resume(worker, fresh):
    """Verify scope continuity after exact resume and a new read-only frame.

    The caller adopts only the returned stage and clears dynamic preparation
    proofs/budget. Spending and pending ledgers must remain in the worker.
    """
    r = _runner()
    saved = r.optional(worker.run / FILE)
    if saved is None:
        return None
    epoch = r.optional(worker.run / 'runner-resume-epoch.json') or {}
    if saved.get('binding', {}).get('manual_id') != epoch.get('consumed_manual_id'):
        return None
    bridge, receipts, states = _verify_record(worker.run, worker.owner, worker.c, worker.records)
    old = bridge['binding']
    status = worker.c.status()
    if (old.get('match_id') != worker.active_match_id or old.get('old_epoch') != epoch.get('previous_epoch')
            or worker.epoch() != epoch.get('id') or old.get('old_epoch') == epoch.get('id')
            or r.manual_state(worker.run) or not status.get('ready') or status.get('paused')
            or status.get('input_halted') or status.get('game_foreground') is not True
            or (worker.run / 'runner-stop').exists() or (worker.run / 'broker-stop').exists()):
        raise ValueError('节点桥的新epoch/接管/所属局或输入健康状态已变')
    event, unused = r.verified_resume_event(worker.run, worker.owner, worker.c, epoch)
    if event.get('match_id') != worker.active_match_id or event.get('stage') not in (bridge['from_stage'], bridge['to_stage']):
        raise ValueError('节点桥与原恢复CAS事件不符；不改写旧事件')
    rid = fresh.get('capture_request_id')
    receipt = receipts.get(rid, {})
    result, request = receipt.get('result') or {}, receipt.get('request') or {}
    frame = result.get('observation') or {}
    current = worker.last_observation or {}
    if (rid in bridge['receipt_watermark'] or request.get('actions') != [{'type': 'observe', 'args': []}]
            or request.get('handoff') is not False or result.get('ok') is not True
            or states.get(rid, {}).get('state') != 'zero_input'
            or frame.get('frame_protocol') != 1 or frame.get('request_id') != rid
            or frame.get('frame_id') != fresh.get('frame_id')
            or frame.get('snapshot_sha256') != fresh.get('snapshot_id')
            or frame.get('captured_at') != fresh.get('captured_at')
            or any(current.get(key) != fresh.get(key) for key in
                   ('snapshot_id', 'capture_request_id', 'frame_id', 'captured_at', 'page'))
            or (r.optional(worker.run / 'result.json') or {}).get('id') != rid
            or hashlib.sha256(worker.frame_path.read_bytes()).hexdigest() != fresh.get('snapshot_id')):
        raise ValueError('节点桥缺少新epoch同请求纯观察原帧')
    times = [datetime.fromisoformat(value) for value in (
        bridge['observations'][-1]['observed_at'], epoch['time'], fresh['captured_at'])]
    if (any(value.tzinfo is None for value in times) or times != sorted(times)
            or (datetime.now(timezone.utc) - times[0]).total_seconds() > 3600):
        raise ValueError('节点桥/恢复/当前观察时序不符或已过期')
    added = set(receipts) - set(bridge['receipt_watermark']) - {epoch['id']}
    covered = _manual_covered_receipts(worker, bridge, receipts, states)
    if any(states[rid]['state'] != 'zero_input' and rid not in covered for rid in added):
        raise ValueError('节点桥之后存在额外可能输入；当前阶段仍待新复核')
    pending_business = business_pending(worker.run, worker.owner, worker.c, worker.active_match_id,
        worker.records, retained=bridge['business_pending'], receipts=receipts)
    pending_business = pending_remaining(pending_business, run=worker.run, owner=worker.owner,
                                        control=worker.c, records=worker.records)
    # Authenticate receipts/PNG/epoch and the input fence BEFORE classifying
    # missing HUD as retryable. Source corruption must never become a defer.
    try:
        native = _native_stage(fresh)
    except ValueError as exc:
        raise StageObservationDeferred(str(exc)) from exc
    if native['stage'] != bridge['to_stage']:
        raise ValueError('新epoch已可靠读到其他节点；原节点桥不适用')
    return {'bridge_id': bridge['bridge_id'], 'stage': bridge['to_stage'],
            'from_stage': bridge['from_stage'], 'resume_epoch': epoch['id'],
            'fresh_receipt_id': rid, 'original_blocked_ids': bridge['blocked_ids'],
            'delivery_blocked_ids': _blocked(states), 'business_pending': pending_business,
            'blocked_ids': sorted(set(_blocked(states)) | _business_ids(pending_business)),
            'scope_only': True, 'input_authorized': False,
            'covered_manual_receipt_ids': sorted(covered & added),
            'completed_phases': [], 'all_rewards_cleared': None}
