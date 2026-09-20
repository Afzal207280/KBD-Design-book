"""Acceptance §238: validators must produce TRUE POSITIVES and TRUE NEGATIVES.

Every validator is exercised against REAL generated artifacts, then sabotaged;
a validator that cannot catch its own sabotage is not trusted.
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookfactory.classify import BOOK_TYPES
from bookfactory.content import generate_content
from bookfactory.geometry import compute_geometry
from bookfactory.layout import layout_content
from bookfactory.ruleset import Ruleset
from bookfactory.schemas import BookConfig
from bookfactory.typography import DEFAULT_TOKENS
from bookfactory.validators import (validate_pagination, validate_puzzles,
                                    validate_fonts, validate_writing_space)

RS = Ruleset()


def make_layout(book_type="puzzle_book", topic="forest puzzles", pages=48):
    prof = BOOK_TYPES[book_type]
    cfg = BookConfig.from_dict({
        "topic": topic, "book_type": book_type, "category": prof["category"],
        "trim_w": prof["default_trim"][0], "trim_h": prof["default_trim"][1],
        "color_mode": prof["default_color"], "illustration_mode": prof["illustration"],
        "bleed": prof["bleed"], "target_pages": pages, "title": "T", "seed": 777})
    content = generate_content(cfg, prof, [])
    geom = compute_geometry(RS, cfg.trim_w, cfg.trim_h, cfg.target_pages, cfg.bleed)
    tokens = DEFAULT_TOKENS.scale_for_audience(cfg.audience.age_max)
    return cfg, content, geom, tokens, layout_content(cfg, geom, content, tokens)


class TestPaginationValidator(unittest.TestCase):
    def test_true_negative_clean_layout(self):
        cfg, content, geom, tokens, layout = make_layout()
        defects, ev = validate_pagination(layout, geom, cfg)
        self.assertEqual([d for d in defects if d.severity.value == "BLOCKING"], [],
                         [str(d) for d in defects])

    def test_true_positive_nonsequential_pages(self):
        cfg, content, geom, tokens, layout = make_layout()
        layout.pages[3].meta.index = 99  # sabotage
        defects, _ = validate_pagination(layout, geom, cfg)
        self.assertTrue(any(d.root_cause == "non-sequential-pages" for d in defects))

    def test_true_positive_duplicate_chapter_start(self):
        cfg, content, geom, tokens, layout = make_layout("poetry_collection",
                                                         "quiet mornings", 40)
        # find a heading op and clone it on a later page
        heading = None
        for p in layout.pages:
            for op in p.ops:
                if op.get("heading") == 1:
                    heading = dict(op)
                    break
            if heading:
                break
        self.assertIsNotNone(heading)
        layout.pages[-1].ops.append(heading)
        defects, _ = validate_pagination(layout, geom, cfg)
        self.assertTrue(any(d.root_cause == "duplicate-chapter-start" for d in defects))

    def test_true_positive_too_short(self):
        cfg, content, geom, tokens, layout = make_layout()
        layout.pages = layout.pages[:4]
        defects, _ = validate_pagination(layout, geom, cfg)
        self.assertTrue(any(d.root_cause == "book-too-short" for d in defects))


class TestPuzzleValidator(unittest.TestCase):
    def test_true_negative(self):
        cfg, content, geom, tokens, layout = make_layout()
        defects, ev = validate_puzzles(content)
        self.assertEqual(defects, [])
        self.assertGreaterEqual(ev.get("verified", 0), 30)

    def test_true_positive_corrupted_solution(self):
        cfg, content, geom, tokens, layout = make_layout()
        puz = next(p for p in content["puzzles"] if p["kind"] == "sudoku")
        puz["data"]["solution"][0][0] = (puz["data"]["solution"][0][0] % 9) + 1
        defects, _ = validate_puzzles(content)
        self.assertTrue(any(d.root_cause == "sudoku-invalid" for d in defects),
                        [d.root_cause for d in defects])

    def test_true_positive_missing_answer_key(self):
        cfg, content, geom, tokens, layout = make_layout()
        content["answer_key"] = content["answer_key"][:-1]
        defects, _ = validate_puzzles(content)
        self.assertTrue(any("answer" in d.root_cause for d in defects),
                        [d.root_cause for d in defects])


class TestFontValidator(unittest.TestCase):
    def test_true_negative(self):
        cfg, content, geom, tokens, layout = make_layout("mystery_novel",
                                                         "a quiet mystery", 96)
        defects, _ = validate_fonts(content, cfg)
        self.assertEqual([d for d in defects if d.severity.value != "INFO"], [])

    def test_true_positive_unsupported_glyph(self):
        cfg, content, geom, tokens, layout = make_layout("mystery_novel",
                                                         "a quiet mystery", 96)
        for s in content["sections"]:
            for b in s["blocks"]:
                if b.get("t") == "p":
                    b["text"] += " \u4e2d\u6587"  # CJK not in WinAnsi
                    break
            break
        defects, _ = validate_fonts(content, cfg)
        self.assertTrue(defects, "glyph outside WinAnsi must be flagged")


class TestWritingSpaceValidator(unittest.TestCase):
    def test_planner_has_usable_space(self):
        cfg, content, geom, tokens, layout = make_layout("daily_planner",
                                                         "daily focus", 60)
        defects, ev = validate_writing_space(content, cfg.book_type)
        self.assertEqual([d for d in defects if d.severity.value == "BLOCKING"], [])


if __name__ == "__main__":
    unittest.main()
