"""The concept phase (solution analysis) before the spec / plan of a feature or a maintenance request."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import decisions  # noqa: E402
import gates  # noqa: E402
from features import FeatureError, Features  # noqa: E402
from test_auto_review import REVIEW_BLOCKED, REVIEW_OK, REVIEW_RESERVES, Fake, FakeCommit, finish_stage  # noqa: E402

pytestmark = pytest.mark.with_concept

CONCEPT = "## Overview\n- a\n\n## Approaches considered\n- A (kept)\n- B (rejected: costs more)\n\n## Recommendation and decisions\n- D1: do A\n"
CONCEPT_PT = "## Visão geral\n- a\n\n## Abordagens consideradas\n- A\n\n## Recomendação e decisões\n- D1: fazer A\n"
MINOR = "## Verdict\nBLOCKED — small things.\n\n## Findings\n- **[MEDIUM] Overview — vague:** say more\n- **[LOW] Goals — wording:** reword\n"


@pytest.fixture
def env(tmp_path):
    fake, commit = Fake(), FakeCommit()
    return Features(tmp_path, fake.launch, fake.is_active, auto_review=True, committer=commit), fake, tmp_path


def card(fs, fid):
    return next(x for x in fs.list() if x["id"] == fid)


def new(fs, kind="feature", **kw):
    return fs.create("Alfa", "Login with 2FA", "I want 2FA", None, kind=kind, **kw)


def concept_validated(fs, fake, root, fid, text=CONCEPT, review=REVIEW_OK):
    finish_stage(fs, fake, root, fid, "concept.md", text)
    return finish_stage(fs, fake, root, fid, "concept-review.md", review)


# ------------------------------------------------------------------ the flow

@pytest.mark.parametrize("kind,after", [("feature", "spec"), ("maintenance", "plan")])
def test_a_request_starts_with_the_concept_and_its_validation_then_waits_for_you(env, kind, after):
    fs, fake, root = env
    f = new(fs, kind)
    assert (f["stage"], f["status"]) == ("concept", "working") and f["step"] == "concept"
    assert "solution analysis" in fake.calls[0]["task"] and str(root / "features" / f["id"] / "concept.md") in fake.calls[0]["task"]
    g = finish_stage(fs, fake, root, f["id"], "concept.md", CONCEPT)
    assert (g["stage"], g["status"]) == ("concept", "working")                          # the independent validator runs by itself
    assert "independent concept validation" in fake.calls[-1]["task"] and "concept-review.md" in fake.calls[-1]["task"]
    g = finish_stage(fs, fake, root, f["id"], "concept-review.md", REVIEW_OK)
    assert (g["stage"], g["status"]) == ("concept", "awaiting")
    assert any(h["event"] == "concept validated" for h in g["history"])
    n = len(fake.calls)
    fs.approve(f["id"])                                                                  # your «yes»
    g = card(fs, f["id"])
    assert (g["stage"], g["status"]) == (after, "working") and len(fake.calls) == n + 1


def test_the_spec_and_the_plan_read_the_approved_concept(env):
    fs, fake, root = env
    f = new(fs)
    concept_validated(fs, fake, root, f["id"])
    fs.approve(f["id"])
    spec_task = " ".join(fake.calls[-1]["task"].split())
    assert "Approved concept" in spec_task and "concept.md" in spec_task and "Do not reopen a decision" in spec_task
    finish_stage(fs, fake, root, f["id"], "spec.md", "# spec")
    fs.approve(f["id"])
    assert "Approved concept" in " ".join(fake.calls[-1]["task"].split())             # the plan too


def test_a_request_without_a_concept_gets_the_old_prompts(env):
    fs, fake, root = env
    f = new(fs, "fix")                                                                  # a fix has no concept phase
    assert (f["stage"], f["step"]) == ("spec", "spec") and "Approved concept" not in fake.calls[0]["task"]


def test_the_maintenance_concept_says_behaviour_does_not_change(env):
    fs, fake, root = env
    new(fs, "maintenance")
    assert "behaviour does NOT change" in " ".join(fake.calls[0]["task"].split())


# ------------------------------------------------------------------ the validation rounds

def test_a_blocked_validation_sends_the_findings_back_once_then_asks_you(env):
    fs, fake, root = env
    f = new(fs)
    g = concept_validated(fs, fake, root, f["id"], review=REVIEW_BLOCKED)
    assert (g["stage"], g["status"]) == ("concept", "working")
    task = " ".join(fake.calls[-1]["task"].split())
    assert "concept-review.md" in task and "fix the concept" in task                     # back to the concept worker with the findings
    assert any(h["event"] == "concept fixes" for h in g["history"])
    g = concept_validated(fs, fake, root, f["id"], review=REVIEW_BLOCKED)                # the round is used: it is up to you now
    assert g["status"] == "awaiting" and any(h["event"] == "concept fixes limit" for h in g["history"])


def test_only_minor_findings_inform_and_do_not_send_it_back(env):
    fs, fake, root = env
    f = new(fs)
    n = len(fake.calls)
    g = concept_validated(fs, fake, root, f["id"], review=MINOR)
    assert g["status"] == "awaiting" and len(fake.calls) == n + 1                        # only the validator ran
    assert any(h["event"] == "concept validated" and "reservations" in h["note"] for h in g["history"])


def test_a_review_with_no_verdict_is_repeated(env):
    fs, fake, root = env
    f = new(fs)
    g = concept_validated(fs, fake, root, f["id"], review="# thoughts\nnothing readable\n")
    assert g["status"] == "working" and any(h["event"] == "concept validation repeated" for h in g["history"])


# ------------------------------------------------------------------ the gate and the other modes

def test_approving_a_concept_without_its_sections_is_refused(env):
    fs, fake, root = env
    f = new(fs)
    concept_validated(fs, fake, root, f["id"], text="## Overview\n- only this\n")
    with pytest.raises(FeatureError, match="missing sections"):
        fs.approve(f["id"])
    assert card(fs, f["id"])["stage"] == "concept"


def test_the_sections_may_be_written_in_portuguese(env):
    fs, fake, root = env
    f = new(fs, lang="pt")
    concept_validated(fs, fake, root, f["id"], text=CONCEPT_PT)
    fs.approve(f["id"])
    assert card(fs, f["id"])["stage"] == "spec"
    assert gates.required_sections(CONCEPT_PT, [["Approaches considered", "Abordagens consideradas"]]) == []
    assert gates.required_sections("## Visão geral\n", [["Approaches considered", "Abordagens consideradas"], "Overview"]) == ["Approaches considered", "Overview"]


def test_autopilot_never_approves_the_concept_but_still_approves_the_spec(env):
    fs, fake, root = env
    f = new(fs, autopilot=True)
    (root / "features" / f["id"]).mkdir(parents=True, exist_ok=True)
    (root / "features" / f["id"] / "questions.json").write_text("[]", encoding="utf-8")
    g = concept_validated(fs, fake, root, f["id"])
    assert (g["stage"], g["status"]) == ("concept", "awaiting")                         # the concept is yours
    assert not any(h["event"] == "approved automatically" for h in g["history"])
    fs.approve(f["id"])
    (root / "features" / f["id"] / "questions.json").write_text("[]", encoding="utf-8")
    g = finish_stage(fs, fake, root, f["id"], "spec.md", "# spec")
    assert g["stage"] == "plan"                                                          # «run until the review» starts at the spec


def test_asking_for_changes_runs_the_concept_again_with_the_comment(env):
    fs, fake, root = env
    f = new(fs)
    concept_validated(fs, fake, root, f["id"])
    fs.revise(f["id"], "also consider offline use")
    g = card(fs, f["id"])
    assert (g["stage"], g["status"]) == ("concept", "working") and "offline use" in fake.calls[-1]["task"]
    assert (root / "features" / f["id"] / "concept.v1.md").is_file()                    # the earlier version is kept


def test_the_trail_records_the_concept_decisions(env):
    fs, fake, root = env
    f = new(fs)
    concept_validated(fs, fake, root, f["id"])
    fs.approve(f["id"])
    trail = (root / "features" / f["id"] / decisions.FILE).read_text(encoding="utf-8")
    assert "concept validated" in trail and "approved" in trail and "concept.md " in trail


def test_the_card_carries_the_concept_and_its_validation(env):
    fs, fake, root = env
    f = new(fs)
    concept_validated(fs, fake, root, f["id"])
    arts = fs.artifacts(f["id"])
    assert arts["concept"].startswith("## Overview") and arts["conceptreview"].startswith("## Verdict")
    assert fs.pipeline_view(card(fs, f["id"])) == {"name": "", "stages": ["concept", "spec", "plan", "build", "review"]}


def test_the_cost_of_the_two_phases_is_tagged_with_their_phase(tmp_path):
    tagged = []
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True, committer=FakeCommit(),
                  on_launch=lambda wid, request=None, phase=None: tagged.append(phase))
    f = new(fs)
    concept_validated(fs, fake, tmp_path, f["id"])
    assert tagged[:2] == ["concept", "concept-validation"]


def test_a_blocked_review_of_a_reserved_concept_does_not_leave_a_check_running(env):
    fs, fake, root = env
    f = new(fs)
    g = concept_validated(fs, fake, root, f["id"], review=REVIEW_RESERVES)
    assert g["status"] == "awaiting" and not g.get("cv", {}).get("checking")
