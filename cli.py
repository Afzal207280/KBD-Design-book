#!/usr/bin/env python3
"""CLI entry point: one input → complete verified book package.

  python3 cli.py build "gratitude journal for nurses" [--type gratitude_journal]
  python3 cli.py status <project_id>
  python3 cli.py export <project_id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bookfactory.classify import BOOK_TYPES
from bookfactory.exporter import build_export_package
from bookfactory.pipeline import Pipeline
from bookfactory.ruleset import Ruleset
from bookfactory.storage import ProjectStore

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(prog="bookfactory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("topic")
    b.add_argument("--type", dest="book_type")
    b.add_argument("--color", dest="color_mode")
    b.add_argument("--format", dest="format")
    b.add_argument("--binding")
    b.add_argument("--paper")
    b.add_argument("--author")
    b.add_argument("--pages", type=int, dest="target_pages")
    b.add_argument("--store", default=os.path.join(ROOT, "projects"))
    s = sub.add_parser("status"); s.add_argument("pid")
    s.add_argument("--store", default=os.path.join(ROOT, "projects"))
    e = sub.add_parser("export"); e.add_argument("pid")
    e.add_argument("--store", default=os.path.join(ROOT, "projects"))
    e.add_argument("--url", default="http://localhost:8010")
    t = sub.add_parser("types")
    args = ap.parse_args()

    if args.cmd == "types":
        print("\n".join(sorted(BOOK_TYPES)))
        return
    store = ProjectStore(args.store)
    pipe = Pipeline(store, Ruleset())
    if args.cmd == "build":
        overrides = {k: getattr(args, k) for k in
                     ("book_type", "color_mode", "format", "binding", "paper",
                      "author", "target_pages") if getattr(args, k, None)}
        pid = pipe.create(args.topic, overrides)
        print(f"project: {pid}")
        try:
            state = pipe.run(pid)
        except Exception as e:  # structured failure, never a raw traceback (§88)
            print(json.dumps({"project_id": pid, "status": "RED",
                              "error": str(e)}, indent=1))
            sys.exit(1)
        print(json.dumps({
            "project_id": pid, "status": state["status"], "state": state["state"],
            "pages": state["page_count"], "title": state["config"]["title"],
            "repairs": state.get("repair_count", 0),
            "defects": len(state.get("defects", [])),
            "final_audit": state.get("final_audit", {}).get("pass"),
        }, indent=1))
        if state["status"] == "GREEN":
            out = build_export_package(store, pid, state,
                                       "http://localhost:8010")
            print("package:", out["path"])
    elif args.cmd == "status":
        st = store.load_state(args.pid)
        print(json.dumps({k: st.get(k) for k in
                          ("state", "status", "page_count", "progress",
                           "repair_count", "defects")}, indent=1, default=str))
    elif args.cmd == "export":
        st = store.load_state(args.pid)
        out = build_export_package(store, args.pid, st, args.url)
        print(out["path"])


if __name__ == "__main__":
    main()
