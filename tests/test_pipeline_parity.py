"""Golden traces of the request pipelines (plan T0). They were captured from the code before the engine was wired in, so any change
that alters what is launched (task, model, effort, permissions, directories), the stage/status/pv after each step, the history, the
commits or the archived documents makes a trace differ. Regenerate on purpose with `python tests/tools/capture_golden.py`."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import capture_golden as cg  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

CASES = [(k, s) for k, scs in SCENARIOS.items() for s in scs]
KINDS = {"feature", "fix", "maintenance", "analysis", "ship", "release"}


def test_every_kind_has_scenarios():
    assert set(SCENARIOS) == KINDS and all(len(v) >= 5 for v in SCENARIOS.values())


def test_golden_files_present():
    missing = [f"{k}-{s}" for k, s in CASES if not cg.golden_path(k, s).is_file()]
    assert not missing, missing
    extra = {p.stem for p in cg.GOLDEN.glob("*.json")} - {f"{k}-{s}" for k, s in CASES}
    assert not extra, f"golden files without a scenario: {sorted(extra)}"


def test_normalize_stable():
    bs = chr(92)
    home = bs.join(["C:", "x", "tmp"])
    obj = {"b": home + bs + "features" + bs + "fid1" + bs + "a.md 2026-11-13T10:11:12Z", "a": [1]}
    once = cg.normalize(obj, [(home, "<home>"), ("fid1", "<fid>")])
    assert once == {"a": [1], "b": "<home>/features/<fid>/a.md <date>"}
    assert cg.normalize(once) == once


@pytest.mark.parametrize("kind,scenario", CASES, ids=[f"{k}-{s}" for k, s in CASES])
def test_trace_matches_golden(kind, scenario):
    expected = json.loads(cg.golden_path(kind, scenario).read_text(encoding="utf-8"))
    assert cg.capture(kind, scenario) == expected


@pytest.mark.parametrize("kind,scenario", [("feature", "review-blocked-1"), ("release", "negative")])
def test_capture_is_deterministic(kind, scenario):
    assert cg.capture(kind, scenario) == cg.capture(kind, scenario)


def test_golden_traces_record_model_effort_and_permissions():
    t = cg.capture("ship", "approve-all")["trace"]
    launches = [x for step in t for x in step["launches"]]
    assert launches and all(x["model"].startswith("m-") and x["effort"] == "high" for x in launches)
    assert "Bash(git push -u:*)" in launches[-1]["allow"] and "Bash(gh pr merge:*)" in launches[-1]["deny"]
