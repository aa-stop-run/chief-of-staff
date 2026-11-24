"""/api/pipelines over HTTP against the demo server (a throw-away home; nothing real is touched)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pipelines  # noqa: E402
from test_demo_server import DemoServer  # noqa: E402


@pytest.fixture
def srv(tmp_path):
    s = DemoServer(tmp_path)
    yield s
    s.stop()


def mine(pid="my-flow", base="maintenance"):
    d = json.loads((pipelines.builtin_dir() / f"{base}.json").read_text(encoding="utf-8"))
    d["id"], d["name"], d["kind"] = pid, "My flow", base
    return d


def test_list_has_user_entries_first_the_builtins_usage_and_the_catalog(srv):
    assert srv.post("/api/pipelines", {"data": mine()})[0] == 200
    status, out = srv.get("/api/pipelines")
    assert status == 200
    rows = out["pipelines"]
    assert rows[0]["id"] == "my-flow" and rows[0]["origin"] == "user" and rows[0]["valid"] is True and rows[0]["used"] == 0
    assert {r["id"] for r in rows if r["origin"] == "builtin"} == set(pipelines.BUILTIN_IDS)
    cat = out["catalog"]
    assert "worktree" in cat["modes"] and {"worker", "check", "approval"} <= set(cat["types"])
    assert "builtin:feature.spec" in cat["prompt_refs"] and cat["gates"] and "approved" in cat["verdicts"]
    assert out["home_hint"]


def test_builtins_in_use_are_counted(srv):
    used = {r["id"]: r["used"] for r in srv.get("/api/pipelines")[1]["pipelines"]}
    assert used["feature"] >= 1                           # the demo has feature requests


def test_get_one(srv):
    srv.post("/api/pipelines", {"data": mine()})
    status, one = srv.get("/api/pipelines/my-flow")
    assert status == 200 and one["origin"] == "user" and one["data"]["name"] == "My flow" and one["valid"] and one["digest"]
    assert srv.get("/api/pipelines/feature")[1]["origin"] == "builtin"
    assert srv.get("/api/pipelines/ghost")[0] == 404


def test_create_update_delete_round_trip(srv):
    assert srv.post("/api/pipelines", {"data": mine()})[0] == 200
    d = mine()
    d["name"] = "Renamed"
    status, out = srv.post("/api/pipelines/my-flow/update", {"data": d})
    assert status == 200 and out["valid"] is True
    assert srv.get("/api/pipelines/my-flow")[1]["data"]["name"] == "Renamed"
    assert srv.post("/api/pipelines/my-flow/delete", {})[0] == 200
    assert srv.get("/api/pipelines/my-flow")[0] == 404


def test_create_fork_records_where_it_came_from(srv):
    d = json.loads((pipelines.builtin_dir() / "fix.json").read_text(encoding="utf-8"))
    d["id"] = "my-fix"
    assert srv.post("/api/pipelines", {"data": d, "forked_from": "fix"})[0] == 200
    assert srv.get("/api/pipelines/my-fix")[1]["data"]["based_on"] == "fix"


def test_create_409_when_the_id_is_taken_and_422_when_invalid(srv):
    srv.post("/api/pipelines", {"data": mine()})
    status, out = srv.post("/api/pipelines", {"data": mine()})
    assert status == 409 and "already" in out["error"]
    bad = mine("other-flow")
    bad["steps"][0]["mode"] = "teleport"
    status, out = srv.post("/api/pipelines", {"data": bad})
    assert status == 422 and "mode" in out["error"]
    assert srv.get("/api/pipelines/other-flow")[0] == 404


def test_a_builtin_cannot_be_updated_deleted_or_overwritten(srv):
    assert srv.post("/api/pipelines/feature/update", {"data": mine("feature")})[0] == 403
    assert srv.post("/api/pipelines/feature/delete", {})[0] == 403
    d = mine("feature")
    assert srv.post("/api/pipelines", {"data": d})[0] == 403


def test_delete_is_refused_while_requests_use_it(srv):
    d = json.loads((pipelines.builtin_dir() / "feature.json").read_text(encoding="utf-8"))
    d["id"], d["name"] = "my-feature", "My feature"
    srv.post("/api/pipelines", {"data": d, "forked_from": "feature"})
    # a request created with the pipeline: the demo's home keeps requests in features.json
    home = srv.dir / "home" / "state" / "features.json"
    data = json.loads(home.read_text(encoding="utf-8"))
    data[0]["pipeline"] = {"id": "my-feature", "version": 1, "hash": "x"}
    home.write_text(json.dumps(data), encoding="utf-8")
    status, out = srv.post("/api/pipelines/my-feature/delete", {})
    assert status == 409 and out["count"] == 1 and "1 request" in out["error"]


def test_writes_need_the_token_and_the_origin_and_reads_need_the_token(srv):
    assert srv.post("/api/pipelines", {"data": mine()}, token=False)[0] == 403
    assert srv.post("/api/pipelines", {"data": mine()}, origin=False)[0] == 403
    assert srv.get("/api/pipelines", token=False)[0] == 403
    assert srv.get("/api/pipelines/feature", token=False)[0] == 403
    assert srv.get("/api/pipelines")[0] == 200


def test_the_error_is_translated_on_request(srv):
    srv.post("/api/pipelines", {"data": mine()})
    status, out = srv.post("/api/pipelines", {"data": mine()}, headers={"X-Lang": "pt"})
    assert status == 409 and "já existe" in out["error"]


def lean(pid="lean-maint"):
    d = mine(pid, "maintenance")
    d["steps"] = [s for s in d["steps"] if s["id"] != "plancheck"]
    return d


def test_a_request_can_be_created_from_your_pipeline_and_its_card_brings_the_phases(srv):
    assert srv.post("/api/pipelines", {"data": lean()})[0] == 200
    status, f = srv.post("/api/features", {"project": "Dotfiles", "title": "Tidy up", "desc": "tidy", "pipeline": "lean-maint"})
    assert status == 200 and f["kind"] == "maintenance" and f["pipeline"]["id"] == "lean-maint"
    card = next(c for c in srv.get("/api/state")[1]["features"] if c["id"] == f["id"])
    assert card["pipeline_name"] == "My flow" and card["stages"] == ["plan", "build", "review"]


def test_a_builtin_request_card_has_no_pipeline_name(srv):
    status, f = srv.post("/api/features", {"project": "Dotfiles", "title": "Tidy", "desc": "tidy", "kind": "feature"})
    card = next(c for c in srv.get("/api/state")[1]["features"] if c["id"] == f["id"])
    assert "pipeline_name" not in card and "stages" not in card


def test_a_pipeline_that_does_not_exist_or_cannot_start_a_request_is_a_400(srv):
    assert srv.post("/api/features", {"project": "Dotfiles", "title": "x", "desc": "y", "pipeline": "ghost"})[0] == 400
    status, out = srv.post("/api/features", {"project": "Dotfiles", "title": "x", "desc": "y", "pipeline": "feature"})
    assert status == 400 and "built-in" in out["error"]


def test_the_trust_alert_route_is_closed_in_the_demo_and_the_state_lists_none(srv):
    assert srv.get("/api/state")[1]["trust"] == []
    status, out = srv.post("/api/trust/grant", {"path": "D:/Vault"})
    assert status == 400 and "demo" in out["error"]
    assert srv.post("/api/trust/grant", {"path": "D:/Vault"}, token=False)[0] == 403
