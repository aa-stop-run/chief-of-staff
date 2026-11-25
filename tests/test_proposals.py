"""Requests suggested by workers: a card the user accepts or dismisses; nothing starts by itself."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import chief  # noqa: E402
from proposals import ProposalError, Proposals  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return Proposals(tmp_path)


def add(store, title="Commit the website changes", by="analysis-1", kind="ship", **kw):
    return store.add("Alfa", kind, title, kw.get("desc", "Commit website_deploy/ on a new branch and open a PR."), by)


def test_a_suggestion_is_stored_listed_and_dismissed(store):
    p = add(store)
    assert store.list() == [p] and p["kind"] == "ship" and p["by"] == "analysis-1" and not p["dismissed"]
    assert store.get(p["id"])["title"] == "Commit the website changes"
    store.dismiss(p["id"])
    assert store.list() == []
    with pytest.raises(ProposalError):
        store.dismiss(p["id"])


def test_the_same_suggestion_twice_is_one_card(store):
    assert add(store)["id"] == add(store)["id"] and len(store.list()) == 1


@pytest.mark.parametrize("kw", [{"title": ""}, {"title": "x" * 141}, {"desc": ""}, {"desc": "x" * 4001}])
def test_empty_or_oversized_text_is_refused(store, kw):
    with pytest.raises(ProposalError):
        add(store, **kw)


def test_unknown_kinds_and_missing_projects_are_refused(store):
    with pytest.raises(ProposalError, match="unknown kind"):
        add(store, kind="deploy-everything")
    with pytest.raises(ProposalError):
        store.add("", "ship", "t", "d")


def test_credentials_in_a_suggestion_are_removed(store):
    p = store.add("Alfa", "ship", "Push with token=abc123secret", "Use api_key: sk-ABCDEFGHIJKLMNOPQRSTUV and ghp_ABCDEFGHIJKLMNOPQRSTUVWX", "w")
    blob = p["title"] + p["desc"]
    assert "abc123secret" not in blob and "sk-ABCDEF" not in blob and "ghp_ABCD" not in blob and "[removed]" in blob


def test_a_worker_cannot_flood_the_dashboard(store):
    for i in range(5):
        add(store, title=f"Suggestion {i}")
    with pytest.raises(ProposalError, match="already has 5"):
        add(store, title="One more")
    add(store, title="Another worker's", by="other")                  # the cap is per worker...
    for i in range(44):
        store.add("Alfa", "ship", f"Filler {i}", "d", f"w{i}")
    with pytest.raises(ProposalError, match="too many open"):          # ...and global
        store.add("Alfa", "ship", "Over", "d", "z")


def test_a_corrupt_file_means_no_suggestions(tmp_path):
    (tmp_path / "proposals.json").write_text("{not json", encoding="utf-8")
    assert Proposals(tmp_path).list() == []


def test_ids_are_validated(store):
    for bad in ("../x", "", "A B"):
        with pytest.raises(ProposalError):
            store.get(bad)
        with pytest.raises(ProposalError):
            store.dismiss(bad)


def test_the_worker_preamble_tells_workers_how_to_suggest_and_that_it_starts_nothing():
    for pre in (chief.PREAMBLE, chief.PREAMBLE_NO_VAULT):
        assert "proposals.py" in pre and "{chief}" in pre and "{worker}" in pre and "cannot start work" in pre
    p = {"slug": "a", "name": "Alfa", "path": ".", "hub": ""}
    name, _, prompt = chief.build_command(p, "task", label="x-build")
    assert '--project "Alfa"' in prompt and f'--by "{name}"' in prompt and "scripts/proposals.py" in prompt


def test_the_command_line_refuses_unknown_projects_and_writes_nothing(tmp_path):
    env = {**__import__("os").environ, "CHIEF_HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "proposals.py"), "add", "--project", "No such project", "--kind", "ship",
                        "--title", "t", "--desc", "d"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode != 0 and "does not exist" in r.stderr
    assert not (tmp_path / "state" / "proposals.json").exists()


def test_more_credential_shapes_are_removed(store):
    text = ("remote https://user:hunter2@github.com/x/y.git Authorization: Bearer abcDEF123456 github_pat_ABCDEFGHIJKLMNOPQRSTUV "
            "xoxb-1234567890-abcdef password: my long secret phrase\n-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----")
    p = store.add("Alfa", "ship", "t", text, "w")
    for leak in ("hunter2", "abcDEF123456", "github_pat_ABCD", "xoxb-1234", "long secret phrase", "MIIabc"):
        assert leak not in p["desc"], leak


def test_a_worker_without_a_name_shares_one_capped_bucket(store):
    for i in range(5):
        store.add("Alfa", "ship", f"Anonymous {i}", "d", "")
    with pytest.raises(ProposalError, match="already has 5"):
        store.add("Alfa", "ship", "Anonymous 6", "d", "")


def test_a_dismissed_suggestion_cannot_come_straight_back(store):
    p = add(store)
    store.dismiss(p["id"])
    with pytest.raises(ProposalError, match="dismissed recently"):
        add(store)


def test_a_damaged_file_is_never_overwritten_by_an_add(tmp_path):
    f = tmp_path / "proposals.json"
    f.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProposalError, match="damaged"):
        Proposals(tmp_path).add("Alfa", "ship", "t", "d", "w")
    assert f.read_text(encoding="utf-8") == "{not json"


def test_old_dismissed_entries_are_forgotten(tmp_path):
    import json
    old = {"id": "s-old", "project": "Alfa", "kind": "ship", "title": "t", "desc": "d", "by": "w", "created": "2020-01-01T00:00:00",
           "dismissed": True, "dismissed_at": "2020-01-02T00:00:00"}
    (tmp_path / "proposals.json").write_text(json.dumps([old]), encoding="utf-8")
    s = Proposals(tmp_path)
    s.add("Alfa", "ship", "new", "d", "w")
    assert "s-old" not in (tmp_path / "proposals.json").read_text(encoding="utf-8")
