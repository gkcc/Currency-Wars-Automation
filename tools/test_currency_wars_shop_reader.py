"""Badge recognition regressions; synthetic art, no capture or game input."""
import unittest

import cv2
import numpy as np

from currency_wars_shop_reader import ShopReader


class BadgeRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.reader = ShopReader()
        # A gold book with a star, inside a dark circle. No private game art.
        self.badge = np.full((51, 47, 3), 210, dtype=np.uint8)
        cv2.circle(self.badge, (23, 25), 21, (65, 65, 65), -1)
        gold = (245, 205, 30)
        cv2.circle(self.badge, (23, 25), 21, gold, 2)
        cv2.polylines(self.badge, [np.array([(12, 20), (23, 25), (34, 20),
                                            (34, 37), (23, 43), (12, 37), (12, 20)])], True, gold, 2)
        cv2.line(self.badge, (23, 25), (23, 43), gold, 2)
        cv2.fillPoly(self.badge, [np.array([(23, 9), (25, 13), (30, 14), (26, 17),
                                           (27, 21), (23, 19), (19, 21), (20, 17),
                                           (16, 14), (21, 13)])], gold)
        self.reader.templates['recommend_badge.png'] = cv2.cvtColor(self.badge, cv2.COLOR_RGB2GRAY)

    def frame(self, background=110):
        return np.full((320, 260, 3), background, dtype=np.uint8)

    def read_badge(self, frame):
        return self.reader._recommended(frame, (0, 0, 244, 273))[0]

    def test_book_survives_different_portrait_pixels_outside_circle(self):
        for background in (30, 110, 220):
            with self.subTest(background=background):
                frame = self.frame(background)
                mask = np.zeros((51, 47), dtype=np.uint8)
                cv2.circle(mask, (23, 25), 22, 1, -1)
                region = frame[9:60, 11:58]
                region[mask.astype(bool)] = self.badge[mask.astype(bool)]
                self.assertIs(self.read_badge(frame), True)

    def test_gold_portrait_without_book_is_not_recommended(self):
        frame = self.frame()
        # Gold hair/bright area below the possible badge position.
        frame[58:80, 50:82] = (245, 205, 30)
        self.assertIsNot(self.read_badge(frame), True)
        self.assertIs(self.read_badge(self.frame()), False)

    def test_identical_book_elsewhere_in_card_is_not_recommended(self):
        frame = self.frame()
        frame[90:141, 90:137] = self.badge
        self.assertIs(self.read_badge(frame), False)

    def test_unobservable_or_missing_badge_evidence_stays_unknown(self):
        for background in (0, 255):
            self.assertIsNone(self.read_badge(self.frame(background)))
        self.reader.templates.clear()
        self.assertIsNone(self.read_badge(self.frame()))

    def test_unsupported_template_geometry_stays_unknown(self):
        self.reader.templates['recommend_badge.png'] = np.zeros((30, 30), dtype=np.uint8)
        self.assertIsNone(self.read_badge(self.frame()))


if __name__ == '__main__':
    unittest.main()
