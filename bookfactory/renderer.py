"""Render engine (spec §73, §74): rasterizes every page's display list to
real pixels. Used for visual QA and for preview evidence (§147). Same ops
feed the PDF writer, so layout → PDF → raster is one consistent pipeline.
"""
from __future__ import annotations

import math

from .raster import Canvas
from .typography import METRICS

_KAPPA = 0.5522847498


def page_offsets_pt(page_index: int, geom) -> tuple[float, float]:
    """Trim-origin offset inside the physical (with-bleed) page, in pt."""
    bleed_pt = geom.bleed_in * 72.0
    if bleed_pt == 0:
        return 0.0, 0.0
    right_page = (page_index % 2 == 0)  # page 1 recto
    off_x = 0.0 if right_page else bleed_pt   # bleed on outside edge only
    return off_x, bleed_pt


def _to_gray(rgb):
    r, g, b = rgb[:3]
    lum = int(0.2126 * r + 0.7152 * g + 0.0722 * b)
    return (lum, lum, lum)


def render_page(page, geom, assets: dict, dpi: int = 72,
                force_gray=False) -> Canvas:
    """Render one PageLayout. force_gray converts to luminance (§31)."""
    w_pt = geom.page_w_in * 72.0
    h_pt = geom.page_h_in * 72.0
    can = Canvas(w_pt, h_pt, dpi=dpi)
    off_x, off_y = page_offsets_pt(page.index, geom)

    def T(xy):
        return (xy[0] + off_x, xy[1] + off_y)

    def C(color):
        rgb = tuple(int(round(min(1.0, max(0.0, c)) * 255)) for c in color[:3])
        return _to_gray(rgb) if force_gray else rgb

    for op in page.ops:
        k = op["op"]
        if k == "rect":
            x, y = T((op["x"], op["y"]))
            col = C(op["color"])
            can.fill_rect(x, y, op["w"], op["h"], col)
        elif k == "frame":
            x, y = T((op["x"], op["y"]))
            can.frame_rect(x, y, op["w"], op["h"], C(op["color"]), op["width"])
        elif k == "line":
            x1, y1 = T((op["x1"], op["y1"]))
            x2, y2 = T((op["x2"], op["y2"]))
            can.line(x1, y1, x2, y2, C(op["color"]), op["width"])
        elif k == "poly":
            pts = [T(p) for p in op["pts"]]
            can.poly(pts, C(op["color"]), op.get("width", 1.5),
                     closed=op.get("closed", True), filled=op.get("filled", False))
        elif k == "circle":
            cx, cy = T((op["cx"], op["cy"]))
            can.circle(cx, cy, op["r"], C(op["color"]), filled=op.get("filled", True),
                       width_pt=op.get("width", 1.0))
        elif k == "ellipse":
            _ellipse(can, *T((op["x"], op["y"])), op["w"], op["h"],
                     C(op["color"]), outline=op.get("outline"),
                     width_pt=op.get("width", 1.0))
        elif k == "text":
            x, y = T((op["x"], op["y"]))
            can.text(x, y, op["text"], op["size"], METRICS,
                     color=C(op["color"]), font=op.get("font", "Helvetica"))
        elif k == "image":
            ent = assets.get(op["asset_id"])
            if ent is None:
                # missing asset → draw an explicit red X marker (§74)
                x, y = T((op["x"], op["y"]))
                can.frame_rect(x, y, op["w"], op["h"], (200, 30, 30), 2.0)
                can.line(x, y, x + op["w"], y + op["h"], (200, 30, 30), 2.0)
                can.line(x + op["w"], y, x, y + op["h"], (200, 30, 30), 2.0)
                continue
            img: Canvas = ent["canvas"]
            if force_gray:
                img = _gray_copy(img)
            x, y = T((op["x"], op["y"]))
            if op.get("full_bleed"):
                # asset carries bleed padding on all sides; anchor so the trim
                # area aligns with the trim box (clipped at physical edges)
                pad_x = (img.w / img.scale - geom.trim_w_in * 72.0) / 2
                pad_y = (img.h / img.scale - geom.trim_h_in * 72.0) / 2
                can.blit(img, off_x - pad_x, off_y - pad_y,
                         img.w / img.scale, img.h / img.scale)
            else:
                can.blit(img, x, y, op["w"], op["h"])
    return can


def _gray_copy(img: Canvas) -> Canvas:
    out = Canvas(img.w / img.scale, img.h / img.scale, dpi=img.dpi)
    px = bytearray(img.px)
    for i in range(0, len(px), 3):
        lum = int(0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2])
        px[i] = px[i + 1] = px[i + 2] = lum
    out.px = px
    return out


def _ellipse(can: Canvas, x, y, w, h, fill_color, outline=None, width_pt=1.0):
    cx, cy = x + w / 2, y + h / 2
    n = 40
    pts = [(cx + w / 2 * math.cos(2 * math.pi * i / n),
            cy + h / 2 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    if fill_color is not None:
        can.poly(pts, fill_color, filled=True)
    if outline is not None:
        oc = tuple(int(round(min(1.0, max(0.0, c)) * 255)) for c in outline[:3])
        can.poly(pts, oc, closed=True, width_pt=width_pt)
