"""Project storage (spec §79, §101, §102, §103, §104, §129, §205, §206, §207).

Structured on-disk project:

  projects/<id>/
    project.json      authoritative project state (§197)
    audit.jsonl       append-only audit log (§86)
    checkpoints/      stage checkpoints for resumability (§79)
    content/          generated content artifacts
    assets/           generated assets
    renders/          rendered page images (visual evidence §147)
    exports/          final packages (atomic, §105)
    versions/         version history of project.json

Writes are atomic (tmp file + rename). Concurrent access is serialized with
an fcntl lock file (§129). A crash can never leave a half-written
project.json (§206, §207): the rename is the commit point.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import time

from .schemas import utcnow


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_text(path: str, text: str) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_bytes(path: str, data: bytes) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextlib.contextmanager
def project_lock(project_dir: str, timeout: float = 30.0):
    """Cross-process lock (§129 concurrency safety)."""
    os.makedirs(project_dir, exist_ok=True)
    lock_path = os.path.join(project_dir, ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    deadline = time.time() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() > deadline:
                os.close(fd)
                raise TimeoutError(f"could not lock {project_dir} within {timeout}s")
            time.sleep(0.02)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class AuditLog:
    """Append-only structured audit trail (§86). Every major operation is
    recorded with timestamp/actor/stage/io/version/decision/reason."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def record(self, actor: str, stage: str, action: str, *, inputs=None,
               outputs=None, version=None, decision=None, reason=None,
               validation=None, repair=None):
        entry = {
            "ts": utcnow(), "actor": actor, "stage": stage, "action": action,
            "input": inputs, "output": outputs, "version": version,
            "decision": decision, "reason": reason,
            "validation": validation, "repair": repair,
        }
        from .security import redact
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def read(self) -> list:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


class ProjectStore:
    """Structured project storage root (§101)."""

    SUBDIRS = ("checkpoints", "content", "assets", "layouts", "renders",
               "validation", "repairs", "audit", "exports", "versions")

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    # -- identity ------------------------------------------------------
    def new_project_id(self, topic: str) -> str:
        from .security import safe_filename
        base = safe_filename(topic)[:32] or "project"
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        pid = f"{base}-{stamp}"
        # never overwrite: collision-safe suffix (§133 unintended deletion)
        i = 1
        while os.path.exists(os.path.join(self.root, pid)):
            i += 1
            pid = f"{base}-{stamp}-{i}"
        return pid

    def dir(self, pid: str) -> str:
        from .security import safe_join
        return safe_join(self.root, pid)

    def path(self, pid: str, *parts: str) -> str:
        from .security import safe_join
        return safe_join(self.dir(pid), *parts)

    def create(self, pid: str) -> str:
        d = self.dir(pid)
        for sub in self.SUBDIRS:
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        return d

    def list_projects(self) -> list[str]:
        if not os.path.isdir(self.root):
            return []
        return sorted(p for p in os.listdir(self.root)
                      if os.path.isdir(os.path.join(self.root, p))
                      and os.path.exists(os.path.join(self.root, p, "project.json")))

    # -- project state (atomic + locked) --------------------------------
    def save_state(self, pid: str, state: dict) -> None:
        d = self.dir(pid)
        with project_lock(d):
            # version history (§83) — keep prior version for recovery (§104)
            p = os.path.join(d, "project.json")
            if os.path.exists(p):
                hist = os.path.join(d, "versions")
                os.makedirs(hist, exist_ok=True)
                v = state.get("gen_version", 0)
                with open(p, "r", encoding="utf-8") as f:
                    atomic_write_text(os.path.join(hist, f"project.v{v}.json"), f.read())
            state["saved_at"] = utcnow()
            atomic_write_text(p, json.dumps(state, ensure_ascii=False, indent=1))

    def load_state(self, pid: str) -> dict:
        p = self.path(pid, "project.json")
        if not os.path.exists(p):
            raise FileNotFoundError(p)
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def exists(self, pid: str) -> bool:
        return os.path.exists(os.path.join(self.dir(pid), "project.json"))

    def audit(self, pid: str) -> AuditLog:
        return AuditLog(self.path(pid, "audit", "audit.jsonl"))
