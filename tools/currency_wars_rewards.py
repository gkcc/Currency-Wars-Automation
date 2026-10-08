"""Read-only native blue/gray-orb observations from the supplied complete frame.

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


# Foreground appearance calibration only: these are two crops of one retained
# native frame, not independent examples of new positions or game outcomes.
_NATIVE_REFERENCES = (
    ('blue_orb', 'initial-blue.png', 'd362f664af664e6864a55193f20d5d9faadf5122c0eb0651b154b4c079aa55fd', (72, 73)),
    ('gray_orb', 'initial-gray.png', '72d266178de25affc73cc0c1d5fd68a047e8a485258b76d394a3bfc0c98d69fd', (47, 46)),
)


def _native_references(legacy):
    references = [('blue_orb', TEMPLATE_ID, legacy)]
    for kind, filename, digest, size in _NATIVE_REFERENCES:
        payload = (RESOURCE_DIR / filename).read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError('native reward appearance resource changed')
        with Image.open(io.BytesIO(payload)) as opened:
            if opened.format != 'PNG' or opened.mode != 'RGB' or opened.size != size:
                raise ValueError('native reward appearance layout changed')
            references.append((kind, filename, np.array(opened)))
    return references


def _orb_masks(shape):
    height, width = shape[:2]
    y, x = np.ogrid[:height, :width]
    radius = np.sqrt(((x-(width-1)/2)/(width/2))**2 + ((y-(height-1)/2)/(height/2))**2)
    return radius <= .92, (radius >= .55) & (radius <= .94)


def _masked_ncc(left, right, mask):
    a, b = left[mask].astype(np.float64), right[mask].astype(np.float64)
    a -= a.mean(axis=0)
    b -= b.mean(axis=0)
    denominator = float(np.sqrt(np.sum(a*a) * np.sum(b*b)))
    return None if denominator < 1. else float(np.sum(a*b) / denominator)


def _orb_appearance(crop, reference, kind):
    """Current appearance comparison; it never supplies a frame or input proof.

    A foreground annulus preserves the bright native perimeter while avoiding
    the random scene outside it. Interior shape, color and bounded patchwise
    RGB differences separately veto flat fills, darkening and partial covers.
    No registration is performed: callers cannot slide an old target to make
    it pass. Thresholds are calibrated on three times of the same two orbs.
    """
    import cv2
    result = {'trusted': False, 'reasons': []}
    if (not isinstance(crop, np.ndarray) or not isinstance(reference, np.ndarray)
            or crop.dtype != np.uint8 or reference.dtype != np.uint8
            or crop.shape != reference.shape or crop.ndim != 3 or crop.shape[2] != 3
            or kind not in ('blue_orb', 'gray_orb')
            or not 36 <= min(crop.shape[:2]) <= max(crop.shape[:2]) <= 82
            or max(crop.shape[:2]) / min(crop.shape[:2]) > 1.12):
        result['reasons'].append('unsupported_complete_orb_extent')
        return result
    body, ring = _orb_masks(crop.shape)
    ring_score, body_score = _masked_ncc(crop, reference, ring), _masked_ncc(crop, reference, body)
    difference = np.abs(crop.astype(np.int16)-reference.astype(np.int16))
    height, width = crop.shape[:2]
    patch_delta = []
    for iy in range(4):
        for ix in range(4):
            y1, y2 = iy*height//4, (iy+1)*height//4
            x1, x2 = ix*width//4, (ix+1)*width//4
            mask = body[y1:y2, x1:x2]
            if np.count_nonzero(mask) >= 10:
                patch_delta.append(float(difference[y1:y2, x1:x2][mask].mean(axis=0).max()))
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    pixels = hsv[body]
    blue = float(np.mean((pixels[:, 0] >= 93) & (pixels[:, 0] <= 120)
                         & (pixels[:, 1] >= 70) & (pixels[:, 2] >= 150)))
    neutral = float(np.mean((pixels[:, 1] < 80) & (pixels[:, 2] >= 100)))
    value = float(np.median(pixels[:, 2]))
    if ring_score is None or ring_score < .96:
        result['reasons'].append('foreground_ring_changed')
    if body_score is None or body_score < .90:
        result['reasons'].append('interior_shape_changed')
    if not patch_delta or max(patch_delta) > 32.:
        result['reasons'].append('local_color_or_exposure_changed')
    if (kind == 'blue_orb' and (blue < .70 or value < 210)
            or kind == 'gray_orb' and (neutral < .60 or not 100 <= value <= 235)):
        result['reasons'].append('native_orb_color_not_supported')
    result.update(ring_score=round(ring_score, 6) if ring_score is not None else None,
                  body_score=round(body_score, 6) if body_score is not None else None,
                  max_patch_delta=round(max(patch_delta), 4) if patch_delta else None,
                  blue_fraction=round(blue, 4), neutral_fraction=round(neutral, 4),
                  median_value=value, trusted=not result['reasons'])
    return result


def target_pair_stable(original_rgb, current_rgb, target):
    """Same current extent, independently of automatic vs supervisor origin.

    The caller authenticates both immutable frames, page, stage and epoch.
    This comparator never upgrades a supervisor annotation to native reading.
    """
    if (not isinstance(original_rgb, np.ndarray) or not isinstance(current_rgb, np.ndarray)
            or original_rgb.shape != (1080, 1920, 3) or current_rgb.shape != original_rgb.shape
            or not isinstance(target, dict) or not _inside(target.get('bounds'), SCAN_BOUNDS)
            or any(type(edge) is not int for edge in target['bounds'])):
        return False
    x, y, right, bottom = target['bounds']
    return _orb_appearance(current_rgb[y:bottom, x:right], original_rgb[y:bottom, x:right],
                           target.get('kind'))['trusted']


def _matches(rgb, template, diagnostics=None):
    """Locate reference foregrounds across the area, never a stored position.

    Preserve rejected proposals for diagnosis. A local ambiguity still stops
    automatic input; neither no candidates nor rejection means rewards clear.
    """
    import cv2
    diagnostics = diagnostics if diagnostics is not None else []
    x1, y1, x2, y2 = SCAN_BOUNDS
    search = rgb[y1:y2, x1:x2]
    proposals = []
    for kind, reference_id, reference in _native_references(template):
        body, ring = _orb_masks(reference.shape)
        response = cv2.matchTemplate(search.astype(np.float32), reference.astype(np.float32),
                                     cv2.TM_CCOEFF_NORMED, mask=ring.astype(np.uint8))
        # A flat local window has undefined NCC and is not a positive target.
        response[~np.isfinite(response)] = -1.
        height, width = reference.shape[:2]
        for unused in range(12):
            unused_min, score, unused_loc, location = cv2.minMaxLoc(response)
            if score < .70:
                break
            x, y = location
            response[max(0, y-height//2):y+height//2+1,
                     max(0, x-width//2):x+width//2+1] = -1.
            bounds = [x+x1, y+y1, x+x1+width, y+y1+height]
            appearance = _orb_appearance(search[y:y+height, x:x+width], reference, kind)
            proposals.append({'kind': kind, 'bounds': bounds,
                'center': [(bounds[0]+bounds[2])//2, (bounds[1]+bounds[3])//2],
                'template_id': kind + '_foreground_v1', 'appearance_reference': reference_id,
                'appearance_sha256': (TEMPLATE_SHA256 if reference_id == TEMPLATE_ID else
                    next(item[2] for item in _NATIVE_REFERENCES if item[1] == reference_id)),
                'score': round(float(score), 6), 'appearance': appearance})
        else:
            diagnostics.append({'kind': kind, 'reason': 'proposal_limit', 'trusted': False})
    # Alternate reference sizes of the same orb share one cluster; do not let
    # its weaker reference veto a trusted matching appearance of that control.
    groups = []
    for proposal in sorted(proposals, key=lambda value: value['score'], reverse=True):
        group = next((items for items in groups if proposal['kind'] == items[0]['kind']
                      and max(abs(a-b) for a, b in zip(proposal['center'], items[0]['center'])) <= 8), None)
        if group is None:
            groups.append([proposal])
        else:
            group.append(proposal)
    candidates, uncertain = [], bool(diagnostics)
    for items in groups:
        accepted = next((value for value in items if value['appearance']['trusted']), None)
        diagnostics.extend({**value, 'trusted': value['appearance']['trusted']} for value in items)
        if accepted is None:
            uncertain = True
        else:
            candidates.append(accepted)
    if len(candidates) > 8:
        uncertain = True
        diagnostics.append({'reason': 'target_limit', 'trusted': False})
    for index, left in enumerate(candidates):
        for right in candidates[index+1:]:
            a, b = left['bounds'], right['bounds']
            if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                uncertain = True
                diagnostics.append({'reason': 'overlapping_targets', 'trusted': False})
    return sorted(candidates, key=lambda item: (item['center'][1], item['center'][0])), uncertain


def detect(image, page, rows, snapshot_id):
    """Locate complete controls in the known native preparation reward area.

    ``rows`` must be the local OCR of this very image. ``snapshot_id`` is the
    caller-verified PNG SHA-256; its format alone is not input authorization.
    Supported blue/gray orbs are individually located. Uncertain or overlapping
    proposals withhold input; retained diagnostics never establish an effect.
    """
    result = {'snapshot_id': snapshot_id, 'scanned': False,
        'scope': 'native_preparation_orbs_1920', 'scan_bounds': list(SCAN_BOUNDS),
        'targets': [], 'all_rewards_cleared': None, 'interaction_required': False,
        'interaction_evidence': [], 'area_fully_visible': False,
        'input_allowed': False, 'uncertain': False, 'candidate_diagnostics': [], 'reason': 'unsupported_page'}
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
        targets, uncertain = _matches(rgb, _template(), result['candidate_diagnostics'])
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
        result['reason'] = 'ambiguous_or_partial_orb'
    elif targets:
        result['reason'] = 'visible_supported_orbs'
    else:
        result['reason'] = 'no_supported_orb_match_in_scanned_region'
    return result


def _context_images(original, actual, original_png, current_png, *, allow_coin_change=False):
    """Authenticate exposed page anchors independently of orb recognition.

    A native detector's unknown remains unknown. It is neither evidence of an
    overlay nor permission for a click. Only the separate current target guard
    may authorize that target. After a free claim, only the bound gold HUD's
    numeric row may change; every other visible semantic row still participates.
    """
    import cv2
    from currency_wars_visual_guards import _navigation_row, _navigation_visible_semantics
    try:
        stage = original.get('fields', {}).get('stage')
        if (original.get('page') not in ('preparation', 'shop') or actual.get('page') != original['page']
                or not isinstance(stage, str) or not re.fullmatch(r'[1-3]-[1-9]', stage)
                or actual.get('fields', {}).get('stage') != stage
                or any(not obs.get(key) for obs in (original, actual)
                       for key in ('capture_request_id', 'frame_id', 'snapshot_id'))
                or original['frame_id'] == actual['frame_id']
                or original['capture_request_id'] == actual['capture_request_id']):
            return None
        semantic_pair = (original, actual)
        if allow_coin_change:
            from currency_wars_perception import GOLD_HUD
            semantic_pair = tuple({**obs, 'rows': [row for row in obs.get('rows', [])
                if not (_inside(row.get('box'), GOLD_HUD)
                        and re.fullmatch(r'\d+', re.sub(r'\s+', '', str(row.get('text', '')))))]}
                for obs in semantic_pair)
        if not _navigation_visible_semantics(*semantic_pair):
            return None
        for obs in (original, actual):
            rows = obs.get('rows', [])
            if (obs['page'] == 'preparation' and (
                    _rows(rows, '收起', (1554, 943, 1694, 1020), minimum=.72)
                    or _rows(rows, r'刷新.*', (1510, 420, 1720, 580), minimum=.72))
                    or _rows(rows, '取消', (1700, 235, 1890, 295), minimum=.72)
                    or obs.get('semantic', {}).get('rewards', {}).get('interaction_required')):
                return None
        images = []
        for payload, obs in ((original_png, original), (current_png, actual)):
            if hashlib.sha256(payload).hexdigest() != obs['snapshot_id']:
                return None
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != 'PNG' or image.size != (1920, 1080):
                    return None
                image.load()
                images.append(np.array(image.convert('RGB')))
        shop = original['page'] == 'shop'
        anchors = [('备战阶段', [220, 40, 365, 92] if shop else [410, 20, 540, 65]),
                   (stage, [225, 80, 355, 140] if shop else [420, 50, 520, 105]),
                   ('出战', [1760, 710, 1875, 790]),
                   ('收起' if original['page'] == 'shop' else '商店', [1554, 943, 1694, 1020])]
        for label, bounds in anchors:
            if _navigation_row(original, actual, label, images, bounds, target=label == '收起') is not None:
                continue
            # q00's native shop title is gray, below the navigation helper's
            # light-ink cutoff. Only this non-clicked header may use exact
            # exposed RGB support plus native text/geometry; other anchors
            # and the click target keep the existing navigation guard.
            if not shop or label != '备战阶段':
                return None
            rows = [_rows(obs.get('rows', []), label, bounds) for obs in (original, actual)]
            if (any(len(found) != 1 for found in rows)
                    or rows[0][0]['box'] != rows[1][0]['box']):
                return None
            x, y, right, bottom = map(int, rows[0][0]['box'])
            crops = [rgb[y-2:bottom+2, x-2:right+2] for rgb in images]
            gray = cv2.cvtColor(crops[0], cv2.COLOR_RGB2GRAY).astype(np.int16)
            if (np.ptp(gray) < 64 or np.count_nonzero(np.abs(np.diff(gray, axis=1)) > 16) < 24
                    or np.mean(np.max(np.abs(crops[0].astype(np.int16)
                                             - crops[1].astype(np.int16)), axis=2) > 16) > .005):
                return None
        return images
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None


def stable_reward_target(original, actual, original_png, current_png, target):
    """An explicit target stays exposed at its original location; no relocation."""
    images = _context_images(original, actual, original_png, current_png)
    return bool(images is not None and original.get('page') == 'preparation'
                and target_pair_stable(images[0], images[1], target))


def stable_layout(original, actual, original_png, current_png):
    """Same native layout, then all current target appearances; no clear inference."""
    try:
        images = _context_images(original, actual, original_png, current_png)
        if images is None:
            return False
        if original['page'] == 'shop':
            return True  # The only permitted operation here is close_shop.
        scans = [obs.get('semantic', {}).get('rewards', {}) for obs in (original, actual)]
        for obs, scan, rgb in zip((original, actual), scans, images):
            if (scan.get('snapshot_id') != obs['snapshot_id'] or scan.get('scanned') is not True
                    or scan.get('uncertain') or scan.get('interaction_required')
                    or scan.get('area_fully_visible') is not True
                    or scan.get('image_rgb_sha256') != hashlib.sha256(rgb.tobytes()).hexdigest()):
                return False
        targets = [[(t.get('kind'), t.get('template_id'), t.get('bounds'), t.get('center'))
                    for t in scan.get('targets', [])] for scan in scans]
        if targets[0] != targets[1] or not targets[0]:
            return False
        for target in scans[0]['targets']:
            if not target_pair_stable(images[0], images[1], target):
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return False


def _effect_bounds(target):
    if (not isinstance(target, dict) or target.get('kind') not in ('blue_orb', 'gray_orb')
            or not _inside(target.get('bounds'), SCAN_BOUNDS)
            or any(type(value) is not int for value in target['bounds'])):
        return None
    x, y, right, bottom = target['bounds']
    if not 36 <= min(right-x, bottom-y) <= max(right-x, bottom-y) <= 82:
        return None
    return x, y, right, bottom


def _reward_background(rgb, target):
    """Conservative local background evidence, never an item-grant detector.

    An absent template alone could be a cover. Require a textured, continuous
    background compatible with the exposed surround. The thresholds reject
    an opaque/flat replacement; unsupported textures deliberately stay unknown.
    """
    import cv2
    bounds = _effect_bounds(target)
    if bounds is None:
        return {'plausible': False, 'reason': 'unsupported_target_geometry'}
    x, y, right, bottom = bounds
    left, top = max(SCAN_BOUNDS[0], x-10), max(SCAN_BOUNDS[1], y-10)
    end_x, end_y = min(SCAN_BOUNDS[2], right+10), min(SCAN_BOUNDS[3], bottom+10)
    patch = rgb[top:end_y, left:end_x].astype(np.float64)
    yy, xx = np.mgrid[top:end_y, left:end_x]
    dx = (xx-(x+right-1)/2) / ((right-x)/2)
    dy = (yy-(y+bottom-1)/2) / ((bottom-y)/2)
    radius = dx*dx + dy*dy
    core, surround = radius <= .60**2, (radius >= 1.08**2) & (radius <= 1.50**2)
    if np.count_nonzero(core) < 100 or np.count_nonzero(surround) < 100:
        return {'plausible': False, 'reason': 'insufficient_exposed_surround'}
    design = np.stack((np.ones_like(dx), dx, dy), axis=-1)
    coefficients = np.linalg.lstsq(design[surround], patch[surround], rcond=None)[0]
    residual = np.max(np.abs(patch-design @ coefficients), axis=2)
    core_fit, surround_fit = (float(np.percentile(residual[mask], 95)) for mask in (core, surround))
    gray = cv2.cvtColor(patch.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float64)
    core_std, surround_std = float(np.std(gray[core])), float(np.std(gray[surround]))
    # A matching flat paint patch cannot prove disappearance. A genuinely flat
    # game background is likewise unsupported until separately observed.
    textured = core_std >= .35 and surround_std >= .35
    continuous = core_fit <= min(40., max(20., 3*surround_fit))
    return {'plausible': bool(textured and continuous),
        'reason': 'textured_background_continuity' if textured and continuous else
            'flat_or_covered_target_region' if not textured else 'target_region_not_background',
        'core_background_residual_p95': round(core_fit, 4),
        'surround_background_residual_p95': round(surround_fit, 4),
        'core_gray_std': round(core_std, 4), 'surround_gray_std': round(surround_std, 4),
        'bounds': [left, top, end_x, end_y]}


def _effect_siblings(original, before, after, target):
    scan = original.get('semantic', {}).get('rewards', {})
    values = scan.get('targets', [])
    if not isinstance(values, list) or len(values) > 8:
        return None
    if values and scan.get('snapshot_id') != original.get('snapshot_id'):
        return None
    x, y, right, bottom = target['bounds']
    center = ((x+right)/2, (y+bottom)/2)
    siblings = []
    for other in values:
        bounds = _effect_bounds(other)
        if bounds is None:
            return None
        ox, oy, oright, obottom = bounds
        distance = max(abs((ox+oright)/2-center[0]), abs((oy+obottom)/2-center[1]))
        if other['kind'] == target['kind'] and distance <= 8:
            continue  # Native bounds and the explicit supervisor ROI may differ.
        if not target_pair_stable(before, after, other):
            return None
        siblings.append(other)
    return siblings


def _outside_target_change(before, after, target, siblings):
    """Reject unexplained strong changes without demanding exact background RGB."""
    import cv2
    sx, sy, ex, ey = SCAN_BOUNDS
    difference = np.max(np.abs(before[sy:ey, sx:ex].astype(np.int16)
                               - after[sy:ey, sx:ex].astype(np.int16)), axis=2)
    changed = (difference > 48).astype(np.uint8)
    for item in [target, *siblings]:
        x, y, right, bottom = item['bounds']
        changed[max(0, y-sy-3):min(ey-sy, bottom-sy+3),
                max(0, x-sx-3):min(ex-sx, right-sx+3)] = 0
    unused, unused_labels, stats, unused_centers = cv2.connectedComponentsWithStats(changed, 8)
    return int(np.max(stats[1:, cv2.CC_STAT_AREA])) if len(stats) > 1 else 0


def _source_appearance_veto(before, after, target, siblings):
    """Search the source appearance only to veto disappearance, never to click."""
    import cv2
    x, y, right, bottom = target['bounds']
    source = cv2.cvtColor(before[y:bottom, x:right], cv2.COLOR_RGB2GRAY)
    h, w = source.shape
    yy, xx = np.mgrid[:h, :w]
    mask = ((((xx-(w-1)/2)/(w*.44))**2 + ((yy-(h-1)/2)/(h*.44))**2) <= 1).astype(np.uint8)
    if float(np.std(source[mask.astype(bool)])) < 8:
        return {'reason': 'source_appearance_has_insufficient_structure'}
    sx, sy, ex, ey = SCAN_BOUNDS
    search = cv2.cvtColor(after[sy:ey, sx:ex], cv2.COLOR_RGB2GRAY)
    scores = cv2.matchTemplate(search, source, cv2.TM_CCOEFF_NORMED, mask=mask)
    if not np.all(np.isfinite(scores)):
        return {'reason': 'source_appearance_probe_nonfinite'}
    for other in siblings:
        if other['kind'] != target['kind']:
            continue
        ox, oy, oright, obottom = other['bounds']
        px, py = int(round((ox+oright-w)/2-sx)), int(round((oy+obottom-h)/2-sy))
        scores[max(0, py-8):py+9, max(0, px-8):px+9] = -1
    unused, score, unused_location, location = cv2.minMaxLoc(scores)
    # Lower-than-input confidence is deliberately a rejection threshold. It
    # may withhold a valid removal, but cannot approve a weakly matched click.
    if score >= .65:
        px, py = location
        return {'reason': 'source_appearance_present_or_moved', 'veto_score': round(float(score), 4),
                'bounds': [sx+px, sy+py, sx+px+w, sy+py+h]}
    return None


def target_effect(original, actual, original_png, current_png, target):
    """One target's bounded visual effect; no receipt or phase authorization.

    Caller must verify original delivery/fees and require a second distinct
    stable absent frame. Unknown native siblings remain unknown. These guards
    cannot identify a granted item or rule out a perfect background imitation.
    """
    result = {'state': 'unknown', 'reason': 'reward_effect_context_unverified',
              'scope': list(SCAN_BOUNDS), 'all_rewards_cleared': None}
    try:
        if _effect_bounds(target) is None or original.get('page') != 'preparation':
            return {**result, 'reason': 'unsupported_reward_target'}
        images = _context_images(original, actual, original_png, current_png, allow_coin_change=True)
        if images is None:
            return result
        before, after = images
        if not target_pair_stable(before, before, target):
            return {**result, 'reason': 'source_target_not_exposed'}
        if target_pair_stable(before, after, target):
            return {**result, 'state': 'present', 'reason': 'source_target_still_present'}
        siblings = _effect_siblings(original, before, after, target)
        if siblings is None:
            return {**result, 'reason': 'known_sibling_changed_or_unbound'}
        outside_change = _outside_target_change(before, after, target, siblings)
        if outside_change >= 24:
            return {**result, 'reason': 'unexplained_change_outside_target',
                    'largest_changed_component': outside_change}
        background = _reward_background(after, target)
        if not background['plausible']:
            return {**result, 'reason': background['reason'], 'background': background}
        veto = _source_appearance_veto(before, after, target, siblings)
        if veto is not None:
            return {**result, 'reason': veto['reason'], 'source_probe': veto}
        return {**result, 'state': 'absent', 'reason': 'target_absent_in_exposed_reward_region',
                'background': background, 'known_siblings_checked': len(siblings),
                'largest_changed_component': outside_change}
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, np.linalg.LinAlgError):
        return result


def stable_absence(previous, actual, previous_png, current_png, target):
    """Compare two exposed empty target regions; caller retains both provenances."""
    try:
        if _effect_bounds(target) is None or previous.get('page') != 'preparation':
            return False
        images = _context_images(previous, actual, previous_png, current_png, allow_coin_change=False)
        if images is None:
            return False
        readings = [_reward_background(rgb, target) for rgb in images]
        if not all(value['plausible'] for value in readings):
            return False
        x, y, right, bottom = readings[0]['bounds']
        delta = np.max(np.abs(images[0][y:bottom, x:right].astype(np.int16)
                              - images[1][y:bottom, x:right].astype(np.int16)), axis=2)
        # Local background animation may change pixels. A changed target or
        # cover must also pass each frame's structural-background check.
        return bool(float(np.median(delta)) <= 16 and float(np.percentile(delta, 95)) <= 40
                    and float(np.mean(delta > 48)) <= .01)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, np.linalg.LinAlgError):
        return False
