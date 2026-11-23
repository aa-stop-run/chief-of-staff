"""The user's own pipelines: files in `<home>/pipelines/`, over the engine's validator.

The engine (`pipelines.py`) already decides what is a valid pipeline; this module only reads and writes the files and applies the
rules about ids, built-ins and use. Built-in pipelines are never written. Only the user edits pipelines (never a worker)."""
import json
from pathlib import Path

import atomicio
import pipelines

MIN_ID = 3


class StoreError(Exception):
    """`code`: invalid (422) | exists (409) | builtin (403) | notfound (404) | inuse (409); `count` for inuse."""

    def __init__(self, code: str, msg: str, count: int = 0):
        super().__init__(msg)
        self.code, self.count = code, count


def user_dir(home) -> Path:
    return Path(home) / "pipelines"


def _user_path(home, pid: str) -> Path:
    return user_dir(home) / f"{pid}.json"


def _digest_of(data: dict) -> str:
    try:
        return pipelines.digest(pipelines.Pipeline(data))[:12]
    except (KeyError, TypeError):
        return ""


def list_entries(home) -> list:
    """Every pipeline, the user's first: [{id, origin, valid, reason}]."""
    rows = [{"id": e.id, "origin": e.origin, "valid": e.valid, "reason": e.reason} for e in pipelines.list_all(home)]
    return [r for r in rows if r["origin"] == "user"] + [r for r in rows if r["origin"] != "user"]


def get(home, pid: str) -> dict:
    """One pipeline for the editor: {id, origin, valid, reason, digest, data}. An invalid user file still returns its data."""
    builtin_path = pipelines.builtin_dir() / f"{pid}.json"
    if pid in pipelines.BUILTIN_IDS and builtin_path.is_file():
        p = pipelines.load(builtin_path, builtin=True)
        return {"id": pid, "origin": "builtin", "valid": True, "reason": "", "digest": pipelines.digest(p)[:12], "data": p.data}
    path = _user_path(home, pid)
    if not path.is_file() or path.is_symlink():
        raise StoreError("notfound", f"no pipeline «{pid}»")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"id": pid, "origin": "user", "valid": False, "reason": f"file: not valid JSON: {e}", "digest": "", "data": {}}
    try:
        p = pipelines.load(path)
        return {"id": pid, "origin": "user", "valid": True, "reason": "", "digest": pipelines.digest(p)[:12], "data": p.data}
    except pipelines.PipelineError as e:
        return {"id": pid, "origin": "user", "valid": False, "reason": str(e), "digest": "", "data": data}


def _validated(data, pid: str | None = None) -> dict:
    try:
        return pipelines.validate(data, filename_id=pid)
    except pipelines.PipelineError as e:
        code = "builtin" if e.field == "id" and "built-in" in e.msg else "invalid"
        raise StoreError(code, str(e)) from e


def _write(home, pid: str, data: dict) -> dict:
    user_dir(home).mkdir(parents=True, exist_ok=True)
    atomicio.write_text(_user_path(home, pid), json.dumps(data, ensure_ascii=False, indent=1))
    return {"id": pid, "origin": "user", "valid": True, "reason": "", "digest": _digest_of(data)}


def create(home, data: dict, forked_from: str | None = None) -> dict:
    """Validate and write a new pipeline. The id must be new, not a built-in's, and at least three characters."""
    pid = str(data.get("id", "")) if isinstance(data, dict) else ""
    if pid in pipelines.BUILTIN_IDS:
        raise StoreError("builtin", f"id: «{pid}» belongs to a built-in pipeline")
    if pid and len(pid) < MIN_ID:
        raise StoreError("invalid", f"id: at least {MIN_ID} characters")
    if forked_from:
        data = {**data, "based_on": forked_from}
    _validated(data)
    if _user_path(home, pid).exists():
        raise StoreError("exists", f"there already is a pipeline «{pid}»")
    return _write(home, pid, data)


def update(home, pid: str, data: dict) -> dict:
    """Replace a user pipeline (the id is its file name and cannot change). A built-in is never written: fork it instead."""
    if pid in pipelines.BUILTIN_IDS:
        raise StoreError("builtin", f"«{pid}» is built in: make a copy of it instead")
    if not _user_path(home, pid).is_file():
        raise StoreError("notfound", f"no pipeline «{pid}»")
    _validated(data, pid)
    return _write(home, pid, data)


def usage(requests) -> dict:
    """How many requests (open or closed) were created with each pipeline: {pipeline id: count}."""
    out: dict = {}
    for r in requests or []:
        pid = (r.get("pipeline") or {}).get("id")
        if pid:
            out[pid] = out.get(pid, 0) + 1
    return out


def delete(home, pid: str, requests) -> None:
    """Delete a user pipeline, unless a request still uses it (a request keeps a snapshot, but the page names the pipeline)."""
    if pid in pipelines.BUILTIN_IDS:
        raise StoreError("builtin", f"«{pid}» is built in and cannot be deleted")
    path = _user_path(home, pid)
    if not path.is_file() and not path.is_symlink():
        raise StoreError("notfound", f"no pipeline «{pid}»")
    n = usage(requests).get(pid, 0)
    if n:
        raise StoreError("inuse", f"{n} request(s) were created with «{pid}»: cancel or finish them first", n)
    path.unlink()
