"""The engine drives `Features` (plan T5/T7): requests keep their pipeline as data, the code has no branch per request type,
the fixed denials reach the launch, and data from before pipelines were files is migrated and keeps advancing."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "tools"))
import chief  # noqa: E402
import engine  # noqa: E402
import migrations  # noqa: E402
import pipelines  # noqa: E402
from capture_golden import Recorder  # noqa: E402
from features import Features  # noqa: E402

BLOCKED = "## Verdict\nBLOCKED — a problem.\n## Findings\n- **[HIGH] x.py — bad:** it is wrong\n"
KINDS = ("feature", "fix", "maintenance", "analysis", "ship", "release")


def make(tmp_path, **kw):
    rec = Recorder()
    fs = Features(tmp_path, rec.launch, rec.is_active, auto_review=True, **kw)
    return fs, rec


def finish(fs, rec, tmp_path, fid, name, text):
    rec.stop_all()
    d = tmp_path / "features" / fid
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    fs.list()


def test_features_has_no_branch_per_request_type():
    text = (ROOT / "scripts" / "features.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    branches = [r'get\("kind"[^)]*\)\s*(==|!=|in |not in )', r'\["kind"\]\s*(==|!=|in |not in )', r"(?<![.\w])kind\s*(==|!=)"]
    assert not [b for b in branches if re.search(b, code)], "a branch on the request type is back"
    assert "KIND_STAGES" not in code


@pytest.mark.parametrize("kind", KINDS)
def test_a_new_request_keeps_its_pipeline_as_data(tmp_path, kind):
    fs, _ = make(tmp_path)
    f = fs.create("Alfa", "Title", "Desc", None, kind=kind)
    p = pipelines.load(pipelines.builtin_dir() / f"{kind}.json", builtin=True)
    assert f["pipeline"] == {"id": kind, "version": p.data["version"], "hash": pipelines.digest(p)}
    assert f["step"] == p.steps[0]["id"] and f["stage"] == p.steps[0]["phase"] and f["rounds"] == {}
    snap = json.loads((tmp_path / "features" / f["id"] / "pipeline.json").read_text(encoding="utf-8"))
    assert snap == pipelines.snapshot(p)


def test_the_request_follows_its_snapshot_not_the_builtin_file(tmp_path):
    fs, rec = make(tmp_path)
    f = fs.create("Alfa", "Title", "Desc", None, kind="analysis")
    fid, snap_path = f["id"], tmp_path / "features" / f["id"] / "pipeline.json"
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    for s in snap["steps"]:
        if s["id"] == "review":
            s["on"]["blocked"]["max_rounds"] = 1
    snap_path.write_text(json.dumps(snap), encoding="utf-8")
    fs2, rec2 = make(tmp_path)      # a fresh instance reads the edited snapshot
    rec2.active = rec.active
    finish(fs2, rec2, tmp_path, fid, "report.md", "report")          # the review starts by itself
    finish(fs2, rec2, tmp_path, fid, "review.md", BLOCKED)           # round 1 goes back to the report
    finish(fs2, rec2, tmp_path, fid, "report.md", "report v2")
    finish(fs2, rec2, tmp_path, fid, "review.md", BLOCKED)           # no second round: one was the limit
    cur = next(x for x in fs2.list() if x["id"] == fid)
    assert cur["status"] == "awaiting" and cur["rounds"] == {"review:blocked": 1}
    assert cur["history"][-1]["event"] == "automatic fixes limit" and "1 rounds" in cur["history"][-1]["note"]


def test_rounds_are_mirrored_to_the_older_counters(tmp_path):
    fs, rec = make(tmp_path)
    f = fs.create("Alfa", "Title", "Desc", None, kind="maintenance")
    fid = f["id"]
    finish(fs, rec, tmp_path, fid, "plan.md", "plan")
    finish(fs, rec, tmp_path, fid, "plan-review.md", BLOCKED)
    cur = next(x for x in fs.list() if x["id"] == fid)
    assert cur["rounds"] == {"plancheck:blocked": 1} and cur["pv"]["fixes"] == 1 and cur["step"] == "plan"


def test_fixed_denials_reach_every_launch_with_the_step_callables(tmp_path):
    fs, rec = make(tmp_path, route_step=chief.route_step, tools_for_step=chief.tools_for_step)
    fs.create("Alfa", "Title", "Desc", None, kind="feature")
    assert set(engine.FIXED_DENY) <= set(rec.calls[-1]["deny"])
    fs2, rec2 = make(tmp_path / "ship", route_step=chief.route_step, tools_for_step=chief.tools_for_step)
    f = fs2.create("Alfa", "Title", "Desc", None, kind="ship")
    finish(fs2, rec2, tmp_path / "ship", f["id"], "report.md", "report")
    assert "Bash(git add:*)" in rec2.calls[0]["allow"] and set(engine.FIXED_DENY) <= set(rec2.calls[0]["deny"])


def test_the_legacy_callables_are_used_when_no_step_callables_are_given(tmp_path):
    seen = []
    fs, rec = make(tmp_path, route=lambda phase, model="": (seen.append(phase) or "m", None),
                   tools=lambda kind, stage: ((), (f"Bash({kind}-{stage}:*)",)))
    fs.create("Alfa", "Title", "Desc", None, kind="feature")
    assert seen == ["spec"] and rec.calls[0]["deny"] == ["Bash(feature-spec:*)"] and rec.calls[0]["model"] == "m"


def test_the_step_callables_win_over_the_legacy_ones(tmp_path):
    got = []
    fs, rec = make(tmp_path, route=lambda p, m="": ("legacy", None), tools=lambda k, s: ((), ("Bash(legacy:*)",)),
                   route_step=lambda step, m="": (got.append(step["id"]) or "stepped", "low"),
                   tools_for_step=lambda step, kind="": ((), ("Bash(stepped:*)",)))
    fs.create("Alfa", "Title", "Desc", None, kind="release")
    assert got == ["plan"] and rec.calls[0]["model"] == "stepped" and rec.calls[0]["effort"] == "low"
    assert rec.calls[0]["deny"] == ["Bash(stepped:*)"]


def test_the_server_wires_the_step_callables():
    text = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
    assert "route_step=chief.route_step" in text and "tools_for_step=chief.tools_for_step" in text


def _old_request(kind, stage, **extra):
    base = {"id": f"old-{kind}-{stage}", "title": "Old", "desc": "d", "project": "Alfa", "model": "", "lang": "en", "kind": kind,
            "stage": stage, "status": "awaiting", "worker_id": "", "worker_name": "", "created": "2026-11-01T10:00:00",
            "history": [], "rev": {"spec": 0, "plan": 0, "build": 0, "review": 0}, "auto": {"fixes": 0, "retries": 0}}
    base.update(extra)
    return base


def test_migration_gives_old_requests_a_pipeline_a_step_and_rounds(tmp_path):
    old = [_old_request("feature", "spec"), _old_request("fix", "review", auto={"fixes": 2, "retries": 1}),
           _old_request("maintenance", "plan", pv={"checking": True, "fixes": 1, "retries": 2, "rev": 1}),
           _old_request("analysis", "build"), _old_request("ship", "build"), _old_request("release", "review"),
           _old_request("odd", "plan")]
    (tmp_path / "features.json").write_text(json.dumps(old), encoding="utf-8")
    migrations.to_schema_3(tmp_path)
    done = {f["id"]: f for f in json.loads((tmp_path / "features.json").read_text(encoding="utf-8"))}
    assert done["old-fix-review"]["pipeline"]["id"] == "fix" and done["old-fix-review"]["step"] == "review"
    assert done["old-fix-review"]["rounds"] == {"review:blocked": 2, "review:unknown": 1}
    m = done["old-maintenance-plan"]
    assert m["step"] == "plancheck" and m["rounds"] == {"plancheck:blocked": 1, "plancheck:unknown": 2}
    assert done["old-ship-build"]["rounds"] == {} and done["old-odd-plan"]["pipeline"]["id"] == "feature"
    for fid, f in done.items():
        snap = json.loads((tmp_path / "features" / fid / "pipeline.json").read_text(encoding="utf-8"))
        assert snap["id"] == f["pipeline"]["id"]
    before = (tmp_path / "features.json").read_text(encoding="utf-8")
    migrations.to_schema_3(tmp_path)
    assert (tmp_path / "features.json").read_text(encoding="utf-8") == before


def test_migration_copes_with_no_data(tmp_path):
    migrations.to_schema_3(tmp_path)
    (tmp_path / "features.json").write_text("not json", encoding="utf-8")
    migrations.to_schema_3(tmp_path)
    assert (tmp_path / "features.json").read_text(encoding="utf-8") == "not json"


@pytest.mark.parametrize("migrate", (True, False))
def test_an_old_request_in_the_middle_of_a_phase_keeps_advancing(tmp_path, migrate):
    old = _old_request("feature", "review", status="working", worker_id="w9", auto={"fixes": 0, "retries": 0})
    (tmp_path / "features.json").write_text(json.dumps([old]), encoding="utf-8")
    if migrate:
        migrations.to_schema_3(tmp_path)
    fs, rec = make(tmp_path)
    rec.active.add("w9")
    finish(fs, rec, tmp_path, old["id"], "review.md", BLOCKED)       # a blocked review sends the findings back to the build
    cur = next(x for x in fs.list() if x["id"] == old["id"])
    assert cur["stage"] == "build" and cur["status"] == "working" and cur["rounds"] == {"review:blocked": 1}
    assert cur["auto"]["fixes"] == 1


def test_newer_data_is_still_refused_by_home(tmp_path):
    assert migrations.CURRENT == 3 and migrations.pending(2)[0].name == "Pipelines as data"
