"""The demo server, tested over HTTP like a user's browser would use it: pages, security, the state it serves, work sessions,
costs, and every kind of action (each action that changes state gets a server of its own, so the tests stay independent)."""
import http.client
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import demo  # noqa: E402
import report  # noqa: E402
import usage  # noqa: E402

TODAY, YESTERDAY, BEFORE = demo.SESSION_NOW, demo.SESSION_YESTERDAY, demo.SESSION_BEFORE


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DemoServer:
    """`python app/server.py --demo` on a free port, with its sample folder inside the test's temp dir."""

    def __init__(self, tmp_path: Path, mode_args=("--demo",)):
        self.port = free_port()
        self.dir = tmp_path / "demo-world"
        env = {**__import__("os").environ, "CHIEF_DEMO_DIR": str(self.dir)}
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "app" / "server.py"), *mode_args, "--port", str(self.port)], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.token = None
        for _ in range(80):
            try:
                _, html = self.request("GET", "/", token=False)
                self.token = re.search(r'const T="([^"]+)"', html).group(1)
                break
            except OSError:
                time.sleep(0.25)
        assert self.token, self.proc.poll()

    # ---- plumbing
    def request(self, method, path, body=None, token=True, origin=True, headers=None, raw=None, ctype="application/json", host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        data = raw if raw is not None else (json.dumps(body if body is not None else {}).encode("utf-8") if method == "POST" else None)
        if method == "POST":
            h["Content-Type"] = ctype
            if token:
                h["X-Token"] = self.token
            if origin:
                h["Origin"] = f"http://127.0.0.1:{self.port}"
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        text = r.read().decode("utf-8")
        status = r.status
        self.last_headers = dict(r.getheaders())
        conn.close()
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text

    def get(self, path, token=True, **kw):
        sep = "&" if "?" in path else "?"
        return self.request("GET", path + (f"{sep}t={self.token}" if token else ""), token=False, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)

    def state(self):
        status, s = self.get("/api/state")
        assert status == 200
        return s

    def feature(self, title):
        return next(f for f in self.state()["features"] if f["title"] == title)

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="module")
def ro(tmp_path_factory):
    """One shared server for the tests that only read."""
    srv = DemoServer(tmp_path_factory.mktemp("ro"))
    yield srv
    srv.stop()


@pytest.fixture
def fresh(tmp_path):
    """A server of its own, for a test that changes things."""
    srv = DemoServer(tmp_path)
    yield srv
    srv.stop()


@pytest.fixture(scope="module")
def expected(tmp_path_factory):
    """What the sample data must add up to, worked out independently from the files the demo wrote."""
    home = demo.seed(tmp_path_factory.mktemp("expected"))
    ledger = json.loads((home / "state" / "usage.json").read_text(encoding="utf-8"))["workers"]
    return {"ledger": ledger, "today": usage.aggregate(ledger, session=TODAY), "yesterday": report.totals_now(ledger, YESTERDAY),
            "before": report.totals_now(ledger, BEFORE)}


# ---------------------------------------------------------------- pages and security
def test_the_page_is_served_with_a_fresh_token_and_safe_headers(ro):
    status, html = ro.request("GET", "/", token=False)
    assert status == 200 and "__TOKEN__" not in html and f'const T="{ro.token}"' in html
    assert "default-src 'self'" in ro.last_headers["Content-Security-Policy"] and ro.last_headers["X-Content-Type-Options"] == "nosniff"
    assert ro.last_headers["Cache-Control"] == "no-store"


def test_the_logo_is_served_and_unknown_paths_are_404(ro):
    status, svg = ro.request("GET", "/logo.svg", token=False)
    assert status == 200 and "<svg" in svg
    assert ro.request("GET", "/nope", token=False)[0] == 404


@pytest.mark.parametrize("path", ["/api/state", "/api/costs", "/api/settings"])
def test_reads_need_the_token(ro, path):
    assert ro.get(path, token=False)[0] == 403
    assert ro.request("GET", path + "?t=wrong", token=False)[0] == 403
    assert ro.get(path)[0] == 200


def test_a_request_for_another_host_is_refused(ro):
    assert ro.request("GET", f"/api/state?t={ro.token}", token=False, host="evil.example")[0] == 403
    assert ro.request("GET", "/", token=False, host="evil.example")[0] == 403


def test_writes_are_refused_without_the_token_the_right_origin_or_a_json_body(ro):
    assert ro.post("/api/session/end", token=False)[1] == {"error": "invalid token; reload the page"}
    assert ro.post("/api/session/end", origin=False)[1] == {"error": "origin not allowed"}
    assert ro.post("/api/session/end", headers={"Origin": "http://evil.example"}, origin=False)[0] == 403
    assert ro.post("/api/session/end", raw=b"x=1", ctype="text/plain")[0] == 415
    assert ro.post("/api/session/end", raw=b"{not json")[1] == {"error": "invalid JSON"}
    assert ro.post("/api/session/end", raw=b"[1, 2]")[1] == {"error": "invalid JSON"}
    assert ro.post("/api/session/end", raw=b"")[0] == 413
    assert ro.post("/api/session/end", raw=b'{"x": "' + b"a" * 20_000 + b'"}')[0] == 413
    assert ro.post("/api/nowhere")[1] == {"error": "unknown route"}


def test_errors_come_back_in_the_language_the_page_asked_for(ro):
    assert ro.post("/api/session/end", token=False, headers={"X-Lang": "pt"})[1] == {"error": "token inválido; recarrega a página"}
    assert ro.post("/api/nowhere", headers={"X-Lang": "pt"})[1] == {"error": "rota desconhecida"}
    assert ro.post("/api/nowhere", headers={"X-Lang": "fr"})[1] == {"error": "unknown route"}


def test_the_page_has_the_session_and_costs_dialog_and_no_clock(ro):
    _, html = ro.request("GET", "/", token=False)
    assert 'id="costD"' in html and "Sessions and costs" in html and "/api/session/end" in html and "/api/costs" in html
    assert "S.updated" not in html and "toLocaleTimeString" not in html


# ---------------------------------------------------------------- the state it serves
def test_the_state_describes_the_demo_world(ro):
    s = ro.state()
    assert s["demo"] is True and s["claude_ok"] is True and s["vault"] is True and s["models"] == ["opus", "sonnet", "haiku"]
    assert {p["name"] for p in s["projects"]} == {h for _, h, _, _ in demo.PROJECTS}
    assert len(s["pending"]) == 4 and "updated" not in s or isinstance(s.get("updated"), str)
    assert s["curation"]["running"] is False


def test_only_open_requests_and_the_ones_closed_in_this_session_are_listed(ro):
    s = ro.state()
    titles = {f["title"]: f["status"] for f in s["features"]}
    assert titles == {"Two-factor login (TOTP)": "awaiting", "Retry failed webhooks": "awaiting", "Cart total is off by one cent": "awaiting",
                      "Why is the nightly sync slow?": "working", "CSV export of trips": "working", "Weekly digest email": "done"}
    assert s["earlier_closed"] == 2                        # "Upgrade pytest to 9" and "Fix the flaky login test" belong to past sessions


def test_the_workers_history_is_this_sessions_only(ro):
    workers = {w["id"]: w for w in ro.state()["workers"]}
    assert workers["demo007"]["state"] == "working" and workers["demo008"]["state"] == "working"
    assert {w["session"] for w in workers.values()} == {TODAY}                   # nothing from a past session
    assert "demo101" not in workers and "demo105" not in workers and "demo001" in workers
    assert workers["demo001"]["state"] == "finished"


def test_the_header_shows_the_open_session_with_its_active_time_and_estimated_cost(ro, expected):
    sess = ro.state()["session"]
    assert sess["id"] == TODAY and sess["active_s"] >= 19000 and sess["started"]
    assert sess["cost_usd"] == pytest.approx(expected["today"]["cost_usd"]) and sess["workers"] == expected["today"]["workers"]
    assert sess["unpriced"] is True and sess["closed"] == 1


def test_models_unknown_to_the_price_list_are_flagged_not_priced_as_free(ro):
    cur = ro.get("/api/costs")[1]["current"]
    assert cur["unpriced_models"] == ["local/qwen3-coder"]
    assert cur["by_model"]["local/qwen3-coder"]["cost_usd"] == 0 and cur["by_model"]["local/qwen3-coder"]["unpriced_tokens"] > 0


# ---------------------------------------------------------------- costs and sessions
def test_the_cost_report_adds_up_with_the_ledger(ro, expected):
    rep = ro.get("/api/costs")[1]
    assert rep["current"]["id"] == TODAY and rep["current"]["cost_usd"] == pytest.approx(expected["today"]["cost_usd"])
    assert sum(m["cost_usd"] for m in rep["current"]["by_model"].values()) == pytest.approx(rep["current"]["cost_usd"])
    assert rep["total"]["cost_usd"] == pytest.approx(usage.aggregate(expected["ledger"])["cost_usd"])
    assert [m["model"] for m in rep["models"]] == sorted((m["model"] for m in rep["models"]), key=lambda x: -next(
        r["cost_usd"] for r in rep["models"] if r["model"] == x))


def test_past_sessions_show_the_totals_frozen_when_they_ended(ro, expected):
    past = ro.get("/api/costs")[1]["sessions"]
    assert [s["id"] for s in past] == [YESTERDAY, BEFORE]                      # newest first
    assert past[0]["cost_usd"] == pytest.approx(expected["yesterday"]["cost_usd"]) and past[1]["cost_usd"] == pytest.approx(expected["before"]["cost_usd"])
    assert past[0]["ended_by"] == "idle" and past[1]["ended_by"] == "user" and past[0]["active_s"] == 14100 and past[1]["active_s"] == 12900
    assert [r["title"] for r in past[0]["requests"]] == ["Upgrade pytest to 9"] and [r["title"] for r in past[1]["requests"]] == ["Fix the flaky login test"]
    assert all("T" in s["started"] and s["ended"] for s in past)


def test_the_requests_a_session_closed_belong_to_it(ro):
    cur = ro.get("/api/costs")[1]["current"]
    assert [r["title"] for r in cur["requests"]] == ["Weekly digest email"] and cur["requests"][0]["status"] == "done"


def test_cost_per_project_is_ranked_and_lists_models_and_sessions(ro, expected):
    projects = ro.get("/api/costs")[1]["projects"]
    names = [p["project"] for p in projects]
    assert set(names) == {"Storefront", "Payments API", "Home Automation", "Dotfiles", "Travel Journal"}
    assert [p["cost_usd"] for p in projects] == sorted((p["cost_usd"] for p in projects), reverse=True)
    store = next(p for p in projects if p["project"] == "Storefront")
    assert store["sessions"] == 2 and {"claude-sonnet-5-5", "claude-opus-5-5"} <= set(store["by_model"])
    assert store["cost_usd"] == pytest.approx(usage.aggregate(expected["ledger"], project="Storefront")["cost_usd"])
    assert next(p for p in projects if p["project"] == "Dotfiles")["unpriced_models"] == ["local/qwen3-coder"]


def test_the_report_says_how_prices_were_set(ro):
    prices = ro.get("/api/costs")[1]["prices"]
    assert prices["as_of"] == usage.PRICES_AS_OF and prices["source"].startswith("https://") and prices["custom"] == []


def test_settings_show_only_the_simulated_github_account(ro):
    snap = ro.get("/api/settings")[1]
    assert snap["github"]["account"] == "demo-user" and len(snap["projects"]) == 6 and snap["vault"]
    assert "remote" in snap["projects"][0] and all(p["remote"].startswith("github.com/acme/") for p in snap["projects"])


# ---------------------------------------------------------------- actions (a server each)
def test_approving_a_request_launches_a_simulated_worker_tagged_with_its_request_and_session(fresh):
    f = fresh.feature("Two-factor login (TOTP)")
    status, out = fresh.post(f"/api/features/{f['id']}/approve")
    assert status == 200 and out["stage"] == "plan" and out["status"] == "working"
    w = next(w for w in fresh.state()["workers"] if w["id"] == out["worker_id"])
    assert w["id"].startswith("demo") and w["session"] == TODAY and w["request"] == f["id"] and w["phase"] == "plan" and w["state"] == "working"


def test_asking_for_changes_runs_the_same_phase_again(fresh):
    f = fresh.feature("Two-factor login (TOTP)")
    out = fresh.post(f"/api/features/{f['id']}/revise", {"comment": "Please add a recovery option."})[1]
    assert out["stage"] == "spec" and out["status"] == "working" and any(h["event"] == "changes requested" for h in out["history"])
    assert fresh.post(f"/api/features/{f['id']}/revise", {"comment": ""})[0] == 400


def test_a_new_request_is_created_launched_and_tagged(fresh):
    status, out = fresh.post("/api/features", {"project": "Dotfiles", "title": "Tidy the git aliases", "desc": "Group them in one file.",
                                               "model": "my-gateway/some-model", "kind": "maintenance"})
    assert status == 200 and out["kind"] == "maintenance" and out["stage"] == "concept" and out["model"] == "my-gateway/some-model" and out["lang"] == "en"
    w = next(w for w in fresh.state()["workers"] if w["id"] == out["worker_id"])
    assert w["request"] == out["id"] and w["phase"] == "concept" and w["model"] == "my-gateway/some-model"


@pytest.mark.parametrize("body,error", [
    ({"project": "Dotfiles", "title": "x", "desc": "y", "model": "--evil"}, "invalid model name"),
    ({"project": "Dotfiles", "title": "", "desc": "y"}, "title required (max. 120 characters)"),
    ({"project": "Nope", "title": "x", "desc": "y"}, "unknown project"),
    ({"project": "Dotfiles", "title": "x", "desc": "y", "kind": "magic"}, "unknown request type")])
def test_a_bad_new_request_is_refused_with_a_reason(fresh, body, error):
    status, out = fresh.post("/api/features", body)
    assert status == 400 and out == {"error": error}
    assert len(fresh.state()["features"]) == 6                                   # nothing was created


def test_cancelling_closes_the_request_into_the_open_session(fresh):
    f = fresh.feature("Why is the nightly sync slow?")
    status, out = fresh.post(f"/api/features/{f['id']}/cancel")
    assert status == 200 and out["status"] == "cancelled" and out["closed"]["session"] == TODAY
    assert fresh.feature("Why is the nightly sync slow?")["status"] == "cancelled"
    assert "Why is the nightly sync slow?" in [r["title"] for r in fresh.get("/api/costs")[1]["current"]["requests"]]
    assert fresh.post(f"/api/features/{f['id']}/cancel")[1] == {"error": "already finished"}


def test_actions_on_the_wrong_state_or_a_missing_request_are_refused(fresh):
    working = fresh.feature("CSV export of trips")
    assert fresh.post(f"/api/features/{working['id']}/approve")[1]["error"].startswith("current state")
    assert fresh.post("/api/features/not-a-real-id-123/approve")[1] == {"error": "feature not found"}
    assert fresh.post("/api/features/../approve")[0] == 400


def test_ending_the_session_freezes_it_and_opens_a_clean_one(fresh, expected):
    before = fresh.state()
    status, out = fresh.post("/api/session/end")
    assert status == 200
    ended, now = out["ended"], out["current"]
    assert ended["id"] == TODAY and ended["ended_by"] == "user" and ended["ended"] and ended["active_s"] >= before["session"]["active_s"] - 1
    assert ended["cost_usd"] == pytest.approx(expected["today"]["cost_usd"]) and [r["title"] for r in ended["requests"]] == ["Weekly digest email"]
    assert now["id"] != TODAY and now["cost_usd"] == 0 and now["active_s"] < 120 and now["requests"] == []

    s = fresh.state()
    assert s["session"]["id"] == now["id"] and s["session"]["cost_usd"] == 0 and s["session"]["closed"] == 0
    assert "Weekly digest email" not in [f["title"] for f in s["features"]] and s["earlier_closed"] == 3     # "Done" starts again
    ids = {w["id"] for w in s["workers"]}
    assert ids == {"demo007", "demo008"}                                                                     # only the ones still running
    rep = fresh.get("/api/costs")[1]
    assert [x["id"] for x in rep["sessions"]] == [TODAY, YESTERDAY, BEFORE] and rep["sessions"][0]["cost_usd"] == pytest.approx(ended["cost_usd"])
    assert rep["current"]["id"] == now["id"]
    assert rep["total"]["cost_usd"] == pytest.approx(usage.aggregate(expected["ledger"])["cost_usd"])       # nothing was lost or counted twice


def test_work_started_after_ending_a_session_belongs_to_the_new_one(fresh):
    fresh.post("/api/session/end")
    new_id = fresh.state()["session"]["id"]
    f = fresh.feature("Two-factor login (TOTP)")
    out = fresh.post(f"/api/features/{f['id']}/approve")[1]
    w = next(w for w in fresh.state()["workers"] if w["id"] == out["worker_id"])
    assert w["session"] == new_id
    done = fresh.post(f"/api/features/{fresh.feature('Retry failed webhooks')['id']}/cancel")[1]
    assert done["closed"]["session"] == new_id and "Retry failed webhooks" in [x["title"] for x in fresh.state()["features"]]


def test_ending_two_sessions_in_a_row_records_both(fresh):
    first = fresh.post("/api/session/end")[1]["ended"]["id"]
    second = fresh.post("/api/session/end")[1]["ended"]
    ids = [s["id"] for s in fresh.get("/api/costs")[1]["sessions"]]
    assert ids[:2] == [second["id"], first] and second["cost_usd"] == 0 and len(set(ids)) == len(ids) == 4


def test_adding_a_project_starts_the_simulated_extraction_in_this_session(fresh, tmp_path):
    folder = tmp_path / "somewhere" / "brand-new-app"
    folder.mkdir(parents=True)
    status, out = fresh.post("/api/projects", {"path": str(folder), "domain": "Tools"})
    assert status == 200 and out["name"] == "brand-new-app" and out["extraction"] == "extracting the project's information into the vault"
    w = next(w for w in fresh.state()["workers"] if w["project"] == "brand-new-app")
    assert w["phase"] == "extraction" and w["session"] == TODAY


def test_things_that_need_the_real_world_are_refused_politely(fresh):
    assert fresh.post("/api/projects/clone", {"url": "https://github.com/acme/x"})[1] == {"error": "cloning is disabled in the demo"}
    pending = fresh.state()["pending"][0]
    assert fresh.post(f"/api/pending/{pending['id']}/resume")[1] == {"error": "resuming a session is disabled in the demo"}
    assert fresh.post("/api/inbox/curate")[0] == 400


def test_a_to_do_can_be_dismissed_or_turned_into_a_request(fresh):
    todos = fresh.state()["pending"]
    assert fresh.post(f"/api/pending/{todos[0]['id']}/dismiss")[0] == 200 and len(fresh.state()["pending"]) == 3
    out = fresh.post(f"/api/pending/{todos[1]['id']}/feature")[1]
    assert out["title"] == todos[1]["title"] and out["status"] == "working" and len(fresh.state()["pending"]) == 2


def test_a_category_chosen_in_settings_is_remembered(fresh):
    assert fresh.post("/api/settings/domain", {"name": "Dotfiles", "domain": "Personal"})[0] == 200
    assert next(p for p in fresh.state()["projects"] if p["name"] == "Dotfiles")["domain"] == "personal"
    assert fresh.post("/api/settings/domain", {"name": "Dotfiles", "domain": "no/good"})[1] == {
        "error": "invalid category: use letters, numbers, spaces or hyphens (max. 30)"}


def test_the_demo_never_leaves_its_own_folder(fresh):
    fresh.post("/api/session/end")
    assert fresh.dir.is_dir() and (fresh.dir / demo.MARKER).is_file()
    written = [p for p in fresh.dir.rglob("*") if p.is_file()]
    assert written and all(str(fresh.dir) in str(p) for p in written)
    assert (fresh.dir / "home" / "state" / "work_sessions.json").is_file()


# ---------------------------------------------------------------- the cost of a card (a request), across its workers and sessions
def card_costs(expected):
    return usage.request_costs(expected["ledger"])


def test_every_card_carries_what_the_request_has_cost_in_all(ro, expected):
    costs = card_costs(expected)
    for f in ro.state()["features"]:
        assert f["cost"] is not None, f["title"]
        assert f["cost"]["cost_usd"] == pytest.approx(costs[f["id"]]["cost_usd"]) and f["cost"]["workers"] == costs[f["id"]]["workers"]


def test_a_request_that_ran_in_two_work_sessions_is_one_card_with_one_total(ro, expected):
    f = ro.feature("Retry failed webhooks")
    by_hand = sum(e["models"][m]["cost_usd"] for e in expected["ledger"].values() if e["request"] == f["id"] for m in e["models"])
    assert f["cost"]["sessions"] == 2 and f["cost"]["workers"] == 3 and by_hand > 0 and f["cost"]["cost_usd"] == pytest.approx(by_hand)
    assert set(f["cost"]["by_phase"]) == {"spec", "plan", "plan-validation"}
    today_share = usage.aggregate(expected["ledger"], session=TODAY)["by_request"][f["id"]]["cost_usd"]
    assert f["cost"]["cost_usd"] > today_share                                    # the spec was paid for in yesterday's session


def test_the_phases_and_models_of_a_card_add_up_to_its_total(ro):
    for f in ro.state()["features"]:
        c = f["cost"]
        assert sum(p["cost_usd"] for p in c["by_phase"].values()) == pytest.approx(c["cost_usd"])
        assert sum(m["cost_usd"] for m in c["by_model"].values()) == pytest.approx(c["cost_usd"])


def test_a_card_with_several_workers_in_one_phase_counts_them_all(ro):
    build = ro.feature("Cart total is off by one cent")["cost"]["by_phase"]
    assert build["build"]["workers"] == 1 and set(build) == {"spec", "plan", "plan-validation", "build", "review"}


def test_the_cost_report_lists_requests_with_their_totals_and_leaves_unattributed_work_out(ro, expected):
    rep = ro.get("/api/costs")[1]
    rows = rep["requests"]
    assert [r["cost_usd"] for r in rows] == sorted((r["cost_usd"] for r in rows), reverse=True)
    assert {r["title"] for r in rows} == {"Two-factor login (TOTP)", "Retry failed webhooks", "Cart total is off by one cent", "Why is the nightly sync slow?",
                                          "CSV export of trips", "Weekly digest email", "Upgrade pytest to 9", "Fix the flaky login test"}
    unattributed = sum(e["models"][m]["cost_usd"] for e in expected["ledger"].values() if not e["request"] for m in e["models"])
    assert sum(r["cost_usd"] for r in rows) + unattributed == pytest.approx(rep["total"]["cost_usd"]) and unattributed > 0


def test_a_request_closed_in_an_earlier_session_keeps_its_cost_in_the_report(ro, expected):
    rows = {r["title"]: r for r in ro.get("/api/costs")[1]["requests"]}
    pytest_row = rows["Upgrade pytest to 9"]
    assert pytest_row["status"] == "done" and pytest_row["sessions"] == 1 and pytest_row["cost_usd"] > 0
    assert "Upgrade pytest to 9" not in [f["title"] for f in ro.state()["features"]]                 # hidden from the cards, not from the report


def test_ending_the_session_does_not_change_what_a_card_has_cost(fresh):
    before = {f["title"]: f["cost"]["cost_usd"] for f in fresh.state()["features"]}
    fresh.post("/api/session/end")
    after = {f["title"]: f["cost"]["cost_usd"] for f in fresh.state()["features"]}
    assert "Weekly digest email" in before and "Weekly digest email" not in after                   # closed in the old session: off the cards now
    assert after == {t: c for t, c in before.items() if t in after}                                    # every card left keeps exactly its total
    assert fresh.get("/api/costs")[1]["current"]["cost_usd"] == 0


def test_the_page_shows_a_cost_chip_on_cards_and_a_by_request_table(ro):
    _, html = ro.request("GET", "/", token=False)
    assert "const costChip" in html and 'class="fcost"' in html and "By request, all sessions" in html and "costTip(" in html


def test_each_session_lists_the_cards_it_worked_on_with_that_days_cost_and_the_total_so_far(ro):
    rep = ro.get("/api/costs")[1]
    today = {c["title"]: c for c in rep["current"]["cards"]}
    yesterday = {c["title"]: c for c in next(s for s in rep["sessions"] if s["id"] == YESTERDAY)["cards"]}
    hooks = today["Retry failed webhooks"]
    spec_yesterday = yesterday["Retry failed webhooks"]
    assert hooks["cost_session"] > 0 and spec_yesterday["cost_session"] > 0
    assert hooks["cost_session"] + spec_yesterday["cost_session"] == pytest.approx(hooks["cost_total"]) == pytest.approx(spec_yesterday["cost_total"])
    assert hooks["status"] == "awaiting" and hooks["closed_here"] is False                       # still open, carried over
    assert today["Weekly digest email"]["closed_here"] is True and today["Weekly digest email"]["status"] == "done"
    assert "Two-factor login (TOTP)" in today and "Two-factor login (TOTP)" not in yesterday


def test_a_card_left_open_overnight_is_still_on_the_dashboard_after_ending_the_session(fresh):
    open_before = {f["title"] for f in fresh.state()["features"] if f["status"] not in ("done", "cancelled")}
    fresh.post("/api/session/end")
    open_after = {f["title"] for f in fresh.state()["features"] if f["status"] not in ("done", "cancelled")}
    assert open_before == open_after and len(open_after) == 5


# ---------------------------------------------------------------- plan usage and usage limits
def test_the_state_carries_the_plan_percentages_and_what_the_chiefs_workers_spent(ro):
    p = ro.state()["plan"]
    assert p["connected"] is True
    five, seven = p["windows"]["five_hour"], p["windows"]["seven_day"]
    assert five["percent"] == 62.0 and five["level"] == "ok" and 0 < five["resets_in_s"] <= 95 * 60
    assert seven["percent"] == 38.0 and seven["resets_in_s"] > 3 * 86400
    assert p["spend"]["five_hour_usd"] >= 0 and p["spend"]["seven_day_usd"] >= p["spend"]["five_hour_usd"]


def test_the_costs_report_has_the_plan_block_and_the_setup_snippet_but_no_secrets(ro):
    status, c = ro.get("/api/costs")
    assert status == 200 and c["plan"]["connected"] is True
    snippet = c["plan"]["setup"]["snippet"]["statusLine"]
    assert snippet["type"] == "command" and snippet["command"].endswith("statusline")
    assert "token" not in json.dumps(c["plan"]).lower()


def test_the_page_has_the_meters_and_the_plan_section(ro):
    status, html = ro.get("/", token=False)
    assert status == 200 and "planMeters" in html and "Plan usage" in html and "paused by a usage limit" in html


def test_a_worker_that_hits_a_limit_pauses_its_card_and_shows_in_the_state(fresh):
    f = next(x for x in fresh.state()["features"] if x.get("worker_id") == "demo007")
    assert "paused" not in f and fresh.state()["limits"] == []
    status, _ = fresh.post("/api/demo/limit", {"worker": "demo007", "on": True, "auto": True})
    assert status == 200
    s = fresh.state()
    card = next(x for x in s["features"] if x["id"] == f["id"])
    assert card["paused"]["auto"] is True and card["paused"]["kind"] == "session"
    assert s["limits"] == [{"id": "demo007", "kind": "session", "auto": True, "resumes_in_s": s["limits"][0]["resumes_in_s"]}]
    assert 0 < s["limits"][0]["resumes_in_s"] <= 90 * 60
    fresh.post("/api/demo/limit", {"worker": "demo007", "on": False})
    assert fresh.state()["limits"] == []
    assert "paused" not in next(x for x in fresh.state()["features"] if x["id"] == f["id"])


def test_settings_in_the_demo_connect_and_disconnect_without_touching_the_real_claude_settings(fresh):
    real = Path.home() / ".claude" / "settings.json"
    before = real.read_bytes() if real.exists() else None
    status, snap = fresh.get("/api/settings")
    assert status == 200 and snap["plan"] == {"resume_after_limit": True, "connection": "connected"}
    assert fresh.post("/api/settings/plan_disconnect")[1]["connection"] == "none"
    assert fresh.get("/api/settings")[1]["plan"]["connection"] == "none"
    assert fresh.post("/api/settings/resume_after_limit", {"on": False})[0] == 200
    assert fresh.get("/api/settings")[1]["plan"]["resume_after_limit"] is False
    assert (real.read_bytes() if real.exists() else None) == before


# ---------------------------------------------------------------- «Ask the worker»
def test_the_demo_shows_an_answered_question_on_a_card(ro):
    f = ro.feature("Cart total is off by one cent")
    assert len(f["qa"]) == 1 and f["qa"][0]["status"] == "answered" and "round half to even" in f["qa"][0]["answer"]
    assert ro.feature("Two-factor login (TOTP)")["qa"] == []


def test_asking_goes_through_the_route_and_leaves_the_request_untouched(fresh):
    before = fresh.feature("Two-factor login (TOTP)")
    code, got = fresh.post(f"/api/features/{before['id']}/ask", {"question": "Why TOTP and not SMS?"})
    assert code == 200 and got["n"] == 1 and got["status"] == "working"
    after = fresh.feature("Two-factor login (TOTP)")
    assert (after["stage"], after["status"]) == (before["stage"], before["status"])
    assert after["qa"][0]["q"] == "Why TOTP and not SMS?" and after["qa"][0]["status"] == "working"
    code, err = fresh.post(f"/api/features/{before['id']}/ask", {"question": "And another?"})
    assert code == 400 and "still being answered" in err["error"]
    code, err = fresh.post(f"/api/features/{before['id']}/ask", {"question": "  "}, headers={"X-Lang": "pt"})
    assert code == 400 and "pergunta obrigatória" in err["error"]


def test_asking_needs_the_token_and_the_own_origin(fresh):
    fid = fresh.feature("Two-factor login (TOTP)")["id"]
    assert fresh.post(f"/api/features/{fid}/ask", {"question": "x"}, token=False)[0] == 403
    assert fresh.post(f"/api/features/{fid}/ask", {"question": "x"}, origin=False)[0] == 403


# ---------------------------------------------------------------- requests suggested by workers
def test_suggestions_are_listed_dismissed_and_never_start_anything(fresh):
    sys_path = str(ROOT / "scripts")
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from proposals import Proposals
    Proposals(fresh.dir / "home" / "state").add("Dotfiles", "ship", "Commit the aliases", "Commit the new alias file and open a PR.", "analysis-1")
    s = fresh.state()
    assert [p["title"] for p in s["proposals"]] == ["Commit the aliases"]
    before = len(s["features"])
    code, _ = fresh.post(f"/api/proposals/{s['proposals'][0]['id']}/dismiss")
    assert code == 200 and fresh.state()["proposals"] == [] and len(fresh.state()["features"]) == before
    code, err = fresh.post("/api/proposals/s-nope/dismiss", headers={"X-Lang": "pt"})
    assert code == 400 and "sugestão não encontrada" in err["error"]
    assert fresh.post("/api/proposals/s-nope/dismiss", token=False)[0] == 403


def test_a_ship_request_can_be_created_through_the_route(fresh):
    code, out = fresh.post("/api/features", {"project": "Dotfiles", "title": "Commit the aliases", "desc": "Commit alias.sh.", "kind": "ship"})
    assert code == 200 and out["kind"] == "ship" and out["stage"] == "build"


# ---- fewer clicks, fewer surprises ----
@pytest.mark.parametrize("path", ["/api/state", "/api/costs", "/api/settings", "/api/projects"])
def test_reads_accept_the_token_in_a_header_so_it_stays_out_of_the_address(ro, path):
    assert ro.request("GET", path, token=False, headers={"X-Token": ro.token})[0] == 200
    assert ro.request("GET", path, token=False, headers={"X-Token": "wrong"})[0] == 403


def test_the_state_carries_the_alerts_list(ro):
    assert isinstance(ro.state()["alerts"], list)


def test_a_request_can_ask_to_run_until_the_review_but_never_a_publication(fresh):
    code, out = fresh.post("/api/features", {"project": "Dotfiles", "title": "Group aliases", "desc": "One file.", "autopilot": True})
    assert code == 200 and out["autopilot"] is True
    code, out = fresh.post("/api/features", {"project": "Dotfiles", "title": "Plain one", "desc": "One file.", "autopilot": "yes"})
    assert code == 200 and "autopilot" not in out
    code, out = fresh.post("/api/features", {"project": "Dotfiles", "title": "Commit it", "desc": "Commit alias.sh.", "kind": "ship",
                                            "autopilot": True})
    assert code == 200 and "autopilot" not in out


def test_the_page_sends_the_token_in_a_header_and_offers_the_new_option(ro):
    _, html = ro.request("GET", "/", token=False)
    assert "?t=" not in html and '"t="+T' not in html
    assert 'id="nfAuto"' in html and "function alertBanner" in html
