"""Software rasterizer + PNG codec (spec §73, §74, §75, §147, §209).

Renders display lists (layout output) to pixel buffers so visual validation
is performed on ACTUAL rendered pixels, never metadata (§113, §157).
Pure-stdlib, deterministic.
"""
from __future__ import annotations

import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """rgb = row-major W*H*3 bytes."""
    if len(rgb) != width * height * 3:
        raise ValueError("rgb buffer size mismatch")
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 6)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", comp) + _chunk(b"IEND", b"")


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    """Minimal PNG reader (8-bit RGB/RGBA), used to verify written PNGs and
    to inspect external images in stress tests. Raises on corruption."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")
    pos = 8
    w = h = None
    bitdepth = ctype = None
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4]); pos += 4
        tag = data[pos:pos + 4]; pos += 4
        body = data[pos:pos + length]; pos += length + 4
        if tag == b"IHDR":
            w, h, bitdepth, ctype = struct.unpack(">IIBB", body[:10])
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if w is None or bitdepth != 8 or ctype not in (2, 6):
        raise ValueError("unsupported PNG (need 8-bit RGB/RGBA)")
    comp = zlib.decompress(bytes(idat))
    ch = 3 if ctype == 2 else 4
    stride = w * ch
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(h):
        f = comp[pos]; pos += 1
        line = bytearray(comp[pos:pos + stride]); pos += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        elif f != 0:
            raise ValueError(f"bad PNG filter {f}")
        if ctype == 2:
            out += line
        else:  # RGBA -> RGB
            out += bytes(line[i] for i in range(len(line)) if i % 4 != 3)
        prev = bytearray(line)
    return w, h, bytes(out)


class Canvas:
    """RGB pixel canvas. Coordinates in POINTS (72/in); scale = dpi/72."""

    def __init__(self, width_pt: float, height_pt: float, dpi: int = 72,
                 background=(255, 255, 255)):
        self.dpi = dpi
        self.scale = dpi / 72.0
        self.w = max(1, int(round(width_pt * self.scale)))
        self.h = max(1, int(round(height_pt * self.scale)))
        self.px = bytearray([255]) * (self.w * self.h * 3)
        if background != (255, 255, 255):
            self.fill_rect(0, 0, width_pt, height_pt, background)

    # -- primitives ----------------------------------------------------
    def _px(self, x: int, y: int):
        return 0 <= x < self.w and 0 <= y < self.h

    def fill_rect(self, x, y, w, h, color):
        import math
        x0, y0 = int(x * self.scale), int(y * self.scale)
        # ceil the far edge so full-bleed fills reach the last pixel row/col
        x1 = int(math.ceil((x + w) * self.scale))
        y1 = int(math.ceil((y + h) * self.scale))
        x0, x1 = max(0, min(x0, x1)), min(self.w, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(self.h, max(y0, y1))
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for yy in range(y0, y1):
            off = (yy * self.w + x0) * 3
            self.px[off:off + (x1 - x0) * 3] = row

    def frame_rect(self, x, y, w, h, color, width_pt=1.0):
        self.fill_rect(x, y, w, width_pt, color)
        self.fill_rect(x, y + h - width_pt, w, width_pt, color)
        self.fill_rect(x, y, width_pt, h, color)
        self.fill_rect(x + w - width_pt, y, width_pt, h, color)

    def hline(self, x, y, w, color, width_pt=1.0):
        self.fill_rect(x, y, w, width_pt, color)

    def line(self, x1, y1, x2, y2, color, width_pt=1.0):
        sx, sy = self.scale, self.scale
        x1, y1, x2, y2 = x1 * sx, y1 * sy, x2 * sx, y2 * sy
        dx, dy = x2 - x1, y2 - y1
        steps = int(max(abs(dx), abs(dy), 1))
        r = max(1, int(width_pt * sx / 2))
        for i in range(steps + 1):
            t = i / steps
            cx, cy = int(x1 + dx * t), int(y1 + dy * t)
            for ox in range(-r, r + 1):
                for oy in range(-r, r + 1):
                    xx, yy = cx + ox, cy + oy
                    if self._px(xx, yy):
                        off = (yy * self.w + xx) * 3
                        self.px[off:off + 3] = bytes(color)

    def poly(self, pts, color, width_pt=1.0, closed=True, filled=False):
        if filled:
            self._fill_poly(pts, color)
        else:
            n = len(pts)
            for i in range(n - 1):
                self.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], color, width_pt)
            if closed and n > 2:
                self.line(pts[-1][0], pts[-1][1], pts[0][0], pts[0][1], color, width_pt)

    def _fill_poly(self, pts, color):
        sx, sy = self.scale, self.scale
        P = [(x * sx, y * sy) for x, y in pts]
        if len(P) < 3:
            return
        ymin = max(0, int(min(p[1] for p in P)))
        ymax = min(self.h - 1, int(max(p[1] for p in P)))
        n = len(P)
        for yy in range(ymin, ymax + 1):
            xs = []
            for i in range(n):
                x1, y1 = P[i]
                x2, y2 = P[(i + 1) % n]
                if (y1 <= yy < y2) or (y2 <= yy < y1):
                    if y2 != y1:
                        xs.append(x1 + (yy - y1) * (x2 - x1) / (y2 - y1))
            xs.sort()
            for j in range(0, len(xs) - 1, 2):
                x0, x1 = int(xs[j]), int(xs[j + 1])
                for xx in range(max(0, x0), min(self.w, x1 + 1)):
                    off = (yy * self.w + xx) * 3
                    self.px[off:off + 3] = bytes(color)

    def circle(self, cx, cy, r, color, filled=True, width_pt=1.0):
        cxp, cyp = cx * self.scale, cy * self.scale
        rp = r * self.scale
        if filled:
            r2 = rp * rp
            x0, x1 = max(0, int(cxp - rp)), min(self.w - 1, int(cxp + rp))
            y0, y1 = max(0, int(cyp - rp)), min(self.h - 1, int(cyp + rp))
            for yy in range(y0, y1 + 1):
                for xx in range(x0, x1 + 1):
                    if (xx - cxp) ** 2 + (yy - cyp) ** 2 <= r2:
                        off = (yy * self.w + xx) * 3
                        self.px[off:off + 3] = bytes(color)
        else:
            import math
            steps = max(12, int(rp * 4))
            for i in range(steps):
                a1 = 2 * math.pi * i / steps
                a2 = 2 * math.pi * (i + 1) / steps
                self.line(cx + r * math.cos(a1), cy + r * math.sin(a1),
                          cx + r * math.cos(a2), cy + r * math.sin(a2),
                          color, width_pt)

    def blit(self, img: "Canvas", x, y, w_pt=None, h_pt=None):
        """Scale-draw another canvas/image onto this one."""
        dw = int((w_pt or img.w / img.scale) * self.scale)
        dh = int((h_pt or img.h / img.scale) * self.scale)
        dx0, dy0 = int(x * self.scale), int(y * self.scale)
        if dw <= 0 or dh <= 0:
            return
        for yy in range(dh):
            ty = dy0 + yy
            if ty < 0 or ty >= self.h:
                continue
            sy = int(yy * img.h / dh)
            if sy >= img.h:
                sy = img.h - 1
            for xx in range(dw):
                tx = dx0 + xx
                if tx < 0 or tx >= self.w:
                    continue
                sxp = int(xx * img.w / dw)
                if sxp >= img.w:
                    sxp = img.w - 1
                soff = (sy * img.w + sxp) * 3
                toff = (ty * self.w + tx) * 3
                self.px[toff:toff + 3] = img.px[soff:soff + 3]

    # -- text (measured glyph boxes; deterministic ink model) -----------
    def text(self, x, y, text, font_size_pt, metrics, color=(0, 0, 0),
             font="Helvetica"):
        """Draw text as measured glyph ink boxes. Returns total width (pt)."""
        cx = x
        ascent = font_size_pt * 0.75
        for ch in text:
            wpt = metrics.char_width(font, font_size_pt, ch)
            if ch != " ":
                # glyph ink box ~ 0.55 of advance width, cap height 0.7em
                gw = max(0.5, wpt * 0.62)
                gh = font_size_pt * 0.66 if ch.isupper() or ch.isdigit() else font_size_pt * 0.48
                gy = y - ascent + (font_size_pt * 0.70 - gh)
                self.fill_rect(cx + wpt * 0.12, gy, gw, gh, color)
            cx += wpt
        return cx - x

    # -- output ----------------------------------------------------------
    def png(self) -> bytes:
        return encode_png(self.w, self.h, bytes(self.px))

    # -- analysis --------------------------------------------------------
    def get(self, x, y):
        off = (y * self.w + x) * 3
        return tuple(self.px[off:off + 3])

    def classify_color_content(self) -> dict:
        """§27/§29: classify ACTUAL pixel content: how much chroma exists.
        Returns counts used by color validation."""
        n = self.w * self.h
        chromatic = 0
        nonwhite = 0
        nonblack_gray_levels = set()
        step = max(1, n // 200000)  # cap work on huge canvases
        i = 0
        px = self.px
        for i in range(0, len(px), 3 * step):
            r, g, b = px[i], px[i + 1], px[i + 2]
            mx, mn = max(r, g, b), min(r, g, b)
            if mx - mn > 12:            # visible chroma
                chromatic += 1
            if mx > 245 and mn > 245:
                continue
            nonwhite += 1
            nonblack_gray_levels.add((r + g + b) // 3)
        sampled = len(range(0, len(px), 3 * step)) or 1
        return {
            "sampled": sampled,
            "chromatic": chromatic,
            "chromatic_ratio": chromatic / sampled,
            "nonwhite_ratio": nonwhite / sampled,
            "gray_levels": len(nonblack_gray_levels),
        }

    def region_stats(self, x, y, w, h) -> dict:
        x0, y0 = int(x * self.scale), int(y * self.scale)
        x1, y1 = int((x + w) * self.scale), int((y + h) * self.scale)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.w, x1), min(self.h, y1)
        if x1 <= x0 or y1 <= y0:
            return {"ink": 0, "samples": 0}
        ink = 0
        samples = 0
        for yy in range(y0, y1, max(1, (y1 - y0) // 120)):
            for xx in range(x0, x1, max(1, (x1 - x0) // 120)):
                off = (yy * self.w + xx) * 3
                r, g, b = self.px[off], self.px[off + 1], self.px[off + 2]
                if max(r, g, b) < 235:
                    ink += 1
                samples += 1
        return {"ink": ink, "samples": samples or 1}
