"""Offline native-ROI calibration and six focused environment-card checks.

Requires the seven unmodified public evidence files from commit 6ef3793a.
No download, OCR, screenshot, controller, input, installation or READY change.
The complete historical actual observation is unavailable; calibration cannot
restore its identity or approve ROOT's old request. Mixed test canvases receive
their own new hashes and are not reported as retained full game frames.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import time
import unittest

from replay_currency_wars_card_confirmation import Result, cases, checkout_head, digest, environment


BASE = '30870dd5113456fae930d5a23d44b7556611605e'
EVIDENCE_COMMIT = '6ef3793a827af65ff047ef89941714f7dd7a0017'
SELECTION = 'test_currency_wars_native_card_ink.NativeCardInkTests'
FULL_PNG_SHA256 = (
    'bdbc45bd1343b4b78ca11c1b626eccb7dec1767e422b36237c96736cfac57e85',
    'd38dce61543579231ae4e1144e182552af105bf9db017b5c7faf48c501e43da2',
)
EVIDENCE_SHA256 = {
    'ROOT_ENVIRONMENT_CONFIRM_RGB.json': 'b57fb0d915eb2693d3b92d5f7482100d5339c8931e7aa02c49cd34396fc676c0',
    'ROOT_ENVIRONMENT_CAPTIONS_RGB.json': '80351a617917cb89acde862db56756b39d0e466db9d89ba6c91cf71914222f6d',
    'ROOT_NATIVE_CONFIRM_CALIBRATION/pair-886956e3ada52cf7-0-confirm-roi.png': '14bd876a520186181d223976f41c6ffb613d254b593a0b7cb81d294ecadc2ceb',
    'ROOT_NATIVE_CONFIRM_CALIBRATION/pair-886956e3ada52cf7-0-confirm-text.png': 'bba43de081fdee55d550651be5920220239eab6dadb091bc98620f05a391b2ed',
    'ROOT_NATIVE_CONFIRM_CALIBRATION/pair-886956e3ada52cf7-1-confirm-roi.png': 'c9daacdd76f2623cd0e1ccbece11c6560926ed45ae3bfa23c9fd1b06ff41f407',
    'ROOT_NATIVE_CONFIRM_CALIBRATION/pair-886956e3ada52cf7-1-confirm-text.png': '5f28851c8649c589cfdfbbecbdc15d8106462fd095011e600ec55c55fa5ce816',
    'ROOT_NATIVE_CONFIRM_CALIBRATION/pair-886956e3ada52cf7-calibration.json': '677aa89522233fb9aef57705fed44397116ab819db099c3645c7f9ac72caba09',
}


def hashes(root):
    from currency_wars_source_guard import production_files
    names = set(production_files(root)) | {
        'tools/replay_currency_wars_native_card_ink.py',
        'tools/test_currency_wars_native_card_ink.py',
        'tools/replay_currency_wars_card_confirmation.py',
        'tools/test_currency_wars_card_confirmation.py',
        'tools/test_local_runtime_compatibility.py',
    }
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def evidence_hashes(directory):
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in EVIDENCE_SHA256}


def native_measurements(directory):
    import cv2
    import numpy as np
    from PIL import Image
    import currency_wars_visual_guards as guards
    from test_currency_wars_native_card_ink import CONFIRM_GLYPH, decode_pair, roi_images

    if evidence_hashes(directory) != EVIDENCE_SHA256:
        raise ValueError('Evidence files differ from the frozen public commit')
    confirm = json.loads((directory / 'ROOT_ENVIRONMENT_CONFIRM_RGB.json').read_bytes())
    captions = json.loads((directory / 'ROOT_ENVIRONMENT_CAPTIONS_RGB.json').read_bytes())
    confirm_arrays = decode_pair(confirm)
    manifest = json.loads((directory / 'ROOT_NATIVE_CONFIRM_CALIBRATION' /
                           'pair-886956e3ada52cf7-calibration.json').read_bytes())
    compared_crops = []
    for item in manifest['artifacts']:
        index = item['source_frame_index']
        if index not in (0, 1) or item['source_png_sha256'] != FULL_PNG_SHA256[index]:
            raise ValueError('Calibration crop belongs to a different full PNG')
        name = 'ROOT_NATIVE_CONFIRM_CALIBRATION/' + item['artifact_name']
        if item['crop_png_sha256'] != EVIDENCE_SHA256.get(name):
            raise ValueError('Calibration PNG manifest mismatch')
        with Image.open(directory / name) as image:
            box = item['crop_bounds']
            if image.format != 'PNG' or image.size != (box[2]-box[0], box[3]-box[1]):
                raise ValueError('Calibration PNG layout mismatch')
            pixels = np.array(image.convert('RGB'))
        region = CONFIRM_GLYPH if item['role'] == 'confirm-text' else confirm['bounds']
        def cut(array, outer, inner):
            return array[inner[1]-outer[1]:inner[3]-outer[1], inner[0]-outer[0]:inner[2]-outer[0]]
        if not np.array_equal(cut(pixels, box, region), cut(confirm_arrays[index], confirm['bounds'], region)):
            raise ValueError('Published RGB does not match the independently exported PNG crop')
        compared_crops.append(name)
    if len(compared_crops) != 4 or len(set(compared_crops)) != 4:
        raise ValueError('Calibration requires the two native ROI/text PNG pairs')

    measurements = []
    for item in [dict(confirm, label='确认'), *captions['rois']]:
        images = roi_images(item)
        if tuple(item[role]['full_png_sha256'] for role in ('original', 'actual')) != FULL_PNG_SHA256:
            raise ValueError('Native ROI source pair mismatch')
        label = item['label']
        bounds = CONFIRM_GLYPH if label == '确认' else item['bounds']
        check = (guards._environment_gray_confirmation_equal if label == '确认'
                 else guards._environment_book_equal if label.startswith('yellow_book')
                 else guards._environment_caption_equal)
        x, y, right, bottom = bounds
        crops = [image[y:bottom, x:right] for image in images]
        delta = np.max(np.abs(crops[0].astype(np.int16)-crops[1].astype(np.int16)), axis=2)
        hsv = [cv2.cvtColor(crop, cv2.COLOR_RGB2HSV) for crop in crops]
        measured = {'label': label, 'array_bounds': item['bounds'], 'checked_bounds': bounds,
            'array_rgb_sha256': [item[role]['roi_rgb_sha256'] for role in ('original', 'actual')],
            'legacy_text_pass': bool(guards._text_equal(images, bounds)),
            'candidate_pass': bool(check(images, bounds)),
            'rgb_exact': bool(np.array_equal(*crops)), 'changed_fraction': float(np.mean(delta != 0)),
            'max_rgb_delta': int(delta.max()),
            'white_pixels': [int(np.count_nonzero((a[:, :, 1] < 70) & (a[:, :, 2] > 190))) for a in hsv],
            'edge_pixels': [int(np.count_nonzero(guards._edges(image, bounds))) for image in images]}
        if label == '确认':
            masks = [(a[:, :, 1] < 40) & (a[:, :, 2] >= 100) & (a[:, :, 2] <= 190) for a in hsv]
            union = masks[0] | masks[1]
            halo = cv2.dilate(union.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
            measured.update(gray_pixels=[int(mask.sum()) for mask in masks],
                gray_iou=float(np.count_nonzero(masks[0] & masks[1])/np.count_nonzero(union)),
                gray_rgb_max_delta=int(delta[union].max()), halo_rgb_max_delta=int(delta[halo].max()),
                max_value=[int(a[:, :, 2].max()) for a in hsv])
        measurements.append(measured)
    return {'public_evidence_commit': EVIDENCE_COMMIT, 'evidence_sha256': EVIDENCE_SHA256,
        'source_full_png_sha256': list(FULL_PNG_SHA256), 'array_pairs': len(measurements),
        'decoded_array_sha256_verified': 12, 'independent_png_crosschecks': len(compared_crops),
        'measurements': measurements, 'all_calibrated_regions_pass': all(m['candidate_pass'] for m in measurements),
        'derived_calibration_only': True, 'independent_generalization': False,
        'historical_full_frame_qualification': False, 'historical_actual_rows_reconstructed': False,
        'original_request_reauthorized': False, 'full_native_frames_replayed': 0}


def run_protocol():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames((SELECTION,))
    names = [test.id() for test in cases(suite)]
    if (loader.errors or len(names) != 6 or len(set(names)) != 6
            or any(not name.startswith(SELECTION+'.test_') for name in names)):
        raise ValueError('Frozen six-test selection did not resolve')
    stream = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    return {'selection': [SELECTION], 'tests': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'skips': len(result.skipped), 'seconds': time.perf_counter()-started,
        'test_results': result.outcomes,
        'failure_details': [{'test': test.id(), 'traceback': trace} for test, trace in result.failures+result.errors],
        'ok': result.wasSuccessful() and not result.skipped}, stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    before = hashes(root)
    evidence_directory = args.evidence_dir.resolve()
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    os.environ['CW_NATIVE_CARD_EVIDENCE_DIR'] = str(evidence_directory)
    report = {'schema': 'currency-wars-native-card-ink/v1', 'basis_commit': BASE,
        'tested_checkout_head': checkout_head(root), 'environment': environment(),
        'source_sha256': before, 'source_file_count': len(before), 'runtime_source_count': 24,
        'native_crop_calibration': native_measurements(evidence_directory),
        'game_inputs': 0, 'new_captures': 0, 'new_ocr_calls': 0, 'controllers_started': 0,
        'online_model_called': False, 'installed': False, 'ready_changed': False,
        'historical_actual_available': False, 'whole_game_speedup': None,
        'limits': ['Native positives cover only the supplied six local ROI pairs.',
            'Negative mutations and mixed full canvases are protocol fixtures with new PNG hashes.',
            'A single inert publication does not verify the actual card selection or next page.',
            'No historical epoch, pending, foreground, broker identity or input eligibility is restored.']}
    report['focused_checks'], log = run_protocol()
    after = hashes(root)
    report.update(source_set_sha256_before=digest(before), source_set_sha256_after=digest(after),
        source_unchanged=before == after,
        source_sha256_after_differences={key: {'before': before.get(key), 'after': after.get(key)}
            for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)},
        evidence_unchanged=evidence_hashes(evidence_directory) == EVIDENCE_SHA256)
    report['ok'] = (report['focused_checks']['ok'] and report['source_unchanged']
        and report['evidence_unchanged'] and report['native_crop_calibration']['all_calibrated_regions_pass'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    args.output.with_suffix('.tests.txt').write_text(log, encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('ok', 'source_file_count', 'source_set_sha256_before',
        'source_set_sha256_after', 'evidence_unchanged')}
        | {key: report['focused_checks'][key] for key in ('tests', 'failures', 'errors', 'skips', 'seconds')}))
    if not report['ok']:
        print(log)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
