"""Automatic review: the implementation is only committed after an independent review that passes."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from features import MAX_AUTO_FIXES, MAX_PLAN_FIXES, MAX_REVIEW_RETRIES, Features, parse_review  # noqa: E402

TESTS_OK = "\n## Tests\nCONFIRMED\nREVIEWER RESULT: 10 passed, 0 failed\n"
REVIEW_OK = "## Verdict\nAPPROVED — all good.\n\n## Findings\nNone.\n" + TESTS_OK
REVIEW_RESERVES = "## Verdict\nWITH RESERVATIONS — style only.\n\n## Findings\n- **[LOW] a.py:1 — name:** rename\n" + TESTS_OK
REVIEW_BLOCKED = "## Verdict\nBLOCKED — input validation is missing.\n\n## Findings\n- **[HIGH] app.py:9 — no validation:** validate\n"
REVIEW_UNKNOWN = "# Review\nI do not know\n"
# the same verdicts as written by a worker that was asked for Portuguese: the parsers accept both languages
REVIEW_OK_PT = "## Veredicto\nAPROVADO — tudo certo.\n\n## Achados\nNenhum.\n"
REVIEW_RESERVES_PT = "## Veredicto\nCOM RESERVAS — só estilo.\n\n## Achados\n- **[LOW] a.py:1 — nome:** renomear\n"
REVIEW_BLOCKED_PT = "## Veredicto\nBLOQUEADO — falta validar o input.\n\n## Achados\n- **[HIGH] app.py:9 — sem validação:** validar\n"


class Fake:
    def __init__(self):
        self.calls, self.active = [], set()

    def launch(self, project, task, model, extra_dirs, label=None):
        wid = f"w{len(self.calls) + 1}"
        self.calls.append({"project": project, "task": task, "id": wid})
        self.active.add(wid)
        return {"id": wid, "name": f"cos-{wid}"}

    def is_active(self, wid):
        return wid in self.active

    def finish(self, root, fid, artifact, text):
        self.active.discard(f"w{len(self.calls)}")
        d = Path(root) / "features" / fid
        d.mkdir(parents=True, exist_ok=True)
        (d / artifact).write_text(text, encoding="utf-8")


class FakeCommit:
    def __init__(self, result=(True, "abc1234")):
        self.calls, self.result = [], result

    def __call__(self, path, message):
        self.calls.append((str(path), message))
        return self.result


@pytest.fixture
def auto(tmp_path):
    fake, commit = Fake(), FakeCommit()
    return Features(tmp_path, fake.launch, fake.is_active, auto_review=True, committer=commit), fake, tmp_path, commit


def finish_stage(fs, fake, root, fid, art, text="# ok"):
    fake.finish(root, fid, art, text)
    return next(x for x in fs.list() if x["id"] == fid)


def feature_in_build(fs, fake, root, work=True):
    f = fs.create("Alfa", "Login with 2FA", "I want 2FA", None)
    finish_stage(fs, fake, root, f["id"], "spec.md")
    fs.approve(f["id"])
    finish_stage(fs, fake, root, f["id"], "plan.md")
    finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)      # the plan validation runs by itself
    fs.approve(f["id"])
    if work:
        (root / "features" / f["id"] / "work").mkdir(parents=True)
    return f


@pytest.mark.parametrize("text,verdict", [(REVIEW_OK, "approved"), (REVIEW_RESERVES, "reserves"),
                                           (REVIEW_BLOCKED, "blocked"), (REVIEW_UNKNOWN, "unknown"), ("", "unknown"),
                                           (REVIEW_OK_PT, "approved"), (REVIEW_RESERVES_PT, "reserves"),
                                           (REVIEW_BLOCKED_PT, "blocked")])
def test_parse_review_verdict(text, verdict):
    assert parse_review(text)[0] == verdict


def test_parse_review_counts_findings():
    assert parse_review(REVIEW_BLOCKED)[1] == {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 0, "LOW": 0}


def test_review_starts_by_itself_when_the_implementation_is_ready(auto):
    fs, fake, root, _ = auto
    f = feature_in_build(fs, fake, root)
    g = finish_stage(fs, fake, root, f["id"], "report.md")
    assert (g["stage"], g["status"]) == ("review", "working")
    assert "independent reviewer" in fake.calls[-1]["task"]
    assert any(h["event"] == "automatic review" for h in g["history"])


def test_approved_review_commits_the_worktree_only_after_the_review(auto):
    fs, fake, root, commit = auto
    f = feature_in_build(fs, fake, root)
    finish_stage(fs, fake, root, f["id"], "report.md")
    assert commit.calls == []                                  # nothing committed before the review
    g = finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_OK)
    assert g["status"] == "awaiting" and len(commit.calls) == 1
    path, message = commit.calls[0]
    assert path.endswith("work") and f["title"] in message and f["id"] in message
    assert any(h["event"] == "committed" and "abc1234" in h["note"] for h in g["history"])


def test_review_with_only_minor_reservations_also_commits(auto):
    fs, fake, root, commit = auto
    f = feature_in_build(fs, fake, root)
    finish_stage(fs, fake, root, f["id"], "report.md")
    finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_RESERVES)
    assert len(commit.calls) == 1


def test_blocked_review_sends_the_findings_back_automatically_and_does_not_commit(auto):
    fs, fake, root, commit = auto
    f = feature_in_build(fs, fake, root)
    finish_stage(fs, fake, root, f["id"], "report.md")
    g = finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_BLOCKED)
    assert (g["stage"], g["status"]) == ("build", "working") and commit.calls == []
    task = " ".join(fake.calls[-1]["task"].split())
    assert "review.md" in task and "Do NOT create it again" in task
    assert any(h["event"] == "automatic fixes" for h in g["history"])


def test_after_the_fixes_the_review_runs_again_and_then_commits(auto):
    fs, fake, root, commit = auto
    f = feature_in_build(fs, fake, root)
    finish_stage(fs, fake, root, f["id"], "report.md")
    finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_BLOCKED)
    g = finish_stage(fs, fake, root, f["id"], "report.md", "# fixed")
    assert (g["stage"], g["status"]) == ("review", "working")
    g = finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_OK)
    assert g["status"] == "awaiting" and len(commit.calls) == 1


def test_automatic_fixes_have_a_limit_then_the_user_decides(auto):
    fs, fake, root, commit = auto
    f = feature_in_build(fs, fake, root)
    for _ in range(MAX_AUTO_FIXES):
        finish_stage(fs, fake, root, f["id"], "report.md")
        finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_BLOCKED)
    finish_stage(fs, fake, root, f["id"], "report.md")
    g = finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_BLOCKED)
    assert (g["stage"], g["status"]) == ("review", "awaiting") and commit.calls == []
    assert any(h["event"] == "automatic fixes limit" for h in g["history"])


def test_review_without_a_recognisable_verdict_is_repeated_then_left_to_the_user(auto):
    fs, fake, root, commit = auto
    f = feature_in_build(fs, fake, root)
    finish_stage(fs, fake, root, f["id"], "report.md")
    for _ in range(MAX_REVIEW_RETRIES):
        g = finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_UNKNOWN)
        assert (g["stage"], g["status"]) == ("review", "working")
    g = finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_UNKNOWN)
    assert g["status"] == "awaiting" and commit.calls == []
    assert any(h["event"] == "review without verdict" for h in g["history"])


def test_if_the_automatic_review_cannot_start_the_user_can_still_send_it(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True, committer=FakeCommit())
    f = feature_in_build(fs, fake, tmp_path)
    calls_before = len(fake.calls)
    real = fs._launch

    def boom(*a):
        raise RuntimeError("claude did not start")

    fs._launch = boom
    g = finish_stage(fs, fake, tmp_path, f["id"], "report.md")
    assert (g["stage"], g["status"]) == ("build", "awaiting") and len(fake.calls) == calls_before
    assert any(h["event"] == "automatic review failed" for h in g["history"])
    fs._launch = real
    assert fs.approve(f["id"])["stage"] == "review"           # the manual button keeps working


def test_commit_failure_is_reported_and_leaves_the_decision_to_the_user(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True,
                  committer=FakeCommit(result=(False, "pre-commit hook failed")))
    f = feature_in_build(fs, fake, tmp_path)
    finish_stage(fs, fake, tmp_path, f["id"], "report.md")
    g = finish_stage(fs, fake, tmp_path, f["id"], "review.md", REVIEW_OK)
    assert g["status"] == "awaiting"
    assert any(h["event"] == "commit failed" and "pre-commit" in h["note"] for h in g["history"])


def test_no_worktree_means_nothing_to_commit(auto):
    fs, fake, root, commit = auto
    f = feature_in_build(fs, fake, root, work=False)            # project without git / operational work
    finish_stage(fs, fake, root, f["id"], "report.md")
    g = finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_OK)
    assert g["status"] == "awaiting" and commit.calls == []


def test_build_worker_must_not_commit_nor_run_irreversible_actions(auto):
    fs, fake, root, _ = auto
    f = feature_in_build(fs, fake, root, work=False)
    task = " ".join(fake.calls[-1]["task"].split())      # the build worker
    assert "Do NOT commit" in task and "irreversible actions" in task


def test_resumed_build_also_forbids_commits(auto):
    fs, fake, root, _ = auto
    f = feature_in_build(fs, fake, root)
    fs._auto_review = False                 # the report waits for a decision, without automatic review
    finish_stage(fs, fake, root, f["id"], "report.md")
    fs.revise(f["id"], "fix it")
    assert "do NOT commit" in " ".join(fake.calls[-1]["task"].split())


def test_reviewer_reviews_the_uncommitted_work(auto):
    fs, fake, root, _ = auto
    f = feature_in_build(fs, fake, root)
    finish_stage(fs, fake, root, f["id"], "report.md")
    task = " ".join(fake.calls[-1]["task"].split())
    assert "uncommitted" in task and "git -C" in task and "diff HEAD" in task


# --- independent plan validation -----------------------------------------------------------------------------------

PLAN_OK = "## Verdict\nAPPROVED — covers the spec.\n\n## Findings\nNone.\n"
PLAN_BAD = "## Verdict\nBLOCKED — a criterion is missing.\n\n## Findings\n- **[HIGH] Steps — criterion 2 has no step:** add one\n"


def feature_with_plan(fs, fake, root):
    f = fs.create("Alfa", "Login with 2FA", "I want 2FA", None)
    finish_stage(fs, fake, root, f["id"], "spec.md")
    fs.approve(f["id"])
    return finish_stage(fs, fake, root, f["id"], "plan.md")          # the planner has finished


def test_plan_is_validated_by_a_fresh_read_only_worker_before_reaching_the_user(auto):
    fs, fake, root, _ = auto
    f = feature_with_plan(fs, fake, root)
    assert f["status"] == "working" and f["pv"]["checking"]          # the user cannot approve yet
    task = fake.calls[-1]["task"]
    assert "independent plan validator" in task and "READ-ONLY" in task and "plan-review.md" in task
    assert "Open questions" in task and "secret" in task
    assert fake.calls[-1]["id"] != fake.calls[-2]["id"]
    f = finish_stage(fs, fake, root, f["id"], "plan-review.md", PLAN_OK)
    assert f["status"] == "awaiting" and f["stage"] == "plan" and not f["pv"]["checking"]
    assert "planreview" in fs.artifacts(f["id"])
    assert any(h["event"] == "plan validated" for h in f["history"])


def test_blocked_plan_goes_back_to_the_planner_then_is_validated_again(auto):
    fs, fake, root, _ = auto
    f = feature_with_plan(fs, fake, root)
    f = finish_stage(fs, fake, root, f["id"], "plan-review.md", PLAN_BAD)
    assert f["status"] == "working" and not f["pv"]["checking"] and f["pv"]["fixes"] == 1
    assert "blocked it" in fake.calls[-1]["task"] and "plan-review.md" in fake.calls[-1]["task"]
    f = finish_stage(fs, fake, root, f["id"], "plan.md", "# plan v2")  # the planner fixed it
    assert f["pv"]["checking"] and "independent plan validator" in fake.calls[-1]["task"]
    f = finish_stage(fs, fake, root, f["id"], "plan-review.md", PLAN_OK)
    assert f["status"] == "awaiting"
    assert (root / "features" / f["id"] / "plan-review.v1.md").is_file()   # the previous validation is archived


def test_plan_rounds_are_capped_and_the_decision_returns_to_the_user(auto):
    fs, fake, root, _ = auto
    f = feature_with_plan(fs, fake, root)
    for _ in range(MAX_PLAN_FIXES):
        f = finish_stage(fs, fake, root, f["id"], "plan-review.md", PLAN_BAD)
        f = finish_stage(fs, fake, root, f["id"], "plan.md", "# fixed plan")
    f = finish_stage(fs, fake, root, f["id"], "plan-review.md", PLAN_BAD)
    assert f["status"] == "awaiting" and f["stage"] == "plan"
    assert any(h["event"] == "plan fixes limit" for h in f["history"])
    fs.approve(f["id"])                                              # the final decision is still the user's


def test_plan_validation_without_verdict_is_repeated_then_left_to_the_user(auto):
    fs, fake, root, _ = auto
    f = feature_with_plan(fs, fake, root)
    for _ in range(MAX_REVIEW_RETRIES):
        f = finish_stage(fs, fake, root, f["id"], "plan-review.md", "garbage")
        assert f["status"] == "working" and f["pv"]["checking"]
    f = finish_stage(fs, fake, root, f["id"], "plan-review.md", "garbage")
    assert f["status"] == "awaiting" and any(h["event"] == "validation without verdict" for h in f["history"])


def test_if_the_validator_cannot_start_the_plan_waits_for_the_user(tmp_path):
    fake = Fake()
    real = fake.launch
    state = {"n": 0}

    def launch(project, task, model, extra_dirs, label=None):
        state["n"] += 1
        if state["n"] == 3:                                          # the 3rd launch is the validator's
            raise RuntimeError("no claude")
        return real(project, task, model, extra_dirs, label=label)

    fs = Features(tmp_path, launch, fake.is_active, auto_review=True)
    f = feature_with_plan(fs, fake, tmp_path)
    assert f["status"] == "awaiting" and any(h["event"] == "plan validation failed" for h in f["history"])


def test_manual_plan_changes_restart_the_rounds_and_get_validated_again(auto):
    fs, fake, root, _ = auto
    f = feature_with_plan(fs, fake, root)
    f = finish_stage(fs, fake, root, f["id"], "plan-review.md", PLAN_OK)
    fs.revise(f["id"], "use another approach")
    f = finish_stage(fs, fake, root, f["id"], "plan.md", "# plan v2")
    assert f["pv"]["checking"] and f["status"] == "working"
