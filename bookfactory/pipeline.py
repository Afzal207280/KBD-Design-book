"""Pipeline orchestrator (spec §155, §235, §80–§90, §79, §137, §138).

USER TOPIC → INTENT → BOOK TYPE → AUDIENCE → MARKET → POSITIONING → TITLE →
AUTHOR → METADATA → RESEARCH → CONTENT PLAN → CONTENT → EDITING → FACT CHECK
→ ORIGINALITY → BOOK-SPECIFIC ENGINE → COLOR ENGINE → ILLUSTRATION/CARTOON →
DESIGN → PAGINATION → PAGE COUNT → COVER → PDF/EPUB → STRUCTURAL QA →
VISUAL QA → COLOR QA → CROSS-FILE QA → REPAIR → REVALIDATION → FINAL AUDIT
→ RELEASE LOCK → EXPORT.

Explicit state machine, dependency-aware invalidation, checkpoint/resume,
truthful progress, no fake PASS (§112, §156, §167).
"""
from __future__ import annotations

import json
import os
import time

from . import classify as C
from .ai import plan_book
from .cartoon import spec_fingerprint, style_fingerprint, default_style
from .classify import BOOK_TYPES, default_author, generate_title, infer_audience, market_analysis
from .color import check_intent
from .compat import validate_config
from .content import generate_content
from .cover import design_cover, validate_cover
from .editorial import run_editorial
from .epubgen import build_epub, check_epub
from .errors import Severity, blocking, critical, warning, FactoryError
from .final_audit import run_final_audit
from .geometry import compute_geometry, verify_independent
from .layout import layout_content
from .originality import check_content_originality, check_asset_provenance
from .pdfcheck import check_pdf
from .pdfgen import build_interior_pdf, build_cover_pdf, deterministic_date
from .render import save_assets
from .renderer import render_page
from .repair import DEFAULT_MAX_REPAIRS, apply_repair, plan_repairs
from .research import ResearchLedger
from .ruleset import Ruleset
from .schemas import Author, Audience, BookConfig, ValidationRecord, utcnow
from .security import sanitize_text
from .state import DependencyState, Stage, StateMachine, ValidationLedger
from .storage import ProjectStore, sha256_bytes
from .typography import DEFAULT_TOKENS
from .validators import (VALIDATOR_VERSION, validate_crossfile, validate_fonts,
                         validate_output_security, validate_pagination,
                         validate_puzzles, validate_writing_space)
from .visualqa import validate_visual

STAGES = [Stage.ANALYZING, Stage.RESEARCHING, Stage.PLANNING, Stage.GENERATING,
          Stage.EDITING, Stage.DESIGNING, Stage.RENDERING, Stage.VALIDATING,
          Stage.AUDITING]

PROGRESS_WEIGHTS = {Stage.ANALYZING: 6, Stage.RESEARCHING: 8, Stage.PLANNING: 12,
                    Stage.GENERATING: 30, Stage.EDITING: 38, Stage.DESIGNING: 44,
                    Stage.RENDERING: 62, Stage.VALIDATING: 86, Stage.AUDITING: 96}


class PipelineError(Exception):
    pass


class Pipeline:
    def __init__(self, store: ProjectStore, ruleset: Ruleset | None = None):
        self.store = store
        self.ruleset = ruleset or Ruleset()

    # ------------------------------------------------------------------
    def create(self, topic: str, overrides: dict | None = None) -> str:
        topic = sanitize_text(topic).strip()
        if not topic:
            raise PipelineError("topic is required (§3)")
        pid = self.store.new_project_id(topic)
        self.store.create(pid)
        cfg = BookConfig(topic=topic, book_type="how_to_guide", category="nonfiction")
        state = {
            "project_id": pid, "created_at": utcnow(), "topic": topic,
            "state": Stage.CREATED.value, "config": cfg.to_dict(),
            "gen_version": 1,
            "deps": DependencyState().to_dict(),
            "decisions": [], "overrides": [], "research": [],
            "page_count": 0, "spine_in": 0.0,
            "ruleset_version": self.ruleset.version,
            "repair_count": 0, "status": "UNVERIFIED",
            "defects": [], "repairs": [], "validation": {},
            "artifacts": {}, "progress": {"stage": "CREATED", "pct": 0,
                                           "detail": "project created",
                                           "validations": 0, "repairs": 0,
                                           "errors": 0, "elapsed_s": 0.0,
                                           "updated_at": utcnow()},
            "release": None, "last_completed_stage": None,
            "errors": [], "warnings": [],
        }
        if overrides:
            state["pending_overrides"] = overrides
        self.store.save_state(pid, state)
        self.store.audit(pid).record("system", "CREATED", "project-created",
                                     inputs={"topic": topic})
        return pid

    # ------------------------------------------------------------------
    def load(self, pid: str) -> dict:
        return self.store.load_state(pid)

    def _save(self, pid: str, state: dict):
        self.store.save_state(pid, state)

    def _progress(self, state: dict, stage: Stage, detail: str, started: float):
        pct = PROGRESS_WEIGHTS.get(stage, 0)
        if state.get("state") == Stage.REVALIDATING.value:
            pct = 90
        state["progress"] = {
            "stage": stage.value, "pct": pct, "detail": detail,
            "validations": len(state.get("validation", {})),
            "repairs": state.get("repair_count", 0),
            "errors": len(state.get("errors", [])),
            "elapsed_s": round(time.time() - started, 2),
            "updated_at": utcnow(),
        }

    def _record_validation(self, state: dict, validator_id: str, artifact_id: str,
                           ok: bool, errors: list, warnings: list, evidence: dict):
        deps = DependencyState.from_dict(state["deps"])
        rec = ValidationRecord(
            validator_id=validator_id, version=VALIDATOR_VERSION,
            artifact_id=artifact_id,
            artifact_version=deps.version_of(artifact_id) if artifact_id in deps.versions else 0,
            ruleset_version=self.ruleset.version, timestamp=utcnow(),
            status="PASS" if ok and not warnings else ("WARN" if ok else "FAIL"),
            errors=[str(e) for e in errors],
            warnings=[str(w) for w in warnings], evidence=evidence)
        state.setdefault("validation", {})[validator_id] = rec.to_dict()
        return rec

    def _record_artifact(self, state: dict, name: str, path: str, node: str):
        from .storage import sha256_file
        deps = DependencyState.from_dict(state["deps"])
        state.setdefault("artifacts", {})[name] = {
            "file": path, "checksum": sha256_file(path) if os.path.exists(path) else "",
            "version": deps.version_of(node), "recorded_at": utcnow(),
        }

    # ------------------------------------------------------------------
    def run(self, pid: str, stop_after: str | None = None) -> dict:
        started = time.time()
        state = self.load(pid)
        if (state.get("release") or {}).get("locked") and state["status"] == "GREEN":
            # §144/§145: locked release; re-running requires invalidation first
            return state
        sm = StateMachine(Stage(state["state"]))
        resume_after = state.get("last_completed_stage")
        skipped = False

        for stage in STAGES:
            if resume_after and not skipped:
                if stage.value == resume_after:
                    skipped = True
                else:
                    continue
            if sm.stage != stage and sm.can(stage):
                sm.transition(stage)
                state["state"] = stage.value
            elif sm.stage != stage:
                # e.g. revalidation loop entry
                pass
            handler = getattr(self, f"_stage_{stage.value.lower()}")
            handler(pid, state, started)
            state["last_completed_stage"] = stage.value
            self._progress(state, stage, f"{stage.value} complete", started)
            self._save(pid, state)
            if stop_after and stage.value == stop_after:
                state["progress"]["detail"] = f"stopped after {stage.value} (interruption test)"
                self._save(pid, state)
                return state

            # repair loop right after RENDERING (page-count boundary repairs)
            if stage == Stage.RENDERING:
                while self._needs_repair(state) and state["repair_count"] < \
                        state["config"]["options"].get("max_repairs", DEFAULT_MAX_REPAIRS):
                    state["state"] = Stage.REPAIRING.value
                    self._stage_repairing(pid, state, started)
                    self._save(pid, state)
                    self._rerun_after_repair(pid, state, started)
                    state["last_completed_stage"] = Stage.RENDERING.value
                    self._save(pid, state)

            # repair loop between VALIDATING and AUDITING
            if stage == Stage.VALIDATING:
                while self._needs_repair(state) and state["repair_count"] < \
                        state["config"]["options"].get("max_repairs", DEFAULT_MAX_REPAIRS):
                    sm2 = StateMachine(Stage(state["state"]))
                    if sm2.can(Stage.REPAIRING):
                        sm2.transition(Stage.REPAIRING)
                        state["state"] = Stage.REPAIRING.value
                    self._stage_repairing(pid, state, started)
                    self._save(pid, state)
                    if stop_after == "REPAIRING":
                        return state
                    self._rerun_after_repair(pid, state, started)  # render chain
                    state["state"] = Stage.REVALIDATING.value
                    self._stage_validating(pid, state, started)  # revalidate
                    state["last_completed_stage"] = Stage.VALIDATING.value
                    self._save(pid, state)
                    if stop_after == "REVALIDATING":
                        return state

        self._finalize(pid, state, started)
        return state

    def _needs_repair(self, state: dict) -> bool:
        return any(d["severity"] in ("BLOCKING", "CRITICAL")
                   for d in state.get("defects", []))

    def _rerun_after_repair(self, pid, state, started):
        """§89: RENDER → VALIDATE → COMPARE → ACCEPT/REPAIR AGAIN. Repairs
        invalidate the upstream chain, so regenerate content + re-edit +
        re-layout + re-render every time (idempotent & deterministic for a
        given seed). Editing MUST rerun too — otherwise its validation result
        goes stale against the new content version and the final audit
        rightfully rejects it."""
        self._stage_generating(pid, state, started)
        self._stage_editing(pid, state, started)
        self._stage_designing(pid, state, started)
        self._stage_rendering(pid, state, started)

    # ============================ STAGES ================================
    def _stage_analyzing(self, pid, state, started):
        self._progress(state, Stage.ANALYZING, "classifying book type & audience", started)
        cfg = BookConfig.from_dict(state["config"])
        overrides = state.pop("pending_overrides", {}) or {}
        plan, model_used = plan_book(cfg.topic, overrides.get("book_type"))
        state["decisions"].append({
            "decision": "book_type", "inputs": {"topic": cfg.topic},
            "rules": "classify.py keyword scoring + deterministic planner",
            "reason": plan["rationale"], "result": plan["book_type"],
            "model": model_used})
        bt = overrides.get("book_type") or plan["book_type"]
        if bt not in C.BOOK_TYPES:
            # explicit instruction respected but impossible as-is (§impossible-rule
            # handling): fail with the nearest valid alternatives listed.
            near = [k for k in C.BOOK_TYPES
                    if k.startswith(bt) or bt in k or k.split("_")[0] == bt.split("_")[0]]
            raise FactoryError(
                id="ERR-BAD-TYPE", severity=Severity.BLOCKING, stage="ANALYZING",
                root_cause="invalid-book-type",
                message=f"Unknown book type '{bt}'. Valid types: "
                        f"{', '.join(sorted(C.BOOK_TYPES))}."
                        + (f" Nearest matches: {', '.join(sorted(near))}." if near else ""),
                remediation="pick one of the listed book types")
        prof = C.BOOK_TYPES[bt]
        cfg.book_type = bt
        cfg.category = prof["category"]
        cfg.color_mode = overrides.get("color_mode") or prof["default_color"]
        cfg.illustration_mode = overrides.get("illustration_mode") or prof["illustration"]
        _trim_override = overrides.get("trim")
        cfg.trim_w, cfg.trim_h = _trim_override or prof["default_trim"]
        cfg.bleed = bool(overrides.get("bleed", prof["bleed"]))
        cfg.target_pages = int(overrides.get("target_pages", prof["target_pages"]))
        cfg.format = overrides.get("format", "combined")
        cfg.binding = overrides.get("binding", "paperback")
        if not _trim_override and \
                self.ruleset.find_trim(cfg.trim_w, cfg.trim_h, cfg.binding) is None:
            # trim was left autonomous ("auto") but the profile default trim is
            # not offered for the chosen binding → autonomously pick the valid
            # trim closest in area and record the decision deterministically.
            import math
            want_area = prof["default_trim"][0] * prof["default_trim"][1]
            best = min(self.ruleset.trims(cfg.binding),
                       key=lambda t: abs(math.log((t.w * t.h) / want_area)))
            old = (cfg.trim_w, cfg.trim_h)
            cfg.trim_w, cfg.trim_h = best.w, best.h
            state["decisions"].append({
                "decision": "trim_autoselect", "stage": "ANALYZING",
                "inputs": {"profile_default_trim": list(old),
                           "binding": cfg.binding},
                "rules": f"ruleset {self.ruleset.version}: {cfg.binding} trim list",
                "reason": f"trim {old[0]}x{old[1]} not offered for {cfg.binding}; "
                          f"nearest-area valid trim chosen autonomously (trim=auto)",
                "result": [cfg.trim_w, cfg.trim_h], "model": "none-deterministic"})
        paper = overrides.get("paper", cfg.paper or "white")
        if paper not in ("white", "cream", "groundwood"):
            raise FactoryError(
                id="ERR-BAD-PAPER", severity=Severity.BLOCKING, stage="ANALYZING",
                root_cause="invalid-paper",
                message=f"Unknown paper '{paper}'. Valid: white, cream, groundwood.",
                remediation="choose white, cream or groundwood")
        if paper in ("cream", "groundwood") and cfg.color_mode != "BLACK_AND_WHITE":
            raise FactoryError(
                id="ERR-PAPER-INK", severity=Severity.BLOCKING, stage="ANALYZING",
                root_cause="paper-ink-mismatch",
                message=f"{paper} paper is only available for BLACK_AND_WHITE ink.",
                remediation="switch color mode to black_and_white or use white paper")
        if cfg.binding == "hardcover" and paper == "groundwood":
            raise FactoryError(
                id="ERR-HC-PAPER", severity=Severity.BLOCKING, stage="ANALYZING",
                root_cause="paper-binding-mismatch",
                message="Groundwood paper is not offered for hardcover binding.",
                remediation="use white or cream paper for hardcover")
        cfg.paper = paper
        cfg.language = overrides.get("language", "en")
        cfg.seed = int(overrides.get("seed", cfg.seed))
        if overrides.get("author"):
            cfg.author = Author(name=overrides["author"], pen_name=False)
        cfg.audience = infer_audience(cfg.topic, bt)
        cfg.options.update({k: v for k, v in overrides.items()
                            if k.startswith("opt_")} and {} or {})
        for k, v in overrides.items():
            if k.startswith("opt."):
                cfg.options[k[4:]] = v
        # compatibility gate BEFORE expensive generation (§116)
        problems = validate_config(cfg, self.ruleset)
        if problems:
            state["errors"] += [critical("ANALYZING", "invalid-config", "config",
                                         "fix configuration", p).to_dict()
                                for p in problems]
            state["state"] = Stage.FAILED.value
            state["status"] = "RED"
            self.store.audit(pid).record("system", "ANALYZING", "config-rejected",
                                         decision="FAIL", reason="; ".join(problems))
            raise PipelineError("invalid configuration: " + "; ".join(problems))
        state["config"] = cfg.to_dict()
        self.store.audit(pid).record("system", "ANALYZING", "classified",
                                     decision=bt, reason=plan["rationale"],
                                     version=state["gen_version"])

    def _stage_researching(self, pid, state, started):
        self._progress(state, Stage.RESEARCHING, "building research ledger", started)
        ledger = ResearchLedger()
        rs = self.ruleset
        ledger.add(
            claim=f"KDP print specifications used: ruleset {rs.version} "
                  f"(trims, bleed 0.125in, gutter bands, spine multipliers)",
            source="Amazon KDP Help: Set Trim Size, Bleed, and Margins",
            url="https://kdp.amazon.com/en_US/help/topic/GVBQ3CMEQW3W2VL6",
            date=rs.retrieval_date, confidence="official",
            usage="geometry, pagination limits, cover formulas")
        ledger.add(
            claim="Spine width = page_count × paper_multiplier (+0.06in paperback)",
            source="KDP cover calculators (kdpeasy.com, makemybookcover.com)",
            url="https://www.kdpeasy.com/faq/covers", date=rs.retrieval_date,
            confidence="multi-source cross-checked", usage="spine/cover geometry")
        ledger.note_no_live_research("offline build; no live market scrape performed")
        state["research"] = ledger.to_list()
        self.store.audit(pid).record("system", "RESEARCHING", "research-recorded",
                                     outputs=[e["id"] for e in state["research"]])

    def _stage_planning(self, pid, state, started):
        self._progress(state, Stage.PLANNING, "market, positioning, title, author", started)
        cfg = BookConfig.from_dict(state["config"])
        market = market_analysis(cfg.book_type, cfg.topic, state.get("research", []))
        market["recorded_on"] = utcnow()[:10]
        state["market"] = market
        t = generate_title(cfg.book_type, cfg.topic, cfg.seed)
        cfg.title = t["title"]; cfg.subtitle = t["subtitle"]
        cfg.series = t["series"]; cfg.edition = t["edition"]
        if cfg.author.name == "A. Publisher":
            cfg.author = default_author(cfg.topic, cfg.seed)
        state["config"] = cfg.to_dict()
        state["decisions"].append({
            "decision": "title", "inputs": {"topic": cfg.topic, "type": cfg.book_type},
            "rules": "title engine templates", "reason": "truthful topic-derived title (§10)",
            "result": cfg.title})
        state["decisions"].append({
            "decision": "author", "inputs": {"seed": cfg.seed},
            "rules": "author engine", "reason": "deterministic original pen name (§11)",
            "result": cfg.author.name})
        self.store.audit(pid).record("system", "PLANNING", "planned",
                                     decision=cfg.title, reason="title/author decided")

    def _stage_generating(self, pid, state, started):
        self._progress(state, Stage.GENERATING, "generating manuscript", started)
        cfg = BookConfig.from_dict(state["config"])
        prof = C.BOOK_TYPES[cfg.book_type]
        content = generate_content(cfg, prof, state.get("research", []))
        # padding pages repair support (§90)
        pad = int(cfg.options.get("pad_pages", 0))
        if pad:
            for i in range(pad):
                content["sections"].append({"title": "", "blocks": [
                    {"t": "ruled", "lines": 26}]})
        content_path = self.store.path(pid, "content", "content.json")
        from .storage import atomic_write_text
        atomic_write_text(content_path, json.dumps(content, ensure_ascii=False,
                                                   default=str))
        state["content_path"] = content_path
        state["word_count"] = content["word_count"]
        state["decisions"].append({
            "decision": "content", "inputs": {"type": cfg.book_type, "seed": cfg.seed},
            "rules": f"content engine '{prof['engine']}'",
            "reason": "book-type-specific generation (§6, §121)",
            "result": f"{len(content['sections'])} sections, {content['word_count']} words"})
        self.store.audit(pid).record("system", "GENERATING", "content-generated",
                                     outputs={"sections": len(content["sections"]),
                                              "words": content["word_count"]})

    def _stage_editing(self, pid, state, started):
        self._progress(state, Stage.EDITING, "editorial + originality review", started)
        cfg = BookConfig.from_dict(state["config"])
        content = self._load_content(pid, state)
        findings = run_editorial(content, cfg.book_type)
        findings += check_content_originality(content, cfg)
        findings += check_asset_provenance([])  # assets checked after render
        for f in findings:
            if f.severity in (Severity.BLOCKING, Severity.CRITICAL):
                state.setdefault("errors", []).append(f.to_dict())
        state["editorial"] = [f.to_dict() for f in findings]
        self._record_validation(state, "editorial", "CONTENT",
                                not any(f.severity in (Severity.BLOCKING, Severity.CRITICAL)
                                        for f in findings),
                                [f.detail for f in findings
                                 if f.severity in (Severity.BLOCKING, Severity.CRITICAL)],
                                [f.detail for f in findings
                                 if f.severity == Severity.WARNING],
                                {"findings": len(findings)})
        self.store.audit(pid).record("system", "EDITING", "editorial-complete",
                                     validation={"findings": len(findings)})
        if any(f.severity == Severity.CRITICAL for f in findings):
            raise PipelineError("critical editorial defect: " +
                                "; ".join(f.detail for f in findings
                                          if f.severity == Severity.CRITICAL))

    def _stage_designing(self, pid, state, started):
        self._progress(state, Stage.DESIGNING, "geometry & design tokens", started)
        cfg = BookConfig.from_dict(state["config"])
        page_estimate = max(24, cfg.target_pages)
        geom = compute_geometry(self.ruleset, cfg.trim_w, cfg.trim_h,
                                page_estimate, cfg.bleed, cfg.binding)
        problems = verify_independent(geom, self.ruleset, cfg.bleed)
        if problems:
            raise PipelineError("independent geometry check failed (§25): " +
                                "; ".join(problems))
        state["geometry"] = geom.to_dict()
        state["decisions"].append({
            "decision": "geometry", "inputs": {"trim": [cfg.trim_w, cfg.trim_h],
                                               "bleed": cfg.bleed},
            "rules": f"ruleset {self.ruleset.version} + independent integer recompute",
            "reason": "primary and independent engines agree (§25)",
            "result": f"page {geom.page_w_in:.3f}x{geom.page_h_in:.3f} in"})
        self.store.audit(pid).record("system", "DESIGNING", "geometry-verified",
                                     validation={"independent": "PASS"})

    def _stage_rendering(self, pid, state, started):
        self._progress(state, Stage.RENDERING, "layout → paginate → render", started)
        cfg = BookConfig.from_dict(state["config"])
        content = self._load_content(pid, state)
        prof = C.BOOK_TYPES[cfg.book_type]

        # page-count limits (§26 boundaries enforced from ruleset)
        try:
            lo, hi = self.ruleset.page_count_limits(cfg.trim_w, cfg.trim_h,
                                                   cfg.color_mode, cfg.paper,
                                                   cfg.binding)
        except Exception as e:
            raise PipelineError(str(e))

        tokens = DEFAULT_TOKENS.scale_for_audience(cfg.audience.age_max)
        scale = float(cfg.options.get("type_scale", 1.0))
        if scale != 1.0:
            from dataclasses import replace
            tokens = replace(tokens, body_size=tokens.body_size * scale,
                             h1_size=tokens.h1_size * scale,
                             h2_size=tokens.h2_size * scale,
                             h3_size=tokens.h3_size * scale)
        if cfg.options.get("high_contrast"):
            from .layout import LayoutColors
            LayoutColors.gray = (0.25, 0.25, 0.27)
            LayoutColors.light = (0.55, 0.55, 0.57)

        geom = compute_geometry(self.ruleset, cfg.trim_w, cfg.trim_h,
                                max(24, cfg.target_pages), cfg.bleed, cfg.binding)
        layout = layout_content(cfg, geom, content, tokens)
        n = layout.stats["page_count"]

        # page-count boundary repair hooks (§90)
        if n < lo:
            cfg.options["pad_needed"] = lo - n
            state.setdefault("defects", []).append(
                blocking("RENDERING", "page-count-below-min", "layout",
                         "add padding pages", f"{n} < KDP minimum {lo}").to_dict())
            state["config"] = cfg.to_dict()
            state["page_count"] = n
            self._save(pid, state)
            return
        if n > hi:
            state.setdefault("defects", []).append(
                blocking("RENDERING", "page-count-above-max", "layout",
                         "reduce content", f"{n} > KDP maximum {hi}").to_dict())
            state["config"] = cfg.to_dict()
            state["page_count"] = n
            self._save(pid, state)
            return

        # recompute geometry with the REAL page count (gutter bands, §24)
        geom = compute_geometry(self.ruleset, cfg.trim_w, cfg.trim_h, n,
                                cfg.bleed, cfg.binding)
        probs = verify_independent(geom, self.ruleset, cfg.bleed)
        if probs:
            raise PipelineError("geometry cross-check failed: " + "; ".join(probs))

        # re-layout if gutter band changed vs estimate
        if abs(geom.gutter_in - float(state["geometry"]["gutter_in"])) > 1e-9:
            state["geometry"] = geom.to_dict()
            layout = layout_content(cfg, geom, content, tokens)
            n = layout.stats["page_count"]

        state["page_count"] = n
        state["geometry"] = geom.to_dict()

        # save assets (§63 provenance + §215 no orphans)
        assets_dir = self.store.path(pid, "assets")
        save_assets(layout.assets, assets_dir)
        used = set()
        for p in layout.pages:
            used.update(p.meta.expected_assets)
        for aid, ent in layout.assets.items():
            ent["record"].pages = sorted(set(ent["record"].pages))
            ent["record"].validation_status = "VALIDATED" if aid in used else "ORPHAN"
        state["assets"] = {aid: ent["record"].to_dict() for aid, ent in layout.assets.items()}
        state["layout_version"] = state["deps"]["versions"].get("LAYOUT", 1)

        # interior PDF
        pdf_bytes = build_interior_pdf(layout, geom, layout.assets, cfg)
        interior_path = self.store.path(pid, "interior.pdf")
        from .storage import atomic_write_bytes
        atomic_write_bytes(interior_path, pdf_bytes)
        self._record_artifact(state, "interior.pdf", interior_path, "INTERIOR_PDF")

        # spine + cover (only after page count is final — §71)
        spine = self.ruleset.spine_width_in(n, cfg.color_mode, cfg.paper, cfg.binding)
        state["spine_in"] = spine
        cover = design_cover(cfg, self.ruleset, n, prof["category"])
        cover_pdf = build_cover_pdf(cover["canvas"], cover["width_in"],
                                    cover["height_in"],
                                    {"title": cfg.title, "author": cfg.author.name,
                                     "subject": "cover",
                                     "date": deterministic_date(cfg.seed)})
        cover_path = self.store.path(pid, "cover.pdf")
        atomic_write_bytes(cover_path, cover_pdf)
        cover_png = cover["canvas"].png()
        from .storage import atomic_write_bytes as _wb
        _wb(self.store.path(pid, "cover.png"), cover_png)
        state["cover"] = {k: v for k, v in cover.items() if k != "canvas"}
        self._record_artifact(state, "cover.pdf", cover_path, "COVER_PDF")

        # EPUB (ebook/combined)
        if cfg.format in ("ebook", "combined"):
            epub_bytes = build_epub(cfg, content, cover_png)
            epub_path = self.store.path(pid, "book.epub")
            atomic_write_bytes(epub_path, epub_bytes)
            self._record_artifact(state, "book.epub", epub_path, "EPUB")

        # full render succeeded: earlier render-stage defects are resolved
        state["defects"] = []
        # layout snapshot for validation stages
        state["_layout_obj"] = None  # never serialize heavy objects
        self._cache[pid] = {"layout": layout, "geom": geom, "content": content,
                            "cover": cover}
        self.store.audit(pid).record("system", "RENDERING", "rendered",
                                     outputs={"pages": n, "spine_in": round(spine, 4),
                                              "assets": len(layout.assets)})

    _cache: dict = {}

    def _cached(self, pid, state):
        if pid in self._cache:
            return self._cache[pid]
        # rebuild from disk after restart (crash recovery §207)
        cfg = BookConfig.from_dict(state["config"])
        content = self._load_content(pid, state)
        from .geometry import compute_geometry as cg
        geom_d = state["geometry"]
        geom = cg(self.ruleset, cfg.trim_w, cfg.trim_h, state["page_count"],
                  cfg.bleed, cfg.binding)
        tokens = DEFAULT_TOKENS.scale_for_audience(cfg.audience.age_max)
        layout = layout_content(cfg, geom, content, tokens)
        prof = C.BOOK_TYPES[cfg.book_type]
        cover = design_cover(cfg, self.ruleset, state["page_count"], prof["category"])
        self._cache[pid] = {"layout": layout, "geom": geom, "content": content,
                            "cover": cover}
        return self._cache[pid]

    def _load_content(self, pid, state):
        path = state.get("content_path") or self.store.path(pid, "content", "content.json")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _stage_validating(self, pid, state, started):
        revalidating = state["state"] == Stage.REVALIDATING.value
        self._progress(state, Stage.REVALIDATING if revalidating else Stage.VALIDATING,
                       "running validators", started)
        cfg = BookConfig.from_dict(state["config"])
        cache = self._cached(pid, state)
        layout, geom, content, cover = (cache["layout"], cache["geom"],
                                        cache["content"], cache["cover"])
        all_defects = []
        ev_all = {}

        # 1. pagination
        d, ev = validate_pagination(layout, geom, cfg)
        all_defects += d; ev_all["pagination"] = ev
        self._record_validation(state, "pagination", "PAGINATION", not d,
                                [x.detail for x in d], [], ev)

        # 2. puzzle correctness (§159)
        d, ev = validate_puzzles(content)
        all_defects += d; ev_all["puzzles"] = ev
        self._record_validation(state, "puzzle-correctness", "CONTENT", not d,
                                [x.detail for x in d], [], ev)

        # 3. writing space / tracing
        d, ev = validate_writing_space(content, cfg.book_type)
        all_defects += d; ev_all["usability"] = ev
        self._record_validation(state, "writing-space", "CONTENT", not d,
                                [x.detail for x in d], [], ev)

        # 4. fonts
        d, ev = validate_fonts(content, cfg)
        all_defects += d; ev_all["fonts"] = ev
        self._record_validation(state, "fonts", "CONTENT", not d,
                                [x.detail for x in d], [], ev)

        # 5. interior PDF structural (§162)
        with open(self.store.path(pid, "interior.pdf"), "rb") as f:
            interior_bytes = f.read()
        chk = check_pdf(interior_bytes, expect_pages=state["page_count"],
                        expect_w_in=geom.page_w_in, expect_h_in=geom.page_h_in,
                        expect_title=cfg.title, expect_author=cfg.author.name)
        pdf_defects = [blocking("PDF", "pdf-structural", "interior.pdf",
                                "rebuild interior PDF", e) for e in chk["errors"]]
        all_defects += pdf_defects; ev_all["pdf"] = dict(chk)
        self._record_validation(state, "pdf-structural", "INTERIOR_PDF", chk["ok"],
                                chk["errors"], chk["warnings"],
                                {"pages": chk.get("page_count"),
                                 "size": [chk.get("page_w_in"), chk.get("page_h_in")]})

        # 6. visual QA on EVERY page (§73, §75) including color (§29, §157)
        d, ev = validate_visual(layout, geom, layout.assets, cfg,
                                self.store.dir(pid),
                                qa_dpi=int(cfg.options.get("qa_dpi", 36)),
                                evidence_dpi=int(cfg.options.get("evidence_dpi", 60)))
        all_defects += d; ev_all["visual"] = {"pages_checked": ev["pages_checked"],
                                              "evidence": ev["evidence_files"]}
        self._record_validation(state, "visual-qa", "VISUAL_QA", not d,
                                [x.detail for x in d], [], 
                                {"pages_checked": ev["pages_checked"]})

        # 7. color intent cross-check (color engine §27/§28)
        d = check_intent(cfg, layout)
        all_defects += d
        self._record_validation(state, "color-mode", "INTERIOR_PDF", not d,
                                [x.detail for x in d], [],
                                {"declared": cfg.color_mode})

        # 8. cover validation (§161)
        d, ev = validate_cover(cover, self.ruleset, cfg)
        all_defects += d; ev_all["cover"] = ev
        self._record_validation(state, "cover", "COVER_PDF", not d,
                                [x.detail for x in d], [], ev)

        # 9. EPUB (§163)
        if cfg.format in ("ebook", "combined"):
            with open(self.store.path(pid, "book.epub"), "rb") as f:
                epub_bytes = f.read()
            r = check_epub(epub_bytes, cfg)
            epub_defects = [blocking("EPUB", "epub-invalid", "book.epub",
                                     "rebuild EPUB", e) for e in r["errors"]]
            all_defects += epub_defects; ev_all["epub"] = r
            self._record_validation(state, "epub", "EPUB", r["ok"], r["errors"],
                                    r["warnings"], {"entries": r.get("entries")})

        # 10. cross-file consistency (§76, §164)
        d, ev = validate_crossfile(state, interior_bytes,
                                   cover if state.get("cover") else None,
                                   epub_bytes if cfg.format in ("ebook", "combined") else None,
                                   cfg)
        all_defects += d; ev_all["crossfile"] = ev
        self._record_validation(state, "crossfile", "CROSSFILE_QA", not d,
                                [x.detail for x in d], [], ev)

        # 11. security of outputs (§78)
        d, ev = validate_output_security(self.store.dir(pid), pid)
        all_defects += d; ev_all["security"] = ev
        self._record_validation(state, "output-security", "EXPORT", not d,
                                [x.detail for x in d], [], ev)

        # 12. asset provenance (§15)
        d = check_asset_provenance([a["record"] for a in layout.assets.values()])
        all_defects += d
        self._record_validation(state, "asset-provenance", "ILLUSTRATIONS", not d,
                                [x.detail for x in d], [],
                                {"assets": len(layout.assets)})

        # ---- compose status from ACTUAL results (§200) ------------------
        # drop resolved defects on revalidation
        state["defects"] = [d.to_dict() for d in all_defects]
        state["warnings"] = [d.to_dict() for d in all_defects
                             if d.severity in (Severity.WARNING, Severity.NON_BLOCKING)]
        blocking_count = sum(1 for d in all_defects if d.severity.blocks_release)
        warning_count = len(state["warnings"])
        state["evidence_summary"] = ev_all
        if blocking_count:
            state["status"] = "RED"
        elif warning_count:
            state["status"] = "YELLOW"
        else:
            state["status"] = "GREEN"
        self.store.audit(pid).record("system", "VALIDATING", "validators-run",
                                     validation={"blocking": blocking_count,
                                                 "warnings": warning_count,
                                                 "validators": len(state["validation"])})

    def _stage_repairing(self, pid, state, started):
        self._progress(state, Stage.REPAIRING, "repairing defects", started)
        cfg = BookConfig.from_dict(state["config"])
        defects = [type("D", (), d) for d in state.get("defects", [])]
        # rebuild simple namespace objects for plan_repairs
        class D:
            pass
        ds = []
        for dd in state.get("defects", []):
            o = D(); o.__dict__.update(dd)
            o.severity = Severity(dd["severity"])
            ds.append(o)
        plans = plan_repairs(ds, cfg, state)
        deps = DependencyState.from_dict(state["deps"])
        audit = self.store.audit(pid)
        if not plans:
            # no known repair for a blocking defect → honest failure (§149)
            state["repair_count"] = state.get("config", {}).get(
                "options", {}).get("max_repairs", DEFAULT_MAX_REPAIRS)
            return
        for plan in plans:
            desc = apply_repair(plan, cfg, None)
            affected = deps.invalidate(plan["invalidate"],
                                       f"repair {plan['action']} for {plan['root_cause']}")
            state["repairs"].append({**plan, "description": desc,
                                     "affected": affected, "at": utcnow()})
            state["repair_count"] = state.get("repair_count", 0) + 1
            audit.record("system", "REPAIRING", "repair-applied",
                         repair=plan, reason=plan["detail"],
                         decision=plan["action"])
        # all repair-invalidated validations are stale now (§82)
        state["deps"] = deps.to_dict()
        state["config"] = cfg.to_dict()
        state["defects"] = []
        state["last_completed_stage"] = Stage.EDITING.value  # redo design→render→validate
        self._cache.pop(pid, None)

    def _stage_auditing(self, pid, state, started):
        self._progress(state, Stage.AUDITING, "independent final audit", started)
        report = run_final_audit(self.store, pid, state, self.ruleset)
        state["final_audit"] = report
        self._record_validation(state, "final-audit", "FINAL_AUDIT", report["pass"],
                                report["failures"], report["warnings"],
                                {"checks": report["checks_run"]})
        self.store.audit(pid).record("system", "AUDITING", "final-audit",
                                     validation={"pass": report["pass"],
                                                 "checks": report["checks_run"]})

    # ------------------------------------------------------------------
    def _finalize(self, pid, state, started):
        audit_pass = state.get("final_audit", {}).get("pass", False)
        blocking_defects = sum(1 for d in state.get("defects", [])
                               if d["severity"] in ("BLOCKING", "CRITICAL"))
        # freshness (§166): any stale validation kills GREEN
        deps = DependencyState.from_dict(state["deps"])
        ledger = ValidationLedger()
        ledger.results = list(state.get("validation", {}).values())
        stale = ledger.stale_count(deps)
        status = state["status"]
        if blocking_defects or not audit_pass or stale or status == "RED":
            state["status"] = "RED"
            state["state"] = Stage.FAILED.value if not audit_pass and not blocking_defects \
                else Stage.FAILED.value
        elif state.get("warnings"):
            state["status"] = "YELLOW"
            state["state"] = Stage.VERIFIED_WITH_WARNINGS.value
        else:
            state["status"] = "GREEN"
            state["state"] = Stage.VERIFIED.value
            # release lock (§144) — immutable snapshot metadata
            state["release"] = {
                "locked": True, "locked_at": utcnow(),
                "build_id": f"build-{state['gen_version']}-{utcnow().replace(':', '')}",
                "status": "100% SOFTWARE VERIFIED",
                "known_defects": {"critical": 0, "blocking": 0},
                "disclaimer": ("Software verification only. Amazon review, physical "
                               "print behaviour and market performance are outside "
                               "software control (§151–§154)."),
            }
        state["progress"] = {
            "stage": state["state"], "pct": 100,
            "detail": f"final status {state['status']}",
            "validations": len(state.get("validation", {})),
            "repairs": state.get("repair_count", 0),
            "errors": len(state.get("errors", [])),
            "elapsed_s": round(time.time() - started, 2),
            "updated_at": utcnow(),
        }
        self._save(pid, state)
        self.store.audit(pid).record("system", "FINAL", "status-derived",
                                     decision=state["status"],
                                     reason=f"blocking={blocking_defects} "
                                            f"audit={audit_pass} stale={stale}")

    # ------------------------------------------------------------------
    def apply_override(self, pid: str, field: str, value) -> dict:
        """Human override (§140): recorded, and dependent artifacts are
        invalidated through the dependency graph (§165)."""
        state = self.load(pid)
        if (state.get("release") or {}).get("locked"):
            # §145: mutating a locked release invalidates it
            state["release"] = {**state["release"], "locked": False,
                                "invalidated_by": f"override {field}={value}",
                                "invalidated_at": utcnow()}
        cfg = BookConfig.from_dict(state["config"])
        node = "CONFIG"
        if field == "color_mode":
            cfg.color_mode = value
        elif field == "trim":
            cfg.trim_w, cfg.trim_h = value
        elif field == "author":
            cfg.author = Author(name=value, pen_name=False)
        elif field == "title":
            cfg.title = value
        elif field == "target_pages":
            cfg.target_pages = int(value)
        elif field == "book_type":
            cfg.book_type = value
            node = "CONFIG"
        else:
            raise PipelineError(f"unsupported override field {field}")
        state["config"] = cfg.to_dict()
        state["overrides"].append({"field": field, "value": str(value), "at": utcnow()})
        deps = DependencyState.from_dict(state["deps"])
        affected = deps.invalidate(node, f"user override {field}={value}")
        state["deps"] = deps.to_dict()
        state["status"] = "UNVERIFIED"
        state["last_completed_stage"] = None
        state["release"] = state.get("release") and {**state["release"], "locked": False}
        state["defects"] = []
        self._cache.pop(pid, None)
        self._save(pid, state)
        self.store.audit(pid).record("user", "OVERRIDE", "override-applied",
                                     decision=f"{field}={value}",
                                     reason="human override (§140)")
        return state
