"""Read-only, fail-closed animation guards for retained native layouts.

Whole-screen resemblance never approves an action. Each allowed branch binds
both native PNG bytes, complete OCR fields, text shapes, layout, and relevant
quality/badge/selection regions. No capture, waiting, controller, or input API.
"""
from __future__ import annotations

import hashlib
import base64
import io
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from currency_wars_perception import OPTION_LAYOUTS, SUPPLY_FIVE_CARD_LAYOUT, clean
from currency_wars_shop_reader import purchase_slot, stable_purchase_slot


# Human-read second native prep navigation icon, retained 2-7 frame:
# debug/runner-01a10ddf-9198333b3ffd/8483bdcd027a42afac3b55ac0fe46c9b-strategy.jpg.
# Packed white-ink mask of [1666, 46, 1708, 87], not a caller-supplied template.
PREPARATION_GUIDE_CONTROL = 'prep_startup_guide_2'
PREPARATION_GUIDE_BOUNDS = [1650, 39, 1727, 94]
PREPARATION_GUIDE_POINT = [1687, 65]
_PREPARATION_GUIDE_MASK = (
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAOAAAAAA/gAAAAD/4AAAAP/8AAAAf//AAAB///wAAH/z/8AAP/x/+AAP/h/+AAH/g/8AAH/A/4AAD/Af4AAD8EHwAABwMDgAAAgeAgAAAA/gAAAAA/gAAAAgOBgAAB4MDwAAD8EPwAAD/Af4AAH/A/8AAH/g/8AAP/h/+AAP/x/+AAD/7/4AAB///gAAAf//AAAAH/8AAAAB/wAAAAA/AAAAAAOAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
)


def _guide_icon_location(image):
    """One native template candidate in the complete prep navigation strip."""
    hsv = cv2.cvtColor(image[38:96, 1570:1918], cv2.COLOR_RGB2HSV)
    ink = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 200)).astype(np.uint8)
    template = np.unpackbits(np.frombuffer(base64.b64decode(_PREPARATION_GUIDE_MASK), dtype=np.uint8))[:41*42].reshape(41, 42)
    scores = cv2.matchTemplate(ink, template, cv2.TM_CCOEFF_NORMED)
    unused, best, unused_location, location = cv2.minMaxLoc(scores)
    if not np.isfinite(best) or best < .95:
        return None
    x, y = location
    # Adjacent response pixels describe one candidate; an independent peak
    # anywhere else among the four navigation controls is ambiguous.
    others = scores.copy()
    others[max(0, y-5):y+6, max(0, x-5):x+6] = -1
    if np.any(others >= .90):
        return None
    native = [x+1570, y+38]
    return native if abs(native[0]-1666) <= 2 and abs(native[1]-46) <= 2 else None


def _exposed_navigation_equal(images, bounds):
    # Compute both edges on the same crop: expanding only one crop introduces
    # artificial Canny boundary differences along the native strip border.
    old, fresh = (_edges(image, bounds) for image in images)
    if min(np.count_nonzero(old), np.count_nonzero(fresh)) < 100:
        return False
    score = cv2.matchTemplate(fresh, old, cv2.TM_CCOEFF_NORMED)[0, 0]
    return bool(np.isfinite(score) and score >= .985)


def stable_preparation_icon_target(action, request, actual, current_png, diagnostic=None):
    """Read-only evidence for one fixed icon; caller enforces epoch/deadline/broker.

    Page OCR anchors establish preparation but the target itself has no text.
    Both PNG digests, stage, unique template and exposed navigation outlines
    must agree. No generic coordinate or caller template exemption is granted.
    """
    allowed = False
    try:
        if not all(isinstance(value, dict) for value in (action, request, actual)):
            return False
        original = request.get('observation', {})
        proof = action.get('target_evidence', {})
        if (request.get('kind') != 'preparation_strategy'
                or original.get('page') != 'preparation' or actual.get('page') != 'preparation'
                or action.get('type') != 'click_point' or action.get('expected_page') != 'preparation'
                or not isinstance(proof, dict) or proof.get('control_id') != PREPARATION_GUIDE_CONTROL
                or proof.get('snapshot_id') != request.get('snapshot_id')
                or proof.get('bounds') != PREPARATION_GUIDE_BOUNDS
                or action.get('args') != PREPARATION_GUIDE_POINT
                or any(type(value) is not int for value in action['args'])):
            return False
        images = _frames(request, actual, current_png)
        if images is None:
            return False
        stage = original.get('fields', {}).get('stage')
        if (not isinstance(stage, str) or not re.fullmatch(r'[1-3]-[1-9]', stage)
                or actual.get('fields', {}).get('stage') != stage):
            return False
        for pattern, bounds in (
                ('备战阶段', [410, 20, 540, 65]),
                (re.escape(stage), [420, 50, 520, 105]),
                ('出战', [1760, 710, 1875, 790]),
                ('商店', [1575, 950, 1675, 1020]),
                ('前台区域', [895, 285, 1030, 330])):
            if _paired_row(original, actual, pattern, bounds, images) is None:
                return False
        locations = [_guide_icon_location(image) for image in images]
        if any(location is None for location in locations) or locations[0] != locations[1]:
            return False
        # An otherwise exposed icon is not an exemption for a newly opened
        # page/panel. Ignore only the bottom latency/UID footer. Whole-screen
        # comparison can veto here, but never establishes target evidence.
        visible = [sorted(clean(row.get('text', '')) for row in obs.get('rows', [])
                          if .90 <= row.get('confidence', 0) <= 1.
                          and _inside(row.get('box'), [0, 0, 1920, 1020]))
                   for obs in (original, actual)]
        if visible[0] != visible[1]:
            return False
        delta = np.max(np.abs(images[0][:1020].astype(np.int16)
                              - images[1][:1020].astype(np.int16)), axis=2)
        if np.mean(delta > 40) > .035:
            return False
        # Full local control boundaries reject changed/occluded strip state;
        # equality of the white target silhouette alone is insufficient.
        allowed = (_exposed_navigation_equal(images, PREPARATION_GUIDE_BOUNDS)
                   and _exposed_navigation_equal(images, [1572, 40, 1885, 93]))
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, cv2.error):
        allowed = False
    finally:
        if diagnostic is not None:
            diagnostic.update(allowed=bool(allowed), guard='native_prep_guide_icon_v1', input_sent=False)
    return bool(allowed)


def _frames(request, actual, current_png):
    original = request.get('observation', {})
    if original.get('snapshot_id') != request.get('snapshot_id'):
        return None
    images = []
    for path, digest in ((request.get('original_png'), request.get('snapshot_id')),
                         (current_png, actual.get('snapshot_id'))):
        if not path or not isinstance(digest, str):
            return None
        source = Path(path)
        if source.stat().st_size > 25 * 1024 * 1024:
            return None
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            return None
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'PNG' or image.size != (1920, 1080):
                return None
            images.append(np.array(image.convert('RGB')))
    return images


def _box(box):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(type(v) is int for v in box)
            and 0 <= box[0] < box[2] <= 1920 and 0 <= box[1] < box[3] <= 1080)


def _inside(box, bounds):
    return (_box(box) and bounds[0] <= box[0] < box[2] <= bounds[2]
            and bounds[1] <= box[1] < box[3] <= bounds[3])


def _edges(image, bounds):
    x, y, right, bottom = bounds
    gray = cv2.cvtColor(image[y:bottom, x:right], cv2.COLOR_RGB2GRAY)
    return cv2.Canny(gray, 40, 110)


def _shape_equal(images, bounds, threshold=.97):
    """Compare target ink/outline, with at most two native pixels of motion."""
    if not _box(bounds) or bounds[0] < 2 or bounds[1] < 2 or bounds[2] > 1918 or bounds[3] > 1078:
        return False
    old = _edges(images[0], bounds)
    if np.count_nonzero(old) < 24:
        return False
    search = _edges(images[1], [bounds[0]-2, bounds[1]-2, bounds[2]+2, bounds[3]+2])
    scores = cv2.matchTemplate(search, old, cv2.TM_CCOEFF_NORMED)
    maximum = cv2.minMaxLoc(scores)[1]
    return bool(np.isfinite(maximum) and maximum >= threshold)


def _text_equal(images, bounds, kind='white'):
    """Native text ink only; moving lines behind the text are not glyphs."""
    masks = []
    for image in images:
        x, y, right, bottom = bounds
        hsv = cv2.cvtColor(image[y:bottom, x:right], cv2.COLOR_RGB2HSV)
        h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        if kind == 'rating':
            mask = (s < 160) & (v > 215)
        elif kind == 'mode':
            mask = (h >= 80) & (h <= 110) & (s > 80) & (v > 140)
        else:
            mask = ((s < 70) & (v > 190)) | ((h >= 80) & (h <= 110) & (s > 80) & (v > 140))
        masks.append(mask.astype(np.uint8)*255)
    if min(np.count_nonzero(mask) for mask in masks) < 24:
        return False
    score = cv2.matchTemplate(masks[1], masks[0], cv2.TM_CCOEFF_NORMED)[0, 0]
    return bool(np.isfinite(score) and score >= (.95 if kind == 'rating' else .94))


def _unique_row(observation, pattern, bounds):
    candidates = [row for row in observation.get('rows', [])
                  if _inside(row.get('box'), bounds)
                  and re.fullmatch(pattern, clean(row.get('text', '')))]
    if len(candidates) != 1:
        return None
    row = candidates[0]
    score = row.get('confidence')
    return row if type(score) in (int, float) and .90 <= score <= 1. else None


def _paired_row(original, actual, pattern, bounds, images):
    rows = [_unique_row(obs, pattern, bounds) for obs in (original, actual)]
    if any(row is None for row in rows) or clean(rows[0]['text']) != clean(rows[1]['text']):
        return None
    if any(abs(a-b) > 4 for a, b in zip(rows[0]['box'], rows[1]['box'])):
        return None
    box = [min(rows[0]['box'][0], rows[1]['box'][0])-2,
           min(rows[0]['box'][1], rows[1]['box'][1])-2,
           max(rows[0]['box'][2], rows[1]['box'][2])+2,
           max(rows[0]['box'][3], rows[1]['box'][3])+2]
    return rows[1] if _text_equal(images, box) else None


def _quality(image, bounds):
    x, y, unused_right, unused_bottom = bounds
    hsv = cv2.cvtColor(image[y+15:y+45, x+80:x+180], cv2.COLOR_RGB2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    if np.mean((15 <= h) & (h <= 40) & (s > 90) & (v > 180)) >= .85:
        return 'gold'
    if np.mean((s < 65) & (v < 225) & (v > 150)) >= .85:
        return 'silver'
    if np.mean((s < 35) & (v > 240)) >= .90:
        return 'prismatic'
    return None


def _card_outline_equal(images, bounds):
    x, y, right, bottom = bounds
    regions = (([x-8, y+110, x+24, bottom-30], 1),
               ([right-24, y+110, right+8, bottom-30], 1),
               ([x+35, y-8, right-35, y+20], 0),
               ([x+35, bottom-20, right-35, bottom+8], 0))
    for box, axis in regions:
        states = []
        for image in images:
            edges = _edges(image, box)
            if np.mean(np.max(edges, axis=axis) > 0) < .95:
                return False
            hsv = cv2.cvtColor(image[box[1]:box[3], box[0]:box[2]], cv2.COLOR_RGB2HSV)
            selected = np.mean((hsv[:, :, 1] < 55) & (hsv[:, :, 2] > 210)) >= .15
            states.append(bool(selected))
        if states[0] != states[1]:
            return False
    return True


def _cards(action, request, actual, images):
    original = request['observation']
    page = original.get('page')
    if request.get('kind') != page + '_strategy' or actual.get('page') != page:
        return False
    options = original.get('semantic', {}).get('options')
    fresh = actual.get('semantic', {}).get('options')
    if not isinstance(options, list) or options != fresh:
        return False
    layout = SUPPLY_FIVE_CARD_LAYOUT if page == 'supply' and len(options) == 5 else OPTION_LAYOUTS.get(page, ())
    if not layout or len(layout) != len(options):
        return False
    header, header_roi, confirm_roi = {
        'investment': ('请选择投资策略', [820, 60, 1100, 145], [880, 935, 1040, 1030]),
        'environment': ('投资环境', [850, 55, 1070, 145], [880, 935, 1040, 1030]),
        'supply': ('补给阶段', [800, 120, 1120, 190], [1580, 950, 1810, 1025]),
    }[page]
    if any(_paired_row(original, actual, re.escape(label), bounds, images) is None
           for label, bounds in ((header, header_roi), ('确认', confirm_roi))):
        return False
    proof = action.get('target_evidence', {})
    index = proof.get('card_index')
    if (type(index) is not int or not 1 <= index <= len(options)
            or proof.get('snapshot_id') != request.get('snapshot_id')
            or action.get('expected_page') != page or action.get('type') not in ('click_text', 'click_point')):
        return False
    selected = options[index-1]
    if (proof.get('bounds') != list(layout[index-1]) or proof.get('text') != selected.get('title')
            or proof.get('effect_lines') != selected.get('effect_lines')
            or (action['type'] == 'click_text' and (action.get('exact') is not True or action.get('text') != selected.get('title')))):
        return False
    for i, (option, bounds) in enumerate(zip(options, layout), 1):
        if (option.get('card_index') != i or option.get('bounds') != list(bounds)
                or not isinstance(option.get('effect_lines'), list) or not option['effect_lines']):
            return False
        labels = [option.get('title'), *option['effect_lines']]
        if any(not isinstance(label, str) or not clean(label) for label in labels):
            return False
        # Every visible row in the full card text area must be accounted for;
        # a shortened effect list or a newly overlaid label cannot qualify.
        text_top = 360 if page == 'environment' else 460 if page == 'investment' else 320
        text_bounds = [bounds[0]+20, text_top, bounds[2]-20, bounds[3]-20]
        for observation in (original, actual):
            visible = [row for row in observation.get('rows', []) if _inside(row.get('box'), text_bounds)]
            if sorted(clean(row['text']) for row in visible) != sorted(clean(label) for label in labels):
                return False
        for label in labels:
            if _paired_row(original, actual, re.escape(clean(label)), bounds, images) is None:
                return False
        x, y, right, bottom = bounds
        # Complete outline and the central illustration preserve the selected
        # state and exposed card, while animated interior background is ignored.
        if not _card_outline_equal(images, bounds):
            return False
        if page == 'investment':
            old_quality, new_quality = (_quality(image, bounds) for image in images)
            if old_quality is None or old_quality != new_quality:
                return False
            if not _text_equal(images, [x+125, y+100, right-125, y+205]):
                return False
        # Native book badges cannot disappear, appear, or be occluded.
        if page in ('investment', 'environment') and not _text_equal(images, [right-45, y+21, right-21, y+41]):
            return False
    point = action.get('args')
    target = _unique_row(actual, re.escape(clean(selected['title'])), layout[index-1])
    if action['type'] == 'click_text' and point is None:
        return target is not None
    return (isinstance(point, list) and len(point) == 2 and target is not None
            and all(type(v) in (int, float) for v in point)
            and target['box'][0] <= point[0] < target['box'][2]
            and target['box'][1] <= point[1] < target['box'][3])


def _settlement(action, request, actual, images):
    original = request['observation']
    if request.get('kind') != 'settlement_verify' or actual.get('page') != 'settlement':
        return False
    records = [obs.get('semantic', {}).get('settlement') for obs in (original, actual)]
    keys = ('mode', 'outcome', 'rating', 'hp', 'tier', 'promotion_level', 'material_reward', 'promotion_points')
    if (any(not isinstance(record, dict) or record.get('snapshot_id') != obs.get('snapshot_id')
            for record, obs in zip(records, (original, actual)))
            or any(records[0].get(key) != records[1].get(key) or records[0].get(key) is None for key in keys)):
        return False
    # Unknown new text over the native summary is a modal/overlay candidate;
    # it must be re-observed through the ordinary page path.
    summary = [270, 150, 1650, 850]
    visible = [sorted(clean(row.get('text', '')) for row in obs.get('rows', [])
                      if _inside(row.get('box'), summary)) for obs in (original, actual)]
    if visible[0] != visible[1]:
        return False
    for key in (*keys, '奖励信息', '获取材料', '晋升点', '我的阵容', '下一页'):
        evidence = [record.get('evidence', {}).get(key, {}) for record in records]
        if any(not _box(item.get('bounds')) or not .90 <= item.get('confidence', 0) <= 1. for item in evidence):
            return False
        if evidence[0]['bounds'] != evidence[1]['bounds'] and any(abs(a-b) > 4 for a, b in zip(evidence[0]['bounds'], evidence[1]['bounds'])):
            return False
        if not _text_equal(images, evidence[0]['bounds'], key if key in ('mode', 'rating') else 'white'):
            return False
    if action.get('type') == 'confirm_match_result':
        result = action.get('result', {})
        mapping = {'mode': 'mode', 'outcome': 'outcome', 'rating': 'rating',
                   'hp': 'hp', 'materials': 'material_reward', 'promotion_points': 'promotion_points'}
        if any(result.get(key) != records[1][field] for key, field in mapping.items()):
            return False
        if any(type(result.get(key)) is not int for key in ('hp', 'materials', 'promotion_points')):
            return False
        if not isinstance(request.get('evidence_file'), str) or result.get('evidence') != request['evidence_file']:
            return False
        return all(key not in result or result[key] == records[1][key] for key in ('tier', 'promotion_level'))
    if action.get('type') == 'click_text':
        return action.get('text') == '下一页' and action.get('exact', True) is True and action.get('expected_page') == 'settlement'
    return False


def _shop(action, request, actual, images):
    original = request['observation']
    if request.get('kind') != 'shop_strategy' or actual.get('page') != 'shop' or action.get('type') != 'buy_shop':
        return False
    slot = stable_purchase_slot(original.get('shop') or {}, actual.get('shop') or {}, action.get('slot'),
                                request['snapshot_id'], actual.get('snapshot_id'))
    if slot is None or slot['name'] != action.get('name') or slot['cost'] != action.get('cost'):
        return False
    stage = original.get('fields', {}).get('stage')
    if not isinstance(stage, str) or not re.fullmatch(r'[1-3]-[1-9]', stage) or stage != actual.get('fields', {}).get('stage'):
        return False
    old_coins = original.get('semantic', {}).get('coins', {}).get('value')
    coins = actual.get('semantic', {}).get('coins', {}).get('value')
    if type(coins) is not int or coins != old_coins or coins < slot['cost']:
        return False
    if not _text_equal(images, [1638, 900, 1687, 940]):
        return False
    old = purchase_slot(original['shop'], action['slot'], request['snapshot_id'])
    for key in ('name', 'cost'):
        item = old['evidence'][key]
        box = item.get('ocr_bounds', item.get('bounds'))
        if not _text_equal(images, box):
            return False
    x, y, width, height = slot['bounds']
    if not _shape_equal(images, [x+3, y+3, x+82, y+80], .94):
        return False
    return True


def stable_semantic_target(action, request, actual, current_png, diagnostic=None):
    """Eligibility only; caller still enforces epoch, deadline, and broker."""
    try:
        if not all(isinstance(value, dict) for value in (action, request, actual)):
            return False
        images = _frames(request, actual, current_png)
        original = request.get('observation', {})
        page = original.get('page')
        if images is None or actual.get('page') != page:
            allowed = False
        elif page in ('investment', 'environment', 'supply'):
            allowed = _cards(action, request, actual, images)
        elif page == 'settlement':
            allowed = _settlement(action, request, actual, images)
        elif page == 'shop':
            allowed = _shop(action, request, actual, images)
        else:
            allowed = False
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, cv2.error):
        allowed = False
    if diagnostic is not None:
        diagnostic.update(allowed=bool(allowed), guard='native_target_semantics_v1', input_sent=False)
    return bool(allowed)


def stable_semantic_plan(reply, request, actual, current_png, diagnostic=None):
    """Only one bounded target action; no stale multi-action plan exemption."""
    if not isinstance(reply, dict) or not isinstance(request, dict):
        return False
    actions = reply.get('actions')
    return (reply.get('snapshot_id') == request.get('snapshot_id')
            and isinstance(actions, list) and len(actions) == 1
            and stable_semantic_target(actions[0], request, actual, current_png, diagnostic))
