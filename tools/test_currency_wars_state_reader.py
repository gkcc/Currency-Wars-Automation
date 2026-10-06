"""Retained-frame regressions: identity ambiguity, false stars, duplicate rows.

Private captures are optional outside this checkout; no capture or input API.
"""
import sys
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from PIL import Image

sys.path.insert(0, 'D:/Codex/home/skills/agent-workflow/scripts')
try:
    from artifacts import scratch_directory
except ImportError:
    scratch_directory = None
from currency_wars_state_reader import StateReader, RESOURCE_DIR, native_slots, native_capacity
from currency_wars_perception import (DEPLOYED_COUNT_ROI, PLAYER_LEVEL_ROI, Perception,
    native_deployed_count, native_player_hud, valid_population_counts)


ROOT = Path(__file__).resolve().parents[1]
EARLY = ROOT / 'debug/runner-01a10ddf-e6f5f6c47294/4903c1373f2345f99c2e571d33a6f033-strategy.jpg'
LATER = ROOT / 'debug/runner-01a10ddf-e6f5f6c47294/27c5e5256ec9426b8db3dca8e7bd93bf-strategy.jpg'
POPUP = ROOT / 'debug/chat-01a102c9-match-213628-c133e542a500/d012-after-name.jpg'
PUBLIC_FIXTURES = ROOT / 'handoff/2026-10-07/fixtures'


class NativePlayerHUDTests(unittest.TestCase):
    def row(self, text, box, confidence=.99):
        return {'text': text, 'raw_text': text, 'box': box, 'confidence': confidence, 'normalization_basis': None}

    def anchors(self):
        return [self.row('购买经验', [248, 848, 350, 879]), self.row('0/84', [267, 933, 328, 964])]

    def read(self, rows, result=None, page='shop'):
        engine=Mock(return_value=(result, None))
        before=copy.deepcopy(rows)
        value=native_player_hud(rows, Image.new('RGB', (1920, 1080)), page, engine, 'actual-frame')
        self.assertEqual(rows,before)
        return value,engine

    def test_player_subject_uses_full_native_roi_and_preserves_raw_card_level(self):
        rows=self.anchors()+[self.row('银狼LV.999', [648, 291, 784, 321]),
            self.row('Lv.', [262, 904, 309, 932])]
        value,engine=self.read(rows,[['Lv.9',.93]])
        self.assertEqual(value['level'],9)
        self.assertEqual(value['xp'],[0,84])
        self.assertEqual(value['snapshot_id'],'actual-frame')
        self.assertEqual(value['evidence']['level']['confidence'],.93)
        self.assertEqual(value['evidence']['level']['bounds'],PLAYER_LEVEL_ROI)
        self.assertEqual(engine.call_count,1)
        self.assertEqual(engine.call_args.kwargs,{'use_det':False,'use_cls':False})
        self.assertEqual(engine.call_args.args[0].shape[:2],(56,118))

    def test_trusted_player_row_needs_no_crop_and_weak_row_can_be_reread(self):
        row=self.row('Lv.8',[252,878,339,942])
        value,engine=self.read(self.anchors()+[row])
        self.assertEqual(value['level'],8);engine.assert_not_called()
        value,engine=self.read(self.anchors()+[{**row,'confidence':.86}],[['Lv.8',.98]])
        self.assertEqual(value['level'],8)
        self.assertEqual(value['evidence']['level']['source'],'complete_player_level_roi')
        value,unused=self.read(self.anchors()+[{**row,'confidence':.86}],[['Lv.9',.98]])
        self.assertIsNone(value['level'])

    def test_missing_moved_conflicting_or_low_confidence_evidence_stays_unknown(self):
        row=self.row('Lv.9',[261,902,322,933])
        for rows in ([], [row], [*self.anchors(),row,row],
                     [*self.anchors(),row,self.row('Lv.8',[261,902,322,933])],
                     [*self.anchors(),self.row('Lv.999',[261,902,322,933])],
                     [*self.anchors(),self.row('Lv.009',[261,902,322,933])]):
            with self.subTest(rows=rows):
                value,engine=self.read(rows)
                self.assertIsNone(value['level']);engine.assert_not_called()
        for result in (None,[['Lv.9',.89]],[['99',.99]],[['Lv.999',.99]]):
            value,unused=self.read(self.anchors()+[self.row('Lv.9',[927,496,1293,534])],result)
            self.assertIsNone(value['level'])
        value,engine=self.read(self.anchors()+[row],[['Lv.9',.99]],page='unknown')
        self.assertIsNone(value['level']);engine.assert_not_called()

    def test_team_capacity_twelve_is_separate_from_player_level_and_slot_geometry(self):
        self.assertTrue(valid_population_counts(7,12))
        self.assertTrue(valid_population_counts(12,12))
        for counts in ((18,8),(13,12),(1,0),(0,13),(True,12)):
            self.assertFalse(valid_population_counts(*counts))
        population=self.row('7/12',[880,212,1020,277])
        self.assertEqual(native_deployed_count([population]),'7/12')
        value,unused=self.read(self.anchors()+[self.row('Lv.12',[261,902,339,933])])
        self.assertIsNone(value['level'])
        # Existing observed front/back/bench layout stays 4+6+9, not 12 invented board slots.
        self.assertEqual(len([s for s in native_slots() if s['location']=='board']),10)


@unittest.skipUnless((PUBLIC_FIXTURES / 'manifest.json').is_file(), 'public real PNG fixtures unavailable')
class PublicPlayerHUDReplayTests(unittest.TestCase):
    def test_four_manifest_bound_pngs_use_production_perception(self):
        manifest=json.loads((PUBLIC_FIXTURES/'manifest.json').read_text(encoding='utf8'))
        reader=Perception()
        for case in manifest['cases']:
            with self.subTest(case=case['id']):
                path=PUBLIC_FIXTURES/case['file']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),case['export_sha256'])
                observed=reader.read(path)
                self.assertEqual(observed['page'],case['expected']['page'])
                level=observed['fields']['level']
                expected=case['expected']['player_level']
                # Production preserves unknown, but missing a known-readable
                # fixture is a coverage failure, never an all-green fallback.
                self.assertEqual(level,str(expected) if expected is not None else None)
                for forbidden in case['expected'].get('forbidden_levels',[]):
                    self.assertNotEqual(level,str(forbidden))
                hud=observed['semantic']['player_hud']
                self.assertEqual(hud['actor'],'player')
                self.assertEqual(hud['snapshot_id'],case['export_sha256'])
                if level is not None:
                    evidence=hud['evidence']['level']
                    self.assertGreaterEqual(evidence.get('confidence',evidence.get('row',{}).get('confidence',0)),.90)
                if 'deployed' in case['expected']:
                    self.assertEqual(observed['fields']['deployed'],case['expected']['deployed'])


class NativePopulationTests(unittest.TestCase):
    def test_only_unique_high_confidence_central_hud_certifies_population(self):
        row = {'text': 'i4/4', 'raw_text': 'i4/4', 'confidence': .99, 'box': [880, 212, 1005, 277]}
        self.assertEqual(native_deployed_count([row]), '4/4')
        self.assertIsNone(native_deployed_count([{**row, 'confidence': .89}]))
        self.assertIsNone(native_deployed_count([{**row, 'box': [120, 520, 230, 565]}]))
        self.assertIsNone(native_deployed_count([row, row]))
        self.assertIsNone(native_deployed_count([{**row, 'raw_text': '5/4'}]))

    def read_population(self, counts, crop_result, *, anchored=True, state_result=None):
        def ocr_row(text, box, confidence=.99):
            x1, y1, x2, y2 = [value / 1.5 for value in box]
            return [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]], text, confidence]
        rows = [ocr_row('备战阶段', [420, 25, 530, 60]),
                ocr_row('出战', [1770, 720, 1860, 780]),
                ocr_row('商店', [1585, 960, 1660, 1005]),
                ocr_row('3-3', [430, 65, 500, 95]),
                ocr_row('100', [1430, 65, 1485, 95])]
        if not anchored:
            rows = [row for row in rows if row[1] != '商店']
        rows.extend(ocr_row(text, [880, 212, 1020, 277], confidence) for text, confidence in counts)
        reader = Perception()
        reader.engine = Mock(side_effect=[(rows, None), (crop_result, None)])
        if state_result is not None:
            reader.state_reader = Mock(read=Mock(return_value=state_result))
        with tempfile.TemporaryDirectory(prefix='cw-population-test-') as temporary:
            path = Path(temporary) / 'frame.png'
            Image.new('RGB', (1920, 1080), (150, 150, 150)).save(path)
            observation = reader.read(path)
        return observation, reader.engine

    def test_invalid_icon_prefixed_count_uses_existing_digit_crop_without_rewriting_raw(self):
        observed, engine = self.read_population([('18/8', .99)], [('8/8', .96)])
        self.assertEqual(observed['fields']['deployed'], '8/8')
        self.assertEqual(engine.call_count, 2)
        self.assertEqual(engine.call_args.kwargs, {'use_det': False, 'use_cls': False})
        self.assertEqual(engine.call_args.args[0].shape[:2], (70, 139))
        raw = next(row for row in observed['rows'] if row['raw_text'] == '18/8')
        self.assertEqual((raw['text'], raw['confidence'], raw['normalization_basis']), ('18/8', .99, None))
        derived = next(row for row in observed['rows']
                       if row.get('normalization_basis') == 'fixed_native_deployed_count_roi')
        self.assertEqual((derived['raw_text'], derived['confidence'], derived['box']), ('8/8', .96, DEPLOYED_COUNT_ROI))
        self.assertFalse(observed['semantic']['team']['checked'])

    def test_valid_count_needs_no_additional_ocr(self):
        observed, engine = self.read_population([('8/8', .99)], [('8/8', .99)])
        self.assertEqual(observed['fields']['deployed'], '8/8')
        self.assertEqual(engine.call_count, 1)

    def test_capacity_twelve_is_read_without_certifying_an_unseen_expanded_layout(self):
        state = {'team': {'fully_read': True,
            'units': [{'location': 'board', 'position': '前台'} for unused in range(7)]},
            'inventory': {'items': []}}
        for capacity, expected_checked in ((10, True), (12, False)):
            with self.subTest(capacity=capacity):
                observed, engine = self.read_population([(f'7/{capacity}', .99)], None, state_result=state)
                self.assertEqual(observed['fields']['deployed'], f'7/{capacity}')
                self.assertEqual(observed['semantic']['team']['checked'], expected_checked)
                self.assertEqual(observed['semantic']['team']['count_reconciliation']['supported_board_slots'], 10)
                self.assertEqual(engine.call_count, 1)

    def test_invalid_low_confidence_or_missing_digit_read_stays_unknown(self):
        for crop in ([('18/8', .99)], [('i8/8', .99)], [('8/8', .89)], None):
            with self.subTest(crop=crop):
                observed, engine = self.read_population([('18/8', .99)], crop)
                self.assertIsNone(observed['fields']['deployed'])
                self.assertEqual(engine.call_count, 2)

    def test_population_conflicts_duplicates_and_missing_layout_remain_unknown(self):
        for counts in ([('18/8', .99), ('7/8', .89)], [('8/8', .99), ('8/8', .99)]):
            with self.subTest(counts=counts):
                observed, unused = self.read_population(counts, [('8/8', .99)])
                self.assertIsNone(observed['fields']['deployed'])
        observed, engine = self.read_population([('18/8', .99)], [('8/8', .99)], anchored=False)
        self.assertIsNone(observed['fields']['deployed'])
        self.assertEqual(engine.call_count, 1)

    def test_only_fixed_crop_may_resolve_an_impossible_original_count(self):
        invalid = {'text': '18/8', 'raw_text': '18/8', 'confidence': .99, 'box': [880, 212, 1020, 277]}
        fresh = {'text': '8/8', 'raw_text': '8/8', 'confidence': .99, 'box': list(DEPLOYED_COUNT_ROI),
                 'normalization_basis': 'fixed_native_deployed_count_roi'}
        self.assertEqual(native_deployed_count([invalid, fresh]), '8/8')
        self.assertIsNone(native_deployed_count([invalid, {**fresh, 'normalization_basis': None}]))
        self.assertIsNone(native_deployed_count([invalid, {**fresh, 'box': [900, 220, 1020, 270]}]))
        self.assertIsNone(native_deployed_count([{**invalid, 'raw_text': '7/8'}, fresh]))


class NativeCapacityTests(unittest.TestCase):
    def test_bench_capacity_does_not_certify_unknown_overflow_or_population(self):
        slots = [{**slot, 'snapshot_id': 'fresh', 'status': 'occupied'} for slot in native_slots()]
        capacity = native_capacity(slots, 'fresh')
        self.assertTrue(capacity['bench_checked'])
        self.assertEqual(capacity['occupied'], 9)
        self.assertEqual(capacity['free_slots'], 0)
        self.assertFalse(capacity['overflow_checked'])
        self.assertIsNone(capacity['overflow_count'])
        slots[0]['status'] = 'unknown'  # Board ambiguity is independent of bench count.
        self.assertTrue(native_capacity(slots, 'fresh')['bench_checked'])

    def test_partial_stale_or_duplicate_bench_stays_unknown(self):
        slots = [{**slot, 'snapshot_id': 'fresh', 'status': 'empty'} for slot in native_slots() if slot['location'] == 'bench']
        for bad in (slots[:-1], slots + [slots[0]],
                    [{**slots[0], 'status': 'unknown'}, *slots[1:]],
                    [{**slots[0], 'snapshot_id': 'old'}, *slots[1:]]):
            with self.subTest(slots=bad):
                value = native_capacity(bad, 'fresh')
                self.assertFalse(value['bench_checked'])
                self.assertIsNone(value['free_slots'])


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
