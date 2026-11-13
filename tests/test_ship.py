"""The «ship» kind: prepare a commit locally, then publish (push / PR) only after the user approves. No worktree, no review."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompts_kinds  # noqa: E402
from features import Features  # noqa: E402
from test_auto_review import Fake, finish_stage  # noqa: E402


@pytest.fixture
def env(tmp_path):
    fake = Fake()
    return Features(tmp_path, fake.launch, fake.is_active, auto_review=True), fake, tmp_path


def make(fs, lang="en"):
    return fs.create("Alfa", "Commit the website", "Commit website_deploy/ on a new branch and open a PR.", None, lang=lang, kind="ship")


def test_it_has_two_phases_and_starts_at_the_commit_preparation(env):
    fs, fake, _ = env
    f = make(fs)
    assert prompts_kinds.KIND_STAGES["ship"] == ("build", "review") and "ship" in prompts_kinds.KINDS
    assert f["kind"] == "ship" and f["stage"] == "build" and f["status"] == "working"


def test_the_first_phase_is_local_only_and_stages_files_one_by_one(env):
    fs, fake, _ = env
    make(fs)
    t = " ".join(fake.calls[-1]["task"].split())
    assert "Do NOT push" in t and "never `git add -A`" in t and "Ready to publish" in t and "gh pr create" in t
    assert "--force" in t and "does not apply" not in t               # publishing rules belong to the second phase only


def test_the_report_waits_for_the_user_and_no_review_or_worktree_is_started(env):
    fs, fake, root = env
    f = make(fs)
    calls = len(fake.calls)
    got = finish_stage(fs, fake, root, f["id"], "report.md", "## Execution status\nEXECUTED AND VERIFIED\n## Ready to publish\n...")
    assert got["status"] == "awaiting" and got["stage"] == "build"
    assert len(fake.calls) == calls and not (root / "features" / f["id"] / "work").exists()
    assert not any("review" in h["event"] for h in got["history"])


def test_publishing_only_starts_when_the_user_approves_and_authorises_exactly_the_reported_commands(env):
    fs, fake, root = env
    f = make(fs)
    finish_stage(fs, fake, root, f["id"], "report.md", "## Ready to publish\n`git push -u origin feat/x`")
    calls = len(fake.calls)
    out = fs.approve(f["id"])
    assert out["stage"] == "review" and out["status"] == "working" and len(fake.calls) == calls + 1
    t = " ".join(fake.calls[-1]["task"].split())
    assert "EXACTLY the commands" in t and "Never --force" in t and "do NOT log in" in t and str(root / "features" / f["id"] / "report.md") in t


def test_the_second_approval_closes_the_request(env):
    fs, fake, root = env
    f = make(fs)
    finish_stage(fs, fake, root, f["id"], "report.md", "## Ready to publish")
    fs.approve(f["id"])
    finish_stage(fs, fake, root, f["id"], "review.md", "## Publication\npushed")
    done = fs.approve(f["id"])
    assert done["status"] == "done" and done["closed"]


def test_cancelling_before_the_approval_publishes_nothing(env):
    fs, fake, root = env
    f = make(fs)
    finish_stage(fs, fake, root, f["id"], "report.md", "## Ready to publish")
    calls = len(fake.calls)
    assert fs.cancel(f["id"])["status"] == "cancelled" and len(fake.calls) == calls


def test_portuguese_requests_get_portuguese_prompts_with_the_same_guards(env):
    fs, fake, root = env
    f = make(fs, "pt")
    t = " ".join(fake.calls[-1]["task"].split())
    assert "NÃO faças push" in t and "nunca `git add -A`" in t
    finish_stage(fs, fake, root, f["id"], "report.md", "## Ready to publish")
    fs.approve(f["id"])
    t = " ".join(fake.calls[-1]["task"].split())
    assert "EXATAMENTE os comandos" in t and "NÃO faças login" in t


def test_other_kinds_keep_their_automatic_review(env):
    fs, fake, root = env
    f = fs.create("Alfa", "Why", "x", None, kind="analysis")
    calls = len(fake.calls)
    finish_stage(fs, fake, root, f["id"], "report.md", "# Analysis")
    assert len(fake.calls) == calls + 1                                # the verification started by itself


def test_read_only_prompts_allow_only_the_suggestion_command_as_a_write():
    for lang in ("en", "pt"):
        text = prompts_kinds._READ_ONLY_EN if lang == "en" else prompts_kinds._READ_ONLY_PT
        assert "rule 6" in text or "regra 6" in text


GOOD = "## Execution status\nEXECUTED AND VERIFIED\n\n## Ready to publish\nBranch feat/x\n\n`git push -u origin feat/x`\n`gh pr create --title t --body b`\n"


def test_the_approval_needs_a_report_with_a_ready_to_publish_section_and_a_sane_push(env):
    from features import FeatureError, ship_gate
    assert ship_gate(GOOD) == "ok"
    assert ship_gate("## Execution status\nEXECUTED\n") == "missing" and ship_gate("") == "missing"
    assert ship_gate(GOOD.replace("EXECUTED AND VERIFIED", "NOT EXECUTED — nothing to commit")) == "notexecuted"
    assert ship_gate("## Ready to publish\nPublication: not requested\n") == "ok"          # commit-only: no push at all
    for bad in ("git push --force origin feat/x", "git push -u origin main", "git push -u origin HEAD:main", "git push -u origin +feat/x",
                "git push -u https://evil.example/r.git feat/x", "git push -u origin feat/x --delete", "git push origin feat/x",
                "git push -u origin feat/x --force-with-lease", "git push -u origin master"):
        assert ship_gate(f"## Ready to publish\n`{bad}`\n") == "badpush", bad
    fs, fake, root = env
    f = make(fs)
    finish_stage(fs, fake, root, f["id"], "report.md", "## Execution status\nEXECUTED\n\n## Ready to publish\n`git push -u origin main`\n")
    calls = len(fake.calls)
    with pytest.raises(FeatureError, match="not allowed"):
        fs.approve(f["id"])
    assert len(fake.calls) == calls and next(x for x in fs.list() if x["id"] == f["id"])["stage"] == "build"


def test_a_report_without_the_section_cannot_be_approved(env):
    from features import FeatureError
    fs, fake, root = env
    f = make(fs)
    finish_stage(fs, fake, root, f["id"], "report.md", "## Execution status\nEXECUTED\n")
    with pytest.raises(FeatureError, match="Ready to publish"):
        fs.approve(f["id"])


def test_the_prompts_close_the_push_loopholes():
    t = " ".join(prompts_kinds.SHIP_EN_REVIEW.split())
    for needle in ("--force-with-lease", "refspecs", "--delete", "--mirror", "--no-verify", "main/master/develop", "NAME of an already configured remote"):
        assert needle in t, needle
    b = " ".join(prompts_kinds.SHIP_EN_BUILD.split())
    assert "git remote add/set-url" in b and "--amend" in b and "do not duplicate" in b
