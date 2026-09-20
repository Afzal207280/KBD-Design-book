"""Cover engine (spec §30, §66–§71, §123, §161, §239).

Front + spine + back designed from the FINAL validated page count.
Spine width is computed from page count + paper via the ruleset (§68) —
never guessed. Cover size follows the official formulas (§67) and is
re-checked with an independent Decimal implementation (§25).
"""
from __future__ import annotations

import math

from .geometry import spine_independent, cover_independent
from .raster import Canvas
from .typography import METRICS, wrap_text
from .errors import blocking, warning

COVER_DPI = 90

CATEGORY_HUES = {
    "low_content": ((0.13, 0.35, 0.42), (0.93, 0.90, 0.82)),
    "medium_content": ((0.55, 0.23, 0.20), (0.97, 0.93, 0.86)),
    "fiction": ((0.12, 0.14, 0.30), (0.85, 0.80, 0.72)),
    "nonfiction": ((0.16, 0.30, 0.52), (0.94, 0.95, 0.97)),
    "childrens": ((0.90, 0.47, 0.20), (1.00, 0.95, 0.80)),
    "visual": ((0.35, 0.20, 0.50), (0.95, 0.92, 0.98)),
    "poetry": ((0.25, 0.35, 0.30), (0.95, 0.95, 0.90)),
}


def design_cover(cfg, ruleset, page_count: int, category: str) -> dict:
    """Produce the full-wrap cover canvas + traceable geometry record."""
    spine = ruleset.spine_width_in(page_count, cfg.color_mode, cfg.paper, cfg.binding)
    spine_chk = spine_independent(ruleset, page_count, cfg.color_mode, cfg.paper, cfg.binding)
    if abs(spine - spine_chk) > 1e-4:
        raise ValueError(f"spine engines disagree: {spine} vs {spine_chk}")
    w_in, h_in = ruleset.cover_size_in(cfg.trim_w, cfg.trim_h, spine, cfg.binding)
    w_chk, h_chk = cover_independent(ruleset, cfg.trim_w, cfg.trim_h, spine, cfg.binding)
    if abs(w_in - w_chk) > 1e-3 or abs(h_in - h_chk) > 1e-3:
        raise ValueError(f"cover engines disagree: {(w_in, h_in)} vs {(w_chk, h_chk)}")

    can = Canvas(w_in * 72, h_in * 72, dpi=COVER_DPI)
    bg, panel = CATEGORY_HUES.get(category, ((0.2, 0.2, 0.25), (0.95, 0.95, 0.95)))
    bg255 = tuple(int(c * 255) for c in bg)
    panel255 = tuple(int(c * 255) for c in panel)
    can.fill_rect(0, 0, w_in * 72, h_in * 72, bg255)

    bleed = ruleset.bleed_in
    safe = ruleset.cover_text_safe_margin_in()
    spine_text_ok = ruleset.spine_text_allowed(page_count)

    # geometry in inches along the wrap: [bleed][back][spine][front][bleed]
    back_x = bleed
    spine_x = bleed + cfg.trim_w
    front_x = spine_x + spine

    # ---- front ----------------------------------------------------------
    fx = front_x + safe
    fw = cfg.trim_w - 2 * safe
    ty = bleed + 0.9
    title_lines = wrap_text(cfg.title, "Helvetica-Bold", 30, fw * 72)
    for line in title_lines:
        can.text(fx * 72, ty * 72 + 30, line, 30, METRICS, panel255, "Helvetica-Bold")
        ty += 0.52
    if cfg.subtitle:
        ty += 0.15
        for line in wrap_text(cfg.subtitle, "Helvetica", 14, fw * 72):
            can.text(fx * 72, ty * 72 + 14, line, 14, METRICS, (235, 235, 235))
            ty += 0.26
    # decorative panel
    can.frame_rect((front_x + 0.35) * 72, (bleed + 0.55) * 72,
                   (cfg.trim_w - 0.7) * 72, (cfg.trim_h - 1.1) * 72,
                   panel255, 1.5)
    # author
    aw = METRICS.text_width("Helvetica-Bold", 15, cfg.author.name)
    can.text((front_x + (cfg.trim_w - aw / 72) / 2) * 72,
             (bleed + cfg.trim_h - 0.7) * 72 + 15, cfg.author.name, 15, METRICS,
             panel255, "Helvetica-Bold")

    # ---- spine ------------------------------------------------------------
    if spine_text_ok and spine >= 0.25:
        st = f"{cfg.title[:44]}  —  {cfg.author.name}"
        size = min(11, max(7, spine * 72 * 0.5))
        tw = METRICS.text_width("Helvetica-Bold", size, st)
        max_w = (cfg.trim_h - 0.5) * 72
        if tw > max_w:
            st = st[: max(8, int(len(st) * max_w / tw))]
            tw = METRICS.text_width("Helvetica-Bold", size, st)
        sx = spine_x + spine / 2
        can.text((sx - tw / 72 / 2) * 72, (bleed + 0.45) * 72 + size, st, size,
                 METRICS, panel255, "Helvetica-Bold")

    # ---- back -------------------------------------------------------------
    bx = back_x + safe
    bw = cfg.trim_w - 2 * safe
    by = bleed + 0.8
    blurb = (f"{cfg.title} — {cfg.subtitle or cfg.topic}. Produced and "
             f"validated by an automated publishing pipeline: content, layout, "
             f"pagination, color and cover geometry were each independently "
             f"checked before export.")
    for line in wrap_text(blurb, "Helvetica", 12, bw * 72):
        can.text(bx * 72, by * 72 + 12, line, 12, METRICS, (240, 240, 240))
        by += 0.22
    # barcode reserve zone (§69): 2.0 x 1.2 inches, white, bottom-right
    bar_w, bar_h = ruleset.barcode_zone_in()
    can.fill_rect((back_x + cfg.trim_w - safe - bar_w) * 72,
                  (bleed + cfg.trim_h - safe - bar_h) * 72,
                  bar_w * 72, bar_h * 72, (255, 255, 255))
    can.frame_rect((back_x + cfg.trim_w - safe - bar_w) * 72,
                   (bleed + cfg.trim_h - safe - bar_h) * 72,
                   bar_w * 72, bar_h * 72, (200, 200, 200), 0.8)

    return {
        "canvas": can,
        "spine_in": spine,
        "width_in": w_in,
        "height_in": h_in,
        "page_count": page_count,
        "spine_text_allowed": spine_text_ok,
        "barcode_zone_in": ruleset.barcode_zone_in(),
        "safe_margin_in": safe,
        "ruleset_version": ruleset.version,
        "binding": cfg.binding,
    }


def validate_cover(cover: dict, ruleset, cfg) -> tuple[list, dict]:
    """Deterministic + rendered-pixel checks (§70, §161)."""
    defects = []
    can: Canvas = cover["canvas"]
    ev = {"width_in": cover["width_in"], "height_in": cover["height_in"],
          "spine_in": cover["spine_in"]}

    # dimensions measured from canvas vs formula (§25 independent);
    # tolerance = 1 pixel at cover dpi (raster quantization) + 2 mil
    tol = 1.0 / COVER_DPI + 0.002
    meas_w = can.w / can.scale / 72.0
    meas_h = can.h / can.scale / 72.0
    if abs(meas_w - cover["width_in"]) > tol or abs(meas_h - cover["height_in"]) > tol:
        defects.append(blocking("COVER", "cover-dimension-mismatch", "cover",
                                "recompute and re-render cover",
                                f"canvas {meas_w:.3f}x{meas_h:.3f} vs "
                                f"{cover['width_in']:.3f}x{cover['height_in']:.3f}"))
    # independent formula re-check
    spine = spine_independent(ruleset, cover["page_count"], cfg.color_mode,
                              cfg.paper, cfg.binding)
    w2, h2 = cover_independent(ruleset, cfg.trim_w, cfg.trim_h, spine, cfg.binding)
    if abs(w2 - cover["width_in"]) > 1e-3 or abs(h2 - cover["height_in"]) > 1e-3:
        defects.append(blocking("COVER", "cover-formula-mismatch", "cover",
                                "recalculate cover geometry",
                                f"independent {w2:.3f}x{h2:.3f}"))
    ev["independent_check"] = f"{w2:.4f}x{h2:.4f}"

    # resolution (§70)
    dpi_w = can.w / cover["width_in"]
    if dpi_w < 89.5:
        defects.append(blocking("COVER", "cover-resolution-low", "cover",
                                "render cover at higher dpi", f"{dpi_w:.1f} dpi"))
    ev["dpi"] = round(dpi_w, 1)

    # no white edges: sample the outermost pixel ring (§70)
    ring_white = 0
    ring_n = 0
    for x in range(0, can.w, max(1, can.w // 80)):
        for y in (0, can.h - 1):
            r, g, b = can.get(x, y)
            ring_n += 1
            if r > 245 and g > 245 and b > 245:
                ring_white += 1
    for y in range(0, can.h, max(1, can.h // 80)):
        for x in (0, can.w - 1):
            r, g, b = can.get(x, y)
            ring_n += 1
            if r > 245 and g > 245 and b > 245:
                ring_white += 1
    if ring_n and ring_white / ring_n > 0.05:
        defects.append(blocking("COVER", "white-edges", "cover",
                                "extend background to bleed edge",
                                f"{ring_white / ring_n:.0%} of edge ring white"))

    # barcode zone must stay clear of dense content (§69)
    bleed = ruleset.bleed_in
    safe = cover["safe_margin_in"]
    bar_w, bar_h = cover["barcode_zone_in"]
    bx = (bleed + cfg.trim_w - safe - bar_w)
    by = (bleed + cfg.trim_h - safe - bar_h)
    stats = can.region_stats(bx * 72, by * 72, bar_w * 72, bar_h * 72)
    if stats["samples"] and stats["ink"] / stats["samples"] > 0.20:
        defects.append(blocking("COVER", "barcode-zone-obstructed", "cover",
                                "clear the barcode reserve",
                                f"ink density {stats['ink'] / stats['samples']:.0%}"))

    # spine text rule (§68/§70)
    if not cover["spine_text_allowed"]:
        ev["spine_text"] = "omitted (<79 pages)"
    else:
        ev["spine_text"] = "rendered"

    # title/author presence in front safe zone (§69)
    front_x = bleed + cfg.trim_w + cover["spine_in"]
    zone = can.region_stats((front_x + 0.4) * 72, (bleed + 0.7) * 72,
                            (cfg.trim_w - 0.8) * 72, 3.2 * 72)
    if zone["samples"] and zone["ink"] / zone["samples"] < 0.001:
        defects.append(blocking("COVER", "front-cover-text-missing", "cover",
                                "render title/author on front cover", "no ink in title zone"))
    ev["ok"] = not defects
    return defects, ev
