"""Integrate a finished request: merge its branch into the project's branch, push when there is a remote, remove the worktree.
Real git repositories in a temporary folder, with a local bare repository as the remote."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import integrate  # noqa: E402
from test_chief_actions import _repo  # noqa: E402

BRANCH = "feat/login-1"


def _g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def _commit(cwd, name, text, msg):
    (Path(cwd) / name).write_text(text, encoding="utf-8")
    _g(cwd, "add", "-A")
    _g(cwd, "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _repo(root)
    _g(root, "branch", "-M", "main")
    work = tmp_path / "home" / "features" / "login-1" / "work"
    work.parent.mkdir(parents=True)
    _g(root, "worktree", "add", "-q", "-b", BRANCH, str(work))
    _commit(work, "new.py", "x = 1\n", "feat: login")
    return root, work


@pytest.fixture
def remote(repo, tmp_path):
    root, _ = repo
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    _g(root, "remote", "add", "origin", str(bare))
    _g(root, "push", "-q", "-u", "origin", "main")
    return bare


def plan(root, work, **kw):
    return integrate.plan(root, work, BRANCH, **kw)


def test_the_plan_lists_what_will_run_and_changes_nothing(repo):
    root, work = repo
    before = _g(root, "rev-parse", "HEAD")
    p = plan(root, work)
    assert p["ok"] and p["into"] == "main" and p["remote"] == ""
    assert [c["subject"] for c in p["commits"]] == ["feat: login"]
    assert p["commands"][0].endswith(f"merge --no-ff {BRANCH}") and not any("push" in c for c in p["commands"])
    assert _g(root, "rev-parse", "HEAD") == before


def test_the_plan_includes_the_push_when_there_is_a_remote(repo, remote):
    root, work = repo
    p = plan(root, work)
    assert p["ok"] and p["remote"] == "origin"
    assert any(c.endswith("push origin main:main") for c in p["commands"])


def test_the_remote_url_is_shown_without_credentials(repo):
    root, work = repo
    _g(root, "remote", "add", "origin", "https://user:secret@example.com/o/r.git")
    p = plan(root, work)
    assert "secret" not in repr(p) and "example.com/o/r.git" in p["remote_url"]


@pytest.mark.parametrize("setup,word", [
    (lambda root, work: (root / "base.txt").write_text("changed", encoding="utf-8"), "uncommitted"),
    (lambda root, work: (work / "more.py").write_text("y\n", encoding="utf-8"), "worktree"),
    (lambda root, work: _g(root, "checkout", "-q", "--detach"), "branch"),
])
def test_the_plan_refuses_a_checkout_it_would_trample(repo, setup, word):
    root, work = repo
    setup(root, work)
    p = plan(root, work)
    assert not p["ok"] and word in " ".join(p["problems"]).lower()


def test_the_plan_refuses_secrets_in_the_branch(repo):
    root, work = repo
    _commit(work, ".env", "KEY=1\n", "oops")
    p = plan(root, work, secret_paths=lambda names: [n for n in names if n.startswith(".env")])
    assert not p["ok"] and ".env" in " ".join(p["problems"])


def test_nothing_to_integrate_is_not_ok(repo):
    root, work = repo
    _g(root, "merge", "-q", "--no-ff", BRANCH, "-m", "m")
    assert not plan(root, work)["ok"]


def test_run_merges_removes_the_worktree_and_the_branch(repo):
    root, work = repo
    p = plan(root, work)
    r = integrate.run(root, work, BRANCH, p["expect"])
    assert r["ok"] and r["merged"] and not r["pushed"]
    assert (root / "new.py").exists() and not work.exists()
    assert _g(root, "branch", "--list", BRANCH) == ""
    assert _g(root, "log", "-1", "--format=%s") == f"Merge {BRANCH}"


def test_run_pushes_to_the_remote(repo, remote):
    root, work = repo
    r = integrate.run(root, work, BRANCH, plan(root, work)["expect"])
    assert r["ok"] and r["pushed"]
    assert _g(remote, "rev-parse", "main") == _g(root, "rev-parse", "HEAD")


def test_run_refuses_when_the_project_moved_since_the_plan(repo):
    root, work = repo
    expect = plan(root, work)["expect"]
    _commit(root, "other.py", "z\n", "someone else committed")
    r = integrate.run(root, work, BRANCH, expect)
    assert not r["ok"] and not r["merged"] and "changed" in r["error"]
    assert work.exists()


def test_a_conflict_is_aborted_and_the_repo_is_left_as_it_was(repo):
    root, work = repo
    _commit(root, "new.py", "x = 2\n", "main wrote the same file")
    expect = plan(root, work)["expect"]
    head = _g(root, "rev-parse", "HEAD")
    r = integrate.run(root, work, BRANCH, expect)
    assert not r["ok"] and not r["merged"] and "new.py" in r["error"]
    assert _g(root, "rev-parse", "HEAD") == head and _g(root, "status", "--porcelain") == "" and work.exists()


def test_a_rejected_push_keeps_the_merge_and_can_be_retried_without_force(repo, remote, tmp_path):
    root, work = repo
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    _g(other, "config", "user.email", "o@example.com")
    _g(other, "config", "user.name", "o")
    _commit(other, "o.py", "1\n", "remote moved")
    _g(other, "push", "-q", "origin", "HEAD:main")
    r = integrate.run(root, work, BRANCH, plan(root, work)["expect"])
    assert r["merged"] and not r["pushed"] and not r["ok"] and work.exists()
    _g(root, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")        # the user catches up; the Chief never forces
    p = plan(root, work)
    assert p["ok"] and p["mode"] == "push"
    r2 = integrate.run(root, work, BRANCH, p["expect"])
    assert r2["ok"] and r2["pushed"] and not work.exists()


# ---------------------------------------------------------------- the request side
def _done_request(tmp_path, integrator, committed=True):
    from features import DONE, Features
    from test_auto_review import Fake
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active, integrator=integrator)
    f = fs.create("Alfa", "Login", "desc", None, path="/p/alfa")
    rows = fs._load()
    row = fs._get(rows, f["id"])
    row["status"] = DONE
    if committed:
        row["history"].append({"at": "2026-01-01T00:00:00+00:00", "stage": "review", "event": "committed", "note": ""})
    fs._save(rows)
    return fs, f["id"]


def test_integrating_needs_a_finished_and_committed_request(tmp_path):
    from features import FeatureError
    fs, fid = _done_request(tmp_path, lambda *a: {"ok": True}, committed=False)
    with pytest.raises(FeatureError, match="did not commit"):
        fs.integrate_plan(fid)


def test_the_plan_is_read_only_and_the_confirmed_run_gets_the_plans_state(tmp_path):
    calls = []
    res = {"ok": True, "merged": True, "pushed": True, "steps": ["merged", "pushed"], "error": ""}
    fs, fid = _done_request(tmp_path, lambda *a: calls.append(a) or ({"ok": True, "expect": {"head": "1"}} if a[3] is None else res))
    assert fs.integrate_plan(fid)["expect"] == {"head": "1"}
    f = fs.integrate(fid, {"head": "1"})
    assert calls[0][3] is None and calls[1][3] == {"head": "1"} and calls[1][2] == f"feat/{fid}"
    assert f["history"][-1]["event"] == "integrated" and "pushed" in f["history"][-1]["note"]


def test_a_failed_integration_is_an_error_and_is_logged(tmp_path):
    from features import FeatureError
    fs, fid = _done_request(tmp_path, lambda *a: {"ok": False, "steps": ["merged x into main"], "error": "the push to origin failed"})
    with pytest.raises(FeatureError, match="push to origin failed"):
        fs.integrate(fid, {})
    last = fs.list()[0]["history"][-1]
    assert last["event"] == "integration failed" and "merged x into main" in last["note"]
