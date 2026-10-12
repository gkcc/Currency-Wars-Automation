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
    ink = gray < 100
    if mode == 'paid':
        x1, y1, x2, y2 = coin['bounds']
        ink[y1-top:y2-top, x1-left:x2-left] = False
    ys, xs = np.nonzero(ink)
    extent = [int(xs.min()) + left, int(ys.min()) + top,
              int(xs.max()) + left + 1, int(ys.max()) + top + 1] if xs.size else None
    return {'method': 'observed_dark_numeric_extent', 'bounds': list(NUMBER_GUARD_BOUNDS),
            'gray_below': 100, 'rgb_sha256': _rgb_hash(image, NUMBER_GUARD_BOUNDS),
            'excluded_coin_bounds': list(coin['bounds']) if mode == 'paid' else None,
            'foreground_pixels': int(xs.size), 'foreground_bounds': extent,
            'inside_numeric_crop': _inside(extent, NUMBER_BOUNDS[mode])}


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
            or not _confident(titles[0].get('confidence')) or len(shortcuts) != 1
            or _raw(shortcuts[0]) != 'D' or not _confident(shortcuts[0].get('confidence'))):
        result['reasons'].append('missing_or_conflicting_native_label')
        return result
    mode = 'free' if _raw(titles[0]) == '免费刷新' else 'paid'
    result['evidence'].update(title=copy.deepcopy(titles[0]), shortcut=copy.deepcopy(shortcuts[0]))
    coin = _coin(image)
    result['evidence']['currency_icon'] = coin
    if coin['present'] is None or coin['present'] != (mode == 'paid'):
        result['reasons'].append('currency_icon_missing_or_conflicts_with_label')
        return result
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
    bounds = NUMBER_BOUNDS[mode]
    if numeric_rows and _inside(numeric_rows[0]['box'], bounds):
        row = numeric_rows[0]
        number = {'source': 'primary_ocr', 'raw_text': _raw(row), 'confidence': row['confidence'],
                  'bounds': list(row['box']), 'raw_result': [copy.deepcopy(row)]}
    else:
        number = {'source': 'current_numeric_crop_ocr', 'bounds': list(bounds), 'raw_result': [],
                  'input_transform': 'original_rgb_crop_no_resize_or_padding'}
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
    if value is None or not _confident(number.get('confidence')):
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
            or not _inside(shortcut.get('box'), SHORTCUT_BOUNDS) or _raw(shortcut) != 'D'
            or not _confident(shortcut.get('confidence'))
            or title not in original_rows or shortcut not in original_rows
            or coin.get('method') != 'observed_native_coin_template'
            or coin.get('template_rgb_sha256') != COIN_RGB_SHA256
            or coin.get('present') is not (mode == 'paid') or coin != _coin(image)):
        raise ValueError('刷新模式、标题或币标证据冲突')
    extent = _number_extent(image, mode, coin)
    if not extent['inside_numeric_crop'] or evidence.get('number_extent') != extent:
        raise ValueError('刷新数字范围或当前完整控件像素证据冲突')
    bounds = number.get('bounds')
    primary = number.get('source') == 'primary_ocr' and _inside(bounds, NUMBER_BOUNDS[mode])
    crop = (number.get('source') == 'current_numeric_crop_ocr' and bounds == NUMBER_BOUNDS[mode]
            and number.get('input_transform') == 'original_rgb_crop_no_resize_or_padding')
    value = _number(str(number.get('raw_text', '')).strip())
    raw_result = number.get('raw_result')
    original_number = raw_result[0] if isinstance(raw_result, list) and len(raw_result) == 1 else {}
    same_raw = (original_number.get('confidence') == number.get('confidence')
                and str(original_number.get('raw_text', '')).strip() == str(number.get('raw_text', '')).strip())
    if primary:
        same_raw = same_raw and original_number in original_rows and original_number.get('box') == bounds
    key, other = ('free_remaining', 'paid_cost') if mode == 'free' else ('paid_cost', 'free_remaining')
    if (not (primary or crop) or not same_raw or not _confident(number.get('confidence')) or value is None
            or type(offer.get(key)) is not int or offer[key] != value or offer.get(other) is not None
            or number.get('rgb_sha256') != _rgb_hash(image, bounds)):
        raise ValueError('刷新数字与当前模式、原读数或像素来源冲突')
    return {'mode': mode, 'free_remaining': offer['free_remaining'], 'paid_cost': offer['paid_cost']}
