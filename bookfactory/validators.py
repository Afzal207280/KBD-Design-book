"""Validator implementations (spec §23, §34, §49, §50, §52, §62, §76, §199,
§231 — true positives AND true negatives are tested).

Each validator returns findings (Defects) + evidence; status is composed
from these, never asserted (§200).
"""
from __future__ import annotations

import json
import os
import re

from .errors import blocking, warning, critical
from .puzzles import verify_sudoku, verify_word_search, verify_maze, verify_crossword
from .typography import METRICS

VALIDATOR_VERSION = "validators-1.2"


# ---------------------------------------------------------------------------
# Pagination (§23)
# ---------------------------------------------------------------------------

def validate_pagination(layout, geom, cfg) -> tuple[list, dict]:
    defects = []
    n = len(layout.pages)
    ev = {"pages": n, "blanks": 0, "types": {}}
    # sequential, no missing/duplicated
    ids = [p.meta.index for p in layout.pages]
    if ids != list(range(n)):
        defects.append(blocking("PAGINATION", "non-sequential-pages", "layout",
                                "rebuild layout", f"indices {ids[:10]}..."))
    for p in layout.pages:
        ev["types"][p.meta.page_type] = ev["types"].get(p.meta.page_type, 0) + 1
        if p.meta.page_type == "blank":
            ev["blanks"] += 1
        # parity: page number = index+1; right pages odd (§23)
        expected_side = "right" if (p.index % 2 == 0) else "left"
        p.meta.side = expected_side
    # page-count limits from ruleset are enforced by caller with ruleset;
    # here: minimum sensible book
    if n < 8:
        defects.append(blocking("PAGINATION", "book-too-short", "layout",
                                "add content or padding pages", f"{n} pages"))
    # section starts: every chapter page should be a recto or at least new
    seen_titles = set()
    for p in layout.pages:
        for op in p.ops:
            if op.get("heading") == 1:
                if op["text"] in seen_titles:
                    defects.append(warning("PAGINATION", "duplicate-chapter-start",
                                           p.meta.page_id, "check chapter list", op["text"]))
                seen_titles.add(op["text"])
    return defects, ev


# ---------------------------------------------------------------------------
# Puzzle correctness (§49, §159) + answer keys (§50)
# ---------------------------------------------------------------------------

def validate_puzzles(content) -> tuple[list, dict]:
    defects = []
    ev = {"puzzles": 0, "verified": 0}
    answers = {a["number"]: a for a in content.get("answer_key", [])}
    numbers = []
    for pz in content.get("puzzles", []):
        ev["puzzles"] += 1
        n = pz["number"]
        numbers.append(n)
        if pz["kind"] == "sudoku":
            r = verify_sudoku(pz["data"])
        elif pz["kind"] == "word_search":
            r = verify_word_search(pz["data"])
        elif pz["kind"] == "maze":
            from .puzzles import rehydrate_maze
            r = verify_maze(rehydrate_maze(pz["data"]))
        elif pz["kind"] == "crossword":
            r = verify_crossword(pz["data"])
        else:
            r = {"valid": False, "errors": [f"unknown puzzle kind {pz['kind']}"]}
        if r["valid"]:
            ev["verified"] += 1
        else:
            defects.append(blocking("PUZZLE", f"{pz['kind']}-invalid", f"puzzle-{n}",
                                    "regenerate puzzle with fresh seed",
                                    f"#{n} {pz['kind']}: {r['errors'][:2]}"))
        # §50: every puzzle must map to an answer
        if n not in answers:
            defects.append(blocking("PUZZLE", "missing-answer-key", f"puzzle-{n}",
                                    "add answer key entry", f"puzzle #{n}"))
        else:
            answers.pop(n)
    # duplicate numbering
    if len(numbers) != len(set(numbers)):
        defects.append(blocking("PUZZLE", "duplicate-puzzle-numbers", "content",
                                "renumber puzzles", "duplicate numbers found"))
    # orphan answers (§50)
    for n, a in answers.items():
        if a["kind"] in ("sudoku", "word_search", "maze", "crossword"):
            defects.append(warning("PUZZLE", "orphan-answer", f"answer-{n}",
                                   "remove or attach answer", f"answer #{n}"))
    ev["answers"] = len(content.get("answer_key", []))
    return defects, ev


# ---------------------------------------------------------------------------
# Fonts (§34) + text safety (§35)
# ---------------------------------------------------------------------------

def validate_fonts(content, cfg) -> tuple[list, dict]:
    defects = []
    ev = {"fonts": ["Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
                    "Times-Roman", "Times-Italic", "Courier"],
          "encoding": "WinAnsiEncoding (Latin-1)"}
    texts = []

    def walk(blocks):
        for b in blocks:
            for k in ("text", "q"):
                if isinstance(b.get(k), str):
                    texts.append(b[k])
            if b.get("t") == "poem":
                texts.extend(l for st in b.get("stanzas", []) for l in st)
            if b.get("t") == "recipe":
                texts.append(str(b.get("data", {}).get("name", "")))

    for s in content["sections"]:
        walk(s["blocks"])
    for s in content["back_matter"]:
        walk(s["blocks"])
    texts += [cfg.title, cfg.subtitle]
    from .typography import WINANSI_EXTRA
    bad = set()
    for t in texts:
        for ch in t:
            o = ord(ch)
            if o > 255 and ch not in WINANSI_EXTRA:
                bad.add(ch)
    if bad:
        defects.append(blocking("FONTS", "glyph-coverage-gap", "content",
                                "transliterate or drop unsupported glyphs",
                                f"chars: {[f'U+{ord(c):04X}' for c in list(bad)[:5]]}"))
    ev["unsupported_chars"] = sorted(f"U+{ord(c):04X}" for c in bad)
    return defects, ev


# ---------------------------------------------------------------------------
# Cross-file consistency (§76, §164)
# ---------------------------------------------------------------------------

def validate_crossfile(project_state: dict, interior_pdf: bytes,
                       cover_info: dict | None, epub: bytes | None,
                       cfg) -> tuple[list, dict]:
    from .pdfcheck import check_pdf
    defects = []
    ev = {}
    # interior PDF metadata vs project
    chk = check_pdf(interior_pdf)
    ev["interior_pages"] = chk.get("page_count")
    if chk.get("page_count") != project_state.get("page_count"):
        defects.append(blocking("CROSSFILE", "page-count-disagreement", "project",
                                "re-render interior",
                                f"project={project_state.get('page_count')} "
                                f"pdf={chk.get('page_count')}"))
    if cover_info is not None:
        if cover_info["page_count"] != project_state.get("page_count"):
            defects.append(blocking("CROSSFILE", "cover-page-count-stale", "cover",
                                    "regenerate cover from final page count (§71)",
                                    f"cover={cover_info['page_count']} "
                                    f"final={project_state.get('page_count')}"))
        ev["cover_spine"] = cover_info["spine_in"]
    if epub is not None:
        from .epubgen import check_epub
        r = check_epub(epub, cfg)
        if not r["ok"]:
            for e in r["errors"]:
                defects.append(blocking("CROSSFILE", "epub-inconsistent", "epub",
                                        "rebuild EPUB", e))
        ev["epub_ok"] = r["ok"]
    # single source of truth (§197)
    c = project_state.get("config", {})
    for field in ("title", "book_type", "color_mode"):
        if field in c and field in project_state and c[field] != project_state.get(field):
            defects.append(critical("CROSSFILE", "source-of-truth-conflict", "project",
                                    "reconcile project state", field))
    ev["title"] = cfg.title
    return defects, ev


# ---------------------------------------------------------------------------
# Security of outputs (§78, §136)
# ---------------------------------------------------------------------------

def validate_output_security(project_dir: str, pid: str) -> tuple[list, dict]:
    defects = []
    ev = {"files_scanned": 0}
    secret_re = re.compile(
        rb"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
        rb"api[_-]?key\s*[:=]\s*[A-Za-z0-9]{12,})", re.I)
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in (".git",)]
        for fn in files:
            if fn.endswith((".png", ".pdf", ".zip", ".epub", ".jpg")):
                continue
            p = os.path.join(root, fn)
            try:
                with open(p, "rb") as f:
                    data = f.read(400_000)
            except OSError:
                continue
            ev["files_scanned"] += 1
            if secret_re.search(data):
                defects.append(critical("SECURITY", "secret-in-output", os.path.relpath(p, project_dir),
                                        "redact secret and rebuild", fn))
            if b"<script" in data.lower() and fn.endswith((".html",)):
                # scripts in our launcher are allowed only when benign & ours
                if b"window.location" not in data and b"document.write" in data:
                    defects.append(warning("SECURITY", "unexpected-script", fn,
                                           "review script content", fn))
    return defects, ev


# ---------------------------------------------------------------------------
# Writing-space sufficiency (§51) & tracing clarity (§52)
# ---------------------------------------------------------------------------

def validate_writing_space(content, book_type: str) -> tuple[list, dict]:
    defects = []
    ev = {}
    if book_type in ("notebook", "lined_notebook", "journal", "diary",
                     "gratitude_journal", "prompt_journal", "guided_journal",
                     "workbook", "handwriting_book", "tracing_book"):
        ruled_lines = 0
        for s in content["sections"]:
            for b in s["blocks"]:
                if b.get("t") == "ruled":
                    ruled_lines += b.get("lines", 0)
        ev["ruled_lines"] = ruled_lines
        if ruled_lines < 100:
            defects.append(blocking("USABILITY", "insufficient-writing-space",
                                    "content", "add more writing pages (§51)",
                                    f"only {ruled_lines} ruled lines"))
    if book_type in ("handwriting_book", "tracing_book", "childrens_tracing_book"):
        trace_blocks = sum(1 for s in content["sections"]
                           for b in s["blocks"] if b.get("t") == "trace")
        ev["trace_blocks"] = trace_blocks
        if trace_blocks < 5:
            defects.append(blocking("USABILITY", "insufficient-tracing", "content",
                                    "add tracing pages (§52)", f"{trace_blocks} blocks"))
    return defects, ev
