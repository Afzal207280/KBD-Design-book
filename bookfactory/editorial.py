"""Editorial engine (spec §14): developmental/copy edits, repetition and
contradiction detection, terminology consistency, structural checks.

All checks are deterministic measurements, not vibes (§202).
"""
from __future__ import annotations

import difflib
import re

from .errors import warning, blocking, Severity


def _iter_text_blocks(content):
    for sec in content["sections"]:
        for b in sec["blocks"]:
            if b.get("t") in ("p", "h1", "h2", "h3", "prompt") and b.get("text"):
                yield sec, b


def check_repetition(content, max_dup=1, similarity=0.92) -> list:
    """Duplicate sentence detection + near-duplicate paragraph detection."""
    findings = []
    seen_sentences: dict[str, int] = {}
    paragraphs = []
    for sec, b in _iter_text_blocks(content):
        if b["t"] != "p":
            continue
        paragraphs.append((sec.get("title", ""), b["text"]))
        for s in re.split(r"(?<=[.!?])\s+", b["text"]):
            s = s.strip().lower()
            if len(s) < 20:
                continue
            seen_sentences[s] = seen_sentences.get(s, 0) + 1
    dupes = {s: c for s, c in seen_sentences.items() if c > max_dup}
    if dupes:
        findings.append(warning(
            "EDITING", "repeated-sentence", "content",
            "de-duplicate or rephrase repeated sentences",
            f"{len(dupes)} duplicated sentence(s), e.g. "
            f"{list(dupes.items())[:2]}"))
    # near-duplicate paragraphs
    for i in range(len(paragraphs)):
        for j in range(i + 1, min(i + 30, len(paragraphs))):
            a, btxt = paragraphs[i][1], paragraphs[j][1]
            if abs(len(a) - len(btxt)) > max(len(a), len(btxt)) * 0.3:
                continue
            ratio = difflib.SequenceMatcher(None, a, btxt).ratio()
            if ratio >= similarity:
                findings.append(warning(
                    "EDITING", "near-duplicate-paragraph", "content",
                    "vary paragraph content",
                    f"paragraphs in '{paragraphs[i][0]}'/'{paragraphs[j][0]}' "
                    f"similar {ratio:.2f}"))
    return findings


def check_contradictions(content) -> list:
    """Deterministic contradiction checks on structural facts."""
    findings = []
    chapter_numbers = []
    for sec in content["sections"]:
        for b in sec["blocks"]:
            if b.get("t") in ("h1", "h2", "h3") and b.get("text"):
                m = re.match(r"Chapter (\d+)", b["text"])
                if m:
                    chapter_numbers.append(int(m.group(1)))
    for i in range(1, len(chapter_numbers)):
        if chapter_numbers[i] != chapter_numbers[i - 1] + 1:
            findings.append(blocking(
                "EDITING", "chapter-sequence-broken", "content",
                "renumber chapters sequentially",
                f"chapter {chapter_numbers[i]} follows {chapter_numbers[i - 1]}"))
    # duplicate section titles among titled sections
    titles = [s["title"] for s in content["sections"] if s.get("title")]
    seen = set()
    for t in titles:
        if t in seen:
            findings.append(warning("EDITING", "duplicate-section-title", "content",
                                    "make section titles unique", f"'{t}' repeats"))
        seen.add(t)
    # empty sections (except blank low-content separators)
    for s in content["sections"]:
        if s.get("title") and not s["blocks"]:
            findings.append(warning("EDITING", "empty-section", "content",
                                    "remove or fill empty section", s["title"]))
    return findings


TERM_CANON = {
    "wordsearch": "word search", "word-search": "word search",
    "coloringbook": "coloring book", "colouring": "coloring",
    "ebook": "eBook", "e book": "eBook",
}


def check_terminology(content) -> list:
    findings = []
    variants: dict[str, set[str]] = {}
    for sec, b in _iter_text_blocks(content):
        txt = b["text"]
        for m in re.finditer(r"[A-Za-z][A-Za-z\- ]{2,}", txt):
            w = m.group(0)
            key = w.lower().replace(" ", "").replace("-", "")
            if key in TERM_CANON:
                variants.setdefault(TERM_CANON[key], set()).add(w)
    for canon, forms in variants.items():
        if len(forms) > 1:
            findings.append(warning("EDITING", "terminology-inconsistent", "content",
                                    f"use '{canon}' consistently",
                                    f"variants found: {sorted(forms)}"))
    return findings


def check_fact_markers(content, book_type: str) -> list:
    """§56/§57: time-sensitive or nutritional claims must carry dates/labels."""
    findings = []
    if book_type == "travel_guide":
        for sec in content["sections"]:
            for b in sec["blocks"]:
                if b.get("t") != "p":
                    continue
                text = b.get("text") or ""
                if "current as of" not in text:
                    continue
                if not re.search(r"\d{4}-\d{2}-\d{2}", text):
                    findings.append(blocking(
                        "EDITING", "time-sensitive-fact-undated", "content",
                        "add research date to time-sensitive text", text[:60]))
    if book_type == "cookbook":
        for sec in content["sections"]:
            for b in sec["blocks"]:
                if b.get("t") == "recipe" and "nutrition" not in str(b.get("data", {})).lower():
                    findings.append(blocking(
                        "EDITING", "nutrition-claim-unlabeled", "content",
                        "label nutrition as sourced or estimate (§56)",
                        b["data"].get("name", "?")))
    return findings


def run_editorial(content, book_type: str) -> list:
    findings = []
    findings += check_repetition(content)
    findings += check_contradictions(content)
    findings += check_terminology(content)
    findings += check_fact_markers(content, book_type)
    return findings
