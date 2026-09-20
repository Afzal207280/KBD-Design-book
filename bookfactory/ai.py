"""AI services layer (spec §130, §131, §201, §202, §203, §204).

AI-as-advisor: deterministic validators decide deterministic facts (§201).
The default authoring model is fully deterministic. If an external model
endpoint is configured (KBF_AI_ENDPOINT), it is used for planning advice
only, with strict schema validation, timeouts, retries and fallback —
malformed output is NEVER treated as valid (§130, §131), and AI output can
never control paths, code, permissions or final status (§203).
"""
from __future__ import annotations

import json
import os
import urllib.request

from .security import contain_ai_output
from .classify import classify_book_type, infer_audience

PLAN_SCHEMA = {
    "book_type": str,
    "rationale": str,
    "audience_label": str,
    "suggested_trim": str,
    "confidence": float,
}


class AIFailure(Exception):
    pass


class DeterministicAuthor:
    """Rule-based planning model — the guaranteed fallback (§204)."""
    model_id = "deterministic-author/1.0"

    def plan(self, topic: str, explicit_type: str | None = None) -> dict:
        bt, profile = classify_book_type(topic, explicit_type)
        aud = infer_audience(topic, bt)
        return {
            "book_type": bt,
            "rationale": f"keyword/classification rules mapped '{topic}' to {bt}",
            "audience_label": aud.label,
            "suggested_trim": f"{profile['default_trim'][0]}x{profile['default_trim'][1]}",
            "confidence": 0.9,
        }


class RemoteAuthor:
    """Optional external model adapter (never required)."""
    model_id = "remote-model"

    def __init__(self, endpoint: str, timeout: float = 8.0, retries: int = 2):
        self.endpoint = endpoint
        self.timeout = timeout
        self.retries = retries

    def plan(self, topic: str, explicit_type: str | None = None) -> dict:
        last_err = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(
                    self.endpoint,
                    data=json.dumps({"task": "plan_book", "topic": topic,
                                     "explicit_type": explicit_type}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = json.loads(r.read().decode("utf-8"))
                return contain_ai_output(raw, PLAN_SCHEMA)  # §131
            except Exception as e:  # timeout / malformed / schema violation
                last_err = e
        raise AIFailure(f"remote model failed after retries: {last_err}")


def get_author():
    endpoint = os.environ.get("KBF_AI_ENDPOINT", "")
    if endpoint:
        return RemoteAuthor(endpoint)
    return DeterministicAuthor()


def plan_book(topic: str, explicit_type: str | None = None) -> tuple[dict, str]:
    """Returns (validated_plan, model_used). Falls back on any failure,
    preserving deterministic behaviour (§204)."""
    author = get_author()
    try:
        plan = author.plan(topic, explicit_type)
        plan = contain_ai_output(plan, PLAN_SCHEMA)
        return plan, author.model_id
    except AIFailure:
        fb = DeterministicAuthor()
        return fb.plan(topic, explicit_type), fb.model_id + " (fallback)"
