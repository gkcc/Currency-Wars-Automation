"""Retained native ROI calibration and separately declared pixel negatives."""
import unittest

import replay_currency_wars_badges as replay


class RetainedBadgeTests(unittest.TestCase):
    def test_retained_native_rois_with_identical_pr16_resources(self):
        report = replay.replay()
        self.assertEqual(report['issues'], [])
        self.assertFalse(report['source_changed'])
        self.assertFalse(report['public_input_changed'])
        self.assertFalse(report['badge_resource_changed'])
        self.assertEqual(report['scope']['ocr_calls'], 0)
        self.assertEqual(report['scope']['full_shop_reads'], 0)
        self.assertTrue(report['calibration']['exact_pixels'])
        self.assertFalse(report['calibration']['independent_positive'])
        expected = [[False] * 5, [False] * 4 + [True], [False] * 5]
        for frame, values in zip(report['retained_frames'], expected):
            self.assertEqual(len(frame['slots']), 5)
            self.assertEqual(frame['geometry']['overlays'], [])
            for slot, value in zip(frame['slots'], values):
                self.assertIs(slot['native_recommended'], value)
                self.assertIs(slot['baseline']['native_recommended'], None if value else False)
                evidence = slot['native_evidence']
                self.assertEqual(evidence['snapshot_id'], frame['png_sha256'])
                self.assertEqual(evidence['card_bounds'], slot['bounds'])
                self.assertEqual(evidence['template_source'], report['resource_context']['badge_source'])
        positive = report['retained_frames'][1]['slots'][4]
        self.assertEqual(positive['coverage'], 'source_calibration')
        self.assertEqual(positive['native_evidence']['variant'], 'orange_gold')
        self.assertEqual(positive['baseline']['native_evidence']['yellow_fraction'], 0)

    def test_declared_negatives_isolate_shape_color_and_envelope(self):
        frames = replay.source_frames()
        replay.verify_public_calibration(frames)
        reader = replay.load_reader(replay.candidate, replay.CALIBRATION)
        unused_item, rgb = frames[1]
        rectangles, unused_geometry = reader._rectangles(rgb)
        evidence = {}
        for name, generated, snapshot in replay.synthetic_frames(rgb, rectangles[4]):
            with self.subTest(perturbation=name):
                value, proof = reader._recommended(generated, rectangles[4], snapshot_id=snapshot)
                self.assertIsNot(value, True)
                evidence[name] = proof
        local_gray = evidence['gray_book_with_original_orange_surround']
        self.assertGreaterEqual(local_gray['match_score'], .97)
        self.assertGreaterEqual(local_gray['whole_template_score'], .80)
        self.assertTrue(local_gray['whole_aligned'])
        self.assertGreaterEqual(local_gray['color_fractions']['orange_gold'], .06)
        self.assertEqual(local_gray['matched_color_fractions']['orange_gold'], 0)
        self.assertIsNone(local_gray['result'])
        interior = evidence['book_interior_without_original_envelope']
        self.assertGreaterEqual(interior['match_score'], .97)
        self.assertGreaterEqual(interior['matched_color_fractions']['orange_gold'], .06)
        self.assertLess(interior['whole_template_score'], .80)
        occluded = evidence['center_4x4_occlusion']
        self.assertGreaterEqual(occluded['match_score'], .90)
        self.assertLess(occluded['match_score'], .97)
        self.assertEqual(reader.engine.calls, 0)


if __name__ == '__main__':
    unittest.main()
