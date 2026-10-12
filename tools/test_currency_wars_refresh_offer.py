"""Real retained PNG reads, plus explicit negative evidence/occlusion fixtures."""
import copy
import hashlib
import io
from unittest import TestCase
from unittest.mock import Mock

from PIL import Image, ImageDraw

import currency_wars_refresh_offer as refresh
from replay_currency_wars_refresh_offer import FIXTURES, read_true_frames


class RefreshOfferReadTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = read_true_frames()

    def image(self, index):
        with Image.open(FIXTURES / self.records[index]['file']) as opened:
            return opened.convert('RGB')

    def test_three_retained_pngs_keep_native_rows_and_unknown_player_fields(self):
        self.assertEqual([row['consumed_offer'] for row in self.records], [
            {'mode': 'free', 'free_remaining': 2, 'paid_cost': None},
            {'mode': 'free', 'free_remaining': 1, 'paid_cost': None},
            {'mode': 'paid', 'free_remaining': None, 'paid_cost': 2}])
        self.assertEqual([row['offer']['numeric_ocr_calls'] for row in self.records], [0, 1, 1])
        for row in self.records:
            self.assertTrue(row['matches_independent_labels'])
            self.assertTrue(row['original_widget_rows_match_root_baseline'])
            self.assertTrue(row['original_player_fields_match_root_baseline'])
            self.assertEqual(row['team_status'], 'not_read')
            self.assertFalse(row['team_checked'])
            self.assertEqual(row['read_contract']['effective_scope'], 'economy')
        self.assertFalse(any(row['raw_text'] == '1' for row in self.records[1]['offer']['raw_rows']))
        self.assertIn('?2', [row['raw_text'] for row in self.records[2]['offer']['raw_rows']])

    def test_missing_label_conflicts_and_digit_overflow_do_not_trigger_more_ocr(self):
        original = self.records[0]
        for rows in ([], [row for row in original['native_rows'] if row['raw_text'] != '免费刷新'],
                     original['native_rows'] + [{'text': '3', 'raw_text': '3', 'confidence': .99,
                                                 'box': [1610, 522, 1626, 548]}]):
            engine = Mock(side_effect=AssertionError('No OCR for ambiguous or absent control'))
            offer = refresh.read_offer(rows, self.image(0), 'shop', engine, original['png_sha256'])
            self.assertEqual(offer['status'], 'unknown')
            self.assertIsNone(refresh.consume_offer(offer, self.image(0), original['png_sha256'], 'shop'))
            engine.assert_not_called()
        for index, bounds in ((0, [1640, 523, 1643, 540]), (2, [1667, 522, 1670, 540])):
            # Declared extra dark strokes outside the numeric crop, not a
            # retained multi-digit game price or evidence of OCR accuracy.
            original = self.records[index]
            image = self.image(index)
            image.paste((0, 0, 0), bounds)
            payload = io.BytesIO()
            image.save(payload, format='PNG')
            digest = hashlib.sha256(payload.getvalue()).hexdigest()
            engine = Mock(side_effect=AssertionError('No OCR of a truncated numeric field'))
            offer = refresh.read_offer(original['native_rows'], image, 'shop', engine, digest)
            self.assertEqual(offer['status'], 'unknown')
            self.assertIn('numeric_ink_missing_or_outside_supported_crop', offer['reasons'])
            engine.assert_not_called()

    def test_low_confidence_question_mark_zero_and_failed_crop_remain_unknown(self):
        original = self.records[1]
        for result in ([['1', .89]], [['?1', .999]], [['0', .999]], [], [['1', .99], ['2', .99]]):
            engine = Mock(return_value=(result, None))
            offer = refresh.read_offer(original['native_rows'], self.image(1), 'shop', engine, original['png_sha256'])
            self.assertEqual(offer['status'], 'unknown')
            self.assertEqual(offer['numeric_ocr_calls'], 1)
            self.assertIsNone(offer['free_remaining'])
            self.assertEqual(offer['evidence']['number']['raw_result'], [
                {'raw_text': text, 'confidence': confidence} for text, confidence in result])
        failed = refresh.read_offer(original['native_rows'], self.image(1), 'shop',
            Mock(side_effect=ValueError('declared OCR failure')), original['png_sha256'])
        self.assertEqual(failed['status'], 'unknown')
        self.assertEqual(failed['evidence']['number']['error'], 'declared OCR failure')

    def test_three_declared_coin_occlusions_are_refused_without_numeric_ocr(self):
        original = self.records[2]
        # Pixel perturbations only; these are not additional retained game images.
        for bounds, color in (([1592, 517, 1625, 549], (244, 245, 255)),
                              ([1604, 528, 1612, 536], (244, 245, 255)),
                              ([1592, 517, 1609, 549], (25, 25, 25))):
            image = self.image(2)
            image.paste(color, bounds)
            payload = io.BytesIO()
            image.save(payload, format='PNG')
            digest = hashlib.sha256(payload.getvalue()).hexdigest()
            engine = Mock(side_effect=AssertionError('No number read without the coin'))
            offer = refresh.read_offer(original['native_rows'], image, 'shop', engine, digest)
            self.assertEqual(offer['status'], 'unknown')
            self.assertLess(offer['evidence']['currency_icon']['score'], .97)
            engine.assert_not_called()

    def test_source_frame_mode_and_original_number_proof_mismatch_are_refused(self):
        original = self.records[2]
        for update in ({'snapshot_id': 'older'}, {'widget_rgb_sha256': 'older'},
                       {'widget_bounds': [1536, 430, 1700, 595]}, {'page': 'preparation'},
                       {'mode': 'free', 'free_remaining': 2, 'paid_cost': None},
                       {'paid_cost': 3}, {'free_remaining': 0}, {'version': True}):
            offer = copy.deepcopy(original['offer'])
            offer.update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                refresh.consume_offer(offer, self.image(2), original['png_sha256'], 'shop')
        offer = copy.deepcopy(original['offer'])
        offer['paid_cost'] = 3
        offer['evidence']['number']['raw_text'] = '3'
        with self.assertRaises(ValueError):
            refresh.consume_offer(offer, self.image(2), original['png_sha256'], 'shop')
        offer = copy.deepcopy(original['offer'])
        offer['evidence']['number_extent']['rgb_sha256'] = 'older'
        with self.assertRaises(ValueError):
            refresh.consume_offer(offer, self.image(2), original['png_sha256'], 'shop')
        image = self.image(2)
        ImageDraw.Draw(image).point((1640, 540), fill=tuple(255 - value for value in image.getpixel((1640, 540))))
        with self.assertRaises(ValueError):
            refresh.consume_offer(original['offer'], image, original['png_sha256'], 'shop')
