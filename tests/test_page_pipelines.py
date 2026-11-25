"""The pure helpers of the Pipelines page (the `// <pipelines>` region of app/index.html), run with node."""
import json
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




# ------------------------------------------------------------------ what the list and the details show

def test_summary_lines():
    d = builtin("feature")
    lines = [call(f"pipeStepLine({json.dumps(s)},t)") for s in d["steps"]]
    assert lines[0].startswith("spec") and "worker" in lines[0] and "read_only" in lines[0]
    assert any("approval" in ln and "spec" in ln for ln in lines)
    assert any("check" in ln for ln in lines)


def test_row_text_shows_origin_validity_and_use():
    ok = {"id": "my-flow", "origin": "user", "valid": True, "reason": "", "used": 2}
    assert call(f"pipeRowFlags({json.dumps(ok)},t)") == {"badge": "Custom", "state": "valid", "note": "2 request(s)", "canEdit": True, "canDelete": True, "canFork": False}
    bad = {"id": "x", "origin": "user", "valid": False, "reason": "mode: nope", "used": 0}
    assert call(f"pipeRowFlags({json.dumps(bad)},t)")["state"] == "invalid" and "mode: nope" in call(f"pipeRowFlags({json.dumps(bad)},t)")["note"]
    b = {"id": "fix", "origin": "builtin", "valid": True, "reason": "", "used": 0}
    assert call(f"pipeRowFlags({json.dumps(b)},t)") == {"badge": "Built-in", "state": "valid", "note": "", "canEdit": False, "canDelete": False, "canFork": True}


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












def test_only_valid_usable_pipelines_of_the_user_are_offered_for_a_request():
    rows = [{"id": "a-flow", "origin": "user", "valid": True, "runnable": True, "kind": "fix", "name": "Quick fix"},
            {"id": "b-flow", "origin": "user", "valid": True, "runnable": False, "kind": "", "name": "Old"},
            {"id": "c-flow", "origin": "user", "valid": False, "runnable": False, "kind": "fix", "name": ""},
            {"id": "fix", "origin": "builtin", "valid": True, "runnable": False, "kind": "", "name": ""}]
    assert call(f"pipeChoices({json.dumps(rows)})") == [{"id": "a-flow", "label": "Quick fix", "kind": "fix"}]
    assert call("pipeChoices(null)") == []


def test_the_new_request_dialog_has_the_pipeline_menu_and_sends_it():
    assert 'id="nfPipe"' in SRC and "pipeline:$(\"nfPipe\").value||undefined" in SRC and "nfLoadPipes()" in SRC


# ------------------------------------------------------------------ the editor: the type's own phases, nothing that cannot run

def runnable(d):
    return pipelines.runnable(d)


def fresh(kind, name="My flow"):
    return call(f"pipeFromBuiltin({json.dumps(builtin(kind))},{json.dumps(kind)},{json.dumps({'id': 'my-flow', 'name': name})})")


@pytest.mark.parametrize("kind", ("feature", "fix", "maintenance", "analysis"))
def test_a_new_pipeline_of_every_type_is_usable_as_it_comes(kind):
    d = fresh(kind)
    assert d["kind"] == kind and d["id"] == "my-flow" and d["name"] == "My flow" and "based_on" not in d
    assert problems(d) == [] and runnable(d) == []
    pipelines.validate(d)


def test_the_plan_validation_can_be_removed_and_put_back_and_stays_usable():
    base = builtin("feature")
    d = fresh("feature")
    i = next(i for i, s in enumerate(d["steps"]) if s.get("phase") == "plan-validation")
    assert call(f"pipeCanAddCheck({json.dumps(d)},{json.dumps(base)})") is False
    gone = call(f"pipeRemove({json.dumps(d)},{i})")
    assert all(s.get("phase") != "plan-validation" for s in gone["steps"]) and runnable(gone) == [] and problems(gone) == []
    assert call(f"pipeCanAddCheck({json.dumps(gone)},{json.dumps(base)})") is True
    back = call(f"pipeAddCheck({json.dumps(gone)},{json.dumps(base)})")
    assert [s["id"] for s in back["steps"]] == [s["id"] for s in d["steps"]] and runnable(back) == []
    assert call(f"pipeAddCheck({json.dumps(back)},{json.dumps(base)})") == back                # not twice
    assert call(f"pipeCanAddCheck({json.dumps(fresh('analysis'))},{json.dumps(builtin('analysis'))})") is False   # no plan in an analysis


def test_model_effort_label_and_rounds_keep_it_usable():
    d = fresh("maintenance")
    bi = next(i for i, s in enumerate(d["steps"]) if s.get("phase") == "build")
    d = call(f'pipeSetStep({json.dumps(d)},{bi},"model","sonnet")')
    d = call(f'pipeSetStep({json.dumps(d)},{bi},"effort","low")')
    d = call(f'pipeSetStep({json.dumps(d)},{bi},"label","Do the work")')
    ri = next(i for i, s in enumerate(d["steps"]) if s.get("phase") == "review")
    d["steps"][ri]["on"]["blocked"]["max_rounds"] = 1
    assert problems(d) == [] and runnable(d) == []
    pipelines.validate(d)
    cleared = call(f'pipeSetStep({json.dumps(d)},{bi},"model","")')
    assert "model" not in cleared["steps"][bi]


def test_flow_and_slug():
    flow = call(f"pipeFlow({json.dumps(builtin('feature'))},t)")
    assert flow.startswith("spec → ✋") and "plan" in flow and flow.endswith("✋ your approval")
    assert call("pipeSlug('Minha Pipeline Ótima!')") == "minha-pipeline-otima"


def test_a_pipeline_that_cannot_start_a_request_says_so_in_the_list():
    r = {"id": "old", "origin": "user", "valid": True, "reason": "", "used": 0, "runnable": False}
    assert "Cannot be chosen" in call(f"pipeRowFlags({json.dumps(r)},t)")["note"]
    r["runnable"] = True
    assert call(f"pipeRowFlags({json.dumps(r)},t)")["note"] == ""
