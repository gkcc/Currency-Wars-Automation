"""One source-bound occupied bench portrait inspection; never capture or input.

Occupancy is not a name, star, qualification, overflow, or checked-team proof.
The caller owns the existing request/epoch, reward, receipt and input gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
from pathlib import Path
import re

import cv2
import numpy as np
from PIL import Image

from currency_wars_slot_probe import SlotProbe
from currency_wars_state_reader import native_slots


CONTROL = 'owned_unit_details'
ANCHORS = (('备战阶段', (410, 20, 540, 65)),
           ('出战', (1760, 710, 1875, 790)),
           ('商店', (1575, 950, 1675, 1020)))
# Four retained native frames, six pairs, five occupied bench cards: maximum
# RGB difference 7, mean .0149, changed fraction .0177, 8x8 tile mean .4300.
# No registration, scaling, masked identity area, or approximate portrait match.
MAX_CHANNEL_DELTA = 7
MAX_MEAN_DELTA = .02
MAX_CHANGED_FRACTION = .02
MAX_TILE_DELTA = .5


def _fail(reason):
    raise ValueError('备选角色详情拒绝：' + reason)


def _stamp(value):
    if not isinstance(value, str):
        _fail('缺少原生帧时间')
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        _fail('原生帧时间没有时区')
    return stamp


def _fresh_request(request):
    now = datetime.now(timezone.utc)
    if not now < _stamp(request.get('deadline_at')):
        _fail('原请求已过期')
    return now


def validate_action(action, request):
    """Small intent schema; run/match/epoch/native frame come from its request."""
    if not isinstance(action, dict) or not isinstance(request, dict):
        _fail('缺少单动作或原请求')
    proof = action.get('target_evidence')
    if (set(action) != {'type', 'args', 'expected_page', 'guard_texts', 'reason', 'target_evidence'}
            or action.get('type') != 'click_point' or action.get('expected_page') != 'preparation'
            or action.get('guard_texts') != [a[0] for a in ANCHORS]
            or not isinstance(action.get('reason'), str) or not action['reason'].strip()
            or not isinstance(proof, dict)
            or set(proof) != {'control_id', 'snapshot_id', 'row', 'slot', 'bounds'}
            or proof.get('control_id') != CONTROL or proof.get('row') != 'bench'
            or type(proof.get('slot')) is not int or not 1 <= proof['slot'] <= 9):
        _fail('只接受备选整卡的单次固定头像点击')
    slot = next(s for s in native_slots() if s['row'] == 'bench' and s['slot'] == proof['slot'])
    if (proof.get('bounds') != slot['bounds'] or action.get('args') != slot['point']
            or not isinstance(proof['bounds'], list) or not isinstance(action['args'], list)
            or any(type(v) is not int for v in proof['bounds'] + action['args'])):
        _fail('槽号、整卡边界或头像安全点不一致')
    original = request.get('observation')
    if (request.get('kind') != 'preparation_strategy' or not isinstance(original, dict)
            or original.get('page') != 'preparation'
            or not isinstance(request.get('snapshot_id'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', request['snapshot_id'])
            or original.get('snapshot_id') != request['snapshot_id']
            or proof['snapshot_id'] != request['snapshot_id']
            or any(not isinstance(request.get(k), str) or not request[k]
                   for k in ('request_id', 'match_id', 'resume_epoch'))):
        _fail('不是当前备战请求的原始快照')
    _fresh_request(request)
    return slot


def _observation(observed, stage, now):
    if (not isinstance(observed, dict) or observed.get('page') != 'preparation'
            or observed.get('fields', {}).get('stage') != stage
            or not isinstance(stage, str) or not re.fullmatch(r'[1-3]-[1-9]', stage)
            or observed.get('read_contract', {}).get('version') != 2
            or observed.get('read_contract', {}).get('effective_scope') != 'full'
            or any(not isinstance(observed.get(k), str) or not observed[k]
                   for k in ('snapshot_id', 'capture_request_id', 'frame_id'))):
        _fail('缺少同节点完整读取和原生帧身份')
    age = (now - _stamp(observed.get('captured_at'))).total_seconds()
    if not 0 <= age <= 180:
        _fail('原生帧不新鲜')
    rows = observed.get('rows')
    if not isinstance(rows, list):
        _fail('缺少当前原生备战锚点')
    for label, bounds in ANCHORS:
        matches = [row for row in rows if isinstance(row, dict)
                   and row.get('text') == label and .90 <= row.get('confidence', 0) <= 1.
                   and isinstance(row.get('box'), list) and len(row['box']) == 4
                   and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                   and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
        if len(matches) != 1:
            _fail('备战/出战/收店锚点不唯一或缺失')
    if any(row.get('text') in ('收起', '刷新', '确认', '取消') for row in rows):
        _fail('页面存在商店或模态控件')
    rewards = observed.get('semantic', {}).get('rewards') or {}
    if rewards.get('snapshot_id') == observed['snapshot_id'] and (
            rewards.get('targets') or rewards.get('uncertain') or rewards.get('interaction_required')):
        _fail('当前奖励仍可见、待选或不确定')


def _image(path, snapshot):
    if not isinstance(path, (str, Path)):
        _fail('缺少原始PNG路径')
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= 25_000_000:
        _fail('原始PNG缺失或越界')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != snapshot:
        _fail('原始PNG字节不匹配')
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG' or image.size != (1920, 1080):
            _fail('仅支持完整1920×1080原生PNG')
        return np.array(image.convert('RGB'))


def _occupied(probe, rgb, slot, rows):
    reader = probe.reader
    if not reader._load():
        _fail('原生槽位资源缺失')
    for name, (x1, y1, x2, y2) in (
            ('front', (900, 285, 1050, 325)), ('back', (900, 555, 1050, 598))):
        matches = reader._match(rgb[y1:y2, x1:x2], 'anchor', name)
        if not matches or matches[0][0] < .9:
            _fail('原生前后台图像锚点不符')
    overlays = reader._overlays(rgb)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    result = probe._slot(rgb, gray, slot, True, overlays)
    if result['status'] != 'occupied':
        _fail('目标槽位空、未知或遮挡')
    x1, y1, x2, y2 = slot['bounds']
    for row in rows:
        box = row.get('box')
        if (isinstance(box, list) and len(box) == 4
                and max(x1, box[0]) < min(x2, box[2]) and max(y1, box[1]) < min(y2, box[3])
                and any(label in row.get('text', '') for label in ('购买', '出售', '升级', '装备', '合成', '复制'))):
            _fail('目标上出现经济或装备文字控件')
    patch = rgb[y1:y2, x1:x2]
    portrait = cv2.cvtColor(patch[30:118, 10:-10], cv2.COLOR_RGB2GRAY)
    # Retained five unknown portraits: weakest 4x4 tile std=8.4583 and
    # weakest 16x16 click-patch std=28.0420. Flat/covered areas stay unknown.
    tile_std = min(float(tile.std()) for row in np.array_split(portrait, 4)
                   for tile in np.array_split(row, 4, axis=1))
    x, y = slot['point']
    point_std = float(gray[y-8:y+8, x-8:x+8].std())
    if tile_std < 8 or point_std < 25:
        _fail('头像或点击区域未完整暴露')
    return {'status': 'occupied', 'portrait_tile_std': tile_std, 'point_std': point_std}


def check_target(action, request, actual, current_png):
    """Compare immutable source/current bytes; no OCR claims or input side effects."""
    slot = validate_action(action, request)
    original = request['observation']
    stage = original.get('fields', {}).get('stage')
    now = _fresh_request(request)
    for observed in (original, actual):
        _observation(observed, stage, now)
    if _stamp(actual['captured_at']) < _stamp(original['captured_at']):
        _fail('当前帧早于请求原帧')
    old = _image(request.get('original_png'), request['snapshot_id'])
    fresh = _image(current_png, actual['snapshot_id'])
    probe = SlotProbe()
    occupied = [_occupied(probe, image, slot, observed['rows'])
                for image, observed in ((old, original), (fresh, actual))]
    cx, cy = slot['point']
    portrait_bounds = [cx-33, cy-29, cx+33, cy+30]
    if not np.array_equal(old[cy-29:cy+30, cx-33:cx+33], fresh[cy-29:cy+30, cx-33:cx+33]):
        _fail('头像内部原RGB不一致')
    x1, y1, x2, y2 = slot['bounds']
    delta = np.abs(old[y1:y2, x1:x2].astype(np.int16) - fresh[y1:y2, x1:x2].astype(np.int16))
    metrics = {'max_channel_delta': int(delta.max()), 'mean_delta': float(delta.mean()),
               'changed_fraction': float(np.mean(delta.max(axis=2) > 0)),
               'max_tile_delta': max(float(tile.mean()) for row in np.array_split(delta, 8)
                                     for tile in np.array_split(row, 8, axis=1))}
    if (metrics['max_channel_delta'] > MAX_CHANNEL_DELTA or metrics['mean_delta'] > MAX_MEAN_DELTA
            or metrics['changed_fraction'] > MAX_CHANGED_FRACTION or metrics['max_tile_delta'] > MAX_TILE_DELTA):
        _fail('原帧与当前整卡图像发生变化')
    _fresh_request(request)  # Image/resource work cannot extend the request lease.
    return {'control_id': CONTROL, 'row': 'bench', 'slot': slot['slot'],
            'bounds': slot['bounds'], 'point': slot['point'], 'stage': stage,
            'source_snapshot_id': request['snapshot_id'], 'current_snapshot_id': actual['snapshot_id'],
            'occupied': occupied, 'stability': metrics, 'portrait_exact_bounds': portrait_bounds,
            'identity_verified': False,
            'star_verified': False, 'overflow_verified': False, 'input_sent': False}
