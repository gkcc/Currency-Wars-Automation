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

from currency_wars_perception import (ENVIRONMENT_CONFIRM_BOUNDS, OPTION_LAYOUTS,
                                      SUPPLY_FIVE_CARD_LAYOUT, classify, clean)
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


# Native PNG calibration, evidence 99809b924393ca4540bfa4ced5c17cb89452ad73:
# ROOT_PR28_STARTUP_GUIDE/source-navigation.png (sha256 a7f73d61...972fb2),
# [1666,46,1708,87]. The old retained JPEG mask scores .940568 on this
# appearance; keep its threshold and add the actual PNG silhouette instead.
_PREPARATION_GUIDE_NATIVE_MASK = (
    'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAOAAAAAA/AAAAAB/wAAAAH/8AAAAf//AAAB/7/gAAD/z/4AAP/x/+AAP/g/+AAH/A/8AAH/Af4AAD+Af4AAB8AHwAABwEBgAAAAOAAAAAA/AAAAAAfAAAAAgOAgAABwEDgAAB8APwAAD+Af4AAH/Af4AAH/g/8AAP/h/+AAP/x/8AAD/z/4AAA/7/gAAAP/+AAAAH/4AAAAB/wAAAAAfAAAAAAMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
)
_GUIDE_STRIP_BOUNDS = [1570, 38, 1918, 96]
_GUIDE_GLYPH_BOUNDS = [1666, 46, 1708, 87]


def _guide_ink(crop):
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    return (hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 200)


def _guide_strip_location(strip, diagnostic=None, *, reviewed_template=None):
    """One exposed candidate across the complete native navigation strip.

    A reviewed template is extracted from the source PNG by this module, never
    supplied by a caller. Its identity is the supervisor's separate assertion.
    """
    result = {'location': None, 'reason': 'invalid_navigation_crop'}
    try:
        if strip.shape != (58, 348, 3) or strip.dtype != np.uint8:
            return None
        ink = _guide_ink(strip).astype(np.uint8)
        if reviewed_template is None:
            templates = [(name, np.unpackbits(np.frombuffer(base64.b64decode(value), dtype=np.uint8))
                          [:41*42].reshape(41, 42))
                         for name, value in (('retained_jpeg', _PREPARATION_GUIDE_MASK),
                                             ('native_png_99809b92', _PREPARATION_GUIDE_NATIVE_MASK))]
        else:
            if (reviewed_template.shape != (41, 42)
                    or not 100 <= np.count_nonzero(reviewed_template) <= 900):
                result['reason'] = 'reviewed_target_has_no_bounded_ink'
                return None
            templates = [('supervisor_source_png', reviewed_template.astype(np.uint8))]
        matches = [(name, cv2.matchTemplate(ink, template, cv2.TM_CCOEFF_NORMED))
                   for name, template in templates]
        scores = np.maximum.reduce([score for unused_name, score in matches])
        unused, best, unused_location, location = cv2.minMaxLoc(scores)
        x, y = location
        result.update(score=float(best), template=max(matches, key=lambda item: item[1][y, x])[0])
        if not np.isfinite(best) or best < (.985 if reviewed_template is not None else .95):
            result['reason'] = 'target_shape_unknown'
            return None
        # Across both native appearances, adjacent response pixels still
        # describe one target. An independent candidate is ambiguous.
        others = scores.copy()
        others[max(0, y-5):y+6, max(0, x-5):x+6] = -1
        result['other_peak'] = float(np.max(others))
        if np.any(others >= .90):
            result['reason'] = 'target_not_unique'
            return None
        native = [x+1570, y+38]
        if abs(native[0]-1666) > 2 or abs(native[1]-46) > 2:
            result['reason'] = 'target_outside_supported_control'
            return None
        result.update(location=native, reason='unique_source_shape')
        return native
    finally:
        if diagnostic is not None:
            diagnostic.update(result)


def _guide_icon_location(image, diagnostic=None):
    """Backward-compatible full-frame native detector; no supervisor inference."""
    return _guide_strip_location(image[38:96, 1570:1918], diagnostic)


def _preparation_header_crops_equal(crops, diagnostic=None):
    """Only the fixed preparation label's light or calibrated gray glyphs.

    Native gray label union [428,31,515,62]: 560/560 pixels at S<40 and
    V=100..190, IoU 1, support and two-pixel halo RGB max delta 1. Full OCR
    still has to identify the unique label; background colors are not glyphs.
    """
    result = {'allowed': False, 'reason': 'header_ink_changed_or_unknown'}
    try:
        if len(crops) != 2 or crops[0].shape != crops[1].shape:
            return False
        hsv = [cv2.cvtColor(crop, cv2.COLOR_RGB2HSV) for crop in crops]
        delta = np.max(np.abs(crops[0].astype(np.int16) - crops[1].astype(np.int16)), axis=2)
        for kind in ('light', 'prep_gray'):
            masks = [((item[:, :, 1] < 70) & (item[:, :, 2] > 190)) if kind == 'light'
                     else ((item[:, :, 1] < 40) & (item[:, :, 2] >= 100) & (item[:, :, 2] <= 190))
                     for item in hsv]
            counts = [int(np.count_nonzero(mask)) for mask in masks]
            if any(count < 24 or not .015 <= count / masks[0].size <= .60 for count in counts):
                continue
            union = masks[0] | masks[1]
            iou = np.count_nonzero(masks[0] & masks[1]) / np.count_nonzero(union)
            halo = cv2.dilate(union.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
            if iou < .985 or np.mean(delta[halo] > 16) > .005:
                continue
            result.update(allowed=True, reason='stable_exposed_header', ink_kind=kind,
                          ink_pixels=counts, ink_iou=float(iou), halo_max_delta=int(np.max(delta[halo])))
            return True
        return False
    finally:
        if diagnostic is not None:
            diagnostic.update(result)


def _preparation_header_equal(images, bounds):
    if not _box(bounds):
        return False
    x, y, right, bottom = bounds
    return _preparation_header_crops_equal([image[y:bottom, x:right] for image in images])


def _guide_navigation_pair(strips, diagnostic=None, *, supervising=False):
    """Local actionability shared by production and native-crop diagnosis.

    This does not classify a whole frame or issue input. Unknown native shape
    may be separately reviewed by ROOT, but current exposure and uniqueness
    cannot be asserted past changed pixels. No moving background equality.
    """
    result = {'allowed': False, 'source': 'supervising_agent' if supervising else 'native',
              'native_locations': [], 'reviewed_locations': [], 'reason': 'invalid_navigation_pair'}
    try:
        if len(strips) != 2 or any(strip.shape != (58, 348, 3) or strip.dtype != np.uint8 for strip in strips):
            return False
        native = [{}, {}]
        native_locations = [_guide_strip_location(strip, details) for strip, details in zip(strips, native)]
        result.update(native_locations=native_locations, native_detection=native)
        if supervising:
            # Fixed, current source glyph; no caller-supplied pixels/template.
            template = _guide_ink(strips[0][8:49, 96:138])
            reviewed = [{}, {}]
            locations = [_guide_strip_location(strip, details, reviewed_template=template)
                         for strip, details in zip(strips, reviewed)]
            result.update(reviewed_locations=locations, reviewed_detection=reviewed)
        else:
            locations = native_locations
        if any(location is None for location in locations) or locations[0] != locations[1]:
            result['reason'] = 'target_identity_or_location_changed'
            return False
        for label, bounds in (('target', [80, 1, 157, 56]), ('navigation', [2, 2, 315, 55])):
            x, y, right, bottom = bounds
            crops = [strip[y:bottom, x:right] for strip in strips]
            masks = [_guide_ink(crop) for crop in crops]
            union = masks[0] | masks[1]
            if np.count_nonzero(union) < 100:
                result['reason'] = label + '_not_exposed'
                return False
            iou = np.count_nonzero(masks[0] & masks[1]) / np.count_nonzero(union)
            halo = cv2.dilate(union.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
            delta = np.max(np.abs(crops[0].astype(np.int16)-crops[1].astype(np.int16)), axis=2)
            result[label] = {'ink_pixels': [int(np.count_nonzero(mask)) for mask in masks],
                             'ink_iou': float(iou), 'halo_max_delta': int(np.max(delta[halo]))}
            if iou < .99 or np.mean(delta[halo] > 16) > .005:
                result['reason'] = label + '_exposure_changed'
                return False
        # A cover over the click center must not hide inside negative space.
        centers = [strip[23:32, 113:122] for strip in strips]
        if (any(np.count_nonzero(_guide_ink(crop)) < 4 for crop in centers)
                or np.max(np.abs(centers[0].astype(np.int16)-centers[1].astype(np.int16))) > 16):
            result['reason'] = 'click_center_not_exposed'
            return False
        result.update(allowed=True, reason='stable_unique_exposed_target')
        return True
    finally:
        if diagnostic is not None:
            diagnostic.update(result)


def _preparation_modal_row(observation):
    # Veto only: preparation anchors can remain behind a confirmation panel.
    # Do not require unrelated board/quest OCR to stay byte-for-byte equal.
    for row in observation.get('rows', []):
        score = row.get('confidence')
        if (type(score) in (int, float) and .72 <= score <= 1.
                and _inside(row.get('box'), [300, 120, 1650, 1000])
                and re.fullmatch(r'(?:确认|确定|取消|关闭|继续|下一步|是否.+|请选择.*)',
                                 clean(row.get('text', '')))):
            return row
    return None


def stable_preparation_icon_target(action, request, actual, current_png, diagnostic=None):
    """One fixed navigation action, anchored to current native preparation.

    Epoch/deadline/owner/manual-proof validation belongs to the existing caller.
    This guard checks byte sources, both native pages/anchors, target uniqueness
    and current local exposure. It never promotes a supervisor label to native.
    """
    result = {'allowed': False, 'guard': 'native_prep_guide_icon_v2', 'input_sent': False,
              'reason': 'unsupported_guide_action'}
    try:
        if not all(isinstance(value, dict) for value in (action, request, actual)):
            return False
        original = request.get('observation', {})
        proof = action.get('target_evidence', {})
        if (request.get('kind') != 'preparation_strategy'
                or original.get('page') != 'preparation' or actual.get('page') != 'preparation'
                or action.get('type') != 'click_point' or action.get('expected_page') != 'preparation'
                or not isinstance(proof, dict) or proof.get('control_id') != PREPARATION_GUIDE_CONTROL
                or proof.get('source') not in (None, 'native', 'supervising_agent')
                or proof.get('snapshot_id') != request.get('snapshot_id')
                or proof.get('bounds') != PREPARATION_GUIDE_BOUNDS
                or action.get('args') != PREPARATION_GUIDE_POINT
                or any(type(value) is not int for value in action['args'])):
            return False
        images = _frames(request, actual, current_png)
        if images is None:
            result['reason'] = 'frame_source_mismatch'
            return False
        stage = original.get('fields', {}).get('stage')
        if (not isinstance(stage, str) or not re.fullmatch(r'[1-3]-[1-9]', stage)
                or actual.get('fields', {}).get('stage') != stage
                or any(classify(obs.get('rows', [])) != 'preparation' for obs in (original, actual))):
            result['reason'] = 'preparation_page_or_stage_changed'
            return False
        if any(_preparation_modal_row(obs) is not None for obs in (original, actual)):
            result['reason'] = 'preparation_modal_control_visible'
            return False
        for pattern, bounds, ink_guard in (
                ('备战阶段', [410, 20, 540, 65], _preparation_header_equal),
                (re.escape(stage), [420, 50, 520, 105], None),
                ('出战', [1760, 710, 1875, 790], None),
                ('商店', [1575, 950, 1675, 1020], None)):
            if _paired_row(original, actual, pattern, bounds, images, ink_guard=ink_guard) is None:
                result.update(reason='preparation_anchor_changed_or_unknown', anchor=pattern)
                return False
        # The front-caption OCR is not a navigation precondition. Nor are
        # unrelated animated background pixels / unchanged whole-page OCR.
        local = {}
        allowed = _guide_navigation_pair([image[38:96, 1570:1918] for image in images], local,
                                         supervising=proof.get('source') == 'supervising_agent')
        result.update(local)
        return allowed
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, cv2.error):
        result['reason'] = 'guide_evidence_unreadable'
        return False
    finally:
        if diagnostic is not None:
            diagnostic.update(result)


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


def _paired_row(original, actual, pattern, bounds, images, *, ink_guard=None):
    rows = [_unique_row(obs, pattern, bounds) for obs in (original, actual)]
    if any(row is None for row in rows) or clean(rows[0]['text']) != clean(rows[1]['text']):
        return None
    if any(abs(a-b) > 4 for a, b in zip(rows[0]['box'], rows[1]['box'])):
        return None
    box = [min(rows[0]['box'][0], rows[1]['box'][0])-2,
           min(rows[0]['box'][1], rows[1]['box'][1])-2,
           max(rows[0]['box'][2], rows[1]['box'][2])+2,
           max(rows[0]['box'][3], rows[1]['box'][3])+2]
    return rows[1] if (ink_guard or _text_equal)(images, box) else None


def _environment_gray_confirmation_equal(images, bounds):
    """One calibrated disabled appearance, only for environment card selection.

    Native pair: evidence commit 6ef3793a, ROOT_ENVIRONMENT_CONFIRM_RGB.json;
    glyph [1052,966,1112,1002] has 368/361 gray pixels, IoU .980978,
    support RGB delta <=8, two-pixel halo delta <=15. Background animation
    is not text. White/enabled, blank, shifted or covered ink fails here.
    This does not authorize clicking the disabled confirmation itself.
    """
    if not _box(bounds):
        return False
    x, y, right, bottom = bounds
    crops = [image[y:bottom, x:right] for image in images]
    masks = []
    for crop in crops:
        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        s, v = hsv[:, :, 1], hsv[:, :, 2]
        if np.any(v > 190):
            return False
        mask = (s < 40) & (v >= 100) & (v <= 190)
        if np.count_nonzero(mask) < 24 or not .015 <= np.mean(mask) <= .60:
            return False
        masks.append(mask)
    union = masks[0] | masks[1]
    if np.count_nonzero(masks[0] & masks[1]) / np.count_nonzero(union) < .98:
        return False
    delta = np.max(np.abs(crops[0].astype(np.int16) - crops[1].astype(np.int16)), axis=2)
    halo = cv2.dilate(union.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    return bool(np.max(delta[union]) <= 8 and np.max(delta[halo]) <= 16)


def _environment_caption_equal(images, bounds):
    """Native structure ink is gray; exact local RGB preserves its appearance.

    ROOT_ENVIRONMENT_CAPTIONS_RGB.json: paired 角色/装备 ROIs are identical,
    with 246/253 Canny pixels. A blank or uniform cover is not a glyph.
    """
    if not _box(bounds):
        return False
    x, y, right, bottom = bounds
    return (np.array_equal(images[0][y:bottom, x:right], images[1][y:bottom, x:right])
            and np.count_nonzero(_edges(images[0], bounds)) >= 24)


def _environment_book_equal(images, bounds):
    # Same retained source: all three yellow book ROIs are RGB-exact pairs,
    # with 106/102/104 edge pixels. Books are not white OCR glyphs. Every
    # color/state byte must still match; neither blank nor flat color passes.
    return _environment_caption_equal(images, bounds)


def _environment_confirmation_equal(images, bounds):
    # Keep the established bright-text contract; the two appearances cannot
    # cross-match because the gray branch rejects every V >190 pixel.
    return (_text_equal(images, bounds)
            or _environment_gray_confirmation_equal(images, bounds))


def _environment_caption_row(original, actual, label, bounds, images):
    center = (bounds[0] + bounds[2]) // 2
    top, bottom = {'角色': (575, 645), '装备': (720, 785)}[clean(label)]
    return _paired_row(original, actual, re.escape(clean(label)),
                       [center-60, top, center+60, bottom], images,
                       ink_guard=_environment_caption_equal)


def navigation_target(action, request):
    """Identify the two guarded text controls, without granting permission.

    Identification deliberately precedes exact/kind/bounds validation: a bad
    variant of these controls must not fall back to the small global-delta path.
    Other targets retain their existing policy and receive no new exemption.
    """
    if not isinstance(action, dict) or not isinstance(request, dict):
        return None
    if action.get('type') != 'click_text':
        return None
    page = request.get('observation', {}).get('page')
    label = clean(action.get('text', ''))
    matches = lambda target: label == target or (bool(label) and not action.get('exact', True) and label in target)
    if page == 'opponents' and matches('下一步'):
        return 'opponents_next'
    if page == 'unknown' and matches('货币战争'):
        return 'activity_currency_wars'
    return None


def _navigation_ink_equal(images, bounds):
    """Fixed-position light OR dark glyphs, with their exposed local halo.

    A normalized grayscale score alone would accept a dimmed/covered button.
    Compare foreground support and original RGB as well. No translated search,
    arbitrary color tolerance, full-card comparison, or extra OCR is used.
    Animation is allowed outside this small observed text/halo region.
    """
    if not _box(bounds):
        return False
    x, y, right, bottom = bounds
    crops = [image[y:bottom, x:right] for image in images]
    hsv = [cv2.cvtColor(crop, cv2.COLOR_RGB2HSV) for crop in crops]
    delta = np.max(np.abs(crops[0].astype(np.int16) - crops[1].astype(np.int16)), axis=2)
    for polarity in ('light', 'dark'):
        masks = [((item[:, :, 1] < 90) & (item[:, :, 2] > 190))
                 if polarity == 'light' else item[:, :, 2] < 80 for item in hsv]
        if any(np.count_nonzero(mask) < 24 or not .015 <= np.mean(mask) <= .60 for mask in masks):
            continue
        union = masks[0] | masks[1]
        if np.count_nonzero(masks[0] & masks[1]) / np.count_nonzero(union) < .985:
            continue
        # A two-pixel halo ties the text to its currently exposed control,
        # including dark text on a light button. Overlay/dimming changes in
        # this local support veto even when OCR still returns the old string.
        exposed = cv2.dilate(union.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        if np.mean(delta[exposed] > 16) <= .005:
            return True
    return False


def _navigation_row(original, actual, label, images, bounds=None, *, target=False):
    rows = [_unique_row(obs, re.escape(label), [0, 0, 1920, 1020])
            for obs in (original, actual)]
    if any(row is None for row in rows):
        return None
    if bounds is not None and any(not _inside(row['box'], bounds) for row in rows):
        return None
    if any(abs(a-b) > 2 for a, b in zip(rows[0]['box'], rows[1]['box'])):
        return None
    box = [max(0, min(rows[0]['box'][0], rows[1]['box'][0])-2),
           max(0, min(rows[0]['box'][1], rows[1]['box'][1])-2),
           min(1920, max(rows[0]['box'][2], rows[1]['box'][2])+2),
           min(1020, max(rows[0]['box'][3], rows[1]['box'][3])+2)]
    if not _navigation_ink_equal(images, box):
        return None
    if target:
        # Text can surround a blank click center. A colored cover in that gap
        # is observable even when every glyph and its halo remain unchanged.
        x, y, right, bottom = box
        delta = np.max(np.abs(images[0][y:bottom, x:right].astype(np.int16)
                              - images[1][y:bottom, x:right].astype(np.int16)), axis=2)
        cx, cy = (rows[1]['box'][0]+rows[1]['box'][2])//2-x, (rows[1]['box'][1]+rows[1]['box'][3])//2-y
        center = delta[max(0, cy-4):cy+5, max(0, cx-4):cx+5]
        if np.mean(delta > 16) > .005 or center.size == 0 or np.max(center) > 16:
            return None
    return rows[1]


def _navigation_visible_semantics(original, actual):
    # This uses the native rows already read from each immutable frame. The
    # lower threshold is veto-only, never an authorization for a weak target.
    # Ignore the existing bottom UID/latency footer, not a dynamic game field.
    rows = []
    for observation in (original, actual):
        visible = []
        for row in observation.get('rows', []):
            if not _box(row.get('box')):
                return False
            if row['box'][1] >= 1020:
                continue
            score = row.get('confidence')
            if type(score) not in (int, float) or not 0 <= score <= 1:
                return False
            if score >= .72:
                visible.append((clean(row.get('text', '')), row['box']))
        rows.append(sorted(visible))
    return (bool(rows[0]) and len(rows[0]) == len(rows[1])
            and all(old[0] == fresh[0] and all(abs(a-b) <= 2 for a, b in zip(old[1], fresh[1]))
                    for old, fresh in zip(*rows)))


def _navigation(action, request, actual, images):
    original = request['observation']
    control = navigation_target(action, request)
    if (control is None or action.get('exact', True) is not True
            or action.get('expected_page') != original.get('page')
            or action.get('bounds') is not None and not _box(action['bounds'])
            or any(classify(obs.get('rows', [])) != original.get('page') for obs in (original, actual))
            or not _navigation_visible_semantics(original, actual)):
        return False
    if control == 'opponents_next':
        kind, target = 'opponents_strategy', '下一步'
        target_region = [1200, 740, 1920, 1020]
        anchor_sets = ((('竞争对手', [0, 0, 1400, 240]),),)
    else:
        kind, target = 'unknown_page', '货币战争'
        # Explicit menu semantics, not a generic unknown-page escape hatch.
        # Native dual-frame coverage for these layouts must be recorded
        # separately; synthetic protocol fixtures are not calibration frames.
        # These are semantic-role bounds (header, menu, content), not a claim
        # of retained native calibration. Reject body-only text lookalikes;
        # these bounds do not prove all unknown layouts are real menu cards.
        target_region = [300, 160, 1920, 1000]
        header = [0, 0, 1000, 180]
        menu = [0, 70, 1900, 1000]
        anchor_sets = ((('星际和平指南', header), ('宇宙纷争', menu)),
                       (('星际和平指南', header), ('逐光捡金', menu)),
                       (('旅情事记', header), ('常驻活动', menu)))
    if action.get('bounds') is not None:
        bounds = action['bounds']
        target_region = [max(target_region[0], bounds[0]), max(target_region[1], bounds[1]),
                         min(target_region[2], bounds[2]), min(target_region[3], bounds[3])]
    if (request.get('kind') != kind or not _box(target_region)
            or _navigation_row(original, actual, target, images, target_region, target=True) is None):
        return False
    return any(all(_navigation_row(original, actual, label, images, bounds) is not None
                   for label, bounds in anchors) for anchors in anchor_sets)


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


def _environment_structure_labels(original, actual, labels, bounds, images):
    """Account for native section headings, without rewriting effect_lines.

    ROOT's retained environment card has 角色 at [420,598,462,626] and
    装备 at [422,744,459,766]. The latter is below the effect reader's y=690
    limit. Only these centered, paired native headings may extend coverage;
    other rows remain unaccounted for and fail the complete-card check.
    """
    extra = []
    for label in ('角色', '装备'):
        found = [[row for row in observation.get('rows', [])
                  if clean(row.get('text', '')) == label and _inside(row.get('box'), bounds)]
                 for observation in (original, actual)]
        if not any(found):
            continue
        if (any(len(rows) != 1 for rows in found)
                or _environment_caption_row(original, actual, label, bounds, images) is None):
            return None
        if label not in [clean(text) for text in labels]:
            extra.append(label)
    return extra


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
        'environment': ('投资环境', [850, 55, 1070, 145], ENVIRONMENT_CONFIRM_BOUNDS),
        'supply': ('补给阶段', [800, 120, 1120, 190], [1580, 950, 1810, 1025]),
    }[page]
    if (_paired_row(original, actual, re.escape(header), header_roi, images) is None
            or _paired_row(original, actual, '确认', confirm_roi, images,
                           ink_guard=_environment_confirmation_equal if page == 'environment' else None) is None):
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
        if page == 'environment':
            structural = _environment_structure_labels(original, actual, labels, bounds, images)
            if structural is None:
                return False
            labels = [*labels, *structural]
        # Every visible row in the full card text area must be accounted for;
        # a shortened effect list or a newly overlaid label cannot qualify.
        text_top = 360 if page == 'environment' else 460 if page == 'investment' else 320
        text_bounds = [bounds[0]+20, text_top, bounds[2]-20, bounds[3]-20]
        for observation in (original, actual):
            visible = [row for row in observation.get('rows', []) if _inside(row.get('box'), text_bounds)]
            if sorted(clean(row['text']) for row in visible) != sorted(clean(label) for label in labels):
                return False
        for label in labels:
            row = (_environment_caption_row(original, actual, label, bounds, images)
                   if page == 'environment' and clean(label) in ('角色', '装备')
                   else _paired_row(original, actual, re.escape(clean(label)), bounds, images))
            if row is None:
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
        if page == 'environment' and not _environment_book_equal(images, [right-45, y+21, right-21, y+41]):
            return False
        if page == 'investment' and not _text_equal(images, [right-45, y+21, right-21, y+41]):
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
                                request['snapshot_id'], actual.get('snapshot_id'), images=images)
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
        elif navigation_target(action, request) is not None:
            allowed = _navigation(action, request, actual, images)
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
