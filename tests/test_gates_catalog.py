"""The gate catalogue: the moved gates behave as before, the two new ones do what the spec says."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import features  # noqa: E402
import gates  # noqa: E402
from gates import GateCtx  # noqa: E402

NAMES = ["parse_review", "parse_tests", "ship_gate", "release_plan_gate", "release_verdict", "release_gate", "review_tests_gate"]


@pytest.mark.parametrize("name", NAMES)
def test_features_reexports_gates(name):
    assert getattr(features, name) is getattr(gates, name)


def test_ship_ready_cases(tmp_path):
    good = "## Ready to publish\n`git push -u origin feat/x`\n"
    (tmp_path / "report.md").write_text(good, encoding="utf-8")
    assert gates.run("ship_ready", {}, GateCtx(tmp_path)).code == "ok"
    (tmp_path / "report.md").write_text("## Ready to publish\n`git push -u origin main`\n", encoding="utf-8")
    assert gates.run("ship_ready", {}, GateCtx(tmp_path)).code == "badpush"
    (tmp_path / "report.md").write_text("nothing", encoding="utf-8")
    assert gates.run("ship_ready", {}, GateCtx(tmp_path)).code == "missing"


def test_release_cases(tmp_path):
    plan = "\n".join(f"## {s}\nx" for s in gates.RELEASE_SECTIONS) + "\nCandidate commit: abcdef1\n"
    (tmp_path / "plan.md").write_text(plan, encoding="utf-8")
    assert gates.run("release_plan", {}, GateCtx(tmp_path)).code == "ok"
    (tmp_path / "plan.md").write_text("## Candidate\n", encoding="utf-8")
    assert gates.run("release_plan", {}, GateCtx(tmp_path)).code == "missing"
    (tmp_path / "plan.md").write_text(plan, encoding="utf-8")
    (tmp_path / "report.md").write_text("## Regression verdict\nPOSITIVE\nCandidate commit: abcdef1\n", encoding="utf-8")
    assert gates.run("release_verdict", {}, GateCtx(tmp_path)).code == "ok"
    (tmp_path / "report.md").write_text("## Regression verdict\nNEGATIVE\n", encoding="utf-8")
    assert gates.run("release_verdict", {}, GateCtx(tmp_path)).code == "negative"


def test_review_tests_cases(tmp_path):
    (tmp_path / "report.md").write_text("## Tests\nNO REGRESSIONS\nFINAL RESULT: 10 passed, 0 failed\n", encoding="utf-8")
    (tmp_path / "review.md").write_text("## Tests\nCONFIRMED\nREVIEWER RESULT: 10 passed, 0 failed\n", encoding="utf-8")
    assert gates.run("review_tests", {}, GateCtx(tmp_path, True)).code == "ok"
    (tmp_path / "review.md").write_text("## Tests\nCONFIRMED\nREVIEWER RESULT: 9 passed, 1 failed\n", encoding="utf-8")
    assert gates.run("review_tests", {}, GateCtx(tmp_path, True)).code == "diverged"
    assert gates.run("review_tests", {}, GateCtx(tmp_path, False)).code == "ok"   # no worktree, nothing to confirm


def test_required_sections_cases(tmp_path):
    a = {"file": "x.md", "sections": ["Alpha", "Beta"]}
    assert gates.run("required_sections", a, GateCtx(tmp_path)).code == "blocked"        # file absent
    (tmp_path / "x.md").write_text("## Alpha\n1\n## Beta\n2\n", encoding="utf-8")
    assert gates.run("required_sections", a, GateCtx(tmp_path)).code == "ok"
    (tmp_path / "x.md").write_text("## Alpha\n1\n", encoding="utf-8")
    r = gates.run("required_sections", a, GateCtx(tmp_path))
    assert r.code == "blocked" and "Beta" in r.message and "Alpha" not in r.message


def test_no_critical_findings_cases(tmp_path):
    a = {"file": "review.md"}
    (tmp_path / "review.md").write_text("## Verdict\nAPPROVED\n\n## Findings\n- **[LOW] x:** y\n", encoding="utf-8")
    assert gates.run("no_critical_findings", a, GateCtx(tmp_path)).code == "ok"
    (tmp_path / "review.md").write_text("## Verdict\nBLOCKED\n\n## Findings\n- **[CRITICAL] x:** y\n", encoding="utf-8")
    assert gates.run("no_critical_findings", a, GateCtx(tmp_path)).code == "blocked"
    (tmp_path / "review.md").write_text("garbage", encoding="utf-8")
    assert gates.run("no_critical_findings", a, GateCtx(tmp_path)).code == "unknown"


def test_unknown_gate_is_refused(tmp_path):
    with pytest.raises(KeyError):
        gates.run("rm_rf", {}, GateCtx(tmp_path))
