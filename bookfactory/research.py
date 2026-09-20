"""Research engine (spec §8, §12): traceable research ledger.

Never fabricates citations/studies/statistics. Only actually-retrieved
sources are recorded; absence of live research is recorded as such.
"""
from __future__ import annotations

import itertools
from .schemas import utcnow

_ids = itertools.count(1)


class ResearchLedger:
    def __init__(self):
        self.entries = []

    def add(self, claim: str, source: str, url: str, date: str, confidence: str,
            usage: str) -> dict:
        e = {
            "id": f"RES-{next(_ids):04d}",
            "claim": claim, "source": source, "url": url, "date": date,
            "retrieved_at": utcnow(), "confidence": confidence, "usage": usage,
        }
        self.entries.append(e)
        return e

    def note_no_live_research(self, reason: str) -> dict:
        return self.add(
            claim="No live market research was performed for this build.",
            source="internal", url="", date=utcnow()[:10],
            confidence="n/a", usage=f"market analysis limited to internal conventions: {reason}")

    def to_list(self):
        return list(self.entries)
