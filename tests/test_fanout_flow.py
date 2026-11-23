"""R5 + R6: approving a plan with a split — the gate, the autopilot stop and the atomic fan-out."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fanout  # noqa: E402
from features import FeatureError, Features  # noqa: E402
from test_auto_review import REVIEW_OK, Fake, FakeCommit, finish_stage  # noqa: E402

A = {"id": "api", "title": "Export endpoint", "files": ["src/a.py", {"new": "tests/test_a.py"}]}
B = {"id": "ui", "title": "Export button", "files": ["app/b.html"]}
SPEC = "# Spec\n\n## Acceptance criteria\n- exports work\n- button works\n\n## Other\nx\n"


def plan(tasks):
    return f"# Plan\nTASKS: 4\n\n## Split\n\n```json\n{json.dumps({'tasks': tasks})}\n```\n"


class FlakyFake(Fake):
    """Launcher whose Nth launch raises."""

    def __init__(self, fail_on=()):
        super().__init__()
        self.fail_on, self.attempts = set(fail_on), 0

    def launch(self, *a, **k):
        self.attempts += 1
        if self.attempts in self.fail_on:
            raise RuntimeError("no capacity")
        return super().launch(*a, **k)


class Archive:
    def __init__(self):
        self.docs = []

    def __call__(self, doc, project, title, fid, created, text, lang):
        self.docs.append((doc, fid))
        return "archived"


@pytest.fixture
def env(tmp_path):
    fake, arch, released = FlakyFake(), Archive(), []
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True, committer=FakeCommit(), publisher=arch,
                  on_release=released.append)
    return fs, fake, tmp_path, arch, released


TITLES = []


def to_plan_approval(fs, fake, root, plan_text, kind="feature", autopilot=False, questions="[]", lang="en", rounds=0):
    TITLES.append(1)
    f = fs.create("Alfa", f"Export trips {len(TITLES)}", "Let me export trips", None, kind=kind, autopilot=autopilot, lang=lang, path=str(root))
    d = root / "features" / f["id"]
    if kind == "feature":
        finish_stage(fs, fake, root, f["id"], "spec.md", SPEC)
        fs.approve(f["id"])
    if questions is not None:
        (d / "questions.json").write_text(questions, encoding="utf-8")
    finish_stage(fs, fake, root, f["id"], "plan.md", plan_text)
    g = finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)
    for _ in range(rounds):                       # a plan the validator keeps blocking: the planner repeats it until the limit
        finish_stage(fs, fake, root, f["id"], "plan.md", plan_text)
        g = finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)
    return g


def stored(fs, fid):
    return next(x for x in fs.list() if x["id"] == fid)


# ------------------------------------------------------------------ R5: the gate and the autopilot stop

def test_open_questions_refuse_a_split_approval_without_side_effects(env):
    fs, fake, root, arch, _ = env
    f = to_plan_approval(fs, fake, root, plan([A, B]), questions='[{"id": "q1", "title": "t", "text": "x", "options": []}]')
    before, launches = stored(fs, f["id"]), len(fake.calls)
    with pytest.raises(FeatureError, match="open questions"):
        fs.approve(f["id"])
    after = stored(fs, f["id"])
    assert after == before and after["status"] == "awaiting" and len(fake.calls) == launches
    assert not any(h["event"] == "approved" and h["stage"] == "plan" for h in after["history"])
    assert not any(d[0] == "plan" for d in arch.docs)


def test_an_unreadable_questions_file_refuses_too(env):
    fs, fake, root, _, _ = env
    f = to_plan_approval(fs, fake, root, plan([A, B]), questions="not json")
    with pytest.raises(FeatureError, match="questions file is missing or invalid"):
        fs.approve(f["id"])


def test_a_blocking_split_is_refused_not_approved_linearly(env):
    fs, fake, root, _, _ = env
    clash = {"id": "ui", "title": "UI", "files": ["src/a.py"]}
    f = to_plan_approval(fs, fake, root, plan([A, clash]), rounds=1)     # blocked until the fix-round limit: it waits for the user
    assert f["status"] == "awaiting"
    with pytest.raises(FeatureError, match="ask for changes"):
        fs.approve(f["id"])
    many = [{"id": f"t{i}", "title": "T", "files": [f"f{i}.py"]} for i in range(11)]
    g = to_plan_approval(fs, fake, root, plan(many))
    with pytest.raises(FeatureError, match="size"):
        fs.approve(g["id"])


@pytest.mark.parametrize("plan_text", [plan([A]), "# Plan\nTASKS: 2\nsteps\n"])
def test_a_single_part_or_no_split_stays_linear(env, plan_text):
    fs, fake, root, _, _ = env
    f = to_plan_approval(fs, fake, root, plan_text)
    g = fs.approve(f["id"])
    assert g["stage"] == "build" and g["status"] == "working" and "split" not in g


def test_autopilot_stops_at_a_split_plan_valid_or_not(env):
    fs, fake, root, _, _ = env
    for tasks, rounds in (([A, B], 0), ([A, {"id": "ui", "title": "UI", "files": ["src/a.py"]}], 1)):
        g = to_plan_approval(fs, fake, root, plan(tasks), autopilot=True, rounds=rounds)
        assert g["status"] == "awaiting" and g["stage"] == "plan"
        assert any(h["event"] == "autopilot stopped" and "split" in h["note"] for h in g["history"])
        assert not any(h["event"] == "approved automatically" and h["stage"] == "plan" for h in g["history"])


def test_autopilot_still_approves_a_plan_without_a_split(env):
    fs, fake, root, _, _ = env
    g = to_plan_approval(fs, fake, root, "# Plan\nTASKS: 2\nsteps\n", autopilot=True)
    assert g["stage"] == "build"


# ------------------------------------------------------------------ R6: the atomic fan-out

def parts_of(fs, parent):
    return [stored(fs, t["part_id"]) for t in stored(fs, parent["id"])["split"]["tasks"]]


def test_two_parts_are_created_and_started_and_the_parent_closes(env):
    fs, fake, root, arch, released = env
    f = to_plan_approval(fs, fake, root, plan([A, B]))
    launches = len(fake.calls)
    fs.approve(f["id"])
    parent = stored(fs, f["id"])
    parts = parts_of(fs, parent)
    assert [p["part"] for p in parts] == ["api", "ui"] and len({p["id"] for p in parts}) == 2
    for p in parts:
        assert (p["kind"], p["lang"], p["project"], p["path"], p["parent"]) == ("feature", "en", "Alfa", str(root), f["id"])
        assert p["stage"] == "plan" and p["status"] == "working" and "autopilot" not in p and len(p["title"]) <= 120
        assert (root / "features" / p["id"]).is_dir()
        spec = (root / "features" / p["id"] / "spec.md").read_text(encoding="utf-8")
        assert p["title"].split("] ", 1)[1] in spec and f["id"] in spec
    assert "src/a.py" in parts[0]["desc"] and "exports work" in parts[0]["desc"]
    assert len(fake.calls) == launches + 2
    assert parent["status"] == "done" and parent["closed"] and parent["split"]["status"] == "fanned-out"
    assert [t["part_id"] for t in parent["split"]["tasks"]] == [p["id"] for p in parts]
    assert parent["split"]["integration"] == "" and parent["split"]["integration_error"] == ""
    assert any(h["event"] == "fanned out" and "2" in h["note"] for h in parent["history"])
    assert [d for d in arch.docs if d == ("plan", f["id"])] == [("plan", f["id"])] and parent["worker_id"] in released


def test_the_same_second_gives_distinct_ids(env):
    fs, fake, root, _, _ = env
    f = to_plan_approval(fs, fake, root, plan([{"id": "x", "title": "X", "files": ["x.py"]}, {"id": "x2", "title": "Y", "files": ["y.py"]}]))
    fs.approve(f["id"])
    ids = [t["part_id"] for t in stored(fs, f["id"])["split"]["tasks"]]
    assert len(set(ids)) == 2


def test_the_users_approval_fans_out_even_if_autopilot_is_still_set(env):
    fs, fake, root, _, _ = env
    f = to_plan_approval(fs, fake, root, plan([A, B]), autopilot=True)
    assert f["status"] == "awaiting"
    fs.approve(f["id"])
    assert stored(fs, f["id"])["split"]["status"] == "fanned-out"


def test_a_maintenance_parent_gives_its_description_to_the_parts(env):
    fs, fake, root, _, _ = env
    f = to_plan_approval(fs, fake, root, plan([A, B]), kind="maintenance")
    fs.approve(f["id"])
    parts = parts_of(fs, stored(fs, f["id"]))
    assert all("Let me export trips" in p["desc"] and p["kind"] == "maintenance" for p in parts)
    assert not any((root / "features" / p["id"] / "spec.md").exists() for p in parts)


def test_a_part_that_cannot_launch_fails_alone_and_can_be_retried(tmp_path):
    fake, arch = FlakyFake(), Archive()
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True, committer=FakeCommit(), publisher=arch)
    f = to_plan_approval(fs, fake, tmp_path, plan([A, B]))
    fake.fail_on, fake.attempts = {2}, 0                       # of the two part launches, the second raises
    fs.approve(f["id"])
    parent, (p1, p2) = stored(fs, f["id"]), parts_of(fs, stored(fs, f["id"]))
    assert p1["status"] == "working" and p2["status"] == "failed" and parent["status"] == "done"
    assert any(h["event"] == "fan-out launch failed" for h in p2["history"])
    fake.fail_on = set()
    launches = len(fake.calls)
    fs.retry(p2["id"])
    assert len(fake.calls) == launches + 1 and stored(fs, p2["id"])["status"] == "working"
    assert stored(fs, p1["id"])["status"] == "working"


def test_a_long_title_is_cut_never_an_error(env):
    fs, fake, root, _, _ = env
    f = to_plan_approval(fs, fake, root, plan([{"id": "api", "title": "T" * 130, "files": ["a.py"]}, B]))
    fs.approve(f["id"])
    assert all(len(p["title"]) <= 120 for p in parts_of(fs, stored(fs, f["id"])))


def test_a_part_that_fails_validation_stops_everything_before_anything_is_written(env, monkeypatch):
    fs, fake, root, arch, _ = env
    g = to_plan_approval(fs, fake, root, plan([A, B]))
    calls = []

    def second_part_is_bad(project, title, desc, model, lang, kind):
        calls.append(title)
        if len(calls) == 2:
            raise FeatureError("bad part")
        return title, desc, kind
    monkeypatch.setattr(fs, "_validate_request_fields", second_part_is_bad)
    launches = len(fake.calls)
    with pytest.raises(FeatureError, match="bad part"):
        fs.approve(g["id"])
    after = stored(fs, g["id"])
    assert after["status"] == "awaiting" and "split" not in after and len(fake.calls) == launches
    assert not any(h["event"] == "approved" and h["stage"] == "plan" for h in after["history"])
    assert ("plan", g["id"]) not in arch.docs and len(fs.list()) == 1


def test_the_portuguese_request_gets_portuguese_parts(env):
    fs, fake, root, _, _ = env
    f = to_plan_approval(fs, fake, root, plan([A, B]), lang="pt")
    fs.approve(f["id"])
    assert all(p["lang"] == "pt" for p in parts_of(fs, stored(fs, f["id"])))


def test_build_parts_is_pure_and_validates_every_part(env):
    from datetime import datetime
    split = {"tasks": [{"id": "a", "title": "A", "files": [{"path": "a.py", "new": False}], "depends_on": []},
                       {"id": "b", "title": "B", "files": [{"path": "b.py", "new": False}], "depends_on": []}], "errors": []}
    parent = {"id": "par-1", "title": "Parent", "desc": "D", "project": "Alfa", "model": "", "lang": "en", "kind": "feature", "path": "/p"}
    seen = []
    out = fanout.build_parts(parent, split, SPEC, {"x"}, datetime(2026, 1, 1), lambda *a: seen.append(a) or (a[1], a[2], a[5]))
    assert [o["extra"] for o in out] == [{"part": "a"}, {"part": "b"}] and len(seen) == 2
    assert out[0]["fid"] != out[1]["fid"] and out[0]["stage"] == "plan" and out[0]["parent"] == "par-1" and out[0]["spec"]
    boom = lambda *a: (_ for _ in ()).throw(FeatureError("bad"))             # noqa: E731
    with pytest.raises(FeatureError):
        fanout.build_parts(parent, split, SPEC, set(), datetime(2026, 1, 1), boom)
