"""The pure helpers of the Pipelines page (the `// <pipelines>` region of app/index.html), run with node."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gates  # noqa: E402
import pipelines  # noqa: E402
import prompt_refs  # noqa: E402

HTML = ROOT / "app" / "index.html"
SRC = HTML.read_text(encoding="utf-8")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
T = "const t=(s,v)=>(v?s.replace(/\\{(\\w+)\\}/g,(m,k)=>k in v?v[k]:m):s);"
CAT = {"modes": list(pipelines.MODES), "risky_modes": list(pipelines.RISKY_MODES), "types": ["worker", "check", "approval"],
       "gates": sorted(gates.GATES), "prompt_refs": list(prompt_refs.REFS), "verdicts": list(pipelines.VERDICTS),
       "input_names": list(pipelines.INPUT_NAMES), "end": pipelines.END, "max_steps": pipelines.MAX_STEPS, "max_rounds": pipelines.MAX_ROUNDS,
       "id_pattern": pipelines.PIPELINE_ID.pattern, "step_pattern": pipelines.STEP_ID.pattern}


def call(expr: str):
    code = SRC[SRC.index("// <routes>"):SRC.index("// </routes>")] + "\n" + SRC[SRC.index("// <pipelines>"):SRC.index("// </pipelines>")] + f"\n{T}\nconst CAT={json.dumps(CAT)};\nconsole.log(JSON.stringify({expr}))"
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def builtin(pid):
    return json.loads((pipelines.builtin_dir() / f"{pid}.json").read_text(encoding="utf-8"))


def problems(data):
    return call(f"pipeProblems({json.dumps(data)},CAT)")


def mine(pid="my-flow", base="maintenance"):
    d = builtin(base)
    d["id"] = pid
    return d


# ------------------------------------------------------------------ routes

def test_routes():
    assert call('parseRoute("#/pipelines")') == {"view": "pipelines"}
    assert call('parseRoute("#/pipelines/")') == {"view": "pipelines"}
    assert call('parseRoute("#/pipelines/new")') == {"view": "pipelines", "id": "", "new": True}
    assert call('parseRoute("#/pipelines/my-flow")') == {"view": "pipelines", "id": "my-flow"}
    assert call('parseRoute("#/pipelines/my-flow/edit")') == {"view": "pipelines", "id": "my-flow", "edit": True}
    assert call('parseRoute("#/projects")') == {"view": "projects"}
    for r in ({"view": "pipelines"}, {"view": "pipelines", "id": "", "new": True}, {"view": "pipelines", "id": "a-b"}, {"view": "pipelines", "id": "a-b", "edit": True}):
        assert call(f"parseRoute(routeHash({json.dumps(r)}))") == r


# ------------------------------------------------------------------ the client-side check agrees with the engine's validator

@pytest.mark.parametrize("pid", sorted(pipelines.BUILTIN_IDS))
def test_every_builtin_has_no_client_problems_once_it_has_a_user_id(pid):
    d = builtin(pid)
    d["id"] = "copy-of-" + pid
    assert problems(d) == []


def test_client_and_server_agree_on_known_bad_pipelines():
    cases = []
    d = mine(); d["id"] = "Bad_Id"; cases.append(d)
    d = mine(); d["steps"] = []; cases.append(d)
    d = mine(); d["steps"][0]["mode"] = "teleport"; cases.append(d)
    d = mine(); d["steps"][1]["id"] = d["steps"][0]["id"]; cases.append(d)
    d = mine(); d["steps"][0]["prompt"] = {}; cases.append(d)
    d = mine(); d["steps"][0]["next"] = "ghost"; cases.append(d)
    d = builtin("ship"); d["id"] = "my-ship"; d["steps"][0]["mode"] = "publish"; cases.append(d)        # publish as the first step
    for d in cases:
        with pytest.raises(pipelines.PipelineError):
            pipelines.validate(d)
        assert problems(d), d["id"]
    short = mine("ab")                                                  # the store (not the validator) wants at least 3 characters
    assert problems(short) and any("3 to 40" in p for p in problems(short))


def test_publish_needs_an_approval_right_before_it():
    d = builtin("ship")
    d["id"] = "my-ship"
    assert problems(d) == []
    i = next(i for i, s in enumerate(d["steps"]) if s.get("mode") == "publish")
    del d["steps"][i - 1]                                           # the approval before it goes away
    assert any("approval" in p for p in problems(d))


def test_going_back_needs_max_rounds():
    d = builtin("feature")
    d["id"] = "my-feat"
    step = next(s for s in d["steps"] if s["type"] == "check")
    step["on"]["blocked"] = {"goto": d["steps"][0]["id"]}
    assert any("max_rounds" in p for p in problems(d))


# ------------------------------------------------------------------ editing (pure: always a new object)

def test_blank_pipeline_has_a_worker_and_its_approval_and_problems_to_fix():
    d = call("pipeBlank()")
    assert d["format"] == 1 and [s["type"] for s in d["steps"]] == ["worker", "approval"] and d["steps"][1]["of"] == d["steps"][0]["id"]
    assert problems(d)                                              # no id, no prompt yet


def test_add_move_remove_steps_never_mutate_and_keep_ids_unique():
    d = mine()
    n = len(d["steps"])
    added = call(f'pipeAddStep({json.dumps(d)},"worker")')
    assert len(added["steps"]) == n + 1 and len(d["steps"]) == n
    added2 = call(f'pipeAddStep({json.dumps(added)},"worker")')
    ids = [s["id"] for s in added2["steps"]]
    assert len(set(ids)) == len(ids)
    moved = call(f"pipeMove({json.dumps(d)},1,-1)")
    assert [s["id"] for s in moved["steps"][:2]] == [d["steps"][1]["id"], d["steps"][0]["id"]]
    assert call(f"pipeMove({json.dumps(d)},0,-1)") == d                    # cannot go above the first
    assert call(f"pipeMove({json.dumps(d)},{n - 1},1)") == d                # nor below the last
    gone = call(f"pipeRemove({json.dumps(d)},0)")
    assert len(gone["steps"]) == n - 2                                       # the step and the approval of it


def test_removing_or_renaming_a_step_repoints_what_named_it():
    d = builtin("maintenance")
    d["id"] = "my-m"
    first = d["steps"][0]["id"]
    ren = call(f'pipeRenameStep({json.dumps(d)},0,"renamed")')
    assert ren["steps"][0]["id"] == "renamed"
    assert all(s.get("of") != first for s in ren["steps"]) and any(s.get("of") == "renamed" for s in ren["steps"] if s["type"] == "approval")
    gone = call(f"pipeRemove({json.dumps(d)},0)")
    assert all(s.get("of") != first for s in gone["steps"] if s["type"] == "approval")


def test_set_step_field_and_unknown_keys_survive():
    d = mine()
    d["steps"][0]["permissions"] = {"allow": ["Bash(ls:*)"]}
    out = call(f'pipeSetStep({json.dumps(d)},0,"artifact","other.md")')
    assert out["steps"][0]["artifact"] == "other.md" and out["steps"][0]["permissions"] == {"allow": ["Bash(ls:*)"]}
    cleared = call(f'pipeSetStep({json.dumps(d)},0,"artifact","")')
    assert "artifact" not in cleared["steps"][0]


def test_changing_a_step_type_keeps_the_shared_keys_and_drops_the_rest():
    d = mine()
    out = call(f'pipeSetType({json.dumps(d)},0,"approval")')
    s = out["steps"][0]
    assert s["type"] == "approval" and s["id"] == d["steps"][0]["id"] and "mode" not in s and "prompt" not in s and s["requires_user"] is True


# ------------------------------------------------------------------ what the list and the details show

def test_summary_lines():
    d = builtin("feature")
    lines = [call(f"pipeStepLine({json.dumps(s)},t)") for s in d["steps"]]
    assert lines[0].startswith("spec") and "worker" in lines[0] and "read_only" in lines[0]
    assert any("approval" in ln and "spec" in ln for ln in lines)
    assert any("check" in ln for ln in lines)


def test_row_text_shows_origin_validity_and_use():
    ok = {"id": "my-flow", "origin": "user", "valid": True, "reason": "", "used": 2}
    assert call(f"pipeRowFlags({json.dumps(ok)},t)") == {"badge": "Custom", "state": "valid", "note": "2 request(s)", "canEdit": True, "canDelete": True}
    bad = {"id": "x", "origin": "user", "valid": False, "reason": "mode: nope", "used": 0}
    assert call(f"pipeRowFlags({json.dumps(bad)},t)")["state"] == "invalid" and "mode: nope" in call(f"pipeRowFlags({json.dumps(bad)},t)")["note"]
    b = {"id": "fix", "origin": "builtin", "valid": True, "reason": "", "used": 0}
    assert call(f"pipeRowFlags({json.dumps(b)},t)") == {"badge": "Built-in", "state": "valid", "note": "", "canEdit": False, "canDelete": False}


def test_delete_arms_on_the_second_click():
    assert call("pipeArm(null,'a')") == {"armed": "a", "go": False}
    assert call("pipeArm('a','a')") == {"armed": None, "go": True}
    assert call("pipeArm('a','b')") == {"armed": "b", "go": False}


# ------------------------------------------------------------------ the page itself

def test_the_page_wires_the_tab_the_view_and_portuguese():
    assert 'id="tabPipelines"' in SRC and 'id="pipelinesView"' in SRC and "renderPipelines" in SRC
    for needle in ('"Pipelines":"Pipelines"', '"Built-in":"Incluída"', '"Custom":"Própria"', '"New pipeline":"Nova pipeline"'):
        assert needle in SRC


# ------------------------------------------------------------------ the friendly editor: blocks in plain words, the approval as a tick box

def named(d, pid="my-flow"):
    d["id"], d["name"] = pid, "My flow"
    return d


def test_every_block_makes_a_pipeline_the_server_accepts():
    d = call("pipeBlank()")
    for kind in ("read", "change", "review", "review", "read", "change"):
        d = call(f"pipeBlock({json.dumps(d)},{json.dumps(kind)},'Step')")
    d = named(d)
    assert problems(d) == []
    pipelines.validate(d)
    phases = [s["phase"] for s in d["steps"] if s["type"] != "approval"]
    assert len(set(phases)) == len(phases)


def test_a_review_block_goes_back_to_the_worker_before_it_a_bounded_number_of_times():
    d = call(f'pipeBlock({json.dumps(call("pipeBlank()"))},"review","R")')
    rev = d["steps"][-1]
    assert rev["type"] == "check" and rev["on"]["blocked"] == {"goto": "work", "max_rounds": 2}


def test_change_the_kind_keeps_the_pipeline_valid():
    d = named(call("pipeBlank()"))
    d["steps"][0]["prompt"] = {"ref": "builtin:feature.spec"}
    for kind in ("change", "review", "read"):
        d = call(f"pipeSetKind({json.dumps(d)},0,{json.dumps(kind)})")
        assert call(f"pipeKind({json.dumps(d['steps'][0])})") == kind
        pipelines.validate(d) if kind != "change" else None


def test_the_approval_is_a_tick_box_and_moves_with_its_step():
    d = named(builtin("maintenance"))
    first = d["steps"][0]["id"]
    at = call(f"pipeApprovalAt({json.dumps(d)},0)")
    assert d["steps"][at]["of"] == first and d["steps"][1]["type"] == "check"      # the review sits between the step and its approval
    off = call(f"pipeSetApproval({json.dumps(d)},0,false)")
    assert not call(f"pipeHasApproval({json.dumps(off)},0)") and all(s.get("of") != first for s in off["steps"])
    on = call(f"pipeSetApproval({json.dumps(off)},0,true)")
    assert call(f"pipeApprovalAt({json.dumps(on)},0)") == at and on == d
    groups = call(f"pipeGroups({json.dumps(d)})")
    assert groups[0] == [0, at]
    start = groups[1][0]
    moved = call(f"pipeMoveBlock({json.dumps(d)},{start},-1)")
    assert moved["steps"][0]["id"] == d["steps"][start]["id"]                          # the next unit went first, whole
    assert sorted(s["id"] for s in moved["steps"]) == sorted(s["id"] for s in d["steps"])
    assert call(f"pipeMoveBlock({json.dumps(d)},0,-1)") == d


def test_flow_sentence_and_names():
    d = builtin("feature")
    flow = call(f"pipeFlow({json.dumps(d)},t)")
    assert flow.startswith("spec → ✋") and "plan" in flow
    assert call("pipePromptName('builtin:feature.spec',t)") == "Feature@k · write the spec@p"
    assert call("pipeSlug('Minha Pipeline Ótima!')") == "minha-pipeline-otima"
