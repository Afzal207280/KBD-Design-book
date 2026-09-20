"""Universal book-type engine + audience + market + positioning + title +
author engines (spec §5, §6, §7, §8, §9, §10, §11).

Classification is deterministic keyword scoring (§202) and it changes REAL
generation behaviour: every type maps to a production profile (layout kind,
page template, color default, illustration mode, trim, engine).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .schemas import Audience, Author

# --------------------------------------------------------------------------
# Book type registry (§5) — each entry: category, keywords, production profile
# --------------------------------------------------------------------------

def _t(category, engine, layout, default_color, illustration, default_trim,
       target_pages, bleed=False, needs_answers=False, min_pages=None):
    return {"category": category, "engine": engine, "layout": layout,
            "default_color": default_color, "illustration": illustration,
            "default_trim": default_trim, "target_pages": target_pages,
            "bleed": bleed, "needs_answers": needs_answers,
            "min_pages": min_pages}


BOOK_TYPES = {
    # LOW CONTENT
    "notebook": _t("low_content", "notebook", "ruled", "BLACK_AND_WHITE", "none", (6, 9), 120),
    "lined_notebook": _t("low_content", "notebook", "ruled", "BLACK_AND_WHITE", "none", (6, 9), 120),
    "journal": _t("low_content", "journal", "ruled", "BLACK_AND_WHITE", "none", (6, 9), 120),
    "diary": _t("low_content", "journal", "ruled", "BLACK_AND_WHITE", "none", (5.5, 8.5), 120),
    "gratitude_journal": _t("low_content", "guided_journal", "prompt+ruled", "BLACK_AND_WHITE", "none", (6, 9), 110),
    "logbook": _t("low_content", "logbook", "log-table", "BLACK_AND_WHITE", "none", (8.5, 11), 120),
    "tracker": _t("low_content", "tracker", "tracker-grid", "BLACK_AND_WHITE", "none", (8.5, 11), 110),
    "checklist": _t("low_content", "checklist", "checklist", "BLACK_AND_WHITE", "none", (6, 9), 100),
    "daily_planner": _t("low_content", "planner", "daily-planner", "BLACK_AND_WHITE", "none", (7, 10), 200),
    "weekly_planner": _t("low_content", "planner", "weekly-planner", "BLACK_AND_WHITE", "none", (8.5, 11), 120),
    "monthly_planner": _t("low_content", "planner", "monthly-planner", "BLACK_AND_WHITE", "none", (8.5, 11), 80),
    "habit_tracker": _t("low_content", "tracker", "habit-grid", "BLACK_AND_WHITE", "none", (8.5, 11), 110),
    "goal_tracker": _t("low_content", "tracker", "goal-grid", "BLACK_AND_WHITE", "none", (8.5, 11), 100),
    "record_book": _t("low_content", "logbook", "log-table", "BLACK_AND_WHITE", "none", (8.5, 11), 120),
    # MEDIUM CONTENT
    "coloring_book": _t("medium_content", "coloring", "coloring", "BLACK_AND_WHITE", "line_art", (8.5, 11), 60, bleed=False),
    "puzzle_book": _t("medium_content", "puzzle", "puzzle", "BLACK_AND_WHITE", "none", (8.5, 11), 100, needs_answers=True),
    "sudoku_book": _t("medium_content", "puzzle", "puzzle", "BLACK_AND_WHITE", "none", (6, 9), 100, needs_answers=True),
    "crossword_book": _t("medium_content", "puzzle", "puzzle", "BLACK_AND_WHITE", "none", (8.5, 11), 90, needs_answers=True),
    "word_search_book": _t("medium_content", "puzzle", "puzzle", "BLACK_AND_WHITE", "none", (8.5, 11), 100, needs_answers=True),
    "maze_book": _t("medium_content", "puzzle", "puzzle", "BLACK_AND_WHITE", "none", (8.5, 11), 90, needs_answers=True),
    "activity_book": _t("medium_content", "activity", "activity", "BLACK_AND_WHITE", "line_art", (8.5, 11), 90, needs_answers=True),
    "workbook": _t("medium_content", "workbook", "workbook", "BLACK_AND_WHITE", "none", (8.5, 11), 110, needs_answers=True),
    "handwriting_book": _t("medium_content", "handwriting", "handwriting", "BLACK_AND_WHITE", "none", (8.5, 11), 80),
    "tracing_book": _t("medium_content", "handwriting", "tracing", "BLACK_AND_WHITE", "none", (8.5, 11), 80),
    "guided_journal": _t("medium_content", "guided_journal", "prompt+ruled", "BLACK_AND_WHITE", "none", (6, 9), 110),
    "prompt_journal": _t("medium_content", "guided_journal", "prompt+ruled", "BLACK_AND_WHITE", "none", (6, 9), 110),
    "educational_activity_book": _t("medium_content", "activity", "activity", "BLACK_AND_WHITE", "line_art", (8.5, 11), 90, needs_answers=True),
    # FICTION
    "novel": _t("fiction", "prose", "prose", "BLACK_AND_WHITE", "none", (6, 9), 260),
    "novella": _t("fiction", "prose", "prose", "BLACK_AND_WHITE", "none", (5.5, 8.5), 120),
    "short_stories": _t("fiction", "prose", "prose", "BLACK_AND_WHITE", "none", (5.5, 8.5), 160),
    "romance_novel": _t("fiction", "prose", "prose", "BLACK_AND_WHITE", "none", (5.5, 8.5), 260),
    "mystery_novel": _t("fiction", "prose", "prose", "BLACK_AND_WHITE", "none", (6, 9), 280),
    "thriller_novel": _t("fiction", "prose", "prose", "BLACK_AND_WHITE", "none", (6, 9), 300),
    "fantasy_novel": _t("fiction", "prose", "prose", "BLACK_AND_WHITE", "none", (6, 9), 320),
    "science_fiction_novel": _t("fiction", "prose", "prose", "BLACK_AND_WHITE", "none", (6, 9), 300),
    "adventure_novel": _t("fiction", "prose", "prose", "BLACK_AND_WHITE", "none", (6, 9), 260),
    "historical_fiction": _t("fiction", "prose", "prose", "BLACK_AND_WHITE", "none", (6, 9), 300),
    "childrens_fiction": _t("childrens", "prose", "prose", "FULL_COLOR", "color", (8.5, 8.5), 48, bleed=True),
    "early_reader": _t("childrens", "prose", "early-reader", "FULL_COLOR", "color", (6, 9), 64, bleed=True),
    "illustrated_story": _t("childrens", "picture_book", "picture", "FULL_COLOR", "color", (8.5, 8.5), 32, bleed=True),
    # NON-FICTION
    "self_help": _t("nonfiction", "prose_nonfiction", "prose", "BLACK_AND_WHITE", "none", (5.5, 8.5), 200),
    "personal_development": _t("nonfiction", "prose_nonfiction", "prose", "BLACK_AND_WHITE", "none", (5.5, 8.5), 200),
    "business_book": _t("nonfiction", "prose_nonfiction", "prose", "BLACK_AND_WHITE", "none", (6, 9), 220),
    "entrepreneurship_book": _t("nonfiction", "prose_nonfiction", "prose", "BLACK_AND_WHITE", "none", (6, 9), 220),
    "education_book": _t("nonfiction", "educational", "lesson", "BLACK_AND_WHITE", "none", (7, 10), 160, needs_answers=True),
    "skill_development": _t("nonfiction", "prose_nonfiction", "prose", "BLACK_AND_WHITE", "none", (6, 9), 200),
    "biography": _t("nonfiction", "prose_nonfiction", "prose", "BLACK_AND_WHITE", "none", (6, 9), 240),
    "travel_guide": _t("nonfiction", "travel", "guide", "FULL_COLOR", "color", (6, 9), 160),
    "cookbook": _t("nonfiction", "cookbook", "recipe", "FULL_COLOR", "color", (7, 10), 120),
    "reference_book": _t("nonfiction", "prose_nonfiction", "prose", "BLACK_AND_WHITE", "none", (7, 10), 240),
    "how_to_guide": _t("nonfiction", "prose_nonfiction", "prose", "BLACK_AND_WHITE", "none", (6, 9), 180),
    "instructional_guide": _t("nonfiction", "prose_nonfiction", "prose", "BLACK_AND_WHITE", "none", (6, 9), 180),
    "educational_guide": _t("nonfiction", "educational", "lesson", "BLACK_AND_WHITE", "none", (7, 10), 160, needs_answers=True),
    # CHILDREN'S
    "picture_book": _t("childrens", "picture_book", "picture", "FULL_COLOR", "color", (8.5, 8.5), 32, bleed=True),
    "storybook": _t("childrens", "picture_book", "picture", "FULL_COLOR", "color", (8.5, 8.5), 32, bleed=True),
    "childrens_educational_book": _t("childrens", "educational", "lesson", "FULL_COLOR", "color", (8.5, 11), 60, needs_answers=True),
    "childrens_activity_book": _t("childrens", "activity", "activity", "MIXED_COLOR", "line_art", (8.5, 11), 80, needs_answers=True),
    "childrens_coloring_book": _t("childrens", "coloring", "coloring", "BLACK_AND_WHITE", "line_art", (8.5, 11), 50),
    "childrens_tracing_book": _t("childrens", "handwriting", "tracing", "BLACK_AND_WHITE", "none", (8.5, 11), 80),
    "alphabet_book": _t("childrens", "alphabet", "alphabet", "FULL_COLOR", "color", (8.5, 8.5), 56, bleed=True),
    "number_book": _t("childrens", "numbers", "numbers", "FULL_COLOR", "color", (8.5, 8.5), 48, bleed=True),
    "childrens_science_book": _t("childrens", "educational", "lesson", "FULL_COLOR", "color", (8.5, 11), 60),
    "childrens_adventure_book": _t("childrens", "picture_book", "picture", "FULL_COLOR", "color", (8.5, 8.5), 36, bleed=True),
    "bedtime_story": _t("childrens", "picture_book", "picture", "FULL_COLOR", "color", (8.5, 8.5), 32, bleed=True),
    "illustrated_chapter_book": _t("childrens", "prose", "prose", "GRAYSCALE", "grayscale", (5.5, 8.5), 120),
    # VISUAL
    "comic": _t("visual", "comic", "comic", "FULL_COLOR", "cartoon", (6.69, 9.61), 60, bleed=True),
    "graphic_novel": _t("visual", "comic", "comic", "FULL_COLOR", "cartoon", (6.69, 9.61), 90, bleed=True),
    "illustrated_guide": _t("visual", "prose_nonfiction", "prose", "FULL_COLOR", "color", (7, 10), 140),
    "visual_guide": _t("visual", "prose_nonfiction", "prose", "FULL_COLOR", "color", (7, 10), 140),
    # POETRY
    "poetry_collection": _t("poetry", "poetry", "poetry", "BLACK_AND_WHITE", "none", (5.5, 8.5), 90),
    "childrens_poetry": _t("poetry", "poetry", "poetry", "FULL_COLOR", "color", (8.5, 8.5), 48, bleed=True),
    "inspirational_poetry": _t("poetry", "poetry", "poetry", "BLACK_AND_WHITE", "none", (5.5, 8.5), 90),
    # CARTOON
    "cartoon_story": _t("childrens", "cartoon_story", "cartoon", "FULL_COLOR", "cartoon", (8.5, 8.5), 40, bleed=True),
}

CATEGORY_LABELS = {
    "low_content": "Low Content", "medium_content": "Medium Content",
    "fiction": "Fiction", "nonfiction": "Non-Fiction",
    "childrens": "Children's", "visual": "Visual", "poetry": "Poetry",
}

_KEYWORD_SYNONYMS = {
    "novel": ["novel", "fiction book", "story for adults"],
    "romance_novel": ["romance", "love story"],
    "mystery_novel": ["mystery", "detective", "whodunit"],
    "thriller_novel": ["thriller", "suspense"],
    "fantasy_novel": ["fantasy", "magic", "dragon"],
    "science_fiction_novel": ["science fiction", "sci-fi", "scifi", "space opera"],
    "sudoku_book": ["sudoku"],
    "crossword_book": ["crossword"],
    "word_search_book": ["word search", "wordsearch"],
    "maze_book": ["maze", "mazes"],
    "coloring_book": ["coloring", "colouring", "color by number"],
    "gratitude_journal": ["gratitude"],
    "habit_tracker": ["habit"],
    "goal_tracker": ["goal"],
    "daily_planner": ["daily planner", "day planner"],
    "weekly_planner": ["weekly planner", "week planner"],
    "monthly_planner": ["monthly planner", "month planner", "calendar"],
    "cookbook": ["cookbook", "recipes", "recipe book", "cooking"],
    "travel_guide": ["travel guide", "travel", "trip planner", "itinerary"],
    "picture_book": ["picture book"],
    "bedtime_story": ["bedtime"],
    "alphabet_book": ["alphabet", "abc book", "letters book"],
    "number_book": ["counting book", "numbers book"],
    "comic": ["comic", "comics"],
    "graphic_novel": ["graphic novel"],
    "poetry_collection": ["poetry", "poems"],
    "handwriting_book": ["handwriting"],
    "tracing_book": ["tracing"],
    "workbook": ["workbook"],
    "self_help": ["self help", "self-help", "self improvement"],
    "biography": ["biography", "memoir"],
    "business_book": ["business"],
}


def classify_book_type(topic: str, explicit_type: str | None = None) -> tuple[str, dict]:
    """Return (book_type, profile). Explicit valid choice wins (§4, §116);
    otherwise deterministic keyword scoring. Records reasoning upstream."""
    if explicit_type:
        key = explicit_type.lower().replace(" ", "_").replace("-", "_")
        if key not in BOOK_TYPES:
            # nearest-match attempt
            for name in BOOK_TYPES:
                if key in name or name in key:
                    key = name
                    break
            else:
                raise ValueError(f"unknown book type '{explicit_type}' (§116: reject "
                                 f"invalid configuration before expensive generation)")
        return key, BOOK_TYPES[key]
    t = topic.lower()
    scores: dict[str, int] = {}
    for type_key, syns in _KEYWORD_SYNONYMS.items():
        for kw in syns:
            if kw in t:
                scores[type_key] = scores.get(type_key, 0) + len(kw.split())
    # audience-aware adjustments
    child_signals = ["kids", "children", "child", "toddler", "baby", "preschool",
                     "kindergarten", "bedtime"]
    is_child = any(s in t for s in child_signals)
    if is_child:
        if scores.get("coloring_book"):
            scores["childrens_coloring_book"] = scores["coloring_book"] + 1
        if scores.get("activity_book"):
            scores["childrens_activity_book"] = scores["activity_book"] + 1
    if not scores:
        # default: prose nonfiction how-to for generic topics (§3 infer)
        return "how_to_guide", BOOK_TYPES["how_to_guide"]
    best = max(scores, key=lambda k: (scores[k], k))
    return best, BOOK_TYPES[best]


# --------------------------------------------------------------------------
# Audience engine (§7)
# --------------------------------------------------------------------------

def infer_audience(topic: str, book_type: str) -> Audience:
    t = topic.lower()
    if "toddler" in t or "baby" in t:
        return Audience(1, 3, "toddler", "pre-reader", "single words")
    if "preschool" in t or "kindergarten" in t:
        return Audience(3, 5, "preschool", "pre-reader", "simple words")
    if any(s in t for s in ("kids", "children", "child")):
        return Audience(4, 8, "children", "early-reader", "simple, concrete words")
    if any(s in t for s in ("teen", "young adult", "ya ")):
        return Audience(12, 18, "teen", "ya", "accessible, vivid")
    cat = BOOK_TYPES[book_type]["category"]
    if cat == "childrens":
        return Audience(4, 8, "children", "early-reader", "simple, concrete words")
    if cat in ("medium_content", "low_content"):
        return Audience(8, 99, "general", "general", "plain, practical")
    return Audience(18, 99, "adult", "adult-general", "general")


def sentence_limits(audience: Audience) -> tuple[int, int]:
    """(max avg words per sentence, max sentences per paragraph) by age (§45)."""
    if audience.age_max <= 5:
        return (6, 2)
    if audience.age_max <= 8:
        return (9, 3)
    if audience.age_max <= 12:
        return (12, 4)
    if audience.age_max <= 18:
        return (16, 5)
    return (22, 6)


# --------------------------------------------------------------------------
# Market / competitor / positioning engine (§8, §9)
# --------------------------------------------------------------------------

MARKET_CONVENTIONS = {
    "low_content": {"title_pattern": "Benefit + Noun", "cover": "clean typographic",
                    "length": "80-140 pages", "gap": "generic interiors; opportunity in focused niches"},
    "medium_content": {"title_pattern": "Noun + Book + Audience", "cover": "sample content visible",
                       "length": "60-120 pages", "gap": "puzzle quality and verified solutions"},
    "fiction": {"title_pattern": "evocative 1-3 words", "cover": "genre imagery",
                "length": "50k-90k words", "gap": "genre promise kept consistently"},
    "nonfiction": {"title_pattern": "Outcome promise + subtitle scope", "cover": "author-forward",
                   "length": "30k-70k words", "gap": "actionable structure over fluff"},
    "childrens": {"title_pattern": "Character + action", "cover": "bright character art",
                  "length": "24-48 pages", "gap": "age-fit vocabulary and rhythm"},
    "visual": {"title_pattern": "Series-forward", "cover": "hero panel",
               "length": "48-120 pages", "gap": "consistent characters"},
    "poetry": {"title_pattern": "image/feeling phrase", "cover": "minimal",
               "length": "60-120 poems", "gap": "typeset line integrity"},
}


def market_analysis(book_type: str, topic: str, research_entries: list) -> dict:
    """Positioning derived from recorded conventions + research ledger.
    Never fabricates external data: conventions are internal knowledge,
    research entries are only what was actually retrieved (§8, §12)."""
    cat = BOOK_TYPES[book_type]["category"]
    conv = MARKET_CONVENTIONS[cat]
    niche = topic.strip().lower()
    return {
        "niche": niche,
        "sub_niche": niche.split()[-1] if niche else niche,
        "category": cat,
        "conventions": conv,
        "competition_notes": "derived from internal convention table; no live "
                             "competitor scraping performed (none fabricated)",
        "positioning": f"A {conv['title_pattern'].lower()} positioned {book_type.replace('_', ' ')}"
                       f" for the {niche} niche with internally-verified quality.",
        "freshness": f"applies the '{niche}' niche to a {book_type.replace('_', ' ')} format",
        "research_refs": [e["id"] for e in research_entries],
        "recorded_on": None,  # filled by caller
    }


# --------------------------------------------------------------------------
# Title engine (§10)
# --------------------------------------------------------------------------

_TITLE_TEMPLATES = {
    "low_content": "{Theme} {Noun}: {Subtitle}",
    "medium_content": "The {Theme} {Noun}: {Subtitle}",
    "fiction": "{Theme} {Noun}",
    "nonfiction": "{Theme} {Noun}: {Subtitle}",
    "childrens": "{Theme} the {Noun}",
    "visual": "{Theme} {Noun}",
    "poetry": "{Theme} and {Theme2}: {Subtitle}",
}


def _theme_words(topic: str) -> list[str]:
    words = [w for w in re.split(r"[^A-Za-z]+", topic.title()) if len(w) > 2]
    return words or ["Little"]


def generate_title(book_type: str, topic: str, seed: int) -> dict:
    """Deterministic, truthful title generation (§10: title must represent
    the book; it is derived from the actual topic and type)."""
    cat = BOOK_TYPES[book_type]["category"]
    words = _theme_words(topic)
    theme = words[0]
    theme2 = words[1] if len(words) > 1 else "Light"
    nouns = {"low_content": "Journal", "medium_content": "Book", "fiction": "Story",
             "nonfiction": "Guide", "childrens": "Adventure", "visual": "Chronicles",
             "poetry": "Verses"}
    noun = {"coloring_book": "Coloring Book", "puzzle_book": "Puzzle Book",
            "sudoku_book": "Sudoku Book", "word_search_book": "Word Search Book",
            "maze_book": "Maze Book", "activity_book": "Activity Book",
            "cookbook": "Cookbook", "travel_guide": "Travel Guide",
            "daily_planner": "Daily Planner", "weekly_planner": "Weekly Planner",
            "monthly_planner": "Monthly Planner", "habit_tracker": "Habit Tracker",
            "goal_tracker": "Goal Tracker", "notebook": "Notebook",
            "journal": "Journal", "diary": "Diary",
            "gratitude_journal": "Gratitude Journal"}.get(book_type, nouns[cat])
    subs = {
        "low_content": f"A {topic.title()} Companion",
        "medium_content": f"{topic.title()} Fun for Every Day",
        "nonfiction": f"A Practical {topic.title()} Handbook",
        "childrens": f"A {topic.title()} Story",
        "poetry": f"Poems About {topic.title()}",
        "visual": f"Volume 1",
        "fiction": "",
    }
    tmpl = _TITLE_TEMPLATES[cat]
    title = tmpl.format(Theme=theme, Theme2=theme2, Noun=noun, Subtitle=subs.get(cat, ""))
    title = re.sub(r"\s+", " ", title).strip(" :")
    if ":" in title:
        head, sub = [s.strip() for s in title.split(":", 1)]
    else:
        head, sub = title, ""
    return {"title": head, "subtitle": sub, "series": "", "edition": "First Edition"}


# --------------------------------------------------------------------------
# Author engine (§11)
# --------------------------------------------------------------------------

def default_author(topic: str, seed: int) -> Author:
    """Deterministic pen name; user can override (§140)."""
    h = hashlib.sha256(f"{topic}:{seed}".encode()).hexdigest()
    firsts = ["Ava", "Noah", "Maya", "Liam", "Zoe", "Eli", "Nina", "Owen", "Iris", "Theo"]
    lasts = ["Bright", "Calloway", "Marsh", "Hale", "Winters", "Ashford", "Vale", "Quill"]
    name = f"{firsts[int(h[:4], 16) % len(firsts)]} {lasts[int(h[4:8], 16) % len(lasts)]}"
    return Author(name=name, pen_name=True)
