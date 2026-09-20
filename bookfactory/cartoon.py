"""Cartoon engine (spec §38–§44, §120, §158, §238).

* Character bible (§39) and style bible (§41) are stored, versioned data.
* Storyboard (§42) is created BEFORE final illustration generation.
* Characters are drawn by a deterministic parametric renderer from their
  bible parameters; consistency validation (§40) compares every rendered
  illustration's recorded spec fingerprint and sampled pixels against the
  bible — drift (face/clothing/color/proportion/species/style) is detected.
* Every illustration maps to project/page/scene/characters/style/version/
  prompt/validation status (§43).
"""
from __future__ import annotations

import hashlib
import json
import random

from .schemas import CharacterSpec, StyleBible

DEFAULT_PALETTES = {
    "fox": {"fur": (0.86, 0.47, 0.20), "belly": (0.98, 0.93, 0.84),
            "accent": (0.30, 0.20, 0.16), "shirt": (0.20, 0.42, 0.65)},
    "rabbit": {"fur": (0.82, 0.82, 0.85), "belly": (0.97, 0.97, 0.98),
               "accent": (0.90, 0.60, 0.66), "shirt": (0.35, 0.62, 0.45)},
    "bear": {"fur": (0.55, 0.40, 0.28), "belly": (0.85, 0.74, 0.60),
             "accent": (0.25, 0.18, 0.12), "shirt": (0.70, 0.25, 0.25)},
    "cat": {"fur": (0.93, 0.75, 0.30), "belly": (0.99, 0.95, 0.85),
            "accent": (0.40, 0.28, 0.12), "shirt": (0.45, 0.30, 0.62)},
    "child": {"fur": (0.96, 0.83, 0.68), "belly": (0.99, 0.96, 0.90),
              "accent": (0.35, 0.24, 0.16), "shirt": (0.85, 0.45, 0.25)},
}


def default_style() -> StyleBible:
    return StyleBible(
        art_style="flat cartoon", line_style="clean rounded outlines",
        line_width_pt=2.4, rendering="flat fills, no gradients",
        lighting="even ambient",
        palette=["#DB7834", "#F8EDD6", "#34567A", "#5C9E74", "#B3523F",
                 "#F2C14E", "#7A9E9F", "#FFFFFF"],
        perspective="frontal / simple", facial_style="simple dot eyes, curved mouth",
        background="simple shapes", complexity="age-appropriate, low clutter",
        age_appropriate=True)


def build_character_bible(seed: int, cast: list[str] | None = None) -> list[CharacterSpec]:
    """Deterministic original characters (§15: no existing IP)."""
    rng = random.Random(seed ^ 0xC4A7)
    cast = cast or rng.sample(["fox", "rabbit", "bear", "cat"], 2) + ["child"]
    names = {"fox": ["Rusty", "Ember", "Maple"], "rabbit": ["Clover", "Pip", "Hazel"],
             "bear": ["Bruno", "Bramble", "Cocoa"], "cat": ["Sunny", "Tofu", "Miso"],
             "child": ["Mia", "Leo", "Ana"]}
    out = []
    for i, species in enumerate(cast):
        pal = DEFAULT_PALETTES[species]
        name = names[species][rng.randrange(len(names[species]))]
        spec = CharacterSpec(
            char_id=f"char-{i:02d}", name=name,
            age="child" if species == "child" else "young",
            species=species,
            proportions={"head_ratio": 0.42, "body_ratio": 0.58,
                         "ear": {"fox": "pointed", "rabbit": "long", "bear": "round",
                                 "cat": "pointed", "child": "none"}[species]},
            face={"eyes": "two black dots", "mouth": "simple curve",
                  "cheeks": "soft blush" if species != "child" else "rosy"},
            hair={"type": {"fox": "fur", "rabbit": "fur", "bear": "fur",
                           "cat": "fur", "child": "short hair"}[species],
                  "color": "dark" if species != "child" else "#5B3A29"},
            clothing={"item": "shirt", "color": pal["shirt"]},
            colors={k: (v if isinstance(v, str) else v) for k, v in pal.items()},
            accessories=rng.sample(["scarf", "cap", "backpack", "none"], 1),
            personality=rng.choice(["curious", "brave", "gentle", "playful"]),
            identifiers=[f"{species} silhouette", "signature shirt color"])
        out.append(spec)
    return out


def spec_fingerprint(spec: CharacterSpec) -> str:
    """Canonical fingerprint of the bible entry (§40 drift detection)."""
    canon = json.dumps(spec.to_dict(), sort_keys=True, ensure_ascii=False,
                       default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def style_fingerprint(style: StyleBible) -> str:
    canon = json.dumps(style.to_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Parametric character renderer
# ---------------------------------------------------------------------------

def _c255(t):
    if isinstance(t, str):  # hex
        t = t.lstrip("#")
        return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(int(round(min(1.0, max(0.0, v)) * 255)) for v in t)


def render_character(canvas, cx: float, baseline_y: float, height_pt: float,
                     spec: CharacterSpec, style: StyleBible | None = None):
    """Draw the character from its bible parameters. Deterministic."""
    style = style or default_style()
    line_w = style.line_width_pt
    ink = (30, 28, 26)
    pal = spec.colors
    fur = _c255(pal.get("fur", (0.8, 0.6, 0.4)))
    belly = _c255(pal.get("belly", (0.95, 0.9, 0.8)))
    shirt = _c255(pal.get("shirt", (0.3, 0.5, 0.7)))
    hr = float(spec.proportions.get("head_ratio", 0.42))
    head_h = height_pt * hr
    body_h = height_pt - head_h
    head_r = head_h / 2
    head_cy = baseline_y - body_h - head_r
    # body (rounded rectangle ≈ rect + belly)
    bw = height_pt * 0.36
    canvas.fill_rect(cx - bw / 2, baseline_y - body_h, bw, body_h, shirt)
    canvas.frame_rect(cx - bw / 2, baseline_y - body_h, bw, body_h, ink, line_w)
    canvas.fill_rect(cx - bw * 0.28, baseline_y - body_h * 0.82, bw * 0.56,
                     body_h * 0.62, belly)
    # ears by species
    ear = spec.proportions.get("ear", "round")
    if ear == "pointed":
        canvas.poly([(cx - head_r * 0.7, head_cy - head_r * 0.5),
                     (cx - head_r * 0.95, head_cy - head_r * 1.45),
                     (cx - head_r * 0.15, head_cy - head_r * 0.95)], ink,
                    filled=False, width_pt=line_w)
        canvas.poly([(cx + head_r * 0.7, head_cy - head_r * 0.5),
                     (cx + head_r * 0.95, head_cy - head_r * 1.45),
                     (cx + head_r * 0.15, head_cy - head_r * 0.95)], ink,
                    filled=False, width_pt=line_w)
    elif ear == "long":
        for s in (-1, 1):
            canvas.fill_rect(cx + s * head_r * 0.5 - head_r * 0.16,
                             head_cy - head_r * 2.1, head_r * 0.32, head_r * 1.3, fur)
            canvas.frame_rect(cx + s * head_r * 0.5 - head_r * 0.16,
                              head_cy - head_r * 2.1, head_r * 0.32, head_r * 1.3,
                              ink, line_w * 0.8)
    elif ear == "round":
        for s in (-1, 1):
            canvas.circle(cx + s * head_r * 0.7, head_cy - head_r * 0.8,
                          head_r * 0.35, fur)
            canvas.circle(cx + s * head_r * 0.7, head_cy - head_r * 0.8,
                          head_r * 0.35, ink, filled=False, width_pt=line_w * 0.8)
    # head
    canvas.circle(cx, head_cy, head_r, fur)
    canvas.circle(cx, head_cy, head_r, ink, filled=False, width_pt=line_w)
    canvas.circle(cx, head_cy + head_r * 0.32, head_r * 0.55, belly)
    # face (style-bible facial style: dot eyes + curved mouth)
    for s in (-1, 1):
        canvas.circle(cx + s * head_r * 0.42, head_cy - head_r * 0.12,
                      head_r * 0.09, ink)
    canvas.line(cx - head_r * 0.25, head_cy + head_r * 0.42,
                cx + head_r * 0.25, head_cy + head_r * 0.42, ink, line_w * 0.9)
    # arms
    for s in (-1, 1):
        canvas.line(cx + s * bw / 2, baseline_y - body_h * 0.75,
                    cx + s * (bw / 2 + height_pt * 0.12), baseline_y - body_h * 0.45,
                    ink, line_w)
    # legs
    for s in (-1, 1):
        canvas.line(cx + s * bw * 0.22, baseline_y, cx + s * bw * 0.22,
                    baseline_y, ink, line_w)
    # accessory (§39 identifier)
    acc = spec.accessories[0] if spec.accessories else "none"
    if acc == "scarf":
        canvas.fill_rect(cx - bw * 0.42, baseline_y - body_h - head_r * 0.15,
                         bw * 0.84, head_r * 0.30, _c255((0.75, 0.2, 0.2)))
    elif acc == "cap":
        canvas.fill_rect(cx - head_r * 0.8, head_cy - head_r * 1.02,
                         head_r * 1.6, head_r * 0.35, _c255((0.2, 0.3, 0.55)))
    return canvas


def character_pixel_signature(canvas, cx: float, baseline_y: float,
                              height_pt: float) -> dict:
    """Sample deterministic anchor pixels around the rendered character so
    visual QA can prove the right character with the right colors is there."""
    samples = {}
    def at(dx, dy):
        x = int((cx + dx * height_pt) * canvas.scale)
        y = int((baseline_y + dy * height_pt) * canvas.scale)
        if 0 <= x < canvas.w and 0 <= y < canvas.h:
            return canvas.get(x, y)
        return None
    samples["head_center"] = at(0, -0.78)
    samples["body_center"] = at(0, -0.30)
    samples["belly"] = at(0, -0.45)
    return samples


# ---------------------------------------------------------------------------
# Storyboard (§42) + traceability (§43)
# ---------------------------------------------------------------------------

def build_storyboard(seed: int, pages: int, characters: list[CharacterSpec],
                     theme: str, dialogue_bank: list[str]) -> list[dict]:
    """Page-level storyboard BEFORE illustration generation."""
    rng = random.Random(seed ^ 0x5B0A)
    cast = [c.char_id for c in characters]
    bgs = [{"sky": (0.62, 0.80, 0.94), "ground": (0.55, 0.75, 0.45),
            "hills": [0.25, 0.7], "tree": True, "sun": True},
           {"sky": (0.98, 0.85, 0.62), "ground": (0.60, 0.55, 0.42),
            "hills": [0.5], "house": True, "sun": True},
           {"sky": (0.55, 0.62, 0.85), "ground": (0.35, 0.45, 0.35),
            "hills": [0.3, 0.6, 0.85], "tree": True, "sun": False},
           {"sky": (0.75, 0.88, 0.95), "ground": (0.50, 0.70, 0.50),
            "hills": [0.4], "tree": True, "house": True, "sun": True}]
    boards = []
    places = ["the hill", "the garden", "the shore", "the meadow", "the bridge",
              "the old oak", "the market", "the cozy house"]
    times = ["In the morning", "After lunch", "When the stars appeared",
             "Just before sunset", "On a rainy afternoon", "At first light",
             "Later that day", "When the wind calmed down"]
    for p in range(pages):
        scene = dict(rng.choice(bgs))
        n_chars = min(len(cast), rng.choice((1, 2, 2, len(cast))))
        chars = rng.sample(cast, n_chars)
        action = rng.choice(["explores", "discovers", "shares", "helps",
                             "celebrates", "rests", "plays", "watches"])
        place = places[p % len(places)]
        who = " and ".join(c.split("-")[-1].title() for c in chars[:2])
        narration = (f"{times[p % len(times)]}, {who} {action} {place}. "
                     f"{theme.title()} felt close by.")
        boards.append({
            "page": p,
            "scene": f"scene-{p:02d}",
            "action": action,
            "characters": chars,
            "background": scene,
            "composition": "characters center, horizon upper third",
            "narration": narration,
            "dialogue": [rng.choice(dialogue_bank)] if rng.random() < 0.7 else [],
            "visual_intent": "warm, readable, age-appropriate",
        })
    return boards


def make_illustration_record(project_id: str, page: int, scene_id: str,
                             char_ids: list, style_fp: str, color_mode: str,
                             asset_version: int, config: dict) -> dict:
    """§43 cartoon traceability record."""
    return {
        "project": project_id, "page": page, "scene": scene_id,
        "characters": list(char_ids), "style_fingerprint": style_fp,
        "color_mode": color_mode, "asset_version": asset_version,
        "prompt_or_config": config, "validation_status": "PENDING",
    }
