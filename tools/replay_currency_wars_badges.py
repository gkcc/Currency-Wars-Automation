"""Frozen, offline badge ROI replay. No capture, input, or OCR calls.

The default resource is an exact public f01 crop: its positive is calibration,
not independent recognition coverage. --resources reads an existing local
resource directory without exporting it. Neither path reads names/costs or
claims a complete shop observation. The optional baseline is exact PR16 code.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import types

import cv2
import numpy as np
from PIL import Image

import currency_wars_shop_reader as candidate


ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / 'handoff/2026-10-07/free-refresh-sequence'
CALIBRATION = ROOT / 'tools/badge_calibration'
BASE = '5938648de4cfd4b5cd3cd723898abb3c2f806a48'
BASE_READER_SHA = 'e6a069766c010021875d3f002db935af741eb5692af1266abccfa8b8f6338bc3'
BOUND_FILES = (
    'tools/currency_wars_shop_reader.py', 'tools/currency_wars_visual_guards.py',
    'tools/replay_currency_wars_badges.py', 'tools/test_currency_wars_shop_reader.py',
    'tools/test_currency_wars_badge_consumers.py', 'tools/test_currency_wars_badge_replay.py',
    'tools/badge_calibration/SOURCES.json', 'tools/badge_calibration/names.json',
    'tools/badge_calibration/recommend_badge.png', 'tools/badge_calibration/ROI_ANNOTATIONS.json',
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hashes(paths):
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in paths}


class NoOCR:
    """Forbid an unnecessary engine call; never provides recognition output."""
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError('Badge ROI replay must not call OCR')


def load_reader(module, resources):
    reader = module.ShopReader(resources)
    reader.engine = NoOCR()  # Avoid constructing an unused model for this ROI path.
    reader._load()           # Actual production resource loading and provenance.
    return reader


def baseline_module():
    payload = subprocess.run(['git', 'show', BASE + ':tools/currency_wars_shop_reader.py'],
                             cwd=ROOT, check=True, capture_output=True).stdout
    if digest(payload) != BASE_READER_SHA:
        raise ValueError('PR16 shop reader source does not match the frozen baseline')
    module = types.ModuleType('badge_pr16_shop_reader')
    module.__file__ = str(ROOT / 'tools/currency_wars_shop_reader.py')
    exec(compile(payload, module.__file__, 'exec'), module.__dict__)
    return module


def source_frames():
    manifest = json.loads((FRAMES / 'manifest.json').read_text(encoding='utf8'))
    frames = []
    for item in manifest['frames']:
        path = FRAMES / item['file']
        payload = path.read_bytes()
        if digest(payload) != item['export_image_sha256']:
            raise ValueError('Retained public PNG source changed: ' + item['file'])
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != 'PNG' or image.size != (1920, 1080):
                raise ValueError('Unsupported retained frame')
            rgb = np.array(image.convert('RGB'))
        frames.append((item, rgb))
    if [item['file'] for item, unused in frames] != ['f00.png', 'f01.png', 'f02.png']:
        raise ValueError('Unexpected retained frame set')
    return frames


def verify_public_calibration(frames):
    entry = json.loads((CALIBRATION / 'SOURCES.json').read_text(encoding='utf8'))['resources'][0]
    item, rgb = frames[1]
    data = (CALIBRATION / candidate.BADGE_FILE).read_bytes()
    with Image.open(io.BytesIO(data)) as image:
        crop = np.array(image.convert('RGB'))
    x0, y0, x1, y1 = entry['source_bounds']
    if (digest(data) != entry['sha256'] or digest(crop.tobytes()) != entry['rgb_sha256']
            or item['export_image_sha256'] != entry['source_sha256']
            or not np.array_equal(rgb[y0:y1, x0:x1], crop)):
        raise ValueError('Public calibration is not the declared exact source crop')
    return dict(source_file=entry['source_file'], source_sha256=entry['source_sha256'],
                source_bounds=entry['source_bounds'], crop_sha256=entry['sha256'],
                gray_sha256=digest(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).tobytes()),
                exact_pixels=True, independent_positive=False)


def synthetic_frames(rgb, rect):
    """Declared pixel perturbations; these are not retained game frames."""
    x, y, unused_w, unused_h = rect
    positive = rgb[y+3:y+80, x+3:x+82]
    gray = cv2.cvtColor(positive, cv2.COLOR_RGB2GRAY)
    gray_rgb = np.repeat(gray[:, :, None], 3, axis=2)
    orange = cv2.cvtColor(np.uint8([[[16, 220, 225]]]), cv2.COLOR_HSV2RGB)[0, 0]
    patches = {}
    flat = np.empty_like(positive)
    flat[:] = orange
    patches['orange_without_book'] = flat
    patches['book_without_color'] = gray_rgb
    local_gray = positive.copy()
    local_gray[18:45, 17:43] = gray_rgb[18:45, 17:43]
    patches['gray_book_with_original_orange_surround'] = local_gray
    occluded = positive.copy()
    occluded[29:33, 28:32] = 35
    patches['center_4x4_occlusion'] = occluded
    interior_only = positive.copy()
    interior_only[6:57, 7:54] = orange
    interior_only[18:45, 17:43] = positive[18:45, 17:43]
    patches['book_interior_without_original_envelope'] = interior_only
    recolored = cv2.cvtColor(positive, cv2.COLOR_RGB2HSV)
    mask = (recolored[:, :, 1] > 130) & (recolored[:, :, 2] > 130)
    recolored[:, :, 0][mask] = 90
    patches['cyan_book_outside_supported_hues'] = cv2.cvtColor(recolored, cv2.COLOR_HSV2RGB)
    for name, patch in patches.items():
        frame = rgb.copy()
        frame[y+3:y+80, x+3:x+82] = patch
        payload = io.BytesIO()
        Image.fromarray(frame).save(payload, format='PNG')
        yield name, frame, digest(payload.getvalue())


def replay(resources=CALIBRATION, *, compare_base=True):
    source_paths = [ROOT / name for name in BOUND_FILES]
    image_paths = [FRAMES / name for name in ('manifest.json', 'f00.png', 'f01.png', 'f02.png')]
    sources_before, images_before = file_hashes(source_paths), file_hashes(image_paths)
    frames = source_frames()
    calibration = verify_public_calibration(frames)
    annotation_file = CALIBRATION / 'ROI_ANNOTATIONS.json'
    annotations = {frame['file']: frame for frame in json.loads(annotation_file.read_text(encoding='utf8'))['frames']}
    reader = load_reader(candidate, resources)
    baseline = load_reader(baseline_module(), resources) if compare_base else None
    source = reader._badge_source
    # A re-encoded container must not make the same matching pixels appear
    # independent. All f01 positives also calibrate this appearance's limits.
    calibration_in_use = bool(source and source['gray_sha256'] == calibration['gray_sha256'])
    report = dict(schema='currency-wars-badge-replay/v1',
        runtime=dict(python=platform.python_version(), platform=platform.system(),
                     opencv=cv2.__version__, numpy=np.__version__),
        source_sha256=sources_before, public_input_sha256=images_before,
        baseline=dict(commit=BASE, reader_sha256=BASE_READER_SHA) if baseline else None,
        scope=dict(new_game_captures=0, physical_inputs=0, ocr_calls=0, full_shop_reads=0,
                   model_initializations=0, generated_transactions=0, independent_positives=0,
                   production_calls=['ShopReader._load', 'ShopReader._rectangles', 'ShopReader._recommended'],
                   ocr_boundary='A raising sentinel forbids OCR and provides no recognition output'),
        resource_context=dict(kind='public_source_calibration' if calibration_in_use else 'supplied_existing_local',
                              declared_template_count=len(reader.manifest['resources']),
                              loaded_template_count=len(reader.templates),
                              badge_source=source, private_resource_contents_exported=False),
        calibration=calibration, annotation_file=str(annotation_file.relative_to(ROOT)),
        retained_frames=[], synthetic_negatives=[], issues=[])
    for item, rgb in frames:
        rectangles, geometry = reader._rectangles(rgb)
        annotation = annotations[item['file']]
        if (annotation['png_sha256'] != item['export_image_sha256']
                or [slot['slot'] for slot in annotation['slots']] != [1, 2, 3, 4, 5]):
            raise ValueError('Manual annotation source or slot list mismatch')
        frame = dict(file=item['file'], png_sha256=item['export_image_sha256'],
                     size=item['dimensions'], geometry=geometry, slots=[])
        if baseline and baseline._rectangles(rgb) != (rectangles, geometry):
            report['issues'].append(item['file'] + ': baseline geometry changed')
        if len(rectangles) != 5 or geometry.get('overlays') != []:
            report['issues'].append(item['file'] + ': retained geometry unavailable')
        for index, rect in enumerate(rectangles, 1):
            value, evidence = reader._recommended(rgb, rect, snapshot_id=item['export_image_sha256'])
            label = annotation['slots'][index-1]
            annotated = label['visible_book']
            x0, y0, x1, y1 = label['roi_bounds_ltrb']
            if (type(annotated) is not bool or label['card_bounds'] != list(rect)
                    or [x0, y0, x1, y1] != [rect[0]+3, rect[1]+3, rect[0]+82, rect[1]+80]
                    or digest(rgb[y0:y1, x0:x1].tobytes()) != label['roi_rgb_sha256']):
                raise ValueError('Manual annotation does not bind this actual ROI')
            row = dict(slot=index, bounds=list(rect), manual_visible_book=annotated,
                       coverage='source_calibration' if annotated and calibration_in_use else
                                'appearance_calibration' if annotated else 'retained_negative',
                       native_recommended=value, native_evidence=evidence)
            if baseline:
                old_value, old_evidence = baseline._recommended(rgb, rect)
                row['baseline'] = dict(native_recommended=old_value, native_evidence=old_evidence)
            if value is not annotated:
                report['issues'].append(item['file'] + ': slot ' + str(index) + ' differs from separate annotation')
            frame['slots'].append(row)
        report['retained_frames'].append(frame)
    # Always use the public calibration for generated counterexamples; do not
    # turn an unknown private asset's generalization into a required assertion.
    synthetic_reader = reader if calibration_in_use else load_reader(candidate, CALIBRATION)
    item, rgb = frames[1]
    rectangles, unused = reader._rectangles(rgb)
    for name, generated, snapshot in synthetic_frames(rgb, rectangles[4]):
        value, evidence = synthetic_reader._recommended(generated, rectangles[4], snapshot_id=snapshot)
        report['synthetic_negatives'].append(dict(name=name, origin='declared_pixel_perturbation_of_f01_slot5',
            template_context='public_source_calibration', png_sha256=snapshot,
            native_recommended=value, native_evidence=evidence))
        if value is True:
            report['issues'].append('synthetic negative qualified: ' + name)
    report['scope']['ocr_calls'] = sum(item.engine.calls for item in (reader, baseline, synthetic_reader)
                                        if item is not None and item is not reader) + reader.engine.calls
    report['source_changed'] = sources_before != file_hashes(source_paths)
    report['public_input_changed'] = images_before != file_hashes(image_paths)
    try:
        unused_template, current_source = candidate._read_badge_resource(resources)
        report['badge_resource_recheck'] = dict(available=True)
    except (OSError, ValueError, KeyError, TypeError, cv2.error) as exc:
        current_source = None
        report['badge_resource_recheck'] = dict(available=False, error=type(exc).__name__)
    report['badge_resource_changed'] = current_source != source
    if report['source_changed'] or report['public_input_changed'] or report['badge_resource_changed']:
        report['issues'].append('Source, input, or badge resource changed during replay')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources', type=Path, default=CALIBRATION)
    parser.add_argument('--no-baseline', action='store_true', help='Skip the exact PR16 git-object comparison')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = replay(args.resources, compare_base=not args.no_baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf8')
    print(json.dumps(dict(output=str(args.output), resource_context=report['resource_context'],
        frames=[dict(file=f['file'], native=[s['native_recommended'] for s in f['slots']])
                for f in report['retained_frames']], synthetic_negatives=len(report['synthetic_negatives']),
        ocr_calls=report['scope']['ocr_calls'], issues=report['issues']), ensure_ascii=False))
    return int(bool(report['issues']))


if __name__ == '__main__':
    sys.exit(main())
