# KDP Book Factory

A production-grade, autonomous pipeline that turns **one topic string** into a
complete, software-verified KDP book package:

```
topic → classify → research → plan → content → edit → design → layout →
render (interior PDF + cover PDF + EPUB) → validate → repair → final audit → export
```

**Core rule:** a build is `GREEN` only when every validator ran for real and
passed with zero critical/blocking defects and a passing final audit.
No mocked PASSes, no metadata-only PASSes.

## Run it

```bash
# CLI — one command, full pipeline
python3 cli.py build "gratitude journal for nurses" --type gratitude_journal
python3 cli.py types                      # list the ~73 book types
python3 cli.py status <project_id>
python3 cli.py export <project_id> --url https://your-host

# Web app (binds 0.0.0.0:8010)
python3 app/server.py

# Tests (52 tests: geometry, puzzles, validators TP/TN, E2E, determinism)
python3 -m unittest discover tests
```

Overrides: `--type`, `--color` (black_and_white|grayscale|full_color|mixed_color),
`--binding` (paperback|hardcover), `--paper` (white|cream|groundwood),
`--author`, `--pages`.

## What is verified, for real, on every build

| Validator | Checks |
|---|---|
| geometry | trim/bleed/gutter per official KDP ruleset, cross-checked by an independent milli-inch engine + Decimal spine/cover recompute |
| pagination | sequential pages, no duplicate chapter starts, book-length bounds |
| puzzle-correctness | every sudoku/word-search/maze re-solved by an **independent** verifier; every puzzle maps to an answer-key entry |
| writing-space | planners/journals/logbooks actually contain usable ruled space |
| fonts | WinAnsi coverage only; unsupported glyphs are blocking defects |
| pdf-structural | real parse of the generated PDF (page count, sizes) |
| visual-qa | raster-rendered pages compared against layout oracle (margins, bleed reach, blank pages, ink density, color-mode chroma) |
| color-mode | declared color mode vs. actual pixel chroma (§27–§32) |
| cover | dimensions vs. independent formula, spine text rules (≥79 pp), barcode zone, spine-safe area |
| epub | mimetype-first zip, spine/reading order, well-formed XHTML |
| crossfile | interior page count ↔ cover spine width ↔ EPUB title consistency |
| output-security | no secrets/paths/PII leaks in outputs |
| editorial | repetition, terminology consistency, time-sensitive-fact dating |
| final-audit | re-reads artifacts from disk, recomputes geometry/spine/cover independently, re-verifies every checksum, scans for staleness |

The ruleset (`data/kdp_ruleset.json`) encodes the official KDP values fetched
2026-09-20 (trim sizes, bleed 0.125", gutter bands 0.375"–0.875", page-count
ranges per trim/ink/paper, hardcover 75–550) plus third-party spine/cover
formulas (recorded with sources in the file).

## Determinism & recovery

* Identical topic+seed → **byte-identical** interior PDF, cover PDF and EPUB
  (verified in `tests/test_pipeline_e2e.py`). PDF/EPUB timestamps are
  seed-derived, never wall-clock.
* Crash recovery: `Pipeline.run(pid, stop_after=…)` + a fresh `Pipeline`
  instance resumes from disk (state machine + dependency graph + fcntl locks).
* Any upstream change invalidates downstream artifacts via a dependency graph;
  a PASS recorded against an older artifact version is **stale** and the final
  audit refuses it.

## Export package

`exports/<Title>-<project_id>.zip` — assembled atomically (temp dir +
`os.replace`), then re-opened and every entry's sha256 re-verified. Contains:

* `interior.pdf`, `cover.pdf`, `cover.png`, `book.epub`
* `metadata.json` (config snapshot), `validation_report.json`, `audit.jsonl`
* `manifest.json` (per-file sha256 + build status)
* `previews/` evidence renders
* **`START_HERE.html` + `BookFactory.url`** — a one-click launcher back into
  the factory website; when downloaded via the web app the link is injected
  from the request's `Host` header.

## Honest boundaries (§151–§154)

This software verifies what software can verify: geometry, pagination, puzzle
correctness, file integrity, color-mode conformance, packaging. It **never**
claims or guarantees:

* Amazon/KDP acceptance or review outcomes,
* physical print perfection (order a proof copy),
* sales, rankings or reviews.

Time-sensitive facts carry their research date; nothing is fabricated.
