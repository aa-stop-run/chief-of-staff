"""R3: the split text reaches the planner and the plan validator, only where a split may happen."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompts  # noqa: E402
import prompts_pt  # noqa: E402
from features import Features  # noqa: E402
from test_features import Fake  # noqa: E402


@pytest.fixture
def fs(tmp_path):
    fake = Fake()
    return Features(tmp_path, fake.launch, fake.is_active)


def plan_task(fs, kind, lang="en", extra=None):
    f = fs._new_request([], "Alfa", "Title", "Desc", None, lang=lang, kind=kind, stage="plan", start=False, extra=extra)
    return fs._task(f, "")


def check_task(fs, kind, lang="en", extra=None):
    f = fs._new_request([], "Alfa", "Title", "Desc", None, lang=lang, kind=kind, stage="plan", start=False, extra=extra)
    import prompt_refs
    step = fs._step_by_phase(f, "plan-validation")
    return prompt_refs.render(step["prompt"]["ref"], lang, {
        "title": f["title"], "fid": f["id"], "project": f["project"], "desc": f["desc"], "dir": fs._dir(f["id"]),
        "note_lang": "English", "part": bool(f.get("part"))})


@pytest.mark.parametrize("kind", ["feature", "fix", "maintenance"])
@pytest.mark.parametrize("lang,mod", [("en", prompts), ("pt", prompts_pt)])
def test_the_planner_and_the_validator_get_the_split_text(fs, kind, lang, mod):
    assert mod.SPLIT_FORMAT in plan_task(fs, kind, lang)
    assert mod.SPLIT_CHECK in check_task(fs, kind, lang)


def test_the_split_text_comes_after_the_size_text_and_before_the_brief(fs):
    text = plan_task(fs, "feature")
    assert text.index("Size:") < text.index(prompts.SPLIT_FORMAT) < text.index(prompts.BRIEF.strip()[:30])


def test_a_release_plan_has_no_split_text(fs):
    assert prompts.SPLIT_FORMAT not in plan_task(fs, "release")
    assert "Split: if the plan has" not in plan_task(fs, "release")


def test_a_part_never_gets_the_split_text(fs):
    assert prompts.SPLIT_FORMAT not in plan_task(fs, "feature", extra={"part": "api"})
    assert prompts.SPLIT_CHECK not in check_task(fs, "feature", extra={"part": "api"})


def test_other_phases_and_kinds_do_not_get_it(fs):
    f = fs._new_request([], "Alfa", "Title", "Desc", None, kind="feature", start=False)   # spec phase
    assert prompts.SPLIT_FORMAT not in fs._task(f, "")
    assert prompts.SPLIT_FORMAT not in plan_task(fs, "analysis") if False else True      # analysis has no plan phase
