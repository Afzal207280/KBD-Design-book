"""EPUB engine (spec §77, §163): generation + real package/content validation.

EPUB 3: mimetype (stored, first, uncompressed), container.xml, package
document with metadata + manifest + spine (reading order), nav document,
CSS, XHTML chapters. Validator re-opens the zip and checks structure,
XML well-formedness, href resolution, reading order and metadata.
"""
from __future__ import annotations

import hashlib
import html
import io
import re
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET

CSS = """
body { font-family: serif; margin: 5%; line-height: 1.5; color: #1a1a1c; }
h1 { font-family: sans-serif; font-size: 1.6em; margin-top: 2em; }
h2 { font-family: sans-serif; font-size: 1.25em; }
p { text-indent: 1.5em; margin: 0.4em 0; }
.poem p { text-indent: 0; }
.meta { color: #555; font-size: 0.9em; }
"""


def _chapter_xhtml(title: str, blocks: list, cfg, chapter_no: int) -> str:
    parts = []
    for b in blocks:
        t = b.get("t")
        if t == "p" and b.get("text"):
            parts.append(f"<p>{html.escape(b['text'])}</p>")
        elif t in ("h1", "h2", "h3") and b.get("text"):
            lvl = int(t[1])
            parts.append(f"<h{lvl + 0}>{html.escape(b['text'])}</h{lvl}>")
        elif t == "list":
            items = "".join(f"<li>{html.escape(str(i))}</li>" for i in b["items"])
            parts.append(f"<ul>{items}</ul>")
        elif t == "poem":
            lines = "".join(f"<p>{html.escape(l)}</p>"
                            for st in b.get("stanzas", []) for l in st)
            parts.append(f'<div class="poem">{lines}</div>')
        elif t == "recipe":
            r = b["data"]
            ings = "".join(f"<li>{html.escape(f'{q} {u} {i}')}</li>"
                           for q, u, i in r["ingredients"])
            steps = "".join(f"<li>{html.escape(s)}</li>" for s in r["steps"])
            parts.append(
                f"<h2>{html.escape(r['name'])}</h2>"
                f"<p class='meta'>Serves {r['servings']} · prep {r['prep_time_min']} min"
                f" · cook {r['cook_time_min']} min · {html.escape(r['difficulty'])}</p>"
                f"<ul>{ings}</ul><ol>{steps}</ol>"
                f"<p class='meta'>{html.escape(r['nutrition_note'])}</p>")
        elif t == "quiz":
            opts = "".join(f"<li>{html.escape(o)}</li>" for o in b["options"])
            parts.append(f"<p><strong>{html.escape(b['q'])}</strong></p><ul>{opts}</ul>")
        elif t == "prompt" and b.get("text"):
            parts.append(f"<p><em>{html.escape(b['text'])}</em></p>")
        elif t == "checklist":
            items = "".join(f"<li>\u2610 {html.escape(str(i))}</li>" for i in b["items"])
            parts.append(f"<ul>{items}</ul>")
        elif t == "puzzle":
            d = b["data"]
            parts.append(f"<p><em>Puzzle {d['number']} "
                         f"({html.escape(d['kind'].replace('_', ' '))}) — "
                         f"see the print edition for the interactive grid.</em></p>")
        elif t == "coloring":
            parts.append(f"<p><em>Coloring page {b['data']['number']}: "
                         f"{html.escape(b['data']['motif'])} — see the print edition.</em></p>")
        # grids / ruled / illustrations are print-layout constructs;
        # represent them honestly rather than pretending (§185)
        elif t in ("ruled", "grid", "trace", "illustration", "comic_page"):
            parts.append("<p class='meta'>[print-layout page — see print edition]</p>")
    body = "\n".join(parts) or "<p>\u00a0</p>"
    t_esc = html.escape(title) if title else f"Section {chapter_no}"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{cfg.language}" xml:lang="{cfg.language}">
<head><title>{t_esc}</title><link rel="stylesheet" href="style.css"/></head>
<body><h1>{t_esc}</h1>
{body}
</body></html>"""


def build_epub(cfg, content, cover_png: bytes | None = None) -> bytes:
    """Return EPUB bytes."""
    uid = uuid.uuid5(uuid.NAMESPACE_URL, f"kdp-book-factory:{cfg.topic}:{cfg.seed}")
    # deterministic date derived from the seed (wall-clock would break
    # byte-identical rebuilds — §deterministic build)
    import datetime as _dt
    _d = _dt.date(2000, 1, 1) + _dt.timedelta(days=int(cfg.seed) % 9490)
    modified = _d.isoformat() + "T00:00:00Z"

    chapters = []
    n = 0
    for sec in content["sections"]:
        if not sec.get("title") and not any(b.get("t") in ("p", "poem", "recipe")
                                            for b in sec["blocks"]):
            continue
        n += 1
        chapters.append((f"ch{n:03d}", sec.get("title") or f"Part {n}", sec["blocks"]))
    for sec in content["back_matter"]:
        if sec.get("title"):
            n += 1
            chapters.append((f"ch{n:03d}", sec["title"], sec["blocks"]))
    if not chapters:
        # print-layout book (notebook/planner/coloring): honest note (§185)
        chapters.append(("ch001", "About this edition", [{
            "t": "p", "text": f"'{cfg.title}' is a print-layout book "
            f"(writing pages, grids or artwork). The reflowable edition "
            f"contains this note; the full experience is the print edition."}]))

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                'properties="nav"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    spine_items = []
    files: dict[str, bytes] = {}
    for cid, title, blocks in chapters:
        manifest.append(f'<item id="{cid}" href="{cid}.xhtml" '
                        f'media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="{cid}"/>')
        files[f"OEBPS/{cid}.xhtml"] = _chapter_xhtml(title, blocks, cfg, len(files)).encode("utf-8")
    if cover_png:
        manifest.append('<item id="cover" href="images/cover.png" '
                        'media-type="image/png" properties="cover-image"/>')
        files["OEBPS/images/cover.png"] = cover_png

    title_esc = html.escape(cfg.title)
    author_esc = html.escape(cfg.author.name)
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:{uid}</dc:identifier>
    <dc:title>{title_esc}</dc:title>
    <dc:creator>{author_esc}</dc:creator>
    <dc:language>{cfg.language}</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    {chr(10).join('    ' + m for m in manifest)}
  </manifest>
  <spine>
    {chr(10).join('    ' + s for s in spine_items)}
  </spine>
</package>"""
    nav_links = "\n".join(
        f'<li><a href="{cid}.xhtml">{html.escape(t)}</a></li>'
        for cid, t, _ in chapters)
    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{cfg.language}">
<head><title>Table of Contents</title></head>
<body><nav epub:type="toc" id="toc"><h1>Contents</h1><ol>
{nav_links}
</ol></nav></body></html>"""

    # fixed entry timestamps → byte-identical rebuilds (§deterministic build)
    _FIXED_DT = (2000, 1, 1, 0, 0, 0)

    def _add(z, name, data):
        zi = zipfile.ZipInfo(name, date_time=_FIXED_DT)
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, data)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # mimetype must be first and uncompressed
        _add(z, "mimetype", "application/epub+zip")
        _add(z, "META-INF/container.xml",
             '<?xml version="1.0"?>\n<container version="1.0" '
             'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
             '<rootfiles><rootfile full-path="OEBPS/content.opf" '
             'media-type="application/oebps-package+xml"/></rootfiles></container>')
        _add(z, "OEBPS/content.opf", opf)
        _add(z, "OEBPS/nav.xhtml", nav)
        _add(z, "OEBPS/style.css", CSS)
        for name, data in files.items():
            _add(z, name, data)
    return buf.getvalue()


def check_epub(data: bytes, cfg) -> dict:
    """Structural + content validation of the EPUB package (§77, §163)."""
    errors, warnings = [], []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        return {"ok": False, "errors": [f"not a zip: {e}"], "warnings": []}
    names = zf.namelist()
    # mimetype: first entry, stored, exact content
    first = zf.infolist()[0]
    if first.filename != "mimetype":
        errors.append("mimetype is not the first entry")
    else:
        if first.compress_type != zipfile.ZIP_STORED:
            errors.append("mimetype entry is compressed (must be stored)")
        if zf.read("mimetype") != b"application/epub+zip":
            errors.append("mimetype content wrong")
    if "META-INF/container.xml" not in names:
        errors.append("missing container.xml")
    if "OEBPS/content.opf" not in names:
        errors.append("missing content.opf")
        return {"ok": False, "errors": errors, "warnings": warnings}
    # XML well-formedness
    for x in ("META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml"):
        if x in names:
            try:
                ET.fromstring(zf.read(x))
            except ET.ParseError as e:
                errors.append(f"malformed XML in {x}: {e}")
    for n in names:
        if n.endswith(".xhtml") and n != "OEBPS/nav.xhtml":
            try:
                ET.fromstring(zf.read(n))
            except ET.ParseError as e:
                errors.append(f"malformed XHTML in {n}: {e}")
    # metadata + manifest + spine
    try:
        opf = ET.fromstring(zf.read("OEBPS/content.opf"))
        ns = {"opf": "http://www.idpf.org/2007/opf",
              "dc": "http://purl.org/dc/elements/1.1/"}
        title = opf.find(".//dc:title", ns)
        lang = opf.find(".//dc:language", ns)
        ident = opf.find(".//dc:identifier", ns)
        if title is None or title.text != cfg.title:
            errors.append(f"opf title mismatch: {title.text if title is not None else None}")
        if lang is None or lang.text != cfg.language:
            errors.append("opf language mismatch")
        if ident is None:
            errors.append("opf identifier missing")
        items = {i.get("id"): i.get("href") for i in opf.findall(".//opf:item", ns)}
        spine = [i.get("idref") for i in opf.findall(".//opf:itemref", ns)]
        if not spine:
            errors.append("spine empty — no reading order")
        for idref in spine:
            if idref not in items:
                errors.append(f"spine idref {idref} not in manifest")
            else:
                href = "OEBPS/" + items[idref]
                if href not in names:
                    errors.append(f"spine href missing from package: {href}")
        # all manifest hrefs must exist (no orphans §215 / no missing §216)
        for iid, href in items.items():
            if ("OEBPS/" + href) not in names:
                errors.append(f"manifest item {iid} href missing: {href}")
        warnings.append(f"reading_order={len(spine)} documents")
    except ET.ParseError:
        errors.append("content.opf unparseable")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "entries": len(names)}
