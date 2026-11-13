"""Request kinds: feature, bug fix, analysis and maintenance each have their own phases, prompts and archiving."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FeatureError, Features  # noqa: E402
from test_auto_review import Fake, finish_stage  # noqa: E402

PLAN_OK = "## Verdict\nAPPROVED — ok.\n\n## Findings\nNone.\n"
REVIEW_OK = "## Verdict\nAPPROVED — ok.\n\n## Tests\nCONFIRMED\nREVIEWER RESULT: 10 passed, 0 failed\n\n## Findings\nNone.\n"
REVIEW_BAD = "## Verdict\nBLOCKED — wrong claim.\n\n## Findings\n- **[HIGH] Evidence — no source:** cite it\n"


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, kind, project, title, fid, created, text, lang):
        self.calls.append((kind, text.strip()))
        return "ok"


@pytest.fixture
def env(tmp_path):
    fake, rec = Fake(), Recorder()
    return Features(tmp_path, fake.launch, fake.is_active, auto_review=True, publisher=rec), fake, tmp_path, rec


def test_unknown_kind_is_rejected_and_feature_is_the_default(env):
    fs, fake, _, _ = env
    with pytest.raises(FeatureError, match="request type"):
        fs.create("Alfa", "x", "y", None, kind="magic")
    f = fs.create("Alfa", "x", "y", None)
    assert f["kind"] == "feature" and f["stage"] == "spec"


@pytest.mark.parametrize("kind,first", [("feature", "spec"), ("fix", "spec"), ("maintenance", "plan"), ("analysis", "build")])
def test_each_kind_starts_in_its_own_first_phase(env, kind, first):
    fs, *_ = env
    assert fs.create("Alfa", "x", "y", None, kind=kind)["stage"] == first


def test_bug_fix_starts_with_a_diagnosis_and_demands_a_failing_test_first(env):
    fs, fake, root, _ = env
    f = fs.create("Alfa", "Login fails", "It returns a 500", None, kind="fix")
    task = fake.calls[-1]["task"]
    assert "Phase 1: diagnosis" in task and "root cause" in task and "Do NOT write the fix" in task
    finish_stage(fs, fake, root, f["id"], "spec.md", "# diagnosis")
    fs.approve(f["id"])
    assert "must be a test that reproduces the bug and fails" in " ".join(fake.calls[-1]["task"].split())
    finish_stage(fs, fake, root, f["id"], "plan.md")
    assert "reproduces the bug and fails" in fake.calls[-1]["task"]     # the validator demands it
    finish_stage(fs, fake, root, f["id"], "plan-review.md", PLAN_OK)
    fs.approve(f["id"])
    build = fake.calls[-1]["task"]
    assert "FIRST write the test that reproduces the bug" in build and "BUG TEST" in build and "BASELINE" in build
    (root / "features" / f["id"] / "work").mkdir(parents=True)
    finish_stage(fs, fake, root, f["id"], "report.md")
    assert "a test exists that reproduces exactly the diagnosis' symptom" in " ".join(fake.calls[-1]["task"].split())


def test_maintenance_has_no_spec_and_must_not_change_behavior(env):
    fs, fake, root, _ = env
    f = fs.create("Alfa", "Update dependencies", "Bump pytest", None, kind="maintenance")
    task = fake.calls[-1]["task"]
    assert "maintenance plan" in task and "There is no spec" in task and f["stage"] == "plan"
    finish_stage(fs, fake, root, f["id"], "plan.md")
    assert "the user's request above" in fake.calls[-1]["task"] and "Maintenance: the plan does not change behavior" in fake.calls[-1]["task"]
    finish_stage(fs, fake, root, f["id"], "plan-review.md", PLAN_OK)
    f = fs.approve(f["id"])
    assert f["stage"] == "build" and "behavior must NOT change" in fake.calls[-1]["task"]
    assert "spec.md" not in fake.calls[-1]["task"]


def test_analysis_is_read_only_and_verified_independently(env):
    fs, fake, root, rec = env
    f = fs.create("Alfa", "Why is start-up slow?", "Investigate", None, kind="analysis")
    task = fake.calls[-1]["task"]
    assert "READ-ONLY" in task and "analysis" in task and "do NOT create" in task and "git worktree add" not in task
    f = finish_stage(fs, fake, root, f["id"], "report.md", "# Analysis\nanswer")        # the verification starts by itself
    assert f["stage"] == "review" and f["status"] == "working"
    assert "independent verifier" in fake.calls[-1]["task"] and "## Claims" in fake.calls[-1]["task"]
    f = finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_OK)
    assert f["status"] == "awaiting" and f["stage"] == "review"
    assert not any(h["event"] == "committed" for h in f["history"])
    f = fs.approve(f["id"])
    assert f["status"] == "done"
    assert rec.calls == [("analysis", "# Analysis\nanswer")]                              # archived on the final approval


def test_analysis_questions_stay_available_next_to_the_verification(env):
    fs, fake, root, _ = env
    f = fs.create("Alfa", "Why is start-up slow?", "Investigate", None, kind="analysis")
    qdir = root / "features" / f["id"]
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "questions.json").write_text(
        '[{"id":"P1","title":"T","text":"What now?","options":[{"label":"A","recommended":true},{"label":"B"}]}]', encoding="utf-8")
    finish_stage(fs, fake, root, f["id"], "report.md", "# Analysis\nanswer")     # the verification starts by itself
    finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_OK)
    qs = fs.questions(f["id"])
    assert qs and qs[0]["id"] == "P1"                                               # the form is not lost
    assert not list(qdir.glob("questions.review.v*.json"))


def test_blocked_verification_sends_the_analysis_back_to_be_fixed(env):
    fs, fake, root, _ = env
    f = fs.create("Alfa", "Why is it slow?", "x", None, kind="analysis")
    finish_stage(fs, fake, root, f["id"], "report.md", "# Analysis")
    f = finish_stage(fs, fake, root, f["id"], "review.md", REVIEW_BAD)
    assert f["stage"] == "build" and f["status"] == "working"
    assert "wrong claims" in fake.calls[-1]["task"] and "READ-ONLY" in fake.calls[-1]["task"]


def test_portuguese_kinds_use_portuguese_prompts(env):
    fs, fake, root, _ = env
    fs.create("Alfa", "Porque é lento", "x", None, lang="pt", kind="analysis")
    assert "SÓ LEITURA" in fake.calls[-1]["task"] and "NÃO cries worktrees" in fake.calls[-1]["task"]
    fs.create("Alfa", "Login falha", "x", None, lang="pt", kind="fix")
    assert "Fase 1: diagnóstico" in fake.calls[-1]["task"] and "NÃO escrevas a correção" in fake.calls[-1]["task"]
    fs.create("Alfa", "Atualizar dependências", "x", None, lang="pt", kind="maintenance")
    assert "plano de manutenção" in fake.calls[-1]["task"] and "Não há spec" in fake.calls[-1]["task"]


def test_english_kinds_use_english_prompts(env):
    fs, fake, root, _ = env
    fs.create("Alfa", "Why slow", "x", None, lang="en", kind="analysis")
    assert fake.calls[-1]["task"].startswith("Language: the user works in English") and "READ-ONLY" in fake.calls[-1]["task"]
    fs.create("Alfa", "Login fails", "x", None, lang="en", kind="fix")
    assert "Phase 1: diagnosis" in fake.calls[-1]["task"] and "Open questions" in fake.calls[-1]["task"]
    fs.create("Alfa", "Upgrade deps", "x", None, lang="en", kind="maintenance")
    assert "maintenance plan" in fake.calls[-1]["task"] and "There is no spec" in fake.calls[-1]["task"]


def test_old_features_without_a_kind_behave_as_features(env):
    fs, fake, root, _ = env
    f = fs.create("Alfa", "x", "y", None)
    data = fs._load()
    del data[0]["kind"]
    fs._save(data)
    finish_stage(fs, fake, root, f["id"], "spec.md")
    assert fs.approve(f["id"])["stage"] == "plan"


def test_maintenance_archives_only_the_plan_and_fix_archives_diagnosis_and_plan(env):
    fs, fake, root, rec = env
    m = fs.create("Alfa", "Cleanup", "x", None, kind="maintenance")
    finish_stage(fs, fake, root, m["id"], "plan.md", "# plan m")
    finish_stage(fs, fake, root, m["id"], "plan-review.md", PLAN_OK)
    fs.approve(m["id"])
    assert rec.calls == [("plan", "# plan m")]
