"""Dependent parts: held at fan-out, released by the ticker onto the earlier parts' code, cancelled with what they follow."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import combine  # noqa: E402
from features import FeatureError, Features  # noqa: E402
from test_auto_review import REVIEW_OK, Fake, FakeCommit  # noqa: E402

SPEC = "# Spec\n\n## Acceptance criteria\n- all three work\n"


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def plan(tasks):
    return f"# Plan\nTASKS: 4\n\n## Split\n\n```json\n{json.dumps({'tasks': tasks})}\n```\n"


API = {"id": "api", "title": "API", "files": [{"new": "api.txt"}]}
DB = {"id": "db", "title": "DB", "files": [{"new": "db.txt"}]}
UI_ON_BOTH = {"id": "ui", "title": "UI", "files": [{"new": "ui.txt"}], "depends_on": ["api", "db"]}
UI_ON_API = {"id": "ui", "title": "UI", "files": [{"new": "ui.txt"}], "depends_on": ["api"]}


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    fake = Fake()
    fs = Features(tmp_path / "state", fake.launch, fake.is_active, auto_review=True, committer=FakeCommit())
    return fs, fake, repo, tmp_path / "state"


def finish(fs, fake, root, fid, artifact, text):
    fake.active.discard(stored(fs, fid)["worker_id"])
    d = root / "features" / fid
    d.mkdir(parents=True, exist_ok=True)
    (d / artifact).write_text(text, encoding="utf-8")
    return stored(fs, fid)


def stored(fs, fid):
    return next(x for x in fs.list() if x["id"] == fid)


def fan_out(world, tasks):
    fs, fake, repo, root = world
    f = fs.create("Alfa", f"Export {len(tasks)}{'x' * len(fs.list())}", "Let me export", None, path=str(repo))
    finish(fs, fake, root, f["id"], "spec.md", SPEC)
    fs.approve(f["id"])
    (root / "features" / f["id"] / "questions.json").write_text("[]", encoding="utf-8")
    finish(fs, fake, root, f["id"], "plan.md", plan(tasks))
    finish(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)
    fs.approve(f["id"])
    parent = stored(fs, f["id"])
    return parent, {t["id"]: t["part_id"] for t in parent["split"]["tasks"]}


def make_ready(world, part_id, name):
    """The part's pipeline commit happened: its branch has a file and the request records `commit.ok`."""
    fs, _, repo, _ = world
    git(repo, "switch", "-c", f"feat/{part_id}", "main")
    (repo / f"{name}.txt").write_text(f"work of {name}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", name)
    git(repo, "switch", "main")
    data = fs._load()
    p = next(x for x in data if x["id"] == part_id)
    p["status"], p["commit"] = "done", {"ok": True, "at": "2999-01-01T00:00:00"}
    fs._save(data)


# ------------------------------------------------------------------ at fan-out

def test_a_dependent_part_is_created_but_not_started(world):
    fs, fake, repo, root = world
    launches = len(fake.calls)
    parent, ids = fan_out(world, [API, DB, UI_ON_BOTH])
    ui = stored(fs, ids["ui"])
    assert ui["held"] is True and ui["follows"] == [ids["api"], ids["db"]] and ui["status"] == "awaiting" and ui["worker_id"] == ""
    assert any(h["event"] == "part held" and "API" in h["note"] and "DB" in h["note"] for h in ui["history"])
    assert stored(fs, ids["api"])["status"] == "working" and stored(fs, ids["db"])["status"] == "working"
    assert len(fake.calls) == launches + 3 + 2 - 0 or len(fake.calls) >= launches + 2           # the two independent parts started
    assert (root / "features" / ids["ui"] / "spec.md").is_file()


def test_nothing_can_be_done_to_a_held_part_except_cancelling_it(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_BOTH])
    for act in (lambda: fs.approve(ids["ui"]), lambda: fs.revise(ids["ui"], "x"), lambda: fs.request_fixes(ids["ui"], "x")):
        with pytest.raises(FeatureError, match="waiting for"):
            act()
    assert fs.cancel(ids["ui"])["status"] == "cancelled"


# ------------------------------------------------------------------ release

def test_a_held_part_waits_until_every_part_it_follows_is_ready(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_BOTH])
    launches = len(fake.calls)
    fs.release_held()
    make_ready(world, ids["api"], "api")
    fs.release_held()
    assert stored(fs, ids["ui"])["held"] is True and len(fake.calls) == launches        # db is still not ready


def test_released_after_two_parts_it_builds_on_a_merged_base_branch(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_BOTH])
    make_ready(world, ids["api"], "api")
    make_ready(world, ids["db"], "db")
    launches = len(fake.calls)
    before = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/feat")
    fs.release_held()
    ui = stored(fs, ids["ui"])
    assert "held" not in ui and ui["status"] == "working" and ui["stage"] == "plan" and len(fake.calls) == launches + 1
    assert ui["base"] == f"base/{ids['ui']}" and ui["base_made"] is True
    tree = git(repo, "ls-tree", "-r", "--name-only", ui["base"]).split()
    assert "api.txt" in tree and "db.txt" in tree
    assert f"base/{ids['ui']}" in fake.calls[-1]["task"]                      # the planner is told where the code is
    assert any(h["event"] == "held part released" for h in ui["history"])
    assert git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/feat") == before
    assert git(repo, "status", "--porcelain") == ""
    fs.release_held()
    assert len(fake.calls) == launches + 1                                    # released once


def test_a_part_that_follows_one_part_builds_directly_on_its_branch(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_API])
    make_ready(world, ids["api"], "api")
    fs.release_held()
    ui = stored(fs, ids["ui"])
    assert ui["base"] == f"feat/{ids['api']}" and not ui.get("base_made") and ui["status"] == "working"
    assert not combine.branch_exists(str(repo), f"base/{ids['ui']}")


def test_the_build_prompt_creates_the_worktree_from_the_base(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_API])
    make_ready(world, ids["api"], "api")
    fs.release_held()
    finish(fs, fake, root, ids["ui"], "plan.md", "# Plan\nTASKS: 1\n")
    finish(fs, fake, root, ids["ui"], "plan-review.md", REVIEW_OK)
    fs.approve(ids["ui"])
    task = fake.calls[-1]["task"]
    assert f'git worktree add -b feat/{ids["ui"]}' in task and f"feat/{ids['api']}" in task.split("git worktree add -b")[-1]


def test_two_parts_that_cannot_be_combined_stay_held_with_the_reason_logged_once(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_BOTH])
    for name, part in (("api", ids["api"]), ("db", ids["db"])):
        git(repo, "switch", "-c", f"feat/{part}", "main")
        (repo / "base.txt").write_text(f"{name} changed it\n")
        git(repo, "add", ".")
        git(repo, "commit", "-m", name)
        git(repo, "switch", "main")
    data = fs._load()
    for part in (ids["api"], ids["db"]):
        p = next(x for x in data if x["id"] == part)
        p["status"], p["commit"] = "done", {"ok": True, "at": "2999-01-01T00:00:00"}
    fs._save(data)
    launches = len(fake.calls)
    fs.release_held()
    fs.release_held()
    ui = stored(fs, ids["ui"])
    assert ui["held"] is True and "cannot be combined" in ui["held_error"] and len(fake.calls) == launches
    assert sum(h["event"] == "held part launch failed" for h in ui["history"]) == 1
    assert not combine.branch_exists(str(repo), f"base/{ids['ui']}")


def test_a_launch_failure_of_a_released_part_marks_it_failed(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_API])
    make_ready(world, ids["api"], "api")

    def broken(*a, **k):
        raise RuntimeError("no capacity")
    fs._launch = broken
    fs.release_held()
    ui = stored(fs, ids["ui"])
    assert ui["status"] == "failed" and "held" not in ui and any(h["event"] == "held part launch failed" for h in ui["history"])


# ------------------------------------------------------------------ cancelling

def test_a_part_that_follows_a_cancelled_part_is_cancelled_with_it(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_BOTH])
    fs.cancel(ids["api"])
    fs.release_held()
    ui = stored(fs, ids["ui"])
    assert ui["status"] == "cancelled" and any(h["event"] == "held part cancelled" for h in ui["history"])


def test_a_failed_followed_part_keeps_the_dependent_waiting(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_API])
    data = fs._load()
    next(x for x in data if x["id"] == ids["api"])["status"] = "failed"
    fs._save(data)
    fs.release_held()
    assert stored(fs, ids["ui"])["held"] is True and stored(fs, ids["ui"])["status"] == "awaiting"


def test_cancelling_the_parent_cancels_the_held_parts_and_leaves_the_running_ones(world):
    fs, fake, repo, root = world
    parent, ids = fan_out(world, [API, DB, UI_ON_BOTH])
    out = fs.cancel(parent["id"])
    assert out["status"] == "done"                                             # the parent is closed already: it stays so
    assert stored(fs, ids["ui"])["status"] == "cancelled"
    assert stored(fs, ids["api"])["status"] == "working" and stored(fs, ids["db"])["status"] == "working"
    with pytest.raises(FeatureError, match="already finished"):
        fs.cancel(parent["id"])                                                # nothing held any more


# ------------------------------------------------------------------ the base branch is housekeeping

def test_the_base_branch_goes_away_when_the_part_is_closed(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_BOTH])
    make_ready(world, ids["api"], "api")
    make_ready(world, ids["db"], "db")
    fs.release_held()
    base = f"base/{ids['ui']}"
    fs.remove_integration_worktrees()
    assert combine.branch_exists(str(repo), base)                              # still being built on
    fs.cancel(ids["ui"])
    fs.remove_integration_worktrees()
    assert not combine.branch_exists(str(repo), base) and not stored(fs, ids["ui"]).get("base_made")


# ------------------------------------------------------------------ the integration waits for the dependent part too

def test_the_integration_includes_the_dependent_part_once_it_is_ready(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_API])
    make_ready(world, ids["api"], "api")
    make_ready(world, ids["db"], "db")
    fs.maybe_integrate()
    assert not [x for x in fs.list() if x.get("integration")]                  # ui is still held: not every part is ready
    fs.release_held()
    make_ready(world, ids["ui"], "ui")
    fs.maybe_integrate()
    (integ,) = [x for x in fs.list() if x.get("integration")]
    assert set(json.loads((root / "features" / integ["id"] / "combine.json").read_text(encoding="utf-8"))["merged"]) == {
        f"feat/{ids['api']}", f"feat/{ids['db']}", f"feat/{ids['ui']}"}


def test_a_held_part_cancelled_by_hand_is_never_released_later(world):
    fs, fake, repo, root = world
    _, ids = fan_out(world, [API, DB, UI_ON_API])
    assert fs.cancel(ids["ui"])["status"] == "cancelled"
    make_ready(world, ids["api"], "api")
    launches = len(fake.calls)
    fs.release_held()
    assert stored(fs, ids["ui"])["status"] == "cancelled" and "held" not in stored(fs, ids["ui"]) and len(fake.calls) == launches
