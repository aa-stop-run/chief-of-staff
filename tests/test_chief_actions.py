import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chief  # noqa: E402


def test_visible_projects_hides_configured_domains():
    projs = [{"name": "A", "domain": "banking"}, {"name": "B", "domain": "home"}, {"name": "C", "domain": ""},
             {"name": "D", "domain": "Banking"}]
    assert [p["name"] for p in chief.visible_projects(projs, ["banking"])] == ["B", "C"]
    assert len(chief.visible_projects(projs, [])) == 4


def test_hidden_projects_cannot_receive_workers(monkeypatch):
    monkeypatch.setattr(chief, "load_projects", lambda: chief.visible_projects(
        [{"name": "Hidden", "slug": "hidden", "path": "x", "domain": "banking", "hub": None}], ["banking"]))
    with pytest.raises(SystemExit):
        chief.find("hidden", chief.load_projects())


def test_stop_worker_validates_id_and_never_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a[0]))
    assert chief.stop_worker("../evil") is False and calls == []
    assert chief.stop_worker("548db498") is True and calls == [["claude", "stop", "548db498"]]

    def boom(*a, **k):
        raise OSError("no claude")

    monkeypatch.setattr(subprocess, "run", boom)
    assert chief.stop_worker("548db498") is False


def _repo(tmp_path):
    def g(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True, check=True).stdout
    g("init", "-q")
    g("config", "user.email", "t@t.t")
    g("config", "user.name", "Test")
    (tmp_path / "base.txt").write_text("base", encoding="utf-8")
    g("add", "-A")
    g("commit", "-q", "-m", "base")
    return g


def test_commit_worktree_commits_tracked_and_new_files(tmp_path):
    g = _repo(tmp_path)
    (tmp_path / "base.txt").write_text("changed", encoding="utf-8")
    (tmp_path / "new.py").write_text("print(1)", encoding="utf-8")
    ok, info = chief.commit_worktree(tmp_path, "feat: x\n\nbody")
    assert ok and len(info) >= 7
    assert g("log", "-1", "--format=%s").strip() == "feat: x"
    assert g("status", "--porcelain").strip() == ""
    assert "new.py" in g("show", "--stat", "--format=", "HEAD")


def test_commit_worktree_with_nothing_to_commit_is_ok_and_makes_no_commit(tmp_path):
    g = _repo(tmp_path)
    ok, info = chief.commit_worktree(tmp_path, "feat: nothing")
    assert ok and "no changes" in info
    assert g("rev-list", "--count", "HEAD").strip() == "1"


def test_commit_worktree_rejects_non_git_folder_and_reports_failures(tmp_path):
    ok, info = chief.commit_worktree(tmp_path / "does-not-exist", "m")
    assert not ok and info
    ok, info = chief.commit_worktree(tmp_path, "m")           # a folder that is not a git repository
    assert not ok


class _Run:
    def __init__(self, out):
        self.stdout, self.stderr, self.returncode = out, "", 0


def test_launch_curator_starts_inside_the_vault_and_returns_the_session_id(monkeypatch, tmp_path):
    monkeypatch.setattr(chief, "VAULT", tmp_path)          # a vault that exists
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw.get("cwd"))
        return _Run("backgrounded · abc12345")

    monkeypatch.setattr(subprocess, "run", fake_run)
    w = chief.launch_curator("do the curation")
    assert w["id"] == "abc12345"
    assert Path(seen["cwd"]) == tmp_path                        # its edits are plain edits of its own folder, not of an added one
    assert "--bg" in seen["cmd"] and seen["cmd"][-1] == "do the curation" and "--add-dir" not in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--settings") + 1] == chief.BG_NO_ISOLATION      # the curator edits the vault itself, not a worktree copy


def test_launch_curator_reports_a_failed_start(monkeypatch, tmp_path):
    monkeypatch.setattr(chief, "VAULT", tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Run("some error"))
    with pytest.raises(RuntimeError):
        chief.launch_curator("x")


def test_git_info_status_never_takes_the_index_lock(monkeypatch):
    calls = []

    class R:
        returncode = 0

        def __init__(self, out):
            self.stdout = out

    def fake_run(cmd, *a, **k):
        calls.append(list(cmd))
        return R("2024-01-01|msg" if "log" in cmd else "true")

    monkeypatch.setattr(chief.subprocess, "run", fake_run)
    chief.git_info("X")
    status = [c for c in calls if "status" in c]
    assert status and all("--no-optional-locks" in c and c.index("--no-optional-locks") < c.index("status") for c in status)
