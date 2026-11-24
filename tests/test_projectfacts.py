"""scripts/projectfacts.py: what the vault audit compares a note with."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import projectfacts as pf  # noqa: E402


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "README.md").write_text("# Alfa\n\n## Run\n\n```\npython app/server.py\npytest -q\n```\n", encoding="utf-8")
    (r / "CLAUDE.md").write_text("# Rules\n## Commands\n    python scripts/chief.py doctor\n", encoding="utf-8")
    (r / ".env").write_text("TOKEN=abc", encoding="utf-8")
    (r / "app.py").write_text("print(1)\n", encoding="utf-8")
    git(r, "add", "README.md", "CLAUDE.md", "app.py")
    git(r, "commit", "-q", "-m", "first commit")
    return r


def test_the_facts_of_a_repository(repo):
    f = pf.collect(repo)
    assert f["exists"] and f["repo"] and f["branch"] == "main" and len(f["head"]) == 12 and f["remote"] == []
    assert f["commits"][0]["subject"] == "first commit" and f["uncommitted"] == 1       # the untracked .env: counted, never listed
    assert "app.py" in f["files"] and ".env" not in f["files"]                                      # secrets files are not even listed
    assert f["docs"]["README.md"]["headings"] == ["Alfa", "Run"] and "python app/server.py" in f["docs"]["README.md"]["commands"]
    assert f["docs"]["CLAUDE.md"]["commands"] == ["python scripts/chief.py doctor"]


def test_a_remote_is_shown_without_its_credentials(repo):
    git(repo, "remote", "add", "origin", "https://user:ghp_abcdefghijklmnopqrstuvwxyz0123456789@github.com/o/r.git")
    f = pf.collect(repo)
    assert f["remote"] == ["https://github.com/o/r.git"]
    assert "ghp_" not in json.dumps(f) and "user:" not in json.dumps(f)


def test_the_digest_follows_the_facts(repo):
    a = pf.digest(pf.collect(repo))
    assert a == pf.digest(pf.collect(repo))
    (repo / "b.py").write_text("x", encoding="utf-8")
    git(repo, "add", "b.py")
    git(repo, "commit", "-q", "-m", "second")
    assert pf.digest(pf.collect(repo)) != a


def test_a_plain_folder_and_a_missing_one(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "notes.txt").write_text("x", encoding="utf-8")
    f = pf.collect(plain)
    assert f["exists"] and f["repo"] is False and f["files"] == ["notes.txt"] and "head" not in f
    assert pf.collect(tmp_path / "nope") == {"exists": False}


def test_the_facts_are_small_even_for_a_huge_folder(tmp_path):
    big = tmp_path / "big"
    big.mkdir()
    for i in range(400):
        (big / f"file-with-a-rather-long-name-number-{i:04d}.txt").write_text("x", encoding="utf-8")
    f = pf.collect(big)
    assert len(json.dumps(f).encode("utf-8")) <= pf.MAX_BYTES and len(f["files"]) <= pf.MAX_FILES


def test_private_addresses_in_the_documents_are_redacted(repo):
    (repo / "README.md").write_text("# Alfa\n\n```\npython serve.py --host 192.168.1.20\n```\n", encoding="utf-8")
    f = pf.collect(repo)
    assert "192.168.1.20" not in json.dumps(f) and "[private ip]" in json.dumps(f)
