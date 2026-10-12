"""Offline reward localization: real retained PNGs and explicit mutations.

Synthetic relocation/negative frames below test the detector contract only;
they are not historical successors or per-click reward receipts.
"""
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['ORT_DISABLE_TELEMETRY'] = '1'

import numpy as np
from PIL import Image, ImageOps

import currency_wars_rewards as rewards
from currency_wars_perception import Perception


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'handoff/2026-10-07/reward-sequence'


def image_digest(image):
    output = io.BytesIO()
    image.save(output, format='PNG')
    return hashlib.sha256(output.getvalue()).hexdigest()


class RewardObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf8'))
        cls.images, cls.observations = {}, {}
        reader = Perception()
        for case in cls.manifest['frames']:
            path = FIXTURES / case['file']
            if hashlib.sha256(path.read_bytes()).hexdigest() != case['export_image_sha256']:
                raise AssertionError('public reward frame hash mismatch')
            with Image.open(path) as opened:
                cls.images[case['file']] = opened.convert('RGB')
            cls.observations[case['file']] = reader.read(path)

    def read(self, image=None, rows=None, page='preparation', snapshot_id=None):
        image = image if image is not None else self.images['q01.png']
        rows = rows if rows is not None else self.observations['q01.png']['rows']
        return rewards.detect(image, page, rows, snapshot_id or image_digest(image))

    def test_template_is_an_exact_source_bound_crop(self):
        resource = json.loads((rewards.RESOURCE_DIR / 'manifest.json').read_text(encoding='utf8'))
        self.assertEqual(resource['source_export_sha256'], self.manifest['frames'][1]['export_image_sha256'])
        self.assertEqual(resource['source_original_sha256'], self.manifest['frames'][1]['source_image_sha256'])
        self.assertEqual(resource['source_roi'], list(rewards.TEMPLATE_SOURCE_ROI))
        self.assertEqual(resource['sha256'], rewards.TEMPLATE_SHA256)
        np.testing.assert_array_equal(rewards._template(), np.array(
            self.images['q01.png'].crop(rewards.TEMPLATE_SOURCE_ROI)))

    def test_real_full_frames_cover_occlusion_two_controls_and_a_side_prompt(self):
        before, exposed, after = [self.observations[name]['semantic']['rewards']
            for name in ('q00.png', 'q01.png', 'q02.png')]
        self.assertFalse(before['scanned'])
        self.assertEqual(before['reason'], 'shop_occludes_reward_area')
        self.assertEqual(before['targets'], [])
        self.assertTrue(exposed['scanned'])
        self.assertTrue(exposed['input_allowed'])
        self.assertEqual(len(exposed['targets']), 2)
        for target, expected in zip(exposed['targets'], ((1585, 300), (1535, 355))):
            self.assertLessEqual(max(abs(a-b) for a, b in zip(target['center'], expected)), 4)
            self.assertGreaterEqual(target['score'], .90)
            self.assertEqual(target['snapshot_id'], self.observations['q01.png']['snapshot_id'])
        self.assertTrue(after['scanned'])
        self.assertEqual(after['targets'], [])
        self.assertTrue(after['interaction_required'])
        self.assertFalse(after['area_fully_visible'])
        self.assertFalse(after['input_allowed'])
        self.assertEqual(after['interaction_evidence'][0]['source_row']['text'], '取消')
        for observed in (before, exposed, after):
            self.assertIsNone(observed['all_rewards_cleared'])
        # There is one two-click group, not two independently observed claims.
        group = self.manifest['receipts'][1]
        self.assertEqual((group['before_frame_index'], group['after_frame_index']), (1, 2))
        self.assertEqual(sum(action['type'] == 'click' for action in group['projection']['completed']), 2)

    def test_wrong_page_missing_or_conflicting_anchors_and_unbound_frames_withhold_targets(self):
        rows = copy.deepcopy(self.observations['q01.png']['rows'])
        header = next(row for row in rows if row['text'] == '备战阶段')
        cases = [self.read(page='shop'), self.read(page='supply'), self.read(rows=[]),
            self.read(rows=rows+[header]),
            self.read(rows=[{**row, 'confidence': .89} if row is header else row for row in rows]),
            self.read(rows=rows+[{'text': '收起', 'confidence': .99, 'box': [1596, 966, 1650, 998]}]),
            self.read(image=Image.new('RGB', (1280, 720))),
            rewards.detect(self.images['q01.png'], 'preparation', rows, None)]
        for observed in cases:
            with self.subTest(reason=observed['reason']):
                self.assertFalse(observed['scanned'])
                self.assertFalse(observed['input_allowed'])
                self.assertEqual(observed['targets'], [])
                self.assertIsNone(observed['all_rewards_cleared'])

    def test_controls_are_relocated_from_each_frame_not_memorized_centers(self):
        image = self.images['q01.png'].copy()
        background = image.crop((1330, 380, 1404, 449))
        for target in self.observations['q01.png']['semantic']['rewards']['targets']:
            image.paste(background, tuple(target['bounds'][:2]))
        template = self.images['q01.png'].crop(rewards.TEMPLATE_SOURCE_ROI)
        for point in ((1360, 395), (1510, 260)):
            image.paste(template, point)
        observed = self.read(image=image)
        self.assertTrue(observed['input_allowed'])
        self.assertEqual(len(observed['targets']), 2)
        self.assertEqual([target['center'] for target in observed['targets']], [[1547, 294], [1397, 429]])
        self.assertNotEqual(observed['snapshot_id'], self.observations['q01.png']['snapshot_id'])
        self.assertIsNone(observed['all_rewards_cleared'])

    def test_absence_is_limited_and_color_or_prompt_uncertainty_blocks_input(self):
        empty = self.images['q01.png'].copy()
        background = empty.crop((1330, 380, 1404, 449))
        gray = self.images['q01.png'].copy()
        for target in self.observations['q01.png']['semantic']['rewards']['targets']:
            empty.paste(background, tuple(target['bounds'][:2]))
            gray.paste(ImageOps.grayscale(gray.crop(target['bounds'])).convert('RGB'),
                       tuple(target['bounds'][:2]))
        negative = self.read(image=empty)
        self.assertTrue(negative['scanned'])
        self.assertEqual(negative['targets'], [])
        self.assertEqual(negative['reason'], 'no_blue_orb_match_in_scanned_region')
        self.assertIsNone(negative['all_rewards_cleared'])
        rejected = self.read(image=gray)
        self.assertTrue(rejected['uncertain'])
        self.assertFalse(rejected['input_allowed'])
        self.assertEqual(rejected['targets'], [])
        prompt = {'text': '取消', 'confidence': .99, 'box': [1773, 252, 1826, 280]}
        waiting = self.read(rows=self.observations['q01.png']['rows']+[prompt])
        self.assertTrue(waiting['interaction_required'])
        self.assertFalse(waiting['input_allowed'])

    def test_resource_replacement_is_rechecked_without_a_stale_template_cache(self):
        with tempfile.TemporaryDirectory(prefix='cw-reward-template-') as temporary:
            path = Path(temporary) / 'blue-orb-q01.png'
            path.write_bytes((rewards.RESOURCE_DIR / path.name).read_bytes())
            with patch.object(rewards, 'RESOURCE_DIR', Path(temporary)):
                self.assertTrue(self.read()['input_allowed'])
                Image.new('RGB', rewards.TEMPLATE_SIZE, (0, 0, 255)).save(path)
                changed = self.read()
                self.assertFalse(changed['scanned'])
                self.assertEqual(changed['reason'], 'reward_template_unavailable')
                self.assertFalse(changed['input_allowed'])
                self.assertEqual(changed['targets'], [])
                path.unlink()
                self.assertFalse(self.read()['scanned'])


if __name__ == '__main__':
    unittest.main()
