import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from features import FeatureError, Features  # noqa: E402


class Fake:
    """Fake launcher + worker states."""

    def __init__(self):
        self.calls = []
        self.active = set()

    def launch(self, project, task, model, extra_dirs, label=None):
        wid = f"w{len(self.calls) + 1}"
        self.calls.append({"project": project, "task": task, "model": model, "dirs": list(extra_dirs), "id": wid})
        self.active.add(wid)
        return {"id": wid, "name": f"cos-{wid}"}

    def is_active(self, wid):
        return wid in self.active

    def finish(self, wid, root, fid, artifact, text="content"):
        self.active.discard(wid)
        if artifact:
            d = Path(root) / "features" / fid
            d.mkdir(parents=True, exist_ok=True)
            (d / artifact).write_text(text, encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    fake = Fake()
    return Features(tmp_path, fake.launch, fake.is_active), fake, tmp_path


def make(fs, project="Alfa", title="Login with 2FA"):
    return fs.create(project, title, "I want 2FA via TOTP", None)


def test_create_launches_spec_worker(env):
    fs, fake, root = env
    f = make(fs)
    assert (f["stage"], f["status"]) == ("spec", "working")
    task = fake.calls[0]["task"]
    assert "Login with 2FA" in task and "spec.md" in task and f["id"] in task
    assert fake.calls[0]["project"] == "Alfa"
    assert str(root / "features" / f["id"]) in fake.calls[0]["dirs"]


def test_refresh_awaiting_when_worker_done_and_artifact_exists(env):
    fs, fake, root = env
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md")
    assert fs.list()[0]["status"] == "awaiting"


def test_refresh_failed_when_worker_done_without_artifact(env):
    fs, fake, root = env
    f = make(fs)
    fake.finish("w1", root, f["id"], None)
    assert fs.list()[0]["status"] == "failed"


def test_refresh_keeps_working_while_worker_active(env):
    fs, _, _ = env
    make(fs)
    assert fs.list()[0]["status"] == "working"


def test_approve_advances_and_launches_plan(env):
    fs, fake, root = env
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md")
    fs.list()
    g = fs.approve(f["id"])
    assert (g["stage"], g["status"]) == ("plan", "working")
    assert "plan.md" in fake.calls[1]["task"] and "spec.md" in fake.calls[1]["task"]


def test_approve_last_stage_is_done(env):
    fs, fake, root = env
    f = make(fs)
    for art in ("spec.md", "plan.md", "report.md", "review.md"):
        fake.finish(f"w{len(fake.calls)}", root, f["id"], art)
        fs.list()
        g = fs.approve(f["id"])
    assert g["status"] == "done"
    assert len(fake.calls) == 4
    assert "feat/" in fake.calls[2]["task"] and "independent reviewer" in fake.calls[3]["task"]


def test_approve_requires_awaiting(env):
    fs, _, _ = env
    f = make(fs)
    with pytest.raises(FeatureError):
        fs.approve(f["id"])


def test_revise_relaunches_same_stage_with_comment_and_keeps_old(env):
    fs, fake, root = env
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md", "version 1")
    fs.list()
    g = fs.revise(f["id"], "the recovery case is missing")
    assert (g["stage"], g["status"]) == ("spec", "working")
    assert "the recovery case is missing" in fake.calls[1]["task"]
    assert not (root / "features" / f["id"] / "spec.md").exists()
    assert (root / "features" / f["id"] / "spec.v1.md").read_text(encoding="utf-8") == "version 1"


def test_retry_after_failure(env):
    fs, fake, root = env
    f = make(fs)
    fake.finish("w1", root, f["id"], None)
    fs.list()
    assert fs.retry(f["id"])["status"] == "working"
    assert len(fake.calls) == 2


def test_several_features_can_run_in_parallel_in_the_same_project(env):
    fs, fake, _ = env
    a = make(fs)
    b = make(fs, title="Other")
    c = make(fs, project="Beta", title="Third")
    assert [x["status"] for x in (a, b, c)] == ["working"] * 3
    assert len({x["id"] for x in (a, b, c)}) == 3 and len(fake.calls) == 3


def test_build_stage_isolates_each_feature_in_its_own_git_worktree(env):
    fs, fake, root = env
    f = make(fs)
    for art in ("spec.md", "plan.md"):
        fake.finish(f"w{len(fake.calls)}", root, f["id"], art)
        fs.list()
        fs.approve(f["id"])
    task = fake.calls[2]["task"]
    assert "git worktree add" in task and f"feat/{f['id']}" in task and "do NOT check out" in task


def test_launch_failure_leaves_no_feature(env):
    fs, fake, _ = env

    def boom(*a):
        raise RuntimeError("claude did not start")

    fs._launch = boom
    with pytest.raises(FeatureError):
        make(fs)
    assert fs.list() == []


@pytest.mark.parametrize("title", ["", "   ", "x" * 121])
def test_rejects_bad_title(env, title):
    fs, _, _ = env
    with pytest.raises(FeatureError):
        fs.create("Alfa", title, "d", None)


def test_rejects_unknown_model_and_bad_id(env):
    fs, _, _ = env
    with pytest.raises(FeatureError):
        fs.create("Alfa", "t", "d", "--evil")
    with pytest.raises(FeatureError):
        fs.create("Alfa", "t", "d", "a model; rm -rf /")
    for bad in ("../x", "a/b", ""):
        with pytest.raises(FeatureError):
            fs.cancel(bad)


def test_cancel_marks_cancelled_and_frees_project(env):
    fs, _, _ = env
    f = make(fs)
    assert fs.cancel(f["id"])["status"] == "cancelled"
    make(fs, title="New")


def test_artifacts_returned_and_capped(env):
    fs, fake, root = env
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md", "a" * 100_000)
    art = fs.artifacts(f["id"])
    assert set(art) == {"spec"} and len(art["spec"]) <= 40_000 + 50


def test_failed_revise_launch_restores_artifact(env):
    fs, fake, root = env
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md", "v1")
    fs.list()

    def boom(*a):
        raise RuntimeError("x")

    fs._launch = boom
    with pytest.raises(FeatureError):
        fs.revise(f["id"], "change this")
    assert (root / "features" / f["id"] / "spec.md").read_text(encoding="utf-8") == "v1"
    assert fs.list()[0]["status"] == "awaiting"


def test_failed_feature_recovers_when_its_worker_is_still_active(env):
    """Wrongly marked as failed (worker still active): goes back to “working”."""
    fs, fake, root = env
    f = make(fs)
    fake.active.discard("w1")
    assert fs.list()[0]["status"] == "failed"
    fake.active.add("w1")
    assert fs.list()[0]["status"] == "working"


def test_chief_treats_working_as_active():
    import chief
    for state in ("working", "running", "blocked", "idle", "waiting"):
        assert state in chief.ACTIVE
    assert "failed" not in chief.ACTIVE


class Fake2(Fake):
    """A worker may be active but stopped waiting for input (idle/blocked), as in the real claude."""

    def __init__(self):
        super().__init__()
        self.busy = set()

    def launch(self, *a, **k):
        w = super().launch(*a, **k)
        self.busy.add(w["id"])
        return w

    def is_busy(self, wid):
        return wid in self.busy


@pytest.fixture
def env2(tmp_path):
    fake = Fake2()
    return Features(tmp_path, fake.launch, fake.is_active, is_busy=fake.is_busy), fake, tmp_path


def test_awaiting_when_artifact_written_and_worker_idle_but_session_open(env2):
    fs, fake, root = env2
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md")
    fake.active.add("w1")      # session still open...
    fake.busy.discard("w1")    # ...but stopped waiting for input
    assert fs.list()[0]["status"] == "awaiting"


def test_still_working_while_artifact_exists_but_worker_busy(env2):
    fs, fake, root = env2
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md")
    fake.active.add("w1")      # still working
    assert fs.list()[0]["status"] == "working"


def test_still_working_when_idle_without_artifact(env2):
    fs, fake, _ = env2
    make(fs)
    fake.busy.discard("w1")    # stopped without an artifact: blocked asking for something, not finished
    assert fs.list()[0]["status"] == "working"


def test_failed_with_artifact_and_idle_worker_becomes_awaiting(env2):
    fs, fake, root = env2
    f = make(fs)
    fake.active.discard("w1")
    assert fs.list()[0]["status"] == "failed"
    fake.finish("w1", root, f["id"], "spec.md")
    fake.active.add("w1")
    fake.busy.discard("w1")
    assert fs.list()[0]["status"] == "awaiting"


def test_task_asks_for_structured_questions_with_options(env):
    fs, fake, _ = env
    make(fs)
    task = fake.calls[0]["task"]
    assert "Open questions" in task and "(recommended)" in task and "- [ ]" in task


def test_revision_asks_worker_to_answer_user_doubts(env):
    fs, fake, root = env
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md")
    fs.list()
    fs.revise(f["id"], "P1: yes. P2: I did not understand, what is that?")
    assert "Answers to your doubts" in fake.calls[1]["task"]


def test_revise_accepts_long_structured_answers(env):
    fs, fake, root = env
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md")
    fs.list()
    assert fs.revise(f["id"], "x" * 5000)["status"] == "working"


@pytest.fixture
def env3(tmp_path):
    fake = Fake()
    stopped = []
    return Features(tmp_path, fake.launch, fake.is_active, on_release=stopped.append), fake, tmp_path, stopped


def test_previous_worker_is_stopped_when_a_new_one_replaces_it(env3):
    fs, fake, root, stopped = env3
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md")
    fs.list()
    fs.revise(f["id"], "change this")      # w2 replaces w1
    assert stopped == ["w1"]
    fake.finish("w2", root, f["id"], "spec.md")
    fs.list()
    fs.approve(f["id"])                  # w3 (plan) replaces w2
    assert stopped == ["w1", "w2"]


def test_retry_stops_the_failed_worker_and_cancel_stops_the_current_one(env3):
    fs, fake, root, stopped = env3
    f = make(fs)
    fake.active.discard("w1")
    fs.list()
    fs.retry(f["id"])
    assert stopped == ["w1"]
    fs.cancel(f["id"])
    assert stopped == ["w1", "w2"]


def test_finishing_the_last_stage_releases_the_last_worker(env3):
    fs, fake, root, stopped = env3
    f = make(fs)
    for art in ("spec.md", "plan.md", "report.md", "review.md"):
        fake.finish(f"w{len(fake.calls)}", root, f["id"], art)
        fs.list()
        fs.approve(f["id"])
    assert stopped == ["w1", "w2", "w3", "w4"]


def test_worker_awaiting_decision_is_kept_so_the_user_can_still_talk_to_it(env3):
    fs, fake, root, stopped = env3
    f = make(fs)
    fake.finish("w1", root, f["id"], "spec.md")
    fake.active.add("w1")
    assert fs.list()[0]["status"] in ("awaiting", "working") and stopped == []


def test_release_failure_never_breaks_the_flow(tmp_path):
    fake = Fake()

    def boom(_):
        raise RuntimeError("claude stop failed")

    fs = Features(tmp_path, fake.launch, fake.is_active, on_release=boom)
    f = make(fs)
    fake.finish("w1", tmp_path, f["id"], "spec.md")
    fs.list()
    assert fs.revise(f["id"], "x")["status"] == "working"


def to_stage(fs, fake, root, stage):
    """Take a new feature to the end of phase `stage` (artifact written, waiting for a decision)."""
    f = make(fs)
    order = ("spec", "plan", "build", "review")
    art = {"spec": "spec.md", "plan": "plan.md", "build": "report.md", "review": "review.md"}
    for st in order:
        fake.finish(f"w{len(fake.calls)}", root, f["id"], art[st], f"# {st}")
        fs.list()
        if st == stage:
            return f
        fs.approve(f["id"])


def test_approving_the_build_report_launches_an_independent_reviewer(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "build")
    g = fs.approve(f["id"])
    assert (g["stage"], g["status"]) == ("review", "working")
    task = fake.calls[3]["task"]
    for needle in ("independent reviewer", "Do NOT change code", "diff HEAD", "review.md", "report.md", "CRITICAL", "APPROVED"):
        assert needle in task, needle


def test_request_fixes_sends_review_findings_back_to_the_builder(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "review")
    (root / "features" / f["id"] / "work").mkdir(parents=True)      # the implementation worktree already exists
    g = fs.request_fixes(f["id"], "give priority to the HIGH ones")
    assert (g["stage"], g["status"]) == ("build", "working")
    task = fake.calls[-1]["task"]
    assert "review.md" in task and "give priority to the HIGH ones" in task
    assert "git worktree add" not in task and "already exists" in task      # does not create the worktree again
    assert any(h["event"] == "fixes requested" for h in g["history"])
    assert (root / "features" / f["id"] / "report.v1.md").exists()


def test_request_fixes_only_in_review_when_awaiting(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "build")
    with pytest.raises(FeatureError):
        fs.request_fixes(f["id"], "")


def test_after_fixes_the_next_approval_triggers_a_fresh_review(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "review")
    fs.request_fixes(f["id"], "")
    fake.finish(f"w{len(fake.calls)}", root, f["id"], "report.md", "# fixed")
    fs.list()
    g = fs.approve(f["id"])
    assert (g["stage"], g["status"]) == ("review", "working")
    assert (root / "features" / f["id"] / "review.v1.md").exists()      # the old review is archived


def test_revising_the_build_reuses_the_existing_worktree(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "build")
    (root / "features" / f["id"] / "work").mkdir(parents=True)
    fs.revise(f["id"], "a test is missing")
    task = fake.calls[-1]["task"]
    assert "git worktree add" not in task and "already exists" in task


def test_every_stage_tells_the_worker_which_superpowers_skills_to_use(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "build")
    fs.approve(f["id"])                                    # launches the review
    spec, plan, build, review = (c["task"] for c in fake.calls[:4])
    assert "superpowers:brainstorming" in spec and "do NOT ask questions in chat" in spec
    assert "superpowers:writing-plans" in plan
    for skill in ("superpowers:executing-plans", "superpowers:test-driven-development", "superpowers:verification-before-completion"):
        assert skill in build, skill
    assert "superpowers:requesting-code-review" in review and "superpowers:verification-before-completion" in review


def test_skill_adaptations_for_background_workers_are_explicit(env):
    fs, fake, root = env
    make(fs)
    spec = " ".join(fake.calls[0]["task"].split())      # the prompt's line breaks do not count
    assert "docs/superpowers/specs" in spec and "or commit to the repo" in spec and "Open questions" in spec


def test_resumed_build_still_uses_tdd_and_verification_skills(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "build")
    (root / "features" / f["id"] / "work").mkdir(parents=True)
    fs.revise(f["id"], "a test is missing")
    task = fake.calls[-1]["task"]
    assert "superpowers:test-driven-development" in task and "superpowers:verification-before-completion" in task


# ---- structured questions (questions.json) ----
from features import load_questions  # noqa: E402

GOOD = [{"id": "P1", "title": "Remote branch", "text": "Delete on GitHub too?",
         "options": [{"label": "Yes, delete", "recommended": True}, {"label": "No, only the local one"}]},
        {"id": "P2", "title": "Scope", "text": "Only SPEC-1..3?", "options": []}]


def write_q(path, data, raw=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")
    return path


def test_load_questions_normalizes_a_valid_file(tmp_path):
    qs = load_questions(write_q(tmp_path / "questions.json", GOOD))
    assert [q["id"] for q in qs] == ["P1", "P2"]
    assert qs[0]["options"] == [{"label": "Yes, delete", "rec": True}, {"label": "No, only the local one", "rec": False}]
    assert qs[1]["options"] == []


def test_load_questions_empty_list_means_no_questions(tmp_path):
    assert load_questions(write_q(tmp_path / "q.json", [])) == []


@pytest.mark.parametrize("raw", ["not json", '{"id": "P1"}', '["loose text"]', '[{"title": "no id or text"}]'])
def test_load_questions_rejects_malformed_files(tmp_path, raw):
    assert load_questions(write_q(tmp_path / "q.json", None, raw=raw)) is None


def test_load_questions_missing_file_is_none(tmp_path):
    assert load_questions(tmp_path / "does-not-exist.json") is None


def test_load_questions_caps_sizes_and_dedupes_ids(tmp_path):
    big = [{"id": "P1", "title": "t" * 500, "text": "x" * 5000,
            "options": [{"label": f"op{i}"} for i in range(20)]} for _ in range(30)]
    qs = load_questions(write_q(tmp_path / "q.json", big))
    assert len(qs) == 20 and len(qs[0]["title"]) <= 120 and len(qs[0]["text"]) <= 1500 and len(qs[0]["options"]) == 8
    assert len({q["id"] for q in qs}) == 20


def test_features_expose_questions_for_the_current_stage(env):
    fs, fake, root = env
    f = make(fs)
    assert fs.questions(f["id"]) is None                    # the worker has not written them yet
    write_q(root / "features" / f["id"] / "questions.json", GOOD)
    assert [q["id"] for q in fs.questions(f["id"])] == ["P1", "P2"]


def test_worker_is_told_to_write_questions_json(env):
    fs, fake, root = env
    f = make(fs)
    task = fake.calls[0]["task"]
    assert str(root / "features" / f["id"] / "questions.json") in task and '"recommended"' in task


def test_old_questions_are_archived_when_a_new_worker_starts(env):
    fs, fake, root = env
    f = make(fs)
    d = root / "features" / f["id"]
    fake.finish("w1", root, f["id"], "spec.md")
    write_q(d / "questions.json", GOOD)
    fs.list()
    fs.revise(f["id"], "answered")
    assert not (d / "questions.json").exists()               # the new worker writes its own
    assert (d / "questions.spec.v1.json").exists()
    assert fs.questions(f["id"]) is None


def test_build_report_must_declare_what_was_actually_executed_and_verified(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "plan")
    fs.approve(f["id"])
    task = " ".join(fake.calls[2]["task"].split())
    assert "## Execution status" in task
    for state in ("EXECUTED AND VERIFIED", "PARTIALLY EXECUTED", "NOT EXECUTED"):
        assert state in task, state
    assert "End with [DONE] only if" in task and "[BLOCKED]" in task


def test_resumed_build_also_requires_the_execution_status(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "build")
    (root / "features" / f["id"] / "work").mkdir(parents=True)
    fs.revise(f["id"], "actually run it")
    assert "## Execution status" in fake.calls[-1]["task"]


def test_reviewer_cannot_approve_what_was_not_executed(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "build")
    fs.approve(f["id"])
    task = " ".join(fake.calls[3]["task"].split())
    assert "Execution status" in task and "NOT EXECUTED" in task and "is never APPROVED" in task


def test_request_fixes_selected_tells_the_builder_to_fix_only_the_chosen_findings(env):
    fs, fake, root = env
    f = to_stage(fs, fake, root, "review")
    (root / "features" / f["id"] / "work").mkdir(parents=True)
    fs.request_fixes(f["id"], "## Achados a corrigir (escolhidos pelo utilizador)\n- A1", selected=True)
    task = fake.calls[-1]["task"]
    assert "Fix ONLY the findings listed" in task and "- A1" in task and "CRITICAL and HIGH ones first" not in task


@pytest.mark.parametrize("model", ["opus", "claude-sonnet-4-5", "us.anthropic.claude-sonnet-4-5-v1:0", "my-gateway/qwen3-coder", "opus[1m]"])
def test_any_reasonable_model_name_is_accepted_and_passed_on(env, model):
    fs, fake, _ = env
    f = fs.create("Alfa", "t", "d", model)
    assert f["model"] == model and fake.calls[-1]["model"] == model


def test_the_models_in_the_dashboard_come_from_the_configuration(monkeypatch):
    import chief
    assert chief.models() == ["opus", "sonnet", "haiku"]
    monkeypatch.setattr(chief, "CFG", {**chief.CFG, "models": ["qwen3-coder", "glm-4.6"]})
    assert chief.models() == ["qwen3-coder", "glm-4.6"]
