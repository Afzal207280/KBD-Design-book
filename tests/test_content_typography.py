"""Acceptance §236: content engine guarantees + typography engine."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookfactory.schemas import BookConfig
from bookfactory.classify import BOOK_TYPES
from bookfactory.content import generate_content
from bookfactory.typography import METRICS, wrap_text


def cfg_for(book_type, topic="quiet practice", **over):
    prof = BOOK_TYPES[book_type]
    d = {"topic": topic, "book_type": book_type, "category": prof["category"],
         "trim_w": prof["default_trim"][0], "trim_h": prof["default_trim"][1],
         "color_mode": prof["default_color"],
         "illustration_mode": prof["illustration"],
         "bleed": prof["bleed"], "target_pages": prof["target_pages"],
         "title": "T", "seed": 1234}
    d.update(over)
    return BookConfig.from_dict(d)


class TestClassificationChangesGeneration(unittest.TestCase):
    def test_novel_never_gets_coloring_blocks(self):
        c = generate_content(cfg_for("mystery_novel"), BOOK_TYPES["mystery_novel"], [])
        kinds = {b.get("t") for s in c["sections"] for b in s["blocks"]}
        self.assertNotIn("coloring", kinds)
        self.assertIn("p", kinds)

    def test_coloring_book_gets_line_art_pages_only(self):
        c = generate_content(cfg_for("coloring_book"), BOOK_TYPES["coloring_book"], [])
        self.assertGreater(len(c["coloring_pages"]), 10)

    def test_puzzle_book_gets_answer_key_mapping(self):
        c = generate_content(cfg_for("puzzle_book"), BOOK_TYPES["puzzle_book"], [])
        self.assertEqual(len(c["puzzles"]), len(c["answer_key"]))
        self.assertEqual({p["number"] for p in c["puzzles"]},
                         {a["number"] for a in c["answer_key"]})


class TestProseUniqueness(unittest.TestCase):
    def test_no_duplicate_sentences_in_novel(self):
        cfg = cfg_for("mystery_novel", target_pages=160,
                      topic="a mystery about a lighthouse keeper")
        cfg.options["chapters"] = 16
        c = generate_content(cfg, BOOK_TYPES["mystery_novel"], [])
        sents = []
        for s in c["sections"]:
            for b in s["blocks"]:
                if b.get("t") == "p":
                    sents.extend(x.strip() for x in b["text"].split(". ") if x.strip())
        self.assertGreater(len(sents), 300)
        self.assertEqual(len(sents), len(set(sents)),
                         f"{len(sents) - len(set(sents))} duplicated sentences")

    def test_no_duplicate_chapter_titles(self):
        cfg = cfg_for("mystery_novel", target_pages=160)
        cfg.options["chapters"] = 30
        c = generate_content(cfg, BOOK_TYPES["mystery_novel"], [])
        titles = [s["title"] for s in c["sections"] if s.get("title")]
        self.assertEqual(len(titles), len(set(titles)))


class TestDeterministicContent(unittest.TestCase):
    def test_same_seed_same_words(self):
        a = generate_content(cfg_for("gratitude_journal", target_pages=40),
                             BOOK_TYPES["gratitude_journal"], [])
        b = generate_content(cfg_for("gratitude_journal", target_pages=40),
                             BOOK_TYPES["gratitude_journal"], [])
        self.assertEqual(a, b)


class TestTypography(unittest.TestCase):
    def test_winanshi_extras_have_widths(self):
        for ch in "\u2014\u2013\u2019\u201c\u2026":  # em-dash en-dash ’ “ …
            self.assertGreater(METRICS.text_width("Helvetica", 12, ch), 0)

    def test_unsupported_glyph_flagged(self):
        # CJK glyph is outside WinAnsi → char_width -1 sentinel (§35),
        # and text_width must refuse to silently measure it.
        self.assertEqual(METRICS.char_width("Helvetica", 12, "\u4e2d"), -1)
        with self.assertRaises(ValueError):
            METRICS.text_width("Helvetica", 12, "\u4e2d")

    def test_wrap_respects_width(self):
        lines = wrap_text("the quick brown fox jumps over the lazy dog " * 3,
                          "Helvetica", 12, 200)
        for ln in lines:
            self.assertLessEqual(METRICS.text_width("Helvetica", 12, ln), 200.01)


if __name__ == "__main__":
    unittest.main()
