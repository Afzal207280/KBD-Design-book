"""Originality / IP engine (spec §15): no infringing material, no unauthorized
characters, no trademark misuse, no copied visual concepts, provenance checks.
"""
from __future__ import annotations

import re

from .errors import blocking, critical

# Registered/owned marks and characters we must never emit.
BLOCKED_TERMS = [
    "disney", "mickey mouse", "marvel", "dc comics", "harry potter", "hogwarts",
    "pokemon", "pokémon", "pikachu", "barbie", "lego", "bluey", "peppa pig",
    "paw patrol", "minecraft", "roblox", "star wars", "hello kitty", "nintendo",
    "mario", "sonic", "coca cola", "coca-cola", "nike", "adidas", "gucci",
    "taylor swift", "beyonce", "beyoncé", "dr seuss", "seuss", "winnie the pooh",
    "spongebob", "frozen", "elsa", "batman", "spider-man", "spiderman",
    "superman", "shrek", "minions", "paw patrol", "teenage mutant",
]

PUBLIC_DOMAIN_OK_NOTE = ("Only original characters/marks may be used; public-domain "
                         "figures are allowed but must be our own rendition.")


def scan_text(text: str) -> list[str]:
    t = text.lower()
    return [term for term in BLOCKED_TERMS if re.search(rf"\b{re.escape(term)}\b", t)]


def check_content_originality(content, cfg) -> list:
    findings = []
    texts = []
    texts.append(cfg.title or "")
    texts.append(cfg.subtitle or "")
    texts.append(cfg.topic or "")
    for sec in content["sections"]:
        for b in sec["blocks"]:
            for key in ("text", "q"):
                if isinstance(b.get(key), str):
                    texts.append(b[key])
            if b.get("t") == "recipe":
                texts.append(str(b.get("data", {}).get("name", "")))
            if b.get("t") == "poem":
                texts += b.get("stanzas", [[]])[0] if b.get("stanzas") else []
    for c in content.get("characters", []):
        texts.append(str(c.get("name", "")) + " " + str(c.get("species", "")))
    hits = set()
    for t in texts:
        hits.update(scan_text(t))
    if hits:
        findings.append(critical(
            "ORIGINALITY", "blocked-trademark-or-character", "content",
            "remove blocked IP terms from topic/title/content (§15)",
            sorted(hits)))
    # shingle self-overlap between chapters (copied-paste inside book)
    shingle_sets = []
    for sec in content["sections"]:
        words = " ".join(b.get("text", "") for b in sec["blocks"]
                         if b.get("t") == "p").lower().split()
        shingle_sets.append({tuple(words[i:i + 6]) for i in range(len(words) - 6)})
    for i in range(len(shingle_sets)):
        for j in range(i + 1, len(shingle_sets)):
            a, b = shingle_sets[i], shingle_sets[j]
            # tiny sections (<8 shingles) cannot support a similarity claim
            if not a or not b or min(len(a), len(b)) < 8:
                continue
            inter = len(a & b) / max(1, min(len(a), len(b)))
            if inter > 0.35:
                findings.append(blocking(
                    "ORIGINALITY", "excessive-internal-similarity", "content",
                    "rewrite duplicated sections",
                    f"sections {i} and {j} share {inter:.0%} of 6-grams"))
    return findings


def check_asset_provenance(assets: list) -> list:
    """§15 copied visual concepts: every asset must have a known, legal
    generation method recorded; unknown sources fail closed (§232)."""
    findings = []
    for a in assets:
        src = a.source if hasattr(a, "source") else a.get("source")
        method = a.method if hasattr(a, "method") else a.get("method")
        if src not in ("deterministic-vector", "deterministic-raster", "user-provided"):
            findings.append(blocking(
                "ORIGINALITY", "unknown-asset-provenance", "asset",
                "record asset provenance before use", f"{a.asset_id if hasattr(a, 'asset_id') else a}"))
        if not method:
            findings.append(blocking(
                "ORIGINALITY", "asset-method-missing", "asset",
                "record generation method", str(a)))
    return findings
