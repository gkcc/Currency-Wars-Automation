"""Retained-frame regressions: identity ambiguity, false stars, duplicate rows.

Private captures are optional outside this checkout; no capture or input API.
"""
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, 'D:/Codex/home/skills/agent-workflow/scripts')
try:
    from artifacts import scratch_directory
except ImportError:
    scratch_directory = None
from currency_wars_state_reader import StateReader, RESOURCE_DIR, native_slots
from currency_wars_perception import native_deployed_count


ROOT = Path(__file__).resolve().parents[1]
EARLY = ROOT / 'debug/runner-01a10ddf-e6f5f6c47294/4903c1373f2345f99c2e571d33a6f033-strategy.jpg'
LATER = ROOT / 'debug/runner-01a10ddf-e6f5f6c47294/27c5e5256ec9426b8db3dca8e7bd93bf-strategy.jpg'
POPUP = ROOT / 'debug/chat-01a102c9-match-213628-c133e542a500/d012-after-name.jpg'


class NativePopulationTests(unittest.TestCase):
    def test_only_unique_high_confidence_central_hud_certifies_population(self):
        row = {'text': 'i4/4', 'raw_text': 'i4/4', 'confidence': .99, 'box': [880, 212, 1005, 277]}
        self.assertEqual(native_deployed_count([row]), '4/4')
        self.assertIsNone(native_deployed_count([{**row, 'confidence': .89}]))
        self.assertIsNone(native_deployed_count([{**row, 'box': [120, 520, 230, 565]}]))
        self.assertIsNone(native_deployed_count([row, row]))
        self.assertIsNone(native_deployed_count([{**row, 'raw_text': '5/4'}]))


@unittest.skipUnless(scratch_directory is not None and EARLY.is_file() and LATER.is_file() and POPUP.is_file()
                     and (RESOURCE_DIR / 'SOURCES.json').is_file(), 'private retained evidence unavailable')
class RetainedStateTests(unittest.TestCase):
    def read_png(self, source, mutate=None):
        with scratch_directory('cw-state-reader-regression') as run:
            image = Image.open(source).convert('RGB')
            if mutate:
                image = mutate(image)
            path = run / 'observation.png'
            image.save(path)
            return StateReader().read(path)

    def test_actual_early_board_and_bench(self):
        observation = self.read_png(EARLY)
        slots = observation['team']['slots']
        board = {(s['row'], s['slot']): s['name'] for s in slots
                 if s['status'] == 'occupied' and s['location'] == 'board'}
        self.assertEqual(board, {('front', 1): '黄泉', ('front', 2): '椒丘',
                                 ('back', 1): '阿格莱雅', ('back', 2): '乱破'})
        self.assertEqual(len([s for s in slots if s['status'] == 'empty']), 14)
        self.assertEqual([(s['slot'], s['name']) for s in slots
                          if s['location'] == 'bench' and s['status'] == 'occupied'], [(4, '乱破')])
        self.assertFalse(observation['team']['checked'])

    def test_inventory_contour_dedup_and_gold_hair_does_not_become_three_stars(self):
        observation = self.read_png(LATER)
        items = observation['inventory']['items']
        self.assertEqual([item['name'] for item in items], ['钻头', '鞋'])
        self.assertEqual(len({tuple(item['point']) for item in items}), 2)
        blade = [u for u in observation['team']['units'] if u['name'] == '千冶·刃']
        self.assertEqual(len(blade), 1)
        self.assertEqual(blade[0]['star'], 1)
        self.assertFalse(observation['inventory']['checked'])

    def test_popup_and_partial_slot_remain_unknown(self):
        observation = self.read_png(POPUP)
        self.assertFalse(observation['team']['checked'])
        self.assertTrue(observation['team']['unknown_slots'])
        def mask(image):
            image.paste((28, 28, 28), (970, 320, 1260, 810))
            return image
        partial = self.read_png(EARLY, mask)
        slots = {(s['row'], s['slot']): s for s in partial['team']['slots']}
        self.assertEqual(slots['front', 3]['status'], 'unknown')
        self.assertIsNone(slots['front', 3]['name'])

    def test_ambiguous_positive_portrait_does_not_assign_name(self):
        reader = StateReader()
        self.assertTrue(reader._load())
        same = next((item, template) for item, template in reader.templates
                    if item['kind'] == 'unit' and item['name'] == '黄泉')
        reader.templates.append(({**same[0], 'name': 'other_candidate'}, same[1].copy()))
        rgb = np.array(Image.open(EARLY).convert('RGB'))
        unit = reader._slot(rgb, native_slots()[0], True)
        self.assertEqual(unit['status'], 'occupied')
        self.assertIsNone(unit['name'])
        self.assertEqual(unit['star'], 1)


if __name__ == '__main__':
    unittest.main()
