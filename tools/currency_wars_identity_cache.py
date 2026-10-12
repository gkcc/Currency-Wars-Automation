"""Source-bound offline portrait-cache candidate, never a live identity authority.

Archives original saved frames and evidence bytes in a bounded JSON bundle.
SHA/field consistency is not authentication of a receipt, a live client, or
physical-card continuity. Every result requires ROOT review; names resolve to
catalogue definitions only. No physical UID, star count, ownership or action is
created. Production readers/resources and the catalogue are read-only inputs.
"""
from __future__ import annotations

import argparse
import base64
import configparser
from contextlib import closing
from copy import deepcopy
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path
import re
import time

import cv2
import numpy as np
from PIL import Image

from currency_wars_database import DEFAULT_DB, normalize_name, open_db
from currency_wars_state_reader import EXPECTED_SIZE, RESOURCE_DIR, StateReader, _score, native_slots


SCHEMA = 'currency-wars-identity-cache/v1'
CAPTURE_SCHEMA = 'currency-wars-identity-capture/v1'
LEGACY_PROFILE = 'legacy-native-1920x1080-source-v1'
MAX_ENTRIES = 128
MAX_BLOB = 12 * 1024 * 1024
MAX_BUNDLE = 64 * 1024 * 1024
HEX = re.compile(r'[0-9a-f]{64}')
ARTIFACT_FIELDS = ('frame', 'tooltip_frame', 'tooltip_read', 'association', 'receipt',
                   'binding_source', 'client_config', 'version_evidence', 'effective_profile_evidence')
TOOLTIP_TITLE_REGION = (1492, 204, 1788, 250)
TOOLTIP_TYPE_REGION = (1528, 251, 1685, 291)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def same_fields(left, right):
    """Exact JSON fields/types: a native UUID epoch is never coerced to int."""
    return encoded(left) == encoded(right)


def valid_epoch(value):
    return ((type(value) is int and value >= 0)
            or (isinstance(value, str) and 1 <= len(value) <= 128
                and value.strip() == value and all(ord(c) >= 32 for c in value)))


def read_bytes(path, limit=MAX_BLOB):
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('input exceeds bounded size')
    return data


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_blob(data):
    value = json.loads(data.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('evidence must be a JSON object')
    return value


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', value):
        return None
    parts = tuple(map(int, value.split('.')))
    return parts + (0,) * (3-len(parts))


def name_key(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid complete title')
    return normalize_name(value)


def image_rgb(data, native=True):
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ('PNG', 'JPEG') or (native and image.size != EXPECTED_SIZE):
            raise ValueError('unsupported image format or geometry')
        if not native and not (12 <= image.width <= 256 and 12 <= image.height <= 256):
            raise ValueError('invalid portrait dimensions')
        return np.array(image.convert('RGB'))


def box(value, width=1920, height=1080):
    if (not isinstance(value, list) or len(value) != 4 or any(type(x) is not int for x in value)
            or not 0 <= value[0] < value[2] <= width or not 0 <= value[1] < value[3] <= height):
        raise ValueError('invalid evidence bounds')
    return value


def slot_definition(value):
    if not isinstance(value, dict) or type(value.get('slot')) is not int:
        raise ValueError('invalid native slot')
    for slot in native_slots():
        if slot['row'] == value.get('row') and slot['slot'] == value['slot']:
            if 'bounds' in value and value['bounds'] != slot['bounds']:
                raise ValueError('slot bounds conflict with native profile')
            return slot
    raise ValueError('unsupported native slot')


def _tooltip_proximal(title_bounds, type_bounds):
    # Actual current black-tower local OCR boxes are 3 px apart; the retained
    # Silver Wolf LV.999 panel is also 3 px apart with these exact local ROIs.
    return (3 <= type_bounds[1] - title_bounds[3] <= 45
            and abs(type_bounds[0] - title_bounds[0]) <= 75)


@lru_cache(maxsize=1)
def _tooltip_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)


def read_tooltip(image_path):
    """Read only an explicit saved 1920x1080 PNG/JPEG, never capture or input.

    The fixed local type ROI starts to the right of the position icon. Accept
    exact typed OCR instead of deleting an OCR '日' prefix. Full titles, local
    raw rows and original-frame bounds are retained, including formal LV.999.
    This is a pixel read, not a bound slot/receipt or catalogue assertion. The
    caller must attach independently verified binding/association evidence.
    One bounded local OCR engine is lazily reused by single-threaded callers.
    """
    started = time.perf_counter()
    path = Path(image_path)
    data = read_bytes(path)
    rgb = image_rgb(data)
    with Image.open(io.BytesIO(data)) as header:
        source_format = header.format
    raw = {}
    for field, region in (('title', TOOLTIP_TITLE_REGION), ('type_anchor', TOOLTIP_TYPE_REGION)):
        x1, y1, x2, y2 = region
        results, _ = _tooltip_engine()(rgb[y1:y2, x1:x2])
        rows = []
        for points, text, confidence in results or []:
            xs = [int(round(point[0])) + x1 for point in points]
            ys = [int(round(point[1])) + y1 for point in points]
            value = float(confidence)
            rows.append({'text': str(text), 'bounds': box([min(xs), min(ys), max(xs), max(ys)]),
                         'confidence': value if np.isfinite(value) else None})
        raw[field] = {'input_bounds': list(region), 'rows': rows}
    missing = ['binding', 'slot_association', 'star', 'gear']
    reasons, title, anchor = [], {}, {}
    type_rows = raw['type_anchor']['rows']
    if (len(type_rows) == 1 and type_rows[0]['text'] in ('前台', '后台', '前后台')
            and type_rows[0]['confidence'] is not None and type_rows[0]['confidence'] >= .9):
        anchor = {**type_rows[0], 'method': 'native_ocr'}
    title_rows = raw['title']['rows']
    if len(title_rows) == 1 and title_rows[0]['confidence'] is not None and title_rows[0]['confidence'] >= .9:
        try:
            name_key(title_rows[0]['text'])
            title = {**title_rows[0], 'method': 'native_ocr'}
        except ValueError:
            pass
    if not title and anchor and 2 <= len(title_rows) <= 3:
        bounds = [row['bounds'] for row in title_rows]
        overlap = min(b[3] for b in bounds) - max(b[1] for b in bounds)
        if overlap >= .75 * min(b[3]-b[1] for b in bounds):
            # Detection can split a single full name into overlapping pieces.
            # Re-read the original complete title ROI once, without detection;
            # never concatenate the recognized fragments or lower confidence.
            x1, y1, x2, y2 = TOOLTIP_TITLE_REGION
            started_fallback = time.perf_counter()
            results, _ = _tooltip_engine()(rgb[y1:y2, x1:x2], use_det=False, use_cls=False, use_rec=True)
            rows = []
            for text, confidence in results or []:
                value = float(confidence)
                rows.append({'text': str(text), 'confidence': value if np.isfinite(value) else None})
            detected_union = [min(b[0] for b in bounds), min(b[1] for b in bounds),
                              max(b[2] for b in bounds), max(b[3] for b in bounds)]
            raw['title_fallback'] = {'reason': 'detected_fragments_on_one_line',
                'input_bounds': list(TOOLTIP_TITLE_REGION), 'use_det': False, 'use_cls': False, 'use_rec': True,
                'rows': rows, 'detected_fragment_union': detected_union,
                'bounds_basis': 'union_of_original_detected_fragment_bounds',
                'elapsed_ms': round((time.perf_counter()-started_fallback)*1000, 3)}
            if len(rows) == 1 and rows[0]['confidence'] is not None and .9 <= rows[0]['confidence'] <= 1:
                try:
                    name_key(rows[0]['text'])
                    title = {**rows[0], 'bounds': detected_union, 'method': 'native_ocr',
                             'ocr_mode': 'recognition_only_full_title_roi',
                             'bounds_basis': 'union_of_original_detected_fragment_bounds'}
                except ValueError:
                    pass
    if not title:
        missing.append('title')
        reasons.append('missing_or_ambiguous_qualified_title')
    if not anchor:
        missing.append('type_anchor')
        reasons.append('missing_or_ambiguous_exact_unit_type')
    if title and anchor and not _tooltip_proximal(title['bounds'], anchor['bounds']):
        missing.append('title_type_proximity')
        reasons.append('tooltip_title_and_type_not_locally_associated')
    qualified = not reasons
    return {'schema': 'currency-wars-tooltip-name-read/v1', 'frame_sha256': digest(data),
            'source_path': str(path.resolve()), 'source_format': source_format, 'size': list(EXPECTED_SIZE),
            'status': 'readable_title_and_type' if qualified else 'unknown',
            'name': title['text'] if qualified else None, 'position': anchor['text'] if qualified else None,
            'title': title, 'type_anchor': anchor, 'raw_ocr': raw,
            'missing_fields': missing, 'reasons': reasons, 'needs_review': True,
            'star': None, 'gear': None, 'role_id': None, 'physical_uid': None, 'owned': None,
            'elapsed_ms': round((time.perf_counter()-started)*1000, 3)}


class Catalogue:
    """A bounded read-only index, pinned to the actual database bytes."""

    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.sha256 = file_digest(self.path)
        self.rows, self.names = {}, {}
        with closing(open_db(self.path)) as db:
            rows = db.execute("SELECT * FROM entities WHERE kind='characters' ORDER BY id LIMIT 513").fetchall()
            if len(rows) > 512:
                raise ValueError('catalogue character bound exceeded')
            for row in rows:
                data = json.loads(row['data_json'])
                source = data.get('source') or {}
                role_id = str(source.get('row_key', {}).get('ID', '')) if isinstance(source, dict) else ''
                if not (role_id.isdecimal() and role_id == str(data.get('id')) == row['source_key']):
                    role_id = None
                self.rows[row['id']] = {
                    'entity_id': row['id'], 'name': row['name'], 'role_id': role_id,
                    'import_id': row['import_id'], 'source_key': row['source_key'],
                    'version': row['version'], 'availability': row['availability'], 'cost': row['cost'],
                    'source_revision': source.get('revision') if isinstance(source, dict) else None,
                    'source_file_sha256': source.get('file_sha256') if isinstance(source, dict) else None,
                    'record_sha256': digest(row['data_json'].encode('utf-8')),
                    'conditional': bool(data.get('is_expert') or data.get('switch_conditions')
                        or (data.get('pool_status') or {}).get('module_bans')
                        or (data.get('pool_status') or {}).get('excluded_from_initial_role_selection')
                        or data.get('shop_source') == 'conditional_variant'),
                    'in_pool': (data.get('pool_status') or {}).get('is_in_pool')}
            for alias in db.execute("SELECT a.* FROM aliases a JOIN entities e ON e.id=a.entity_id WHERE e.kind='characters'"):
                self.names.setdefault(name_key(alias['normalized_name']), set()).add(alias['entity_id'])
            for identity, row in self.rows.items():
                self.names.setdefault(name_key(row['name']), set()).add(identity)
        if file_digest(self.path) != self.sha256:
            raise ValueError('catalogue changed while loading')

    def resolve(self, title, *, client_version=None, cost=None):
        """Full known names block suffix fallback, even when unavailable.

        A level suffix may be removed only after whitespace and only when the
        resulting name has one eligible versioned definition. Glued 'LV.999'
        is never casually chopped off. Cost means an independently read base
        role cost, not star count, purchase price, or a guessed rarity.
        """
        text = name_key(title)
        if cost is not None and (type(cost) is not int or not 0 <= cost <= 99):
            raise ValueError('invalid explicit base role cost')
        matched, mode = text, 'exact_full_name_or_alias'
        ids = self.names.get(text, set())
        suffix = False
        if not ids:
            found = re.fullmatch(r'(.+?)\s+(?:[Ll][Vv]\.|等级)\s*([0-9]{1,4})', text)
            if found:
                matched = name_key(found.group(1))
                ids = self.names.get(matched, set())
                suffix, mode = True, 'bounded_level_suffix_candidate'
        rows = [self.rows[i] for i in sorted(ids)]
        active = [r for r in rows if version(client_version) is not None
                  and version(r['version']) == version(client_version)
                  and r['availability'] == 'current' and r['role_id'] is not None
                  and (cost is None or r['cost'] == cost)]
        reasons = []
        if not rows:
            reasons.append('full_name_missing')
        if version(client_version) is None:
            reasons.append('client_version_missing_or_invalid')
        if len(active) != 1:
            reasons.append('missing_or_ambiguous_versioned_definition')
        if len(active) == 1 and (active[0]['conditional'] or active[0]['in_pool'] is not True):
            reasons.append('conditional_activation_not_verified')
        chosen = active[0] if not reasons else None
        if suffix and chosen is None:
            reasons.append('level_suffix_not_committed_without_unique_valid_definition')
            mode = 'uncommitted_level_suffix'
        return {'title': text, 'matched_name': matched if not suffix or chosen else None,
                'parse_mode': mode, 'status': 'unique_definition' if chosen else 'needs_review',
                'definition': chosen, 'candidates': rows, 'reasons': reasons,
                'runtime_eligible': None, 'catalogue_sha256': self.sha256}


class Cache:
    """Content-addressed original evidence plus portraits, saved exclusively."""

    def __init__(self, catalogue):
        self.catalogue = catalogue
        self.artifacts, self.entries = {}, []
        self._validated_fingerprint, self._validated_infos = None, None
        self._templates = {}
        self._anchor_reader = StateReader()

    def put(self, data, source_path):
        data = bytes(data)
        if len(data) > MAX_BLOB:
            raise ValueError('artifact too large')
        sha = digest(data)
        if sha not in self.artifacts:
            if sum(len(v['data']) for v in self.artifacts.values()) + len(data) > MAX_BUNDLE // 2:
                raise ValueError('decoded artifact bound exceeded')
            self.artifacts[sha] = {'data': data, 'source_path': str(source_path)}
        return sha

    def import_ref(self, ref):
        if not isinstance(ref, dict) or not HEX.fullmatch(str(ref.get('sha256', ''))):
            raise ValueError('explicit artifact SHA256 required')
        data = read_bytes(ref['path'])
        if digest(data) != ref['sha256']:
            raise ValueError('source SHA256 mismatch')
        return self.put(data, str(Path(ref['path']).resolve()))

    def blob(self, sha):
        if sha not in self.artifacts or digest(self.artifacts[sha]['data']) != sha:
            raise ValueError('missing or tampered archived artifact')
        return self.artifacts[sha]['data']

    def add_legacy(self, resources=RESOURCE_DIR, source_root=None):
        resources = Path(resources)
        root = Path(source_root or Path(__file__).resolve().parents[1]).resolve()
        manifest = read_bytes(resources/'SOURCES.json')
        manifest_sha = self.put(manifest, str((resources/'SOURCES.json').resolve()))
        for index, item in enumerate(json_blob(manifest).get('resources', [])):
            if item.get('kind') != 'unit':
                continue
            if len(self.entries) >= MAX_ENTRIES:
                raise ValueError('entry bound exceeded')
            source = (root/item['source']).resolve()
            source.relative_to(root)
            frame = self.import_ref({'path': str(source), 'sha256': item['source_sha256']})
            template = (resources/item['file']).resolve()
            template.relative_to(resources.resolve())
            template_sha = self.put(read_bytes(template), str(template))
            self.entries.append({'kind': 'legacy_template', 'manifest_sha256': manifest_sha,
                'manifest_index': index, 'frame_sha256': frame, 'template_sha256': template_sha})
        self.validate()

    def add_capture(self, evidence_path):
        if len(self.entries) >= MAX_ENTRIES:
            raise ValueError('entry bound exceeded')
        data = read_bytes(evidence_path)
        evidence = json_blob(data)
        if evidence.get('schema') != CAPTURE_SCHEMA:
            raise ValueError('unsupported capture evidence schema')
        refs = {}
        for field in ARTIFACT_FIELDS:
            if evidence.get(field) is not None:
                refs[field] = self.import_ref(evidence[field])
        entry = {'kind': 'tooltip_candidate', 'evidence_sha256': self.put(data, str(Path(evidence_path).resolve())),
                 'refs': refs, 'template_sha256': None}
        if 'frame' in refs and evidence.get('slot') is not None:
            slot = slot_definition(evidence['slot'])
            x1, y1, x2, y2 = slot['bounds']
            crop = box(evidence.get('crop_bounds', [(x1+x2)//2-33, y1+38, (x1+x2)//2+33, y1+97]))
            if not (x1+10 <= crop[0] < crop[2] <= x2-20 and y1+30 <= crop[1] < crop[3] <= y1+105):
                raise ValueError('portrait crop intersects badge/star/equipment margin')
            image = image_rgb(self.blob(refs['frame']))
            stream = io.BytesIO()
            Image.fromarray(image[crop[1]:crop[3], crop[0]:crop[2]]).save(stream, format='PNG')
            entry.update(crop_bounds=crop, template_sha256=self.put(stream.getvalue(), 'derived_verified_frame_crop'))
        self.entries.append(entry)
        self.validate()

    def _capture_info(self, entry):
        evidence = json_blob(self.blob(entry['evidence_sha256']))
        if evidence.get('schema') != CAPTURE_SCHEMA:
            raise ValueError('capture schema mismatch')
        refs = entry['refs']
        for key, sha in refs.items():
            if evidence.get(key, {}).get('sha256') != sha:
                raise ValueError('archive reference differs from capture evidence')
        expected_refs = {k for k in ARTIFACT_FIELDS if evidence.get(k) is not None}
        if set(refs) != expected_refs:
            raise ValueError('capture reference set changed')
        docs = {key: json_blob(self.blob(sha)) for key, sha in refs.items()
                if key not in ('frame', 'tooltip_frame', 'client_config')}
        for key in ('frame', 'tooltip_frame'):
            if key in refs:
                image_rgb(self.blob(refs[key]))
        reasons = ['root_authentication_and_physical_continuity_unverified']
        missing = set(ARTIFACT_FIELDS) - set(refs)
        reasons.extend('missing_'+key for key in sorted(missing))
        profile = evidence.get('profile', {})
        if not isinstance(profile, dict) or not isinstance(profile.get('id'), str) or not profile['id']:
            reasons.append('missing_profile_id')
            profile = {}
        if profile.get('id') == LEGACY_PROFILE:
            raise ValueError('legacy profile is reserved for original source manifests')
        read = docs.get('tooltip_read', {})
        title = read.get('title', {})
        name = title.get('text')
        if name is not None:
            name_key(name)
            box(title.get('bounds'))
            if read.get('schema') != 'currency-wars-tooltip-name-read/v1' or read.get('frame_sha256') != refs.get('tooltip_frame'):
                raise ValueError('tooltip title source mismatch')
            if title.get('method') not in ('native_ocr', 'reviewer_visual_transcription'):
                reasons.append('unrecognized_title_read_method')
            if title.get('method') == 'native_ocr':
                confidence = title.get('confidence')
                if type(confidence) not in (int, float) or not np.isfinite(confidence) or not .9 <= confidence <= 1:
                    reasons.append('unqualified_ocr_title')
        else:
            reasons.append('missing_bound_tooltip_title')
        type_anchor = read.get('type_anchor', {})
        if type_anchor.get('text') not in ('前台', '后台', '前后台'):
            reasons.append('missing_unit_tooltip_type_anchor')
        else:
            anchor_box = box(type_anchor.get('bounds'))
            title_box = title.get('bounds')
            if not title_box or not _tooltip_proximal(title_box, anchor_box):
                reasons.append('tooltip_title_and_type_not_locally_associated')
        cost = read.get('base_role_cost')
        if cost is not None:
            if not isinstance(cost, dict) or cost.get('semantic') != 'base_role_cost':
                raise ValueError('cost must be separately sourced base role cost')
            box(cost.get('bounds'))
            cost = cost.get('value')
        binding = docs.get('binding_source', {}).get('binding')
        if binding is not None:
            witness = docs['binding_source']
            if (witness.get('schema') != 'currency-wars-capture-binding/v1'
                    or witness.get('before_frame_sha256') != refs.get('frame')
                    or witness.get('tooltip_frame_sha256') != refs.get('tooltip_frame')):
                raise ValueError('binding witness frame association mismatch')
            if (not isinstance(binding, dict) or not all(isinstance(binding.get(k), str) and binding[k]
                    for k in ('run_id', 'chat_id', 'process_creation'))
                    or not valid_epoch(binding.get('epoch'))
                    or type(binding.get('game_pid')) is not int or binding['game_pid'] <= 0):
                raise ValueError('invalid explicit binding evidence')
            for key in ('tooltip_read', 'association', 'version_evidence', 'effective_profile_evidence'):
                if key in docs and not same_fields(docs[key].get('binding'), binding):
                    raise ValueError('evidence run/chat/epoch/process binding mismatch')
        else:
            reasons.append('missing_physical_source_binding')
        receipt = docs.get('receipt', {})
        if (receipt.get('isError') is True or receipt.get('ok') is False
                or receipt.get('status') in ('failed', 'error', 'rejected', 'cancelled')):
            raise ValueError('failed receipt cannot support portrait association')
        association = docs.get('association')
        if association is not None:
            if (association.get('schema') != 'currency-wars-slot-tooltip-association/v1'
                    or association.get('before_frame_sha256') != refs.get('frame')
                    or association.get('tooltip_frame_sha256') != refs.get('tooltip_frame')
                    or association.get('receipt_sha256') != refs.get('receipt')
                    or not same_fields(association.get('slot'), evidence.get('slot'))):
                raise ValueError('slot/title association evidence mismatch')
            if association.get('continuity') != 'same_card_no_roster_mutation':
                reasons.append('same_card_continuity_not_asserted')
        ver = docs.get('version_evidence', {})
        client_version = profile.get('client_version')
        config_versions = []
        if 'client_config' in refs:
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(self.blob(refs['client_config']).decode('utf-8-sig'))
            config_versions = [config.get(section, 'game_version') for section in config.sections()
                               if config.has_option(section, 'game_version')]
        if (ver.get('schema') != 'currency-wars-client-version-evidence/v1'
                or version(ver.get('client_version')) is None
                or version(ver.get('client_version')) != version(client_version)
                or len(config_versions) != 1 or version(config_versions[0]) != version(client_version)
                or ver.get('config_sha256') != refs.get('client_config')
                or ver.get('version_source_kind') != 'launcher_config.game_version'):
            reasons.append('client_version_evidence_missing_or_mismatched')
        effective = docs.get('effective_profile_evidence', {})
        if (effective.get('schema') != 'currency-wars-effective-profile-evidence/v1'
                or not isinstance(effective.get('effective_profile_id'), str)
                or effective.get('effective_profile_id') in ('', 'unknown')
                or effective.get('effective_profile_id') != profile.get('effective_profile_id')):
            reasons.append('effective_profile_unverified')
        resolution = self.catalogue.resolve(name, client_version=client_version, cost=cost) if name else None
        if resolution is not None:
            reasons.extend(resolution['reasons'])
        if evidence.get('slot') is None:
            reasons.append('missing_native_slot')
        if entry.get('template_sha256'):
            slot = slot_definition(evidence.get('slot'))
            crop = box(entry['crop_bounds'])
            expected_crop = evidence.get('crop_bounds', [(slot['bounds'][0]+slot['bounds'][2])//2-33,
                slot['bounds'][1]+38, (slot['bounds'][0]+slot['bounds'][2])//2+33, slot['bounds'][1]+97])
            if crop != expected_crop:
                raise ValueError('archived crop differs from requested crop')
            x1, y1, x2, _ = slot['bounds']
            if not (x1+10 <= crop[0] < crop[2] <= x2-20 and y1+30 <= crop[1] < crop[3] <= y1+105):
                raise ValueError('archived portrait includes dynamic badge/star/equipment margin')
            original = image_rgb(self.blob(refs['frame']))[crop[1]:crop[3], crop[0]:crop[2]]
            if not np.array_equal(original, image_rgb(self.blob(entry['template_sha256']), native=False)):
                raise ValueError('portrait pixels do not match original source crop')
        # A versioned name-family portrait is independent of whether one cost
        # variant or its live pool/effects can be selected. Keep those as review
        # reasons and never carry a captured fee/role ID into a later image.
        family_names = {name_key(item['name']) for item in resolution['candidates']
                        if item['role_id'] is not None and item['availability'] == 'current'
                        and version(item['version']) == version(client_version)} if resolution else set()
        valid_family = resolution is not None and resolution['matched_name'] is not None and len(family_names) == 1
        if len(family_names) > 1:
            reasons.append('ambiguous_name_family')
        nonblocking = {'root_authentication_and_physical_continuity_unverified',
                       'missing_effective_profile_evidence', 'effective_profile_unverified',
                       'missing_or_ambiguous_versioned_definition', 'conditional_activation_not_verified'}
        matchable = valid_family and bool(entry.get('template_sha256')) and not (set(reasons)-nonblocking)
        family_name = (next(iter(family_names)) if valid_family else
                       resolution['matched_name'] if resolution and len(family_names) < 2 else None)
        return {'name': family_name, 'observed_title': name,
                'profile': profile.get('id'), 'client_version': client_version,
                'status': 'needs_review', 'reasons': sorted(set(reasons)), 'resolution': resolution,
                'eligible_for_offline_matching': bool(matchable),
                'source_consistency_only': True, 'template_sha256': entry.get('template_sha256'),
                'evidence': {'capture_sha256': entry['evidence_sha256'],
                             'frame_sha256': refs.get('frame'),
                             'tooltip_frame_sha256': refs.get('tooltip_frame'),
                             'tooltip_read_sha256': refs.get('tooltip_read'),
                             'association_sha256': refs.get('association'),
                             'binding_sha256': refs.get('binding_source'),
                             'receipt_sha256': refs.get('receipt')}}

    def _fingerprint(self):
        """Hash actual archived bytes on every use; skip only repeated decoding.

        An unchanged in-memory snapshot may reuse its source/crop validation.
        Entry metadata and all bytes (including unused artifacts) are checked;
        stat/mtime and dictionary keys alone never establish unchanged content.
        Cache objects are for single-threaded callers. Changing the on-disk
        bundle requires load() again; this object owns a byte snapshot.
        """
        if (not isinstance(self.entries, list) or not isinstance(self.artifacts, dict)
                or len(self.entries) > MAX_ENTRIES or len(self.artifacts) > 512):
            raise ValueError('cache bound exceeded')
        metadata = encoded({'catalogue_sha256': self.catalogue.sha256, 'entries': self.entries})
        if len(metadata) > MAX_BLOB:
            raise ValueError('entry metadata bound exceeded')
        fingerprint = hashlib.sha256(metadata)
        total = 0
        for sha, item in sorted(self.artifacts.items()):
            if (not isinstance(sha, str) or not HEX.fullmatch(sha) or not isinstance(item, dict)
                    or type(item.get('data')) is not bytes or not isinstance(item.get('source_path'), str)):
                raise ValueError('invalid archived artifact')
            data = item['data']
            total += len(data)
            if len(data) > MAX_BLOB or total > MAX_BUNDLE // 2:
                raise ValueError('decoded artifact bound exceeded')
            if digest(data) != sha:
                raise ValueError('missing or tampered archived artifact')
            fingerprint.update(encoded([sha, item['source_path']]))
        return fingerprint.digest()

    def validate(self):
        fingerprint = self._fingerprint()
        if fingerprint == self._validated_fingerprint:
            return deepcopy(self._validated_infos)
        infos = []
        for entry in self.entries:
            if entry['kind'] == 'tooltip_candidate':
                infos.append(self._capture_info(entry))
                continue
            if entry['kind'] != 'legacy_template':
                raise ValueError('unknown cache entry kind')
            manifest = json_blob(self.blob(entry['manifest_sha256']))
            index = entry['manifest_index']
            if type(index) is not int or index < 0:
                raise ValueError('invalid source manifest index')
            item = manifest['resources'][index]
            if item.get('kind') != 'unit' or item.get('source_sha256') != entry['frame_sha256']:
                raise ValueError('legacy source manifest mismatch')
            bounds = box(item['bounds'])
            name = name_key(item['name'])
            image = image_rgb(self.blob(entry['frame_sha256']))
            crop = image[bounds[1]:bounds[3], bounds[0]:bounds[2]]
            template = image_rgb(self.blob(entry['template_sha256']), native=False)
            if not np.array_equal(crop, template):
                raise ValueError('legacy template differs from retained original frame')
            infos.append({'name': name, 'profile': LEGACY_PROFILE, 'client_version': None,
                'status': 'needs_review', 'reasons': ['legacy_profile_only', 'client_version_and_live_binding_unverified'],
                'resolution': self.catalogue.resolve(name), 'source_consistency_only': True,
                'eligible_for_offline_matching': True,
                'template_sha256': entry['template_sha256'],
                'evidence': {'manifest_sha256': entry['manifest_sha256'], 'manifest_index': index,
                             'frame_sha256': entry['frame_sha256']}})
        self._validated_fingerprint, self._validated_infos = fingerprint, infos
        self._templates = {}
        return deepcopy(infos)

    def save(self, path):
        self.validate()
        data = encoded({'schema': SCHEMA, 'catalogue_sha256': self.catalogue.sha256,
            'artifacts': {sha: {'source_path': item['source_path'], 'base64': base64.b64encode(item['data']).decode('ascii')}
                          for sha, item in sorted(self.artifacts.items())}, 'entries': self.entries})
        if len(data) > MAX_BUNDLE:
            raise ValueError('encoded cache bound exceeded')
        with Path(path).open('xb') as stream:
            stream.write(data)
        return {'path': str(Path(path).resolve()), 'sha256': digest(data), 'entries': len(self.entries),
                'bytes': len(data), 'production_approved': False}

    @classmethod
    def load(cls, path, catalogue, expected_sha256=None):
        data = read_bytes(path, MAX_BUNDLE)
        if expected_sha256 is not None and digest(data) != expected_sha256:
            raise ValueError('pinned cache SHA256 mismatch')
        document = json_blob(data)
        if document.get('schema') != SCHEMA or document.get('catalogue_sha256') != catalogue.sha256:
            raise ValueError('cache schema/catalogue changed; explicit re-review required')
        cache = cls(catalogue)
        if len(document.get('artifacts', {})) > 512:
            raise ValueError('artifact count bound exceeded')
        for sha, item in document['artifacts'].items():
            if not HEX.fullmatch(sha):
                raise ValueError('invalid artifact SHA256')
            raw = base64.b64decode(item['base64'], validate=True)
            if digest(raw) != sha:
                raise ValueError('tampered archived source SHA256')
            cache.put(raw, item['source_path'])
        cache.entries = document['entries']
        cache.validate()
        return cache

    def query(self, image_path, *, profile, client_version=None, row=None, slot=None):
        """Bounded portrait similarity, never an owned/physical character read."""
        started = time.perf_counter()
        data = read_bytes(image_path)
        image = image_rgb(data)
        infos = self.validate()
        templates = []
        for info in infos:
            sha = info['template_sha256']
            if (not sha or info['profile'] != profile or info['name'] is None
                    or not info['eligible_for_offline_matching'] or (profile != LEGACY_PROFILE
                    and (version(client_version) is None or version(client_version) != version(info['client_version'])))):
                continue
            if sha not in self._templates:
                template = cv2.cvtColor(image_rgb(self.blob(sha), native=False), cv2.COLOR_RGB2GRAY)
                template.setflags(write=False)
                self._templates[sha] = template
            templates.append((info, self._templates[sha]))
        reader = self._anchor_reader
        anchors = {}
        if reader._load():
            for name, bounds in (('front', [900, 285, 1050, 325]), ('back', [900, 555, 1050, 598])):
                values = reader._match(image[bounds[1]:bounds[3], bounds[0]:bounds[2]], 'anchor', name)
                anchors[name] = values[0][0] if values else 0.
        usable = all(anchors.get(k, 0) >= .9 for k in ('front', 'back'))
        definitions = [slot_definition({'row': row, 'slot': slot})] if row is not None or slot is not None else native_slots()
        results = []
        for definition in definitions:
            x1, y1, x2, y2 = definition['bounds']
            patch = image[y1+22:y1+118, x1+5:x2-5]
            scores = {}
            if usable and np.std(patch) >= 5:
                for info, template in templates:
                    score, _ = _score(patch, template, scales=(.9, .95, 1., 1.05, 1.1))
                    key = name_key(info['name'])
                    if key not in scores or score > scores[key][0]:
                        scores[key] = (score, info)
            ranked = sorted(scores.values(), key=lambda item: (-item[0], item[1]['name']))
            best = ranked[0] if ranked else (0., None)
            margin = best[0] - (ranked[1][0] if len(ranked) > 1 else 0.)
            positive = best[0] >= .9 and margin >= .1
            info = best[1] if positive else None
            resolved = self.catalogue.resolve(info['name'], client_version=client_version) if info else None
            results.append({'row': definition['row'], 'slot': definition['slot'], 'bounds': definition['bounds'],
                'status': 'candidate_match' if positive else 'unknown',
                'matched_name': info['name'] if info else None, 'score': round(best[0], 4), 'margin': round(margin, 4),
                'confidence_kind': 'uncalibrated_portrait_similarity',
                'match_evidence': {**info['evidence'], 'template_sha256': info['template_sha256']} if info else None,
                'definition_candidates': resolved['candidates'] if resolved else [],
                'role_id': None, 'star': None, 'physical_uid': None, 'owned': None,
                'needs_review': True, 'reasons': info['reasons'] if info else ['missing_or_ambiguous_portrait_match']})
        return {'schema': 'currency-wars-identity-candidates/v1', 'snapshot_sha256': digest(data),
                'profile': profile, 'catalogue_sha256': self.catalogue.sha256,
                'anchors': {k: round(v, 4) for k, v in anchors.items()}, 'matches': results,
                'production_approved': False, 'elapsed_ms': round((time.perf_counter()-started)*1000, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest='command', required=True)
    resolve = commands.add_parser('resolve')
    resolve.add_argument('title')
    resolve.add_argument('--client-version')
    resolve.add_argument('--cost', type=int)
    tooltip = commands.add_parser('read-tooltip')
    tooltip.add_argument('--image', type=Path, required=True)
    baseline = commands.add_parser('baseline')
    baseline.add_argument('--output', type=Path, required=True)
    baseline.add_argument('--resources', type=Path, default=RESOURCE_DIR)
    baseline.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    capture = commands.add_parser('capture')
    capture.add_argument('--evidence', type=Path, required=True)
    capture.add_argument('--base-cache', type=Path)
    capture.add_argument('--output', type=Path, required=True)
    query = commands.add_parser('query')
    query.add_argument('--cache', type=Path, required=True)
    query.add_argument('--cache-sha256')
    query.add_argument('--image', type=Path, required=True)
    query.add_argument('--profile', required=True)
    query.add_argument('--client-version')
    query.add_argument('--row', choices=('front', 'back', 'bench'))
    query.add_argument('--slot', type=int)
    args = parser.parse_args()
    if args.command == 'read-tooltip':
        print(json.dumps(read_tooltip(args.image), ensure_ascii=False, allow_nan=False))
        return
    catalogue = Catalogue(args.db)
    if args.command == 'resolve':
        result = catalogue.resolve(args.title, client_version=args.client_version, cost=args.cost)
    elif args.command == 'baseline':
        cache = Cache(catalogue)
        cache.add_legacy(args.resources, args.source_root)
        result = cache.save(args.output)
    elif args.command == 'capture':
        cache = Cache.load(args.base_cache, catalogue) if args.base_cache else Cache(catalogue)
        cache.add_capture(args.evidence)
        result = {**cache.save(args.output), 'last_entry': cache.validate()[-1]}
    else:
        cache = Cache.load(args.cache, catalogue, args.cache_sha256)
        result = cache.query(args.image, profile=args.profile, client_version=args.client_version, row=args.row, slot=args.slot)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
