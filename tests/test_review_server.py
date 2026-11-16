"""The routes added by the review of 2026-11-14, over HTTP against the demo server (nothing real runs)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_demo_server import DemoServer  # noqa: E402


@pytest.fixture(scope="module")
def ro(tmp_path_factory):
    srv = DemoServer(tmp_path_factory.mktemp("review-ro"))
    yield srv
    srv.stop()


@pytest.fixture
def fresh(tmp_path):
    srv = DemoServer(tmp_path)
    yield srv
    srv.stop()


def test_the_environment_check_needs_the_token_and_lists_checks(ro):
    assert ro.get("/api/doctor", token=False)[0] == 403
    status, out = ro.get("/api/doctor")
    assert status == 200 and {"Python", "git", "Data folder"} <= {c["name"] for c in out["checks"]}
    assert all(c["level"] in ("ok", "warn", "fail") for c in out["checks"])


def test_the_cards_are_left_out_while_the_page_already_has_them(ro):
    s = ro.state()
    assert s["features"] and len(s["features_hash"]) == 16
    same = ro.get(f"/api/state?fh={s['features_hash']}")[1]
    assert same["features"] is None and same["features_hash"] == s["features_hash"] and same["workers"]
    assert ro.get("/api/state?fh=0000000000000000")[1]["features"]


def test_the_server_announces_itself_for_the_command_line(ro):
    files = list(ro.dir.rglob("server.json"))
    assert len(files) == 1
    info = json.loads(files[0].read_text(encoding="utf-8"))
    assert info["port"] == ro.port and info["token"] == ro.token


def test_every_request_knows_its_projects_folder_after_start(ro):
    assert all(f.get("path") for f in ro.state()["features"])


def test_a_card_under_way_shows_the_size_of_its_change(ro):
    f = ro.feature("CSV export of trips")
    assert f["stage"] in ("build", "review") and f["diff"]["files"] == 6


def test_a_follow_up_request_shows_what_it_follows(fresh):
    parent = fresh.feature("CSV export of trips")
    status, out = fresh.post("/api/features", {"project": parent["project"], "title": "CSV export, part two", "desc": "The rest.",
                                               "parent": parent["id"]})
    assert status == 200 and out["parent"] == parent["id"] and out["path"]
    assert fresh.feature("CSV export, part two")["parent_title"] == "CSV export of trips"
    bad = fresh.post("/api/features", {"project": parent["project"], "title": "x", "desc": "y", "parent": "nope-1"})
    assert bad == (400, {"error": "the request this one follows was not found"})


def test_the_kind_only_changes_on_a_first_phase_that_waits(fresh):
    status, out = fresh.post("/api/features", {"project": "Dotfiles", "title": "Aliases", "desc": "Group them."})
    assert status == 200
    assert fresh.post(f"/api/features/{out['id']}/kind", {"kind": "fix"}) == (
        400, {"error": "the kind can only be changed while the request waits on its first phase"})
    assert fresh.post(f"/api/features/{out['id']}/kind", {"kind": "ship"})[1] == {"error": "this kind of request cannot be converted"}


def test_a_cancelled_request_is_cleaned_up_and_an_open_one_is_not(fresh):
    f = fresh.feature("Why is the nightly sync slow?")
    assert fresh.post(f"/api/features/{f['id']}/cleanup", {"force": True}) == (
        400, {"error": "only a finished or cancelled request can be cleaned up"})
    fresh.post(f"/api/features/{f['id']}/cancel")
    status, out = fresh.post(f"/api/features/{f['id']}/cleanup", {"force": True})
    assert status == 200 and out["history"][-1]["event"] == "cleaned up"


def test_a_project_with_open_requests_is_not_removed(fresh):
    f = fresh.feature("CSV export of trips")
    status, out = fresh.post("/api/settings/project_remove", {"name": f["project"]})
    assert status == 400 and "open request(s)" in out["error"]
    assert f["project"] in [p["name"] for p in fresh.state()["projects"]]


def test_the_session_scan_reads_nothing_real_in_the_demo(fresh):
    assert fresh.post("/api/pending/scan") == (200, {"sessions": []})
    assert fresh.post("/api/pending/add", {"project": "Dotfiles", "session": "1aaaaaaa"}) == (400, {"error": "session not found"})


def test_errors_of_the_new_routes_are_translated(fresh):
    f = fresh.feature("CSV export of trips")
    status, out = fresh.post(f"/api/features/{f['id']}/cleanup", {}, headers={"X-Lang": "pt"})
    assert status == 400 and out == {"error": "só se pode limpar um pedido concluído ou cancelado"}
