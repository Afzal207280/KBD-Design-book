"""PDF structural validation (spec §72, §162, §113).

Actually parses the PDF bytes: header, xref offsets (recomputed), object
table, page tree count, MediaBox dimensions, fonts, images, metadata,
encryption state, JS/OpenAction absence, corruption. No metadata-only
trust — the file bytes are the evidence.
"""
from __future__ import annotations

import re


class PDFCheckResult(dict):
    pass


def _find_xref(data: bytes) -> int:
    m = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data, re.S)
    if not m:
        return -1
    return int(m.group(1))


def check_pdf(data: bytes, expect_pages: int | None = None,
              expect_w_in: float | None = None, expect_h_in: float | None = None,
              expect_title: str | None = None, expect_author: str | None = None,
              tol_in: float = 0.005) -> PDFCheckResult:
    errors, warnings = [], []
    res = PDFCheckResult(errors=errors, warnings=warnings)
    if not data.startswith(b"%PDF-1."):
        errors.append("bad header")
        return res
    if not data.rstrip().endswith(b"%%EOF"):
        errors.append("missing EOF marker")

    # --- xref ------------------------------------------------------------
    xref_pos = _find_xref(data)
    if xref_pos <= 0:
        errors.append("missing/broken startxref")
        return res
    chunk = data[xref_pos:xref_pos + 200]
    m = re.match(rb"xref\s+(\d+)\s+(\d+)\s*", chunk)
    if not m:
        errors.append("xref table not found at startxref offset")
        return res
    first, count = int(m.group(1)), int(m.group(2))
    body_start = xref_pos + m.end()
    entries = re.findall(rb"(\d{10}) (\d{5}) ([nf]) ", data[body_start:body_start + count * 20])
    if len(entries) != count:
        errors.append(f"xref has {len(entries)} entries, expected {count}")
    obj_pat = re.compile(rb"(\d+) 0 obj")
    verified = 0
    for off_b, gen, flag in entries:
        if flag == b"f":
            continue
        off = int(off_b)
        if not (0 < off < len(data)):
            errors.append(f"xref offset {off} out of file")
            continue
        seg = data[off:off + 32]
        if not re.match(rb"\d+ 0 obj", seg):
            errors.append(f"xref offset {off} does not point at an object")
        else:
            verified += 1
    res["xref_entries_verified"] = verified

    # --- trailer -----------------------------------------------------------
    trailer = data[xref_pos:]
    if b"/Root" not in trailer:
        errors.append("trailer missing /Root")
    if re.search(rb"/Encrypt", data):
        errors.append("PDF is encrypted (security state must be open for KDP)")
    if re.search(rb"/OpenAction|/JS\b|/JavaScript", data):
        errors.append("PDF contains actions/JavaScript")

    # --- pages -------------------------------------------------------------
    page_objs = re.findall(rb"/Type\s*/Page[^s]", data)
    res["page_count"] = len(page_objs)
    counts = re.findall(rb"/Type\s*/Pages\s*/Kids\s*\[([^\]]*)\]\s*/Count\s+(\d+)", data)
    if counts:
        kids_n = len(counts[0][0].split(b" 0 R")) - 1
        declared = int(counts[0][1])
        res["declared_count"] = declared
        if declared != len(page_objs):
            errors.append(f"/Count {declared} != actual page objects {len(page_objs)}")
        if kids_n != declared:
            errors.append(f"Kids array has {kids_n} entries, /Count says {declared}")
    if expect_pages is not None and res["page_count"] != expect_pages:
        errors.append(f"expected {expect_pages} pages, file has {res['page_count']}")

    # --- dimensions ----------------------------------------------------------
    boxes = re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", data)
    if boxes:
        w_pt, h_pt = float(boxes[0][0]), float(boxes[0][1])
        res["page_w_in"], res["page_h_in"] = w_pt / 72.0, h_pt / 72.0
        distinct = {(round(float(w) / 72.0, 4), round(float(h) / 72.0, 4)) for w, h in boxes}
        if len(distinct) > 1:
            errors.append(f"inconsistent page sizes across pages: {distinct}")
        if expect_w_in is not None and abs(res["page_w_in"] - expect_w_in) > tol_in:
            errors.append(f"page width {res['page_w_in']:.4f} != expected {expect_w_in:.4f}")
        if expect_h_in is not None and abs(res["page_h_in"] - expect_h_in) > tol_in:
            errors.append(f"page height {res['page_h_in']:.4f} != expected {expect_h_in:.4f}")
    else:
        errors.append("no MediaBox found")

    # --- fonts & images -------------------------------------------------------
    fonts = set(re.findall(rb"/BaseFont\s*/([\w\-]+)", data))
    res["fonts"] = sorted(f.decode() for f in fonts)
    if fonts and not fonts <= {b"Helvetica", b"Helvetica-Bold", b"Helvetica-Oblique",
                              b"Times-Roman", b"Times-Italic", b"Courier"}:
        errors.append(f"non-base14 font without embedding: {res['fonts']}")
    res["image_xobjects"] = len(re.findall(rb"/Subtype\s*/Image", data))

    # --- metadata ---------------------------------------------------------------
    res["has_info"] = b"/Title" in data and b"/Author" in data
    if expect_title is not None:
        if expect_title.encode("latin-1", "replace") not in data:
            warnings.append("title not found verbatim in metadata (may be escaped)")
    if expect_author is not None:
        if expect_author.encode("latin-1", "replace") not in data:
            warnings.append("author not found verbatim in metadata")

    res["ok"] = not errors
    return res
