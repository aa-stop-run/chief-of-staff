"""The «release» kind: plan -> independent regression (a verdict the server enforces) -> promotion carried out by a worker."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompts_kinds  # noqa: E402
from features import FeatureError, Features, release_gate, release_plan_gate, release_verdict  # noqa: E402
from test_auto_review import Fake, finish_stage  # noqa: E402

PLAN = """## Candidate
Candidate commit: abc1234def5678
Branch feat/x -> main

## Regression tests
`pytest -q` (expected: all pass)

## Promotion steps
```
git push origin main
```

## Post-checks
`curl -I https://example.test` (200)

## Rollback
`git revert abc1234def5678`
"""


def report(verdict="POSITIVE", sha="abc1234def5678"):
    return f"## Regression verdict\n{verdict}\n\nCandidate commit: {sha}\n\n| cmd | result |\n|---|---|\n| pytest -q | 120 passed |\n"


@pytest.fixture
def env(tmp_path):
    fake, published = Fake(), []
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True,
                  publisher=lambda *a, **k: published.append(a) or "archived")
    return fs, fake, tmp_path, published


def make(fs, lang="en"):
    return fs.create("Alfa", "Release 2.4", "Promote PR #5 and #6 to production.", None, lang=lang, kind="release")


def to_regression(fs, fake, root, f):
    finish_stage(fs, fake, root, f["id"], "plan.md", PLAN)
    return fs.approve(f["id"])


def test_it_has_three_phases_and_starts_with_a_read_only_plan(env):
    fs, fake, _, _ = env
    f = make(fs)
    assert prompts_kinds.KIND_STAGES["release"] == ("plan", "build", "review") and f["stage"] == "plan" and f["status"] == "working"
    t = " ".join(fake.calls[-1]["task"].split())
    assert "READ-ONLY" in t and "Do NOT run any promotion step" in t and "Promotion steps" in t and "Rollback" in t and "Candidate commit" in t


def test_the_plan_waits_for_the_user_with_no_plan_validation_and_nothing_archived(env):
    fs, fake, root, published = env
    f = make(fs)
    calls = len(fake.calls)
    got = finish_stage(fs, fake, root, f["id"], "plan.md", PLAN)
    assert got["status"] == "awaiting" and len(fake.calls) == calls and got.get("pv") is None
    fs.approve(f["id"])
    assert published == []                                              # the plan is not copied into the user's repo


@pytest.mark.parametrize("plan,msg", [("## Candidate\nCandidate commit: abc1234\n", "lacks a required section"),
                                      (PLAN.replace("Candidate commit:", "Commit:"), "Candidate commit")])
def test_an_incomplete_plan_cannot_be_approved(env, plan, msg):
    fs, fake, root, _ = env
    f = make(fs)
    finish_stage(fs, fake, root, f["id"], "plan.md", plan)
    with pytest.raises(FeatureError, match=msg):
        fs.approve(f["id"])


def test_the_regression_worker_is_independent_local_and_works_in_a_throwaway_worktree(env):
    fs, fake, root, _ = env
    f = make(fs)
    to_regression(fs, fake, root, f)
    t = " ".join(fake.calls[-1]["task"].split())
    assert "independent verifier" in t and "did NOT write the plan" in t and "git worktree add --detach" in t
    assert str(root / "features" / f["id"] / "work") in t and "When in doubt, NEGATIVE" in t and "Do not touch production" in t


@pytest.mark.parametrize("verdict,msg", [("NEGATIVE", "NEGATIVE"), ("maybe", "readable"), ("POSITIVE or NEGATIVE", "readable")])
def test_only_a_positive_verdict_lets_the_promotion_be_approved(env, verdict, msg):
    fs, fake, root, _ = env
    f = make(fs)
    to_regression(fs, fake, root, f)
    finish_stage(fs, fake, root, f["id"], "report.md", report(verdict))
    calls = len(fake.calls)
    with pytest.raises(FeatureError, match=msg):
        fs.approve(f["id"])
    assert len(fake.calls) == calls and next(x for x in fs.list() if x["id"] == f["id"])["stage"] == "build"


def test_a_different_tested_commit_blocks_the_promotion(env):
    fs, fake, root, _ = env
    f = make(fs)
    to_regression(fs, fake, root, f)
    finish_stage(fs, fake, root, f["id"], "report.md", report(sha="ffff9999"))
    with pytest.raises(FeatureError, match="not the planned candidate"):
        fs.approve(f["id"])


def test_a_negative_run_can_be_repeated_and_then_approved(env):
    fs, fake, root, _ = env
    f = make(fs)
    to_regression(fs, fake, root, f)
    finish_stage(fs, fake, root, f["id"], "report.md", report("NEGATIVE"))
    again = fs.revise(f["id"], "Run the regression tests again from scratch and report a new verdict.")
    assert again["stage"] == "build" and again["status"] == "working"
    finish_stage(fs, fake, root, f["id"], "report.md", report("POSITIVE"))
    out = fs.approve(f["id"])
    assert out["stage"] == "review" and out["status"] == "working"


def test_the_promotion_runs_only_the_plans_steps_and_never_rolls_back_by_itself(env):
    fs, fake, root, _ = env
    f = make(fs)
    to_regression(fs, fake, root, f)
    finish_stage(fs, fake, root, f["id"], "report.md", report())
    fs.approve(f["id"])
    t = " ".join(fake.calls[-1]["task"].split())
    assert "EXACTLY the commands" in t and "Do not run the rollback by yourself" in t and "Never --force" in t
    assert str(root / "features" / f["id"] / "plan.md") in t and str(root / "features" / f["id"] / "report.md") in t
    assert "run NOTHING" in t                                           # a diverging commit stops it before any step
    finish_stage(fs, fake, root, f["id"], "review.md", "## Promotion\nall steps passed")
    assert fs.approve(f["id"])["status"] == "done"


def test_cancelling_before_the_approval_runs_no_promotion(env):
    fs, fake, root, _ = env
    f = make(fs)
    to_regression(fs, fake, root, f)
    finish_stage(fs, fake, root, f["id"], "report.md", report())
    calls = len(fake.calls)
    assert fs.cancel(f["id"])["status"] == "cancelled" and len(fake.calls) == calls


def test_portuguese_prompts_keep_the_same_guards(env):
    fs, fake, root, _ = env
    f = make(fs, "pt")
    to_regression(fs, fake, root, f)
    assert "NÃO escreveste o plano" in " ".join(fake.calls[-1]["task"].split())
    finish_stage(fs, fake, root, f["id"], "report.md", report("POSITIVO"))
    fs.approve(f["id"])
    t = " ".join(fake.calls[-1]["task"].split())
    assert "EXATAMENTE os comandos" in t and "rollback por tua conta" in t


def test_the_gates_are_pure_and_strict():
    assert release_plan_gate(PLAN) == "ok" and release_plan_gate("") == "missing"
    assert release_verdict(report("POSITIVO")) == "positive" and release_verdict(report("NEGATIVO")) == "negative"
    assert release_verdict("## Regression verdict\n\nNEGATIVE\n") == "negative" and release_verdict("POSITIVE") == "unknown"
    assert release_gate(PLAN, report()) == "ok" and release_gate(PLAN, report(sha="abc1234")) == "ok"      # a short sha of the same commit matches
    assert release_gate(PLAN, report("NEGATIVE")) == "negative" and release_gate(PLAN, "## Regression verdict\nPOSITIVE\n") == "mismatch"


def test_other_kinds_are_not_affected_by_the_release_gates(env):
    fs, fake, root, _ = env
    f = fs.create("Alfa", "Login", "x", None, kind="feature")
    finish_stage(fs, fake, root, f["id"], "spec.md")
    assert fs.approve(f["id"])["stage"] == "plan"
