"""Assembly of the project list rows and the project detail page.

Pure functions plus a small thread-safe git cache. Text-like content comes from
`projectview` (already redacted); nothing here reads files directly or shows
clock times (ages are seconds).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import projectview

NO_ATTENTION = 4
_RANK = {"awaiting": 0, "failed": 1, "blocked": 1, "working": 2}
_CLOSED = {"done", "cancelled"}


class GitCache:
    """Caches `fetch(path)` per path for `ttl_s` seconds (injectable clock).

    A fetch that raises returns `{"git": False, "age_s": 0}` and is NOT cached, so
    a transient failure is retried on the next call instead of hiding git for
    the whole ttl. Per-path locks keep concurrent requests to one fetch without
    blocking other projects.
    """

    def __init__(self, ttl_s: int = 60, clock: Callable[[], float] = time.time):
        self.ttl_s, self._clock = ttl_s, clock
        self._lock = threading.Lock()
        self._path_locks: dict[str, threading.Lock] = {}
        self._entries: dict[str, tuple[float, dict]] = {}

    def get(self, path: str, fetch: Callable[[str], dict]) -> dict:
        with self._lock:
            plock = self._path_locks.setdefault(path, threading.Lock())
        with plock:
            now = self._clock()
            hit = self._entries.get(path)
            if hit and now - hit[0] < self.ttl_s:
                return {**hit[1], "age_s": int(now - hit[0])}
            try:
                info = dict(fetch(path))
            except Exception:  # noqa: BLE001 - git trouble must never break the page
                return {"git": False, "age_s": 0}
            self._entries[path] = (now, info)
            return {**info, "age_s": 0}


class TtlCache:
    """Small thread-safe TTL cache of computed values (injectable clock); a raising compute is not cached."""

    def __init__(self, ttl_s: int = 30, clock: Callable[[], float] = time.time):
        self.ttl_s, self._clock = ttl_s, clock
        self._lock = threading.Lock()
        self._entries: dict = {}

    def get(self, key, compute: Callable[[], object]):
        now = self._clock()
        with self._lock:
            hit = self._entries.get(key)
            if hit and now - hit[0] < self.ttl_s:
                return hit[1]
        value = compute()
        with self._lock:
            self._entries[key] = (now, value)
            if len(self._entries) > 256:    # bounded: drop the expired entries
                self._entries = {k: v for k, v in self._entries.items() if now - v[0] < self.ttl_s}
        return value


STATIC_CACHE = TtlCache()   # docs list, overview and tools of a project: they read files, 30 s is fresh enough


def git_for_paths(cache: GitCache, paths: list, fetch: Callable[[str], dict], max_workers: int = 8) -> dict:
    """`cache.get(path, fetch)` for every path, refreshed in parallel: a cold cache costs about one fetch."""
    paths = list(dict.fromkeys(paths))
    if not paths:
        return {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(paths))) as pool:
        return dict(zip(paths, pool.map(lambda x: cache.get(x, fetch), paths)))


def duplicate_names(projects: list) -> set:
    """Names carried by more than one project (a page cannot tell them apart)."""
    seen, dup = set(), set()
    for p in projects:
        n = p.get("name")
        (dup if n in seen else seen).add(n)
    return dup


def rank(feat_statuses: list[str]) -> int:
    """Most urgent attention level: 0 awaiting, 1 failed/blocked, 2 working, 3 other, 4 none."""
    if not feat_statuses:
        return NO_ATTENTION
    return min(_RANK.get(s, 3) for s in feat_statuses)


def _mine(p: dict, feats: list) -> list:
    return [f for f in feats if f.get("project") == p.get("name")]


def project_row(p: dict, git: dict, feats: list, fixes_n: int, cost_usd: float, ambiguous: bool = False) -> dict:
    open_ = [f for f in _mine(p, feats) if f.get("status") not in _CLOSED]
    has_git = bool(git.get("git"))
    return {"name": p.get("name"), "domain": p.get("domain"), "path": p.get("path"),
            "branch": projectview.redact(str(git.get("branch"))) if has_git and git.get("branch") else None,
            "dirty": git.get("dirty") if has_git else None,
            "last_date": git.get("last_date") if has_git else None,
            "open": len(open_), "attention": rank([f.get("status") for f in open_]),
            "fixes": fixes_n, "cost_usd": cost_usd,
            "git_age_s": git.get("age_s") if has_git else None, "ambiguous": ambiguous}


def _costs(feats: list, request_costs: dict, ledger_cost: dict | None) -> dict:
    ledger_cost = ledger_cost or {}
    by_request = []
    for f in feats:
        c = (request_costs or {}).get(f.get("id"))
        if c:
            by_request.append({"id": f.get("id"), "title": projectview.redact(str(f.get("title", ""))),
                               "cost_usd": c.get("cost_usd", 0)})
    by_model = {m: (r or {}).get("cost_usd", 0) for m, r in (ledger_cost.get("by_model") or {}).items()}
    return {"total_usd": ledger_cost.get("cost_usd", 0), "by_request": by_request, "by_model": by_model}


def project_detail(p: dict, git: dict, vault_note: Path | None, feats: list, artifacts,
                   pending: list, workers: list, request_costs: dict, ledger_cost: dict | None,
                   cache: TtlCache | None = None) -> dict:
    """Every block is always present; a missing source gives an empty value."""
    name, root = p.get("name"), Path(p.get("path") or "")
    mine = _mine(p, feats)
    has_git = bool(git.get("git"))
    header = {"name": name, "domain": p.get("domain"), "path": p.get("path"), "git": has_git,
              "git_age_s": git.get("age_s") if has_git else None}
    header.update({k: git.get(k) for k in ("branch", "dirty", "last_date", "last_msg", "ahead")} if has_git else {})
    for k in ("branch", "last_msg"):    # commit subjects and branch names can carry a pasted secret
        if isinstance(header.get(k), str):
            header[k] = projectview.redact(header[k])
    cache = cache or STATIC_CACHE
    try:
        ckey = (str(root.resolve()), str(vault_note) if vault_note else "")
    except OSError:
        ckey = (str(root), str(vault_note) if vault_note else "")
    static = cache.get(ckey, lambda: {
        "overview": {k: v for k, v in projectview.overview(root, vault_note).items() if k != "architecture"},
        "docs": projectview.list_docs(root), "tools": projectview.tools(root)})
    return {
        "header": header,
        "requests": projectview.requests_view(name, feats),
        "fixes": projectview.fixes(name, feats, artifacts, pending),
        **static,
        "costs": _costs(mine, request_costs, ledger_cost),
        "workers": [w for w in workers if w.get("project") == name],
        "todos": [t for t in pending if t.get("project") == name],
    }
