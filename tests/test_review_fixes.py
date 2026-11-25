"""The bugs found by the review of 2026-11-14 (docs/reviews/2026-11-14-project-review.md), one test or more per bug."""
import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomicio  # noqa: E402
import chief  # noqa: E402
import features as F  # noqa: E402
import settings  # noqa: E402
from curation import Curator  # noqa: E402
from features import FeatureError, Features  # noqa: E402
from test_auto_review import REVIEW_BLOCKED, REVIEW_OK, Fake, FakeCommit, feature_in_build, finish_stage  # noqa: E402
from test_chief_actions import _repo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class Flaky(Fake):
    """A launcher that can be told to fail, the way `claude --bg` does when it does not start."""

    def __init__(self, error=RuntimeError("claude --bg did not start")):
        super().__init__()
        self.fail, self.error = False, error

    def launch(self, project, task, model, extra_dirs, label=None, **kw):
        if self.fail:
            raise self.error
        return super().launch(project, task, model, extra_dirs, label)


def events(f):
    return [h["event"] for h in f["history"]]


# ---- H1: `chief.py status` ----
def test_status_prints_every_project_with_its_inbox_and_workers(monkeypatch, capsys):
    projs = [{"name": n, "slug": n.lower(), "path": f"/p/{n}", "domain": "", "hub": None} for n in ("Alfa", "Beta", "Gama")]
    monkeypatch.setattr(chief, "load_projects", lambda: [dict(p) for p in projs])
    monkeypatch.setattr(chief, "sessions", lambda: [])
    monkeypatch.setattr(chief, "inbox_notes", lambda: [])
    monkeypatch.setattr(chief, "git_info", lambda path: {"git": False})
    chief.cmd_status(argparse.Namespace(project=None))
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 3 and all("inbox 0" in ln and "workers 0" in ln for ln in out)
    assert "no git" in out[0] and "sem git" not in out[0]


# ---- H2: an unknown project is an error, never an exit ----
def test_launching_a_worker_in_an_unknown_project_raises_instead_of_exiting(monkeypatch):
    monkeypatch.setattr(chief, "load_projects", lambda: [])
    with pytest.raises(LookupError):
        chief.launch_worker("Gone", "do it")


def test_a_launcher_that_exits_does_not_escape_the_pipeline(tmp_path):
    fake = Flaky(error=SystemExit("project 'Alfa' not found"))
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True)
    f = feature_in_build(fs, fake, tmp_path)
    fake.fail = True
    f = finish_stage(fs, fake, tmp_path, f["id"], "report.md")          # the automatic review cannot be launched
    assert f["stage"] == "build" and f["status"] == "awaiting" and "automatic review failed" in events(f)
    with pytest.raises(FeatureError):
        fs.approve(f["id"])


def test_a_project_with_open_requests_cannot_be_removed():
    feats = [{"project": "Alfa", "status": "working"}, {"project": "Alfa", "status": "done"}, {"project": "Beta", "status": "cancelled"}]
    assert F.open_requests(feats, "Alfa") == 1 and F.open_requests(feats, "Beta") == 0


# ---- H3: «run until the review» and a launch that fails ----
def test_autopilot_stays_on_the_phase_when_the_next_one_cannot_be_launched(tmp_path):
    fake = Flaky()
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True)
    f = fs.create("Alfa", "Login", "desc", None, autopilot=True)
    (tmp_path / "features" / f["id"] / "questions.json").write_text("[]", encoding="utf-8")
    fake.fail = True
    f = finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    assert f["stage"] == "spec" and f["status"] == "awaiting" and "autopilot stopped" in events(f)
    fake.fail = False
    f = fs.approve(f["id"])
    assert f["stage"] == "plan" and f["status"] == "working"


# ---- H4: the commit refuses secrets also in folders with non-ASCII names ----
def test_the_commit_refuses_a_secret_inside_a_folder_with_accents(tmp_path):
    g = _repo(tmp_path)
    (tmp_path / "configuração").mkdir()
    (tmp_path / "configuração" / ".env").write_text("TOKEN=x", encoding="utf-8")
    ok, info = chief.commit_worktree(tmp_path, "feat: x")
    assert not ok and ".env" in info
    assert g("log", "--oneline").count("\n") == 1          # only the base commit


# ---- M1: trying a fix round again keeps the review's findings ----
def _blocked_review(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    f = feature_in_build(fs, fake, tmp_path)
    finish_stage(fs, fake, tmp_path, f["id"], "report.md")
    fs.approve(f["id"])
    finish_stage(fs, fake, tmp_path, f["id"], "review.md", REVIEW_BLOCKED)
    return fs, fake, f


def test_retry_of_a_failed_fix_round_still_points_at_the_review(tmp_path):
    fs, fake, f = _blocked_review(tmp_path)
    fs.request_fixes(f["id"], "also rename x")
    assert "review.md" in fake.calls[-1]["task"]
    fake.active.clear()                                      # the worker vanished without a report
    assert fs.list()[0]["status"] == "failed"
    fs.retry(f["id"])
    assert "review.md" in fake.calls[-1]["task"] and "also rename x" in fake.calls[-1]["task"]


def test_resume_after_a_usage_limit_keeps_the_fix_instructions(tmp_path):
    from datetime import datetime, timedelta
    fake = Fake()
    notice = {}
    fs = Features(tmp_path, fake.launch, fake.is_active, limit=lambda wid: notice.get(wid))
    f = feature_in_build(fs, fake, tmp_path)
    finish_stage(fs, fake, tmp_path, f["id"], "report.md")
    fs.approve(f["id"])
    finish_stage(fs, fake, tmp_path, f["id"], "review.md", REVIEW_BLOCKED)
    fs.request_fixes(f["id"], "")
    wid = fs.list()[0]["worker_id"]
    notice[wid] = {"resume_at": (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"), "kind": "session"}
    fs.list()
    assert fs.auto_resume() == [f["id"]]
    assert "review.md" in fake.calls[-1]["task"]


# ---- M2: --home=<folder> ----
def test_the_server_understands_both_forms_of_the_home_option():
    sys.path.insert(0, str(ROOT / "app"))
    import launchargs
    assert launchargs.home_arg(["server.py", "--home", "X:/a"]) == "X:/a"
    assert launchargs.home_arg(["server.py", "--port", "1", "--home=X:/b"]) == "X:/b"
    assert launchargs.home_arg(["server.py", "--demo"]) is None
    assert launchargs.home_arg(["server.py", "--home"]) is None


# ---- M3: curation, drafts that arrive while it runs ----
def test_curation_counts_the_drafts_it_was_given_not_the_size_of_the_inbox(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for n in ("a", "b"):
        (inbox / f"{n}.md").write_text("x", encoding="utf-8")
    clock = {"t": 10_000_000.0}
    cur = Curator(tmp_path, tmp_path / "curation.json", lambda p: {"id": "c1", "name": "c"}, lambda i: "finished",
                  lambda p, m: (True, "abc"), lambda i: None, now=lambda: clock["t"])
    cur.start(force=True)
    for n in ("a", "b"):
        (inbox / f"{n}.md").unlink()
    for n in ("c", "d"):
        (inbox / f"{n}.md").write_text("x", encoding="utf-8")
    clock["t"] += 200
    cur.tick()
    s = cur.status()
    assert s["attempts"] == 0 and s["last"]["handled"] == 2 and s["last"]["remaining"] == 2


# ---- M5: a to-do becomes a request whatever its title length and summary ----
def test_a_todo_always_gives_a_valid_title_and_description():
    title, desc = F.request_from_todo({"title": "T" * 140, "summary": "", "missing": ""}, "Missing")
    assert len(title) <= F.MAX_TITLE and desc
    title, desc = F.request_from_todo({"title": "Short", "summary": "did x", "missing": "y"}, "Missing")
    assert title == "Short" and desc == "did x\n\nMissing: y"


# ---- M7: a report without its final result is said, not hidden ----
def test_a_review_that_cannot_be_compared_with_the_report_is_logged(tmp_path):
    fake, commit = Fake(), FakeCommit()
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True, committer=commit)
    f = feature_in_build(fs, fake, tmp_path)
    finish_stage(fs, fake, tmp_path, f["id"], "report.md", "# report without numbers")
    f = finish_stage(fs, fake, tmp_path, f["id"], "review.md", REVIEW_OK)
    assert "committed" in events(f) and "tests not compared" in events(f)


def test_a_report_with_its_final_result_is_compared_silently(tmp_path):
    fake, commit = Fake(), FakeCommit()
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True, committer=commit)
    f = feature_in_build(fs, fake, tmp_path)
    finish_stage(fs, fake, tmp_path, f["id"], "report.md", "## Tests\nNO REGRESSIONS\nFINAL RESULT: 10 passed, 0 failed\n")
    f = finish_stage(fs, fake, tmp_path, f["id"], "review.md", REVIEW_OK)
    assert "committed" in events(f) and "tests not compared" not in events(f)


# ---- M9: a projects folder that is not there ----
def test_a_missing_projects_folder_is_skipped(monkeypatch, tmp_path):
    (tmp_path / "root" / "one").mkdir(parents=True)
    monkeypatch.setattr(chief, "CFG", {**chief.CFG, "roots": [str(tmp_path / "gone"), str(tmp_path / "root")], "extras": [],
                                       "ignore": [], "domains": {}, "hide_domains": []})
    monkeypatch.setattr(chief, "VAULT", Path(""))
    assert [p["name"] for p in chief.load_projects()] == ["one"]
    assert chief._covered_paths() == [chief.norm(tmp_path / "root" / "one")]


# ---- L1: archived questions are never overwritten ----
def test_archived_questions_of_two_phases_do_not_overwrite_each_other(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    f = fs.create("Alfa", "Login", "desc", None)
    d = tmp_path / "features" / f["id"]
    (d / "questions.json").write_text('[{"id":"S1","text":"spec question"}]', encoding="utf-8")
    finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    fs.approve(f["id"])
    (d / "questions.json").write_text('[{"id":"P1","text":"plan question"}]', encoding="utf-8")
    finish_stage(fs, fake, tmp_path, f["id"], "plan.md")
    fs.revise(f["id"], "change it")
    kept = "".join(p.read_text(encoding="utf-8") for p in d.glob("questions.*.json"))
    assert "spec question" in kept and "plan question" in kept


# ---- L2: the commit says what kind of change it is ----
@pytest.mark.parametrize("kind,prefix", [("feature", "feat: "), ("fix", "fix: "), ("maintenance", "chore: ")])
def test_the_commit_message_follows_the_kind_of_request(kind, prefix):
    assert F.commit_message({"kind": kind, "title": "T", "id": "t-1"}, "approved").startswith(prefix + "T\n")


# ---- L3: a failed request does not flip while claude cannot be asked ----
def test_a_failed_request_stays_failed_while_the_sessions_are_unknown(tmp_path):
    known = {"v": True}
    fake = Fake()
    fs = Features(tmp_path, lambda *a, **k: fake.launch(*a, **k), lambda w: True if not known["v"] else w in fake.active,
                  live_known=lambda: known["v"])
    f = fs.create("Alfa", "Login", "desc", None)
    fake.active.clear()
    assert fs.list()[0]["status"] == "failed"
    known["v"] = False
    f = fs.list()[0]
    assert f["status"] == "failed" and "worker still active" not in events(f)


# ---- L4: every state file is written through atomicio ----
@pytest.mark.parametrize("module", ["proposals", "worksession", "usage", "curation", "onboarding", "planusage", "pending", "chief"])
def test_no_module_writes_state_through_a_shared_temporary_name(module):
    src = (ROOT / "scripts" / f"{module}.py").read_text(encoding="utf-8")
    assert '.with_suffix(".tmp")' not in src and '"projects.json.tmp"' not in src


def test_two_processes_adding_suggestions_do_not_lose_one(tmp_path):
    from proposals import Proposals
    a, b = Proposals(tmp_path), Proposals(tmp_path)           # two stores = two processes sharing the file
    a.add("P", "fix", "one", "d", by="w1")
    b.add("P", "fix", "two", "d", by="w2")
    assert {i["title"] for i in a.list()} == {"one", "two"}
    assert not list(tmp_path.glob("*.lock"))


def test_the_lock_is_released_when_the_block_fails(tmp_path):
    with pytest.raises(ValueError):
        with atomicio.locked(tmp_path / "x.json"):
            raise ValueError("boom")
    assert not (tmp_path / "x.json.lock").exists()


# ---- L5 / L7: the CLI is in English; «Resume» opens a terminal on every system ----
def test_workers_are_listed_with_the_chiefs_own_state(monkeypatch, capsys):
    monkeypatch.setattr(chief, "sessions", lambda: [{"id": "abc123", "state": "working", "status": "idle"}])
    monkeypatch.setattr(chief, "load_workers", lambda: [{"name": "w", "id": "abc123", "task": "t"}, {"name": "v", "id": "def456", "task": "t"}])
    chief.cmd_workers(None)
    out = capsys.readouterr().out
    assert "idle" in out and "finished" in out and "terminado" not in out


def test_resume_builds_a_terminal_command_for_each_system():
    win = chief.terminal_command("win32", "C:/p", "abc12345")
    assert win == (["claude", "--resume", "abc12345"], True)
    mac, _ = chief.terminal_command("darwin", "/p/my app", "abc12345")
    assert mac[0] == "osascript" and "claude --resume abc12345" in mac[-1] and "/p/my app" in mac[-1]
    linux, _ = chief.terminal_command("linux", "/p", "abc12345", which=lambda n: "/usr/bin/" + n if n == "xterm" else None)
    assert linux[0] == "xterm" and linux[-3:] == ["claude", "--resume", "abc12345"]
    assert chief.terminal_command("linux", "/p", "abc12345", which=lambda n: None) == (None, False)


# ---- L6: Settings tell the vault's category from the Chief's own ----
def test_settings_show_the_vaults_category_next_to_the_chiefs(monkeypatch, tmp_path):
    (tmp_path / "p").mkdir()
    monkeypatch.setattr(chief, "CFG", {**chief.CFG, "roots": [], "extras": [str(tmp_path / "p")], "ignore": [],
                                       "domains": {"Hub": "mine"}, "hide_domains": []})
    monkeypatch.setattr(chief, "hubs", lambda: [{"name": "Hub", "local": chief.norm(tmp_path / "p"), "domain": "vaults", "file": "x"}])
    monkeypatch.setattr(settings, "git_remote", lambda path: {"remote": "", "host": "", "git": False})
    monkeypatch.setattr(settings, "github_status", lambda: {})
    monkeypatch.setattr(settings, "plan_connection", lambda: "none")
    row = settings.snapshot()["projects"][0]
    assert row["domain"] == "mine" and row["from_vault"] == "vaults" and row["custom"] is True


# ---- L8: accents are transliterated, not dropped ----
def test_slugs_keep_the_letters_under_the_accents():
    assert chief.slug("Pipelines configuráveis") == "pipelines-configuraveis"
    assert F._slug("Correção do início") == "correcao-do-inicio"
