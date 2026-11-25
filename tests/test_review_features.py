"""What the review of 2026-11-14 added to the workflow: the finish step, requests that follow their project, the size of a plan,
why a worker failed, changing a request's kind, the pipeline from the command line, and to-dos from a scan of the sessions."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chief  # noqa: E402
import control  # noqa: E402
import features as F  # noqa: E402
import finish  # noqa: E402
import prompts  # noqa: E402
import prompts_pt  # noqa: E402
import sessions  # noqa: E402
import usage  # noqa: E402
import workerlog  # noqa: E402
from features import FeatureError, Features  # noqa: E402
from test_auto_review import Fake, finish_stage  # noqa: E402
from test_chief_actions import _repo  # noqa: E402


def events(f):
    return [h["event"] for h in f["history"]]


# ---------------------------------------------------------------- the finish step (git)
@pytest.fixture
def repo(tmp_path):
    """A project repo with a request's worktree on its own branch, one commit ahead."""
    root = tmp_path / "proj"
    root.mkdir()
    g = _repo(root)
    g("branch", "-M", "main")
    work = tmp_path / "home" / "features" / "login-1" / "work"
    work.parent.mkdir(parents=True)
    g("worktree", "add", "-q", "-b", "feat/login-1", str(work))
    (work / "new.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "feat: login"], check=True)
    return root, work, g


def test_a_committed_branch_is_reported_unmerged_with_the_commands_to_merge_it(repo):
    root, work, g = repo
    st = chief.request_finish(root, work, "login-1")
    assert st["exists"] and st["worktree"] and not st["merged"] and st["into"] == "main" and st["dirty"] == 0
    assert st["commands"][-1].endswith("merge --no-ff feat/login-1") and "push" not in " ".join(st["commands"])


def test_after_the_users_merge_the_worktree_and_the_branch_are_removed(repo):
    root, work, g = repo
    g("merge", "-q", "--no-ff", "feat/login-1", "-m", "merge")
    st = chief.request_finish(root, work, "login-1")
    assert st["merged"] and st["commands"] == []
    ok, info = finish.remove(root, work, "feat/login-1")
    assert ok and "worktree removed" in info and "branch feat/login-1 deleted" in info
    assert not work.exists() and "feat/login-1" not in g("branch", "--list")
    assert (root / "new.py").exists()                      # the merged work stays


def test_an_unmerged_branch_and_uncommitted_work_are_kept_unless_forced(repo):
    root, work, g = repo
    (work / "draft.py").write_text("y = 2\n", encoding="utf-8")
    ok, info = finish.remove(root, work, "feat/login-1")
    assert not ok and work.exists() and "not removed" in info
    (work / "draft.py").unlink()
    ok, info = finish.remove(root, work, "feat/login-1")
    assert ok and not work.exists() and "was kept" in info and "feat/login-1" in g("branch", "--list")
    ok, info = finish.remove(root, work, "feat/login-1", force=True)
    assert ok and "feat/login-1" not in g("branch", "--list")


def test_only_request_branches_are_ever_removed(repo):
    root, work, g = repo
    assert finish.remove(root, work, "main") == (False, "not a request branch")
    assert finish.remove(root, work, "feat/../main")[0] is False
    assert "main" in g("branch", "--list")


def test_the_size_of_a_change_is_numbers_only(repo):
    root, work, g = repo
    assert finish.diff_summary(work) == {"files": 1, "insertions": 1, "deletions": 0, "untracked": 0, "committed": True}
    (work / "new.py").write_text("x = 2\nz = 3\n", encoding="utf-8")
    (work / "other.py").write_text("o = 1\n", encoding="utf-8")
    d = finish.diff_summary(work)        # against the commit the branch started from: the file is new there, so nothing was deleted
    assert d["committed"] is False and d["untracked"] == 1 and d["files"] == 2 and d["insertions"] == 2 and d["deletions"] == 0
    assert finish.diff_summary(root / "missing") is None


def test_the_size_counts_every_commit_of_the_branch_not_only_the_last(repo):
    root, work, g = repo
    (work / "second.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "feat: more"], check=True)
    assert finish.diff_summary(work) == {"files": 2, "insertions": 3, "deletions": 0, "untracked": 0, "committed": True}
    (root / "elsewhere.txt").write_text("the project moved on\n", encoding="utf-8")        # work on main does not count as the request's
    g("add", "-A")
    g("commit", "-q", "-m", "main moves")
    assert finish.diff_summary(work)["files"] == 2


def test_outside_a_request_worktree_the_size_is_the_last_commit(repo):
    root, work, g = repo
    assert finish.diff_summary(root) == {"files": 1, "insertions": 1, "deletions": 0, "untracked": 0, "committed": True}   # the base commit


def test_cleanup_is_only_for_closed_requests_and_logs_what_happened(tmp_path):
    calls = []
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active, cleaner=lambda *a: calls.append(a) or (True, "worktree removed"))
    f = fs.create("Alfa", "Login", "desc", None, path="/p/alfa")
    with pytest.raises(FeatureError):
        fs.cleanup(f["id"])
    fs.cancel(f["id"])
    f = fs.cleanup(f["id"], force=True)
    assert calls == [("/p/alfa", tmp_path / "features" / f["id"] / "work", f"feat/{f['id']}", True)]
    assert f["history"][-1]["event"] == "cleaned up" and f["history"][-1]["note"] == "worktree removed"


def test_a_failed_cleanup_is_an_error_with_gits_reason(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active, cleaner=lambda *a: (False, "the worktree was not removed: contains modified files"))
    f = fs.create("Alfa", "Login", "desc", None, path="/p/alfa")
    fs.cancel(f["id"])
    with pytest.raises(FeatureError, match="modified files"):
        fs.cleanup(f["id"])
    assert "clean-up failed" in events(fs.list()[0])


# ---------------------------------------------------------------- a request follows its project
def test_a_request_follows_its_project_when_the_project_is_renamed(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    f = fs.create("my-repo", "Login", "desc", None, path="D:/Projects/my-repo")
    assert fs.reconcile([{"name": "My Repo", "path": "D:\\Projects\\my-repo"}]) == 1
    f = fs.list()[0]
    assert f["project"] == "My Repo" and "project renamed" in events(f)
    assert fs.reconcile([{"name": "My Repo", "path": "D:/Projects/my-repo"}]) == 0


def test_an_older_request_learns_its_projects_folder(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    fs.create("Alfa", "Login", "desc", None)
    assert fs.reconcile([{"name": "Alfa", "path": "/p/alfa"}, {"name": "Beta", "path": "/p/beta"}]) == 1
    assert fs.list()[0]["path"] == "/p/alfa"


def test_workers_follow_a_renamed_project_but_the_curator_does_not(tmp_path):
    chief.record_worker({"id": "a1", "name": "w", "project": "my-repo", "cwd": "D:/Projects/my-repo", "task": "t"})
    chief.record_worker({"id": "c1", "name": "cos-curation", "project": chief.CURATION_PROJECT, "cwd": "D:/Projects/my-repo", "task": "t"})
    assert chief.reconcile_workers([{"name": "My Repo", "path": "D:\\Projects\\my-repo"}]) == 1
    assert [w["project"] for w in chief.load_workers()] == ["My Repo", chief.CURATION_PROJECT]


def test_the_ledger_takes_the_new_project_name(tmp_path, monkeypatch):
    log = tmp_path / "a1.jsonl"
    log.write_text(json.dumps({"type": "assistant", "timestamp": "2026-11-06T11:29:01.822Z", "message": {
        "id": "m1", "model": "claude-sonnet-4-5", "usage": {"input_tokens": 10, "output_tokens": 5}}}) + "\n", encoding="utf-8")
    monkeypatch.setattr(usage, "find_transcript", lambda wid, cwd="": log)
    ledger = usage.Ledger(tmp_path / "usage.json")
    ledger.refresh([{"id": "a1", "project": "my-repo"}])
    assert ledger.refresh([{"id": "a1", "project": "my-repo"}]) == 0
    assert ledger.refresh([{"id": "a1", "project": "My Repo"}]) == 1
    assert ledger.load()["a1"]["project"] == "My Repo"


# ---------------------------------------------------------------- the size of a plan, and follow-up requests
def test_the_planner_counts_its_steps_and_the_validator_checks_the_size(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True)
    f = fs.create("Alfa", "Login", "desc", None)
    finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    fs.approve(f["id"])
    assert "TASKS: <number of ordered steps>" in fake.calls[-1]["task"] and f"more than {F.MAX_PLAN_TASKS} steps" in fake.calls[-1]["task"]
    finish_stage(fs, fake, tmp_path, f["id"], "plan.md")          # the validation starts by itself
    task = fake.calls[-1]["task"]
    assert "8. Size:" in task and task.index("8. Size:") < task.index("Write the result to")


def test_the_size_rule_exists_in_both_languages_and_not_for_a_release_plan(tmp_path):
    assert "{max}" in prompts.PLAN_SIZE and "{max}" in prompts_pt.PLAN_SIZE and "TASKS:" in prompts_pt.PLAN_SIZE
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    fs.create("Alfa", "Ship 1.2", "desc", None, kind="release")
    assert "TASKS:" not in fake.calls[-1]["task"]


@pytest.mark.parametrize("text,n", [("## Steps\n1. a\n\nTASKS: 14\n", 14), ("**TASKS: 3**", 3), ("- TAREFAS: 7", 7), ("no line", None), ("", None)])
def test_the_declared_size_of_a_plan_is_read(text, n):
    assert F.plan_tasks(text) == n


def test_a_request_can_follow_another(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    a = fs.create("Alfa", "Part one", "desc", None)
    b = fs.create("Alfa", "Part two", "desc", None, parent=a["id"])
    assert b["parent"] == a["id"] and "parent" not in a
    with pytest.raises(FeatureError):
        fs.create("Alfa", "Part three", "desc", None, parent="nope-1")


# ---------------------------------------------------------------- changing the kind
def test_a_request_changes_kind_on_its_first_phase(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    f = fs.create("Alfa", "Dates are wrong", "desc", None)
    finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    f = fs.set_kind(f["id"], "maintenance")
    assert f["kind"] == "maintenance" and f["stage"] == "plan" and f["status"] == "working" and "kind changed" in events(f)
    assert "maintenance" in fake.calls[-1]["task"].lower()


def test_the_kind_cannot_change_later_or_for_publishing(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    f = fs.create("Alfa", "Login", "desc", None)
    with pytest.raises(FeatureError):                  # still working
        fs.set_kind(f["id"], "fix")
    finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    for bad in ("ship", "release", "feature", "nonsense"):
        with pytest.raises(FeatureError):
            fs.set_kind(f["id"], bad)
    fs.approve(f["id"])
    finish_stage(fs, fake, tmp_path, f["id"], "plan.md")
    with pytest.raises(FeatureError):                  # the spec was approved: nothing approved is thrown away
        fs.set_kind(f["id"], "fix")


# ---------------------------------------------------------------- what the worker said last
def _log(tmp_path, *entries):
    p = tmp_path / "w.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return p


def test_the_last_message_of_a_worker_is_its_reason(tmp_path):
    p = _log(tmp_path, {"type": "assistant", "message": {"content": [{"type": "text", "text": "Working on it."}]}},
             {"type": "user", "message": {"content": "tool result"}},
             {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"},
                                                           {"type": "text", "text": "Summary.\n[BLOCKED] The tests need a database\nthat is not running."}]}})
    assert workerlog.last_message(p) == {"mark": "BLOCKED", "text": "[BLOCKED] The tests need a database that is not running."}


def test_a_worker_that_was_cut_off_has_no_mark_and_secrets_never_show(tmp_path):
    p = _log(tmp_path, {"type": "assistant", "message": {"content": "I will now use token: abc123SECRET and continue " + "x" * 900}})
    m = workerlog.last_message(p)
    assert m["mark"] == "" and "abc123SECRET" not in m["text"] and "[removed]" in m["text"] and len(m["text"]) <= workerlog.MAX_TEXT
    assert workerlog.last_message(tmp_path / "missing.jsonl") is None
    assert workerlog.last_message(_log(tmp_path, {"type": "user", "message": {"content": "hi"}})) is None


def test_a_usage_limit_notice_is_not_the_workers_word(tmp_path):
    p = _log(tmp_path, {"type": "assistant", "message": {"content": "[QUESTION] Which database?"}},
             {"type": "assistant", "isApiErrorMessage": True, "message": {"content": "Usage limit reached"}})
    assert workerlog.last_message(p)["mark"] == "QUESTION"


# ---------------------------------------------------------------- the pipeline from the command line
class Reply:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_the_server_announces_itself_and_the_cli_calls_it_like_the_page_does(tmp_path):
    assert control.read(tmp_path) is None
    with pytest.raises(control.ControlError, match="not running"):
        control.call(tmp_path, "GET", "/api/state")
    control.announce(tmp_path, 8791, "tok")
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), headers={k.lower(): v for k, v in req.header_items()}, body=req.data)
        return Reply({"ok": True})

    assert control.call(tmp_path, "POST", "/api/features/x-1/approve", opener=opener) == {"ok": True}
    assert seen["url"] == "http://127.0.0.1:8791/api/features/x-1/approve" and seen["method"] == "POST"
    assert seen["headers"]["x-token"] == "tok" and seen["headers"]["origin"] == "http://127.0.0.1:8791"
    assert seen["headers"]["content-type"] == "application/json" and json.loads(seen["body"])
    control.withdraw(tmp_path)
    assert control.read(tmp_path) is None


def test_a_request_is_found_by_id_prefix_or_title():
    rs = [{"id": "login-with-2fa-261114", "title": "Login with 2FA"}, {"id": "logout-261114", "title": "Logout button"}]
    assert control.find_request(rs, "logout-261114")["title"] == "Logout button"
    assert control.find_request(rs, "login-")["id"] == "login-with-2fa-261114"
    assert control.find_request(rs, "2fa")["id"] == "login-with-2fa-261114"
    for bad in ("log", "nothing", ""):
        with pytest.raises(control.ControlError):
            control.find_request(rs, bad)


def test_requests_are_listed_from_the_saved_state_when_no_server_runs(capsys):
    chief.STATE.mkdir(parents=True, exist_ok=True)
    (chief.STATE / "features.json").write_text(json.dumps([
        {"id": "a-1", "title": "Open one", "kind": "fix", "stage": "plan", "status": "awaiting", "project": "Alfa"},
        {"id": "b-1", "title": "Closed one", "kind": "feature", "stage": "review", "status": "done", "project": "Alfa"}]), encoding="utf-8")
    chief.cmd_requests(argparse.Namespace(all=False))
    out = capsys.readouterr().out
    assert "not running" in out and "a-1" in out and "TO DECIDE" in out and "b-1" not in out
    chief.cmd_requests(argparse.Namespace(all=True))
    assert "b-1" in capsys.readouterr().out


def test_approving_from_the_command_line_needs_the_server(capsys):
    with pytest.raises(SystemExit) as e:
        chief.cmd_approve(argparse.Namespace(request="a-1"))
    assert "not found" in str(e.value) or "not running" in str(e.value)


# ---------------------------------------------------------------- every subcommand runs
@pytest.mark.parametrize("argv", [["home"], ["projects"], ["status"], ["workers"], ["plan"], ["session"], ["costs"], ["requests"],
                                  ["doctor"], ["dispatch", "nope", "--task", "x", "--dry-run"]])
def test_every_subcommand_runs_on_an_empty_home(argv, monkeypatch, capsys):
    monkeypatch.setattr(chief, "sessions", lambda: [])
    monkeypatch.setattr(chief, "sessions_checked", lambda: [])
    monkeypatch.setattr(chief, "doctor_checks", lambda: [{"level": "ok", "name": "x", "detail": "y", "hint": ""}])
    monkeypatch.setattr(chief.home, "ensure", lambda h=None: h)
    monkeypatch.setattr(chief, "CFG", {**chief.CFG, "roots": [], "extras": []})
    monkeypatch.setattr(sys, "argv", ["chief.py", *argv])
    try:
        chief.main()
    except SystemExit as e:                       # a clean refusal (no such project) is fine; a traceback is not
        assert isinstance(e.code, str) and "not found" in e.code
    capsys.readouterr()


# ---------------------------------------------------------------- to-dos from a scan of the sessions
def _scan():
    s = lambda sid, first, todos=(): {"id": sid, "modified": f"2026-11-1{sid[0]}T10:00", "messages": 4, "first": first,  # noqa: E731
                                      "last_user": "", "last_assistant": "I stopped here.", "open_todos": list(todos)}
    return [{"project": "Alfa", "path": "/p/alfa", "folder": "p-alfa", "sessions": [
        s("1aaaaaaa", "fix the login"), s("2bbbbbbb", "add export", [{"status": "pending", "content": "write tests"}]),
        s("3ccccccc", "a worker's own log"), s("4ddddddd", "already a card"), s("5eeeeeee", "")]},
        {"project": None, "path": None, "folder": "x", "sessions": [s("6fffffff", "no project")]}]


def test_a_session_still_in_use_is_not_offered():
    from datetime import datetime
    at = lambda hhmm: datetime.fromisoformat(f"2026-11-12T{hhmm}")   # noqa: E731 - session 2 was written at 10:00 that day
    ids = lambda now: [c["session"] for c in sessions.candidates(_scan(), set(), ["3ccc"], now=now)]   # noqa: E731
    assert "2bbbbbbb" not in ids(at("10:05")) and "1aaaaaaa" in ids(at("10:05"))
    assert "2bbbbbbb" in ids(at("10:11"))


def test_only_sessions_without_a_card_that_are_not_workers_are_offered():
    got = sessions.candidates(_scan(), known_sessions={"4ddddddd"}, worker_ids=["3ccc", ""])
    assert [c["session"] for c in got] == ["2bbbbbbb", "1aaaaaaa"]          # unfinished to-dos first
    assert got[0]["open_todos"] == 1 and got[0]["project"] == "Alfa"


def test_a_session_becomes_a_card_with_what_is_missing():
    s = _scan()[0]["sessions"][1]
    assert sessions.todo_from_session(s) == {"title": "add export", "status": "awaiting", "summary": "I stopped here.",
                                             "missing": "write tests", "date": "2026-11-12"}


def test_a_session_is_only_read_from_its_own_projects_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
    folder = tmp_path / sessions.encode_path("/p/alfa")
    folder.mkdir()
    (folder / "1aaaaaaa.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "fix the login"}}) + "\n", encoding="utf-8")
    projects = [{"name": "Alfa", "path": "/p/alfa"}, {"name": "Beta", "path": "/p/beta"}]
    assert sessions.find_session(projects, "Alfa", "1aaaaaaa")["first"] == "fix the login"
    assert sessions.find_session(projects, "Beta", "1aaaaaaa") is None
    assert sessions.find_session(projects, "Alfa", "../1aaaaaaa") is None
    assert sessions.find_session(projects, "Gone", "1aaaaaaa") is None
