"""Web application (spec §114, §137, §188–§190, §220, §221).

Every UI control is wired to real backend behaviour (§114): creating a
project runs the actual pipeline in a worker thread; progress shown is the
real recorded progress (§137); downloads serve the real verified artifacts.

Binds 0.0.0.0 for the sandbox preview proxy. The website URL injected into
exports is derived from the request Host header, so the START_HERE launcher
inside every downloaded package points straight back to THIS website.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookfactory.exporter import build_export_package
from bookfactory.pipeline import Pipeline, PipelineError
from bookfactory.ruleset import Ruleset
from bookfactory.storage import ProjectStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = ProjectStore(os.path.join(ROOT, "projects"))
RULESET = Ruleset()
PIPE = Pipeline(STORE, RULESET)
WORKERS: dict[str, threading.Thread] = {}
EXPORT_CACHE: dict[str, dict] = {}

INDEX_HTML = os.path.join(ROOT, "app", "static", "index.html")


def _public_state(state: dict) -> dict:
    """Strip heavy/internal fields before sending to the browser (§78)."""
    out = {k: v for k, v in state.items()
           if k not in ("_layout_obj",) and not k.startswith("_")}
    return out


def _website_url(handler) -> str:
    host = handler.headers.get("Host", "localhost:8010")
    proto = "http" if host.startswith(("localhost", "127.0.0.1")) else "https"
    return f"{proto}://{host}"


class Handler(BaseHTTPRequestHandler):
    server_version = "BookFactory/1.0"

    # ---- helpers ---------------------------------------------------------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: str, ctype: str, download_name: str | None = None):
        if not os.path.exists(path):
            return self._json({"error": "not found"}, 404)
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if download_name:
            from urllib.parse import quote
            self.send_header("Content-Disposition",
                             f'attachment; filename="{quote(download_name)}"')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # §136 log safety
        from bookfactory.security import redact
        sys.stderr.write(redact("%s - %s\n" % (self.address_string(), fmt % args)))

    # ---- routes ------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/":
                return self._file(INDEX_HTML, "text/html; charset=utf-8")
            if path == "/health":
                return self._json({"ok": True, "app": "KDP Book Factory",
                                   "version": "1.0.0",
                                   "ruleset": RULESET.version})
            if path == "/api/ruleset":
                return self._json({"version": RULESET.version,
                                   "retrieved": RULESET.retrieval_date,
                                   "sources": RULESET.data["sources"],
                                   "trims": [t.label() for t in RULESET.trims()]})
            if path == "/api/booktypes":
                from bookfactory.classify import BOOK_TYPES
                return self._json({"types": sorted(BOOK_TYPES)})
            if path == "/api/projects":
                ids = STORE.list_projects()
                out = []
                for pid in ids[-30:]:
                    try:
                        s = STORE.load_state(pid)
                        out.append({"project_id": pid, "topic": s.get("topic"),
                                    "status": s.get("status"),
                                    "state": s.get("state"),
                                    "title": s.get("config", {}).get("title"),
                                    "progress": s.get("progress", {}).get("pct")})
                    except Exception:
                        continue
                return self._json({"projects": list(reversed(out))})
            if path.startswith("/api/projects/"):
                parts = path.split("/")
                pid = parts[3]
                if not STORE.exists(pid):
                    return self._json({"error": "unknown project"}, 404)
                if len(parts) == 4:
                    return self._json(_public_state(STORE.load_state(pid)))
                sub = parts[4]
                state = STORE.load_state(pid)
                if sub == "download":
                    if pid not in EXPORT_CACHE:
                        EXPORT_CACHE[pid] = build_export_package(
                            STORE, pid, state, _website_url(self))
                    return self._file(EXPORT_CACHE[pid]["path"], "application/zip",
                                      EXPORT_CACHE[pid]["name"])
                if sub == "artifact":
                    name = parts[5]
                    allowed = {"interior.pdf", "cover.pdf", "cover.png", "book.epub"}
                    if name not in allowed:
                        return self._json({"error": "not allowed"}, 403)
                    return self._file(STORE.path(pid, name),
                                      {"interior.pdf": "application/pdf",
                                       "cover.pdf": "application/pdf",
                                       "cover.png": "image/png",
                                       "book.epub": "application/epub+zip"}[name],
                                      name)
                if sub == "preview":
                    fn = parts[5]
                    if fn.isdigit():  # numeric alias: /preview/3 -> evidence-0004.png
                        fn = f"evidence-{int(fn) + 1:04d}.png"
                    from bookfactory.security import safe_join
                    p = safe_join(STORE.path(pid, "renders"), os.path.basename(fn))
                    return self._file(p, "image/png")
                return self._json({"error": "unknown endpoint"}, 404)
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # no silent errors (§88)
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            if path == "/api/projects":
                topic = (body.get("topic") or "").strip()
                if not topic:
                    return self._json({"error": "topic is required"}, 400)
                overrides = body.get("overrides") or {}
                pid = PIPE.create(topic, overrides)
                def worker():
                    try:
                        PIPE.run(pid)
                    except PipelineError as e:
                        s = STORE.load_state(pid)
                        s["state"] = "FAILED"
                        s["status"] = "RED"
                        s.setdefault("errors", []).append(str(e))
                        STORE.save_state(pid, s)
                    except Exception as e:
                        STORE.audit(pid).record("system", "RUN", "unexpected-error",
                                                reason=traceback.format_exc()[-2000:])
                        s = STORE.load_state(pid)
                        s["state"] = "FAILED"
                        s["status"] = "RED"
                        s.setdefault("errors", []).append(str(e))
                        STORE.save_state(pid, s)
                t = threading.Thread(target=worker, daemon=True)
                WORKERS[pid] = t
                t.start()
                return self._json({"project_id": pid, "started": True})
            if path == "/api/projects/resume":
                pid = body.get("project_id")
                if not pid or not STORE.exists(pid):
                    return self._json({"error": "unknown project"}, 404)
                t = threading.Thread(target=PIPE.run, args=(pid,), daemon=True)
                t.start()
                return self._json({"project_id": pid, "resumed": True})
            if path.startswith("/api/projects/") and path.endswith("/override"):
                pid = path.split("/")[3]
                if not STORE.exists(pid):
                    return self._json({"error": "unknown project"}, 404)
                state = PIPE.apply_override(pid, body.get("field"), body.get("value"))
                t = threading.Thread(target=PIPE.run, args=(pid,), daemon=True)
                t.start()
                return self._json({"ok": True, "status": state.get("status")})
            return self._json({"error": "not found"}, 404)
        except (PipelineError, ValueError) as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            return self._json({"error": str(e)}, 500)


def main(host="0.0.0.0", port=8010):
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"KDP Book Factory listening on http://{host}:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main(port=int(os.environ.get("KBF_PORT", "8010")))
