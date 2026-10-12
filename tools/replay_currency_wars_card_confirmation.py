"""Frozen card checks and local retained-PNG confirmation diagnostics.

No OCR, capture, controller, input or installation. A schema=1 pair spec names
request_json, reply_json, original_png and current_png. It may name either
current_observation_json (historical) or independent_observation_json (a later
read), never both. Omitting both, or supplying historical JSON without rows,
is a valid missing-evidence diagnostic, not historical visual eligibility.
Without either observation JSON, current_png_sha256 must bind the second PNG.
Paths are local, absolute or relative to the spec. Optional
manual_annotation.confirm_disabled is a Boolean human annotation only.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import time
import unittest


BASE = '02e0a7c86f82e74216b759bed60a1a7cb55849eb'
SELECTION = ('test_currency_wars_card_confirmation.CardConfirmationTests',)
FIXTURE_SOURCES = ('tools/test_local_runtime_compatibility.py',)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def hashes(root):
    from currency_wars_source_guard import production_files
    names = set(production_files(root)) | set(FIXTURE_SOURCES)
    names.add('tools/' + Path(__file__).name)
    names.update('tools/' + name.split('.')[0] + '.py' for name in SELECTION)
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def read_object(path):
    payload = Path(path).read_bytes()
    if len(payload) > 2_000_000:
        raise ValueError('Retained JSON exceeds its bound')
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError('Retained JSON must be an object')
    return result, hashlib.sha256(payload).hexdigest()


def box_inside(box, bounds):
    return (isinstance(box, (list, tuple)) and len(box) == 4
        and all(type(value) is int for value in box)
        and bounds[0] <= box[0] < box[2] <= bounds[2]
        and bounds[1] <= box[1] < box[3] <= bounds[3])


def bounded_rows(observation, bounds):
    rows = observation.get('rows') if isinstance(observation, dict) else None
    return [row for row in rows if isinstance(row, dict) and box_inside(row.get('box'), bounds)] \
        if isinstance(rows, list) else []


def report_rows(rows):
    """Only bounded text/confidence/boxes, never the raw row dictionary."""
    result = []
    for row in rows:
        text = row.get('text')
        private = (not isinstance(text, str) or len(text) > 2048
            or re.search(r'(?i)\b(?:uid|owner|token|password|authorization)\b|(?<!\d)\d{7,}(?!\d)', text))
        confidence = row.get('confidence')
        result.append({'text': '[redacted-sensitive-row]' if private else text,
            'confidence': confidence if type(confidence) in (int, float) and math.isfinite(confidence) else None,
            'box': list(row['box']), 'text_redacted': bool(private)})
    return result


def text_bounds(observation, confirm_bounds):
    from currency_wars_perception import clean
    rows = [row for row in bounded_rows(observation, confirm_bounds)
        if isinstance(row.get('text'), str) and clean(row['text']) == '确认'
        and type(row.get('confidence')) in (int, float) and .90 <= row['confidence'] <= 1.]
    if len(rows) != 1:
        return None
    x, y, right, bottom = rows[0]['box']
    return [x-2, y-2, right+2, bottom+2]


def crop(image, bounds):
    return image[bounds[1]:bounds[3], bounds[0]:bounds[2]]


def pixel_statistics(image, bounds):
    import cv2
    import numpy as np
    rgb = crop(image, bounds)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = (hsv[:, :, index] for index in range(3))
    white = (s < 70) & (v > 190)
    production = white | ((h >= 80) & (h <= 110) & (s > 80) & (v > 140))
    def channels(array, labels):
        values = np.quantile(array.reshape(-1, 3), [0, .1, .25, .5, .75, .9, 1.], axis=0)
        keys = ('min', 'p10', 'p25', 'median', 'p75', 'p90', 'max')
        return {label: {key: round(float(value), 4) for key, value in zip(keys, values[:, index])}
                for index, label in enumerate(labels)}
    return {'bounds': list(bounds), 'pixels': int(rgb.shape[0] * rgb.shape[1]),
        'white_mask_count': int(np.count_nonzero(white)),
        'existing_text_mask_count': int(np.count_nonzero(production)),
        'rgb': channels(rgb, ('r', 'g', 'b')), 'opencv_hsv': channels(hsv, ('h', 's', 'v'))}


def pixel_difference(images, bounds):
    import numpy as np
    old, fresh = (crop(image, bounds).astype(np.int16) for image in images)
    delta = np.abs(old - fresh)
    changed = np.any(delta != 0, axis=2)
    yy, xx = np.nonzero(changed)
    local = [int(xx.min()), int(yy.min()), int(xx.max())+1, int(yy.max())+1] if len(xx) else None
    return {'bounds': list(bounds), 'pixels_equal': not bool(np.any(changed)),
        'changed_pixels': int(np.count_nonzero(changed)), 'changed_fraction': float(np.mean(changed)),
        'mean_absolute_rgb_delta': [float(value) for value in np.mean(delta, axis=(0, 1))],
        'max_absolute_channel_delta': int(np.max(delta)), 'difference_bounds_local': local}


def option_summary(observation, layout):
    from currency_wars_perception import clean, option_facts
    if not isinstance(observation, dict):
        return {'rows_present': False, 'native_options_present': False, 'cards': []}
    rows = observation.get('rows')
    semantic = observation.get('semantic')
    options = semantic.get('options') if isinstance(semantic, dict) else None
    native = isinstance(options, list)
    derived = option_facts(rows, 'environment') if isinstance(rows, list) else None
    cards = []
    for index, bounds in enumerate(layout, 1):
        visible = bounded_rows(observation, bounds)
        option = options[index-1] if native and index <= len(options) and isinstance(options[index-1], dict) else None
        labels = ([option.get('title'), *option.get('effect_lines', [])]
            if option and isinstance(option.get('effect_lines'), list) else [])
        texts = [clean(row['text']) for row in visible if isinstance(row.get('text'), str)]
        cards.append({'card_index': index, 'bounds': list(bounds), 'rows': report_rows(visible),
            'native_option_present': option is not None,
            'native_bounds_match': bool(option and option.get('bounds') == list(bounds)),
            'native_index_match': bool(option and option.get('card_index') == index),
            'native_label_count': len(labels),
            'native_labels_each_have_one_row': bool(labels and all(isinstance(label, str)
                and texts.count(clean(label)) == 1 for label in labels)),
            'native_effect_count': len(option['effect_lines']) if option and isinstance(option.get('effect_lines'), list) else None})
    return {'rows_present': isinstance(rows, list), 'native_options_present': native,
        'native_option_count': len(options) if native else None,
        'native_options_sha256': digest(options) if native else None,
        'native_options_equal_pure_row_derivation': options == derived if native and derived is not None else None,
        'pure_row_derivation_is_new_ocr': False, 'cards': cards}


def export_calibration(directory, pair_hash, images, png_hashes, bounds):
    from PIL import Image
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for index, (image, source_hash, regions) in enumerate(zip(images, png_hashes, bounds)):
        for role, region in regions:
            name = 'pair-' + pair_hash[:16] + '-' + str(index) + '-' + role + '.png'
            stream = io.BytesIO()
            Image.fromarray(crop(image, region), mode='RGB').save(stream, format='PNG')
            payload = stream.getvalue()
            (directory / name).write_bytes(payload)
            artifacts.append({'artifact_name': name, 'source_frame_index': index,
                'source_png_sha256': source_hash, 'crop_bounds': list(region),
                'crop_png_sha256': hashlib.sha256(payload).hexdigest(), 'role': role})
    manifest = {'schema': 'currency-wars-confirmation-local-calibration/v1', 'spec_sha256': pair_hash,
        'artifacts': artifacts, 'derived_calibration_samples': True,
        'independent_generalization_examples': False, 'historical_input_qualification': False,
        'native_disabled_state': 'unknown', 'automatically_uploaded': False}
    (directory / ('pair-' + pair_hash[:16] + '-calibration.json')).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def replay_pair(spec_path, calibration_dir=None):
    """Diagnose supplied bytes; never synthesize historical rows or identities."""
    import numpy as np
    from PIL import Image
    from currency_wars_perception import ENVIRONMENT_CONFIRM_BOUNDS, OPTION_LAYOUTS, fingerprint
    from currency_wars_visual_guards import _card_outline_equal, _text_equal, stable_semantic_target

    spec_path = Path(spec_path).resolve()
    spec, spec_hash = read_object(spec_path)
    if type(spec.get('schema')) is not int or spec['schema'] != 1:
        raise ValueError('Retained pair specification schema is invalid')
    def local_path(key):
        name = spec.get(key)
        if not isinstance(name, str) or not name:
            raise ValueError('Retained pair file is missing')
        return (spec_path.parent / name).resolve()
    historical_key, independent_key = 'current_observation_json', 'independent_observation_json'
    if historical_key in spec and independent_key in spec:
        raise ValueError('Historical and independent observation inputs are mutually exclusive')
    request, request_hash = read_object(local_path('request_json'))
    reply, reply_hash = read_object(local_path('reply_json'))
    actual, actual_hash = None, None
    actual_kind = 'unavailable'
    observation_key = historical_key if historical_key in spec else independent_key if independent_key in spec else None
    if observation_key:
        actual, actual_hash = read_object(local_path(observation_key))
        actual_kind = 'historical' if observation_key == historical_key else 'independent_reread'
    original = request.get('observation')
    if not isinstance(original, dict) or original.get('page') != 'environment' or request.get('kind') != 'environment_strategy':
        raise ValueError('Only an original environment strategy request is supported')
    images, png_hashes, prints = [], [], []
    for key in ('original_png', 'current_png'):
        payload = local_path(key).read_bytes()
        if len(payload) > 25 * 1024 * 1024:
            raise ValueError('Retained PNG exceeds its bound')
        png_hashes.append(hashlib.sha256(payload).hexdigest())
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != 'PNG' or image.size != (1920, 1080):
                raise ValueError('Pair must contain native 1920x1080 PNGs; no resizing')
            prints.append(fingerprint(image))
            images.append(np.array(image.convert('RGB')))
    if png_hashes[0] != request.get('snapshot_id') or png_hashes[0] != original.get('snapshot_id'):
        raise ValueError('Original PNG is not bound to the original request')
    current_digest = actual.get('snapshot_id') if actual is not None else spec.get('current_png_sha256')
    if (not isinstance(current_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', current_digest)
            or png_hashes[1] != current_digest
            or ('current_png_sha256' in spec and spec['current_png_sha256'] != png_hashes[1])):
        raise ValueError('Current PNG is not bound to its supplied source')
    # Relocation changes only the local lookup path. No source field, rows,
    # frame, capture, epoch, snapshot or fingerprint is filled or repaired.
    request = copy.deepcopy(request)
    request['original_png'] = str(local_path('original_png'))
    observations = (original, actual)
    identities = all(isinstance(obs, dict) and all(isinstance(obs.get(key), str) and bool(obs[key])
        for key in ('capture_request_id', 'frame_id', 'snapshot_id')) for obs in observations)
    digests_match = png_hashes[0] == request.get('snapshot_id') == original.get('snapshot_id') and png_hashes[1] == current_digest
    fingerprints_match = all(isinstance(obs, dict) and obs.get('fingerprint') == value
        for obs, value in zip(observations, prints))
    request_reply_match = all(isinstance(request.get(key), str) and bool(request[key]) and reply.get(key) == request[key]
        for key in ('request_id', 'snapshot_id', 'resume_epoch'))
    actions = reply.get('actions')
    single_action = isinstance(actions, list) and len(actions) == 1 and isinstance(actions[0], dict)
    eligible = (bool(stable_semantic_target(actions[0], request, actual, local_path('current_png')))
        if actual is not None and single_action else None)
    rows_present = all(isinstance(obs, dict) and isinstance(obs.get('rows'), list) and bool(obs['rows']) for obs in observations)
    historical_available = actual_kind == 'historical' and rows_present
    byte_bound = bool(eligible and digests_match and fingerprints_match and request_reply_match and rows_present)
    confirm_bounds = list(ENVIRONMENT_CONFIRM_BOUNDS)
    text_regions = [text_bounds(obs, confirm_bounds) for obs in observations]
    confirm = []
    for index, (obs, image, region) in enumerate(zip(observations, images, text_regions)):
        confirm.append({'source_frame_index': index,
            'row_source': 'original_request' if index == 0 else actual_kind,
            'rows_present': isinstance(obs, dict) and isinstance(obs.get('rows'), list),
            'rows': report_rows(bounded_rows(obs, confirm_bounds)),
            'fixed_roi': pixel_statistics(image, confirm_bounds),
            'unique_high_confidence_text_crop': pixel_statistics(image, region) if region else None})
    text_pair = None
    if all(region is not None for region in text_regions):
        old, fresh = text_regions
        union = [min(old[0], fresh[0]), min(old[1], fresh[1]), max(old[2], fresh[2]), max(old[3], fresh[3])]
        text_pair = {'box_edges_within_existing_four_pixel_rule': all(abs(a-b) <= 4 for a, b in zip(old, fresh)),
            'pixel_difference': pixel_difference(images, union),
            'existing_text_equal': bool(_text_equal(images, union))}
    layout = OPTION_LAYOUTS['environment']
    card_checks = []
    for index, bounds in enumerate(layout, 1):
        x, y, right, bottom = bounds
        book = [right-45, y+21, right-21, y+41]
        corners = ([x-8, y-8, x+24, y+24], [right-24, y-8, right+8, y+24],
                   [x-8, bottom-24, x+24, bottom+8], [right-24, bottom-24, right+8, bottom+8])
        card_checks.append({'card_index': index, 'bounds': list(bounds),
            'existing_card_outline_equal': bool(_card_outline_equal(images, bounds)),
            'existing_outline_covers_complete_corners': False,
            'corner_pixel_diagnostics': [pixel_difference(images, region) for region in corners],
            'book_bounds': book, 'existing_book_text_equal': bool(_text_equal(images, book)),
            'book_pixel_statistics': [pixel_statistics(image, book) for image in images]})
    annotation = spec.get('manual_annotation')
    annotation = annotation.get('confirm_disabled') if isinstance(annotation, dict) else None
    report = {'spec_sha256': spec_hash, 'request_json_sha256': request_hash, 'reply_json_sha256': reply_hash,
        'supplied_observation_json_sha256': actual_hash, 'supplied_observation_kind': actual_kind,
        'png_sha256': png_hashes, 'png_format_and_1920x1080_verified': True,
        'original_png_matches_request': png_hashes[0] == request.get('snapshot_id') == original.get('snapshot_id'),
        'supplied_observation_png_matches': bool(actual is not None and png_hashes[1] == actual.get('snapshot_id')),
        'original_source_identities_present': identities, 'original_png_digests_match': digests_match,
        'original_fingerprints_match': fingerprints_match, 'request_reply_identity_match': request_reply_match,
        'reply_has_single_action': single_action, 'historical_qualification_available': historical_available,
        'historical_visual_eligibility': eligible if historical_available else None,
        'historical_byte_bound_visual_eligibility': bool(historical_available and identities and byte_bound),
        'historical_qualification_status': 'available' if historical_available else
            'missing_historical_rows' if actual_kind == 'historical' else 'missing_historical_actual',
        'independent_visual_eligibility': eligible if actual_kind == 'independent_reread' else None,
        'independent_byte_bound_visual_eligibility': byte_bound if actual_kind == 'independent_reread' else None,
        'independent_reread_requires_historical_capture_identities': False,
        'native_disabled_state': 'unknown', 'native_enabled_state': 'unknown',
        'manual_annotation': {'confirm_disabled': annotation, 'is_native_state': False} if type(annotation) is bool else None,
        'confirm_frames': confirm, 'confirm_fixed_roi_difference': pixel_difference(images, confirm_bounds),
        'confirm_text_pair': text_pair,
        'text_mask_definition': 'Existing _text_equal: (S < 70 and V > 190) or (80 <= H <= 110 and S > 80 and V > 140); OpenCV HSV.',
        'card_sources': [option_summary(obs, layout) for obs in observations], 'existing_card_region_checks': card_checks,
        'diagnostic_region_checks_grant_input_eligibility': False,
        'native_capture_authentication_replayed': False, 'current_epoch_pending_foreground_verified': False,
        'historical_rows_or_identities_reconstructed': False, 'input_published': False,
        'card_selection_outcome_verified': False, 'confirmation_outcome_verified': False}
    if calibration_dir is not None:
        regions = [[('confirm-roi', confirm_bounds)] + ([('confirm-text', region)] if region else []) for region in text_regions]
        report['local_calibration'] = export_calibration(calibration_dir, spec_hash, images, png_hashes, regions)
    return report


def cases(suite):
    for value in suite:
        if isinstance(value, unittest.TestSuite):
            yield from cases(value)
        else:
            yield value


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes, self.started = [], {}

    def startTest(self, test):
        self.started[test.id()] = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        failed = any(item is test or getattr(item, 'test_case', None) is test for item, unused in self.failures)
        error = any(item is test or getattr(item, 'test_case', None) is test for item, unused in self.errors)
        skipped = any(item is test for item, unused in self.skipped)
        self.outcomes.append({'test': test.id(), 'status': 'error' if error else 'failed' if failed else
            'skipped' if skipped else 'passed', 'seconds': time.perf_counter() - self.started[test.id()]})
        super().stopTest(test)


def run_protocol():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(SELECTION)
    selected = [test.id() for test in cases(suite)]
    if (loader.errors or len(selected) != 6 or len(set(selected)) != 6
            or any(not name.startswith(SELECTION[0] + '.test_') for name in selected)):
        raise ValueError('Frozen 6-test selection did not resolve; refuse imported/old discovery')
    stream = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    return {'selection': list(SELECTION), 'tests': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'skips': len(result.skipped), 'elapsed_seconds': time.perf_counter()-started,
        'test_results': result.outcomes,
        'failure_details': [{'test': test.id(), 'traceback': trace} for test, trace in result.failures + result.errors],
        'skip_details': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped],
        'ok': result.wasSuccessful() and not result.skipped}, stream.getvalue()


def checkout_head(root):
    try:
        values = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--show-toplevel', 'HEAD'],
            text=True, stderr=subprocess.DEVNULL).splitlines()
        return values[1] if len(values) == 2 and Path(values[0]).resolve() == root else None
    except (OSError, subprocess.CalledProcessError):
        return None


def environment():
    versions = {}
    for name in ('numpy', 'Pillow', 'psutil', 'opencv-python'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {'system': platform.system(), 'python': platform.python_version(), 'packages': versions}


def run(root, output, *, protocol=False, pair_specs=(), calibration_dir=None):
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    root = Path(root).resolve()
    if Path(__file__).resolve().parent != root / 'tools':
        raise ValueError('Driver and tested sources must come from the same candidate')
    before = hashes(root)
    from currency_wars_source_guard import production_files
    report = {'schema': 'currency-wars-card-confirmation-diagnostic/v1', 'basis_commit': BASE,
        'tested_checkout_head': checkout_head(root),
        'source_binding_authority': 'Exact before/after bytes; checkout HEAD does not identify uncommitted files.',
        'environment': environment(), 'source_file_count': len(before),
        'runtime_source_count': len(production_files(root)), 'source_sha256': before,
        'game_inputs': 0, 'controllers_started': 0, 'new_game_captures': 0, 'new_ocr_calls': 0,
        'online_model_called': False, 'private_resources_read_or_exported': False,
        'candidate_installed': False, 'ready_changed': False, 'whole_game_speedup': None,
        'same_endpoint_speedup': None, 'native_disabled_state': 'unknown',
        'limits': [
            'Six focused protocol methods use generated frames and declared reader rows, not native gameplay.',
            'Independent rereads cannot fill historical actual rows, frame/capture identities or epoch.',
            'Gray or zero-mask text is not inferred to be disabled, enabled, or safe to click.',
            'Existing region checks are diagnostics only and do not bypass the production visual guard.',
            'Optional local crops are derived calibration samples, not independent generalization positives.',
            'No live epoch, pending, foreground, capture authenticity, input receipt or selection outcome is verified.',
        ]}
    log = None
    if protocol:
        report['protocol'], log = run_protocol()
    report['retained_pairs'], report['pair_errors'] = [], []
    for index, spec in enumerate(pair_specs):
        try:
            report['retained_pairs'].append(replay_pair(spec, calibration_dir))
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            # Decoder messages, local paths and raw user data must not leak.
            report['pair_errors'].append({'pair_index': index, 'error_type': type(exc).__name__})
    after = hashes(root)
    differences = {key: {'before': before.get(key), 'after': after.get(key)}
        for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)}
    report.update(source_sha256_after_differences=differences,
        source_set_sha256_before=digest(before), source_set_sha256_after=digest(after), source_unchanged=not differences)
    # A correct diagnostic of an unknown/refused target is not a driver error.
    # Visual eligibility stays in the separate, source-qualified pair fields.
    report['ok'] = not differences and report.get('protocol', {}).get('ok', True) and not report['pair_errors']
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if log is not None:
        output.with_suffix('.tests.txt').write_text(log, encoding='utf-8')
    summary = {key: report[key] for key in ('ok', 'source_file_count', 'runtime_source_count',
        'source_set_sha256_before', 'source_set_sha256_after', 'native_disabled_state')}
    summary.update(retained_pair_count=len(report['retained_pairs']), pair_error_count=len(report['pair_errors']))
    if protocol:
        summary.update({key: report['protocol'][key] for key in ('tests', 'failures', 'errors', 'skips', 'elapsed_seconds')})
    print(json.dumps(summary))
    if protocol and not report['protocol']['ok']:
        print(log)
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--protocol', action='store_true')
    parser.add_argument('--pair-spec', action='append', default=[])
    parser.add_argument('--calibration-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.protocol and not args.pair_spec:
        parser.error('Select --protocol or an existing --pair-spec')
    if args.calibration_dir is not None and not args.pair_spec:
        parser.error('--calibration-dir requires --pair-spec')
    return run(args.root, args.output, protocol=args.protocol, pair_specs=args.pair_spec, calibration_dir=args.calibration_dir)


if __name__ == '__main__':
    raise SystemExit(main())
