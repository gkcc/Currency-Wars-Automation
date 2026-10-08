"""Unchanged native HUD pixels; generated consumer contexts are separate.

The source files are two current context crops, not full original frames.
Their digit RGB is identical, so this is one glyph calibration at two times,
not independent recognition of two amounts or a post-pickup verification.
NativeOcrTests needs the already-installed RapidOCR and its local models.
No model download, new capture, controller, or game input occurs here.
"""
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

import currency_wars_perception as perception


def load_native_coin_crops():
    directory = Path(os.environ.get('CW_NATIVE_HUD_EVIDENCE_DIR',
        Path(__file__).resolve().parents[1] / 'handoff/2026-10-08/ROOT_PR27_HUD'))
    report = json.loads((directory / 'diagnosis.json').read_text(encoding='utf-8-sig'))
    samples = []
    for item in report['source_map']:
        if not item['file'].endswith('-coins.png'):
            continue
        path = directory / Path(item['file']).name
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['crop_sha256']:
            raise ValueError('Native context crop bytes changed')
        bounds = item['source_bounds']
        if item['original_size'] != [1920, 1080] or bounds != [1520, 805, 1720, 1020]:
            raise ValueError('Native HUD source layout changed')
        with Image.open(path) as opened:
            if opened.mode != 'RGB' or opened.size != (200, 215):
                raise ValueError('Native context crop dimensions changed')
            context = opened.copy()
        digit_bounds = perception.GOLD_DIGIT_ROI
        crop = context.crop((digit_bounds[0]-bounds[0], digit_bounds[1]-bounds[1],
                             digit_bounds[2]-bounds[0], digit_bounds[3]-bounds[1]))
        samples.append((item, context, crop))
    if len(samples) != 2:
        raise ValueError('Exactly the two current native contexts are selected')
    return report, samples


def complete_digit_context(source_context):
    bounds = perception.GOLD_DIGIT_CONTEXT
    return source_context.crop((bounds[0]-1520, bounds[1]-805, bounds[2]-1520, bounds[3]-805))


class DeclaredEngine:
    """Inert OCR outputs for protocol checks, never native reading evidence."""
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, image, **kwargs):
        self.calls.append((image.copy(), kwargs))
        if not self.outputs:
            raise AssertionError('Unexpected extra OCR call')
        value = self.outputs.pop(0)
        return (None if value is None else [value]), None


class RewardHudPixelTests(unittest.TestCase):
    metrics = []

    @classmethod
    def setUpClass(cls):
        cls.diagnosis, cls.samples = load_native_coin_crops()

    def test_native_digit_is_complete_and_identical_at_two_times(self):
        self.__class__.metrics = []
        hashes = []
        for source, context, crop in self.samples:
            before = hashlib.sha256(crop.tobytes()).hexdigest()
            prepared, evidence = perception._gold_digit_crop(crop)
            boundary = perception._gold_digit_boundary(complete_digit_context(context), crop)
            self.assertIsNone(boundary['reason'], boundary)
            self.assertEqual(boundary['gold_bounds'], [1585, 897, 1636, 943])
            self.assertEqual(boundary['digit_band_bounds'], perception.GOLD_DIGIT_ROI)
            self.assertIsNone(evidence['reason'], evidence)
            self.assertEqual(evidence['glyph_bounds'], [3, 6, 21, 32])
            self.assertEqual(evidence['tight_bounds'], [1, 4, 23, 34])
            self.assertEqual(evidence['component_count'], 1)
            self.assertEqual(prepared.size, (60, 76))
            self.assertEqual(before, hashlib.sha256(crop.tobytes()).hexdigest())
            hashes.append(before)
            self.__class__.metrics.append({'source': source, 'scope': 'native_digit_pixel_geometry_only',
                'digit_bounds': list(perception.GOLD_DIGIT_ROI), 'geometry': evidence, 'boundary': boundary,
                'prepared_rgb_sha256': hashlib.sha256(prepared.tobytes()).hexdigest()})
        self.assertEqual(hashes[0], hashes[1])

    def test_declared_ocr_keeps_raw_confidence_and_bounded_rejection(self):
        crop = self.samples[0][2]
        context = complete_digit_context(self.samples[0][1])
        cases = (
            ([('4 ', .7972828149795532), ('4', .98)], 4, None),
            ([('4 ', .7972828149795532), ('4', .89)], None, 'digit_unreadable'),
            ([('4 ', .7972828149795532), ('7', .99)], None, 'conflicting_digit_reads'),
            ([('?4', .99), ('?4', .99)], None, 'digit_unreadable'),
            ([None, None], None, 'digit_unreadable'),
        )
        for outputs, expected, reason in cases:
            with self.subTest(outputs=outputs):
                engine = DeclaredEngine(outputs)
                value, evidence = perception.read_gold_hud_digits(crop, engine, context=context)
                self.assertEqual(value['value'] if value else None, expected)
                self.assertEqual(evidence['reason'], reason)
                self.assertEqual(len(engine.calls), 2)
                self.assertEqual([list(call[0].shape[:2]) for call in engine.calls], [[80, 98], [76, 60]])
                self.assertEqual(evidence['attempts'][0]['raw_text'], outputs[0][0] if outputs[0] else None)
                self.assertTrue(all(call[1] == {'use_det': False, 'use_cls': False} for call in engine.calls))
        engine = DeclaredEngine([('4 ', .99)])
        value, evidence = perception.read_gold_hud_digits(crop, engine, context=context)
        self.assertEqual(value['raw_text'], '4 ')
        self.assertEqual(value['method'], 'fixed_hud_digit_ocr')
        self.assertEqual(len(engine.calls), 1)

    def test_generated_blank_clipped_and_extra_mark_do_not_trigger_ocr(self):
        original = self.samples[0][2]
        blank = Image.new('RGB', (49, 40), (235, 237, 246))
        clipped = blank.copy()
        clipped.paste(original, (-5, 0))
        mark = original.copy()
        mark.paste((0, 0, 0), (36, 20, 39, 23))
        for crop in (blank, clipped, mark, Image.new('RGB', (49, 40)), original.crop((0, 0, 48, 40))):
            context = complete_digit_context(self.samples[0][1])
            context.paste(crop, (80, 15))
            engine = DeclaredEngine([('4', .999)])
            value, evidence = perception.read_gold_hud_digits(crop, engine, context=context)
            self.assertIsNone(value)
            self.assertTrue(evidence['reason'])
            self.assertEqual(engine.calls, [])
        # Generated two-character reflow: coin shifts left, an additional 1 is
        # outside the old ROI, and the original 4 remains intact inside it.
        # A complete-number guard must reject before any mock OCR can say 4.
        context = complete_digit_context(self.samples[0][1])
        icon = context.crop((24, 8, 81, 60))
        context.paste((235, 237, 246), (24, 8, 81, 60))
        context.paste(icon, (12, 8))
        context.paste(original, (80, 15))
        context.paste((0, 0, 0), (73, 22, 77, 48))
        self.assertEqual(context.crop((80, 15, 129, 55)).tobytes(), original.tobytes())
        engine = DeclaredEngine([('4', .999)])
        value, evidence = perception.read_gold_hud_digits(original, engine, context=context)
        self.assertIsNone(value)
        self.assertEqual(evidence['reason'], 'adjacent_digit_outside_fixed_roi')
        self.assertEqual(engine.calls, [])
        engine = DeclaredEngine([('4', .999)])
        value, evidence = perception.read_gold_hud_digits(original, engine)
        self.assertIsNone(value)
        self.assertEqual(evidence['reason'], 'complete_digit_context_missing')
        self.assertEqual(engine.calls, [])

    def test_declared_page_labels_icon_conflict_and_full_row_path(self):
        # A generated coordinate container qualifies protocol calls only. Its
        # declared page/labels are not the missing full native observations.
        source, context, unused_crop = self.samples[0]
        image = Image.new('RGB', (1920, 1080))
        image.paste(context, tuple(source['source_bounds'][:2]))
        rows = [
            {'text': '购买经验', 'raw_text': '购买经验', 'box': [245, 838, 350, 875], 'confidence': .9994},
            {'text': '商店', 'raw_text': '商店', 'box': [1597, 968, 1650, 1000], 'confidence': .9999},
        ]
        cases = [('battle', rows, image), ('preparation', rows[1:], image)]
        weak = copy.deepcopy(rows)
        weak[1]['confidence'] = .89
        cases.append(('preparation', weak, image))
        wrong = copy.deepcopy(rows)
        wrong[1]['box'] = [1500, 968, 1700, 1000]
        cases.append(('preparation', wrong, image))
        no_icon = image.copy()
        no_icon.paste((235, 237, 246), (1572, 891, 1626, 949))
        cases.append(('preparation', rows, no_icon))
        duplicate = copy.deepcopy(rows) + [
            {'text': str(value), 'box': [1640, 905, 1662, 935], 'confidence': .99}
            for value in (4, 7)]
        cases.append(('preparation', duplicate, image))
        for confidence in (.89, .99):
            crossing = copy.deepcopy(rows) + [
                {'text': '14', 'box': [1605, 905, 1662, 935], 'confidence': confidence}]
            cases.append(('preparation', crossing, image))
        for page, current_rows, current_image in cases:
            engine = DeclaredEngine([('4', .999)])
            value, evidence = perception.native_gold_hud(current_rows, current_image, page, engine, 'declared-source')
            self.assertIsNone(value, evidence)
            self.assertEqual(engine.calls, [])
        engine = DeclaredEngine([('4 ', .7972828149795532), ('4', .98)])
        value, evidence = perception.native_gold_hud(rows, image, 'preparation', engine, 'declared-source')
        self.assertEqual(value['value'], 4)
        self.assertEqual(value['snapshot_id'], 'declared-source')
        self.assertEqual(evidence['attempts'][0]['confidence'], .7972828149795532)
        self.assertGreater(value['currency_icon_gold_fraction'], .15)
        self.assertEqual(len(engine.calls), 2)
        trusted = copy.deepcopy(rows) + [
            {'text': '4', 'raw_text': '4', 'box': [1640, 905, 1662, 935], 'confidence': .99}]
        engine = DeclaredEngine([])
        value, evidence = perception.native_gold_hud(trusted, image, 'preparation', engine, 'declared-source')
        self.assertEqual(value['value'], 4)
        self.assertEqual(value['method'], 'native_hud_row')
        self.assertEqual(engine.calls, [])


class RewardHudNativeOcrTests(unittest.TestCase):
    metrics = []

    def test_two_unchanged_native_digit_crops_use_existing_rapidocr(self):
        # Missing local packages/models is an error, never skip or download.
        distribution = importlib.metadata.distribution('rapidocr-onnxruntime')
        paths = [Path(distribution.locate_file(item)).resolve() for item in distribution.files or ()
                 if str(item).endswith('.onnx')]
        models = {}
        for role in ('det', 'cls', 'rec'):
            found = [path for path in paths if '_' + role + '_' in path.name]
            if len(found) != 1 or not found[0].is_file() or found[0].stat().st_size == 0:
                raise RuntimeError('Already-installed RapidOCR ' + role + ' model is missing or ambiguous')
            models[role + '_model_path'] = str(found[0])
        # Explicit, existing package model paths prevent a constructor from
        # choosing a missing default and trying to acquire it at runtime.
        os.environ['ORT_DISABLE_TELEMETRY'] = '1'
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1, **models)
        unused_diagnosis, samples = load_native_coin_crops()
        self.__class__.metrics = []
        for source, context, crop in samples:
            before = hashlib.sha256(crop.tobytes()).hexdigest()
            value, evidence = perception.read_gold_hud_digits(crop, engine,
                context=complete_digit_context(context))
            self.__class__.metrics.append({'source': source, 'scope': 'native_digit_crop_ocr_only',
                'digit_rgb_sha256': before, 'native_value': value, 'native_read': evidence,
                'expected_value_source': 'supervising_agent_annotation', 'expected_value': 4})
            with self.subTest(source=Path(source['file']).name):
                self.assertIsNotNone(value, evidence)
                self.assertEqual(value['value'], 4)
                self.assertGreaterEqual(value['confidence'], .90)
                self.assertLessEqual(len(evidence['attempts']), 2)
                self.assertEqual(before, hashlib.sha256(crop.tobytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
