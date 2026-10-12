"""Source-bound native bench identities from explicitly supplied RGB frames.

Only the approved 4.6 / 1920x1080 profile is supported. The bank certifies
historical associations; each current portrait, full star strip and badge must
match independently. No OCR, capture, control, identity cache or database writes.
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


BANK_DIR = Path(__file__).resolve().parents[1] / 'data/currency_wars/native_unit_bank'
APPROVED_SHA256 = '3b15ee95393890a0fe41c07a3b9b01e51b2b648e08f19d9b710248c97084c70a'
PROFILE = 'native-1920x1080-client4.6-unit-profile-v1'
CLIENT_VERSION = '4.6.0'
ROOT_CHAT_ID = '01a1271b-d42a-73c3-a4f9-f754c0a43d06'
# Portrait and complete star-strip RGB checks retain the original tight bounds.
# Badge background pulse is handled independently by _compare_badge below.
MAX_CHANNEL_DELTA = 5
MAX_MEAN_DELTA = .1


def _sha(blob):
    return hashlib.sha256(blob).hexdigest()


def _crop(rgb, bounds):
    if (not isinstance(bounds, list) or len(bounds) != 4
            or any(type(value) is not int for value in bounds)):
        raise ValueError('invalid_crop_bounds')
    x1, y1, x2, y2 = bounds
    if not (0 <= x1 < x2 <= 1920 and 0 <= y1 < y2 <= 1080):
        raise ValueError('crop_out_of_native_frame')
    return rgb[y1:y2, x1:x2]


def _compare(patch, template):
    if patch.shape != template.shape or float(np.std(template)) < 5:
        return {'matches': False, 'score': 0.}
    delta = np.abs(patch.astype(np.int16) - template.astype(np.int16))
    gray = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
    target = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    score = float(cv2.matchTemplate(gray, target, cv2.TM_CCOEFF_NORMED)[0, 0])
    if not np.isfinite(score):
        score = 0.
    maximum, mean = int(delta.max()), float(delta.mean())
    return {'matches': score >= .90 and maximum <= MAX_CHANNEL_DELTA and mean <= MAX_MEAN_DELTA,
            'score': round(score, 6), 'max_channel_delta': maximum, 'mean_delta': round(mean, 6)}


def _compare_badge(patch, template):
    """Verify every pixel of the complete badge, including both filled/hollow blocks.

    Retained background pulse changes a few low-luminance blue edge pixels by
    6..7 levels, but never the complete >=180 luminance glyph map. Keep the
    full RGB mean bound and a tighter correlation as independent constraints;
    no ROI edge is masked, cropped away or excused by the portrait identity.
    """
    result = _compare(patch, template)
    if patch.shape != template.shape or patch.shape != (47, 42, 3):
        return {**result, 'matches': False}
    mask = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY) >= 180
    reference = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY) >= 180
    mismatch = int(np.count_nonzero(mask != reference))
    result.update(method='complete_badge_rgb_mean_and_exact_luminance_glyph',
                  glyph_mismatch_pixels=mismatch, glyph_pixels=int(reference.sum()))
    result['matches'] = bool(result.get('mean_delta', 256) <= MAX_MEAN_DELTA
        and result['score'] >= .995 and reference.sum() >= 64 and mismatch == 0)
    return result


class NativeUnitBank:
    def __init__(self, directory=BANK_DIR):
        self.directory = Path(directory)
        self._views = None
        self._manifest = None

    def _load(self):
        """Rehash source bytes; decode/crop the static PNG bank only once."""
        raw = (self.directory / 'manifest.json').read_bytes()
        if _sha(raw) != APPROVED_SHA256:
            raise ValueError('bank_manifest_digest_mismatch')
        data = json.loads(raw)
        if (data['schema'] != 'currency-wars-native-unit-bank/v1' or data['profile'] != PROFILE
                or data['client_version'] != CLIENT_VERSION
                or data['root_authorization']['chat_id'] != ROOT_CHAT_ID):
            raise ValueError('bank_profile_not_approved')
        root = self.directory.resolve()
        blobs = {}
        for source in data['resources']:
            relative = Path(source['file'])
            path = (root / relative).resolve()
            if relative.is_absolute() or root not in path.parents:
                raise ValueError('bank_source_outside_root')
            blob = path.read_bytes()
            if len(blob) > 8_000_000 or _sha(blob) != source['sha256']:
                raise ValueError('bank_source_digest_mismatch')
            blobs[source['file']] = blob
        if self._views is not None:
            return

        def referenced(ref):
            blob = blobs[ref['file']]
            if _sha(blob) != ref['sha256']:
                raise ValueError('bank_reference_digest_mismatch')
            return blob

        def image(ref):
            with Image.open(io.BytesIO(referenced(ref))) as im:
                if im.format != 'PNG' or im.size != (1920, 1080) or im.mode != 'RGB':
                    raise ValueError('bank_source_not_native_rgb_png')
                return np.array(im)

        def receipt(ref, binding, action, expected_id=None, snapshot=None):
            value = json.loads(referenced(ref))
            request, result = value['request'], value['result']
            actions = [action] if action['type'] == 'observe' else [action, {'type': 'wait', 'args': [.7]}]
            observed = result['observation']
            if (value['id'] != request['id'] or value['id'] != result['id']
                    or (expected_id is not None and value['id'] != expected_id)
                    or request['kind'] != 'actions' or request['handoff'] is not False
                    or request['chat_id'] != binding['chat_id']
                    or value['origin']['chat_id'] != binding['chat_id']
                    or value['origin']['run_id'] != binding['run_id']
                    or result['ok'] is not True or request['actions'] != actions
                    or result['completed'] != actions or observed['request_id'] != value['id']
                    or observed['frame_protocol'] != 1 or observed['pid'] != binding['game_pid']
                    or observed['snapshot_size'] != [1920, 1080]
                    or (snapshot is not None and observed['snapshot_sha256'] != snapshot)):
                raise ValueError('bank_receipt_association_invalid')
            if action['type'] == 'observe' and (result['input_attempted'] is not False
                                               or result['attempted_actions'] != []):
                raise ValueError('bank_observation_included_input')
            return datetime.fromisoformat(observed['captured_at'])

        from currency_wars_state_reader import native_slots
        definitions = {s['slot']: s for s in native_slots() if s['row'] == 'bench'}
        views = []
        for profile in data['profiles']:
            slot, binding = profile['source_slot'], profile['binding']
            if (slot != definitions.get(slot['slot']) or binding['chat_id'] != ROOT_CHAT_ID
                    or profile['title_read']['text'] != profile['name']
                    or profile['title_read']['confidence'] < .90
                    or profile['type_read']['text'] != profile['position']
                    or profile['type_read']['confidence'] < .90
                    or profile['position'] not in ('前台', '后台', '前后台')
                    or profile['observed_star'] != 1):
                raise ValueError('bank_profile_association_invalid')
            receipt(profile['opening_receipt'], binding, {'type': 'click', 'args': slot['point']},
                    binding['input_request_id'])
            receipt(profile['following_observe_receipt'], binding, {'type': 'observe', 'args': []},
                    binding['tooltip_capture_request_id'], profile['tooltip_frame']['sha256'])
            tooltip = image(profile['tooltip_frame'])
            properties = profile['property_evidence']
            evidence = properties['star']['evidence']
            if (properties['frame_sha256'] != profile['tooltip_frame']['sha256']
                    or properties['star']['value'] != profile['observed_star']
                    or _sha(_crop(tooltip, evidence['bounds']).tobytes()) != evidence['roi_rgb_sha256']):
                raise ValueError('bank_complete_tooltip_star_not_bound')
            entries = [{**profile, 'frame': profile['before_frame'], 'view': 'normal_bench'}]
            for alternate in profile.get('alternate_views', []):
                shifted = {**slot, 'bounds': [slot['bounds'][0], slot['bounds'][1]-12,
                            slot['bounds'][2], slot['bounds'][3]-12],
                           'point': [slot['point'][0], slot['point'][1]-12]}
                selected_views = {('花火', 6): {'selected_bench_minus12_after_close'},
                                  ('银狼LV.999', 4): {'selected_bench_minus12_after_close',
                                                     'selected_bench_minus12_with_tooltip'}}
                if (alternate['view'] not in selected_views.get((profile['name'], slot['slot']), set())
                        or alternate['approved_current_view_only'] is not True
                        or alternate['source_slot'] != shifted
                        or alternate['observed_star'] != profile['observed_star']
                        or alternate['position'] != profile['position']):
                    raise ValueError('bank_selected_pose_not_approved')
                pose_binding = alternate.get('binding', binding)
                if pose_binding != binding:
                    # A later session's selected view must carry its own exact
                    # before/open/passive-popup chain, not inherit old receipt IDs.
                    before_at = receipt(alternate['before_observe_receipt'], pose_binding,
                        {'type': 'observe', 'args': []}, pose_binding['before_capture_request_id'],
                        alternate['before_frame']['sha256'])
                    opened_at = receipt(alternate['opening_receipt'], pose_binding,
                        {'type': 'click', 'args': slot['point']}, pose_binding['input_request_id'])
                    popup_at = receipt(alternate['tooltip_observe_receipt'], pose_binding,
                        {'type': 'observe', 'args': []}, pose_binding['tooltip_capture_request_id'],
                        alternate['tooltip_frame']['sha256'])
                    popup = image(alternate['tooltip_frame'])
                    observed_name = json.loads(referenced(alternate['named_tooltip_evidence']))['name_read']
                    star = alternate['property_evidence']
                    proof = star['star']['evidence']
                    if (not before_at < opened_at < popup_at
                            or pose_binding['chat_id'] != ROOT_CHAT_ID
                            or observed_name['frame_sha256'] != alternate['tooltip_frame']['sha256']
                            or observed_name['name'] != profile['name']
                            or observed_name['position'] != profile['position']
                            or observed_name['title']['confidence'] < .90
                            or observed_name['type_anchor']['confidence'] < .90
                            or star['frame_sha256'] != alternate['tooltip_frame']['sha256']
                            or star['star']['value'] != profile['observed_star']
                            or _sha(_crop(popup, proof['bounds']).tobytes()) != proof['roi_rgb_sha256']
                            or not _compare(_crop(image(alternate['before_frame']), profile['portrait_bounds']),
                                            _crop(image(profile['before_frame']), profile['portrait_bounds']))['matches']):
                        raise ValueError('bank_later_pose_association_invalid')
                if alternate['view'] == 'selected_bench_minus12_after_close':
                    closed_at = receipt(alternate['close_receipt'], pose_binding, {'type': 'key', 'args': [27]})
                    seen_at = receipt(alternate['following_observe_receipt'], pose_binding,
                        {'type': 'observe', 'args': []}, snapshot=alternate['frame']['sha256'])
                    if not closed_at < seen_at or (pose_binding != binding and not popup_at < closed_at):
                        raise ValueError('bank_selected_close_order_invalid')
                else:
                    receipt(alternate['following_observe_receipt'], pose_binding,
                        {'type': 'observe', 'args': []}, pose_binding['tooltip_capture_request_id'],
                        alternate['frame']['sha256'])
                    if alternate['frame'] != alternate['tooltip_frame']:
                        raise ValueError('bank_selected_popup_frame_mismatch')
                entries.append(alternate)
            for entry in entries:
                frame = image(entry['frame'])
                x, y, right, bottom = entry['source_slot']['bounds']
                expected = {'portrait_bounds': [x+25, y+38, x+91, y+97],
                            'complete_star_strip_bounds': [x+27, bottom-49, right-27, bottom-5],
                            'position_badge_bounds': [right-42, y, right, y+47]}
                if any(entry[key] != bounds for key, bounds in expected.items()):
                    raise ValueError('bank_crop_not_complete_native_region')
                if entry['view'] != 'normal_bench' and not _compare(
                        _crop(frame, expected['portrait_bounds']),
                        _crop(image(profile['before_frame']), profile['portrait_bounds']))['matches']:
                    raise ValueError('bank_selected_portrait_differs_from_named_source')
                views.append({'profile': profile, 'view': entry['view'],
                              'frame_sha256': entry['frame']['sha256'],
                              'definition': entry['source_slot'], 'bounds': expected,
                              'templates': {key: _crop(frame, box).copy() for key, box in expected.items()}})
        self._views, self._manifest = views, data

    def augment(self, rgb, slots, reader, usable, overlays, *, client_version=None, bench_usable=None):
        """Return slots or None for legacy fallback; never establishes ownership."""
        bench_usable = usable if bench_usable is None else bench_usable
        metadata = {'profile': PROFILE, 'bank_sha256': APPROVED_SHA256,
                    'supported_client_version': CLIENT_VERSION, 'current_client_version': client_version,
                    'current_client_version_verified': False, 'status': 'unknown',
                    'scope': 'native_bench_portrait_complete_star_strip_and_position_badge',
                    'readable_geometry': {'board': bool(usable), 'bench': bool(bench_usable)}}
        if client_version is not None and client_version != CLIENT_VERSION:
            metadata['reason'] = 'unsupported_client_version'
            return None, metadata
        try:
            self._load()
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            self._views, self._manifest = None, None
            metadata['reason'] = str(exc) if isinstance(exc, ValueError) else 'bank_source_unavailable_or_invalid'
            return None, metadata
        metadata['status'] = 'source_verified'
        if not (usable or bench_usable) or rgb.shape != (1080, 1920, 3):
            metadata['reason'] = 'current_native_geometry_or_anchors_missing'
            return None, metadata
        # Import lazily: SlotProbe's legacy reader remains independent of this
        # augmentation. _slot never calls StateReader.read.
        from currency_wars_slot_probe import SlotProbe
        probe = SlotProbe.__new__(SlotProbe)
        probe.reader = reader
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        result = []
        for old in slots:
            definition = {key: old[key] for key in ('location', 'row', 'slot', 'bounds', 'point')}
            slot_usable = bench_usable if old['row'] == 'bench' else usable
            current = probe._slot(rgb, gray, definition, slot_usable, overlays)
            current['confidence'] = old.get('confidence')
            if old['row'] != 'bench':
                result.append(current)
                continue
            matches = []
            for view in self._views:
                # Selected poses have source approval only for their slots. Normal
                # equal-sized bench slots share a profile; board geometry does not.
                if view['view'] != 'normal_bench' and old['slot'] != view['definition']['slot']:
                    continue
                dx = definition['bounds'][0] - view['definition']['bounds'][0]
                boxes = {key: [box[0]+dx, box[1], box[2]+dx, box[3]]
                         for key, box in view['bounds'].items()}
                face = _compare(_crop(rgb, boxes['portrait_bounds']), view['templates']['portrait_bounds'])
                matches.append((face['score'], view, face, boxes))
            matches.sort(key=lambda item: item[0], reverse=True)
            if not matches:
                result.append(current)
                continue
            score, view, face, boxes = matches[0]
            profile = view['profile']
            second = max((item[0] for item in matches if item[1]['profile']['name'] != profile['name']), default=0.)
            if not face['matches'] or score-second < .10:
                result.append(current)
                continue
            if view['view'] != 'normal_bench':
                selected = probe._slot(rgb, gray, view['definition'], slot_usable, overlays)
                if current['status'] == 'empty' or selected['status'] != 'occupied':
                    result.append(current)
                    continue
                current = {**selected, **definition, 'confidence': old.get('confidence')}
            if current['status'] != 'occupied' or (current['name'] is not None and current['name'] != profile['name']):
                result.append(current)
                continue
            bank_evidence = {'bank_sha256': APPROVED_SHA256, 'source_frame_sha256': view['frame_sha256'],
                             'view': view['view'], 'portrait': {**face, 'bounds': boxes['portrait_bounds'],
                             'margin': round(score-second, 6)}, 'historical_tooltip': {
                                 'frame_sha256': profile['tooltip_frame']['sha256'],
                                 'displayed_coin': profile['observed_displayed_coin'],
                                 'semantic': profile['displayed_coin_semantic'],
                                 'equipment_empty_count': profile['observed_equipment_empty_count'],
                                 'current_equipment_verified': False}}
            current['name'] = profile['name']
            # Always replace legacy guesses for these two dynamic fields with
            # independent full-region evidence for this same named bank profile.
            for field, key, value in (('star', 'complete_star_strip_bounds', profile['observed_star']),
                                      ('position', 'position_badge_bounds', profile['position'])):
                # A selected card has observed popup/closed border appearances.
                # Match each whole field against only same-name, same-geometry,
                # receipt-bound alternatives; never copy a field from the face.
                alternatives = []
                for _, candidate, candidate_face, candidate_boxes in matches:
                    if (candidate['profile']['name'] != profile['name'] or not candidate_face['matches']
                            or candidate_boxes[key] != boxes[key]):
                        continue
                    compare = _compare_badge if field == 'position' else _compare
                    proof = compare(_crop(rgb, boxes[key]), candidate['templates'][key])
                    alternatives.append((proof, candidate))
                compared, source = max(alternatives, key=lambda item: (item[0]['matches'], item[0]['score']))
                bank_evidence[field] = {**compared, 'bounds': boxes[key],
                    'source_frame_sha256': source['frame_sha256'], 'reference_view': source['view']}
                current[field] = value if compared['matches'] else None
            current['evidence']['native_unit_bank'] = bank_evidence
            current['confidence'] = face['score']
            current['reasons'] = [f'{field}_unobservable' for field in ('star', 'position') if current[field] is None]
            result.append(current)
        metadata['matched_units'] = sum('native_unit_bank' in slot['evidence'] for slot in result)
        return result, metadata
