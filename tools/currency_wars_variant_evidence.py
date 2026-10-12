"""Standalone saved-frame variant evidence; no capture, input or production import.

Compare complete, explicitly named catalogue fields with actual bounded local
OCR. A unique static definition candidate does not assert current UID, ownership,
enabled pool, shop price or observed star. No fuzzy/nearest match or digit guess.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import re
import time
import unicodedata

import numpy as np
from PIL import Image

from currency_wars_database import normalize_name
from currency_wars_decision import Catalogue, DEFAULT_DATABASE
from currency_wars_identity_cache import (
    _tooltip_engine, box, digest, encoded, file_digest, image_rgb, read_bytes,
    read_tooltip, version,
)


SCHEMA = 'currency-wars-variant-evidence/v1'
SUMMARY_ROI = (1408, 651, 1857, 752)
MIN_FIELD_CONFIDENCE = .95
MAX_VARIANTS = 16


def normalize_field(text):
    """Ignore line wrapping/width and one final full stop, never field content."""
    if not isinstance(text, str) or not 1 <= len(text) <= 2048:
        raise ValueError('invalid complete field text')
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))
    if text.endswith(('。', '.')):
        text = text[:-1]
    if not text:
        raise ValueError('empty field text')
    return text


def catalogue_variants(query, *, name, client_version, star):
    """Read released complete fields; cost is an annotation, never a selector."""
    if version(client_version) is None or type(star) is not int or not 1 <= star <= 4:
        raise ValueError('explicit valid client version and comparison star required')
    records = query.get('records', [])
    if not 1 <= len(records) <= MAX_VARIANTS:
        raise ValueError('missing or excessive catalogue variants')
    candidates = []
    for record in records:
        data = record['data']
        source = data.get('source') or {}
        role_id = str(data.get('id', ''))
        if (record.get('availability') != 'current' or version(record.get('version')) != version(client_version)
                or normalize_name(record.get('name', '')) != normalize_name(name)
                or not role_id.isdecimal() or role_id != str(record.get('source_key'))
                or role_id != str(source.get('row_key', {}).get('ID'))):
            raise ValueError('unqualified catalogue variant; explicit review required')
        stars = [item for item in data.get('star_effects', []) if item.get('star') == star]
        if len(stars) != 1:
            raise ValueError('missing or ambiguous comparison star definition')
        effects = stars[0]
        field_source = effects.get('source') or {}
        if (str(field_source.get('row_key', {}).get('ID')) != role_id
                or field_source.get('row_key', {}).get('Star') != star):
            raise ValueError('star field provenance mismatch')
        fields = {}
        for field in ('front_summary', 'back_summary'):
            if effects.get(field):
                fields[field] = {'text': effects[field], 'source': field_source}
        for skill in effects.get('front_skills', []):
            if not skill.get('name') or not skill.get('effects'):
                continue
            key = 'front_skill.' + skill['name'] + '.effects'
            skill_source = skill.get('source') or {}
            if (key in fields or str(skill_source.get('row_key', {}).get('SkillID')) != str(skill.get('id'))):
                raise ValueError('ambiguous skill field or mismatched provenance')
            fields[key] = {'text': skill['effects'], 'source': skill_source}
        for value in fields.values():
            normalize_field(value['text'])
            if not re.fullmatch(r'[0-9a-f]{64}', str(value['source'].get('file_sha256', ''))):
                raise ValueError('missing field source SHA256')
        candidates.append({'role_id': role_id, 'name': record['name'], 'version': record['version'],
            'catalogue_cost_annotation': record.get('cost'), 'comparison_star': star,
            'record_sha256': digest(encoded(record)), 'fields': fields})
    if len({item['role_id'] for item in candidates}) != len(candidates):
        raise ValueError('duplicate catalogue role definition')
    return candidates


def read_summary(rgb, frame_sha256):
    x1, y1, x2, y2 = SUMMARY_ROI
    rows, _ = _tooltip_engine()(rgb[y1:y2, x1:x2])
    raw = []
    for points, text, confidence in rows or []:
        xs = [int(round(p[0])) + x1 for p in points]
        ys = [int(round(p[1])) + y1 for p in points]
        value = float(confidence)
        raw.append({'text': str(text), 'bounds': box([min(xs), min(ys), max(xs), max(ys)]),
                    'confidence': value if np.isfinite(value) else None})
    ordered = sorted(raw, key=lambda row: (row['bounds'][1], row['bounds'][0]))
    reasons = []
    if not 1 <= len(ordered) <= 4:
        reasons.append('missing_or_excessive_field_rows')
    if any(row['confidence'] is None or not MIN_FIELD_CONFIDENCE <= row['confidence'] <= 1 for row in ordered):
        reasons.append('low_confidence_field_row')
    if any(a['bounds'][3] > b['bounds'][1] + 2 for a, b in zip(ordered, ordered[1:])):
        reasons.append('ambiguous_field_line_order')
    text = ''.join(row['text'] for row in ordered)
    if not text:
        reasons.append('missing_complete_field_text')
    return {'field': 'front_summary', 'status': 'readable' if not reasons else 'unknown',
            'text': text, 'raw_rows': raw, 'ordered_rows': ordered, 'bounds': list(SUMMARY_ROI),
            'frame_sha256': frame_sha256, 'method': 'native_local_ocr_complete_paragraph',
            'minimum_confidence': MIN_FIELD_CONFIDENCE, 'reasons': reasons}


def compare_fields(candidates, observations):
    """Intersect exact matches for every supplied field; no best-field voting.

    Pure comparator. Caller-supplied observations are not authenticated here;
    read_variant_evidence is the actual saved-byte/OCR producing entrypoint.
    """
    if not candidates or len(candidates) > MAX_VARIANTS or not observations or len(observations) > 8:
        return {'status': 'unknown', 'role_id_candidate': None, 'reasons': ['missing_or_excessive_evidence'], 'fields': []}
    remaining = {item['role_id'] for item in candidates}
    comparisons, reasons = [], []
    for key, observation in observations.items():
        rows = observation.get('raw_rows', [])
        if (observation.get('status') != 'readable' or not rows
                or any(type(row.get('confidence')) not in (int, float) or not np.isfinite(row['confidence'])
                       or not MIN_FIELD_CONFIDENCE <= row['confidence'] <= 1 for row in rows)):
            reasons.append('unqualified_observed_field:' + key)
            continue
        if any(key not in item['fields'] for item in candidates):
            reasons.append('incomplete_catalogue_field:' + key)
            continue
        observed = normalize_field(observation['text'])
        matching = [item['role_id'] for item in candidates
                    if normalize_field(item['fields'][key]['text']) == observed]
        comparisons.append({'field': key, 'observed_normalized_text': observed,
                            'exact_matching_role_ids': matching})
        remaining.intersection_update(matching)
    if len(remaining) != 1:
        reasons.append('no_exact_common_variant' if not remaining else 'ambiguous_exact_variants')
    if reasons:
        return {'status': 'unknown', 'role_id_candidate': None, 'reasons': reasons, 'fields': comparisons}
    return {'status': 'unique_static_variant_candidate', 'role_id_candidate': next(iter(remaining)),
            'reasons': [], 'fields': comparisons}


def read_variant_evidence(image_path, *, frame_sha256, name, client_version, star,
                          database_path=DEFAULT_DATABASE):
    """Read one pinned saved PNG and compare its complete native front summary.

    star is an explicit comparison context, not an automatic image star read.
    The source bank/receipt's physical association is ROOT's separate check.
    """
    started = time.perf_counter()
    path = Path(image_path)
    data = read_bytes(path)
    actual_sha = digest(data)
    if not re.fullmatch(r'[0-9a-f]{64}', str(frame_sha256)) or actual_sha != frame_sha256:
        raise ValueError('explicit source frame SHA256 mismatch')
    with Image.open(io.BytesIO(data)) as header:
        if header.format != 'PNG':
            raise ValueError('variant evidence requires a saved original PNG')
    rgb = image_rgb(data)
    title = read_tooltip(path)
    if title['frame_sha256'] != actual_sha:
        raise ValueError('frame changed during independent title read')
    observation = read_summary(rgb, actual_sha)
    query = Catalogue(database_path).query(name, kind='characters', detail=True, star=star)
    candidates = catalogue_variants(query, name=name, client_version=client_version, star=star)
    result = compare_fields(candidates, {'front_summary': observation})
    if (title['status'] != 'readable_title_and_type' or normalize_name(title.get('name') or '') != normalize_name(name)
            or title.get('position') not in ('前台', '前后台')):
        result.update(status='unknown', role_id_candidate=None,
                      reasons=result['reasons'] + ['title_or_position_not_bound_to_expected_name'])
    if file_digest(path) != actual_sha or file_digest(database_path) != query['database_sha256']:
        raise ValueError('source frame or catalogue changed during read')
    observed_fields = {'front_summary': observation}
    return {'schema': SCHEMA, **result, 'frame_sha256': actual_sha, 'source_path': str(path.resolve()),
            'database_sha256': query['database_sha256'], 'client_version_context': client_version,
            'comparison_star': star, 'name_read': title, 'observed_fields': observed_fields,
            'catalogue_candidates': candidates,
            'unobserved_fields': sorted({field for item in candidates for field in item['fields']} - observed_fields.keys()),
            'matching_policy': 'Complete field equality after NFKC/whitespace/one-terminal-full-stop normalization only.',
            'price_used_for_matching': False, 'current_shop_price': None, 'observed_base_cost': None,
            'star': None, 'physical_uid': None, 'owned': None, 'effective_pool': None,
            'production_approved': False, 'needs_current_binding': True, 'input_sent': False,
            'elapsed_ms': round((time.perf_counter()-started)*1000, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--frame-sha256', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--client-version', required=True)
    parser.add_argument('--star', type=int, required=True, help='Explicit comparison context, not a star read.')
    parser.add_argument('--db', type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    result = read_variant_evidence(args.image, frame_sha256=args.frame_sha256, name=args.name,
                                   client_version=args.client_version, star=args.star, database_path=args.db)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
