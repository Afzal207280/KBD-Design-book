"""Deterministic vector art generation: coloring motifs and story scenes.

Everything is parametric vector output drawn from a seeded RNG — original
art (§15) with guaranteed closed coloring regions (§47).
"""
from __future__ import annotations

import math
import random


def _poly_ring(cx, cy, r, n, phase=0.0):
    return [(cx + r * math.cos(phase + 2 * math.pi * i / n),
             cy + r * math.sin(phase + 2 * math.pi * i / n)) for i in range(n)]


def motif_paths(kind: str, seed: int, w: float, h: float) -> list[dict]:
    """Return list of {'pts': [(x,y)..], 'width': pt} in a w x h box.
    All main shapes are CLOSED polygons → enclosed coloring regions."""
    rng = random.Random(seed)
    paths = []
    cx, cy = w / 2, h / 2
    m = min(w, h)

    if kind == "mandala":
        petals = rng.choice((8, 10, 12))
        for ring, rr in ((0.42, m * 0.42), (0.30, m * 0.30), (0.18, m * 0.18)):
            paths.append({"pts": _poly_ring(cx, cy, rr * ring / 0.42, 24), "width": 2.4})
        for i in range(petals):
            a = 2 * math.pi * i / petals
            pts = []
            for t in [0.25, 0.5, 0.75, 1.0]:
                r1 = m * 0.42 * t
                pts.append((cx + r1 * math.cos(a - 0.12), cy + r1 * math.sin(a - 0.12)))
            for t in [1.0, 0.75, 0.5, 0.25]:
                r1 = m * 0.42 * t
                pts.append((cx + r1 * math.cos(a + 0.12), cy + r1 * math.sin(a + 0.12)))
            paths.append({"pts": pts, "width": 2.0})
    elif kind == "flower":
        paths.append({"pts": _poly_ring(cx, cy + m * 0.30, m * 0.06, 16), "width": 2.4})
        for i in range(6):
            a = math.pi * 2 * i / 6 + rng.uniform(-0.05, 0.05)
            px, py = cx + m * 0.22 * math.cos(a), cy + m * 0.30 + m * 0.22 * math.sin(a)
            paths.append({"pts": _poly_ring(px, py, m * 0.13, 18, a), "width": 2.2})
        stem = [(cx - m * 0.02, cy + m * 0.36), (cx + m * 0.02, cy + m * 0.36),
                (cx + m * 0.02, cy + m * 0.48), (cx - m * 0.02, cy + m * 0.48)]
        paths.append({"pts": stem, "width": 2.0})
        leaf = [(cx + m * 0.02, cy + m * 0.42), (cx + m * 0.16, cy + m * 0.38),
                (cx + m * 0.10, cy + m * 0.46)]
        paths.append({"pts": leaf, "width": 2.0})
    elif kind == "fish":
        body = [(cx - m * 0.30, cy), (cx - m * 0.10, cy - m * 0.18),
                (cx + m * 0.18, cy - m * 0.12), (cx + m * 0.26, cy),
                (cx + m * 0.18, cy + m * 0.12), (cx - m * 0.10, cy + m * 0.18)]
        paths.append({"pts": body, "width": 2.6})
        tail = [(cx + m * 0.26, cy), (cx + m * 0.42, cy - m * 0.14),
                (cx + m * 0.42, cy + m * 0.14)]
        paths.append({"pts": tail, "width": 2.4})
        paths.append({"pts": _poly_ring(cx - m * 0.18, cy - m * 0.04, m * 0.035, 12), "width": 2.0})
        for i in range(3):
            sx = cx - m * 0.02 + i * m * 0.08
            paths.append({"pts": [(sx, cy - m * 0.10), (sx + m * 0.03, cy), (sx, cy + m * 0.10)],
                          "width": 1.8})
        for i, by in enumerate((cy - m * 0.30, cy - m * 0.36, cy - m * 0.42)):
            paths.append({"pts": _poly_ring(cx - m * 0.35 - i * m * 0.04, by,
                                            m * 0.02 + i * m * 0.008, 12), "width": 1.6})
    elif kind == "butterfly":
        for sx in (-1, 1):
            paths.append({"pts": [
                (cx, cy - m * 0.02),
                (cx + sx * m * 0.30, cy - m * 0.28),
                (cx + sx * m * 0.38, cy - m * 0.10),
                (cx + sx * m * 0.16, cy + m * 0.02)], "width": 2.4})
            paths.append({"pts": [
                (cx, cy + m * 0.02),
                (cx + sx * m * 0.26, cy + m * 0.10),
                (cx + sx * m * 0.22, cy + m * 0.30),
                (cx + sx * m * 0.04, cy + m * 0.16)], "width": 2.4})
            paths.append({"pts": _poly_ring(cx + sx * m * 0.20, cy - m * 0.14, m * 0.06, 14),
                          "width": 1.8})
        paths.append({"pts": [(cx - m * 0.035, cy - m * 0.16), (cx + m * 0.035, cy - m * 0.16),
                              (cx + m * 0.035, cy + m * 0.22), (cx - m * 0.035, cy + m * 0.22)],
                      "width": 2.2})
        paths.append({"pts": _poly_ring(cx, cy - m * 0.20, m * 0.05, 14), "width": 2.0})
    elif kind == "rocket":
        paths.append({"pts": [(cx - m * 0.10, cy + m * 0.28), (cx - m * 0.10, cy - m * 0.10),
                              (cx, cy - m * 0.34), (cx + m * 0.10, cy - m * 0.10),
                              (cx + m * 0.10, cy + m * 0.28)], "width": 2.6})
        paths.append({"pts": [(cx - m * 0.10, cy + m * 0.10), (cx - m * 0.24, cy + m * 0.30),
                              (cx - m * 0.10, cy + m * 0.28)], "width": 2.2})
        paths.append({"pts": [(cx + m * 0.10, cy + m * 0.10), (cx + m * 0.24, cy + m * 0.30),
                              (cx + m * 0.10, cy + m * 0.28)], "width": 2.2})
        paths.append({"pts": _poly_ring(cx, cy - m * 0.06, m * 0.06, 16), "width": 2.0})
        paths.append({"pts": [(cx - m * 0.05, cy + m * 0.28), (cx, cy + m * 0.42),
                              (cx + m * 0.05, cy + m * 0.28)], "width": 2.0})
        for i, sx in enumerate((-m * 0.32, m * 0.30)):
            paths.append({"pts": _poly_ring(cx + sx, cy - m * (0.28 - i * 0.1),
                                            m * 0.025, 10), "width": 1.6})
    elif kind == "house":
        paths.append({"pts": [(cx - m * 0.28, cy + m * 0.30), (cx - m * 0.28, cy - m * 0.02),
                              (cx, cy - m * 0.26), (cx + m * 0.28, cy - m * 0.02),
                              (cx + m * 0.28, cy + m * 0.30)], "width": 2.6})
        paths.append({"pts": [(cx - m * 0.07, cy + m * 0.30), (cx - m * 0.07, cy + m * 0.08),
                              (cx + m * 0.07, cy + m * 0.08), (cx + m * 0.07, cy + m * 0.30)],
                      "width": 2.0})
        paths.append({"pts": [(cx - m * 0.22, cy + m * 0.02), (cx - m * 0.22, cy - m * 0.06),
                              (cx - m * 0.12, cy - m * 0.06), (cx - m * 0.12, cy + m * 0.02)],
                      "width": 1.8})
        paths.append({"pts": [(cx + m * 0.12, cy + m * 0.02), (cx + m * 0.12, cy - m * 0.06),
                              (cx + m * 0.22, cy - m * 0.06), (cx + m * 0.22, cy + m * 0.02)],
                      "width": 1.8})
        paths.append({"pts": [(cx + m * 0.10, cy - m * 0.20), (cx + m * 0.16, cy - m * 0.20),
                              (cx + m * 0.16, cy - m * 0.34), (cx + m * 0.10, cy - m * 0.34)],
                      "width": 1.8})
        paths.append({"pts": _poly_ring(cx - m * 0.40, cy - m * 0.30, m * 0.09, 18), "width": 2.0})
    elif kind == "cupcake":
        paths.append({"pts": [(cx - m * 0.22, cy), (cx + m * 0.22, cy),
                              (cx + m * 0.14, cy + m * 0.26), (cx - m * 0.14, cy + m * 0.26)],
                      "width": 2.6})
        paths.append({"pts": _poly_ring(cx - m * 0.12, cy - m * 0.08, m * 0.10, 16), "width": 2.2})
        paths.append({"pts": _poly_ring(cx + m * 0.12, cy - m * 0.08, m * 0.10, 16), "width": 2.2})
        paths.append({"pts": _poly_ring(cx, cy - m * 0.18, m * 0.11, 16), "width": 2.2})
        paths.append({"pts": _poly_ring(cx, cy - m * 0.30, m * 0.03, 10), "width": 1.8})
        for i in range(3):
            x0 = cx - m * 0.10 + i * m * 0.10
            paths.append({"pts": [(x0, cy + m * 0.04), (x0 + m * 0.02, cy + m * 0.24)],
                          "width": 1.4, "open": True})
    else:  # "star"/generic
        n = rng.choice((5, 6, 8))
        outer = _poly_ring(cx, cy, m * 0.40, n, -math.pi / 2)
        inner = _poly_ring(cx, cy, m * 0.18, n, -math.pi / 2 + math.pi / n)
        pts = []
        for o, ip in zip(outer, inner):
            pts.append(o); pts.append(ip)
        paths.append({"pts": pts, "width": 2.6})
        paths.append({"pts": _poly_ring(cx, cy, m * 0.10, 18), "width": 2.0})
    return paths


COLORING_MOTIFS = ("mandala", "flower", "fish", "butterfly", "rocket",
                   "house", "cupcake", "star")


# ---------------------------------------------------------------------------
# Simple story scenes (children's / picture books)
# ---------------------------------------------------------------------------

def render_scene(canvas, box, scene: dict, char_renderer, characters: dict):
    """Draw a parametric scene into box (x,y,w,h in pt). Deterministic."""
    x, y, w, h = box
    sky = scene.get("sky", (0.62, 0.80, 0.94))
    ground = scene.get("ground", (0.55, 0.75, 0.45))
    def C(t): return tuple(int(c * 255) for c in t)
    canvas.fill_rect(x, y, w, h, C(sky))
    gh = h * scene.get("ground_frac", 0.30)
    canvas.fill_rect(x, y + h - gh, w, gh, C(ground))
    if scene.get("sun", True):
        canvas.circle(x + w * 0.82, y + h * 0.16, min(w, h) * 0.07, (250, 210, 80))
    for i, hill in enumerate(scene.get("hills", [])):
        hx = x + w * hill
        pts = [(hx - w * 0.25, y + h - gh), (hx, y + h - gh - h * 0.18),
               (hx + w * 0.25, y + h - gh)]
        canvas.poly(pts, (70, 120, 70), filled=True)
    if scene.get("tree"):
        tx, ty = x + w * 0.12, y + h - gh
        canvas.fill_rect(tx - w * 0.012, ty - h * 0.16, w * 0.024, h * 0.16, (110, 75, 40))
        canvas.circle(tx, ty - h * 0.20, min(w, h) * 0.09, (60, 130, 60))
    if scene.get("house"):
        hx, hy = x + w * 0.80, y + h - gh
        hw2, hh2 = w * 0.16, h * 0.14
        canvas.fill_rect(hx - hw2 / 2, hy - hh2, hw2, hh2, (220, 190, 150))
        canvas.poly([(hx - hw2 / 2 - w * 0.02, hy - hh2), (hx, hy - hh2 - h * 0.08),
                     (hx + hw2 / 2 + w * 0.02, hy - hh2)], (160, 60, 50), filled=True)
    # characters placed left→right (reading order)
    n = len(scene.get("chars", []))
    for i, cid in enumerate(scene.get("chars", [])):
        spec = characters.get(cid)
        if spec is None:
            raise KeyError(f"scene references unknown character {cid}")
        cx = x + w * (0.30 + 0.40 * i / max(1, n - 1)) if n > 1 else x + w * 0.45
        ch_h = min(w, h) * scene.get("char_frac", 0.34)
        char_renderer(canvas, cx, y + h - gh, ch_h, spec)
    return canvas
