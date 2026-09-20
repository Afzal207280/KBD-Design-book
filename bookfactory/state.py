"""Explicit state machine + dependency graph (spec §80, §81, §82, §165, §166).

Illegal transitions are rejected. Invalidation propagates downstream and
marks every dependent validation stale ("no stale PASS", §82/§166).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Stage(str, enum.Enum):
    CREATED = "CREATED"
    ANALYZING = "ANALYZING"
    RESEARCHING = "RESEARCHING"
    PLANNING = "PLANNING"
    GENERATING = "GENERATING"
    EDITING = "EDITING"
    DESIGNING = "DESIGNING"
    RENDERING = "RENDERING"
    VALIDATING = "VALIDATING"
    REPAIRING = "REPAIRING"
    REVALIDATING = "REVALIDATING"
    AUDITING = "AUDITING"
    VERIFIED = "VERIFIED"
    VERIFIED_WITH_WARNINGS = "VERIFIED_WITH_WARNINGS"
    FAILED = "FAILED"


# Legal forward transitions. FAILED is reachable from anywhere.
_TRANSITIONS = {
    Stage.CREATED: {Stage.ANALYZING, Stage.FAILED},
    Stage.ANALYZING: {Stage.RESEARCHING, Stage.FAILED},
    Stage.RESEARCHING: {Stage.PLANNING, Stage.FAILED},
    Stage.PLANNING: {Stage.GENERATING, Stage.FAILED},
    Stage.GENERATING: {Stage.EDITING, Stage.REPAIRING, Stage.FAILED},
    Stage.EDITING: {Stage.DESIGNING, Stage.REPAIRING, Stage.FAILED},
    Stage.DESIGNING: {Stage.RENDERING, Stage.REPAIRING, Stage.FAILED},
    Stage.RENDERING: {Stage.VALIDATING, Stage.REPAIRING, Stage.FAILED},
    Stage.VALIDATING: {Stage.REPAIRING, Stage.AUDITING, Stage.FAILED},
    Stage.REPAIRING: {Stage.RENDERING, Stage.REVALIDATING, Stage.FAILED},
    Stage.REVALIDATING: {Stage.REPAIRING, Stage.AUDITING, Stage.FAILED},
    Stage.AUDITING: {Stage.VERIFIED, Stage.VERIFIED_WITH_WARNINGS,
                     Stage.REPAIRING, Stage.FAILED},
    # re-entry for incremental edits / invalidation reruns (§19, §140)
    Stage.VERIFIED: {Stage.ANALYZING, Stage.DESIGNING, Stage.RENDERING,
                     Stage.VALIDATING, Stage.FAILED},
    Stage.VERIFIED_WITH_WARNINGS: {Stage.ANALYZING, Stage.DESIGNING,
                                   Stage.RENDERING, Stage.VALIDATING, Stage.FAILED},
    Stage.FAILED: {Stage.REPAIRING, Stage.ANALYZING},
}


class IllegalTransition(Exception):
    pass


class StateMachine:
    def __init__(self, initial: Stage = Stage.CREATED):
        self.stage = initial

    def transition(self, target: Stage) -> None:
        if target not in _TRANSITIONS.get(self.stage, set()):
            raise IllegalTransition(f"{self.stage.value} -> {target.value} is illegal")
        self.stage = target

    def can(self, target: Stage) -> bool:
        return target in _TRANSITIONS.get(self.stage, set())


# --------------------------------------------------------------------------
# Dependency graph (§81). Each node carries a monotonically increasing
# version; invalidating a node bumps it and every downstream node.
# --------------------------------------------------------------------------

DEPENDENCIES = {
    "INTENT": [],
    "CONFIG": ["INTENT"],
    "RESEARCH": ["CONFIG"],
    "CONTENT_PLAN": ["CONFIG", "RESEARCH"],
    "CONTENT": ["CONTENT_PLAN"],
    "EDITING": ["CONTENT"],
    "CHARACTERS": ["CONFIG", "CONTENT_PLAN"],
    "ILLUSTRATIONS": ["CONTENT", "CHARACTERS"],
    "DESIGN": ["CONFIG", "EDITING"],
    "LAYOUT": ["DESIGN", "EDITING", "ILLUSTRATIONS"],
    "PAGINATION": ["LAYOUT"],
    "PAGE_COUNT": ["PAGINATION"],
    "SPINE": ["PAGE_COUNT", "CONFIG"],
    "COVER": ["SPINE", "PAGE_COUNT", "CONFIG"],
    "INTERIOR_PDF": ["PAGINATION", "PAGE_COUNT"],
    "COVER_PDF": ["COVER"],
    "EPUB": ["EDITING", "COVER"],
    "VISUAL_QA": ["INTERIOR_PDF", "COVER_PDF"],
    "STRUCTURAL_QA": ["INTERIOR_PDF", "COVER_PDF", "EPUB"],
    "CROSSFILE_QA": ["STRUCTURAL_QA"],
    "FINAL_AUDIT": ["VISUAL_QA", "CROSSFILE_QA"],
    "EXPORT": ["FINAL_AUDIT"],
    "RELEASE": ["EXPORT"],
}


def _downstream(node: str) -> set[str]:
    out, stack = set(), [node]
    while stack:
        n = stack.pop()
        for cand, deps in DEPENDENCIES.items():
            if n in deps and cand not in out:
                out.add(cand)
                stack.append(cand)
    return out


@dataclass
class DependencyState:
    versions: dict = field(default_factory=lambda: {n: 1 for n in DEPENDENCIES})
    invalidated: dict = field(default_factory=dict)   # node -> [reasons]

    def invalidate(self, node: str, reason: str) -> list[str]:
        """Bump node + all downstream versions. Returns affected nodes."""
        if node not in DEPENDENCIES:
            raise KeyError(f"unknown dependency node {node}")
        affected = [node] + sorted(_downstream(node))
        for n in affected:
            self.versions[n] += 1
            self.invalidated.setdefault(n, []).append(reason)
        return affected

    def version_of(self, node: str) -> int:
        return self.versions[node]

    def to_dict(self):
        return {"versions": dict(self.versions), "invalidated": dict(self.invalidated)}

    @staticmethod
    def from_dict(d: dict) -> "DependencyState":
        ds = DependencyState()
        ds.versions = {**ds.versions, **d.get("versions", {})}
        ds.invalidated = dict(d.get("invalidated", {}))
        return ds


class ValidationLedger:
    """Tracks freshness of validation results against artifact versions
    (§82, §166). A PASS recorded against an older version is STALE."""

    def __init__(self):
        self.results = []  # ValidationRecord dicts

    def add(self, rec_dict: dict):
        self.results.append(rec_dict)

    def latest_for(self, artifact_id: str, validator_id: str,
                   current_version: int) -> dict | None:
        best = None
        for r in self.results:
            if r["artifact_id"] == artifact_id and r["validator_id"] == validator_id:
                if best is None or r["artifact_version"] > best["artifact_version"]:
                    best = r
        if best is None:
            return None
        best = dict(best)
        best["fresh"] = best["artifact_version"] == current_version
        return best

    def stale_count(self, deps: DependencyState) -> int:
        n = 0
        for r in self.results:
            v = deps.versions.get(r["artifact_id"])
            if v is not None and r["artifact_version"] != v:
                n += 1
        return n

    def to_list(self):
        return list(self.results)
