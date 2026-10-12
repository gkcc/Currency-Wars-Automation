"""Native crop calibration and explicitly separate generated negative cases.

Three scans are three times of the same two controls, not independent spatial
or scale generalization. Coordinate containers do not recreate source frames.
No OCR, capture, broker or game action is executed by these tests.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

import currency_wars_rewards as rewards


def load_native_scans(directory):
    directory = Path(directory)
    report = json.loads((directory / 'diagnosis.json').read_text(encoding='utf-8-sig'))
    if report['scan_bounds'] != list(rewards.SCAN_BOUNDS):
        raise ValueError('Native scan bounds differ from the explicit evidence selection')
    result = []
    for frame in report['frames']:
        item = next(crop for crop in frame['derived_crops'] if crop['bounds'] == report['scan_bounds'])
        source = directory / Path(item['file']).name
        payload = source.read_bytes()
        if (hashlib.sha256(payload).hexdigest() != item['png_sha256']
                or item['source_png_sha256'] != frame['full_source_png_sha256']):
            raise ValueError('Native scan bytes or full-frame source binding changed')
        with Image.open(source) as image:
            if image.size != (340, 270) or image.mode != 'RGB':
                raise ValueError('Native scan crop layout changed')
            crop = np.array(image)
        # Only the scan rectangle contains retained native pixels.
        canvas = np.zeros((1080, 1920, 3), np.uint8)
        canvas[230:500, 1320:1660] = crop
        result.append((frame, canvas))
    if len(result) != 3:
        raise ValueError('Exactly the three retained calibration times are selected')
    return report, result


def mutated_target(image, target, change):
    output = image.copy()
    x, y, right, bottom = target['bounds']
    crop = output[y:bottom, x:right]
    body, ring = rewards._orb_masks(crop.shape)
    if change == 'white_ring':
        crop[ring] = 255
    elif change == 'blank':
        crop[:] = [35, 45, 75]
    elif change == 'flat_color':
        crop[:] = [40, 135, 255] if target['kind'] == 'blue_orb' else [170, 170, 190]
    elif change == 'dim_cover':
        crop[:] = (crop.astype(np.float32) * .55).astype(np.uint8)
    elif change == 'center_cover':
        cx, cy = crop.shape[1]//2, crop.shape[0]//2
        crop[cy-8:cy+9, cx-8:cx+9] = [35, 45, 75]
    elif change == 'edge_cover':
        crop[:, :crop.shape[1]//3] = [35, 45, 75]
    elif change == 'move':
        crop[:] = np.roll(crop.copy(), 6, axis=1)
    elif change == 'change_color':
        crop[body] = crop[body][:, ::-1]
    else:
        raise ValueError(change)
    return output


class NativeRewardDetectorTests(unittest.TestCase):
    metrics = []

    @classmethod
    def setUpClass(cls):
        cls.diagnosis, cls.samples = load_native_scans(os.environ.get('CW_NATIVE_REWARD_EVIDENCE_DIR',
            Path(__file__).resolve().parents[1] / 'handoff/2026-10-08/ROOT_NATIVE_REWARDS'))

    def test_three_native_scans_locate_blue_and_gray_without_old_positions(self):
        self.__class__.metrics = []
        for frame, image in self.samples:
            with self.subTest(frame=frame['label']):
                before = hashlib.sha256(image.tobytes()).hexdigest()
                diagnostics = []
                found, uncertain = rewards._matches(image, rewards._template(), diagnostics)
                self.assertFalse(uncertain)
                self.assertEqual({target['kind'] for target in found}, {'blue_orb', 'gray_orb'})
                self.assertEqual(len(found), 2)
                for target in found:
                    expected = self.diagnosis['manual_annotations'][target['kind'].split('_')[0] + '_bounds']
                    self.assertEqual(target['bounds'], expected)
                    self.assertTrue(target['appearance']['trusted'])
                # Keep weak legacy-template reasons, even though the same orb
                # has a trusted foreground reference; do not hide the old gap.
                self.assertTrue(any(not item['trusted'] for item in diagnostics))
                self.assertEqual(before, hashlib.sha256(image.tobytes()).hexdigest())
                self.__class__.metrics.append({'frame': frame['label'], 'source_png_sha256': frame['full_source_png_sha256'],
                    'scope': 'native_derived_scan_calibration', 'targets': found, 'uncertain': uncertain,
                    'candidate_diagnostics': diagnostics})

    def test_native_foreground_stability_is_distinct_from_whole_crop_rgb(self):
        original = self.samples[0][1]
        targets, unused = rewards._matches(original, rewards._template())
        for unused_frame, actual in self.samples[1:]:
            for target in targets:
                with self.subTest(kind=target['kind']):
                    x, y, right, bottom = target['bounds']
                    self.assertFalse(np.array_equal(original[y:bottom, x:right], actual[y:bottom, x:right]))
                    self.assertTrue(rewards.target_pair_stable(original, actual, target))
                    # Supervisor origin is not read or rewritten by comparator.
                    manual = {**target, 'origin': 'supervising_agent', 'native': False}
                    unchanged = copy.deepcopy(manual)
                    self.assertTrue(rewards.target_pair_stable(original, actual, manual))
                    self.assertEqual(manual, unchanged)

    def test_generated_translation_is_dynamic_location_not_native_coverage(self):
        original = self.samples[0][1]
        targets, unused = rewards._matches(original, rewards._template())
        generated = np.zeros_like(original)
        expected = []
        for target, (x, y) in zip(targets, ((1340, 250), (1540, 390))):
            left, top, right, bottom = target['bounds']
            crop = original[top:bottom, left:right]
            generated[y:y+crop.shape[0], x:x+crop.shape[1]] = crop
            expected.append((target['kind'], [x, y, x+crop.shape[1], y+crop.shape[0]]))
        found, uncertain = rewards._matches(generated, rewards._template())
        self.assertFalse(uncertain)
        self.assertEqual([(target['kind'], target['bounds']) for target in found], expected)
        for old in targets:
            self.assertFalse(rewards.target_pair_stable(original, generated, old))

    def test_derived_occlusion_flat_color_state_and_movement_negatives(self):
        original = self.samples[0][1]
        targets, unused = rewards._matches(original, rewards._template())
        for target in targets:
            for change in ('white_ring', 'blank', 'flat_color', 'dim_cover', 'center_cover',
                           'edge_cover', 'move', 'change_color'):
                with self.subTest(kind=target['kind'], change=change):
                    actual = mutated_target(original, target, change)
                    self.assertFalse(rewards.target_pair_stable(original, actual, target))
                    found, uncertain = rewards._matches(actual, rewards._template())
                    # Movement may locate a new center but never preserves old
                    # target authority; other mutations must not accept it.
                    if change != 'move':
                        self.assertFalse(any(item['kind'] == target['kind'] for item in found))

    def test_missing_or_changed_calibration_is_unknown_not_empty_success(self):
        original = self.samples[0][1]
        anchors = [
            {'text': '备战阶段', 'box': [430, 30, 520, 55], 'confidence': .99},
            {'text': '1-1', 'box': [440, 65, 490, 90], 'confidence': .99},
            {'text': '出战', 'box': [1790, 735, 1840, 770], 'confidence': .99},
            {'text': '商店', 'box': [1590, 970, 1650, 1000], 'confidence': .99},
        ]
        # These declared rows/hash qualify a generated container only. They
        # are not the missing original full production observations.
        image = Image.fromarray(original)
        source = rewards.RESOURCE_DIR
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for filename in ('blue-orb-q01.png', 'initial-blue.png', 'initial-gray.png'):
                (root / filename).write_bytes((source / filename).read_bytes())
            with mock.patch.object(rewards, 'RESOURCE_DIR', root):
                for change in ('missing', 'changed'):
                    target = root / 'initial-gray.png'
                    if change == 'missing':
                        target.unlink()
                    else:
                        target.write_bytes(b'changed resource')
                    value = rewards.detect(image, 'preparation', anchors, 'a'*64)
                    self.assertFalse(value['input_allowed'])
                    self.assertFalse(value['scanned'])
                    self.assertEqual(value['reason'], 'reward_template_unavailable')
                    self.assertIsNone(value['all_rewards_cleared'])
        value = rewards.detect(image, 'shop', anchors, 'a'*64)
        self.assertFalse(value['input_allowed'])
        self.assertEqual(value['reason'], 'shop_occludes_reward_area')
        value = rewards.detect(image, 'preparation', [], 'a'*64)
        self.assertFalse(value['input_allowed'])
        self.assertIsNone(value['all_rewards_cleared'])


if __name__ == '__main__':
    unittest.main()
