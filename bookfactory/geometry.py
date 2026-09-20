"""Page-geometry engine with INDEPENDENT cross-calculation (spec §24, §25).

Primary path works in float inches.  The independent path re-derives every
result in integer milli-inches (1 inch = 1000 milli-in) through a separate
code path and the two must agree within tolerance or the build FAILs (§25).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from .ruleset import Ruleset

IN_TO_PT = 72.0
TOL_IN = 1e-4          # allowed difference between primary & independent paths
MIL = 1000             # milli-inches per inch


@dataclass(frozen=True)
class PageGeometry:
    trim_w_in: float
    trim_h_in: float
    bleed_in: float
    page_w_in: float          # physical PDF page size (with bleed when bleed)
    page_h_in: float
    gutter_in: float          # inside margin
    outside_in: float         # outside margin
    top_in: float
    bottom_in: float
    content_x_in: float       # content box, LEFT page coords (mirror on right)
    content_y_in: float
    content_w_in: float
    content_h_in: float
    page_count_used: int
    ruleset_version: str

    def mirrored_x(self, right_page: bool) -> float:
        """X of the outer edge origin for gutter mirroring (binding on left of
        right-hand pages, on right of left-hand pages)."""
        if right_page:
            return self.gutter_in
        return self.outside_in

    def to_dict(self):
        return asdict(self)


def compute_geometry(rs: Ruleset, trim_w: float, trim_h: float, page_count: int,
                     bleed: bool, binding: str = "paperback") -> PageGeometry:
    """Primary implementation: float inches."""
    gutter = rs.gutter_in(page_count)
    outside = rs.outside_margin_min_in(bleed)
    # margins we actually use: outside margin gets a little comfort padding
    outside_used = max(outside, 0.5 if not bleed else 0.5)
    top = bottom = max(outside, 0.6)
    if bleed:
        page_w, page_h = rs.page_size_with_bleed(trim_w, trim_h)
    else:
        page_w, page_h = trim_w, trim_h
    content_w = trim_w - gutter - outside_used
    content_h = trim_h - top - bottom
    if content_w <= 0 or content_h <= 0:
        raise ValueError(f"margins exceed trim: content {content_w}x{content_h}")
    return PageGeometry(
        trim_w_in=trim_w, trim_h_in=trim_h, bleed_in=rs.bleed_in if bleed else 0.0,
        page_w_in=page_w, page_h_in=page_h,
        gutter_in=gutter, outside_in=outside_used, top_in=top, bottom_in=bottom,
        content_x_in=gutter, content_y_in=bottom,
        content_w_in=content_w, content_h_in=content_h,
        page_count_used=page_count, ruleset_version=rs.version)


def _m(x: float) -> int:
    return int(round(x * MIL))


def independent_geometry(rs: Ruleset, trim_w: float, trim_h: float, page_count: int,
                         bleed: bool) -> dict:
    """Independent re-implementation: pure integer milli-inch arithmetic.
    Deliberately different structure from compute_geometry (§25)."""
    gut = 0
    for band in rs.data["margins"]["gutter_by_page_count"]:
        if band["min_pages"] <= page_count <= band["max_pages"]:
            gut = _m(band["gutter_in"])
            break
    if gut == 0:
        gut = _m(rs.data["margins"]["gutter_by_page_count"][-1]["gutter_in"])
    out_min = _m(rs.data["margins"]["outside_min_with_bleed_in" if bleed
                                   else "outside_min_no_bleed_in"])
    outside_used = max(out_min, 500)
    top = bottom = max(out_min, 600)
    trim_wm, trim_hm = _m(trim_w), _m(trim_h)
    bleed_m = _m(rs.bleed_in) if bleed else 0
    page_wm = trim_wm + bleed_m
    page_hm = trim_hm + 2 * bleed_m
    return {
        "page_w_in": page_wm / MIL,
        "page_h_in": page_hm / MIL,
        "gutter_in": gut / MIL,
        "outside_in": outside_used / MIL,
        "top_in": top / MIL,
        "bottom_in": bottom / MIL,
        "content_w_in": (trim_wm - gut - outside_used) / MIL,
        "content_h_in": (trim_hm - top - bottom) / MIL,
    }


def verify_independent(g: PageGeometry, rs: Ruleset, bleed: bool) -> list[str]:
    """Return list of mismatches; empty list means the two engines agree."""
    ind = independent_geometry(rs, g.trim_w_in, g.trim_h_in, g.page_count_used, bleed)
    problems = []
    pairs = [("page_w_in", g.page_w_in), ("page_h_in", g.page_h_in),
             ("gutter_in", g.gutter_in), ("outside_in", g.outside_in),
             ("top_in", g.top_in), ("bottom_in", g.bottom_in),
             ("content_w_in", g.content_w_in), ("content_h_in", g.content_h_in)]
    for key, primary in pairs:
        if abs(primary - ind[key]) > TOL_IN:
            problems.append(f"{key}: primary={primary:.6f} independent={ind[key]:.6f}")
    return problems


# -- spine & cover, independent check -----------------------------------------

def spine_independent(rs: Ruleset, page_count: int, color_mode: str, paper: str,
                      binding: str = "paperback") -> float:
    """Second implementation of the spine formula using Decimal."""
    from decimal import Decimal
    mult = Decimal(str(rs.paper_thickness(color_mode, paper)))
    allowance = Decimal(str(rs.data["spine"]["paperback_cover_allowance_in"]
                            if binding == "paperback"
                            else rs.data["spine"]["hardcover_cover_allowance_in"]))
    return float(Decimal(page_count) * mult + allowance)


def cover_independent(rs: Ruleset, trim_w: float, trim_h: float, spine: float,
                      binding: str = "paperback") -> tuple[float, float]:
    """Second implementation of the cover-size formula using Decimal."""
    from decimal import Decimal
    tw, th, sp, b = (Decimal(str(x)) for x in (trim_w, trim_h, spine, rs.bleed_in))
    if binding == "paperback":
        return float(b + tw + sp + tw + b), float(th + 2 * b)
    c = rs.data["cover"]["hardcover_case_laminate"]
    hinge, wrap = Decimal(str(c["hinge_in"])), Decimal(str(c["wrap_in"]))
    return float(2 * tw + sp + hinge + 2 * wrap), float(th + Decimal("0.236") + 2 * wrap)
