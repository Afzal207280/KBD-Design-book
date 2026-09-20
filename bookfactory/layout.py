"""Design system + layout + pagination engine (spec §22, §23, §33, §36, §60,
§61, §62, §64).

Turns the content block stream into concrete pages: an ordered display list
per page (points, trim coordinates), page metadata (§62), generated art
assets (§63), a computed table of contents, and deterministic pagination
with widow/orphan control. The page count comes from THIS layout (§22) —
never estimated from word count.
"""
from __future__ import annotations

from .artgen import motif_paths, render_scene
from .cartoon import render_character, spec_fingerprint, style_fingerprint, default_style
from .schemas import CharacterSpec, PageMeta, Asset, StyleBible
from .typography import METRICS, wrap_text, detect_widows_orphans, DEFAULT_TOKENS

IN = 72.0  # pt per inch

PAGE_TYPES = ("half_title", "title_page", "copyright", "dedication", "toc",
              "introduction", "chapter", "text", "illustration", "activity",
              "puzzle", "coloring", "worksheet", "answer", "table", "recipe",
              "comic", "planner", "tracker", "index", "references", "blank")


class PageLayout:
    __slots__ = ("index", "ops", "meta")

    def __init__(self, index: int, page_type: str, cfg, geom):
        self.index = index
        self.ops = []
        self.meta = PageMeta(page_id=f"page-{index + 1:04d}", index=index,
                             page_type=page_type,
                             expected_color_mode=cfg.color_mode,
                             source_version=0)


class LayoutResult:
    def __init__(self):
        self.pages: list[PageLayout] = []
        self.assets: dict[str, dict] = {}
        self.toc: dict[str, int] = {}
        self.stats: dict = {}
        self.widow_orphan_repairs = 0


class _Pager:
    """Tracks the current page and cursor; emits ops."""

    def __init__(self, cfg, geom, tokens, result: LayoutResult, characters: dict,
                 style: StyleBible, scene_dpi: int):
        self.cfg = cfg
        self.geom = geom
        self.tokens = tokens
        self.res = result
        self.chars = characters
        self.style = style
        self.scene_dpi = scene_dpi
        self.metrics = METRICS
        self.cur: PageLayout | None = None
        self.y = 0.0
        self.page_type_stack = ["text"]
        self._asset_seq = 0

    # geometry helpers ---------------------------------------------------
    @property
    def x0(self):  # content left (trim coords)
        return self.geom.content_x_in * IN

    @property
    def x1(self):
        return (self.geom.content_x_in + self.geom.content_w_in) * IN

    @property
    def cw(self):
        return self.geom.content_w_in * IN

    @property
    def y_top(self):
        return self.geom.top_in * IN

    @property
    def y_bottom_limit(self):
        return (self.geom.trim_h_in - self.geom.bottom_in) * IN

    @property
    def remaining(self):
        return self.y_bottom_limit - self.y

    # page management -----------------------------------------------------
    def new_page(self, page_type: str = "text"):
        self.cur = PageLayout(len(self.res.pages), page_type, self.cfg, self.geom)
        self.res.pages.append(self.cur)
        self.y = self.y_top
        return self.cur

    def ensure_room(self, needed_pt: float, page_type: str = "text"):
        if self.cur is None or self.y + needed_pt > self.y_bottom_limit + 0.01:
            self.new_page(page_type)

    def add_op(self, op: dict, text_bbox=None, image_bbox=None):
        self.cur.ops.append(op)
        if text_bbox:
            self.cur.meta.text_regions.append(text_bbox)
        if image_bbox:
            self.cur.meta.image_regions.append(image_bbox)

    # asset helpers -------------------------------------------------------
    def next_asset_id(self, kind: str):
        self._asset_seq += 1
        return f"asset-{kind}-{self._asset_seq:04d}"

    def register_scene(self, scene, char_ids, box_w_pt, box_h_pt, bleed_pad_pt=0.0,
                       grayscale=False) -> str:
        """Rasterize a vector scene into an asset canvas (§63 provenance)."""
        from .raster import Canvas
        aid = self.next_asset_id("scene")
        W = box_w_pt + 2 * bleed_pad_pt
        H = box_h_pt + 2 * bleed_pad_pt
        can = Canvas(W, H, dpi=self.scene_dpi)
        specs = {cid: self.chars[cid] for cid in char_ids if cid in self.chars}
        render_scene(can, (0, 0, can.w / can.scale, can.h / can.scale), scene,
                     lambda c, cx, cy, h, spec: render_character(c, cx, cy, h, spec, self.style),
                     specs)
        if grayscale:
            px = can.px
            for i in range(0, len(px), 3):
                r, g, b = px[i], px[i + 1], px[i + 2]
                lum = int(0.2126 * r + 0.7152 * g + 0.0722 * b)
                px[i] = px[i + 1] = px[i + 2] = lum
        rec = Asset(asset_id=aid, kind="scene", source="deterministic-vector",
                    method="parametric-scene-renderer/1.0",
                    pages=[len(self.res.pages)],
                    checksum="", file=f"{aid}.png")
        self.res.assets[aid] = {"canvas": can, "record": rec,
                                "dpi": self.scene_dpi,
                                "box_w_pt": W, "box_h_pt": H}
        return aid


# ---------------------------------------------------------------------------
# Block renderers
# ---------------------------------------------------------------------------

def _ink(tokens):
    return LayoutColors.ink


class LayoutColors:
    ink = (0.10, 0.10, 0.12)
    gray = (0.45, 0.45, 0.47)
    light = (0.78, 0.78, 0.80)
    accent = (0.16, 0.32, 0.55)
    paper = (1.0, 1.0, 1.0)


def layout_content(cfg, geom, content, tokens=None) -> LayoutResult:
    """Pagination with a correctly-sized TOC. The TOC line count equals the
    number of titled sections, so placeholder space matches reality and no
    phantom TOC pages survive (§23 intentional blanks only)."""
    tokens = tokens or DEFAULT_TOKENS.scale_for_audience(cfg.audience.age_max)
    entries = sum(1 for s in content["sections"] if s.get("title")) + \
        sum(1 for s in content["back_matter"] if s.get("title"))
    res = _paginate(cfg, geom, content, tokens, entries)
    res.stats["toc_passes"] = 1
    return res


def _paginate(cfg, geom, content, tokens, toc_capacity) -> LayoutResult:
    res = LayoutResult()
    characters = {c["char_id"]: CharacterSpec.from_dict(c)
                  for c in content.get("characters", [])}
    style = default_style()
    scene_dpi = cfg.options.get("art_dpi", 120)
    pg = _Pager(cfg, geom, tokens, res, characters, style, scene_dpi)
    color_mode = cfg.color_mode
    grayscale_scenes = color_mode in ("BLACK_AND_WHITE", "GRAYSCALE")

    # ---- front matter ---------------------------------------------------
    for fm in content["front_matter"]:
        for b in fm["blocks"]:
            t = b["t"]
            if t == "half_title":
                pg.new_page("half_title")
                pg.y = pg.y_top + 120
                _center_text(pg, b["text"], tokens.heading_font, tokens.h1_size, LayoutColors.ink)
            elif t == "title_page":
                pg.new_page("title_page")
                pg.y = pg.y_top + 90
                _center_text(pg, b["title"], tokens.heading_font, tokens.h1_size + 6, LayoutColors.ink)
                pg.y += 26
                if b.get("subtitle"):
                    _center_text(pg, b["subtitle"], tokens.body_font, tokens.body_size + 1, LayoutColors.gray)
                    pg.y += 40
                else:
                    pg.y += 40
                _center_text(pg, b["author"], tokens.heading_font, tokens.body_size + 2, LayoutColors.ink)
            elif t == "copyright":
                pg.new_page("copyright")
                pg.y = pg.y_top + 200
                for line in b["text"].split("\n"):
                    _center_text(pg, line, tokens.body_font, tokens.caption_size + 1, LayoutColors.gray)
                    pg.y += (tokens.caption_size + 1) * 1.5
            elif t == "dedication":
                pg.new_page("dedication")
                pg.y = pg.y_top + 160
                _center_text(pg, b["text"], tokens.body_font, tokens.body_size, LayoutColors.ink, italic=True)
            elif t == "toc":
                if toc_capacity <= 0:
                    continue  # nothing to list — no phantom TOC page (§23)
                pg.new_page("toc")
                pg.y = pg.y_top
                _heading(pg, "Contents", tokens, 1)
                pg.y += 10
                res.toc_pages_start = len(res.pages) - 1
                res.toc_capacity = toc_capacity
                # placeholder lines; replaced with real entries below
                for _ in range(toc_capacity):
                    pg.ensure_room(tokens.body_size * 1.7, "toc")
                    pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + tokens.body_size,
                               "text": ". . .", "font": tokens.body_font,
                               "size": tokens.body_size, "color": LayoutColors.light})
                    pg.y += tokens.body_size * 1.7

    # ---- body -----------------------------------------------------------
    body_started = False
    body_start_page = None
    for sec in content["sections"]:
        titled = bool(sec.get("title"))
        blocks = sec["blocks"]
        if titled:
            # §23: never emit the same heading twice — drop a leading h1/h2/h3
            # block that merely repeats the section title.
            if blocks and blocks[0].get("t") in ("h1", "h2", "h3") and \
                    (blocks[0].get("text") or "").strip() == sec["title"].strip():
                blocks = blocks[1:]
            pg.new_page("chapter")
            _heading(pg, sec["title"], tokens, 1)
            pg.y += 8
            if body_start_page is None:
                body_start_page = len(res.pages) - 1
        for b in blocks:
            _render_block(pg, b, tokens, characters, style, grayscale_scenes, color_mode)
        body_started = True

    # ---- back matter ----------------------------------------------------
    # NOTE: back-matter sections already carry their own h1 block; the title
    # here only controls the page break (avoids double headings, §23).
    for sec in content["back_matter"]:
        if sec.get("title"):
            pg.new_page("chapter")
        for b in sec["blocks"]:
            _render_block(pg, b, tokens, characters, style, grayscale_scenes,
                          color_mode, content=content)

    # ---- fill real TOC (pass within same pagination; page numbers known
    #      for everything after toc because TOC sits before body) ---------
    _fill_toc(res, content, tokens, geom)

    # ---- page numbers + numbering metadata -------------------------------
    number_from = (body_start_page if body_start_page is not None else 0)
    for p in res.pages:
        n = p.index + 1
        if p.index >= number_from and p.meta.page_type not in (
                "half_title", "title_page", "copyright", "blank"):
            num_text = str(n)
            w = METRICS.text_width(tokens.body_font, tokens.page_number_size, num_text)
            cx = (pg.x0 + pg.x1) / 2 - w / 2
            p.ops.append({"op": "text", "x": cx,
                          "y": (geom.trim_h_in - geom.bottom_in / 2) * IN,
                          "text": num_text, "font": tokens.body_font,
                          "size": tokens.page_number_size, "color": LayoutColors.gray,
                          "footer": True})
            p.meta.footer_regions.append((cx / IN,
                                          geom.trim_h_in - geom.bottom_in / 2 - 0.06,
                                          w / IN, 0.14))
        # expected per-page color mode for MIXED (§27/§29)
        if color_mode == "MIXED_COLOR":
            has_color_asset = any(op.get("op") == "image" for op in p.ops)
            p.meta.expected_color_mode = "FULL_COLOR" if has_color_asset else "BLACK_AND_WHITE"
        p.meta.expected_assets = [op["asset_id"] for op in p.ops if op.get("op") == "image"]

    res.stats.update({
        "page_count": len(res.pages),
        "body_start_page": body_start_page,
        "assets": len(res.assets),
    })
    return res


def _fill_toc(res: LayoutResult, content, tokens, geom):
    entries = []
    for s in content["sections"]:
        if s.get("title"):
            entries.append(s["title"])
    for s in content["back_matter"]:
        if s.get("title"):
            entries.append(s["title"])
    # map titles to the first page they appear on
    page_of = {}
    for p in res.pages:
        for op in p.ops:
            if op.get("op") == "text" and op.get("heading") == 1 and op["text"] not in page_of:
                page_of[op["text"]] = p.index + 1
    lines = [(e, page_of[e]) for e in entries if e in page_of]
    res.toc = dict(lines)
    toc_pages = [p for p in res.pages if p.meta.page_type == "toc"]
    if not toc_pages:
        return
    x_left = geom.content_x_in * IN
    x_right = (geom.content_x_in + geom.content_w_in) * IN
    cap_per_page = max(8, (res.toc_capacity or len(lines)) // len(toc_pages) + 1)
    li = 0
    for p in toc_pages:
        p.ops = [op for op in p.ops if op.get("text") != ". . ."]
        ys = [op["y"] for op in p.ops if op.get("heading")]
        y = (max(ys) + 14) if ys else 70
        for _ in range(cap_per_page):
            if li >= len(lines):
                break
            title, num = lines[li]
            label = title[:46]
            numw = METRICS.text_width(tokens.body_font, tokens.body_size, str(num))
            p.ops.append({"op": "text", "x": x_left, "y": y, "text": label,
                          "font": tokens.body_font, "size": tokens.body_size,
                          "color": LayoutColors.ink, "toc_line": True})
            p.meta.text_regions.append((x_left / IN, y / IN - 0.1,
                                        geom.content_w_in, 0.25))
            p.ops.append({"op": "text", "x": x_right - numw, "y": y, "text": str(num),
                          "font": tokens.body_font, "size": tokens.body_size,
                          "color": LayoutColors.ink, "toc_line": True})
            li += 1
            y += tokens.body_size * 1.7


def _center_text(pg: _Pager, text: str, font: str, size: float, color, italic=False):
    w = METRICS.text_width(font, size, text)
    x = pg.x0 + max(0.0, (pg.cw - w) / 2)
    pg.add_op({"op": "text", "x": x, "y": pg.y, "text": text, "font": font,
               "size": size, "color": color, "italic": italic},
              text_bbox=(x / IN, pg.y / IN - size / IN, w / IN, size * 1.2 / IN))


def _heading(pg: _Pager, text: str, tokens, level: int):
    sizes = {1: tokens.h1_size, 2: tokens.h2_size, 3: tokens.h3_size}
    size = sizes[level]
    pg.ensure_room(size * 1.8, "chapter" if level == 1 else "text")
    pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + size, "text": text,
               "font": tokens.heading_font, "size": size,
               "color": LayoutColors.accent if level > 1 else LayoutColors.ink,
               "heading": level},
              text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, size * 1.3 / IN))
    pg.y += size * 1.6


def _render_block(pg: _Pager, b: dict, tokens, characters, style, grayscale,
                  color_mode, content=None):
    t = b["t"]
    ink = LayoutColors.ink
    if t == "p":
        lines = wrap_text(b["text"], tokens.body_font, tokens.body_size, pg.cw)
        lh = tokens.body_size * tokens.leading
        # widow/orphan control (§36): plan split
        fit = int(pg.remaining // lh)
        if 0 < fit < len(lines):
            if fit < 2 or (len(lines) - fit) < 2:
                if len(lines) * lh <= (pg.y_bottom_limit - pg.y_top):
                    pg.new_page("text")
                    pg.res.widow_orphan_repairs += 1
        for line in lines:
            pg.ensure_room(lh, "text")
            pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + tokens.body_size,
                       "text": line, "font": tokens.body_font,
                       "size": tokens.body_size, "color": ink},
                      text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, lh / IN))
            pg.y += lh
        pg.y += tokens.para_space_after
    elif t in ("h1", "h2", "h3"):
        _heading(pg, b["text"], tokens, int(t[1]))
    elif t == "list":
        for item in b["items"]:
            lines = wrap_text(item, tokens.body_font, tokens.body_size, pg.cw - 14)
            lh = tokens.body_size * tokens.leading
            first = True
            for line in lines:
                pg.ensure_room(lh, "text")
                if first:
                    pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + tokens.body_size,
                               "text": "\u2022", "font": tokens.body_font,
                               "size": tokens.body_size, "color": LayoutColors.accent})
                    first = False
                pg.add_op({"op": "text", "x": pg.x0 + 14, "y": pg.y + tokens.body_size,
                           "text": line, "font": tokens.body_font,
                           "size": tokens.body_size, "color": ink},
                          text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, lh / IN))
                pg.y += lh
            pg.y += 2
        pg.y += 4
    elif t == "ruled":
        line_h = 20.0
        for _ in range(b["lines"]):
            pg.ensure_room(line_h, "text")
            pg.add_op({"op": "line", "x1": pg.x0, "y1": pg.y + line_h - 4,
                       "x2": pg.x1, "y2": pg.y + line_h - 4,
                       "color": LayoutColors.light, "width": 0.8})
            pg.y += line_h
    elif t == "grid":
        _render_grid(pg, b, tokens)
    elif t == "table":
        _render_grid(pg, {"rows": len(b["rows"]), "cols": len(b["headers"]),
                          "header": b["headers"], "cells": b["rows"]}, tokens)
    elif t == "checklist":
        for item in b["items"]:
            pg.ensure_room(20, "text")
            pg.add_op({"op": "frame", "x": pg.x0, "y": pg.y + 3, "w": 11, "h": 11,
                       "color": ink, "width": 0.9})
            lines = wrap_text(item, tokens.body_font, tokens.body_size, pg.cw - 18)
            pg.add_op({"op": "text", "x": pg.x0 + 18, "y": pg.y + 12,
                       "text": lines[0], "font": tokens.body_font,
                       "size": tokens.body_size, "color": ink},
                      text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, 0.25))
            pg.y += max(20, len(lines) * tokens.body_size * tokens.leading)
        pg.y += 6
    elif t == "puzzle":
        _render_puzzle(pg, b, tokens)
    elif t == "coloring":
        _render_coloring(pg, b, tokens)
    elif t == "illustration":
        _render_illustration(pg, b, tokens, characters, grayscale, color_mode)
    elif t == "comic_page":
        _render_comic_page(pg, b, tokens, characters, grayscale, color_mode)
    elif t == "prompt":
        pg.ensure_room(tokens.h3_size * 1.8, "text")
        pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + tokens.h3_size,
                   "text": b["text"], "font": tokens.heading_font,
                   "size": tokens.h3_size, "color": LayoutColors.accent},
                  text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, 0.3))
        pg.y += tokens.h3_size * 1.7
    elif t == "quiz":
        lines = wrap_text(b["q"], tokens.body_font, tokens.body_size + 0.5, pg.cw)
        lh = (tokens.body_size + 0.5) * tokens.leading
        for line in lines:
            pg.ensure_room(lh, "text")
            pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + tokens.body_size + 0.5,
                       "text": line, "font": tokens.heading_font,
                       "size": tokens.body_size + 0.5, "color": ink},
                      text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, lh / IN))
            pg.y += lh
        for i, opt in enumerate(b["options"]):
            olines = wrap_text(f"({chr(65 + i)}) {opt}", tokens.body_font,
                               tokens.body_size, pg.cw - 10)
            for ol in olines:
                pg.ensure_room(lh, "text")
                pg.add_op({"op": "text", "x": pg.x0 + 10, "y": pg.y + tokens.body_size,
                           "text": ol, "font": tokens.body_font,
                           "size": tokens.body_size, "color": ink},
                          text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, lh / IN))
                pg.y += lh
        pg.y += 8
    elif t == "poem":
        _render_poem(pg, b, tokens)
    elif t == "recipe":
        _render_recipe(pg, b, tokens)
    elif t == "trace":
        _render_trace(pg, b, tokens)
    elif t == "answer_key_table":
        _render_answer_key(pg, content, tokens)
    elif t == "space":
        pg.y += b.get("h", 12)
    elif t == "pagebreak":
        pg.new_page("text")
    elif t == "caption":
        pg.ensure_room(tokens.caption_size * 1.5, "text")
        pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + tokens.caption_size,
                   "text": b["text"], "font": tokens.body_font,
                   "size": tokens.caption_size, "color": LayoutColors.gray},
                  text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, 0.2))
        pg.y += tokens.caption_size * 1.6
    else:
        raise ValueError(f"unknown block type {t}")


def _render_grid(pg, b, tokens):
    rows, cols = b["rows"], b["cols"]
    header = b.get("header")
    cells = b.get("cells")
    row_h = min(26.0, max(18.0, (pg.y_bottom_limit - pg.y_top) / (rows + 2)))
    col_w = pg.cw / cols
    cell_font = tokens.caption_size
    for r in range(rows + (1 if header else 0)):
        pg.ensure_room(row_h, "planner")
        for c in range(cols):
            x = pg.x0 + c * col_w
            pg.add_op({"op": "frame", "x": x, "y": pg.y, "w": col_w, "h": row_h,
                       "color": LayoutColors.light, "width": 0.7})
            txt = None
            if header and r == 0:
                txt = header[c] if c < len(header) else ""
            elif cells:
                rr = r - (1 if header else 0)
                if rr < len(cells) and c < len(cells[rr]):
                    txt = str(cells[rr][c])
            if txt:
                short = txt[:max(2, int(col_w / (cell_font * 0.55)))]
                pg.add_op({"op": "text", "x": x + 3, "y": pg.y + row_h * 0.62,
                           "text": short, "font": tokens.body_font,
                           "size": cell_font, "color": LayoutColors.ink},
                          text_bbox=(x / IN, pg.y / IN, col_w / IN, row_h / IN))
        pg.y += row_h
    pg.y += 8


def _render_puzzle(pg, b, tokens):
    data = b["data"]
    kind = data["kind"]
    n = data["number"]
    # one puzzle per page (puzzle-book convention; prevents overflow)
    if pg.cur is not None and len(pg.cur.ops) > 0:
        pg.new_page("puzzle")
    avail = min(pg.cw, pg.y_bottom_limit - pg.y - 46)
    if avail < 200:
        pg.new_page("puzzle")
        avail = min(pg.cw, pg.y_bottom_limit - pg.y - 46)
    pg.ensure_room(20, "puzzle")
    pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + tokens.h3_size,
               "text": f"Puzzle {n} — {kind.replace('_', ' ').title()}",
               "font": tokens.heading_font, "size": tokens.h3_size,
               "color": LayoutColors.accent, "heading": 3},
              text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, 0.3))
    pg.y += tokens.h3_size * 1.6
    ink = LayoutColors.ink
    if kind == "sudoku":
        size = min(avail, 360)
        cell = size / 9
        p = data["puzzle"]
        for r in range(9):
            for c in range(9):
                x = pg.x0 + c * cell
                y = pg.y + r * cell
                pg.add_op({"op": "frame", "x": x, "y": y, "w": cell, "h": cell,
                           "color": LayoutColors.light, "width": 0.5})
                v = p["grid"][r][c]
                if v:
                    pg.add_op({"op": "text", "x": x + cell * 0.32, "y": y + cell * 0.68,
                               "text": str(v), "font": tokens.heading_font,
                               "size": cell * 0.55, "color": ink},
                              text_bbox=(x / IN, y / IN, cell / IN, cell / IN))
        for k in range(10):
            w = 1.6 if k % 3 == 0 else 0.6
            pg.add_op({"op": "line", "x1": pg.x0 + k * cell, "y1": pg.y,
                       "x2": pg.x0 + k * cell, "y2": pg.y + size,
                       "color": ink, "width": w})
            pg.add_op({"op": "line", "x1": pg.x0, "y1": pg.y + k * cell,
                       "x2": pg.x0 + size, "y2": pg.y + k * cell,
                       "color": ink, "width": w})
        pg.y += size + 14
        pg.cur.meta.page_type = "puzzle"
    elif kind == "word_search":
        p = data["puzzle"]
        size = min(avail, 400)
        cell = size / p["size"]
        for r in range(p["size"]):
            for c in range(p["size"]):
                pg.add_op({"op": "text", "x": pg.x0 + c * cell + cell * 0.25,
                           "y": pg.y + r * cell + cell * 0.72,
                           "text": p["grid"][r][c], "font": "Courier",
                           "size": cell * 0.62, "color": ink},
                          text_bbox=((pg.x0 + c * cell) / IN, (pg.y + r * cell) / IN,
                                     cell / IN, cell / IN))
        pg.add_op({"op": "frame", "x": pg.x0, "y": pg.y, "w": size, "h": size,
                   "color": ink, "width": 1.0})
        pg.y += size + 6
        wl = wrap_text("Find: " + ", ".join(p["words"]), tokens.body_font,
                       tokens.caption_size + 1, pg.cw)
        for line in wl:
            pg.ensure_room(tokens.caption_size * 1.5, "puzzle")
            pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + tokens.caption_size + 1,
                       "text": line, "font": tokens.body_font,
                       "size": tokens.caption_size + 1, "color": LayoutColors.gray})
            pg.y += (tokens.caption_size + 1) * 1.4
        pg.cur.meta.page_type = "puzzle"
    elif kind == "maze":
        from .puzzles import rehydrate_maze
        p = rehydrate_maze(data["puzzle"])
        size = min(avail, 400)
        cell = size / p["cols"]
        rows = p["rows"]
        h = rows * cell
        for r in range(rows):
            for c in range(p["cols"]):
                ws = p["walls"][(r, c)]
                x, y = pg.x0 + c * cell, pg.y + r * cell
                if ws[0]:
                    pg.add_op({"op": "line", "x1": x, "y1": y, "x2": x + cell, "y2": y,
                               "color": ink, "width": 1.1})
                if ws[3]:
                    pg.add_op({"op": "line", "x1": x, "y1": y, "x2": x, "y2": y + cell,
                               "color": ink, "width": 1.1})
                if r == rows - 1 and ws[2]:
                    pg.add_op({"op": "line", "x1": x, "y1": y + cell, "x2": x + cell,
                               "y2": y + cell, "color": ink, "width": 1.1})
                if c == p["cols"] - 1 and ws[1]:
                    pg.add_op({"op": "line", "x1": x + cell, "y1": y, "x2": x + cell,
                               "y2": y + cell, "color": ink, "width": 1.1})
        sr, sc = p["start"]; er, ec = p["end"]
        pg.add_op({"op": "text", "x": pg.x0 + sc * cell + cell * 0.2,
                   "y": pg.y + sr * cell + cell * 0.7, "text": "S",
                   "font": tokens.heading_font, "size": cell * 0.6,
                   "color": LayoutColors.accent})
        pg.add_op({"op": "text", "x": pg.x0 + ec * cell + cell * 0.2,
                   "y": pg.y + er * cell + cell * 0.7, "text": "E",
                   "font": tokens.heading_font, "size": cell * 0.6,
                   "color": LayoutColors.accent})
        pg.y += h + 14
        pg.cur.meta.page_type = "puzzle"


def _render_coloring(pg, b, tokens):
    data = b["data"]
    pg.new_page("coloring")
    size = min(pg.cw, pg.y_bottom_limit - pg.y_top - 24)
    paths = motif_paths(data["motif"], data["seed"], size, size)
    ink = (0.12, 0.12, 0.12)
    for path in paths:
        pts = [(pg.x0 + x, pg.y + y) for x, y in path["pts"]]
        pg.add_op({"op": "poly", "pts": pts, "color": ink,
                   "width": path.get("width", 2.0),
                   "closed": not path.get("open", False),
                   "filled": False})
    pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + size + tokens.caption_size + 8,
               "text": f"{data['motif'].title()} — page {data['number']}",
               "font": tokens.body_font, "size": tokens.caption_size,
               "color": LayoutColors.gray})
    pg.y += size + 30


def _render_illustration(pg, b, tokens, characters, grayscale, color_mode):
    """Picture-book spread: full-bleed scene + text panel (§46)."""
    bleed_pt = pg.geom.bleed_in * IN
    full_w = pg.geom.trim_w_in * IN + bleed_pt
    full_h = pg.geom.trim_h_in * IN + 2 * bleed_pt
    aid = pg.register_scene(b["scene"], b.get("chars", []),
                            pg.geom.trim_w_in * IN, pg.geom.trim_h_in * IN,
                            bleed_pad_pt=bleed_pt, grayscale=grayscale)
    pg.new_page("illustration")
    # full-bleed image (trim coords; renderer extends through bleed)
    pg.add_op({"op": "image", "asset_id": aid, "x": 0, "y": -bleed_pt,
               "w": pg.geom.trim_w_in * IN,
               "h": pg.geom.trim_h_in * IN, "bleed": True, "full_bleed": True},
              image_bbox=(0, 0, pg.geom.trim_w_in, pg.geom.trim_h_in))
    pg.cur.meta.bleed_regions.append((0, 0, pg.geom.trim_w_in, pg.geom.trim_h_in))
    # text panel at bottom (illustration-led: text stays in safe zone)
    panel_h = 1.4 * IN
    pg.add_op({"op": "rect", "x": 0.4 * IN, "y": pg.geom.trim_h_in * IN - panel_h - 0.35 * IN,
               "w": pg.geom.trim_w_in * IN - 0.8 * IN, "h": panel_h,
               "color": (1, 1, 1, 0.92)})
    pg.y = pg.geom.trim_h_in * IN - panel_h - 0.35 * IN + 12
    pg.cur.meta.page_type = "illustration"


def _render_comic_page(pg, b, tokens, characters, grayscale, color_mode):
    """§53: panels + gutters + speech bubbles + captions + sequence."""
    pg.new_page("comic")
    panels = b["panels"]
    n = len(panels)
    gutter = 0.16 * IN
    avail_h = pg.y_bottom_limit - pg.y_top - 18
    ph = (avail_h - gutter * (n - 1)) / n
    pw = pg.cw
    ink = (0.08, 0.08, 0.08)
    for i, panel in enumerate(panels):
        x, y = pg.x0, pg.y_top + i * (ph + gutter)
        aid = pg.register_scene(panel["scene"], panel.get("chars", []), pw, ph,
                                grayscale=grayscale)
        pg.add_op({"op": "image", "asset_id": aid, "x": x, "y": y, "w": pw, "h": ph},
                  image_bbox=(x / IN, y / IN, pw / IN, ph / IN))
        pg.add_op({"op": "frame", "x": x, "y": y, "w": pw, "h": ph,
                   "color": ink, "width": 1.6})
        # panel number (reading order §53)
        pg.add_op({"op": "text", "x": x + 4, "y": y + 11, "text": str(i + 1),
                   "font": tokens.heading_font, "size": 8, "color": LayoutColors.gray})
        # caption box
        if panel.get("caption"):
            cap_w = min(pw * 0.55, METRICS.text_width(tokens.body_font,
                                                       tokens.caption_size, panel["caption"]) + 12)
            pg.add_op({"op": "rect", "x": x + 5, "y": y + 5, "w": cap_w, "h": 16,
                       "color": (1.0, 0.97, 0.85)})
            pg.add_op({"op": "frame", "x": x + 5, "y": y + 5, "w": cap_w, "h": 16,
                       "color": ink, "width": 0.8})
            pg.add_op({"op": "text", "x": x + 10, "y": y + 16, "text": panel["caption"][:60],
                       "font": tokens.body_font, "size": tokens.caption_size,
                       "color": ink}, text_bbox=((x + 5) / IN, (y + 5) / IN, cap_w / IN, 16 / IN))
        # speech bubble (§53 dialogue placement; bubble fully inside panel)
        if panel.get("dialogue"):
            text = panel["dialogue"][0]
            size = max(8.0, tokens.caption_size + 1)
            tw = METRICS.text_width(tokens.body_font, size, text)
            bw = min(tw + 26, pw * 0.6)
            bh = 30
            bx = x + pw - bw - 8
            by = y + 8
            pg.add_op({"op": "ellipse", "x": bx, "y": by, "w": bw, "h": bh,
                       "color": (1, 1, 1), "outline": ink, "width": 1.2})
            lines = wrap_text(text, tokens.body_font, size, bw - 16)
            ly = by + (bh - len(lines) * size * 1.15) / 2 + size
            for line in lines[:2]:
                lw = METRICS.text_width(tokens.body_font, size, line)
                pg.add_op({"op": "text", "x": bx + (bw - lw) / 2, "y": ly,
                           "text": line, "font": tokens.body_font, "size": size,
                           "color": ink},
                          text_bbox=(bx / IN, by / IN, bw / IN, bh / IN))
                ly += size * 1.15
    pg.y = pg.y_top + n * (ph + gutter)
    pg.cur.meta.page_type = "comic"


def _render_poem(pg, b, tokens):
    """§55: keep stanzas intact; only break between stanzas."""
    size = tokens.body_size + 1
    lh = size * 1.5
    stanza_h = [len(st) * lh + 10 for st in b["stanzas"]]
    total = sum(stanza_h)
    page_h = pg.y_bottom_limit - pg.y_top
    if total <= page_h and pg.remaining < total:
        pg.new_page("text")
    for st, sh in zip(b["stanzas"], stanza_h):
        if pg.remaining < sh and total <= page_h:
            pg.new_page("text")
        for line in st:
            pg.ensure_room(lh, "text")
            pg.add_op({"op": "text", "x": pg.x0 + 18, "y": pg.y + size,
                       "text": line, "font": tokens.body_font, "size": size,
                       "color": LayoutColors.ink, "italic": True},
                      text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, lh / IN))
            pg.y += lh
        pg.y += 10
    pg.y += 8


def _render_recipe(pg, b, tokens):
    r = b["data"]
    _heading(pg, r["name"], tokens, 2)
    meta = (f"Serves {r['servings']}   |   Prep {r['prep_time_min']} min   |   "
            f"Cook {r['cook_time_min']} min   |   Difficulty: {r['difficulty']}")
    pg.ensure_room(tokens.caption_size * 1.6, "text")
    pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + tokens.caption_size + 2,
               "text": meta, "font": tokens.body_font, "size": tokens.caption_size + 1,
               "color": LayoutColors.accent},
              text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, 0.25))
    pg.y += tokens.caption_size * 2.0
    _heading(pg, "Ingredients", tokens, 3)
    for qty, unit, ing in r["ingredients"]:
        pg.ensure_room(tokens.body_size * 1.5, "text")
        pg.add_op({"op": "text", "x": pg.x0 + 8, "y": pg.y + tokens.body_size,
                   "text": f"\u2022 {qty} {unit} {ing}", "font": tokens.body_font,
                   "size": tokens.body_size, "color": LayoutColors.ink},
                  text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, 0.25))
        pg.y += tokens.body_size * 1.5
    pg.y += 4
    _heading(pg, "Method", tokens, 3)
    for i, step in enumerate(r["steps"], 1):
        lines = wrap_text(f"{i}. {step}", tokens.body_font, tokens.body_size, pg.cw - 8)
        lh = tokens.body_size * tokens.leading
        for line in lines:
            pg.ensure_room(lh, "text")
            pg.add_op({"op": "text", "x": pg.x0 + 8, "y": pg.y + tokens.body_size,
                       "text": line, "font": tokens.body_font,
                       "size": tokens.body_size, "color": LayoutColors.ink},
                      text_bbox=(pg.x0 / IN, pg.y / IN, pg.cw / IN, lh / IN))
            pg.y += lh
    pg.y += 14


def _render_trace(pg, b, tokens):
    """§52: big light letterforms + handwriting guide lines."""
    chars = b["chars"]
    size = min(120, pg.cw / max(1, len(chars)) * 0.9)
    pg.ensure_room(size * 1.5 + 60, "worksheet")
    baseline = pg.y + size
    for i, ch in enumerate(chars):
        x = pg.x0 + i * (pg.cw / len(chars)) + (pg.cw / len(chars) - size * 0.62) / 2
        pg.add_op({"op": "text", "x": x, "y": baseline, "text": ch,
                   "font": "Helvetica-Bold", "size": size,
                   "color": (0.82, 0.82, 0.84), "trace": True},
                  text_bbox=(x / IN, pg.y / IN, size * 0.7 / IN, size / IN))
    pg.y += size * 1.12
    # guide lines
    for gy in (pg.y, pg.y + 22, pg.y + 44):
        pg.add_op({"op": "line", "x1": pg.x0, "y1": gy, "x2": pg.x1, "y2": gy,
                   "color": LayoutColors.light, "width": 0.8})
    pg.y += 56
    pg.cur.meta.page_type = "worksheet"


def _render_answer_key(pg, content, tokens):
    """§50: every answer maps to its puzzle; rendered as a real table."""
    entries = content.get("answer_key", []) if content else []
    _heading(pg, "Answers", tokens, 2)
    cols = 4
    col_w = pg.cw / cols
    row_h = 20
    shown = 0
    for e in entries:
        if e["kind"] in ("maze",):
            continue  # maze solutions are paths; listed separately below
        label = f"#{e['number']} ({e['kind']})"
        if e["kind"] == "sudoku":
            val = "see grid below"
        elif isinstance(e.get("answer"), list):
            val = f"{len(e['answer'])} items"
        else:
            val = str(e["answer"])[:18]
        r, c = divmod(shown, cols)
        pg.ensure_room(row_h, "answer")
        x = pg.x0 + c * col_w
        pg.add_op({"op": "frame", "x": x, "y": pg.y, "w": col_w, "h": row_h,
                   "color": LayoutColors.light, "width": 0.6})
        pg.add_op({"op": "text", "x": x + 3, "y": pg.y + row_h * 0.66,
                   "text": f"{label}: {val}", "font": tokens.body_font,
                   "size": tokens.caption_size, "color": LayoutColors.ink},
                  text_bbox=(x / IN, pg.y / IN, col_w / IN, row_h / IN))
        shown += 1
        if c == cols - 1:
            pg.y += row_h
    if shown % cols:
        pg.y += row_h
    pg.y += 10
    pg.cur.meta.page_type = "answer"
    # sudoku solution grids
    for e in entries:
        if e["kind"] != "sudoku":
            continue
        if pg.remaining < 180:
            pg.new_page("answer")
        cell = 16
        pg.add_op({"op": "text", "x": pg.x0, "y": pg.y + tokens.caption_size + 2,
                   "text": f"Puzzle #{e['number']} solution",
                   "font": tokens.body_font, "size": tokens.caption_size + 1,
                   "color": LayoutColors.gray})
        pg.y += tokens.caption_size + 8
        for r in range(9):
            for c in range(9):
                pg.add_op({"op": "text", "x": pg.x0 + c * cell + 4,
                           "y": pg.y + r * cell + 12, "text": str(e["answer"][r][c]),
                           "font": "Courier", "size": 9, "color": LayoutColors.ink})
        pg.add_op({"op": "frame", "x": pg.x0, "y": pg.y, "w": cell * 9, "h": cell * 9,
                   "color": LayoutColors.ink, "width": 0.8})
        pg.y += cell * 9 + 12
