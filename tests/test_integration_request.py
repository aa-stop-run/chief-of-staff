"""R7: the integration request — one, only when every non-cancelled part is ready, with a clean worktree and untouched branches."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import combine  # noqa: E402
import prompts_integration  # noqa: E402
from features import Features  # noqa: E402
from test_auto_review import Fake  # noqa: E402


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    fake = Fake()
    fs = Features(tmp_path / "state", fake.launch, fake.is_active, auto_review=True)
    return fs, fake, repo, tmp_path / "state"


SEEDS = []


def seed(world, n=2, statuses=None, commit_ok=None):
    """A fanned-out parent with n parts whose branches `feat/<part id>` exist in the project's repo."""
    fs, fake, repo, root = world
    statuses = statuses or ["done"] * n
    commit_ok = commit_ok if commit_ok is not None else [True] * n
    SEEDS.append(1)
    n_seed = len(SEEDS)
    data = fs._load()
    parent = fs._new_request(data, "Alfa", f"Export trips {n_seed}", "Let me export", None, kind="feature", path=str(repo), start=False)
    parent["status"] = "done"
    tasks = []
    for i in range(n):
        pid = f"part-{n_seed}-{i}-260101000000"
        git(repo, "switch", "-c", f"feat/{pid}", "main")
        (repo / f"f{i}.txt").write_text(f"part {i}\n")
        git(repo, "add", ".")
        git(repo, "commit", "-m", f"part {i}")
        git(repo, "switch", "main")
        part = fs._new_request(data, "Alfa", f"Part {i}", "d", None, kind="feature", path=str(repo), parent=parent["id"], fid=pid,
                               stage="plan", start=False, extra={"part": f"p{i}"})
        part["status"] = statuses[i]
        part["history"].append({"at": "2026-01-01T00:00:00", "event": "worker launched", "stage": "build", "note": ""})
        if statuses[i] != "cancelled" and commit_ok[i] is not None:
            part["commit"] = {"ok": commit_ok[i], "at": "2026-01-01T01:00:00"}
        (root / "features" / pid / "review.md").write_text("## Verdict\nAPPROVED\n", encoding="utf-8")
        tasks.append({"id": f"p{i}", "title": f"Part {i}", "files": [], "part_id": pid})
    parent["split"] = {"tasks": tasks, "status": "fanned-out", "integration": "", "integration_error": ""}
    fs._save(data)
    return parent["id"], [t["part_id"] for t in tasks]


def stored(fs, fid):
    return next(x for x in fs.list() if x["id"] == fid)


def integration_of(fs, pid):
    return [x for x in fs.list() if x.get("integration")]


def test_nothing_is_created_while_a_part_is_not_ready(world):
    fs, fake, repo, root = world
    pid, _ = seed(world, 2, statuses=["done", "working"], commit_ok=[True, None])
    fs.maybe_integrate()
    assert integration_of(fs, pid) == [] and fake.calls == []
    assert stored(fs, pid)["split"]["integration"] == ""


def test_one_integration_request_is_created_and_a_second_tick_adds_none(world):
    fs, fake, repo, root = world
    pid, parts = seed(world)
    before = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    fs.maybe_integrate()
    (integ,) = integration_of(fs, pid)
    parent = stored(fs, pid)
    assert parent["split"]["integration"] == integ["id"] and integ["id"].startswith(pid[:40].rstrip("-") + "-integration-")
    assert (integ["kind"], integ["parent"], integ["project"], integ["path"], integ["lang"]) == ("analysis", pid, "Alfa", str(repo), "en")
    assert "autopilot" not in integ and len(integ["title"]) <= 120 and integ["status"] == "working" and len(fake.calls) == 1
    assert any(h["event"] == "integration created" for h in parent["history"])
    d = root / "features" / integ["id"]
    info = json.loads((d / "combine.json").read_text(encoding="utf-8"))
    assert set(info) == {"base", "head", "merged", "conflicts", "missing"} and info["merged"] == [f"feat/{p}" for p in parts]
    assert (d / "integration" / "f0.txt").exists() and not (d / "work").exists()
    task = fake.calls[-1]["task"]
    assert str(d / "integration") in task and str(d / "combine.json") in task and "do NOT create" not in task
    assert all(str(d / "parts") in task or p in task for p in parts)
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == before
    fs.maybe_integrate()
    fs.maybe_integrate()
    assert len(integration_of(fs, pid)) == 1 and len(fake.calls) == 1


def test_a_cancelled_part_is_skipped_and_one_ready_part_is_not_enough(world):
    fs, fake, repo, root = world
    pid, parts = seed(world, 3, statuses=["done", "done", "cancelled"])
    fs.maybe_integrate()
    (integ,) = integration_of(fs, pid)
    assert parts[2] in integ["desc"] and "Cancelled" in integ["desc"]
    pid2, _ = seed(world, 2, statuses=["done", "cancelled"])
    fs.maybe_integrate()
    assert len(integration_of(fs, pid2)) == 1                 # only the first parent's request exists: no second one for pid2
    assert stored(fs, pid2)["split"]["integration"] == ""


def test_a_combine_error_creates_no_request_and_is_not_retried(world, monkeypatch):
    fs, fake, repo, root = world
    pid, _ = seed(world)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise combine.CombineError("git is unhappy")
    monkeypatch.setattr(combine, "run", boom)
    fs.maybe_integrate()
    parent = stored(fs, pid)
    assert integration_of(fs, pid) == [] and fake.calls == []
    assert parent["split"]["integration"] == "" and "git is unhappy" in parent["split"]["integration_error"]
    assert any(h["event"] == "integration failed" for h in parent["history"])
    fs.maybe_integrate()
    assert len(calls) == 1
    assert not any(p.name.startswith(pid[:20]) and "integration" in p.name for p in (root / "features").iterdir())


def test_a_launch_failure_saves_a_failed_request_that_can_be_retried(world):
    fs, fake, repo, root = world
    pid, _ = seed(world)

    def broken(*a, **k):
        raise RuntimeError("no capacity")
    fs._launch = broken
    fs.maybe_integrate()
    (integ,) = integration_of(fs, pid)
    assert integ["status"] == "failed" and any(h["event"] == "integration launch failed" for h in integ["history"])
    assert stored(fs, pid)["split"]["integration"] == integ["id"]


def test_the_worktree_is_removed_once_the_integration_request_is_closed(world):
    fs, fake, repo, root = world
    pid, _ = seed(world)
    fs.maybe_integrate()
    (integ,) = integration_of(fs, pid)
    wt = root / "features" / integ["id"] / "integration"
    fs.remove_integration_worktrees()
    assert wt.exists()                                        # still open: left alone
    data = fs._load()
    next(x for x in data if x["id"] == integ["id"])["status"] = "done"
    fs._save(data)
    fs.remove_integration_worktrees()
    assert not wt.exists() and "integration" not in git(repo, "worktree", "list").replace("proj", "")


def test_a_failed_removal_is_logged_once_and_not_retried_for_ten_minutes(world, monkeypatch):
    fs, fake, repo, root = world
    pid, _ = seed(world)
    fs.maybe_integrate()
    (integ,) = integration_of(fs, pid)
    data = fs._load()
    next(x for x in data if x["id"] == integ["id"])["status"] = "cancelled"
    fs._save(data)
    attempts = []
    monkeypatch.setattr(combine, "remove", lambda *a: attempts.append(1))      # removal that leaves the folder behind
    fs.remove_integration_worktrees()
    fs.remove_integration_worktrees()
    g = stored(fs, integ["id"])
    assert len(attempts) == 1 and g["integration_cleanup_failed_at"]
    assert sum(h["event"] == "integration cleanup failed" for h in g["history"]) == 1


def test_a_reservation_lost_to_a_crash_is_recorded_once(world):
    fs, fake, repo, root = world
    pid, _ = seed(world)
    data = fs._load()
    parent = next(x for x in data if x["id"] == pid)
    parent["split"]["integration"] = "ghost-integration-1"
    parent["split"]["integration_reserved_at"] = "2020-01-01T00:00:00"
    fs._save(data)
    fs.maybe_integrate()
    g = stored(fs, pid)
    assert g["split"]["integration_error"] == "integration reservation lost" and integration_of(fs, pid) == []
    assert sum(h["event"] == "integration failed" for h in g["history"]) == 1


def test_the_integration_prompt_is_the_rendered_one(world):
    fs, fake, repo, root = world
    pid, parts = seed(world)
    fs.maybe_integrate()
    (integ,) = integration_of(fs, pid)
    d = root / "features" / integ["id"]
    assert prompts_integration.REPORT_SECTIONS["en"][0] in fake.calls[-1]["task"]
    assert (d / "parts").is_dir() and all((d / "parts" / f"{p}.review.md").is_file() for p in parts)
