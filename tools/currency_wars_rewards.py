"""Read-only native blue-orb observations from the supplied complete frame.

The caller owns the PNG-byte/snapshot binding, OCR, epoch and input authority.
This module neither captures nor clicks. A missing match says only that this
known control was not observed in its bounded native region; it never clears
the rewards phase or proves an individual reward was received.
"""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import numpy as np
from PIL import Image


RESOURCE_DIR = Path(__file__).parent / 'reward_resources'
SCAN_BOUNDS = (1320, 230, 1660, 500)
TEMPLATE_ID = 'blue_orb_q01'
TEMPLATE_SHA256 = '20aa7afcef3c35e05de5347e27925d4806f41bb7d4178ee34b078bbcf77e3b11'
TEMPLATE_SIZE = (74, 69)
# This is the exact public q01 crop. The manifest retains both original and
# exported source hashes. Neither a caller nor an editable manifest supplies
# the template, matching threshold, scan region or click target.
TEMPLATE_SOURCE_SHA256 = 'c08b5ba47534afa4c778240f9327494d610a3f331060cda5665face50bc3e4bf'
TEMPLATE_SOURCE_ROI = (1497, 322, 1571, 391)


def _inside(box, bounds):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(type(value) in (int, float) and np.isfinite(value) for value in box)
            and bounds[0] <= box[0] < box[2] <= bounds[2]
            and bounds[1] <= box[1] < box[3] <= bounds[3])


def _rows(rows, pattern, bounds, minimum=.90):
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        confidence = row.get('confidence')
        if (type(confidence) not in (float, int) or not minimum <= confidence <= 1.
                or not _inside(row.get('box'), bounds)):
            continue
        text = re.sub(r'\s+', '', str(row.get('text', '')))
        if re.fullmatch(pattern, text):
            found.append(row)
    return found


def _template():
    # Revalidate bytes on every read, including a long-lived reader. A changed
    # or missing resource cannot silently reuse a formerly trusted template.
    data = (RESOURCE_DIR / 'blue-orb-q01.png').read_bytes()
    if hashlib.sha256(data).hexdigest() != TEMPLATE_SHA256:
        raise ValueError('reward template hash mismatch')
    with Image.open(io.BytesIO(data)) as opened:
        if opened.format != 'PNG' or opened.size != TEMPLATE_SIZE or opened.mode != 'RGB':
            raise ValueError('reward template layout mismatch')
        return np.array(opened)


def _matches(rgb, template):
    import cv2

    x1, y1, x2, y2 = SCAN_BOUNDS
    search = rgb[y1:y2, x1:x2]
    scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    if not np.all(np.isfinite(scores)):
        return [], True
    candidates = []
    while True:
        unused, score, unused_location, location = cv2.minMaxLoc(scores)
        if score < .75:
            break
        x, y = location
        # Only the response cluster of the same control is suppressed. A
        # distinct close peak is retained below and rejects the observation.
        scores[max(0, y-8):y+9, max(0, x-8):x+9] = -1
        if score < .90 or len(candidates) >= 8:
            return [], True
        w, h = TEMPLATE_SIZE
        bounds = [x+x1, y+y1, x+x1+w, y+y1+h]
        crop = rgb[bounds[1]:bounds[3], bounds[0]:bounds[2]]
        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        blue = float(np.mean((hsv[:, :, 0] >= 93) & (hsv[:, :, 0] <= 116)
                             & (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 130)))
        pale = float(np.mean((hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 116)
                             & (hsv[:, :, 1] < 140) & (hsv[:, :, 2] >= 200)))
        if blue < .40 or pale < .10:
            return [], True
        center = [(bounds[0]+bounds[2]) // 2, (bounds[1]+bounds[3]) // 2]
        if any(np.hypot(center[0]-item['center'][0], center[1]-item['center'][1])
               < min(TEMPLATE_SIZE)*.85 for item in candidates):
            return [], True
        candidates.append({'kind': 'blue_orb', 'bounds': bounds, 'center': center,
            'score': round(float(score), 4), 'template_id': TEMPLATE_ID,
            'blue_fraction': round(blue, 4), 'pale_rim_fraction': round(pale, 4)})
    return sorted(candidates, key=lambda item: (item['center'][1], item['center'][0])), False


def detect(image, page, rows, snapshot_id):
    """Locate complete controls in the known native preparation reward area.

    ``rows`` must be the local OCR of this very image. ``snapshot_id`` is the
    caller-verified PNG SHA-256; its format alone is not input authorization.
    Independent blue orbs are individually located. Uncertain or overlapping
    matches withhold all targets, so their count cannot establish an effect.
    """
    result = {'snapshot_id': snapshot_id, 'scanned': False,
        'scope': 'native_preparation_blue_orbs_1920', 'scan_bounds': list(SCAN_BOUNDS),
        'targets': [], 'all_rewards_cleared': None, 'interaction_required': False,
        'interaction_evidence': [], 'area_fully_visible': False,
        'input_allowed': False, 'uncertain': False, 'reason': 'unsupported_page'}
    if page == 'shop':
        result['reason'] = 'shop_occludes_reward_area'
        return result
    if page != 'preparation':
        return result
    if not isinstance(snapshot_id, str) or not re.fullmatch(r'[a-f0-9]{64}', snapshot_id):
        result['reason'] = 'missing_frame_binding'
        return result
    if not isinstance(image, Image.Image) or image.size != (1920, 1080):
        result['reason'] = 'unsupported_frame_size'
        return result
    if not isinstance(rows, (list, tuple)):
        result['reason'] = 'native_preparation_anchors_missing'
        return result
    anchors = []
    for pattern, bounds in (
            ('备战阶段', (410, 20, 540, 65)),
            (r'[1-3]-[1-9]', (420, 50, 520, 105)),
            ('出战', (1760, 710, 1875, 790)),
            ('商店', (1575, 950, 1675, 1020))):
        matching = _rows(rows, pattern, bounds)
        if len(matching) != 1:
            result['reason'] = 'native_preparation_anchors_missing'
            return result
        anchors.extend(matching)
    if (_rows(rows, '收起', (1554, 943, 1694, 1020), minimum=.72)
            or _rows(rows, r'刷新.*', (1510, 420, 1720, 580), minimum=.72)):
        result['reason'] = 'conflicting_shop_state'
        return result
    rgb = np.array(image.convert('RGB'))
    result['image_rgb_sha256'] = hashlib.sha256(rgb.tobytes()).hexdigest()
    result['anchor_rows'] = anchors
    # q02 exposes this native side prompt after the historical grouped claim.
    # Its current Cancel label is enough to stop mechanical claims. We do not
    # infer equipment identity, suitability or the result of either old click.
    prompts = _rows(rows, '取消', (1700, 235, 1890, 295), minimum=.72)
    if prompts:
        result.update(interaction_required=True, interaction_evidence=[
            {'kind': 'native_side_prompt', 'source_row': row,
             'bounds': [1540, 150, 1890, 295]} for row in prompts])
    try:
        targets, uncertain = _matches(rgb, _template())
    except (OSError, ValueError, TypeError, ImportError):
        result['reason'] = 'reward_template_unavailable'
        return result
    result.update(scanned=True, uncertain=uncertain,
        targets=[{**item, 'snapshot_id': snapshot_id} for item in targets],
        area_fully_visible=not bool(prompts),
        template={'id': TEMPLATE_ID, 'sha256': TEMPLATE_SHA256,
                  'source_sha256': TEMPLATE_SOURCE_SHA256,
                  'source_roi': list(TEMPLATE_SOURCE_ROI)})
    result['input_allowed'] = bool(targets) and not uncertain and not prompts
    if prompts:
        result['reason'] = 'native_side_prompt_requires_review'
    elif uncertain:
        result['reason'] = 'ambiguous_or_partial_blue_orb'
    elif targets:
        result['reason'] = 'visible_blue_orbs'
    else:
        result['reason'] = 'no_blue_orb_match_in_scanned_region'
    return result
