"""Pipeline definitions: the validator accepts a good one and refuses each unsafe or malformed rule, one test per rule."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pipelines  # noqa: E402
from pipelines import PipelineError  # noqa: E402


def good():
    return {"format": 1, "id": "demo", "version": 1, "name": "Demo", "steps": [
        {"id": "plan", "type": "worker", "phase": "plan", "mode": "read_only", "artifact": "plan.md",
         "prompt": {"en": "Plan {title} in {path}"}},
        {"id": "ok-plan", "type": "approval", "of": "plan"},
        {"id": "build", "type": "worker", "phase": "build", "mode": "worktree", "prompt": {"en": "Build {title}"}},
        {"id": "review", "type": "check", "phase": "review", "mode": "read_only", "prompt": {"en": "Review {report}"},
         "gates": [{"use": "no_critical_findings", "args": {"file": "review.md"}}],
         "on": {"blocked": {"goto": "build", "max_rounds": 3}, "unknown": {"retry": 2}}},
        {"id": "ok-build", "type": "approval", "of": "build"},
    ]}


def bad(change, match):
    d = good()
    change(d)
    with pytest.raises(PipelineError, match=match):
        pipelines.load(d)


def test_good_pipeline_loads_and_snapshot_is_a_copy():
    p = pipelines.load(good())
    snap = pipelines.snapshot(p)
    snap["steps"][0]["id"] = "changed"
    assert p.steps[0]["id"] == "plan"
    assert len(pipelines.digest(p)) == 64


def test_rejects_unknown_key():
    bad(lambda d: d["steps"][0].update(shell="rm -rf /"), "unknown key")
    bad(lambda d: d.update(hook="x"), "unknown key")


def test_rejects_goto_back_without_max_rounds():
    bad(lambda d: d["steps"][3]["on"].update(blocked={"goto": "build"}), "max_rounds")
    bad(lambda d: d["steps"][3]["on"].update(blocked="build"), "max_rounds")


def publishing_step():
    return {"id": "ship", "type": "worker", "mode": "publish", "prompt": {"en": "push"}}


def publishing(d):
    d["steps"].insert(4, publishing_step())


def test_rejects_publish_without_approval_before():
    def change(d):
        publishing(d)
        d["steps"] = [s for s in d["steps"] if s["type"] != "approval"]
        d["steps"][-2]["on"] = {"blocked": {"goto": "build", "max_rounds": 2}}
    bad(change, "approval")


def test_publish_requires_approval_on_all_paths():
    d = good()
    publishing(d)
    d["steps"][3]["on"]["approved"] = "ship"            # approved review jumps straight to publishing, skipping ok-build
    d["steps"][3]["on"]["blocked"]["goto"] = "build"
    with pytest.raises(PipelineError, match="approval"):
        pipelines.load(d)
    d2 = good()
    d2["steps"].append(publishing_step())                # ship sits right after ok-build: the only way in is an approval
    pipelines.load(d2)


def test_rejects_unknown_gate():
    bad(lambda d: d["steps"][3]["gates"].append({"use": "eval_python"}), "unknown gate")


def test_rejects_git_push_outside_publish_mode():
    bad(lambda d: d["steps"][2].update(permissions={"allow": ["Bash(git push:*)"]}), "push")


def test_rejects_next_to_missing_step():
    bad(lambda d: d["steps"][0].update(next="nowhere"), "unknown step")


def test_rejects_unbounded_loop_through_next():
    def change(d):
        d["steps"][3]["next"] = "build"                 # an unconditional way back: it would never end
    bad(change, "max_rounds")


def test_rejects_id_not_matching_file_name(tmp_path):
    (tmp_path / "other.json").write_text(json.dumps(good()), encoding="utf-8")
    with pytest.raises(PipelineError, match="differs from the file name"):
        pipelines.load(tmp_path / "other.json")
    (tmp_path / "demo.json").write_text(json.dumps(good()), encoding="utf-8")
    assert pipelines.load(tmp_path / "demo.json").id == "demo"


def test_limits_size_64kb(tmp_path):
    d = good()
    d["description"] = "x" * 70_000
    (tmp_path / "demo.json").write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(PipelineError, match="64 KB"):
        pipelines.load(tmp_path / "demo.json")


def test_limits_30_steps():
    def change(d):
        d["steps"] = [{"id": f"s{i}", "type": "worker", "prompt": {"en": "x"}} for i in range(31)]
    bad(change, "at most 30")


def test_limits_prompt_and_rules():
    bad(lambda d: d["steps"][0]["prompt"].update(en="x" * 20_001), "20000")
    bad(lambda d: d["steps"][2].update(permissions={"allow": ["Read"] * 25}), "at most 24")


def test_rejects_token_in_text():
    bad(lambda d: d["steps"][0]["prompt"].update(en="use sk-abcdefghijklmnopqrstuv now"), "secret")


def test_rejects_unknown_prompt_marker():
    bad(lambda d: d["steps"][0]["prompt"].update(en="hello {os_environ}"), "unknown marker")


def test_rejects_newer_format():
    bad(lambda d: d.update(format=2), "newer")


def test_rejects_user_id_equal_to_builtin():
    bad(lambda d: d.update(id="feature"), "built-in")


def test_approval_cannot_be_made_optional():
    bad(lambda d: d["steps"][1].update(requires_user=False), "needs the user")


def test_accepted_keys_per_step_type():
    d = good()
    d["steps"][2]["inputs"] = ["spec", "plan"]
    d["steps"][2]["keep_questions_on_next"] = True
    pipelines.load(d)
    for bad_step in (1,):                               # approval refuses worker-only extensions
        e = copy.deepcopy(good())
        e["steps"][bad_step]["inputs"] = ["spec"]
        with pytest.raises(PipelineError, match="unknown key"):
            pipelines.load(e)
    e = good()
    e["steps"][2]["gates"] = []                         # `gates` belongs to check steps only
    with pytest.raises(PipelineError, match="unknown key"):
        pipelines.load(e)


def test_diff_lists_changes():
    a, b = pipelines.load(good()), good()
    b["steps"][2]["model"] = "opus"
    changes = pipelines.diff(a, pipelines.load(b))
    assert [(c.path, c.new) for c in changes] == [("steps[build].model", "opus")]


# ---- built-in pipelines and their prompts ----
import itertools  # noqa: E402
import re  # noqa: E402

import prompt_refs  # noqa: E402
import prompts_kinds  # noqa: E402
from features import Features  # noqa: E402

KINDS = tuple(prompts_kinds.KIND_STAGES)
pytestmark = pytest.mark.with_concept          # these test the shipped built-in files, concept phase included


@pytest.mark.parametrize("kind", KINDS)
def test_builtins_valid(kind):
    p = pipelines.load(pipelines.builtin_dir() / f"{kind}.json", builtin=True)
    assert p.id == kind and p.builtin
    phases = [s.get("phase") for s in p.steps if s["type"] != "approval"]
    stages = prompts_kinds.KIND_STAGES[kind]
    assert [x for x in phases if x in stages] == list(stages)        # same phases, same order as before


def test_builtins_use_only_documented_keys():
    # the validator refuses unknown keys, so loading every built-in is the proof; they also may not be edited as user files
    assert {e.id for e in pipelines.list_all("/nonexistent") if e.origin == "builtin" and e.valid} == set(KINDS)


def test_markers_cover_builtin_prompts():
    for kind in KINDS:
        for s in pipelines.load(pipelines.builtin_dir() / f"{kind}.json", builtin=True).steps:
            ref = (s.get("prompt") or {}).get("ref")
            if ref:
                assert ref in prompt_refs.REFS, ref
    for P in (prompt_refs.prompts, prompt_refs.prompts_pt):
        texts = [P.COMMON, P.PLANCHECK, P.CONCEPTCHECK, P.CONCEPT_BLOCK, P.CHANGES_BLOCK, P.USER_INSTRUCTIONS, *P.STAGE_TEXT.values(), P.BUILD_RESUME]
        for t in texts:
            assert set(re.findall(r"\{([A-Za-z_]\w*)\}", t)) <= pipelines.MARKERS, t[:60]


def test_user_pipeline_may_not_reuse_builtin_id(tmp_path):
    d = json.loads((pipelines.builtin_dir() / "ship.json").read_text(encoding="utf-8"))
    with pytest.raises(PipelineError, match="built-in"):
        pipelines.load(d)


def test_builtin_publishing_steps_follow_an_approval():
    for kind in ("ship", "release"):
        p = pipelines.load(pipelines.builtin_dir() / f"{kind}.json", builtin=True)
        ids = [s["id"] for s in p.steps]
        last = p.steps[-1]
        assert last["mode"] in pipelines.RISKY_MODES and p.steps[ids.index(last["id"]) - 1]["type"] == "approval"


@pytest.fixture
def feats(tmp_path):
    launched = []

    def launch(project, task, model, dirs, label=None, **kw):
        launched.append(task)
        return {"id": f"w{len(launched)}", "name": "n"}

    return Features(tmp_path, launch, lambda w: True), launched, tmp_path


CASES = [(k, ph) for k in KINDS for ph in prompts_kinds.KIND_STAGES[k]]


@pytest.mark.parametrize("lang,has_vault,feedback,work", list(itertools.product(("en", "pt"), (True, False), ("", "fix this"), (False, True))))
@pytest.mark.parametrize("kind,phase", CASES)
def test_prompt_refs_byte_identical(feats, kind, phase, lang, has_vault, feedback, work):
    fs, _, root = feats
    fs._has_vault = has_vault
    f = {"id": "x-1", "title": "T", "desc": "D", "project": "P", "lang": lang, "kind": kind, "stage": phase, "rev": {phase: 2}}
    d = fs._dir("x-1")
    d.mkdir(parents=True)
    if work:
        (d / "work").mkdir()
    expected = fs._task(f, feedback)
    got = prompt_refs.render(f"builtin:{kind}.{phase}", lang, {"title": "T", "fid": "x-1", "project": "P", "desc": "D", "dir": d,
                             "rev": 2, "feedback": feedback, "note_lang": fs._note_lang, "has_vault": has_vault})
    assert got == expected


@pytest.mark.parametrize("lang", ("en", "pt"))
@pytest.mark.parametrize("kind", [k for k in KINDS if "builtin:%s.plancheck" % k in prompt_refs.REFS])
def test_plancheck_prompt_byte_identical(feats, kind, lang):
    fs, launched, root = feats
    f = {"id": "x-1", "title": "T", "desc": "D", "project": "P", "lang": lang, "kind": kind, "stage": "plan", "status": "awaiting",
         "worker_id": "", "history": [], "rev": {}, "model": ""}
    fs._dir("x-1").mkdir(parents=True)
    fs._start_subcheck([f], f, "plan-validation")
    d = fs._dir("x-1")
    got = prompt_refs.render(f"builtin:{kind}.plancheck", lang, {"title": "T", "fid": "x-1", "project": "P", "desc": "D", "dir": d,
                             "note_lang": fs._note_lang})
    assert got == launched[-1]


def test_unknown_prompt_ref_is_refused(tmp_path):
    with pytest.raises(KeyError):
        prompt_refs.render("builtin:feature.deploy", "en", {})


def test_there_is_no_cost_limit_field():
    """Cost is only accounted for (usage.request_costs), never a limit: the field was removed and is refused like any unknown key."""
    d = json.loads((pipelines.builtin_dir() / "fix.json").read_text(encoding="utf-8"))
    d["id"], d["limits"] = "my-fix", {"max_cost_usd": 5}
    with pytest.raises(pipelines.PipelineError) as e:
        pipelines.validate(d)
    assert "max_cost_usd" in str(e.value)
