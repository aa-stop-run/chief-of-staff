"""Git cache and list/detail assembly for the project pages."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import projectpage  # noqa: E402

P = {"name": "Alpha", "domain": "dev", "path": ""}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_git_is_fetched_once_per_ttl():
    clock, calls = Clock(), []

    def fetch(path):
        calls.append(path)
        return {"git": True, "branch": "main"}

    cache = projectpage.GitCache(ttl_s=60, clock=clock)
    assert cache.get("/a", fetch)["age_s"] == 0
    clock.t += 30
    got = cache.get("/a", fetch)
    assert len(calls) == 1 and got["age_s"] == 30 and got["branch"] == "main"
    cache.get("/b", fetch)
    assert len(calls) == 2
    clock.t += 31
    assert cache.get("/a", fetch)["age_s"] == 0
    assert len(calls) == 3


def test_git_failure_returns_no_git_and_is_retried_soon():
    clock, calls = Clock(), []

    def fetch(path):
        calls.append(path)
        raise OSError("boom")

    cache = projectpage.GitCache(ttl_s=60, clock=clock)
    assert cache.get("/a", fetch)["git"] is False
    cache.get("/a", fetch)
    assert len(calls) == 2


def test_git_cache_is_thread_safe_single_fetch():
    calls = []

    def fetch(path):
        calls.append(path)
        return {"git": True}

    cache = projectpage.GitCache(ttl_s=60)
    ts = [threading.Thread(target=cache.get, args=("/a", fetch)) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1


def test_attention_order_awaiting_before_failed_before_working():
    r = projectpage.rank
    assert r(["awaiting"]) < r(["failed"]) == r(["blocked"]) < r(["working"]) < r(["queued"]) < r([])
    assert r(["working", "awaiting", "failed"]) == 0
    assert r(["done", "cancelled"]) == 3 or r(["done"]) == 3


def test_a_project_without_requests_shows_zero_and_no_attention():
    row = projectpage.project_row(P, {"git": False}, [], 0, 0)
    assert row["open"] == 0 and row["attention"] == 4 and row["cost_usd"] == 0 and row["fixes"] == 0
    assert row["branch"] is None and row["git_age_s"] is None


def test_row_counts_only_open_requests_for_attention():
    feats = [{"id": "1", "project": "Alpha", "status": "done"},
             {"id": "2", "project": "Alpha", "status": "working"},
             {"id": "3", "project": "Other", "status": "awaiting"}]
    git = {"git": True, "branch": "main", "dirty": 2, "last_date": "2026-01-01", "age_s": 5}
    row = projectpage.project_row(P, git, feats, 3, 1.5)
    assert (row["open"], row["attention"], row["fixes"], row["cost_usd"]) == (1, 2, 3, 1.5)
    assert (row["branch"], row["dirty"], row["git_age_s"]) == ("main", 2, 5)


def test_detail_has_every_block_even_when_sources_are_missing(tmp_path):
    p = {"name": "Alpha", "domain": "", "path": str(tmp_path / "nope")}
    d = projectpage.project_detail(p, {"git": False}, None, [], lambda i: {}, [], [], {}, None)
    for key in ("header", "requests", "fixes", "overview", "docs", "tools", "costs", "workers", "todos"):
        assert key in d
    assert d["requests"] == {"open": [], "closed": []}
    assert d["fixes"] == [] and d["docs"] == [] and d["workers"] == [] and d["todos"] == []
    assert d["costs"] == {"total_usd": 0, "by_request": [], "by_model": {}}
    assert d["header"]["name"] == "Alpha" and d["header"]["git"] is False


def test_detail_assembles_costs_workers_and_todos(tmp_path):
    (tmp_path / "README.md").write_text("# T\n", encoding="utf-8")
    p = {"name": "Alpha", "domain": "dev", "path": str(tmp_path)}
    feats = [{"id": "f1", "title": "X", "project": "Alpha", "status": "working", "stage": "spec"}]
    workers = [{"project": "Alpha", "id": "w1", "state": "running"}, {"project": "Z", "id": "w2"}]
    pending = [{"project": "Alpha", "id": "p1", "status": "open"}, {"project": "Z", "id": "p2"}]
    costs = {"f1": {"cost_usd": 2.0}, "f9": {"cost_usd": 9.0}}
    led = {"cost_usd": 3.5, "by_model": {"m": {"cost_usd": 3.5}}}
    d = projectpage.project_detail(p, {"git": True, "branch": "main", "age_s": 7}, None, feats,
                                   lambda i: {}, pending, workers, costs, led)
    assert [w["id"] for w in d["workers"]] == ["w1"] and [t["id"] for t in d["todos"]] == ["p1"]
    assert d["costs"]["total_usd"] == 3.5 and d["costs"]["by_model"] == {"m": 3.5}
    assert d["costs"]["by_request"] == [{"id": "f1", "title": "X", "cost_usd": 2.0}]
    assert d["header"]["branch"] == "main" and d["header"]["git_age_s"] == 7
    assert d["docs"][0]["path"] == "README.md" and "text" not in d["docs"][0]


# ---- final fix wave ----

def test_git_branch_and_commit_subject_are_redacted_in_the_header(tmp_path):
    p = {"name": "Alpha", "domain": "", "path": str(tmp_path)}
    git = {"git": True, "branch": "fix/ghp_" + "A1b2C3d4E5" * 3 + "Zz", "dirty": 0, "last_date": "2024-01-01",
           "last_msg": "set password: hunter2 in config", "ahead": 0}
    d = projectpage.project_detail(p, git, None, [], lambda i: {}, [], [], {}, None, cache=projectpage.TtlCache())
    assert "hunter2" not in d["header"]["last_msg"] and "ghp_" not in d["header"]["branch"]
    row = projectpage.project_row(p, git, [], 0, 0)
    assert "ghp_" not in row["branch"]


def test_the_detail_payload_has_no_architecture_field(tmp_path):
    (tmp_path / "README.md").write_text("# T\n## Architecture\nlayers\n", encoding="utf-8")
    p = {"name": "Alpha", "domain": "", "path": str(tmp_path)}
    d = projectpage.project_detail(p, {"git": False}, None, [], lambda i: {}, [], [], {}, None, cache=projectpage.TtlCache())
    assert "layers" in d["overview"]["text"] and "architecture" not in d["overview"]


def test_static_parts_are_cached_per_root_until_the_ttl_expires(tmp_path):
    clock = Clock()
    cache = projectpage.TtlCache(ttl_s=30, clock=clock)
    p = {"name": "Alpha", "domain": "", "path": str(tmp_path)}
    (tmp_path / "README.md").write_text("# One\n", encoding="utf-8")

    def detail():
        return projectpage.project_detail(p, {"git": False}, None, [], lambda i: {}, [], [], {}, None, cache=cache)

    assert "One" in detail()["overview"]["text"]
    (tmp_path / "README.md").write_text("# Two\n", encoding="utf-8")
    clock.t += 10
    assert "One" in detail()["overview"]["text"]            # hit
    clock.t += 25
    assert "Two" in detail()["overview"]["text"]            # expired


def test_duplicate_names_are_detected():
    ps = [{"name": "A"}, {"name": "B"}, {"name": "A"}]
    assert projectpage.duplicate_names(ps) == {"A"}
    assert projectpage.project_row({"name": "A"}, {"git": False}, [], 0, 0, ambiguous=True)["ambiguous"] is True
    assert projectpage.project_row({"name": "A"}, {"git": False}, [], 0, 0)["ambiguous"] is False


def test_git_for_paths_refreshes_in_parallel():
    def slow(path):
        time.sleep(0.2)
        return {"git": True, "branch": path}

    paths = [f"p{i}" for i in range(8)]
    t0 = time.perf_counter()
    out = projectpage.git_for_paths(projectpage.GitCache(), paths, slow)
    took = time.perf_counter() - t0
    assert took < 0.2 * 4 and [out[p]["branch"] for p in paths] == paths
    assert projectpage.git_for_paths(projectpage.GitCache(), [], slow) == {}
