"""The sessions-and-costs report: one session's numbers, per-project totals, frozen totals for finished sessions, text output."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import report  # noqa: E402
from worksession import WorkSessions  # noqa: E402


def row(cost, inp=0, out=0, read=0, unpriced=0):
    return {"messages": 1, "input": inp, "output": out, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": read, "cost_usd": cost,
            "unpriced_messages": unpriced}


class Clock:
    t = datetime(2026, 11, 8, 9, 0, 0)

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    ws = WorkSessions(tmp_path / "ws.json", now=clock)
    first = ws.ensure()["id"]
    clock.t += timedelta(seconds=45)
    ended = ws.end(totals={"cost_usd": 9.99, "tokens": {"input": 1, "output": 1, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0},
                           "workers": 1, "unpriced_tokens": 0, "unpriced_models": [], "by_model": {}, "by_project": {}, "by_request": {}})
    second = ended["current"]["id"]
    ledger = {
        "w1": {"project": "Alfa", "session": first, "request": "r1", "models": {"claude-sonnet-5-5": row(1.0, 100, 10)}},
        "w2": {"project": "Alfa", "session": second, "request": "r2", "models": {"claude-opus-5-5": row(2.0, 50, 5), "claude-sonnet-5-5": row(0.5)}},
        "w3": {"project": "Beta", "session": second, "request": "r3", "models": {"local/odd": row(0.0, 7, 1, unpriced=1)}}}
    features = [{"id": "r1", "title": "One", "project": "Alfa", "kind": "fix", "status": "done", "closed": {"at": "2026-11-08T09:00:30", "session": first}},
                {"id": "r2", "title": "Two", "project": "Alfa", "status": "cancelled", "closed": {"at": "2026-11-08T09:10:00", "session": second}},
                {"id": "r3", "title": "Three", "project": "Beta", "status": "awaiting"}]
    workers = [{"id": "w1", "session": first}, {"id": "w2", "session": second}, {"id": "w3", "session": second}]
    return ws, ledger, features, workers, first, second


def test_the_open_session_is_priced_from_the_ledger_and_counts_what_it_closed(env):
    ws, ledger, features, workers, first, second = env
    cur = report.build(ws, ledger, features, workers)["current"]
    assert cur["id"] == second and cur["cost_usd"] == pytest.approx(2.5) and cur["workers"] == 2
    assert [r["id"] for r in cur["requests"]] == ["r2"] and cur["unpriced_models"] == ["local/odd"]


def test_a_finished_session_shows_the_totals_frozen_when_it_ended(env):
    ws, ledger, features, workers, first, second = env
    past = report.build(ws, ledger, features, workers)["sessions"]
    assert [s["id"] for s in past] == [first] and past[0]["cost_usd"] == 9.99          # not recomputed from the ledger's 1.0
    assert past[0]["active_s"] == 45 and past[0]["ended_by"] == "user" and [r["id"] for r in past[0]["requests"]] == ["r1"]


def test_a_finished_session_without_frozen_totals_falls_back_to_the_ledger(env):
    ws, ledger, features, workers, first, _ = env
    sessions = ws._load()
    sessions[0]["totals"] = None
    ws._save(sessions)
    assert report.build(ws, ledger, features, workers)["sessions"][0]["cost_usd"] == pytest.approx(1.0)


def test_requests_still_open_belong_to_no_session_yet(env):
    ws, ledger, features, workers, *_ = env
    rep = report.build(ws, ledger, features, workers)
    assert "r3" not in [r["id"] for s in rep["sessions"] + [rep["current"]] for r in s["requests"]]


def test_projects_are_ranked_by_cost_with_their_models_and_session_count(env):
    ws, ledger, features, workers, *_ = env
    projects = report.build(ws, ledger, features, workers)["projects"]
    assert [p["project"] for p in projects] == ["Alfa", "Beta"]
    alfa = projects[0]
    assert alfa["cost_usd"] == pytest.approx(3.5) and alfa["sessions"] == 2 and sorted(alfa["by_model"]) == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert projects[1]["unpriced_models"] == ["local/odd"]


def test_models_overall_and_how_prices_were_set(env):
    ws, ledger, features, workers, *_ = env
    rep = report.build(ws, ledger, features, workers, {"my/model": {}})
    assert [m["model"] for m in rep["models"]][:2] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert rep["prices"]["custom"] == ["my/model"] and rep["prices"]["as_of"] and rep["prices"]["source"].startswith("https://")
    assert rep["total"]["cost_usd"] == pytest.approx(3.5)


def test_a_fresh_install_reports_zero_not_an_error(tmp_path):
    ws = WorkSessions(tmp_path / "ws.json")
    rep = report.build(ws, {}, [], [])
    assert rep["current"] is None and rep["sessions"] == [] and rep["projects"] == [] and rep["total"]["cost_usd"] == 0


def test_the_legacy_session_is_flagged(tmp_path):
    ws = WorkSessions(tmp_path / "ws.json")
    ws.add_legacy("2026-10-01T00:00:00", "2026-11-01T00:00:00")
    assert report.build(ws, {}, [], [])["sessions"][0]["legacy"] is True


@pytest.mark.parametrize("x,text", [(0, "$0.00"), (3.456, "$3.46"), (0.004, "$0.0040"), (1234.5, "$1,234.50")])
def test_money_is_readable(x, text):
    assert report.money(x) == text


@pytest.mark.parametrize("s,text", [(5, "5s"), (65, "1m 05s"), (3725, "1h 02m"), (0, "0s")])
def test_durations_are_readable(s, text):
    assert report.duration(s) == text


def test_the_text_report_names_models_projects_and_the_caveat(env):
    ws, ledger, features, workers, *_ = env
    text = report.render_text(report.build(ws, ledger, features, workers))
    assert "estimates at list price" in text and "This session:" in text and "claude-opus-5-5" in text
    assert "Alfa" in text and "No price for: local/odd" in text and "Past sessions: 1" in text


# ---------------------------------------------------------------- cost per request
def test_every_request_is_listed_with_its_total_across_sessions_most_expensive_first(env):
    ws, ledger, features, workers, first, second = env
    ledger["w9"] = {"project": "Alfa", "session": first, "request": "r2", "phase": "spec", "models": {"claude-sonnet-5-5": row(4.0)}}   # r2 also ran earlier
    rows = report.build(ws, ledger, features, workers)["requests"]
    assert [r["id"] for r in rows] == ["r2", "r1", "r3"]                   # by cost; r3 has only an unpriced model, so $0
    assert rows[2]["unpriced"] is True and rows[2]["cost_usd"] == 0
    r2 = rows[0]
    assert r2["title"] == "Two" and r2["status"] == "cancelled" and r2["cost_usd"] == pytest.approx(6.5) and r2["sessions"] == 2
    assert r2["by_phase"]["spec"]["cost_usd"] == pytest.approx(4.0)


def test_the_text_report_has_a_by_request_section(env):
    ws, ledger, features, workers, *_ = env
    text = report.render_text(report.build(ws, ledger, features, workers))
    assert "By request (all sessions):" in text and "Two" in text and "sessions" in text
