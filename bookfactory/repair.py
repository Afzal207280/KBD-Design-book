"""Automatic repair engine (spec §89, §90, §149, §234).

Repairs target only the affected dependency chain; after every repair the
affected stages RENDER → VALIDATE → COMPARE → ACCEPT/REPAIR AGAIN. A
configurable repair cap forces RED rather than looping forever.
"""
from __future__ import annotations

from .errors import Severity

DEFAULT_MAX_REPAIRS = 3

# defect root_cause -> (repair action, dependency node to invalidate)
REPAIR_TABLE = {
    "puzzle-invalid": ("regenerate-puzzles", "CONTENT"),
    "missing-answer-key": ("regenerate-puzzles", "CONTENT"),
    "book-too-short": ("add-padding-pages", "CONTENT"),
    "page-count-below-min": ("add-padding-pages", "CONTENT"),
    "page-count-above-max": ("reduce-content-pages", "CONTENT"),
    "insufficient-writing-space": ("add-writing-pages", "CONTENT"),
    "insufficient-tracing": ("add-writing-pages", "CONTENT"),
    "text-outside-safe-area": ("shrink-type-scale", "DESIGN"),
    "text-collision": ("shrink-type-scale", "DESIGN"),
    "margin-violation": ("shrink-type-scale", "DESIGN"),
    "low-contrast": ("enable-high-contrast", "DESIGN"),
    "unexpected-color": ("force-intent-render", "ILLUSTRATIONS"),
    "missing-color": ("force-intent-render", "ILLUSTRATIONS"),
    "bleed-not-reaching-edge": ("force-intent-render", "ILLUSTRATIONS"),
    "unexpected-blank": ("drop-blank-pages", "LAYOUT"),
    "cover-dimension-mismatch": ("rebuild-cover", "COVER"),
    "cover-formula-mismatch": ("rebuild-cover", "COVER"),
    "white-edges": ("rebuild-cover", "COVER"),
    "barcode-zone-obstructed": ("rebuild-cover", "COVER"),
    "front-cover-text-missing": ("rebuild-cover", "COVER"),
    "cover-page-count-stale": ("rebuild-cover", "COVER"),
    "epub-inconsistent": ("rebuild-epub", "EPUB"),
    "page-count-disagreement": ("rebuild-interior", "INTERIOR_PDF"),
}


def plan_repairs(defects: list, cfg, state: dict) -> list[dict]:
    """Deterministic repair plan for blocking defects."""
    plans = []
    max_repairs = state.get("config", {}).get("options", {}).get(
        "max_repairs", DEFAULT_MAX_REPAIRS)
    repair_count = state.get("repair_count", 0)
    for d in defects:
        if d.severity not in (Severity.BLOCKING, Severity.CRITICAL):
            continue
        if repair_count >= max_repairs:
            break
        entry = REPAIR_TABLE.get(d.root_cause)
        if entry is None:
            continue
        action, node = entry
        plans.append({
            "defect_id": d.id, "root_cause": d.root_cause,
            "action": action, "invalidate": node,
            "detail": d.detail,
        })
        repair_count += 1
    return plans


def apply_repair(plan: dict, cfg, content: dict | None) -> str:
    """Mutate configuration so the next render fixes the defect.
    Returns a human-readable description of what changed."""
    a = plan["action"]
    opts = cfg.options
    if a == "regenerate-puzzles":
        cfg.seed = int(cfg.seed) + 101  # fresh deterministic generation
        return "puzzle seeds rolled forward; puzzles regenerate deterministically"
    if a == "add-padding-pages":
        opts["pad_pages"] = int(opts.get("pad_pages", 0)) + max(
            2, int(opts.get("pad_needed", 8)))
        return f"add {opts['pad_pages']} ruled padding pages to reach KDP minimum"
    if a == "reduce-content-pages":
        for key in ("structured_pages", "ruled_pages", "journal_pages",
                    "prompt_pages", "coloring_pages", "n_sudoku", "n_wordsearch",
                    "n_maze", "chapters", "poems", "recipes", "lessons",
                    "exercises", "comic_pages", "story_pages"):
            if key in opts:
                opts[key] = max(4, int(opts[key] * 0.8))
        opts.setdefault("chapters", None)
        if opts.get("chapters") is None and "chapters" not in opts:
            pass
        return "content volume reduced by 20% to fit KDP maximum"
    if a == "add-writing-pages":
        for key in ("structured_pages", "ruled_pages", "journal_pages"):
            if key in opts:
                opts[key] = int(opts[key]) + 8
                break
        else:
            opts["ruled_pages"] = int(opts.get("ruled_pages", 60)) + 16
        return "additional writing pages added"
    if a == "shrink-type-scale":
        scale = float(opts.get("type_scale", 1.0)) * 0.93
        opts["type_scale"] = max(0.8, scale)
        return f"type scale now {opts['type_scale']:.2f}"
    if a == "enable-high-contrast":
        opts["high_contrast"] = True
        return "high-contrast palette enabled"
    if a == "force-intent-render":
        opts["render_bump"] = int(opts.get("render_bump", 0)) + 1
        return "illustration render bumped to enforce declared color intent"
    if a == "drop-blank-pages":
        opts["allow_blanks"] = False
        return "unintended blank pages removed"
    if a in ("rebuild-cover", "rebuild-epub", "rebuild-interior"):
        return f"{a}: downstream artifact rebuilt from current upstream state"
    return "no-op"
