"""Visual QA engine (spec §29, §35, §73, §74, §75, §113, §147, §157, §209).

Every page is rasterized and measured (bounding boxes, ink in margins,
contrast, collisions, color classification, blank detection, bleed reach).
Deterministic oracles; no "looks good" judgments (§208).
"""
from __future__ import annotations

import os

from .renderer import render_page
from .errors import blocking, warning, Severity

INK_BAND_MAX_RATIO = 0.004       # stray ink allowed in margin bands
BLANK_MAX_INK = 0.0008           # page considered blank below this
CONTRAST_MIN = 90.0              # min luminance gap text-vs-background
TEXT_OVERLAP_MAX = 0.35          # max bbox overlap fraction between regions


def _boxes_overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    return inter / max(1e-9, min(aw * ah, bw * bh))


def validate_visual(layout, geom, assets, cfg, project_dir: str,
                    qa_dpi: int = 36, evidence_dpi: int = 60,
                    color_validator=None) -> tuple[list, dict]:
    """Returns (defects, evidence_summary). Renders EVERY page (§75)."""
    defects = []
    evidence = {"pages_checked": 0, "pages": {}, "evidence_files": []}
    chromatic_pages_seen = [0]
    renders_dir = os.path.join(project_dir, "renders")
    os.makedirs(renders_dir, exist_ok=True)

    n = len(layout.pages)
    evidence_indices = {0, n - 1, n // 2}
    seen_types = set()

    prev_page_number = 0
    for page in layout.pages:
        idx = page.index
        meta = page.meta
        force_gray = cfg.color_mode in ("BLACK_AND_WHITE", "GRAYSCALE")
        if cfg.color_mode == "MIXED_COLOR" and meta.expected_color_mode != "FULL_COLOR":
            force_gray = True
        can = render_page(page, geom, assets, dpi=qa_dpi, force_gray=force_gray)
        stats = can.classify_color_content()
        evidence["pages_checked"] += 1

        page_def = []

        # ---- 1. blank page detection (§74 unexpected blanks) ----------
        # oracle: a page is blank iff it carries no content ops (page-number
        # footer ops don't count); ink threshold is the secondary measure.
        intentional_blank = meta.page_type == "blank"
        content_ops = [op for op in page.ops if not op.get("footer")]
        if not content_ops and not intentional_blank:
            page_def.append(("unexpected-blank", f"page {idx + 1} has no content ops"))
        elif (content_ops and stats["nonwhite_ratio"] < BLANK_MAX_INK / 4
              and not intentional_blank):
            page_def.append(("render-failure",
                             f"page {idx + 1} has ops but rendered nothing"))

        # ---- 2. color validation against rendered pixels (§29) --------
        expected = meta.expected_color_mode
        if expected == "BLACK_AND_WHITE":
            if stats["chromatic_ratio"] > 0.002:
                page_def.append(("unexpected-color",
                                 f"page {idx + 1}: B&W intent but "
                                 f"{stats['chromatic_ratio']:.3%} chromatic pixels"))
        elif expected == "GRAYSCALE":
            if stats["chromatic_ratio"] > 0.002:
                page_def.append(("unexpected-color", f"page {idx + 1}: grayscale intent"))
        elif expected == "FULL_COLOR":
            # only art pages MUST carry chroma; text-only pages may be plain
            has_art = bool(meta.image_regions) or bool(meta.bleed_regions)
            if has_art and stats["chromatic_ratio"] < 0.01:
                page_def.append(("missing-color",
                                 f"page {idx + 1}: art page rendered without color"))
            if stats["chromatic_ratio"] > 0.01:
                chromatic_pages_seen[0] += 1
        meta.actual_color_mode = ("FULL_COLOR" if stats["chromatic_ratio"] > 0.01
                                  else ("GRAYSCALE" if stats["gray_levels"] > 6
                                        else "BLACK_AND_WHITE"))
        meta.actual_assets = meta.expected_assets

        # ---- 3. margin safety: ink inside gutter/outside margins -------
        if not meta.bleed_regions:  # non-bleed pages must keep margins clean
            band = _margin_band_stats(can, geom, page.index)
            if band > INK_BAND_MAX_RATIO:
                page_def.append(("margin-violation",
                                 f"page {idx + 1}: ink in margin band {band:.4%}"))
        else:
            # bleed pages: ink must actually REACH the physical edge (§74)
            reach = _edge_reach(can, geom, page.index)
            if reach < 0.5:
                page_def.append(("bleed-not-reaching-edge",
                                 f"page {idx + 1}: full-bleed art stops short "
                                 f"(edge coverage {reach:.0%})"))

        # ---- 4. text safety: regions within safe area (§35) ------------
        safe = (geom.content_x_in - 0.02, geom.content_y_in - 0.02,
                geom.content_w_in + 0.04, geom.content_h_in + 0.04)
        for (rx, ry, rw, rh) in meta.text_regions:
            if rw <= 0 or rh <= 0:
                continue
            if (rx < safe[0] - 0.01 or ry < safe[1] - 0.01 or
                    rx + rw > safe[0] + safe[2] + 0.01 or
                    ry + rh > safe[1] + safe[3] + 0.03):
                page_def.append(("text-outside-safe-area",
                                 f"page {idx + 1}: text region at "
                                 f"({rx:.2f},{ry:.2f}) outside safe area"))
        # footer (page-number) zone: allowed inside the bottom margin only
        for (fx, fy, fw, fh) in meta.footer_regions:
            if fy + fh > geom.trim_h_in + 0.01 or fy < geom.trim_h_in - geom.bottom_in - 0.3:
                page_def.append(("footer-misplaced",
                                 f"page {idx + 1}: page number outside footer zone"))
        # ---- 5. text collisions (§35, §74) ------------------------------
        regs = [r for r in meta.text_regions if r[2] > 0.4 and r[3] > 0.1]
        for i in range(len(regs)):
            for j in range(i + 1, min(i + 6, len(regs))):
                ov = _boxes_overlap(regs[i], regs[j])
                if ov > TEXT_OVERLAP_MAX:
                    page_def.append(("text-collision",
                                     f"page {idx + 1}: text overlap {ov:.0%}"))

        # ---- 6. contrast (§29, §65) --------------------------------------
        if meta.text_regions and stats["nonwhite_ratio"] > 0.001:
            c = _contrast(can, geom)
            if c < CONTRAST_MIN:
                page_def.append(("low-contrast", f"page {idx + 1}: contrast {c:.0f}"))

        # ---- 7. asset presence (§74 missing assets) -----------------------
        for aid in meta.expected_assets:
            if aid not in assets:
                page_def.append(("missing-asset", f"page {idx + 1}: {aid}"))

        # ---- record --------------------------------------------------------
        if page_def:
            for kind, detail in page_def:
                sev = blocking
                if kind in ("low-contrast",):
                    sev = warning
                defects.append(sev("VISUAL_QA", kind, meta.page_id,
                                   "re-layout or repair the offending page", detail))
        evidence["pages"][idx + 1] = {
            "type": meta.page_type, "expected_color": expected,
            "actual_color": meta.actual_color_mode,
            "nonwhite": round(stats["nonwhite_ratio"], 5),
            "chromatic": round(stats["chromatic_ratio"], 5),
            "problems": [d[0] for d in page_def],
        }

        # ---- evidence screenshots (§147) -----------------------------------
        if idx in evidence_indices or meta.page_type not in seen_types:
            seen_types.add(meta.page_type)
            ev = render_page(page, geom, assets, dpi=evidence_dpi, force_gray=force_gray)
            fn = os.path.join(renders_dir, f"evidence-{idx + 1:04d}.png")
            with open(fn, "wb") as f:
                f.write(ev.png())
            evidence["evidence_files"].append(os.path.basename(fn))

    # ---- book-level color intent (§27) ---------------------------------
    if cfg.color_mode == "FULL_COLOR" and chromatic_pages_seen[0] == 0:
        defects.append(blocking("VISUAL_QA", "no-chromatic-page", "layout",
                                "verify illustration pipeline (§29)",
                                "FULL_COLOR book contains no chromatic page"))

    # ---- pagination-level checks (§23) --------------------------------
    page_numbers = [p.index + 1 for p in layout.pages]
    if page_numbers != list(range(1, len(layout.pages) + 1)):
        defects.append(blocking("VISUAL_QA", "page-sequence-broken", "layout",
                                "fix pagination", "page indices not sequential"))
    # duplicate rendered pages (identical ops) — excluding intentional repeats
    seen_hashes = {}
    import hashlib, json
    for p in layout.pages:
        if p.meta.page_type in ("chapter", "text"):
            h = hashlib.sha256(json.dumps(p.ops, default=str).encode()).hexdigest()
            if h in seen_hashes and p.meta.page_type == "text":
                defects.append(warning("VISUAL_QA", "duplicate-page-content",
                                       p.meta.page_id, "review duplicated content",
                                       f"page {p.index + 1} identical to {seen_hashes[h]}"))
            seen_hashes.setdefault(h, p.index + 1)

    evidence["widow_orphan_repairs"] = layout.widow_orphan_repairs
    return defects, evidence


def _margin_band_stats(can, geom, index) -> float:
    """Fraction of inked pixels inside the outside margin bands."""
    from .renderer import page_offsets_pt
    off_x, off_y = page_offsets_pt(index, geom)
    g_in = geom.gutter_in
    o_in = geom.outside_in
    w, h = can.w, can.h
    s = can.scale
    ink = 0
    total = 0
    # top band
    top_px = int((off_y / 72.0 + (geom.top_in - off_y / 72.0) * 0) * 0)  # keep simple below
    bands = []
    top_h = int((geom.top_in * 0.66) * s)
    bot_h = int((geom.bottom_in * 0.66) * s)
    gutter_w = int((g_in * 0.66) * s)
    outside_w = int((o_in * 0.66) * s)
    right_page = index % 2 == 0
    bands.append((0, 0, w, top_h))
    bands.append((0, h - bot_h, w, bot_h))
    if right_page:
        bands.append((0, 0, gutter_w, h))          # inside = left
        bands.append((w - outside_w, 0, outside_w, h))
    else:
        bands.append((0, 0, outside_w, h))
        bands.append((w - gutter_w, 0, gutter_w, h))
    for (x0, y0, bw, bh) in bands:
        for yy in range(max(0, y0), min(h, y0 + bh), max(1, bh // 60 or 1)):
            for xx in range(max(0, x0), min(w, x0 + bw), max(1, bw // 60 or 1)):
                off = (yy * w + xx) * 3
                if max(can.px[off], can.px[off + 1], can.px[off + 2]) < 240:
                    ink += 1
                total += 1
    return ink / total if total else 0.0


def _edge_reach(can, geom, index) -> float:
    """For bleed pages: fraction of physical edge pixels carrying ink."""
    w, h = can.w, can.h
    def is_white(off):
        r, g, b = can.px[off], can.px[off + 1], can.px[off + 2]
        return r > 245 and g > 245 and b > 245

    samples = 0
    ink = 0
    for xx in range(0, w, max(1, w // 100)):
        for yy in (0, h - 1):
            off = (yy * w + xx) * 3
            if not is_white(off):
                ink += 1
            samples += 1
    for yy in range(0, h, max(1, h // 100)):
        for xx in (0, w - 1):
            off = (yy * w + xx) * 3
            if not is_white(off):
                ink += 1
            samples += 1
    return ink / samples if samples else 0.0


def _contrast(can, geom) -> float:
    """Luminance gap between darkest ink and lightest background (0..255)."""
    dark = 255
    light = 0
    px = can.px
    step = max(1, len(px) // (3 * 150000))
    for i in range(0, len(px), 3 * step):
        lum = (px[i] + px[i + 1] + px[i + 2]) // 3
        if lum < dark:
            dark = lum
        if lum > light:
            light = lum
    return float(light - dark)
