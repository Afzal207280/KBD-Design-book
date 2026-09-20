"""Atomic export engine (spec §104, §105, §106, §211, §212, §214, §222–§225).

Builds the final package in a temp dir and renames it into place — an
incomplete package is NEVER presented as final. After packaging, the zip is
re-opened and every entry's checksum is re-verified (§211 final recheck).

The package also contains START_HERE.html + BookFactory.url — direct links
back into the factory website (user requirement: the downloaded file carries
a one-click entry into the web app).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile

from .storage import atomic_write_bytes, sha256_file
from .schemas import utcnow


def _launcher_html(state: dict, website_url: str) -> str:
    cfg = state["config"]
    pid = state["project_id"]
    safe_url = website_url.replace('"', "%22")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>START HERE — {cfg['title']}</title>
<meta http-equiv="refresh" content="6;url={safe_url}">
<style>
 body {{ font-family: system-ui, sans-serif; background:#101418; color:#e8ecef;
        display:flex; align-items:center; justify-content:center; min-height:100vh;
        margin:0; }}
 .card {{ background:#1a2129; border:1px solid #2c3947; border-radius:14px;
         padding:42px 54px; max-width:640px; text-align:center; }}
 h1 {{ margin:0 0 6px; font-size:26px; }}
 p.sub {{ color:#9fb0c0; margin-top:0; }}
 a.btn {{ display:inline-block; margin-top:22px; padding:14px 34px;
         background:#2e7d5b; color:white; text-decoration:none;
         border-radius:8px; font-size:18px; font-weight:600; }}
 a.btn:hover {{ background:#37956d; }}
 .meta {{ margin-top:26px; font-size:13px; color:#7d8ea0; text-align:left; }}
</style>
</head>
<body>
<div class="card">
  <h1>{cfg['title']}</h1>
  <p class="sub">{cfg.get('subtitle', '')}</p>
  <p>This package was produced and verified by the <b>KDP Book Factory</b>.</p>
  <a class="btn" href="{safe_url}">Open the Book Factory website &rarr;</a>
  <p style="color:#9fb0c0;font-size:13px">If it does not open automatically, this page
     redirects you in a few seconds.</p>
  <div class="meta">
    Project: {pid}<br>
    Status: {state.get('status', 'UNVERIFIED')} &nbsp;|&nbsp;
    Pages: {state.get('page_count', '?')} &nbsp;|&nbsp;
    Trim: {cfg['trim_w']}&quot; x {cfg['trim_h']}&quot; &nbsp;|&nbsp;
    Color: {cfg['color_mode']}<br>
    Ruleset: {state.get('ruleset_version', '?')} &nbsp;|&nbsp;
    Built: {utcnow()}
  </div>
</div>
</body>
</html>"""


def _url_file(website_url: str) -> str:
    return f"[InternetShortcut]\nURL={website_url}\n"


def build_export_package(store, pid: str, state: dict, website_url: str) -> dict:
    """Assemble, zip atomically, re-verify. Returns manifest dict."""
    pdir = store.dir(pid)
    cfg = state["config"]
    from .security import safe_filename
    base = safe_filename(f"{cfg['title']}-{pid}")
    tmp_dir = os.path.join(pdir, "exports", f".tmp-{base}-{os.getpid()}")
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir)
    manifest = {
        "project_id": pid,
        "build_id": (state.get("release") or {}).get("build_id", f"build-{state['gen_version']}"),
        "version": state.get("gen_version"),
        "ruleset": state.get("ruleset_version"),
        "created_at": utcnow(),
        "final_status": state.get("status"),
        "website": website_url,
        "files": {},
    }

    def add(name: str, src: str):
        if not os.path.exists(src):
            raise FileNotFoundError(f"required artifact missing: {src} (§216)")
        dst = os.path.join(tmp_dir, name)
        os.makedirs(os.path.dirname(dst) or dst, exist_ok=True)
        shutil.copy2(src, dst)
        manifest["files"][name] = {"sha256": sha256_file(dst),
                                   "bytes": os.path.getsize(dst)}

    add("interior.pdf", os.path.join(pdir, "interior.pdf"))
    add("cover.pdf", os.path.join(pdir, "cover.pdf"))
    add("cover.png", os.path.join(pdir, "cover.png"))
    if cfg["format"] in ("ebook", "combined"):
        add("book.epub", os.path.join(pdir, "book.epub"))

    # metadata.json — the single source of truth snapshot (§141 config snapshot)
    meta = {
        "title": cfg["title"], "subtitle": cfg["subtitle"],
        "author": cfg["author"], "series": cfg["series"], "edition": cfg["edition"],
        "book_type": cfg["book_type"], "category": cfg["category"],
        "audience": cfg["audience"], "language": cfg["language"],
        "trim": [cfg["trim_w"], cfg["trim_h"]], "bleed": cfg["bleed"],
        "binding": cfg["binding"], "color_mode": cfg["color_mode"],
        "paper": cfg["paper"], "page_count": state["page_count"],
        "spine_in": state.get("spine_in"),
        "cover": {k: v for k, v in (state.get("cover") or {}).items()
                  if k in ("width_in", "height_in", "spine_in", "ruleset_version")},
        "word_count": state.get("word_count"),
        "configuration": cfg,
        "environment": {"app_version": "1.0.0", "renderer": "bookfactory.raster/1.0",
                        "pdf_engine": "bookfactory.pdfgen/1.0",
                        "ruleset": state.get("ruleset_version")},
        "disclaimer": ("Software-verified package. Amazon review, physical print and "
                       "market outcomes are outside software control (§151–§154)."),
    }
    meta_path = os.path.join(tmp_dir, "metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    manifest["files"]["metadata.json"] = {"sha256": sha256_file(meta_path),
                                          "bytes": os.path.getsize(meta_path)}

    # validation report (§223 machine + human readable)
    report = {
        "project_id": pid, "generated_at": utcnow(),
        "final_status": state.get("status"),
        "validators": state.get("validation", {}),
        "defects": state.get("defects", []),
        "warnings": state.get("warnings", []),
        "repairs": state.get("repairs", []),
        "final_audit": state.get("final_audit"),
        "decisions": state.get("decisions", []),
        "market": state.get("market"),
        "research": state.get("research", []),
    }
    rep_path = os.path.join(tmp_dir, "validation_report.json")
    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False, default=str)
    manifest["files"]["validation_report.json"] = {"sha256": sha256_file(rep_path),
                                                   "bytes": os.path.getsize(rep_path)}

    # audit export (§222)
    audit_src = os.path.join(pdir, "audit", "audit.jsonl")
    if os.path.exists(audit_src):
        add("audit.jsonl", audit_src)

    # build manifest (§143) written last with its own checksum added after
    man_path = os.path.join(tmp_dir, "manifest.json")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)

    # visual evidence previews (§147)
    renders = os.path.join(pdir, "renders")
    if os.path.isdir(renders):
        os.makedirs(os.path.join(tmp_dir, "previews"), exist_ok=True)
        for fn in sorted(os.listdir(renders))[:8]:
            if fn.endswith(".png"):
                add(f"previews/{fn}", os.path.join(renders, fn))

    # launcher with direct website link (user requirement)
    launcher_path = os.path.join(tmp_dir, "START_HERE.html")
    with open(launcher_path, "w", encoding="utf-8") as f:
        f.write(_launcher_html(state, website_url))
    manifest["files"]["START_HERE.html"] = {"sha256": sha256_file(launcher_path),
                                            "bytes": os.path.getsize(launcher_path)}
    urlfile = os.path.join(tmp_dir, "BookFactory.url")
    with open(urlfile, "w", encoding="utf-8") as f:
        f.write(_url_file(website_url))
    manifest["files"]["BookFactory.url"] = {"sha256": sha256_file(urlfile),
                                            "bytes": os.path.getsize(urlfile)}
    # rewrite manifest including launcher checksums
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    manifest["manifest_sha256"] = sha256_file(man_path)

    # ---- atomic zip (§105) ------------------------------------------------
    zip_name = f"{base}.zip"
    zip_tmp = os.path.join(pdir, "exports", f".{zip_name}.part")
    with zipfile.ZipFile(zip_tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(tmp_dir):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, tmp_dir))
    zip_final = os.path.join(pdir, "exports", zip_name)
    os.replace(zip_tmp, zip_final)          # commit point
    shutil.rmtree(tmp_dir)

    # ---- final recheck (§211): reopen & verify every entry -----------------
    with zipfile.ZipFile(zip_final) as z:
        names = set(z.namelist())
        for name, info in manifest["files"].items():
            if name not in names:
                raise IOError(f"package self-check failed: {name} missing (§212)")
            data = z.read(name)
            if hashlib.sha256(data).hexdigest() != info["sha256"]:
                raise IOError(f"package self-check failed: {name} checksum (§212)")
    return {"path": zip_final, "name": zip_name, "manifest": manifest,
            "bytes": os.path.getsize(zip_final)}
