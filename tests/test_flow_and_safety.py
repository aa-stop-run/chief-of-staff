"""Fewer clicks and fewer surprises: «run until the review», the validator that does not block on minor findings, alerts for
stuck workers, rules that no worker escapes, a commit that refuses secrets, state files safe across processes, short artifacts."""
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomicio  # noqa: E402
import chief  # noqa: E402
import prompts  # noqa: E402
import prompts_pt  # noqa: E402
from alerts import Alerts  # noqa: E402
from features import Features  # noqa: E402
from test_auto_review import REVIEW_OK, Fake, feature_with_plan, finish_stage  # noqa: E402
from test_chief_actions import _repo  # noqa: E402

MINOR = "## Verdict\nBLOCKED — details.\n\n## Findings\n- **[MEDIUM] a:** x\n- **[LOW] b:** y\n"
SERIOUS = "## Verdict\nBLOCKED — gap.\n\n## Findings\n- **[HIGH] s:** missing\n- **[MEDIUM] t:** x\n"


def make(tmp_path):
    fake = Fake()
    return Features(tmp_path, fake.launch, fake.is_active, auto_review=True), fake


def questions(root, fid, items):
    (Path(root) / "features" / fid / "questions.json").write_text(json.dumps(items), encoding="utf-8")


def events(f):
    return [h["event"] for h in f["history"]]


# ---- the plan validator ----
def test_the_validator_does_not_send_the_plan_back_for_minor_findings(tmp_path):
    fs, fake = make(tmp_path)
    f = feature_with_plan(fs, fake, tmp_path)
    f = finish_stage(fs, fake, tmp_path, f["id"], "plan-review.md", MINOR)
    assert f["status"] == "awaiting" and f["stage"] == "plan"
    assert any(h["event"] == "plan validated" and h["note"] == "with reservations" for h in f["history"])
    assert "plan fixes" not in events(f)


def test_a_serious_finding_sends_the_plan_back_once_and_then_it_is_the_users(tmp_path):
    fs, fake = make(tmp_path)
    f = feature_with_plan(fs, fake, tmp_path)
    f = finish_stage(fs, fake, tmp_path, f["id"], "plan-review.md", SERIOUS)
    assert "plan fixes" in events(f)
    finish_stage(fs, fake, tmp_path, f["id"], "plan.md", "# fixed")
    f = finish_stage(fs, fake, tmp_path, f["id"], "plan-review.md", SERIOUS)
    assert f["status"] == "awaiting" and "plan fixes limit" in events(f) and events(f).count("plan fixes") == 1


def test_a_blocked_verdict_without_any_finding_still_goes_back(tmp_path):
    fs, fake = make(tmp_path)
    f = feature_with_plan(fs, fake, tmp_path)
    f = finish_stage(fs, fake, tmp_path, f["id"], "plan-review.md", "## Verdict\nBLOCKED — unreadable plan.\n")
    assert "plan fixes" in events(f)


# ---- run until the review ----
def test_autopilot_approves_spec_and_validated_plan_and_stops_at_the_implementation(tmp_path):
    fs, fake = make(tmp_path)
    f = fs.create("Alfa", "Login", "I want 2FA", None, autopilot=True)
    questions(tmp_path, f["id"], [])
    f = finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    assert f["stage"] == "plan" and f["status"] == "working" and "approved automatically" in events(f)
    questions(tmp_path, f["id"], [])
    finish_stage(fs, fake, tmp_path, f["id"], "plan.md")
    questions(tmp_path, f["id"], [])
    f = finish_stage(fs, fake, tmp_path, f["id"], "plan-review.md", REVIEW_OK)
    assert f["stage"] == "build" and f["status"] == "working" and events(f).count("approved automatically") == 2
    assert "approved" not in events(f)                      # no click was recorded: the history says who approved
    finish_stage(fs, fake, tmp_path, f["id"], "report.md")
    f = finish_stage(fs, fake, tmp_path, f["id"], "review.md", REVIEW_OK)
    assert f["stage"] == "review" and f["status"] == "awaiting"      # accepting the implementation is always the user's


def test_autopilot_stops_when_the_worker_left_questions_or_no_readable_file(tmp_path):
    fs, fake = make(tmp_path)
    f = fs.create("Alfa", "Login", "I want 2FA", None, autopilot=True)
    questions(tmp_path, f["id"], [{"id": "P1", "text": "Which one?", "options": [{"label": "A"}, {"label": "B"}]}])
    f = finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    assert f["stage"] == "spec" and f["status"] == "awaiting" and "autopilot stopped" in events(f)
    g = fs.create("Alfa", "Other", "x", None, autopilot=True)          # no questions.json at all: not proof of «no questions»
    g = finish_stage(fs, fake, tmp_path, g["id"], "spec.md")
    assert g["stage"] == "spec" and g["status"] == "awaiting"


def test_autopilot_does_not_pass_a_plan_that_was_not_validated(tmp_path):
    fs, fake = make(tmp_path)
    f = fs.create("Alfa", "Login", "I want 2FA", None, autopilot=True)
    questions(tmp_path, f["id"], [])
    finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    questions(tmp_path, f["id"], [])
    finish_stage(fs, fake, tmp_path, f["id"], "plan.md")
    finish_stage(fs, fake, tmp_path, f["id"], "plan-review.md", SERIOUS)
    questions(tmp_path, f["id"], [])
    finish_stage(fs, fake, tmp_path, f["id"], "plan.md", "# fixed")
    questions(tmp_path, f["id"], [])
    f = finish_stage(fs, fake, tmp_path, f["id"], "plan-review.md", SERIOUS)
    assert f["stage"] == "plan" and f["status"] == "awaiting"
    assert f["history"][-1]["event"] == "autopilot stopped"


def test_without_autopilot_nothing_is_approved_by_itself(tmp_path):
    fs, fake = make(tmp_path)
    f = fs.create("Alfa", "Login", "I want 2FA", None)
    questions(tmp_path, f["id"], [])
    f = finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    assert f["stage"] == "spec" and f["status"] == "awaiting" and "autopilot" not in f


@pytest.mark.parametrize("kind", ["ship", "release"])
def test_publishing_and_production_requests_never_run_on_autopilot(tmp_path, kind):
    fs, fake = make(tmp_path)
    f = fs.create("Alfa", "Publish", "commit it", None, kind=kind, autopilot=True)
    assert "autopilot" not in f


# ---- alerts ----
def test_an_alert_only_shows_after_the_grace_period_and_is_said_once():
    a = Alerts(after_s=180)
    cand = Alerts.items([{"id": "w1", "state": "blocked", "project": "P", "name": "build", "waiting_for": "permission prompt"}], [])
    assert a.update(cand, now=1000) == ([], [])
    shown, fresh = a.update(cand, now=1200)
    assert len(shown) == 1 and len(fresh) == 1 and shown[0]["detail"] == "permission prompt" and shown[0]["for_s"] == 200
    shown, fresh = a.update(cand, now=1300)
    assert len(shown) == 1 and fresh == []
    assert a.update([], now=1400) == ([], [])
    assert a.update(cand, now=1500) == ([], [])


def test_failed_requests_alert_and_working_workers_do_not():
    cand = Alerts.items([{"id": "w1", "state": "working", "project": "P"}],
                        [{"id": "f1", "status": "failed", "project": "P", "title": "Card"}, {"id": "f2", "status": "working"}])
    assert [c[0] for c in cand] == ["failed:f1"]


def test_a_blocked_worker_carries_what_it_waits_for(monkeypatch):
    monkeypatch.setattr(chief, "load_projects", lambda: [])
    monkeypatch.setattr(chief, "inbox_notes", lambda: [])
    monkeypatch.setattr(chief, "load_workers", lambda: [{"id": "abc123", "name": "w", "project": "P"}])
    live = [{"id": "abc123", "state": "blocked", "waitingFor": "permission:\n EnterWorktree"}]
    w = chief.full_state(live, with_git=False)["workers"][0]
    assert w["state"] == "blocked" and w["waiting_for"] == "permission: EnterWorktree"


# ---- rules no worker escapes ----
def test_every_worker_is_denied_pushing_and_the_curator_too(monkeypatch):
    assert chief.NO_PUSH in chief.fixed_deny() and all(d in chief.fixed_deny() for d in chief.FIXED_DENY)
    allowed = chief.fixed_deny(["Bash(git push -u:*)"], ["Bash(git config:*)"])
    assert chief.NO_PUSH not in allowed and allowed[0] == "Bash(git config:*)" and "Bash(gh pr merge:*)" in allowed
    assert len(allowed) == len(set(allowed))
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "backgrounded · abc12345", "")

    monkeypatch.setattr(chief, "vault_enabled", lambda: True)
    monkeypatch.setattr(chief.subprocess, "run", run)
    monkeypatch.setattr(chief, "record_worker", lambda w: None)
    chief.launch_curator("curate")
    deny = next(c for c in seen["cmd"] if c.startswith("--disallowedTools=")).split("=", 1)[1].split(",")
    assert chief.NO_PUSH in deny and seen["cmd"][-2:] == ["--", "curate"]


def test_a_launch_that_cannot_be_read_says_what_was_expected(monkeypatch):
    monkeypatch.setattr(chief, "load_projects", lambda: [{"name": "Alfa", "slug": "alfa", "path": ".", "hub": ""}])
    monkeypatch.setattr(chief.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "started somehow", ""))
    with pytest.raises(RuntimeError, match="backgrounded"):
        chief.launch_worker("Alfa", "task")


# ---- the commit refuses secrets ----
@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "server.pem", "app.key", "credentials.json", "secrets.json",
                                  "SEGREDOS.md", ".npmrc"])
def test_secret_looking_files_are_recognised(name):
    assert chief.secret_paths([f"deep/dir/{name}"]) == [f"deep/dir/{name}"]


@pytest.mark.parametrize("name", [".env.example", "secrets.py", "keyboard.py", "token_utils.py", "README.md", "environment.yml"])
def test_ordinary_files_are_not_taken_for_secrets(name):
    assert chief.secret_paths([name]) == []


def test_commit_worktree_refuses_a_secret_and_leaves_everything_uncommitted(tmp_path):
    g = _repo(tmp_path)
    (tmp_path / "new.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / ".env").write_text("KEY=1", encoding="utf-8")
    ok, info = chief.commit_worktree(tmp_path, "feat: x")
    assert not ok and ".env" in info and "secret" in info
    assert g("log", "--format=%s").strip() == "base"
    assert g("diff", "--cached", "--name-only").strip() == ""          # nothing left staged
    assert (tmp_path / ".env").is_file() and (tmp_path / "new.py").is_file()


# ---- state files ----
def test_atomic_write_replaces_the_file_and_leaves_no_temporary(tmp_path):
    target = tmp_path / "state" / "x.json"
    atomicio.write_text(target, "one")
    atomicio.write_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two" and [p.name for p in target.parent.iterdir()] == ["x.json"]


def test_the_lock_makes_concurrent_updates_lossless(tmp_path):
    target = tmp_path / "n.json"
    atomicio.write_text(target, "0")

    def bump():
        for _ in range(25):
            with atomicio.locked(target):
                atomicio.write_text(target, str(int(target.read_text(encoding="utf-8")) + 1))

    threads = [threading.Thread(target=bump) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert target.read_text(encoding="utf-8") == "100" and not (tmp_path / "n.json.lock").exists()


def test_a_lock_left_by_a_dead_process_is_taken_over_and_a_live_one_times_out(tmp_path):
    target = tmp_path / "n.json"
    (tmp_path / "n.json.lock").write_text("", encoding="utf-8")
    with pytest.raises(TimeoutError):
        with atomicio.locked(target, wait_s=0.2):
            pass
    with atomicio.locked(target, wait_s=0.2, stale_s=0):
        pass
    assert not (tmp_path / "n.json.lock").exists()


def test_recording_workers_from_several_threads_loses_none(tmp_path, monkeypatch):
    monkeypatch.setattr(chief, "STATE", tmp_path)
    threads = [threading.Thread(target=chief.record_worker, args=({"id": f"w{i}", "name": f"n{i}"},)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(w["id"] for w in chief.load_workers()) == sorted(f"w{i}" for i in range(12))


# ---- short artifacts ----
@pytest.mark.parametrize("P,words", [(prompts, ("Bullets only", "80", "-details.md")), (prompts_pt, ("Só tópicos", "80", "-details.md"))])
def test_the_brevity_rule_exists_in_both_languages(P, words):
    assert all(w in P.BRIEF for w in words)


def test_every_phase_the_plan_validation_and_questions_get_the_brevity_rule(tmp_path):
    fs, fake = make(tmp_path)
    f = feature_with_plan(fs, fake, tmp_path)                  # spec, plan, then the validator
    assert all(prompts.BRIEF in c["task"] for c in fake.calls) and len(fake.calls) == 3
    fs.ask(f["id"], "why?")
    assert prompts.BRIEF in fake.calls[-1]["task"]
    assert "{" not in prompts.BRIEF and "{" not in prompts_pt.BRIEF     # appended after formatting: no placeholders
