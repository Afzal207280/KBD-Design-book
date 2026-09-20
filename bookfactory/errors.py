"""Error taxonomy (spec §87, §88).

Every error raised by the factory carries:
  id, severity, stage, root_cause, affected_artifact, remediation, retryable.

No swallowed exceptions: every caught exception must be recorded in the
audit log before being converted or re-raised (§88).
"""
from __future__ import annotations

import enum
import itertools
from dataclasses import dataclass, field
from typing import Optional

_counter = itertools.count(1)


class Severity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    NON_BLOCKING = "NON_BLOCKING"
    BLOCKING = "BLOCKING"
    CRITICAL = "CRITICAL"

    @property
    def blocks_release(self) -> bool:
        return self in (Severity.BLOCKING, Severity.CRITICAL)


@dataclass(frozen=True)
class FactoryError(Exception):
    """Structured error. Raising it never loses context (§88)."""
    id: str
    severity: Severity
    stage: str
    root_cause: str
    message: str
    affected_artifact: Optional[str] = None
    remediation: str = ""
    retryable: bool = False
    cause: Optional[str] = None

    def __post_init__(self):
        super().__init__(self.message)

    def to_dict(self):
        return {
            "error_id": self.id,
            "severity": self.severity.value,
            "stage": self.stage,
            "root_cause": self.root_cause,
            "message": self.message,
            "affected_artifact": self.affected_artifact,
            "remediation": self.remediation,
            "retryable": self.retryable,
        }


class Defect:
    """A recorded defect (validation finding). Severity decides gates (§108)."""
    __slots__ = ("id", "severity", "stage", "root_cause", "artifact",
                 "remediation", "detail")

    def __init__(self, severity, stage, root_cause, artifact, remediation, detail):
        self.id = f"DEF-{next(_counter):05d}"
        self.severity = severity
        self.stage = stage
        self.root_cause = root_cause
        self.artifact = artifact
        self.remediation = remediation
        self.detail = detail

    def to_dict(self):
        return {"id": self.id, "severity": self.severity.value, "stage": self.stage,
                "root_cause": self.root_cause, "artifact": self.artifact,
                "remediation": self.remediation, "detail": self.detail}


def blocking(stage, cause, artifact, remediation, detail) -> Defect:
    return Defect(Severity.BLOCKING, stage, cause, artifact, remediation, detail)


def critical(stage, cause, artifact, remediation, detail) -> Defect:
    return Defect(Severity.CRITICAL, stage, cause, artifact, remediation, detail)


def warning(stage, cause, artifact, remediation, detail) -> Defect:
    return Defect(Severity.WARNING, stage, cause, artifact, remediation, detail)
