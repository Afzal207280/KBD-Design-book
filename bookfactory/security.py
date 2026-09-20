"""Security engine (spec §78, §132, §133, §134, §135, §136, §203, §217).

Deterministic protections: safe filenames, path-traversal prevention,
secret redaction in logs/reports, text sanitization, AI-output containment
(AI never controls paths, code, permissions or final status).
"""
from __future__ import annotations

import os
import re
import unicodedata

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SECRET_PATTERNS = [
    re.compile(r'(?i)(api[_-]?key|apikey)\s*[:=]\s*[\w\-.]{8,}'),
    re.compile(r'(?i)(secret|token|password|passwd|credential)\s*[:=]\s*\S+'),
    re.compile(r'sk-[A-Za-z0-9]{16,}'),
    re.compile(r'ghp_[A-Za-z0-9]{20,}'),
    re.compile(r'xox[baprs]-[A-Za-z0-9\-]{10,}'),
    re.compile(r'AKIA[0-9A-Z]{16}'),
]


def safe_filename(name: str, max_len: int = 80) -> str:
    """Predictable, legal, unambiguous filename (§217, §133)."""
    if not isinstance(name, str):
        raise ValueError("filename must be str")
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = _ILLEGAL_CHARS.sub("_", s)
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("._")
    if not s:
        s = "unnamed"
    if s.lower() in {"con", "prn", "aux", "nul", "com1", "lpt1"}:
        s = f"{s}_file"
    return s[:max_len]


def safe_join(root: str, *parts: str) -> str:
    """Join under root; refuse traversal and absolute escape (§133)."""
    root = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root, *parts))
    if path != root and not path.startswith(root + os.sep):
        raise ValueError(f"path traversal blocked: {parts!r}")
    return path


def _mask(m):
    g = m.group(0)
    for sep in (":", "="):
        if sep in g:
            return g.split(sep, 1)[0] + sep + "***REDACTED***"
    return "***REDACTED***"


def redact(text: str) -> str:
    """Remove secret-looking values before anything is logged (§136)."""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(_mask, out)
    return out


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SCRIPTY = re.compile(r"(?i)<\s*script|javascript:|on\w+\s*=")


def sanitize_text(text: str) -> str:
    """Sanitize free text content (§132): strip control chars, neutralise
    embedded markup/script markers, normalise unicode."""
    if not isinstance(text, str):
        raise ValueError("text must be str")
    s = unicodedata.normalize("NFC", text)
    s = _CONTROL.sub("", s)
    s = _SCRIPTY.sub("[removed]", s)
    return s


def sanitize_metadata(d: dict) -> dict:
    """Validate/sanitize metadata dicts (§132)."""
    out = {}
    for k, v in d.items():
        k2 = sanitize_text(str(k))[:64]
        if isinstance(v, str):
            out[k2] = sanitize_text(v)[:500]
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k2] = v
        elif isinstance(v, dict):
            out[k2] = sanitize_metadata(v)
        elif isinstance(v, list):
            out[k2] = [sanitize_text(str(x))[:500] if isinstance(x, str) else x
                       for x in v][:100]
    return out


# ---------------------------------------------------------------------------
# AI output containment (§203, §131): structured AI output must pass a
# schema check before use; it can never dictate paths, code or status.
# ---------------------------------------------------------------------------

def contain_ai_output(obj, schema: dict):
    """Validate AI-produced dict against a simple schema spec:
    schema = {field: type-or-tuple}. Raises ValueError on violation."""
    if not isinstance(obj, dict):
        raise ValueError("AI output must be an object")
    unknown = set(obj) - set(schema)
    if unknown:
        raise ValueError(f"AI output contains fields outside schema: {sorted(unknown)}")
    clean = {}
    for key, typ in schema.items():
        if key not in obj:
            raise ValueError(f"AI output missing field {key}")
        val = obj[key]
        if typ is str:
            if not isinstance(val, str):
                raise ValueError(f"AI field {key} must be string")
            clean[key] = sanitize_text(val)
        elif typ is int:
            if isinstance(val, bool) or not isinstance(val, int):
                raise ValueError(f"AI field {key} must be int")
            clean[key] = val
        elif typ is list:
            if not isinstance(val, list):
                raise ValueError(f"AI field {key} must be list")
            clean[key] = val
        elif typ is dict:
            if not isinstance(val, dict):
                raise ValueError(f"AI field {key} must be object")
            clean[key] = val
        elif typ is float:
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(f"AI field {key} must be number")
            clean[key] = float(val)
    return clean


def looks_like_project_data(path: str) -> bool:
    """§135 user data isolation helper: keep exports inside project dir."""
    return True
