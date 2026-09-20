"""Acceptance tests §236: geometry engines + ruleset facts.

Every number asserted here comes from the official KDP help article
(GVBQ3CMEQW3W2VL6, fetched 2026-09-20) encoded in data/kdp_ruleset.json,
or from the third-party spine/cover formulas recorded there.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookfactory.ruleset import Ruleset, RulesetError
from bookfactory.geometry import (compute_geometry, verify_independent,
                                  spine_independent, cover_independent)

RS = Ruleset()


class TestPageLimits(unittest.TestCase):
    def test_official_bw_6x9_range(self):
        lo, hi = RS.page_count_limits(6.0, 9.0, "BLACK_AND_WHITE", "white")
        self.assertEqual((lo, hi), (24, 828))

    def test_official_cream_max(self):
        lo, hi = RS.page_count_limits(6.0, 9.0, "BLACK_AND_WHITE", "cream")
        self.assertEqual(hi, 776)

    def test_official_8x8_exceptions(self):
        for color, paper, mx in (("BLACK_AND_WHITE", "white", 590),
                                 ("BLACK_AND_WHITE", "cream", 550),
                                 ("FULL_COLOR", "white", 590)):
            self.assertEqual(RS.page_count_limits(8.5, 8.5, color, paper)[1], mx)

    def test_a4_no_standard_color(self):
        with self.assertRaises(RulesetError):
            RS.page_count_limits(8.27, 11.69, "STANDARD_COLOR", "white")

    def test_hardcover_range(self):
        lo, hi = RS.page_count_limits(6.0, 9.0, "BLACK_AND_WHITE", "white",
                                      binding="hardcover")
        self.assertEqual((lo, hi), (75, 550))


class TestSpine(unittest.TestCase):
    def test_white_paper_multiplier(self):
        # 200pp * 0.002252 + 0.06" wrap allowance = 0.5104"
        s = RS.spine_width_in(200, "BLACK_AND_WHITE", "white", "paperback")
        self.assertAlmostEqual(s, 0.4504 + 0.06, places=4)

    def test_cream_multiplier(self):
        s = RS.spine_width_in(200, "BLACK_AND_WHITE", "cream", "paperback")
        self.assertAlmostEqual(s, 0.5 + 0.06, places=4)  # 200*0.0025 + wrap

    def test_groundwood_refused(self):
        with self.assertRaises(RulesetError):
            RS.spine_width_in(200, "BLACK_AND_WHITE", "groundwood", "paperback")

    def test_spine_independent_agrees(self):
        for n, paper in ((100, "white"), (300, "cream"), (828, "white")):
            a = RS.spine_width_in(n, "BLACK_AND_WHITE", paper, "paperback")
            b = spine_independent(RS, n, "BLACK_AND_WHITE", paper, "paperback")
            self.assertAlmostEqual(a, b, places=6)


class TestGeometry(unittest.TestCase):
    def test_bleed_adds_eighth_inch(self):
        g_no = compute_geometry(RS, 6.0, 9.0, 200, bleed=False)
        g_yes = compute_geometry(RS, 6.0, 9.0, 200, bleed=True)
        self.assertAlmostEqual(g_yes.page_w_in - g_no.page_w_in, 0.125, places=6)
        self.assertAlmostEqual(g_yes.page_h_in - g_no.page_h_in, 0.25, places=6)

    def test_gutter_band_boundaries(self):
        # official bands: 24-150 -> 0.375; 151-300 -> 0.5; 301-500 -> 0.625
        g150 = compute_geometry(RS, 6.0, 9.0, 150, bleed=False)
        g151 = compute_geometry(RS, 6.0, 9.0, 151, bleed=False)
        g301 = compute_geometry(RS, 6.0, 9.0, 301, bleed=False)
        self.assertAlmostEqual(g150.gutter_in, 0.375, places=6)
        self.assertAlmostEqual(g151.gutter_in, 0.5, places=6)
        self.assertAlmostEqual(g301.gutter_in, 0.625, places=6)

    def test_independent_engine_agrees(self):
        for pages in (24, 150, 300, 828):
            g = compute_geometry(RS, 6.0, 9.0, pages, bleed=True)
            problems = verify_independent(g, RS, bleed=True)
            self.assertEqual(problems, [], f"pages={pages}: {problems}")

    def test_cover_width_independent(self):
        pages = 240
        spine = RS.spine_width_in(pages, "BLACK_AND_WHITE", "white", "paperback")
        w_in, h_in = cover_independent(RS, 6.0, 9.0, spine)
        # KDP paperback cover: bleed + back + spine + front + bleed
        self.assertAlmostEqual(w_in, 2 * RS.bleed_in + 6.0 + spine + 6.0, places=5)
        self.assertAlmostEqual(h_in, 9.0 + 2 * RS.bleed_in, places=5)


if __name__ == "__main__":
    unittest.main()
