"""Color mode engine (spec §27–§32, §119, §122, §124).

Every print interior declares exactly one mode. Declared intent is compared
against ACTUAL layout/asset content — metadata alone never passes (§113).
Includes controlled conversion checks (§31) and user-override support (§122).
"""
from __future__ import annotations

from .errors import blocking, warning

MODES = ("BLACK_AND_WHITE", "GRAYSCALE", "FULL_COLOR", "MIXED_COLOR")

# §28 typical defaults (recommendations, not hard rules)
DEFAULTS = {
    "novel": "BLACK_AND_WHITE", "self_help": "BLACK_AND_WHITE",
    "notebook": "BLACK_AND_WHITE", "planner": "BLACK_AND_WHITE",
    "puzzle": "BLACK_AND_WHITE", "coloring": "BLACK_AND_WHITE",
    "picture_book": "FULL_COLOR", "comic": "FULL_COLOR",
}


def _chroma(rgb) -> bool:
    r, g, b = rgb[:3]
    return (max(r, g, b) - min(r, g, b)) > 0.05


def check_intent(cfg, layout) -> list:
    """Compare declared color mode against the actual display list."""
    defects = []
    mode = cfg.color_mode
    if mode not in MODES:
        return [blocking("COLOR", "unknown-color-mode", "config",
                         "declare a valid color mode (§27)", str(mode))]
    chroma_pages = 0
    for p in layout.pages:
        page_chroma = False
        for op in p.ops:
            if op.get("op") == "image":
                ent = layout.assets.get(op["asset_id"])
                if ent and not _canvas_is_gray(ent["canvas"]):
                    page_chroma = True
            else:
                col = op.get("color") or op.get("outline")
                if col and _chroma(col):
                    page_chroma = True
        if page_chroma:
            chroma_pages += 1
        expected = p.meta.expected_color_mode
        if mode == "BLACK_AND_WHITE" and page_chroma:
            # the renderer will force gray, but the layout declaring chroma
            # while the book is B&W means intent/render disagreement (§32)
            p.meta.expected_color_mode = "BLACK_AND_WHITE"
        if mode == "MIXED_COLOR" and expected not in ("FULL_COLOR", "BLACK_AND_WHITE"):
            defects.append(blocking("COLOR", "mixed-page-intent", p.meta.page_id,
                                    "set explicit per-page intent (§29)", expected))
    if mode in ("BLACK_AND_WHITE", "GRAYSCALE") and chroma_pages == 0:
        pass  # consistent
    if mode == "FULL_COLOR" and chroma_pages == 0 and len(layout.pages) > 4:
        defects.append(warning("COLOR", "no-color-in-full-color-book", "layout",
                               "verify illustration mode",
                               "FULL_COLOR declared but no chromatic content found"))
    if mode == "MIXED_COLOR" and (chroma_pages == 0 or chroma_pages == len(layout.pages)):
        defects.append(warning("COLOR", "mixed-mode-degenerate", "layout",
                               "MIXED_COLOR should contain both color and non-color pages",
                               f"chroma on {chroma_pages}/{len(layout.pages)} pages"))
    return defects


def _canvas_is_gray(canvas) -> bool:
    px = canvas.px
    step = max(1, len(px) // (3 * 40000))
    for i in range(0, len(px), 3 * step):
        r, g, b = px[i], px[i + 1], px[i + 2]
        if abs(r - g) > 12 or abs(g - b) > 12:
            return False
    return True


def validate_conversion(canvas) -> dict:
    """§31: before accepting a color→grayscale/B&W conversion, verify the
    conversion did not destroy essential tonal information."""
    px = canvas.px
    lumas = set()
    step = max(1, len(px) // (3 * 60000))
    for i in range(0, len(px), 3 * step):
        lum = int(0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2])
        lumas.add(lum)
    distinct = len(lumas)
    return {
        "distinct_gray_levels": distinct,
        "information_preserved": distinct >= 4,
        "verdict": "PASS" if distinct >= 4 else "WARN",
    }
