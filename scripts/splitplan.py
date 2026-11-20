"""Structural helpers for the split & fan-out feature.

A plan may declare 2-10 independent parts in a fenced JSON block; these
functions parse and check that block without ever raising. The behaviour is
shared by the server (fan-out, integration) and the page (the «Split: N parts»
line); see docs/plans/2026-11-20-split-fanout-task-graph.md.
"""
import json
import re

# Field names in both languages; the first key found wins.
_ID_KEYS = ("id",)
_TITLE_KEYS = ("title", "titulo")
_FILES_KEYS = ("files", "ficheiros")
_DEPENDS_KEYS = ("depends_on", "depende_de")
_PARTS_KEYS = ("parts", "partes")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _norm_file(value):
    """One file entry: a string or {"path","new"} -> {"path": str, "new": bool}."""
    if isinstance(value, str):
        return {"path": value, "new": False}
    if isinstance(value, dict):
        path = value.get("path", "")
        return {"path": str(path), "new": bool(value.get("new", False))}
    return {"path": "", "new": False}


def _norm_task(raw):
    """One part: accept both languages' field names; missing keys -> empty."""
    if not isinstance(raw, dict):
        raw = {}

    def pick(keys):
        for key in keys:
            if key in raw and raw[key] is not None:
                return raw[key]
        return None

    files_raw = pick(_FILES_KEYS) or []
    depends_raw = pick(_DEPENDS_KEYS) or []
    return {
        "id": str(pick(_ID_KEYS) or ""),
        "title": str(pick(_TITLE_KEYS) or ""),
        "files": [_norm_file(f) for f in files_raw if isinstance(f, (str, dict))],
        "depends_on": [str(d) for d in depends_raw if isinstance(d, str)],
    }


def parse_split(plan_text: str):
    """Read the fenced JSON split block of a plan.

    Returns {"parts": [normalised part]} or None when there is no usable block.
    It never raises: bad JSON, wrong shapes and missing keys all degrade to
    empty values (or None for an absent/invalid block).
    """
    if not isinstance(plan_text, str):
        return None
    for match in _FENCE_RE.finditer(plan_text):
        body = match.group(1).strip()
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        parts_raw = None
        for key in _PARTS_KEYS:
            if key in data:
                parts_raw = data[key]
                break
        if not isinstance(parts_raw, list):
            continue
        return {"parts": [_norm_task(t) for t in parts_raw]}
    return None
