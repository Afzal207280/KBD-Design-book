"""Asset persistence (spec §63, §102, §103): every generated asset is saved
with identity, version and checksum — no orphan artifacts (§215)."""
from __future__ import annotations

import os

from .storage import atomic_write_bytes, sha256_bytes


def save_assets(assets: dict, assets_dir: str) -> None:
    os.makedirs(assets_dir, exist_ok=True)
    for aid, ent in assets.items():
        png = ent["canvas"].png()
        path = os.path.join(assets_dir, f"{aid}.png")
        atomic_write_bytes(path, png)
        ent["record"].file = path
        ent["record"].checksum = sha256_bytes(png)
