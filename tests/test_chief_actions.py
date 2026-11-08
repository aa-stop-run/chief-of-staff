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


def test_launch_curator_runs_from_the_chief_folder_and_returns_the_session_id(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw.get("cwd"))
        return _Run("backgrounded · abc12345")

    monkeypatch.setattr(subprocess, "run", fake_run)
    w = chief.launch_curator("do the curation")
    assert w["id"] == "abc12345"
    assert Path(seen["cwd"]) == chief.ROOT                      # not started in the vault: claude refuses untrusted folders
    assert "--bg" in seen["cmd"] and seen["cmd"][-1] == "do the curation"
    assert seen["cmd"][seen["cmd"].index("--add-dir") + 1] == str(chief.VAULT)


def test_launch_curator_reports_a_failed_start(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Run("some error"))
    with pytest.raises(RuntimeError):
        chief.launch_curator("x")


def test_a_worker_command_has_no_add_dir_when_no_vault_is_configured(monkeypatch):
    p = {"name": "Alfa", "slug": "alfa", "path": "x", "hub": None}
    monkeypatch.setattr(chief, "load_workers", lambda: [])
    monkeypatch.setattr(chief, "CFG", {**chief.CFG, "vault": ""})
    _, cmd, _ = chief.build_command(p, "do it")
    assert "--add-dir" not in cmd
    monkeypatch.setattr(chief, "CFG", {**chief.CFG, "vault": "D:/Vault"})
    _, cmd, _ = chief.build_command(p, "do it")
    assert "--add-dir" in cmd
