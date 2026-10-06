"""Read native roster and equipment slots from an explicit saved PNG only.

No capture, input or controller APIs. Private cropped templates retain their
original snapshot provenance in shop_reader_resources/state_reader/SOURCES.json.
Recognition follows OpenCV bounded normalized correlation, with positive native
slot evidence and an identity margin; an unrecognized portrait remains unnamed.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


SCHEMA = 'currency-wars-state-observation/v1'
RESOURCE_DIR = Path(__file__).with_name('shop_reader_resources') / 'state_reader'
EXPECTED_SIZE = (1920, 1080)


def _score(rgb, template, scales=(1.,)):
    """Bounded template match. Flat/absent evidence cannot be positive."""
    if not rgb.size or template is None:
        return 0., None
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    best, point = 0., None
    for scale in scales:
        w, h = round(template.shape[1] * scale), round(template.shape[0] * scale)
        if w < 12 or h < 12 or w > gray.shape[1] or h > gray.shape[0]:
            continue
        target = cv2.resize(template, (w, h))
        if np.std(target) < 1:
            continue
        scores = cv2.matchTemplate(gray, target, cv2.TM_CCOEFF_NORMED)
        _, value, _, loc = cv2.minMaxLoc(scores)
        if np.isfinite(value) and value > best:
            best, point = float(value), [loc[0], loc[1], w, h]
    return best, point


def native_slots():
    """Only the currently evidenced native 1920x1080 front4/back6/bench9."""
    result = []
    for row, count, x, y, step, width, height in (
            ('front', 4, 678, 327, 145, 132, 143),
            ('back', 6, 536, 597, 144, 132, 145),
            ('bench', 9, 381, 844, 125, 116, 137)):
        for slot in range(1, count + 1):
            left = x + (slot - 1) * step
            result.append({'location': 'bench' if row == 'bench' else 'board',
                           'row': row, 'slot': slot,
                           'bounds': [left, y, left + width, y + height],
                           'point': [left + width // 2, y + height // 2]})
    return result


def native_capacity(slots, snapshot_id):
    """Known bench occupancy is independent of board population and overflow."""
    bench = [slot for slot in slots if slot.get('location') == 'bench']
    complete = (bool(snapshot_id) and len(bench) == 9
        and {slot.get('slot') for slot in bench} == set(range(1, 10))
        and all(slot.get('snapshot_id') == snapshot_id and slot.get('row') == 'bench'
            and slot.get('status') in ('empty', 'occupied') for slot in bench))
    occupied = sum(slot['status'] == 'occupied' for slot in bench) if complete else None
    return {'snapshot_id': snapshot_id, 'bench_capacity': 9, 'bench_checked': complete,
            'occupied': occupied, 'free_slots': 9 - occupied if complete else None,
            'overflow_checked': False, 'overflow_count': None,
            'reason': 'temporary_overflow_layout_not_yet_observed',
            'origin': 'native_visual_state_reader'}


class StateReader:
    def __init__(self, resources=RESOURCE_DIR):
        self.resources = Path(resources)
        self._version = None
        self.templates = []

    def _load(self):
        try:
            manifest = (self.resources / 'SOURCES.json').read_bytes()
            data = json.loads(manifest)
            blobs = [(item, (self.resources / item['file']).read_bytes())
                     for item in data['resources']]
            version = hashlib.sha256(manifest + b''.join(blob for _, blob in blobs)).hexdigest()
            if version == self._version:
                return True
            templates = []
            for item, blob in blobs:
                template = np.array(Image.open(io.BytesIO(blob)).convert('L'))
                if (item.get('kind') not in {'anchor', 'empty', 'unit', 'badge', 'item', 'star', 'starbar'}
                        or not isinstance(item.get('source_sha256'), str)
                        or len(item['source_sha256']) != 64 or min(template.shape) < 12):
                    return False
                templates.append((item, template))
            self.templates, self._version = templates, version
            return True
        except (OSError, ValueError, KeyError, TypeError):
            self.templates, self._version = [], None
            return False

    def _match(self, rgb, kind, name=None, scales=(1.,)):
        matches = []
        for item, template in self.templates:
            if item['kind'] == kind and (name is None or item['name'] == name):
                score, box = _score(rgb, template, scales)
                matches.append((score, item, box))
        return sorted(matches, key=lambda row: row[0], reverse=True)

    @staticmethod
    def _covered(patch):
        hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
        # Native empty slots are bright purple; large dark popup panels are not
        # negative occupancy evidence. Partial and blackout views stay unknown.
        return (float(np.mean(hsv[:, :, 2] < 125)) > .63
                or float(np.mean(hsv[:, :, 2] > 247)) > .85)

    def _slot(self, rgb, definition, usable, overlays=()):
        result = {**definition, 'status': 'unknown', 'name': None, 'star': None,
                  'position': None, 'confidence': None, 'evidence': {}, 'reasons': []}
        if not usable:
            result['reasons'] = ['native_roster_anchors_missing']
            return result
        x1, y1, x2, y2 = result['bounds']
        if any(max(x1, b[0]) < min(x2, b[2]) and max(y1, b[1]) < min(y2, b[3])
               for b in overlays):
            result['reasons'] = ['slot_intersects_visible_popup']
            return result
        patch = rgb[y1:y2, x1:x2]
        if (self._covered(patch) and result['row'] != 'bench') or np.std(patch) < 5:
            result['reasons'] = ['slot_occluded_or_unobservable']
            return result
        empty = self._match(patch, 'empty', f"{result['row']}_{result['slot']}")[0:1]
        empty_score = empty[0][0] if empty else 0.
        # Search owned-position badge only in its actual top-right native area.
        badge = self._match(patch[:47, -42:], 'badge', scales=(.95, 1., 1.05))
        badge_score = badge[0][0] if badge else 0.
        names = self._match(patch[22:118, 5:-5], 'unit', scales=(.9, .95, 1., 1.05, 1.1))
        first = names[0] if names else (0., {}, None)
        margin = first[0] - (names[1][0] if len(names) > 1 else 0.)
        name_positive = first[0] >= .90 and margin >= .10
        result['evidence'] = {'method': 'bounded_native_slot_templates',
            'empty_score': round(empty_score, 4), 'badge_score': round(badge_score, 4),
            'identity_score': round(first[0], 4), 'identity_margin': round(margin, 4)}
        if empty_score >= .90 and badge_score < .65 and not name_positive:
            result.update(status='empty', confidence=round(empty_score, 4))
            return result
        # Badge alone is positive occupied evidence for unknown portraits. A
        # conflicting empty symbol refuses occupancy rather than guessing.
        if empty_score >= .90 or not (badge_score >= .90 or name_positive):
            result['reasons'] = ['ambiguous_or_partial_slot']
            return result
        result.update(status='occupied', confidence=round(max(first[0] if name_positive else 0., badge_score), 4))
        if name_positive:
            result['name'] = first[1]['name']
            result['evidence']['identity'] = {**first[1], 'score': round(first[0], 4)}
        if badge_score >= .90:
            result['position'] = badge[0][1]['name']
        # Gold hair/stat icons are not stars. Only the retained star glyph
        # may provide rarity, within the native lower portrait strip.
        star_matches = self._match(patch[-48:-7, 21:-21], 'star', 'one')
        if star_matches and star_matches[0][0] >= .90:
            # One glyph does not prove the total star count; return the
            # observed evidence, requesting a full star/name tooltip instead.
            result['evidence']['stars'] = {'method': 'native_star_glyph',
                'score': round(star_matches[0][0], 4), 'count': None}
        bars = self._match(patch[-49:-5, 27:-27], 'starbar')
        if bars:
            first_bar = bars[0]
            other = max((b[0] for b in bars if b[1]['name'] != first_bar[1]['name']), default=0.)
            # Whole count strip, independently compared against the observed
            # one-/two-star alternatives. Tight threshold intentionally leaves
            # unfamiliar/glowing/moved star displays for a named detail read.
            if first_bar[0] >= .97 and first_bar[0] - other >= .12:
                result['star'] = int(first_bar[1]['name'])
                result['evidence']['stars'] = {'method': 'observed_complete_star_strip',
                    'count': result['star'], 'score': round(first_bar[0], 4),
                    'margin': round(first_bar[0] - other, 4), 'template': first_bar[1]}
        if result['name'] is None:
            result['reasons'].append('unit_identity_unknown_read_named_tooltip')
        if result['star'] is None:
            result['reasons'].append('star_unobservable')
        return result

    def _inventory(self, rgb, usable):
        items, unknown = [], []
        if not usable:
            return {'checked': False, 'items': [], 'unknown_slots': [],
                    'reason': 'native_roster_anchors_missing'}
        # Two fixed tools above y230 are not inventory. Each current row is
        # located anew from its visible white square boundary after mutation.
        region = rgb[230:835, 1795:1889]
        gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
        edge = cv2.Canny(gray, 70, 180)
        contours, _ = cv2.findContours(edge, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            x, y, w, h = map(int, cv2.boundingRect(contour))
            if 64 <= w <= 80 and 64 <= h <= 81 and 5 <= x <= 24:
                if not any(abs(230+y - prior[1]) < 12 for prior in candidates):
                    candidates.append([1795+x, 230+y, w, h])
        for index, (x, y, w, h) in enumerate(sorted(candidates, key=lambda b: b[1]), 1):
            patch = rgb[y+5:y+h-5, x+5:x+w-5]
            matches = self._match(patch, 'item', scales=(.9, .95, 1., 1.05, 1.1))
            score, item, _ = matches[0] if matches else (0., {}, None)
            margin = score - (matches[1][0] if len(matches) > 1 else 0.)
            entry = {'name': item.get('name') if score >= .90 and margin >= .10 else None,
                'location': 'inventory', 'slot': index, 'bounds': [x, y, x+w, y+h],
                'point': [x+w//2, y+h//2], 'confidence': round(score, 4),
                'evidence': {'method': 'fresh_inventory_square_and_unique_template',
                             'identity_margin': round(margin, 4),
                             'template': item if score >= .90 and margin >= .10 else None}}
            items.append(entry)
            if entry['name'] is None:
                unknown.append(entry)
        # No square is not proof of an empty inventory (scrolling/popup/edge).
        return {'checked': False, 'items': items, 'unknown_slots': unknown,
                'reason': 'visible_rows_only_inventory_completeness_unproven'}

    @staticmethod
    def _overlays(rgb):
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        contours, _ = cv2.findContours(cv2.Canny(gray, 45, 140), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for contour in contours:
            x, y, w, h = map(int, cv2.boundingRect(contour))
            if 250 <= w <= 900 and 250 <= h <= 860 and y >= 130:
                if float(np.mean(gray[y:y+h, x:x+w] < 125)) > .55:
                    boxes.append([x, y, x+w, y+h])
        return boxes

    @staticmethod
    def _tooltips(rows, snapshot_id):
        """Typed full names from readable native popup; never bind to a slot."""
        found = []
        for row in rows or []:
            if not isinstance(row, dict) or row.get('confidence', 0) < .90:
                continue
            text, box = row.get('text'), row.get('box')
            if not isinstance(text, str) or not isinstance(box, list) or len(box) != 4:
                continue
            kind = ('item' if text in ('简易装备', '进阶装备', '消耗品') else
                    'unit' if text in ('前台', '后台', '前后台') else None)
            if kind is None:
                continue
            candidates = [r for r in rows if isinstance(r, dict) and r.get('confidence', 0) >= .90
                and isinstance(r.get('box'), list) and len(r['box']) == 4
                and 8 <= box[1] - r['box'][3] <= 45
                and abs(r['box'][0] - box[0]) <= 75 and isinstance(r.get('text'), str)]
            if len(candidates) == 1:
                title = candidates[0]
                name = title['text'].split('Lv.')[0].split('LV.')[0].strip()
                if 1 <= len(name) <= 32:
                    found.append({'kind': kind, 'name': name, 'snapshot_id': snapshot_id,
                        'origin': 'readable_native_tooltip', 'owned': None,
                        'location': None, 'bounds': title['box'], 'confidence': title['confidence'],
                        'position': text if kind == 'unit' else None,
                        'evidence': {'title': title, 'type_anchor': row}})
        return found

    def read(self, path, *, rows=None, page=None):
        data = Path(path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        with Image.open(io.BytesIO(data)) as image:
            fmt, size = image.format, image.size
            rgb = np.array(image.convert('RGB'))
        native = fmt == 'PNG' and size == EXPECTED_SIZE and self._load()
        anchors = {}
        if native:
            for name, box in (('front', [900, 285, 1050, 325]), ('back', [900, 555, 1050, 598])):
                anchors[name] = self._match(rgb[box[1]:box[3], box[0]:box[2]], 'anchor', name)[0][0]
        usable = native and all(anchors.get(key, 0.) >= .90 for key in ('front', 'back'))
        # A supplied classified page can only narrow the pixel evidence.
        if page is not None and page not in ('preparation', 'shop', 'investment_summary'):
            usable = False
        overlays = self._overlays(rgb) if native else []
        slots = [self._slot(rgb, slot, usable, overlays) for slot in native_slots()] if native else [
            {**slot, 'status': 'unknown', 'name': None, 'star': None, 'position': None,
             'confidence': None, 'evidence': {}, 'reasons': ['unsupported_or_missing_resources']}
            for slot in native_slots()]
        for slot in slots:
            slot.update(snapshot_id=digest, origin='native_visual_state_reader')
        units = [slot for slot in slots if slot['status'] == 'occupied']
        unknown = [slot for slot in slots if slot['status'] == 'unknown']
        board = [slot for slot in slots if slot['location'] == 'board']
        # Root reconciles the independent central deployed HUD as an additional
        # condition; this reader never promotes geometry alone to team.checked.
        fully_read = (usable and all(slot['status'] != 'unknown' for slot in board)
            and all(slot['name'] is not None and slot['star'] is not None
                    for slot in board if slot['status'] == 'occupied'))
        inventory = self._inventory(rgb, usable) if native else {'checked': False, 'items': [], 'unknown_slots': []}
        for item in inventory['items']:
            item.update(snapshot_id=digest, origin='native_visual_state_reader')
        requests = [{'kind': 'unit_name_or_star', 'row': slot['row'], 'slot': slot['slot'],
                     'point': slot['point'], 'snapshot_id': digest}
                    for slot in units if slot['name'] is None or slot['star'] is None]
        requests.extend({'kind': 'item_tooltip', 'point': item['point'], 'snapshot_id': digest}
                        for item in inventory['unknown_slots'])
        return {'schema': SCHEMA, 'snapshot_id': digest,
            'input': {'sha256': digest, 'size': list(size), 'format': fmt},
            'origin': 'native_visual_state_reader', 'resource_version': self._version,
            'anchors': {k: round(v, 4) for k, v in anchors.items()}, 'overlays': overlays,
            'team': {'checked': False, 'fully_read': bool(fully_read), 'slots': slots,
                     'units': units, 'unknown_slots': unknown, 'snapshot_id': digest,
                     'capacity': native_capacity(slots, digest)},
            'inventory': {**inventory, 'snapshot_id': digest},
            'tooltips': self._tooltips(rows, digest) if native else [], 'requests': requests}
