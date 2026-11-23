"""The concept phases in pipelines of your own: what makes them usable, and the editor's handling of them."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipelines  # noqa: E402
from test_page_pipelines import call  # noqa: E402

pytestmark = pytest.mark.with_concept
LEGACY = Path(__file__).resolve().parent / "legacy_pipelines"


def shipped(kind):
    d = json.loads((pipelines.builtin_dir() / f"{kind}.json").read_text(encoding="utf-8"))
    d["id"], d["kind"] = "my-flow", kind
    return d


def drop(d, *sids):
    d = copy.deepcopy(d)
    d["steps"] = [s for s in d["steps"] if s["id"] not in sids]
    return d


@pytest.mark.parametrize("kind", ("feature", "maintenance"))
def test_the_shipped_types_have_the_concept_and_a_copy_of_them_is_usable(kind):
    d = shipped(kind)
    assert [s["id"] for s in d["steps"][:3]] == ["concept", "conceptcheck", "ok-concept"]
    pipelines.validate(d)
    assert pipelines.runnable(d) == []


@pytest.mark.parametrize("kind", ("feature", "maintenance"))
def test_a_pipeline_made_before_the_concept_existed_is_still_usable(kind):
    old = json.loads((LEGACY / f"{kind}.json").read_text(encoding="utf-8"))
    old["id"], old["kind"] = "old-flow", kind
    assert not any(s.get("phase") == "concept" for s in old["steps"])
    assert pipelines.runnable(old) == []


def test_the_concept_and_its_check_may_be_left_out_or_the_check_alone():
    assert pipelines.runnable(drop(shipped("feature"), "concept", "conceptcheck", "ok-concept")) == []
    assert pipelines.runnable(drop(shipped("feature"), "conceptcheck")) == []
    assert pipelines.runnable(drop(shipped("feature"), "ok-concept")) == []


def test_a_concept_check_needs_its_concept_and_the_concept_comes_first():
    d = drop(shipped("feature"), "concept", "ok-concept")
    assert any("«concept»" in p for p in pipelines.runnable(d))
    d = shipped("feature")
    order = ["spec", "ok-spec", "concept", "conceptcheck", "ok-concept", "plan", "plancheck", "ok-plan", "build", "review", "accept"]
    d["steps"] = [next(s for s in d["steps"] if s["id"] == i) for i in order]
    assert any("concept must come before" in p for p in pipelines.runnable(d))


def test_a_fix_or_an_analysis_has_no_concept():
    d = json.loads((pipelines.builtin_dir() / "fix.json").read_text(encoding="utf-8"))
    d["id"], d["kind"] = "my-fix", "fix"
    d["steps"].insert(0, copy.deepcopy(shipped("feature")["steps"][0]))
    assert any("not a phase of a fix request" in p for p in pipelines.runnable(d))


def test_the_concept_step_cannot_be_changed_into_something_else():
    d = shipped("feature")
    d["steps"][0]["prompt"] = {"ref": "builtin:feature.spec"}
    assert any("built-in" in p for p in pipelines.runnable(d))
    d = shipped("feature")
    d["steps"][0]["artifact"] = "other.md"
    assert pipelines.runnable(d)


# ------------------------------------------------------------------ the editor

def test_taking_the_concept_away_takes_its_validation_and_approval_and_it_comes_back_whole():
    d = shipped("feature")
    base = shipped("feature")
    gone = call(f"pipeRemovePhase({json.dumps(d)},0)")
    assert [s["id"] for s in gone["steps"]][:2] == ["spec", "ok-spec"] and pipelines.runnable(gone) == []
    assert call(f"pipeCanAddConcept({json.dumps(gone)},{json.dumps(base)})") is True
    back = call(f"pipeAddConcept({json.dumps(gone)},{json.dumps(base)})")
    assert [s["id"] for s in back["steps"]] == [s["id"] for s in d["steps"]] and pipelines.runnable(back) == []
    assert call(f"pipeCanAddConcept({json.dumps(d)},{json.dumps(base)})") is False
    assert call(f"pipeAddConcept({json.dumps(d)},{json.dumps(base)})") == d


def test_the_concept_validation_alone_can_be_removed_and_put_back():
    d = shipped("maintenance")
    base = shipped("maintenance")
    i = next(i for i, s in enumerate(d["steps"]) if s.get("phase") == "concept-validation")
    gone = call(f"pipeRemovePhase({json.dumps(d)},{i})")
    assert pipelines.runnable(gone) == [] and call(f"pipeCanAddCheck({json.dumps(gone)},{json.dumps(base)},\"concept-validation\")") is True
    back = call(f"pipeAddCheck({json.dumps(gone)},{json.dumps(base)},\"concept-validation\")")
    assert [s["id"] for s in back["steps"]] == [s["id"] for s in d["steps"]] and pipelines.runnable(back) == []


def test_a_fix_has_nothing_to_add():
    base = json.loads((pipelines.builtin_dir() / "fix.json").read_text(encoding="utf-8"))
    assert call(f"pipeCanAddConcept({json.dumps(base)},{json.dumps(base)})") is False
