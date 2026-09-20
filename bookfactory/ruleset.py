"""KDP ruleset engine (spec §18, §19, §21, §67, §68).

Rules are data (data/kdp_ruleset.json), never hard-coded assumptions.
Every calculation here is pure and deterministic; every consumer records
which ruleset version produced its numbers (§82).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

_RULESET_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "kdp_ruleset.json")


class RulesetError(ValueError):
    pass


def load_ruleset(path: Optional[str] = None) -> dict:
    with open(path or _RULESET_PATH, "r", encoding="utf-8") as f:
        rs = json.load(f)
    for key in ("ruleset_id", "version", "retrieval_date", "marketplace",
                "spine", "margins", "trim_sizes", "cover", "bleed_in"):
        if key not in rs:
            raise RulesetError(f"ruleset missing required key: {key}")
    return rs


def ink_key(color_mode: str, paper: str) -> str:
    """Map (color_mode, paper) -> ruleset limits key."""
    if color_mode in ("BLACK_AND_WHITE", "GRAYSCALE"):
        if paper == "cream":
            return "black_cream"
        if paper == "groundwood":
            return "black_groundwood"
        return "black_white"
    if color_mode in ("FULL_COLOR", "MIXED_COLOR"):
        return "premium_color"
    raise RulesetError(f"unknown color mode {color_mode}")


@dataclass(frozen=True)
class TrimSpec:
    w: float
    h: float
    large: bool
    limits: dict

    def label(self) -> str:
        return f'{self.w:g}" x {self.h:g}"'


class Ruleset:
    """Read-only view over the KDP ruleset with calculation helpers."""

    def __init__(self, data: Optional[dict] = None):
        self.data = data or load_ruleset()
        self.version = self.data["version"]
        self.ruleset_id = self.data["ruleset_id"]
        self.retrieval_date = self.data["retrieval_date"]
        self.bleed_in = float(self.data["bleed_in"])

    # -- trims ---------------------------------------------------------
    def trims(self, binding: str = "paperback") -> list[TrimSpec]:
        out = []
        for t in self.data["trim_sizes"].get(binding, []):
            out.append(TrimSpec(t["w"], t["h"], t["large"], t["limits"]))
        return out

    def find_trim(self, w: float, h: float, binding: str = "paperback") -> Optional[TrimSpec]:
        for t in self.trims(binding):
            if abs(t.w - w) < 1e-9 and abs(t.h - h) < 1e-9:
                return t
        return None

    def page_count_limits(self, w: float, h: float, color_mode: str, paper: str,
                          binding: str = "paperback") -> tuple[int, int]:
        t = self.find_trim(w, h, binding)
        if t is None:
            raise RulesetError(f"trim {w}x{h} not supported for {binding} by ruleset {self.version}")
        lim = t.limits.get(ink_key(color_mode, paper))
        if lim is None:
            raise RulesetError(
                f"ink/paper combination color={color_mode} paper={paper} not available "
                f"for trim {t.label()} under ruleset {self.version}")
        return int(lim[0]), int(lim[1])

    # -- margins (§24, §67) --------------------------------------------
    def gutter_in(self, page_count: int) -> float:
        for band in self.data["margins"]["gutter_by_page_count"]:
            if band["min_pages"] <= page_count <= band["max_pages"]:
                return float(band["gutter_in"])
        # beyond the highest published band: use the largest band (fail-safe,
        # documented behaviour; KDP max paperback is 828 pages)
        bands = self.data["margins"]["gutter_by_page_count"]
        return float(bands[-1]["gutter_in"])

    def outside_margin_min_in(self, bleed: bool) -> float:
        m = self.data["margins"]
        return float(m["outside_min_with_bleed_in"] if bleed else m["outside_min_no_bleed_in"])

    def page_size_with_bleed(self, trim_w: float, trim_h: float) -> tuple[float, float]:
        b = self.bleed_in
        return (trim_w + b, trim_h + 2 * b)

    # -- spine (§68) ----------------------------------------------------
    def paper_thickness(self, color_mode: str, paper: str) -> float:
        key = ink_key(color_mode, paper)
        table = self.data["spine"]["paper_thickness_in_per_page"]
        if key == "black_white":
            return float(table["white"])
        if key == "black_cream":
            return float(table["cream"])
        if key == "black_groundwood":
            raise RulesetError(
                "No officially published spine multiplier for groundwood paper in "
                f"ruleset {self.version}; refusing to guess (§68). Choose white/cream.")
        return float(table["color_premium"])

    def spine_width_in(self, page_count: int, color_mode: str, paper: str,
                       binding: str = "paperback") -> float:
        if page_count < 1:
            raise RulesetError("page count must be >= 1")
        mult = self.paper_thickness(color_mode, paper)
        s = self.data["spine"]
        allowance = float(s["paperback_cover_allowance_in"] if binding == "paperback"
                          else s["hardcover_cover_allowance_in"])
        return page_count * mult + allowance

    def spine_text_allowed(self, page_count: int) -> bool:
        return page_count >= int(self.data["spine"]["spine_text_min_pages"])

    # -- cover (§67) ----------------------------------------------------
    def cover_size_in(self, trim_w: float, trim_h: float, spine_in: float,
                      binding: str = "paperback") -> tuple[float, float]:
        if binding == "paperback":
            b = self.bleed_in
            return (b + trim_w + spine_in + trim_w + b, trim_h + 2 * b)
        c = self.data["cover"]["hardcover_case_laminate"]
        hinge = float(c["hinge_in"]); wrap = float(c["wrap_in"])
        return (2 * trim_w + spine_in + hinge + 2 * wrap,
                trim_h + 0.236 + 2 * wrap)

    def barcode_zone_in(self) -> tuple[float, float]:
        z = self.data["cover"]["paperback"]["barcode_zone_in"]
        return float(z["w"]), float(z["h"])

    def cover_text_safe_margin_in(self) -> float:
        return float(self.data["cover"]["paperback"]["text_safe_margin_from_trim_in"])

    def is_large_trim(self, w: float, h: float) -> bool:
        th = self.data["large_trim_threshold_in"]
        return w > th["width_gt"] or h > th["height_gt"]
