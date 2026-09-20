"""Compatibility matrices (spec §117) — configurable, not universal.

book_type x color, book_type x illustration, book_type x trim,
book_type x format, language x typography, cartoon x color, cover x interior.
"""
from __future__ import annotations

from .classify import BOOK_TYPES
from .ruleset import ink_key

# cover color is always FULL_COLOR for print covers; interior varies (§30).
COVER_INTERIOR_MATRIX = {
    ("FULL_COLOR", "BLACK_AND_WHITE"): True,
    ("FULL_COLOR", "GRAYSCALE"): True,
    ("FULL_COLOR", "FULL_COLOR"): True,
    ("FULL_COLOR", "MIXED_COLOR"): True,
}


def check_book_type_color(book_type: str, color_mode: str) -> tuple[bool, str]:
    prof = BOOK_TYPES[book_type]
    if color_mode not in ("BLACK_AND_WHITE", "GRAYSCALE", "FULL_COLOR", "MIXED_COLOR"):
        return False, f"unknown color mode {color_mode}"
    # coloring books: interior must stay B&W line art (§47) unless user
    # explicitly requests color (they get an activity-style colored book).
    if prof["engine"] == "coloring" and color_mode in ("FULL_COLOR", "MIXED_COLOR"):
        return False, ("coloring-book interiors are black-and-white line art; "
                       "FULL_COLOR interior conflicts with the coloring engine. "
                       "Use activity_book for color interiors.")
    return True, "ok"


def check_book_type_illustration(book_type: str, illustration_mode: str) -> tuple[bool, str]:
    prof = BOOK_TYPES[book_type]
    if illustration_mode == "none":
        return True, "ok"
    if prof["category"] == "fiction" and illustration_mode == "cartoon":
        return False, "cartoon illustration is not supported for prose fiction interiors"
    if prof["engine"] == "coloring" and illustration_mode not in ("none", "line_art"):
        return False, "coloring books accept line_art only"
    return True, "ok"


def check_cartoon_color(color_mode: str) -> tuple[bool, str]:
    # cartoon engine supports full color, grayscale and b&w (§120)
    if color_mode in ("FULL_COLOR", "GRAYSCALE", "BLACK_AND_WHITE", "MIXED_COLOR"):
        return True, "ok"
    return False, f"cartoon engine does not support {color_mode}"


def check_language_typography(language: str) -> tuple[bool, str]:
    """Built-in PDF fonts cover Latin-1. Other scripts are supported in the
    content model but must be flagged for font fallback (§34, §35)."""
    if language in ("en", "de", "fr", "es", "it", "pt", "nl", "sv", "da", "no"):
        return True, "latin-1 subset fully covered by built-in fonts"
    return False, (f"language '{language}' needs glyph coverage beyond built-in "
                   "fonts; content will be transliterated/flagged, never silently broken")


def check_format_binding(fmt: str, binding: str) -> tuple[bool, str]:
    if fmt == "ebook":
        return True, "ebook only"
    if binding not in ("paperback", "hardcover"):
        return False, f"unknown binding {binding}"
    return True, "ok"


def check_trim_for_binding(trim_w: float, trim_h: float, binding: str, ruleset) -> tuple[bool, str]:
    if ruleset.find_trim(trim_w, trim_h, binding) is None:
        return False, f"trim {trim_w}x{trim_h} not available for {binding} in ruleset {ruleset.version}"
    return True, "ok"


def validate_config(cfg, ruleset) -> list[str]:
    """Reject invalid combinations BEFORE expensive generation (§116)."""
    problems = []
    ok, why = check_book_type_color(cfg.book_type, cfg.color_mode)
    if not ok:
        problems.append(why)
    ok, why = check_book_type_illustration(cfg.book_type, cfg.illustration_mode)
    if not ok:
        problems.append(why)
    ok, why = check_format_binding(cfg.format, cfg.binding)
    if not ok:
        problems.append(why)
    if cfg.format != "ebook":
        ok, why = check_trim_for_binding(cfg.trim_w, cfg.trim_h, cfg.binding, ruleset)
        if not ok:
            problems.append(why)
    if cfg.book_type in BOOK_TYPES:
        prof = BOOK_TYPES[cfg.book_type]
        if cfg.binding == "paperback":
            try:
                lo, hi = ruleset.page_count_limits(cfg.trim_w, cfg.trim_h,
                                                   cfg.color_mode, cfg.paper, cfg.binding)
                if cfg.target_pages > hi:
                    problems.append(f"target {cfg.target_pages} pages exceeds KDP max {hi}")
            except Exception as e:  # ruleset rejects combination
                problems.append(str(e))
    if not cfg.topic.strip():
        problems.append("topic is required")
    return problems
