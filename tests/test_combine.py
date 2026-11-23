"""combine.py on real throw-away git repos (nothing outside tmp_path is touched)."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import combine  # noqa: E402


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def commit_file(repo, name, text, msg):
    (Path(repo) / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-b", "main")
    commit_file(r, "shared.txt", "one\ntwo\nthree\n", "init")
    for name, fname in (("feat/a", "a.txt"), ("feat/b", "b.txt")):
        git(r, "switch", "-c", name, "main")
        commit_file(r, fname, name, name)
    git(r, "switch", "main")
    return r


def refs(repo):
    return git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


def test_two_independent_branches_merge_cleanly(repo, tmp_path):
    dest = tmp_path / "dest"
    before, status = refs(repo), git(repo, "status", "--porcelain")
    res = combine.run(str(repo), ["feat/a", "feat/b"], str(dest))
    assert res["merged"] == ["feat/a", "feat/b"] and res["conflicts"] == {} and res["missing"] == []
    assert res["base"] == git(repo, "rev-parse", "HEAD") and res["head"] != res["base"]
    assert (dest / "a.txt").exists() and (dest / "b.txt").exists()
    assert git(dest, "status", "--porcelain") == ""
    assert subprocess.run(["git", "-C", str(dest), "symbolic-ref", "-q", "HEAD"], capture_output=True).returncode != 0  # detached
    assert refs(repo) == before and git(repo, "status", "--porcelain") == status


def test_a_conflict_is_aborted_reported_and_does_not_stop_the_rest(repo, tmp_path):
    for name, text in (("feat/c1", "ONE\ntwo\nthree\n"), ("feat/c2", "uno\ntwo\nthree\n")):
        git(repo, "switch", "-c", name, "main")
        commit_file(repo, "shared.txt", text, name)
        git(repo, "switch", "main")
    dest = tmp_path / "dest"
    before = refs(repo)
    res = combine.run(str(repo), ["feat/c1", "feat/c2", "feat/a"], str(dest))
    assert res["merged"] == ["feat/c1", "feat/a"]
    assert res["conflicts"] == {"feat/c2": ["shared.txt"]}
    assert git(dest, "status", "--porcelain") == ""
    assert refs(repo) == before


def test_a_missing_branch_is_listed_and_the_rest_merged(repo, tmp_path):
    res = combine.run(str(repo), ["feat/a", "feat/nope", "feat/b"], str(tmp_path / "dest"))
    assert res["missing"] == ["feat/nope"] and res["merged"] == ["feat/a", "feat/b"]


def test_remove_deletes_the_folder_and_the_registration_and_is_idempotent(repo, tmp_path):
    dest = tmp_path / "dest"
    combine.run(str(repo), ["feat/a"], str(dest))
    assert "dest" in git(repo, "worktree", "list")
    combine.remove(str(repo), str(dest))
    combine.remove(str(repo), str(dest))
    assert not dest.exists() and "dest" not in git(repo, "worktree", "list")


def test_an_existing_dest_is_replaced(repo, tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "junk.txt").write_text("x")
    res = combine.run(str(repo), ["feat/a"], str(dest))
    assert res["merged"] == ["feat/a"] and not (dest / "junk.txt").exists()


def test_a_folder_that_is_not_a_repo_raises(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(combine.CombineError):
        combine.run(str(plain), ["feat/a"], str(tmp_path / "dest"))
