"""A background worker cannot answer a permission prompt: the prompts must never send it through EnterWorktree."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import prompts  # noqa: E402
import prompts_pt  # noqa: E402


@pytest.mark.parametrize("mod", [prompts, prompts_pt])
def test_the_resume_prompt_works_by_path_and_forbids_enterworktree(mod):
    text = mod.BUILD_RESUME
    assert "EnterWorktree" in text and 'cd "{work}"' in text
    assert "enter it" not in text and "entra nele" not in text


def test_trying_again_keeps_the_changes_that_were_asked_for(tmp_path):
    from features import Features
    from test_auto_review import Fake
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    f = fs.create("Alfa", "Why", "x", None)
    fake.finish(tmp_path, f["id"], "spec.md", "# Spec")
    fs.list()
    fs.revise(f["id"], "Fix the naming in A1")
    fake.active.clear()                                   # the worker died before writing anything
    assert next(x for x in fs.list() if x["id"] == f["id"])["status"] == "failed"
    fs.retry(f["id"])
    assert "Fix the naming in A1" in fake.calls[-1]["task"]


def test_trying_again_without_a_request_for_changes_adds_nothing(tmp_path):
    from features import Features
    from test_auto_review import Fake
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active)
    f = fs.create("Alfa", "Why", "x", None)
    fake.active.clear()
    fs.list()
    fs.retry(f["id"])
    assert "Fix the naming" not in fake.calls[-1]["task"] and Features._pending_feedback(fs.list()[0]) == ""
