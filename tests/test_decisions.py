"""The audit trail (`decisions.md`) and the change warnings."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import changecheck  # noqa: E402
import decisions  # noqa: E402
from test_auto_review import Fake, finish_stage  # noqa: E402
from features import Features  # noqa: E402


# ------------------------------------------------------------------ the trail

def test_line_has_time_stage_event_note_and_artifact_digests():
    assert decisions.line("2026-11-23T10:00:00", "spec", "approved", "by you\nnow", {"spec.md": "abc"}) == \
        "- 2026-11-23T10:00:00 · spec · approved: by you now [spec.md abc]\n"
    assert decisions.line("t", "plan", "x") == "- t · plan · x\n"


def test_append_is_append_only_skips_noise_and_never_needs_the_folder(tmp_path):
    d = tmp_path / "req"
    assert decisions.append(d, "T", {"at": "t", "event": "approved", "stage": "spec"}) is False      # no folder: nothing written, no error
    d.mkdir()
    (d / "spec.md").write_text("# spec", encoding="utf-8")
    assert decisions.append(d, "My title", {"at": "t1", "event": "approved", "stage": "spec", "note": "n"})
    assert decisions.append(d, "My title", {"at": "t2", "event": "worker launched", "stage": "spec"}) is False
    assert decisions.append(d, "My title", {"at": "t3", "event": "cancelled", "stage": "plan"})
    text = (d / decisions.FILE).read_text(encoding="utf-8")
    assert text.startswith("# Decisions: My title\n\n") and text.count("# Decisions") == 1
    lines = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert len(lines) == 2 and "spec.md " + decisions.digest(d / "spec.md") in lines[0] and "[" not in lines[1]


def test_a_request_writes_its_trail_as_it_goes(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    f = fs.create("proj", "Trail", "desc", None)
    fid = f["id"]
    finish_stage(fs, fake, tmp_path, fid, "spec.md", "# the spec")
    fs.approve(fid)
    trail = (tmp_path / "features" / fid / decisions.FILE).read_text(encoding="utf-8")
    assert "approved" in trail and "spec.md " in trail and "worker launched" not in trail
    fs.cancel(fid)
    assert "cancelled" in (tmp_path / "features" / fid / decisions.FILE).read_text(encoding="utf-8")


# ------------------------------------------------------------------ the warnings

def test_limits_take_valid_user_values_only():
    assert changecheck.limits(None) == {"max_diff_lines": 800, "max_diff_files": 25}
    assert changecheck.limits({"max_diff_lines": 100, "max_diff_files": "x", "other": 1}) == {"max_diff_lines": 100, "max_diff_files": 25}
    assert changecheck.limits({"max_diff_lines": -1, "max_diff_files": True})["max_diff_lines"] == 800


@pytest.mark.parametrize("glob,path,hit", [
    ("**/migrations/**", "app/db/migrations/001.sql", True), ("**/migrations/**", "app/models.py", False),
    ("*.ddl", "sql/schema.ddl", True), ("*.ddl", "schema.ddl.md", False), ("Dockerfile", "svc/Dockerfile", True),
    (".github/workflows/*", ".github/workflows/ci.yml", True), (".github/workflows/*", ".github/workflows/a/ci.yml", False),
    ("src/?.py", "src/a.py", True), ("src/?.py", "src/ab.py", False)])
def test_protected_globs(glob, path, hit):
    assert changecheck.matches(path, glob) is hit


def test_warnings_for_size_files_and_protected_paths():
    lim = changecheck.limits({"max_diff_lines": 100, "max_diff_files": 3})
    w = changecheck.warnings({"files": 5, "insertions": 90, "deletions": 20}, ["a.py", "db/x.ddl", "db/y.ddl"], lim, ["*.ddl", "*.tf"])
    assert [x["code"] for x in w] == ["size", "files", "protected"]
    assert w[0] == {"code": "size", "lines": 110, "max": 100}
    assert w[2]["patterns"] == [{"glob": "*.ddl", "files": 2}]
    assert changecheck.warnings({"files": 1, "insertions": 1, "deletions": 0}, ["a.py"], lim, ["*.ddl"]) == []
    assert changecheck.warnings(None, [], lim, []) == []
    assert changecheck.warnings({"files": 99, "insertions": 9999, "deletions": 0}, [], {"max_diff_lines": 0, "max_diff_files": 0}, []) == []


def test_globs_ignores_junk():
    assert changecheck.globs(["a", " ", 3, None, " b "]) == ["a", "b"]
    assert changecheck.globs("x") == []


def test_changed_files_of_a_real_branch_include_new_and_edited_files(tmp_path):
    import subprocess
    import finish

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True, check=True)

    git("init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("1\n", encoding="utf-8")
    git("add", "."); git("commit", "-q", "-m", "base")
    wt = tmp_path.parent / (tmp_path.name + "-wt")
    git("worktree", "add", "-q", "-b", "feat/x", str(wt))
    (wt / "a.py").write_text("2\n", encoding="utf-8")
    (wt / "db").mkdir()
    (wt / "db" / "new.ddl").write_text("x\n", encoding="utf-8")
    assert finish.changed_files(wt) == ["a.py", "db/new.ddl"]
    assert finish.changed_files(tmp_path / "nope") == []
