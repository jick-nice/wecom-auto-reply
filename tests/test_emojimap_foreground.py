import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "client_v33"))
from app import emojimap2


class EmojiForegroundRegression(unittest.TestCase):
    def test_green_theme_background_is_not_foreground(self):
        bg = np.empty((20, 20, 3), dtype=np.uint8)
        bg[:] = (53, 210, 141)
        mask = emojimap2.foreground(bg)
        self.assertEqual(int(mask.sum()), 0)

    def test_weighted_luminance_does_not_overflow(self):
        bg = np.empty((20, 20, 3), dtype=np.uint8)
        bg[:] = (53, 210, 141)
        bg[8:12, 8:12] = (255, 215, 194)
        mask = emojimap2.foreground(bg)
        self.assertEqual(int(mask[:8].sum()), 0)
        self.assertGreater(int(mask[8:12, 8:12].sum()), 0)

    def test_four_reference_glyphs_are_distinct_and_mapped(self):
        image = np.asarray(Image.open(ROOT / "samples" / "gestures_green.png").convert("RGB"))
        boxes = emojimap2.find_boxes(image, right_zone=0.0)
        self.assertEqual(len(boxes), 4)
        codes = ("3", "2", "5", "2")
        samples = []
        for code, (x1, y1, x2, y2) in zip(codes, boxes):
            samples.append((code, f"fixture-{len(samples)}", np.ascontiguousarray(image[y1:y2, x1:x2])))
        table = emojimap2.build_table(samples)
        self.assertEqual(len(table), 4)
        old = emojimap2._FEAT_CACHE
        try:
            emojimap2._FEAT_CACHE = table
            self.assertEqual([emojimap2.classify(patch)[0] for _, _, patch in samples], list(codes))
        finally:
            emojimap2._FEAT_CACHE = old

    def test_gray_digits_and_circled_numbers_are_not_emoji(self):
        gray = np.full((24, 24, 3), 245, dtype=np.uint8)
        gray[5:19, 10:14] = 30
        self.assertIsNone(emojimap2.classify(gray)[0])


if __name__ == "__main__":
    unittest.main()
