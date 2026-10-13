"""Offline candidate: native slot structure from an explicit saved frame.

No capture, controller, SDK, or input imports. This module is not a production
reader. It does not infer empty slots from the deployed HUD or assign names from
an operator's labels. Existing source-bound portrait/star evidence stays separate
from occupied/empty structure; absent identity remains unknown.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image

from currency_wars_state_reader import EXPECTED_SIZE, RESOURCE_DIR, StateReader, native_slots


SCHEMA = 'currency-wars-offline-slot-probe/v1'
PROFILE = 'retained-1920x1080-front4-back6-bench9-v1'
GLYPH_COVERAGE = .925
GLYPH_WEAKEST = .8


def glyph_evidence(gray, slot):
    """Verify all six sides and both stems of the native empty-card symbol.

    The profile follows the retained empty_front/back/bench resources. Local
    foreground-vs-flank contrast removes the animated background's absolute
    brightness. The bounded offsets preserve native slot registration; this is
    not a full-frame search for any vaguely similar contour.
    """
    x1, y1, x2, _ = slot['bounds']
    cx = (x1 + x2) / 2
    # The inherited rectangles are broad ROIs, not exact rendered glyph centres.
    # Their 144/145px steps drift against the retained ~142px board glyph grid.
    if slot['row'] == 'front':
        cx -= 2 * (slot['slot'] - 1)
    elif slot['row'] == 'back':
        cx += 4 - 2 * (slot['slot'] - 1)
    cy = y1 + (69 if slot['row'] == 'bench' else 71 if slot['row'] == 'front' else 72)
    shear = 0 if slot['row'] == 'bench' else (cx - 960) / 3600
    variants = []
    for width, height in itertools.product((12, 13, 14), (35, 36, 37)):
        vertices = [(0, -height), (width, -height + 12), (width, height - 12),
                    (0, height), (-width, height - 12), (-width, -height + 12), (0, -height)]
        lines = list(zip(vertices[:-1], vertices[1:])) + [
            ((0, -height - 12), (0, -height - 3)), ((0, height + 3), (0, height + 12))]
        points, normals = [], []
        for a, b in lines:
            a, b = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
            delta = b - a
            normal = np.array([-delta[1], delta[0]]) / np.linalg.norm(delta)
            for fraction in np.linspace(.12, .88, 10):
                points.append(a + delta * fraction)
                normals.append(normal)
        points, normals = np.array(points, dtype=np.float32), np.array(normals, dtype=np.float32)
        for dx, dy in itertools.product(range(-3, 4), range(-3, 4)):
            shifted = points.copy()
            shifted[:, 0] += shifted[:, 1] * shear + cx + dx
            shifted[:, 1] += cy + dy
            variants.append((shifted, normals, (dx, dy, width, height)))
    positions = np.array([entry[0] for entry in variants], dtype=np.float32)
    normals = np.array([entry[1] for entry in variants], dtype=np.float32)
    samples = []
    for offset in (-4, -1, 0, 1, 4):
        points = positions + normals * offset
        samples.append(cv2.remap(gray, points[:, :, 0], points[:, :, 1], cv2.INTER_LINEAR))
    contrast = np.max(samples[1:4], axis=0) - (samples[0] + samples[4]) / 2
    support = (contrast >= 3).reshape(len(variants), 8, 10).mean(axis=2)
    coverage, weakest = support.mean(axis=1), support.min(axis=1)
    medians = np.median(contrast, axis=1)
    index = int(np.lexsort((medians, weakest, coverage))[-1])
    return {'coverage': round(float(coverage[index]), 4),
            'weakest_segment': round(float(weakest[index]), 4),
            'median_contrast': round(float(medians[index]), 4),
            'segment_coverage': support[index].tolist(),
            'profile': list(variants[index][2]), 'shear': round(shear, 5),
            'method': 'six_sided_native_glyph_and_two_stems_local_contrast'}


def badge_evidence(rgb, sources=()):
    """Verify two complete native blocks, including a hollow front/back block.

    The retained front/back/both resources establish the stacked filled/hollow
    grammar. Anti-aliased outlines are dimmer than filled centres; requiring two
    white filled blocks misses legal single-position cards. This structure does
    not establish a name or qualification (selection also changes badge colour).
    ``rgb`` is the bounded badge ROI, including the board perspective margin.
    """
    patch = rgb
    gray = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
    white = (gray >= 130).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(white)
    blocks = []
    for row in stats[1:]:
        x, y, w, h, area = map(int, row)
        if not (8 <= w <= 12 and 8 <= h <= 11):
            continue
        mask = white[y:y+h, x:x+w].astype(bool)
        core = gray[y+2:y+h-2, x+2:x+w-2]
        rim = np.ones((h, w), dtype=bool)
        rim[2:-2, 2:-2] = False
        # A complete filled block can lean by one or two pixels in the native
        # board perspective. Its axis-aligned bounding box then includes dark
        # corner wedges; box density alone rejects the intact glyph. Require
        # every scanline to be a wide, unbroken fill with bounded edge drift.
        rows = [np.flatnonzero(line) for line in mask]
        widths = [len(line) for line in rows]
        edges = [(int(line[0]), int(line[-1])) for line in rows if len(line)]
        complete_fill = (len(edges) == h and min(widths) >= max(8, w-2)
                         and max(widths)-min(widths) <= 2
                         and all(len(line) == line[-1]-line[0]+1 for line in rows)
                         and all(max(edge[axis] for edge in edges)
                                 - min(edge[axis] for edge in edges) <= 2 for axis in (0, 1)))
        geometry = {'bounding_box_fill': round(area / (w*h), 4),
                    'scanline_widths': widths, 'scanline_edges': [list(edge) for edge in edges],
                    'complete_scanline_fill': bool(complete_fill)}
        if complete_fill and float(np.mean(core >= 130)) >= .95:
            kind = 'filled'
        elif (float(mask[rim].mean()) >= .60
              and min(float(edge.mean()) for edge in
                      (mask[:2], mask[-2:], mask[:, :2], mask[:, -2:])) >= .35
              and float(np.mean(core <= 85)) >= .75):
            kind = 'outlined'
        else:
            continue
        blocks.append(([x, y, w, h, area], kind, geometry))
    result = {'positive': False, 'blocks': [], 'dark_surround': None,
              'pattern': None, 'block_geometry': [], 'sources': list(sources),
              'method': 'source_backed_complete_filled_or_outlined_native_blocks'}
    # Missing provenance must not create an independent positive recognizer.
    if {source.get('name') for source in sources} != {'前台', '后台', '前后台'}:
        return result
    for (a, ak, ag), (b, bk, bg) in itertools.permutations(blocks, 2):
        x, y, w, h, _ = a
        bx, by, bw, bh, _ = b
        if not (8 <= x <= 30 and 3 <= y <= 23 and 10 <= by-y <= 14
                and abs(x-bx) <= 2 and abs(w-bw) <= 2 and abs(h-bh) <= 1
                and (ak, bk) != ('outlined', 'outlined')):
            continue
        left, right = min(x, bx)-3, max(x+w, bx+bw)+3
        top, bottom = y-3, by+bh+3
        if left < 0 or top < 0 or right > gray.shape[1] or bottom > gray.shape[0]:
            continue
        surround = np.ones((bottom-top, right-left), dtype=bool)
        # Exclude the one-pixel antialias boundary as well as each white block.
        surround[y-top-1:y+h-top+1, x-left-1:x+w-left+1] = False
        surround[by-top-1:by+bh-top+1, bx-left-1:bx+bw-left+1] = False
        dark = float((gray[top:bottom, left:right][surround] <= 85).mean())
        if dark >= .85:
            result.update(positive=True, blocks=[a, b], pattern=[ak, bk], block_geometry=[ag, bg],
                          dark_surround=round(dark, 4))
            break
    return result


class SlotProbe:
    """Explicit saved-image API; no screen capture, HUD inference or writes.

    A complete occupancy result is not a checked team, a UID assignment, bench
    overflow evidence, or an executable action. Caller must separately bind the
    original frame to the live run/epoch if integrating this offline candidate.
    """

    def __init__(self, resources=RESOURCE_DIR):
        self.reader = StateReader(resources)

    def _slot(self, rgb, gray, definition, usable, overlays):
        legacy = self.reader._slot(rgb, definition, usable, overlays)
        result = {**definition, 'status': 'unknown', 'name': None, 'star': None,
                  'position': None, 'reasons': [], 'evidence': {
                      'legacy': legacy['evidence'], 'legacy_status': legacy['status']}}
        if legacy['reasons'] and legacy['reasons'][0] in (
                'native_roster_anchors_missing', 'slot_intersects_visible_popup',
                'slot_occluded_or_unobservable'):
            result['reasons'] = legacy['reasons']
            return result
        x1, y1, x2, y2 = definition['bounds']
        patch = rgb[y1:y2, x1:x2]
        glyph = glyph_evidence(gray, definition)
        # Board cards lean with perspective and can extend above the broad
        # slot rectangle. Keep the fixed slot/glyph geometry unchanged and
        # inspect only this bounded top-right margin for complete badge blocks.
        badge_top = y1 - (8 if definition['location'] == 'board' else 0)
        # Match the retained front glyph grid correction above. The broad
        # 145px slot steps drift against rendered badges; keep slot geometry fixed.
        badge_shift = 2 * (definition['slot'] - 1) if definition['row'] == 'front' else 0
        badge_bounds = [x2-42-badge_shift, badge_top, x2-badge_shift, y1+47]
        badge_sources = [{key: item[key] for key in ('file', 'name', 'source_sha256', 'bounds')}
                         for item, unused in self.reader.templates if item['kind'] == 'badge']
        badge = badge_evidence(rgb[badge_bounds[1]:badge_bounds[3], badge_bounds[0]:badge_bounds[2]], badge_sources)
        if any(max(badge_bounds[0], b[0]) < min(badge_bounds[2], b[2])
               and max(badge_bounds[1], b[1]) < min(badge_bounds[3], b[3]) for b in overlays):
            result['reasons'] = ['slot_intersects_visible_popup']
            return result
        badge['bounds'] = badge_bounds
        portrait = cv2.cvtColor(patch[30:108, 10:-10], cv2.COLOR_RGB2GRAY).astype(np.float32)
        portrait_std = float(np.std(portrait))
        texture = [float(np.mean(np.abs(np.diff(portrait, axis=axis)) >= 3)) for axis in (0, 1)]
        result['evidence'].update(empty_glyph=glyph, filled_badge=badge,
                                  portrait_std=round(portrait_std, 4),
                                  portrait_texture=[round(value, 4) for value in texture])
        empty = glyph['coverage'] >= GLYPH_COVERAGE and glyph['weakest_segment'] >= GLYPH_WEAKEST
        # Neither the new nor legacy badge can prove an occupied flat card.
        occupied = (portrait_std >= 18 and min(texture) >= .25
                    and (legacy['status'] == 'occupied' or badge['positive']))
        if empty and (occupied or badge['positive']):
            result['reasons'] = ['conflicting_empty_and_occupied_structure']
        elif empty:
            # Existing qualified badge/identity evidence also vetoes emptiness,
            # even if the legacy reader itself reported a conflicting slot.
            old = legacy['evidence']
            if old.get('badge_score', 0) >= .9 or (old.get('identity_score', 0) >= .9
                                                  and old.get('identity_margin', 0) >= .1):
                result['reasons'] = ['conflicting_empty_and_occupied_structure']
            else:
                result['status'] = 'empty'
        elif occupied:
            result['status'] = 'occupied'
            if legacy['status'] == 'occupied':
                for key in ('name', 'star', 'position'):
                    result[key] = legacy[key]
            if result['name'] is None:
                result['reasons'].append('identity_unknown_requires_bound_named_tooltip')
            if result['star'] is None:
                result['reasons'].append('star_unknown')
            if result['position'] is None:
                result['reasons'].append('qualification_unknown')
        else:
            result['reasons'] = ['no_complete_positive_slot_structure']
        return result

    def read(self, path, *, page=None):
        """Read a native-size PNG/JPEG, preserving its actual input SHA.

        JPEG is permitted for retained offline evidence, never represented as
        an original PNG capture. An incompatible page hint only narrows access.
        """
        started = time.perf_counter()
        data = Path(path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        with Image.open(io.BytesIO(data)) as image:
            fmt, size = image.format, image.size
            geometry = size == EXPECTED_SIZE and fmt in ('PNG', 'JPEG')
            rgb = np.array(image.convert('RGB')) if geometry else None
        resources = self.reader._load()
        anchors = {}
        if geometry and resources:
            for name, box in (('front', [900, 285, 1050, 325]), ('back', [900, 555, 1050, 598])):
                matches = self.reader._match(rgb[box[1]:box[3], box[0]:box[2]], 'anchor', name)
                anchors[name] = matches[0][0] if matches else 0.
        usable = geometry and resources and all(anchors.get(key, 0) >= .9 for key in ('front', 'back'))
        if page is not None and page not in ('preparation', 'shop', 'investment_summary'):
            usable = False
        overlays = self.reader._overlays(rgb) if geometry else []
        if geometry and resources:
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
            slots = [self._slot(rgb, gray, slot, usable, overlays) for slot in native_slots()]
        else:
            slots = [{**slot, 'status': 'unknown', 'name': None, 'star': None,
                      'position': None, 'evidence': {},
                      'reasons': ['unsupported_image_geometry_or_missing_resources']}
                     for slot in native_slots()]
        for slot in slots:
            slot['snapshot_id'] = digest
        counts = {status: sum(slot['status'] == status for slot in slots)
                  for status in ('empty', 'occupied', 'unknown')}
        bench = [slot for slot in slots if slot['row'] == 'bench']
        bench_complete = all(slot['status'] != 'unknown' for slot in bench)
        return {'schema': SCHEMA, 'profile': PROFILE, 'offline_candidate': True,
                'snapshot_id': digest, 'input': {'sha256': digest, 'size': list(size), 'format': fmt},
                'resource_version': self.reader._version, 'usable_anchors': bool(usable),
                'anchors': {key: round(value, 4) for key, value in anchors.items()},
                'overlays': overlays, 'slots': slots, 'counts': counts,
                'occupancy_complete': counts['unknown'] == 0,
                'bench': {'capacity': 9, 'occupancy_complete': bench_complete,
                          'occupied': sum(slot['status'] == 'occupied' for slot in bench) if bench_complete else None,
                          'free_slots': sum(slot['status'] == 'empty' for slot in bench) if bench_complete else None,
                          'overflow_checked': False},
                'team_checked': False, 'elapsed_ms': round((time.perf_counter()-started)*1000, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, help='Explicit saved 1920x1080 PNG/JPEG')
    parser.add_argument('--page', help='Optional narrowing hint; never substitutes for pixel evidence')
    args = parser.parse_args()
    print(json.dumps(SlotProbe().read(args.image, page=args.page), ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
