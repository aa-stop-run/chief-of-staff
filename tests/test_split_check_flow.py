"""R4: the server's structural findings are merged into the plan validation before the engine decides."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import plan_fix_rounds  # noqa: E402
from features import Features, parse_review  # noqa: E402
from test_auto_review import REVIEW_OK, Fake, FakeCommit, finish_stage  # noqa: E402


def plan(tasks, heading="## Split"):
    return f"# Plan\nTASKS: 4\n\n{heading}\n\n```json\n{json.dumps({'tasks': tasks})}\n```\n"


A = {"id": "a", "title": "A", "files": ["a.py"]}
B_OK = {"id": "b", "title": "B", "files": ["b.py"]}
B_CLASH = {"id": "b", "title": "B", "files": ["a.py"]}


@pytest.fixture
def env(tmp_path):
    fake = Fake()
    return Features(tmp_path, fake.launch, fake.is_active, auto_review=True, committer=FakeCommit()), fake, tmp_path


def at_plan_check(fs, fake, root, plan_text, kind="feature", autopilot=False):
    f = fs.create("Alfa", "Title", "Desc", None, kind=kind, autopilot=autopilot)
    if kind == "feature":
        finish_stage(fs, fake, root, f["id"], "spec.md")
        fs.approve(f["id"])
    return f, finish_stage(fs, fake, root, f["id"], "plan.md", plan_text)


def review_file(root, f):
    return (root / "features" / f["id"] / "plan-review.md").read_text(encoding="utf-8")


def test_an_overlapping_split_blocks_an_approving_validator_and_takes_the_fix_round(env):
    fs, fake, root = env
    f, _ = at_plan_check(fs, fake, root, plan([A, B_CLASH]))
    g = finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)
    text = review_file(root, f)
    assert "overlap" in text and parse_review(text)[0] == "blocked" and parse_review(text)[1]["HIGH"] >= 1
    assert plan_fix_rounds(g) == 1 and g["status"] == "working" and g["stage"] == "plan"
    assert "plan-review.md" in fake.calls[-1]["task"]          # the planner got the validation as feedback


def test_after_the_fix_rounds_it_waits_for_the_user_even_under_autopilot(env):
    fs, fake, root = env
    f, _ = at_plan_check(fs, fake, root, plan([A, B_CLASH]), autopilot=True)
    finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)
    finish_stage(fs, fake, root, f["id"], "plan.md", plan([A, B_CLASH]))            # the planner repeats the mistake
    g = finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)
    assert g["status"] == "awaiting" and g["stage"] == "plan"
    assert any(h["event"] == "plan fixes limit" for h in g["history"])
    assert not any(h["event"] == "approved automatically" for h in g["history"])


def test_a_clean_split_leaves_the_validators_review_untouched(env):
    fs, fake, root = env
    f, _ = at_plan_check(fs, fake, root, plan([A, B_OK]))
    g = finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)
    assert review_file(root, f) == REVIEW_OK and plan_fix_rounds(g) == 0 and g["status"] == "awaiting"


def test_a_plan_without_a_split_is_untouched(env):
    fs, fake, root = env
    f, _ = at_plan_check(fs, fake, root, "# Plan\nTASKS: 2\nsteps\n")
    g = finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)
    assert review_file(root, f) == REVIEW_OK and g["status"] == "awaiting"


def test_a_request_that_is_not_allowed_to_split_is_untouched(env):
    fs, fake, root = env
    f, _ = at_plan_check(fs, fake, root, plan([A, B_CLASH]), kind="maintenance")
    finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)
    assert "overlap" in review_file(root, f)                     # maintenance may split ...
    f2 = fs.create("Alfa", "Part", "Desc", None, kind="feature")
    data = fs._load()
    next(x for x in data if x["id"] == f2["id"])["part"] = "a"    # ... but a part may not
    fs._save(data)
    finish_stage(fs, fake, root, f2["id"], "spec.md")
    fs.approve(f2["id"])
    finish_stage(fs, fake, root, f2["id"], "plan.md", plan([A, B_CLASH]))
    finish_stage(fs, fake, root, f2["id"], "plan-review.md", REVIEW_OK)
    assert review_file(root, f2) == REVIEW_OK


def test_a_missing_review_file_is_not_created(env):
    fs, fake, root = env
    f, g = at_plan_check(fs, fake, root, plan([A, B_CLASH]))
    fake.active.discard(f"w{len(fake.calls)}")                  # the validator stops without writing anything
    fs.list()
    assert not (root / "features" / f["id"] / "plan-review.md").exists()
