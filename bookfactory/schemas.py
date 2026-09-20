"""Schema-first project model (spec §198).

Strict dataclasses for Project / Book / Content / Page / Asset / Character /
Style / Color / Layout / Cover / Validation / Repair / Build / Export.
AI output is validated against these schemas before downstream use (§131).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

COLOR_MODES = ("BLACK_AND_WHITE", "GRAYSCALE", "FULL_COLOR", "MIXED_COLOR")
BINDINGS = ("paperback", "hardcover")
FORMATS = ("print", "ebook", "combined")


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class Audience:
    age_min: int = 18
    age_max: int = 99
    label: str = "adult"
    reading_level: str = "adult-general"
    vocabulary: str = "general"

    def to_dict(self):
        return asdict(self)


@dataclass
class Author:
    name: str
    pen_name: bool = False
    role: str = "author"
    contributors: list = field(default_factory=list)  # [{name, role}]

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Author":
        return Author(name=d["name"], pen_name=bool(d.get("pen_name", False)),
                      role=d.get("role", "author"),
                      contributors=list(d.get("contributors", [])))


@dataclass
class BookConfig:
    """Single source of truth for production parameters (§197)."""
    topic: str
    book_type: str
    category: str
    format: str = "combined"          # print | ebook | combined
    binding: str = "paperback"
    trim_w: float = 6.0
    trim_h: float = 9.0
    bleed: bool = False
    color_mode: str = "BLACK_AND_WHITE"
    paper: str = "white"
    illustration_mode: str = "none"   # none|line_art|grayscale|color|cartoon
    language: str = "en"
    target_pages: int = 120
    title: str = ""
    subtitle: str = ""
    series: str = ""
    edition: str = ""
    author: Author = field(default_factory=lambda: Author(name="A. Publisher"))
    audience: Audience = field(default_factory=Audience)
    seed: int = 20260920
    options: dict = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        d["author"] = self.author.to_dict()
        d["audience"] = self.audience.to_dict()
        return d

    @staticmethod
    def from_dict(d: dict) -> "BookConfig":
        a = dict(d)
        a["author"] = Author.from_dict(a.get("author", {"name": "A. Publisher"}))
        aud = a.pop("audience", {})
        cfg = BookConfig(**{k: v for k, v in a.items() if k in BookConfig.__dataclass_fields__})
        if aud:
            cfg.audience = Audience(**{k: v for k, v in aud.items()
                                       if k in Audience.__dataclass_fields__})
        return cfg


@dataclass
class ValidationRecord:
    """Validation contract (§199). Status is derived, never asserted (§200)."""
    validator_id: str
    version: str
    artifact_id: str
    artifact_version: int
    ruleset_version: str
    timestamp: str
    status: str                      # PASS | FAIL | WARN | ERROR
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("PASS", "WARN")

    def to_dict(self):
        return asdict(self)


@dataclass
class PageMeta:
    """Page-level validation model (§62)."""
    page_id: str
    index: int                       # 0-based
    page_type: str
    expected_color_mode: str
    expected_assets: list = field(default_factory=list)
    text_regions: list = field(default_factory=list)   # [(x,y,w,h), inches]
    footer_regions: list = field(default_factory=list)  # page numbers etc.
    image_regions: list = field(default_factory=list)
    safe_regions: list = field(default_factory=list)
    bleed_regions: list = field(default_factory=list)
    is_blank: bool = False
    source_version: int = 0
    validation_results: list = field(default_factory=list)
    actual_color_mode: Optional[str] = None
    actual_assets: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class Asset:
    """Asset provenance (§63)."""
    asset_id: str
    kind: str                        # illustration|icon|chart|cover_art|...
    source: str                      # deterministic-vector|ai-model|user
    method: str
    version: int = 1
    pages: list = field(default_factory=list)
    transforms: list = field(default_factory=list)
    validation_status: str = "UNVALIDATED"
    checksum: str = ""
    file: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class CharacterSpec:
    """Character bible entry (§39)."""
    char_id: str
    name: str
    age: str = ""
    species: str = "human"
    proportions: dict = field(default_factory=dict)   # head_ratio etc.
    face: dict = field(default_factory=dict)
    hair: dict = field(default_factory=dict)
    clothing: dict = field(default_factory=dict)
    colors: dict = field(default_factory=dict)        # hex palette
    accessories: list = field(default_factory=list)
    personality: str = ""
    identifiers: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CharacterSpec":
        return CharacterSpec(**{k: v for k, v in d.items()
                                if k in CharacterSpec.__dataclass_fields__})


@dataclass
class StyleBible:
    """Cartoon style bible (§41)."""
    art_style: str = "flat cartoon"
    line_style: str = "clean rounded outlines"
    line_width_pt: float = 2.4
    rendering: str = "flat fills, no gradients"
    lighting: str = "even ambient"
    palette: list = field(default_factory=list)       # hex list
    perspective: str = "frontal / simple"
    facial_style: str = "simple dot eyes, curved mouth"
    background: str = "simple shapes"
    complexity: str = "age-appropriate, low clutter"
    age_appropriate: bool = True

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "StyleBible":
        return StyleBible(**{k: v for k, v in d.items()
                             if k in StyleBible.__dataclass_fields__})
