"""PDF engine (writer half) (spec §72, §162).

Pure-stdlib, deterministic PDF 1.4 writer:
  * exact page sizes from geometry (with/without bleed)
  * base-14 fonts (universally available; embedding policy validated in
    pdfcheck — §34)
  * vector ops: text, rects, lines, polygons, bezier circles/ellipses
  * raster images as Flate-compressed DeviceRGB XObjects
  * metadata (Title/Author/Creator), no encryption (security state validated)

The same display list that renders to pixels is emitted here, so PDF content
and visual QA content cannot drift.
"""
from __future__ import annotations

import zlib

from .renderer import page_offsets_pt

_KAPPA = 0.5522847498

FONT_MAP = {
    "Helvetica": "/F1", "Helvetica-Bold": "/F2", "Helvetica-Oblique": "/F3",
    "Times-Roman": "/F4", "Times-Italic": "/F5", "Courier": "/F6",
}
BASE_FONTS = [
    ("F1", "Helvetica"), ("F2", "Helvetica-Bold"), ("F3", "Helvetica-Oblique"),
    ("F4", "Times-Roman"), ("F5", "Times-Italic"), ("F6", "Courier"),
]


def _pdf_str(s: str) -> str:
    """WinAnsi-safe literal; latin-1 + WinAnsi extras (others pre-flagged)."""
    from .typography import UNICODE_TO_WINANSI
    out = []
    for ch in s:
        if ch in UNICODE_TO_WINANSI:
            out.append(f"\\{UNICODE_TO_WINANSI[ch]:03o}")
            continue
        c = ord(ch)
        if ch in "\\()":
            out.append("\\" + ch)
        elif 32 <= c <= 126:
            out.append(ch)
        elif 160 <= c <= 255:
            out.append(f"\\{c:03o}")
        else:
            out.append("?")  # validated upstream as flagged (§35)
    return "(" + "".join(out) + ")"


def _rgb(color):
    r, g, b = color[:3]
    if isinstance(r, float):
        return f"{r:.3f} {g:.3f} {b:.3f}"
    return f"{r / 255:.3f} {g / 255:.3f} {b / 255:.3f}"


class PDFWriter:
    def __init__(self, metadata: dict):
        self.metadata = metadata
        self.pages = []          # (w_pt, h_pt, ops)
        self.images = {}         # asset_id -> (w_px, h_px, rgb_bytes)
        self.image_pt = {}       # asset_id -> (w_pt, h_pt) native box size

    def add_image(self, asset_id: str, canvas, w_pt=None, h_pt=None):
        self.images[asset_id] = (canvas.w, canvas.h, bytes(canvas.px))
        self.image_pt[asset_id] = (w_pt or canvas.w / canvas.scale,
                                   h_pt or canvas.h / canvas.scale)

    def add_page(self, w_pt: float, h_pt: float, ops: list):
        self.pages.append((w_pt, h_pt, ops))

    # ------------------------------------------------------------------
    def _emit_ops(self, ops, page_h_pt: float, off_x: float, off_y: float,
                  used_fonts: set, used_images: set) -> bytes:
        out = []

        def aw_pt(aid):
            return self.image_pt.get(aid, (0, 0))[0]

        def ah_pt(aid):
            return self.image_pt.get(aid, (0, 0))[1]

        def Tx(x):
            return x + off_x

        def Ty(y):
            return page_h_pt - (y + off_y)

        for op in ops:
            k = op["op"]
            if k == "text":
                used_fonts.add(op.get("font", "Helvetica"))
                f = FONT_MAP[op.get("font", "Helvetica")]
                out.append(f"BT {f} {op['size']:.2f} Tf {_rgb(op['color'])} rg "
                           f"1 0 0 1 {Tx(op['x']):.2f} {Ty(op['y']):.2f} Tm "
                           f"{_pdf_str(op['text'])} Tj ET")
            elif k == "rect":
                out.append(f"{_rgb(op['color'])} rg {Tx(op['x']):.2f} {Ty(op['y'] + op['h']):.2f} "
                           f"{op['w']:.2f} {op['h']:.2f} re f")
            elif k == "frame":
                out.append(f"{_rgb(op['color'])} RG {op['width']:.2f} w "
                           f"{Tx(op['x']):.2f} {Ty(op['y'] + op['h']):.2f} "
                           f"{op['w']:.2f} {op['h']:.2f} re S")
            elif k == "line":
                out.append(f"{_rgb(op['color'])} RG {op['width']:.2f} w "
                           f"{Tx(op['x1']):.2f} {Ty(op['y1']):.2f} m "
                           f"{Tx(op['x2']):.2f} {Ty(op['y2']):.2f} l S")
            elif k == "poly":
                pts = op["pts"]
                if not pts:
                    continue
                seg = [f"{_rgb(op['color'])} " + ("rg" if op.get("filled") else "RG")]
                if not op.get("filled"):
                    seg.append(f"{op.get('width', 1.5):.2f} w")
                seg.append(f"{Tx(pts[0][0]):.2f} {Ty(pts[0][1]):.2f} m")
                for x, y in pts[1:]:
                    seg.append(f"{Tx(x):.2f} {Ty(y):.2f} l")
                if op.get("closed", True):
                    seg.append("h")
                seg.append("f" if op.get("filled") else "S")
                out.append(" ".join(seg))
            elif k == "circle":
                out.append(self._circle(Tx(op["cx"]), Ty(op["cy"]), op["r"],
                                        op["color"], op.get("filled", True),
                                        op.get("width", 1.0)))
            elif k == "ellipse":
                out.append(self._ellipse(Tx(op["x"]), Ty(op["y"] + op["h"]),
                                         op["w"], op["h"], op["color"],
                                         op.get("outline"), op.get("width", 1.0)))
            elif k == "image":
                used_images.add(op["asset_id"])
                w, h = op["w"], op["h"]
                if op.get("full_bleed") and op["asset_id"] in self.images:
                    aw, ah, _ = self.images[op["asset_id"]]
                    # asset px → pt size at its native dpi equals box incl. pad
                    pad_x = (aw_pt(op["asset_id"]) - w) / 2
                    pad_y = (ah_pt(op["asset_id"]) - h) / 2
                    x = off_x - pad_x
                    y_top = op["y"] + off_y - pad_y
                else:
                    x = Tx(op["x"])
                    y_top = op["y"] + off_y
                y = page_h_pt - (y_top + h)
                out.append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm "
                           f"/{op['asset_id'].replace('-', '_')} Do Q")
        return ("\n".join(out)).encode("latin-1")

    def _circle(self, cx, cy, r, color, filled, width):
        k = _KAPPA * r
        fill = "f" if filled else "S"
        head = (f"{_rgb(color)} " + ("rg" if filled else f"RG {width:.2f} w"))
        return (f"{head} {cx + r:.2f} {cy:.2f} m "
                f"{cx + r:.2f} {cy + k:.2f} {cx + k:.2f} {cy + r:.2f} {cx:.2f} {cy + r:.2f} c "
                f"{cx - k:.2f} {cy + r:.2f} {cx - r:.2f} {cy + k:.2f} {cx - r:.2f} {cy:.2f} c "
                f"{cx - r:.2f} {cy - k:.2f} {cx - k:.2f} {cy - r:.2f} {cx:.2f} {cy - r:.2f} c "
                f"{cx + k:.2f} {cy - r:.2f} {cx + r:.2f} {cy - k:.2f} {cx + r:.2f} {cy:.2f} c "
                f"{fill}")

    def _ellipse(self, x, ytop, w, h, fill_color, outline, width):
        cx, cy = x + w / 2, ytop + h / 2
        rx, ry = w / 2, h / 2
        kx, ky = _KAPPA * rx, _KAPPA * ry
        parts = []
        path = (f"{cx + rx:.2f} {cy:.2f} m "
                f"{cx + rx:.2f} {cy + ky:.2f} {cx + kx:.2f} {cy + ry:.2f} {cx:.2f} {cy + ry:.2f} c "
                f"{cx - kx:.2f} {cy + ry:.2f} {cx - rx:.2f} {cy + ky:.2f} {cx - rx:.2f} {cy:.2f} c "
                f"{cx - rx:.2f} {cy - ky:.2f} {cx - kx:.2f} {cy - ry:.2f} {cx:.2f} {cy - ry:.2f} c "
                f"{cx + kx:.2f} {cy - ry:.2f} {cx + rx:.2f} {cy - ky:.2f} {cx + rx:.2f} {cy:.2f} c h")
        if fill_color is not None:
            parts.append(f"{_rgb(fill_color)} rg {path} f")
        if outline is not None:
            parts.append(f"{_rgb(outline)} RG {width:.2f} w {path} S")
        return " ".join(parts)

    # ------------------------------------------------------------------
    def build(self) -> bytes:
        objs: list[bytes] = []          # 1-based; index 0 unused
        page_obj_ids = []
        contents_ids = []

        def add(obj: bytes) -> int:
            objs.append(obj)
            return len(objs)            # object number

        # placeholder: we build linearly
        used_fonts_all: set[str] = set()
        used_images_all: set[str] = set()

        # Reserve object 1 = Catalog, 2 = Pages tree
        objs.append(b"")  # 1
        objs.append(b"")  # 2

        font_ids = {}
        for fid, base in BASE_FONTS:
            oid = add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} "
                      f"/Encoding /WinAnsiEncoding >>".encode("latin-1"))
            font_ids[base] = oid

        image_ids = {}
        for aid, (w, h, rgb) in self.images.items():
            comp = zlib.compress(rgb, 6)
            d = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                 f"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
                 f"/Filter /FlateDecode /Length {len(comp)} >>").encode("latin-1")
            oid = add(b"")
            objs[oid - 1] = d + b"\nstream\n" + comp + b"\nendstream"
            image_ids[aid] = oid

        for (w_pt, h_pt, ops) in self.pages:
            used_fonts, used_images = set(), set()
            # offsets: page index = len(page_obj_ids)
            idx = len(page_obj_ids)
            right_page = idx % 2 == 0
            bleed_off_x = 0.0
            # compute off from page size vs trim? caller passes ops in trim
            # coords; offsets come from renderer's page_offsets_pt via
            # stored attribute if present
            off_x = ops_off_x = getattr(self, "_off_x", [0.0])[idx] if hasattr(self, "_off_x") else 0.0
            off_y = getattr(self, "_off_y", [0.0])[idx] if hasattr(self, "_off_y") else 0.0
            stream = self._emit_ops(ops, h_pt, off_x, off_y, used_fonts, used_images)
            comp = zlib.compress(stream, 6)
            cid = add(b"")
            objs[cid - 1] = (f"<< /Length {len(comp)} /Filter /FlateDecode >>".encode("latin-1")
                             + b"\nstream\n" + comp + b"\nendstream")
            contents_ids.append(cid)
            used_fonts_all |= used_fonts
            used_images_all |= used_images

        # page objects
        kids = []
        for i, (w_pt, h_pt, ops) in enumerate(self.pages):
            res_fonts = {FONT_MAP[f].strip("/"): font_ids[f]
                         for f in {"Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
                                   "Times-Roman", "Times-Italic", "Courier"}}
            font_str = " ".join(f"/{k} {v} 0 R" for k, v in res_fonts.items())
            img_str = " ".join(
                f"/{aid.replace('-', '_')} {image_ids[aid]} 0 R"
                for aid in self.images if aid in used_images_all or True)
            pid = add(b"")
            page_obj_ids.append(pid)
            kids.append(f"{pid} 0 R")
            objs[pid - 1] = (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w_pt:.3f} {h_pt:.3f}] "
                f"/Resources << /Font << {font_str} >> /XObject << {img_str} >> >> "
                f"/Contents {contents_ids[i]} 0 R >>").encode("latin-1")

        objs[1] = (f"<< /Type /Pages /Kids [{' '.join(kids)}] "
                   f"/Count {len(self.pages)} >>").encode("latin-1")
        objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

        # info
        md = self.metadata
        info = add((f"<< /Title {_pdf_str(md.get('title', ''))} "
                    f"/Author {_pdf_str(md.get('author', ''))} "
                    f"/Subject {_pdf_str(md.get('subject', ''))} "
                    f"/Creator (KDP Book Factory 1.0) "
                    f"/Producer (bookfactory.pdfgen/1.0) "
                    f"/CreationDate (D:{md.get('date', '19700101000000Z')}) >>"
                    ).encode("latin-1"))

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
        xref_pos = len(out)
        n = len(objs) + 1
        out += f"xref\n0 {n}\n".encode("latin-1")
        out += b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode("latin-1")
        out += (f"trailer\n<< /Size {n} /Root 1 0 R /Info {info} 0 R >>\n"
                f"startxref\n{xref_pos}\n%%EOF\n").encode("latin-1")
        return bytes(out)


def deterministic_date(seed: int) -> str:
    """Fixed, reproducible PDF date derived from the build seed.

    Real wall-clock timestamps would break byte-for-byte deterministic builds;
    the PDF spec makes /CreationDate optional metadata, so a seed-derived
    stable value keeps identical inputs → identical bytes.
    """
    import datetime
    base = datetime.date(2000, 1, 1) + datetime.timedelta(days=int(seed) % 9490)
    return base.strftime("%Y%m%d") + "000000Z"


def build_interior_pdf(layout, geom, assets: dict, cfg) -> bytes:
    """Emit the interior PDF from a LayoutResult."""
    writer = PDFWriter({
        "title": cfg.title, "author": cfg.author.name,
        "subject": cfg.subtitle or cfg.topic,
        "date": deterministic_date(cfg.seed),
    })
    offs_x, offs_y = [], []
    for page in layout.pages:
        off_x, off_y = page_offsets_pt(page.index, geom)
        offs_x.append(off_x)
        offs_y.append(off_y)
        writer.add_page(geom.page_w_in * 72.0, geom.page_h_in * 72.0, page.ops)
        for op in page.ops:
            if op.get("op") == "image" and op["asset_id"] in assets:
                ent = assets[op["asset_id"]]
                writer.add_image(op["asset_id"], ent["canvas"],
                                 ent.get("box_w_pt"), ent.get("box_h_pt"))
    writer._off_x = offs_x
    writer._off_y = offs_y
    return writer.build()


def build_cover_pdf(canvas, w_in: float, h_in: float, metadata: dict) -> bytes:
    """Single-page PDF at exact cover dimensions (§67, §161)."""
    writer = PDFWriter({**metadata, "subject": "full wrap cover"})
    aid = "cover-art"
    writer.add_image(aid, canvas)
    ops = [{"op": "image", "asset_id": aid, "x": 0, "y": 0,
            "w": w_in * 72.0, "h": h_in * 72.0}]
    writer.add_page(w_in * 72.0, h_in * 72.0, ops)
    writer._off_x = [0.0]
    writer._off_y = [0.0]
    return writer.build()
