"""A user pipeline can be chosen for a request only when it is made of the built-in phases (docs/plans/2026-11-23-choose-pipeline-in-new-request.md)."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pipelines  # noqa: E402

KINDS = ("feature", "fix", "maintenance", "analysis")


def mine(kind="feature", pid="my-flow"):
    d = json.loads((pipelines.builtin_dir() / f"{kind}.json").read_text(encoding="utf-8"))
    d["id"], d["kind"] = pid, kind
    return d


def drop(d, *sids):
    d = copy.deepcopy(d)
    d["steps"] = [s for s in d["steps"] if s["id"] not in sids]
    return d


@pytest.mark.parametrize("kind", KINDS)
def test_a_copy_of_a_standard_type_is_runnable(kind):
    d = mine(kind)
    pipelines.validate(d)
    assert pipelines.runnable(d) == []


def test_kind_is_a_known_key_with_a_closed_value():
    d = mine()
    d["kind"] = "ship"
    with pytest.raises(pipelines.PipelineError):
        pipelines.validate(d)
    d.pop("kind")
    pipelines.validate(d)                                           # still a valid pipeline, just not a usable one
    assert "kind" in pipelines.runnable(d)[0]


@pytest.mark.parametrize("kind", ("ship", "release"))
def test_publishing_types_are_never_runnable(kind):
    d = json.loads((pipelines.builtin_dir() / f"{kind}.json").read_text(encoding="utf-8"))
    d["id"] = "my-" + kind
    assert pipelines.runnable(d)


def test_approvals_and_the_plan_validation_may_be_left_out():
    d = drop(mine(), "ok-spec")
    pipelines.validate(d)
    assert pipelines.runnable(d) == []
    d = drop(mine("maintenance"), "plancheck", "ok-plan")
    pipelines.validate(d)
    assert pipelines.runnable(d) == []


def test_the_last_step_must_be_the_users_acceptance_of_the_review():
    d = drop(mine(), "accept")
    assert any("acceptance" in p for p in pipelines.runnable(d))


def test_a_phase_cannot_be_invented_or_repeated():
    d = mine()
    d["steps"][0]["phase"] = "write"
    d["steps"][0]["artifact"] = "write.md"
    assert any("«write»" in p for p in pipelines.runnable(d))
    d = mine()
    twin = copy.deepcopy(d["steps"][0])
    twin["id"] = "spec-2"
    d["steps"].insert(1, twin)
    assert any("twice" in p for p in pipelines.runnable(d))


def test_a_step_must_be_the_built_in_one_of_its_phase():
    for key, val in (("artifact", "other.md"), ("mode", "worktree"), ("prompt", {"ref": "builtin:fix.spec"}), ("questions", False)):
        d = mine()
        d["steps"][0][key] = val
        assert any("built-in" in p for p in pipelines.runnable(d)), key
    d = mine()
    d["steps"][0]["label"], d["steps"][0]["model"] = "My spec", "sonnet"      # label and model are free
    assert pipelines.runnable(d) == []


def test_the_review_keeps_its_way_back_and_its_commit():
    d = mine()
    review = next(s for s in d["steps"] if s["id"] == "review")
    review["on"]["approved"] = {}
    assert any("review" in p for p in pipelines.runnable(d))


def test_a_phase_needs_what_its_prompt_reads():
    d = drop(mine(), "plan", "plancheck", "ok-plan")
    assert any("«plan»" in p and "build" in p for p in pipelines.runnable(d))


def test_a_phase_cannot_come_before_what_it_reads():
    d = mine()
    ids = [s["id"] for s in d["steps"]]
    order = ["plan", "plancheck", "ok-plan", "spec", "ok-spec", "build", "review", "accept"]
    assert sorted(order) == sorted(ids)
    d["steps"] = [next(s for s in d["steps"] if s["id"] == i) for i in order]
    assert any("must come before" in p for p in pipelines.runnable(d))


def test_a_build_needs_the_review_after_it():
    d = drop(mine(), "review", "accept")
    d["steps"].append({"id": "accept", "type": "approval", "of": "build", "requires_user": True})
    assert any("review" in p for p in pipelines.runnable(d))


def test_a_step_may_be_renamed_and_the_ways_back_follow():
    d = mine()
    for s in d["steps"]:
        if s["id"] == "build":
            s["id"] = "do-it"
        if s["id"] == "review":
            s["on"]["blocked"]["goto"] = "do-it"
    pipelines.validate(d)
    assert pipelines.runnable(d) == []
