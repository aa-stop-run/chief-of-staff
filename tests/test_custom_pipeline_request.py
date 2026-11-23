"""A request started from one of the user's own pipelines (docs/plans/2026-11-23-choose-pipeline-in-new-request.md)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipelines  # noqa: E402
import pipelines_store as store  # noqa: E402
from features import Features, FeatureError  # noqa: E402
from test_auto_review import Fake, FakeCommit, REVIEW_OK, finish_stage  # noqa: E402


def base(kind):
    d = json.loads((pipelines.builtin_dir() / f"{kind}.json").read_text(encoding="utf-8"))
    d["kind"] = kind
    return d


def make(home, pid, data):
    data = dict(data)
    data["id"], data["name"] = pid, pid
    store.create(home, data)


@pytest.fixture
def env(tmp_path):
    home, root = tmp_path / "home", tmp_path / "state"
    root.mkdir()
    fake, commit = Fake(), FakeCommit()
    fs = Features(root, fake.launch, fake.is_active, auto_review=True, committer=commit, pipeline_home=lambda: home)
    return fs, fake, root, home, commit


def lean_maintenance():
    """Maintenance without the independent plan validation, with a single review round and a chosen model for the build."""
    d = base("maintenance")
    d["steps"] = [s for s in d["steps"] if s["id"] != "plancheck"]
    for s in d["steps"]:
        if s["id"] == "build":
            s["model"] = "sonnet"
        if s["id"] == "review":
            s["on"]["blocked"]["max_rounds"] = 1
    return d


def test_a_request_takes_its_type_and_definition_from_the_pipeline(env):
    fs, fake, root, home, _ = env
    make(home, "lean-maintenance", lean_maintenance())
    f = fs.create("Alfa", "Tidy up", "tidy", None, kind="feature", pipeline="lean-maintenance")
    assert f["kind"] == "maintenance" and f["pipeline"]["id"] == "lean-maintenance"
    assert f["stage"] == "plan"                                            # no spec in this type
    snap = json.loads((root / "features" / f["id"] / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["id"] == "lean-maintenance" and not any(s["id"] == "plancheck" for s in snap["steps"])


def test_it_runs_to_the_commit_without_the_plan_validation(env):
    fs, fake, root, home, commit = env
    make(home, "lean-maintenance", lean_maintenance())
    f = fs.create("Alfa", "Tidy up", "tidy", None, pipeline="lean-maintenance")
    g = finish_stage(fs, fake, root, f["id"], "plan.md")
    assert (g["stage"], g["status"]) == ("plan", "awaiting")               # straight to the user's approval: nothing validated it
    assert not any("plan validation" in h["event"] for h in g["history"])
    fs.approve(f["id"])
    (root / "features" / f["id"] / "work").mkdir(parents=True)
    g = finish_stage(fs, fake, root, f["id"], "report.md")
    assert (g["stage"], g["status"]) == ("review", "working")
    g = finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_OK)
    assert g["status"] == "awaiting" and len(commit.calls) == 1


def test_the_step_model_is_used_for_that_phase(env):
    fs, fake, root, home, _ = env
    seen = []
    fs._route_step = lambda step, m: (seen.append((step["id"], step.get("model"))) or (step.get("model") or m or None, None))
    make(home, "lean-maintenance", lean_maintenance())
    f = fs.create("Alfa", "Tidy up", "tidy", None, pipeline="lean-maintenance")
    finish_stage(fs, fake, root, f["id"], "plan.md")
    fs.approve(f["id"])
    assert ("build", "sonnet") in seen


def test_the_request_keeps_its_definition_when_the_file_changes_or_goes(env):
    fs, fake, root, home, _ = env
    make(home, "lean-maintenance", lean_maintenance())
    f = fs.create("Alfa", "Tidy up", "tidy", None, pipeline="lean-maintenance")
    (home / "pipelines" / "lean-maintenance.json").unlink()
    fs2 = Features(root, fake.launch, fake.is_active, auto_review=True, pipeline_home=lambda: home)
    g = finish_stage(fs2, fake, root, f["id"], "plan.md")
    assert g["status"] == "awaiting" and not any("plan validation" in h["event"] for h in g["history"])


def test_a_pipeline_that_is_missing_built_in_or_not_usable_is_refused(env):
    fs, _, _, home, _ = env
    with pytest.raises(FeatureError):
        fs.create("Alfa", "T", "d", None, pipeline="ghost")
    with pytest.raises(FeatureError, match="built-in type"):
        fs.create("Alfa", "T", "d", None, pipeline="feature")
    (home / "pipelines").mkdir(parents=True)
    old = base("maintenance")
    old["id"] = "old-flow"
    old.pop("kind")
    (home / "pipelines" / "old-flow.json").write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises(FeatureError, match="cannot start a request"):
        fs.create("Alfa", "T", "d", None, pipeline="old-flow")
    assert fs.list() == []                                                # nothing was created


def test_the_type_of_such_a_request_cannot_be_switched(env):
    fs, _, _, home, _ = env
    make(home, "lean-maintenance", lean_maintenance())
    f = fs.create("Alfa", "Tidy up", "tidy", None, pipeline="lean-maintenance")
    f = next(x for x in fs.list() if x["id"] == f["id"])
    with pytest.raises(FeatureError, match="cannot be converted"):
        fs.set_kind(f["id"], "feature")


def test_a_split_plan_still_needs_the_users_approval_and_its_parts_follow_the_pipeline(env):
    from test_fanout_flow import A, B, SPEC, plan
    fs, fake, root, home, _ = env
    d = base("feature")
    d["steps"] = [s for s in d["steps"] if s["id"] != "ok-spec"]
    make(home, "lean-feature", d)
    f = fs.create("Alfa", "Big", "big", None, pipeline="lean-feature", autopilot=True, path=str(root))
    finish_stage(fs, fake, root, f["id"], "spec.md", SPEC)
    fs.approve(f["id"])
    (root / "features" / f["id"] / "questions.json").write_text("[]", encoding="utf-8")
    finish_stage(fs, fake, root, f["id"], "plan.md", plan([A, B]))
    g = finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)
    assert g["status"] == "awaiting" and not g.get("split")                 # autopilot never fans a plan out: only the click does
    fs.approve(f["id"])
    parts = [x for x in fs.list() if x.get("parent") == f["id"]]
    assert len(parts) == 2 and all(x["pipeline"]["id"] == "lean-feature" and x["kind"] == "feature" for x in parts)
    assert all(not (root / "features" / x["id"] / "pipeline.json").read_text(encoding="utf-8").count('"ok-spec"') for x in parts)
