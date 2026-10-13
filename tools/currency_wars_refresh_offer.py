"""Current native refresh widget facts; no input or inferred free-count zero."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import re
import time

import cv2
import numpy as np
from PIL import Image


SCHEMA = 'currency-wars-refresh-offer/v1'
VERSION = 1
WIDGET_BOUNDS = [1535, 430, 1700, 595]
TITLE_BOUNDS = [1565, 464, 1674, 509]
SHORTCUT_BOUNDS = [1595, 430, 1640, 463]
VALUE_BOUNDS = [1585, 510, 1661, 558]
NUMBER_BOUNDS = {'free': [1603, 516, 1636, 554], 'paid': [1626, 516, 1665, 554]}
NUMBER_GUARD_BOUNDS = [1585, 510, 1675, 558]
COIN_SEARCH_BOUNDS = [1588, 514, 1628, 552]
COIN_MIN_SCORE = .97
# Public retained f02.png; exact source/crop and limited coverage in SOURCES.json.
COIN_RGB_SHA256 = 'c4beaf913b8fa991467f15154b6b07928aec1394d53f0a59ab0cf23258d87278'
COIN_PATH = Path(__file__).with_name('refresh_offer_resources') / 'currency_coin.png'


def expects_offer(observed):
    version = (observed.get('read_contract') or {}).get('version')
    return ('refresh_offer' in (observed.get('semantic') or {})
            or type(version) is int and version >= 2)


def _rgb_hash(image, bounds):
    return hashlib.sha256(image.crop(bounds).convert('RGB').tobytes()).hexdigest()


def _inside(box, region):
    return (isinstance(box, list) and len(box) == 4 and all(type(v) is int for v in box)
            and region[0] <= box[0] < box[2] <= region[2]
            and region[1] <= box[1] < box[3] <= region[3])


def _confident(value):
    return type(value) in (float, int) and .90 <= value <= 1.


def _raw(row):
    return str(row.get('raw_text', row.get('text', ''))).strip()


def _number(raw):
    return int(raw) if re.fullmatch(r'[1-9][0-9]{0,5}', raw) else None


def _coin(image):
    result = {'method': 'observed_native_coin_template', 'search_bounds': list(COIN_SEARCH_BOUNDS),
              'template_rgb_sha256': COIN_RGB_SHA256, 'threshold': COIN_MIN_SCORE,
              'present': None, 'score': None, 'bounds': None}
    try:
        with Image.open(COIN_PATH) as opened:
            template = opened.convert('RGB')
        if template.size != (33, 32) or hashlib.sha256(template.tobytes()).hexdigest() != COIN_RGB_SHA256:
            raise ValueError('currency template source mismatch')
        gray = cv2.cvtColor(np.array(template), cv2.COLOR_RGB2GRAY)
        search = cv2.cvtColor(np.array(image.crop(COIN_SEARCH_BOUNDS)), cv2.COLOR_RGB2GRAY)
        if float(gray.std()) <= 1.:
            raise ValueError('currency template has no discriminating pixels')
        scores = cv2.matchTemplate(search, gray, cv2.TM_CCOEFF_NORMED)
        unused, score, unused_location, (x, y) = cv2.minMaxLoc(scores)
        if not np.isfinite(score):
            raise ValueError('currency match is not finite')
        left, top = COIN_SEARCH_BOUNDS[0] + x, COIN_SEARCH_BOUNDS[1] + y
        result.update(score=round(float(score), 6), bounds=[left, top, left + 33, top + 32],
                      present=bool(score >= COIN_MIN_SCORE and float(search[y:y+32, x:x+33].std()) > 1.))
        if result['present'] is False:
            # The retained disabled widget changes neutral background, not the
            # complete coin's chroma bitmap. Keep the normal gray path intact.
            rgb = np.array(image.crop(COIN_SEARCH_BOUNDS)).astype(np.float32)
            reference = np.array(template).astype(np.float32)
            chroma = reference[:, :, 0] - reference[:, :, 2]
            observed = rgb[:, :, 0] - rgb[:, :, 2]
            scores = cv2.matchTemplate(observed, chroma, cv2.TM_CCOEFF_NORMED)
            _, chroma_score, _, (x, y) = cv2.minMaxLoc(scores)
            patch = rgb[y:y+32, x:x+33]
            delta = np.abs((patch[:, :, 0] - patch[:, :, 2]) - chroma)
            interior_delta = float(np.abs(patch-reference)[chroma > 80].mean())
            background = cv2.cvtColor(np.array(image.crop(NUMBER_GUARD_BOUNDS)), cv2.COLOR_RGB2HSV)
            dark_fraction = float(np.mean(background[:, :, 2] <= 80))
            if (dark_fraction >= .75 and np.isfinite(chroma_score) and chroma_score >= .995
                    and float(delta.max()) <= 4. and float(delta.mean()) <= .30
                    and interior_delta <= 3.):
                left, top = COIN_SEARCH_BOUNDS[0]+x, COIN_SEARCH_BOUNDS[1]+y
                result.update(present=True, score=round(float(chroma_score), 6),
                    bounds=[left, top, left+33, top+32], appearance='observed_dark_disabled',
                    chroma_max_delta=round(float(delta.max()), 6),
                    chroma_mean_delta=round(float(delta.mean()), 6),
                    interior_rgb_mean_delta=round(interior_delta, 6),
                    dark_fraction=round(dark_fraction, 6))
    except (OSError, ValueError, cv2.error) as exc:
        result['error'] = str(exc)
    return result


def _number_extent(image, mode, coin):
    """Reject dark numeric ink outside the supported crop, without more OCR.

    This is a bound for the observed dark-on-light layout, not a digit recognizer
    or proof that unobserved multi-digit values have been recognized correctly.
    """
    left, top, unused_right, unused_bottom = NUMBER_GUARD_BOUNDS
    gray = cv2.cvtColor(np.array(image.crop(NUMBER_GUARD_BOUNDS)), cv2.COLOR_RGB2GRAY)
    disabled = mode == 'paid' and coin.get('appearance') == 'observed_dark_disabled'
    if disabled:
        hsv = cv2.cvtColor(np.array(image.crop(NUMBER_GUARD_BOUNDS)), cv2.COLOR_RGB2HSV)
        ink = (((hsv[:, :, 0] <= 10) | (hsv[:, :, 0] >= 170))
               & (hsv[:, :, 1] >= 120) & (hsv[:, :, 2] >= 100))
        neutral = (hsv[:, :, 1] <= 70) & (hsv[:, :, 2] >= 100)
    else:
        ink = gray < 100
    if mode == 'paid':
        x1, y1, x2, y2 = coin['bounds']
        ink[y1-top:y2-top, x1-left:x2-left] = False
        if disabled:
            neutral[y1-top:y2-top, x1-left:x2-left] = False
    ys, xs = np.nonzero(ink)
    extent = [int(xs.min()) + left, int(ys.min()) + top,
              int(xs.max()) + left + 1, int(ys.max()) + top + 1] if xs.size else None
    result = {'method': 'observed_dark_numeric_extent', 'bounds': list(NUMBER_GUARD_BOUNDS),
            'gray_below': 100, 'rgb_sha256': _rgb_hash(image, NUMBER_GUARD_BOUNDS),
            'excluded_coin_bounds': list(coin['bounds']) if mode == 'paid' else None,
            'foreground_pixels': int(xs.size), 'foreground_bounds': extent,
            'inside_numeric_crop': _inside(extent, NUMBER_BOUNDS[mode])}
    if disabled:
        result.update(method='observed_disabled_red_numeric_extent', gray_below=None,
                      hsv_red_bounds={'hue': [[0, 10], [170, 179]], 'saturation_min': 120, 'value_min': 100})
        components = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)[2][1:]
        # A pale extra digit must not disappear when isolating the observed red price.
        complete = (not np.any(neutral) and result['inside_numeric_crop'] and 1 <= len(components) <= 6
                    and all(2 <= w <= 33 and 14 <= h <= 32 and area >= 25
                            for x, y, w, h, area in components))
        padded = [extent[0]-2, extent[1]-2, extent[2]+2, extent[3]+2] if extent else None
        result.update(component_count=len(components), unsupported_neutral_pixels=int(neutral.sum()), ocr_bounds=padded,
                      inside_numeric_crop=bool(complete and _inside(padded, NUMBER_BOUNDS[mode])))
    return result


def _shortcut_crop(image, engine):
    """Only a missing global row may use one complete original shortcut ROI."""
    found, _ = engine(np.array(image.crop(SHORTCUT_BOUNDS)), use_cls=False)
    rows = []
    for points, text, confidence in found or []:
        polygon = np.array(points)
        rows.append({'raw_text': str(text), 'confidence': float(confidence),
                     'box': [int(np.floor(polygon[:, 0].min()))+SHORTCUT_BOUNDS[0],
                             int(np.floor(polygon[:, 1].min()))+SHORTCUT_BOUNDS[1],
                             int(np.ceil(polygon[:, 0].max()))+SHORTCUT_BOUNDS[0],
                             int(np.ceil(polygon[:, 1].max()))+SHORTCUT_BOUNDS[1]]})
    if len(rows) != 1 or _raw(rows[0]) != 'D' or not _confident(rows[0]['confidence']):
        return None
    if not _inside(rows[0]['box'], SHORTCUT_BOUNDS):
        return None
    return {**rows[0], 'source': 'current_shortcut_crop_ocr', 'crop_bounds': list(SHORTCUT_BOUNDS),
            'input_transform': 'original_rgb_crop_no_resize_or_padding',
            'rgb_sha256': _rgb_hash(image, SHORTCUT_BOUNDS), 'raw_result': rows}


def _shortcut_conflict(rows):
    for row in rows:
        box = row.get('box')
        if (isinstance(box, list) and len(box) == 4 and _confident(row.get('confidence'))
                and max(box[0], SHORTCUT_BOUNDS[0]) < min(box[2], SHORTCUT_BOUNDS[2])
                and max(box[1], SHORTCUT_BOUNDS[1]) < min(box[3], SHORTCUT_BOUNDS[3])
                and _raw(row) != 'D'):
            return True
    return False


def _shortcut_valid(shortcut, original_rows, image, coin):
    if not (not _shortcut_conflict(original_rows)
            and _inside(shortcut.get('box'), SHORTCUT_BOUNDS) and _raw(shortcut) == 'D'
            and _confident(shortcut.get('confidence'))):
        return False
    if shortcut in original_rows:
        return True
    original = shortcut.get('raw_result')
    return (coin.get('appearance') == 'observed_dark_disabled'
            and not any(_inside(row.get('box'), SHORTCUT_BOUNDS) for row in original_rows)
            and shortcut.get('source') == 'current_shortcut_crop_ocr'
            and shortcut.get('crop_bounds') == SHORTCUT_BOUNDS
            and shortcut.get('input_transform') == 'original_rgb_crop_no_resize_or_padding'
            and shortcut.get('rgb_sha256') == _rgb_hash(image, SHORTCUT_BOUNDS)
            and original == [{key: shortcut.get(key) for key in ('raw_text', 'confidence', 'box')}])


def unread_offer(snapshot_id, page):
    return {'schema': SCHEMA, 'version': VERSION, 'snapshot_id': snapshot_id, 'page': page,
            'status': 'not_read', 'mode': None, 'free_remaining': None, 'paid_cost': None,
            'reasons': ['outside_requested_read_scope']}


def read_offer(rows, image, page, engine, snapshot_id):
    """Use current primary rows; at most one original-resolution numeric crop."""
    result = {'schema': SCHEMA, 'version': VERSION, 'snapshot_id': snapshot_id, 'page': page,
              'widget_bounds': list(WIDGET_BOUNDS), 'widget_rgb_sha256': _rgb_hash(image, WIDGET_BOUNDS),
              'status': 'unknown', 'mode': None, 'free_remaining': None, 'paid_cost': None,
              'raw_rows': copy.deepcopy([row for row in rows if _inside(row.get('box'), WIDGET_BOUNDS)]),
              'evidence': {}, 'numeric_ocr_calls': 0, 'numeric_ocr_ms': 0., 'reasons': []}
    if image.size != (1920, 1080) or page != 'shop':
        result['reasons'].append('native_shop_widget_not_visible')
        return result
    titles = [row for row in rows if _inside(row.get('box'), TITLE_BOUNDS)]
    shortcuts = [row for row in rows if _inside(row.get('box'), SHORTCUT_BOUNDS)]
    if (len(titles) != 1 or _raw(titles[0]) not in ('免费刷新', '刷新')
            or not _confident(titles[0].get('confidence')) or _shortcut_conflict(rows) or len(shortcuts) > 1
            or shortcuts and (_raw(shortcuts[0]) != 'D' or not _confident(shortcuts[0].get('confidence')))):
        result['reasons'].append('missing_or_conflicting_native_label')
        return result
    mode = 'free' if _raw(titles[0]) == '免费刷新' else 'paid'
    result['evidence']['title'] = copy.deepcopy(titles[0])
    coin = _coin(image)
    result['evidence']['currency_icon'] = coin
    if coin['present'] is None or coin['present'] != (mode == 'paid'):
        result['reasons'].append('currency_icon_missing_or_conflicts_with_label')
        return result
    shortcut = shortcuts[0] if shortcuts else None
    if shortcut is None and coin.get('appearance') == 'observed_dark_disabled':
        try:
            shortcut = _shortcut_crop(image, engine)
        except Exception as exc:
            result['evidence']['shortcut_error'] = type(exc).__name__
    if shortcut is None:
        result['reasons'].append('missing_or_conflicting_native_label')
        return result
    result['evidence']['shortcut'] = copy.deepcopy(shortcut)
    extent = _number_extent(image, mode, coin)
    result['evidence']['number_extent'] = extent
    if not extent['inside_numeric_crop']:
        result['reasons'].append('numeric_ink_missing_or_outside_supported_crop')
        return result
    numeric_rows = [row for row in rows if _inside(row.get('box'), VALUE_BOUNDS)
                    and _confident(row.get('confidence')) and _number(_raw(row)) is not None]
    if len(numeric_rows) > 1:
        result['reasons'].append('conflicting_numeric_rows')
        return result
    bounds = extent.get('ocr_bounds', NUMBER_BOUNDS[mode])
    if numeric_rows and _inside(numeric_rows[0]['box'], bounds):
        row = numeric_rows[0]
        number = {'source': 'primary_ocr', 'raw_text': _raw(row), 'confidence': row['confidence'],
                  'bounds': list(row['box']), 'raw_result': [copy.deepcopy(row)]}
    else:
        number = {'source': 'current_numeric_crop_ocr', 'bounds': list(bounds), 'raw_result': [],
                  'input_transform': ('original_rgb_complete_red_glyph_crop_no_resize_or_padding'
                    if coin.get('appearance') == 'observed_dark_disabled'
                    else 'original_rgb_crop_no_resize_or_padding')}
        started = time.perf_counter()
        try:
            result['numeric_ocr_calls'] = 1
            found, unused = engine(np.array(image.crop(bounds)), use_det=False, use_cls=False)
            for item in found or []:
                text, confidence = item[-2:]
                number['raw_result'].append({'raw_text': str(text), 'confidence': float(confidence)})
            if len(number['raw_result']) == 1:
                number.update(raw_text=number['raw_result'][0]['raw_text'],
                              confidence=number['raw_result'][0]['confidence'])
        except Exception as exc:
            number['error'] = str(exc)
        finally:
            result['numeric_ocr_ms'] = round((time.perf_counter() - started) * 1000, 3)
    number['rgb_sha256'] = _rgb_hash(image, number['bounds'])
    result['evidence']['number'] = number
    value = _number(number.get('raw_text', '').strip())
    if (value is None or not _confident(number.get('confidence'))
            or extent.get('component_count', len(str(value))) != len(str(value))):
        result['reasons'].append('numeric_read_unknown')
        return result
    if numeric_rows and value != _number(_raw(numeric_rows[0])):
        result['reasons'].append('numeric_read_conflicts_with_primary')
        return result
    result.update(status='known', mode=mode, free_remaining=value if mode == 'free' else None,
                  paid_cost=value if mode == 'paid' else None)
    return result


def consume_offer(offer, image, snapshot_id, page):
    """Validate current-frame provenance; never fall back from a native unknown."""
    if (not isinstance(offer, dict) or offer.get('schema') != SCHEMA
            or type(offer.get('version')) is not int or offer['version'] != VERSION
            or offer.get('snapshot_id') != snapshot_id or offer.get('page') != page
            or image.size != (1920, 1080)):
        raise ValueError('刷新控件契约或当前原帧来源不匹配')
    if offer.get('status') == 'not_read':
        if any(offer.get(key) is not None for key in ('mode', 'free_remaining', 'paid_cost')):
            raise ValueError('未读刷新控件不能带入已知值')
        return None
    if (offer.get('widget_bounds') != WIDGET_BOUNDS
            or offer.get('widget_rgb_sha256') != _rgb_hash(image, WIDGET_BOUNDS)):
        raise ValueError('刷新控件像素或完整区域与当前原帧不匹配')
    if offer.get('status') == 'unknown':
        if any(offer.get(key) is not None for key in ('mode', 'free_remaining', 'paid_cost')):
            raise ValueError('未知刷新控件不能带入已知值')
        return None
    mode = offer.get('mode')
    evidence = offer.get('evidence') or {}
    title, shortcut = evidence.get('title') or {}, evidence.get('shortcut') or {}
    number, coin = evidence.get('number') or {}, evidence.get('currency_icon') or {}
    original_rows = offer.get('raw_rows') or []
    if (offer.get('status') != 'known' or page != 'shop' or mode not in NUMBER_BOUNDS
            or not _inside(title.get('box'), TITLE_BOUNDS) or not _confident(title.get('confidence'))
            or _raw(title) != ('免费刷新' if mode == 'free' else '刷新')
            or not _shortcut_valid(shortcut, original_rows, image, coin)
            or title not in original_rows
            or coin.get('method') != 'observed_native_coin_template'
            or coin.get('template_rgb_sha256') != COIN_RGB_SHA256
            or coin.get('present') is not (mode == 'paid') or coin != _coin(image)):
        raise ValueError('刷新模式、标题或币标证据冲突')
    extent = _number_extent(image, mode, coin)
    if not extent['inside_numeric_crop'] or evidence.get('number_extent') != extent:
        raise ValueError('刷新数字范围或当前完整控件像素证据冲突')
    bounds = number.get('bounds')
    primary = number.get('source') == 'primary_ocr' and _inside(bounds, NUMBER_BOUNDS[mode])
    normal_crop = (bounds == NUMBER_BOUNDS[mode]
                   and number.get('input_transform') == 'original_rgb_crop_no_resize_or_padding')
    disabled_crop = (coin.get('appearance') == 'observed_dark_disabled' and bounds == extent.get('ocr_bounds')
        and number.get('input_transform') == 'original_rgb_complete_red_glyph_crop_no_resize_or_padding')
    crop = number.get('source') == 'current_numeric_crop_ocr' and (normal_crop or disabled_crop)
    value = _number(str(number.get('raw_text', '')).strip())
    raw_result = number.get('raw_result')
    original_number = raw_result[0] if isinstance(raw_result, list) and len(raw_result) == 1 else {}
    same_raw = (original_number.get('confidence') == number.get('confidence')
                and str(original_number.get('raw_text', '')).strip() == str(number.get('raw_text', '')).strip())
    if primary:
        same_raw = same_raw and original_number in original_rows and original_number.get('box') == bounds
    key, other = ('free_remaining', 'paid_cost') if mode == 'free' else ('paid_cost', 'free_remaining')
    if (not (primary or crop) or not same_raw or not _confident(number.get('confidence')) or value is None
            or extent.get('component_count', len(str(value))) != len(str(value))
            or type(offer.get(key)) is not int or offer[key] != value or offer.get(other) is not None
            or number.get('rgb_sha256') != _rgb_hash(image, bounds)):
        raise ValueError('刷新数字与当前模式、原读数或像素来源冲突')
    return {'mode': mode, 'free_remaining': offer['free_remaining'], 'paid_cost': offer['paid_cost']}
