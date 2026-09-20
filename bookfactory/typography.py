"""Typography engine (spec §33, §34, §36, §64, §181).

Built-in PDF base-14 font metrics (deterministic), text measurement, word
wrapping, widow/orphan detection, and the reusable design-token system.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- base-14 widths (per-glyph where measured, average otherwise) ----------
# Standard Helvetica widths for ASCII range (units: 1/1000 em).
_H = {
    " ": 278, "!": 278, "\"": 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584,
    "?": 556, "@": 1015, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556,
    "`": 333, "{": 333, "|": 260, "}": 333, "~": 584,
}
_H_UPPER_DEFAULT = 722
_H_LOWER_DEFAULT = 556
_H_DIGIT = 556
_TIMES_AVG = 500
_COURIER = 600  # monospace

# WinAnsiEncoding additions beyond Latin-1 (as used by PDF base-14 fonts).
WINANSI_EXTRA = {
    "\u2013": 556, "\u2014": 1000, "\u2018": 222, "\u2019": 222,
    "\u201c": 333, "\u201d": 333, "\u2022": 350, "\u2026": 1000,
    "\u00a9": 737, "\u00ae": 737, "\u2122": 1000, "\u20ac": 556,
    "\u201a": 222, "\u201e": 333, "\u2030": 1000, "\u2039": 333,
    "\u203a": 333, "\u0152": 1000, "\u0153": 944, "\u0160": 667,
    "\u0161": 556, "\u0178": 667, "\u017d": 611, "\u017e": 556,
    "\u0192": 556, "\u02c6": 333, "\u02dc": 333, "\u2020": 556,
    "\u2021": 556, "\u201a": 222,
}

# Unicode -> WinAnsi byte value (for PDF string emission)
UNICODE_TO_WINANSI = {
    "\u20ac": 0x80, "\u201a": 0x82, "\u0192": 0x83, "\u201e": 0x84,
    "\u2026": 0x85, "\u2020": 0x86, "\u2021": 0x87, "\u02c6": 0x88,
    "\u2030": 0x89, "\u0160": 0x8a, "\u2039": 0x8b, "\u0152": 0x8c,
    "\u017d": 0x8e, "\u2018": 0x91, "\u2019": 0x92, "\u201c": 0x93,
    "\u201d": 0x94, "\u2022": 0x95, "\u2013": 0x96, "\u2014": 0x97,
    "\u02dc": 0x98, "\u2122": 0x99, "\u0161": 0x9a, "\u203a": 0x9b,
    "\u0153": 0x9c, "\u017e": 0x9e, "\u0178": 0x9f,
}


class FontMetrics:
    VERSION = "metrics-1.0"
    FONTS = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
             "Times-Roman", "Times-Bold", "Times-Italic", "Courier")

    def char_width(self, font: str, size_pt: float, ch: str) -> float:
        code = ord(ch)
        if font.startswith("Courier"):
            return size_pt * _COURIER / 1000.0
        if font.startswith("Times"):
            return size_pt * _TIMES_AVG / 1000.0
        # Helvetica family
        if ch in _H:
            return size_pt * _H[ch] / 1000.0
        if ch.isdigit():
            return size_pt * _H_DIGIT / 1000.0
        if ch in WINANSI_EXTRA:
            return size_pt * WINANSI_EXTRA[ch] / 1000.0
        if ch.isupper():
            return size_pt * _H_UPPER_DEFAULT / 1000.0
        if code >= 32 and code <= 255:
            return size_pt * _H_LOWER_DEFAULT / 1000.0
        # beyond latin-1: unsupported glyph -> caller must handle (§34, §35)
        return -1.0

    def text_width(self, font: str, size_pt: float, text: str) -> float:
        w = 0.0
        for ch in text:
            cw = self.char_width(font, size_pt, ch)
            if cw < 0:
                raise ValueError(f"glyph U+{ord(ch):04X} unsupported by {font} (§35 broken glyph)")
            w += cw
        return w

    def encodable(self, font: str, text: str) -> list[str]:
        """List of chars NOT representable (for coverage validation §34)."""
        out = []
        for ch in text:
            if self.char_width(font, 10.0, ch) < 0:
                out.append(ch)
        return out


METRICS = FontMetrics()


def wrap_text(text: str, font: str, size_pt: float, width_pt: float,
              metrics: FontMetrics = METRICS) -> list[str]:
    """Greedy word wrap. Long unbreakable words are hard-broken (§181)."""
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            if w == "":
                continue
            trial = (cur + " " + w).strip()
            if metrics.text_width(font, size_pt, trial) <= width_pt:
                cur = trial
                continue
            if cur:
                lines.append(cur)
                cur = ""
            while metrics.text_width(font, size_pt, w) > width_pt and len(w) > 1:
                # hard break
                lo, hi = 1, len(w)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if metrics.text_width(font, size_pt, w[:mid]) <= width_pt:
                        lo = mid
                    else:
                        hi = mid - 1
                lines.append(w[:lo])
                w = w[lo:]
            cur = w
        lines.append(cur)
    return lines


def detect_widows_orphans(page_line_counts: list[tuple[str, int, int]]) -> list[dict]:
    """§36: input = list of (paragraph_id, lines_on_page, total_lines).
    A paragraph split with fewer than 2 lines on either side is a defect."""
    problems = []
    for pid, on_page, total in page_line_counts:
        remaining = total - on_page
        if 0 < on_page < total:
            if on_page < 2:
                problems.append({"paragraph": pid, "issue": "orphan",
                                 "detail": f"only {on_page} line(s) start the paragraph on this page"})
            if remaining < 2:
                problems.append({"paragraph": pid, "issue": "widow",
                                 "detail": f"only {remaining} line(s) carry over to next page"})
    return problems


# --- design tokens (§64): the ONLY place style values live -----------------

@dataclass(frozen=True)
class DesignTokens:
    version: str = "tokens-1.0"
    ink: tuple = (0.10, 0.10, 0.12)            # near-black
    ink_gray: tuple = (0.45, 0.45, 0.47)
    accent: tuple = (0.16, 0.32, 0.55)
    paper: tuple = (1.0, 1.0, 1.0)
    body_font: str = "Times-Roman"
    heading_font: str = "Helvetica-Bold"
    mono_font: str = "Courier"
    body_size: float = 11.0
    h1_size: float = 20.0
    h2_size: float = 15.0
    h3_size: float = 12.5
    leading: float = 1.32
    para_space_after: float = 6.0
    page_number_size: float = 9.0
    border_pt: float = 0.75
    caption_size: float = 9.0

    def scale_for_audience(self, age_max: int) -> "DesignTokens":
        """§45: children's books get larger type and looser leading."""
        if age_max <= 5:
            return DesignTokens(body_size=18.0, h1_size=28.0, h2_size=22.0,
                                h3_size=19.0, leading=1.6, caption_size=13.0,
                                page_number_size=12.0)
        if age_max <= 8:
            return DesignTokens(body_size=15.0, h1_size=24.0, h2_size=19.0,
                                h3_size=16.0, leading=1.5, caption_size=11.0,
                                page_number_size=11.0)
        if age_max <= 12:
            return DesignTokens(body_size=12.5, leading=1.42)
        return self

    @staticmethod
    def rgb255(t: tuple) -> tuple:
        return tuple(int(round(c * 255)) for c in t)


DEFAULT_TOKENS = DesignTokens()
