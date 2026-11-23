"""tools_for_step keeps the `phase_tools` override by `<kind>:<stage>`; steps may carry a bounded `notes` text without secrets;
a pipeline file may not be a symbolic link."""
import copy
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chief  # noqa: E402
import engine  # noqa: E402
import pipelines  # noqa: E402
from pipelines import PipelineError  # noqa: E402
from test_pipelines_schema import good  # noqa: E402


def step_of(kind, phase, **extra):
    return {"id": phase, "phase": phase, "type": "worker", **extra}


def test_user_phase_tools_override_wins_for_that_kind_only(monkeypatch):
    monkeypatch.setitem(chief.CFG, "phase_tools", {"feature:build": {"allow": ["Bash(make:*)"], "deny": ["Bash(rm:*)"]}})
    st = step_of("feature", "build", permissions={"allow": ["Bash(own:*)"]})
    allow, deny = chief.tools_for_step(st, "feature")
    assert allow == ["Bash(make:*)"] and "Bash(rm:*)" in deny
    allow_other, _ = chief.tools_for_step(st, "fix")      # another kind: the override does not apply
    assert allow_other == ["Bash(own:*)"]
    assert all(d in deny for d in engine.FIXED_DENY)


def test_builtin_default_for_kind_stage_is_used_and_needs_the_kind(monkeypatch):
    monkeypatch.setitem(chief.CFG, "phase_tools", {})
    st = step_of("ship", "build")
    with_kind, _ = chief.tools_for_step(st, "ship")
    without_kind, _ = chief.tools_for_step(st, "")
    assert with_kind == chief.tools_for("ship", "build")[0] and with_kind
    assert without_kind == []


def test_empty_override_switches_the_default_off(monkeypatch):
    monkeypatch.setitem(chief.CFG, "phase_tools", {"ship:build": {}})
    allow, deny = chief.tools_for_step(step_of("ship", "build"), "ship")
    assert allow == [] and all(d in deny for d in engine.FIXED_DENY)


def with_notes(value, index=0):
    d = copy.deepcopy(good())
    d["steps"][index]["notes"] = value
    return d


@pytest.mark.parametrize("index", [0, 1, 3])   # worker, approval, check
def test_notes_are_accepted_on_every_step_type(index):
    assert pipelines.load(with_notes("why this step exists", index)).steps[index]["notes"] == "why this step exists"


def test_notes_must_be_short_text():
    for bad in ("x" * (pipelines.MAX_NOTES + 1), 5, ["a"]):
        with pytest.raises(PipelineError, match="notes"):
            pipelines.load(with_notes(bad))


def test_notes_may_not_hold_a_secret():
    with pytest.raises(PipelineError, match="secret"):
        pipelines.load(with_notes("password: hunter2hunter2"))


def test_a_symlinked_pipeline_file_is_refused(tmp_path):
    import json
    real = tmp_path / "real.json"
    real.write_text(json.dumps(good()), encoding="utf-8")
    link = tmp_path / "demo.json"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available here")
    with pytest.raises(PipelineError, match="symbolic link"):
        pipelines.load(link)
    assert pipelines.load(real).id == "demo"
