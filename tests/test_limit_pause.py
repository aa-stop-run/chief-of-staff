"""A usage limit in the middle of a phase: the request is marked paused, and its phase is restarted once the limit has reset."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import LIMIT_GRACE_S, MAX_LIMIT_RESUMES, Features  # noqa: E402
from test_auto_review import Fake, finish_stage  # noqa: E402

RESET = datetime(2026, 11, 8, 15, 0, 0)
RESET_ISO = RESET.isoformat(timespec="seconds")
AFTER = RESET + timedelta(seconds=LIMIT_GRACE_S + 5)


class Limits:
    """Which workers are currently stopped by a limit (what planusage would read from their logs)."""

    def __init__(self):
        self.by_worker = {}

    def __call__(self, worker_id):
        return self.by_worker.get(worker_id)

    def hit(self, worker_id, auto=False, resume_at=RESET_ISO, kind="session"):
        self.by_worker[worker_id] = {"kind": kind, "auto": auto, "resume_at": resume_at, "text": "Usage limit reached"}


@pytest.fixture
def env(tmp_path):
    fake, limits, stopped = Fake(), Limits(), []

    def stop(worker_id):                                                         # stopping a session really ends it
        stopped.append(worker_id)
        fake.active.discard(worker_id)

    fs = Features(tmp_path, fake.launch, fake.is_active, on_release=stop, limit=limits)
    return fs, fake, limits, tmp_path, stopped


def card(fs, f):
    return next(x for x in fs.list() if x["id"] == f["id"])


def test_a_worker_stopped_by_a_limit_marks_its_request_paused_until_the_reset(env):
    fs, fake, limits, _, _ = env
    f = fs.create("Alfa", "Login", "x", None)
    limits.hit("w1", auto=True)
    c = card(fs, f)
    assert c["paused"] == {"until": RESET_ISO, "kind": "session", "auto": True} and c["status"] == "working"
    assert [h["event"] for h in c["history"]].count("paused by usage limit") == 1
    card(fs, f)                                                                  # listing again does not log it again
    assert [h["event"] for h in card(fs, f)["history"]].count("paused by usage limit") == 1


def test_a_note_about_the_pause_never_contains_a_time_of_day(env):
    fs, _, limits, *_ = env
    f = fs.create("Alfa", "Login", "x", None)
    limits.hit("w1")
    note = next(h["note"] for h in card(fs, f)["history"] if h["event"] == "paused by usage limit")
    assert note == "session" and ":" not in note


def test_the_pause_is_forgotten_when_the_worker_carries_on_by_itself(env):
    fs, _, limits, *_ = env
    f = fs.create("Alfa", "Login", "x", None)
    limits.hit("w1", auto=True)
    assert "paused" in card(fs, f)
    limits.by_worker.clear()                                                     # Claude Code continued: no notice any more
    assert "paused" not in card(fs, f)


def test_nothing_is_restarted_before_the_limit_has_reset_and_a_grace_period_passed(env):
    fs, fake, limits, *_ = env
    f = fs.create("Alfa", "Login", "x", None)
    limits.hit("w1")
    card(fs, f)
    assert fs.auto_resume(RESET - timedelta(minutes=5)) == [] and fs.auto_resume(RESET + timedelta(seconds=LIMIT_GRACE_S - 5)) == []
    assert len(fake.calls) == 1


def test_a_stuck_worker_is_replaced_once_the_limit_has_reset(env):
    fs, fake, limits, _, stopped = env
    f = fs.create("Alfa", "Login", "x", None)
    limits.hit("w1")                                                             # the session sits at the notice, waiting
    card(fs, f)
    assert fs.auto_resume(AFTER) == [f["id"]]
    c = card(fs, f)
    assert len(fake.calls) == 2 and stopped == ["w1"] and c["worker_id"] == "w2" and c["status"] == "working"
    assert "paused" not in c and c["limit_resumes"] == 1
    assert any(h["event"] == "resumed after usage limit" and h["note"] == f"try 1 of {MAX_LIMIT_RESUMES}" for h in c["history"])
    assert fake.calls[1]["task"] == fake.calls[0]["task"] or "spec" in fake.calls[1]["task"]            # the same phase again


def test_a_worker_that_continued_by_itself_is_left_alone(env):
    fs, fake, limits, *_ = env
    f = fs.create("Alfa", "Login", "x", None)
    limits.hit("w1", auto=True)
    card(fs, f)
    limits.by_worker.clear()
    assert fs.auto_resume(AFTER) == [] and len(fake.calls) == 1


def test_a_worker_that_died_at_the_limit_is_restarted_too(env):
    fs, fake, limits, *_ = env
    f = fs.create("Alfa", "Login", "x", None)
    limits.hit("w1")
    card(fs, f)
    fake.active.discard("w1")                                                    # the session ended instead of waiting
    assert card(fs, f)["status"] == "failed" and "paused" in card(fs, f)
    assert fs.auto_resume(AFTER) == [f["id"]] and card(fs, f)["status"] == "working"


def test_a_request_is_restarted_only_a_few_times(env):
    fs, fake, limits, *_ = env
    f = fs.create("Alfa", "Login", "x", None)
    for round_ in range(MAX_LIMIT_RESUMES):
        limits.by_worker.clear()
        limits.hit(f"w{round_ + 1}")
        card(fs, f)
        assert fs.auto_resume(AFTER) == [f["id"]]
    limits.by_worker.clear()
    limits.hit(f"w{MAX_LIMIT_RESUMES + 1}")
    card(fs, f)
    assert fs.auto_resume(AFTER) == [] and len(fake.calls) == MAX_LIMIT_RESUMES + 1                   # then it is left to the user
    assert card(fs, f)["paused"]["until"] == RESET_ISO


def test_the_plan_validation_is_restarted_as_a_validation_not_as_a_plan(tmp_path):
    fake, limits = Fake(), Limits()
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True, limit=limits)
    f = fs.create("Alfa", "Login", "x", None)
    finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    fs.approve(f["id"])
    finish_stage(fs, fake, tmp_path, f["id"], "plan.md")                         # the validator starts
    assert card(fs, f)["pv"]["checking"] is True
    worker = card(fs, f)["worker_id"]
    limits.hit(worker)
    card(fs, f)
    assert fs.auto_resume(AFTER) == [f["id"]]
    assert "independent plan validator" in fake.calls[-1]["task"] and card(fs, f)["pv"]["checking"] is True


def test_a_request_waiting_for_the_user_is_never_touched(env):
    fs, fake, limits, root, _ = env
    f = fs.create("Alfa", "Login", "x", None)
    finish_stage(fs, fake, root, f["id"], "spec.md")
    limits.hit("w1")
    c = card(fs, f)
    assert c["status"] == "awaiting" and "paused" not in c and fs.auto_resume(AFTER) == []


def test_without_a_way_to_read_limits_nothing_changes(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    f = fs.create("Alfa", "Login", "x", None)
    assert fs.auto_resume(AFTER) == [] and "paused" not in card(fs, f)


def test_a_failed_restart_is_reported_not_hidden(env):
    fs, fake, limits, *_ = env
    f = fs.create("Alfa", "Login", "x", None)
    limits.hit("w1")
    card(fs, f)

    def boom(*a, **k):
        raise RuntimeError("claude did not start")

    fs._launch = boom
    fake.active.discard("w1")                                                    # the session was already gone
    assert fs.auto_resume(AFTER) == []
    c = card(fs, f)
    assert c["status"] == "failed" and any(h["event"] == "resume failed" for h in c["history"])


def test_a_restart_that_cannot_start_while_the_old_session_still_sits_at_the_limit_is_tried_again_later(env):
    fs, fake, limits, *_ = env
    f = fs.create("Alfa", "Login", "x", None)
    limits.hit("w1")
    card(fs, f)
    real = fs._launch
    fs._launch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("claude did not start"))
    assert fs.auto_resume(AFTER) == [] and card(fs, f)["status"] == "working"          # the old session is still there, so nothing is lost
    fs._launch = real
    assert fs.auto_resume(AFTER) == [f["id"]] and card(fs, f)["worker_id"] == "w2"      # and the next tick succeeds
