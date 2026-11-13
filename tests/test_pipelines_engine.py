"""The engine's decisions: transitions per verdict, bounded rounds, approvals only by the user, fixed denials, snapshots."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import engine  # noqa: E402
import pipelines  # noqa: E402
from engine import Engine  # noqa: E402


def load(kind):
    return pipelines.load(pipelines.builtin_dir() / f"{kind}.json", builtin=True)


def test_feature_walks_through_its_steps_and_waits_at_approvals():
    e, f = Engine(load("feature")), {}
    assert e.first_step()["id"] == "spec"
    assert e.on_worker_finished(f, e.step("spec")).kind == "wait_user"
    d = e.on_approve(f, e.step("ok-spec"))
    assert (d.kind, d.step) == ("launch", "plan")
    d = e.on_worker_finished(f, e.step("plan"))
    assert (d.kind, d.step) == ("launch", "plancheck")             # validation before the user sees the plan
    assert e.on_check(f, e.step("plancheck"), "approved").kind == "wait_user"
    d = e.on_approve(f, e.step("ok-plan"))
    assert d.step == "build"
    assert e.on_worker_finished(f, e.step("build")).step == "review"
    d = e.on_check(f, e.step("review"), "approved")
    assert (d.kind, d.step, d.action) == ("wait_user", "accept", "commit_worktree")
    assert e.on_approve(f, e.step("accept")).kind == "done"


def test_rounds_exhausted_goes_to_user_and_never_continues_alone():
    e, f = Engine(load("feature")), {}
    for n in (1, 2, 3):
        d = e.on_check(f, e.step("review"), "blocked")
        assert (d.kind, d.step, d.feedback) == ("launch", "build", "review") and f["rounds"]["review:blocked"] == n
    for _ in range(2):
        d = e.on_check(f, e.step("review"), "blocked")
        assert d.kind == "await" and "up to you" in d.log


def test_verdict_without_reading_is_retried_twice():
    e, f = Engine(load("feature")), {}
    assert [e.on_check(f, e.step("review"), "unknown").kind for _ in range(3)] == ["launch", "launch", "await"]


def test_max_total_rounds_goes_to_user():
    data = pipelines.snapshot(load("feature"))
    data.update(id="mine", limits={"max_total_rounds": 2})
    e, f = Engine(pipelines.load(data)), {"rounds": {"plancheck:blocked": 2}}
    assert e.on_check(f, e.step("review"), "blocked").kind == "await"


def test_check_never_advances_approval():
    e = Engine(load("feature"))
    with pytest.raises(ValueError):
        e.on_worker_finished({}, e.step("ok-spec"))
    with pytest.raises(ValueError):
        e.on_worker_finished({}, e.step("review"))
    with pytest.raises(ValueError):
        e.on_check({}, e.step("ok-spec"), "approved")


@pytest.mark.parametrize("gate", ["diverged", "notrun", "regress"])
def test_unconfirmed_tests_stop_the_commit(gate):
    d = Engine(load("feature")).on_check({}, load("feature").step("review"), "approved", [gate])
    assert d.kind == "await" and d.action == ""


def test_missing_tests_section_repeats_the_review():
    e = Engine(load("feature"))
    assert e.on_check({}, e.step("review"), "approved", ["missing"]).step == "review"


def test_request_changes_repeats_the_approved_step():
    e = Engine(load("feature"))
    d = e.on_request_changes({}, e.step("ok-plan"), "shorter")
    assert (d.kind, d.step, d.log) == ("launch", "plan", "shorter")


def test_ship_and_release_publish_only_after_an_approval():
    for kind in ("ship", "release"):
        e = Engine(load(kind))
        prev = e.p.steps[-2]
        assert prev["type"] == "approval"
        assert e.on_approve({}, prev).step == e.p.steps[-1]["id"]


def test_analysis_keeps_a_short_loop_and_archives():
    e, f = Engine(load("analysis")), {}
    assert e.on_worker_finished(f, e.step("build")).step == "review"
    assert e.on_check(f, e.step("review"), "approved").kind == "wait_user"
    assert e.step("review")["archive"] == "analysis"


def test_fixed_denies_always_added():
    allow, deny = engine.effective_permissions({"permissions": {"allow": ["Bash(git push:*)"], "deny": ["Edit"]}})
    assert "Edit" in deny and all(d in deny for d in engine.FIXED_DENY) and allow == ["Bash(git push:*)"]
    assert all(d in engine.effective_permissions({})[1] for d in engine.FIXED_DENY)


def test_snapshot_immune_to_file_edit():
    p = load("maintenance")
    snap = pipelines.snapshot(p)
    p.steps[0]["model"] = "changed-after"          # whoever edits the live object (or the file) does not touch the snapshot
    assert "model" not in snap["steps"][0]
    assert Engine(pipelines.load({**snap, "id": "copy"})).first_step()["id"] == "plan"
