"""One named bench-to-empty-slot result contract; no capture or input APIs.

Only current native roster pixels and the independent population HUD can close
deployment. The existing gear reader has no equipped-owner source; this module
does not close equipment or battle acceptance.
"""
from __future__ import annotations

import copy
import re

from currency_wars_coaching import deployment_position
from currency_wars_perception import clean, native_deployed_count
from currency_wars_state_reader import SCHEMA, native_slots


class DeploymentRejected(ValueError):
    """Source, intent, page or layout changed; discard unexecuted coordinates."""


class DeploymentUnreadable(ValueError):
    """Result remains unknown; bounded observation is allowed, input replay is not."""


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _score(value, minimum=0., maximum=1.):
    return type(value) in (int, float) and minimum <= value <= maximum


def _project_slot(slot):
    result = {key: copy.deepcopy(slot[key]) for key in ('location', 'row', 'slot', 'bounds', 'point',
        'snapshot_id', 'origin', 'status', 'name', 'position', 'confidence', 'reasons') if key in slot}
    evidence = slot.get('evidence')
    result['evidence'] = ({key: copy.deepcopy(evidence[key]) for key in ('method', 'empty_score',
        'badge_score', 'identity_score', 'identity_margin', 'identity') if key in evidence}
        if isinstance(evidence, dict) else copy.deepcopy(evidence))
    return result


def project(observed, plan=None):
    """Preserve current evaluable evidence, not derived success or unrelated text."""
    result = {key: copy.deepcopy(observed[key]) for key in ('page', 'snapshot_id',
        'capture_request_id', 'frame_id', 'captured_at') if key in observed}
    result['fields'] = {key: copy.deepcopy(observed.get('fields', {}).get(key)) for key in ('stage', 'deployed')}
    result['rows'] = []
    for row in observed.get('rows', []):
        box = row.get('box') if isinstance(row, dict) else None
        valid = isinstance(box, list) and len(box) == 4 and all(type(v) is int for v in box)
        if not valid or any(b[0] <= box[0] < box[2] <= b[2] and b[1] <= box[1] < box[3] <= b[3]
                            for b in ((420, 50, 520, 105), (820, 190, 1050, 300))):
            result['rows'].append(copy.deepcopy(row))
    state = observed.get('state_read')
    result['state_read'] = copy.deepcopy(state)
    if isinstance(state, dict) and isinstance(state.get('team'), dict):
        result['state_read'] = {key: copy.deepcopy(state[key]) for key in ('schema', 'status', 'reason',
            'snapshot_id', 'input', 'origin', 'resource_version', 'anchors', 'overlays') if key in state}
        selected = {(plan[k]['row'], plan[k]['slot']) for k in ('source', 'target')} if plan else None
        slots = state['team'].get('slots')
        result['state_read']['team'] = {'snapshot_id': state['team'].get('snapshot_id'),
            'slots': [_project_slot(s) if isinstance(s, dict) else copy.deepcopy(s) for s in slots
                if not isinstance(s, dict) or selected is None or (s.get('row'), s.get('slot')) in selected]
                if isinstance(slots, list) else copy.deepcopy(slots)}
    return result


def spec(action, knowledge):
    if (not isinstance(action, dict) or action.get('type') != 'deploy_unit'
            or action.get('expected_page') != 'preparation'
            or set(action) - {'type', 'name', 'bench_slot', 'target', 'capacity', 'position',
                              'knowledge_sha256', 'expected_page', 'reason'}):
        raise DeploymentRejected('指定部署仅接受角色与槽号，不接受旧坐标')
    name, target = action.get('name'), action.get('target')
    if (not isinstance(name, str) or not 1 <= len(name) <= 32 or name != name.strip()
            or type(action.get('bench_slot')) is not int or not 1 <= action['bench_slot'] <= 9
            or type(action.get('capacity')) is not int or not 1 <= action['capacity'] <= 10
            or not isinstance(target, dict) or set(target) != {'row', 'slot'}
            or target.get('row') not in ('front', 'back') or type(target.get('slot')) is not int
            or not 1 <= target['slot'] <= (4 if target['row'] == 'front' else 6)):
        raise DeploymentRejected('指定部署角色、原生槽号或容量非法')
    if (not isinstance(knowledge, dict) or knowledge.get('origin') != 'historical_observed_knowledge'
            or knowledge.get('error') is not None or not _sha(knowledge.get('sha256'))
            or action.get('knowledge_sha256') != knowledge['sha256']):
        raise DeploymentRejected('部署类型须绑定原已核知识缓存字节')
    try:
        position = deployment_position(knowledge, name)
    except ValueError as error:
        raise DeploymentRejected(str(error)) from error
    if (action.get('position') != position or position != '前后台'
            and target['row'] != ('front' if position == '前台' else 'back')):
        raise DeploymentRejected('计划部署类型或目标行与缓存冲突')
    slots = native_slots()
    return dict(name=name, position=position, knowledge_sha256=knowledge['sha256'],
        capacity=action['capacity'], source=next(s for s in slots if s['row'] == 'bench'
            and s['slot'] == action['bench_slot']), target=next(s for s in slots
            if s['row'] == target['row'] and s['slot'] == target['slot']))


def _slot(slots, definition, snapshot, plan):
    matches = [s for s in slots if isinstance(s, dict)
               and s.get('row') == definition['row'] and s.get('slot') == definition['slot']]
    if len(matches) != 1:
        raise DeploymentRejected('指定原生槽位缺失或重复，布局待重新核实')
    slot = matches[0]
    if (any(slot.get(key) != value for key, value in definition.items())
            or slot.get('snapshot_id') != snapshot or slot.get('origin') != 'native_visual_state_reader'):
        raise DeploymentRejected('槽位几何或当前帧来源不符')
    if slot.get('status') in ('unknown', 'not_read'):
        raise DeploymentUnreadable('指定槽位当前占用尚未读清')
    evidence = slot.get('evidence') or {}
    if (not isinstance(evidence, dict) or evidence.get('method') != 'bounded_native_slot_templates'
            or not all(_score(evidence.get(key)) for key in ('empty_score', 'badge_score', 'identity_score', 'identity_margin'))
            or not _score(slot.get('confidence'), .90)):
        raise DeploymentRejected('槽位原生模板证据不完整或矛盾')
    named = evidence['identity_score'] >= .90 and evidence['identity_margin'] >= .10
    if slot['status'] == 'empty':
        # StateReader classifies before rounding scores to four decimals. Exact
        # boundary displays can come from the negative side; an actual identity
        # record, or scores strictly over both boundaries, contradict emptiness.
        if (slot.get('name') is not None or slot.get('position') is not None
                or evidence['empty_score'] < .90 or evidence['badge_score'] > .65
                or evidence.get('identity') is not None
                or evidence['identity_score'] > .90 and evidence['identity_margin'] > .10):
            raise DeploymentRejected('空槽缺独立空位证据或存在占用冲突')
    elif slot['status'] == 'occupied':
        if slot.get('name') is None:
            raise DeploymentUnreadable('指定角色实名尚未读清，不能由计划补填')
        identity = evidence.get('identity') or {}
        if (slot['name'] != plan['name'] or evidence['empty_score'] > .90 or not named
                or not isinstance(identity, dict) or identity.get('kind') != 'unit'
                or identity.get('name') != plan['name'] or not _sha(identity.get('source_sha256'))
                or not isinstance(identity.get('file'), str) or not identity['file']
                or identity.get('score') != evidence['identity_score']):
            raise DeploymentRejected('角色实名、模板来源或占用结果与计划冲突')
        position = slot.get('position')
        if position is not None and (position != plan['position'] or evidence['badge_score'] < .90):
            raise DeploymentRejected('当前原生部署类型与缓存冲突，不能静默覆盖')
    else:
        raise DeploymentRejected('未知槽位状态协议')
    return _project_slot(slot)


def _read(observed, plan):
    if not isinstance(observed, dict) or observed.get('page') != 'preparation':
        raise DeploymentRejected('部署页面已改变，未执行坐标失效')
    snapshot, state = observed.get('snapshot_id'), observed.get('state_read')
    if not isinstance(state, dict):
        raise DeploymentUnreadable('当前原生槽位尚未读取')
    if state.get('status') == 'not_read' and state.get('snapshot_id') == snapshot:
        raise DeploymentUnreadable('当前观察尚未读取指定原生槽位')
    source = state.get('input') or {}
    if (not _sha(snapshot) or state.get('schema') != SCHEMA or state.get('snapshot_id') != snapshot
            or state.get('origin') != 'native_visual_state_reader' or not isinstance(source, dict)
            or source.get('sha256') != snapshot or source.get('format') != 'PNG'
            or source.get('size') != [1920, 1080]):
        raise DeploymentRejected('原生状态帧身份或布局来源不符')
    version = state.get('resource_version')
    if version is None:
        raise DeploymentUnreadable('当前环境没有可用原生状态资源，保留未知')
    anchors, overlays = state.get('anchors'), state.get('overlays')
    if (not _sha(version) or not isinstance(anchors, dict)
            or not all(_score(anchors.get(key), .90) for key in ('front', 'back'))
            or not isinstance(overlays, list) or overlays):
        raise DeploymentRejected('原生布局锚点或覆盖层改变，剩余坐标失效')
    fields, rows = observed.get('fields') or {}, observed.get('rows')
    if not isinstance(fields, dict) or not isinstance(rows, list):
        raise DeploymentUnreadable('当前节点与中央人口尚未读清')
    if any(not isinstance(r, dict) or not isinstance(r.get('text'), str)
           or not isinstance(r.get('raw_text', r['text']), str) or not _score(r.get('confidence'))
           or not isinstance(r.get('box'), list) or len(r['box']) != 4
           or any(type(v) is not int for v in r['box']) for r in rows):
        raise DeploymentRejected('原生文字来源格式非法')
    stage = fields.get('stage')
    stage_rows = [r for r in rows if re.fullmatch(r'[1-3]-[1-9]', clean(r.get('raw_text', r['text'])))
        and _score(r.get('confidence'), .90) and isinstance(r.get('box'), list) and len(r['box']) == 4
        and all(type(v) is int for v in r['box'])
        and 420 <= r['box'][0] < r['box'][2] <= 520 and 50 <= r['box'][1] < r['box'][3] <= 105]
    if not isinstance(stage, str) or not re.fullmatch(r'[1-3]-[1-9]', stage) or len(stage_rows) != 1:
        raise DeploymentUnreadable('原生准备节点尚未唯一实读')
    if clean(stage_rows[0].get('raw_text', stage_rows[0]['text'])) != stage:
        raise DeploymentRejected('原生准备节点与当前字段冲突')
    deployed = native_deployed_count(rows)
    if deployed is None or fields.get('deployed') is None:
        raise DeploymentUnreadable('当前中央人口读数未知')
    if deployed != fields['deployed']:
        raise DeploymentRejected('中央人口与当前字段冲突')
    occupied, capacity = map(int, deployed.split('/'))
    team = state.get('team') or {}
    if not isinstance(team, dict) or team.get('snapshot_id') != snapshot or not isinstance(team.get('slots'), list):
        raise DeploymentRejected('当前槽位集合来源不符')
    return dict(snapshot_id=snapshot, stage=stage, occupied=occupied, capacity=capacity,
        resource_version=version, source=_slot(team['slots'], plan['source'], snapshot, plan),
        target=_slot(team['slots'], plan['target'], snapshot, plan),
        layout=dict(size=source['size'], anchors=copy.deepcopy(anchors), overlays=[]))


def intent(observed, plan):
    result = _read(observed, plan)
    if result['source']['status'] != 'occupied' or result['target']['status'] != 'empty':
        raise DeploymentRejected('指定角色原席或目标空槽已经改变')
    result['plan'] = copy.deepcopy(plan)
    return result


def before(observed, plan):
    result = intent(observed, plan)
    if result['capacity'] != plan['capacity'] or result['occupied'] >= result['capacity']:
        raise DeploymentUnreadable('当前人口容量尚未达到指定可部署条件')
    return result


def after(observed, plan, prior):
    if not isinstance(prior, dict) or prior.get('plan') != plan:
        raise DeploymentRejected('部署前证据与当前计划不符')
    result = _read(observed, plan)
    if (result['stage'] != prior['stage'] or result['resource_version'] != prior['resource_version']
            or result['capacity'] != prior['capacity']):
        raise DeploymentRejected('部署后的节点、资源或容量改变，不能归因原动作')
    if (result['source']['status'] != 'empty' or result['target']['status'] != 'occupied'
            or result['occupied'] != prior['occupied'] + 1):
        raise DeploymentUnreadable('原席清空、指定目标归属与新增人口尚未同时核实，不重发')
    result['status'] = 'verified'
    return result
