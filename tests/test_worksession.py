"""Work sessions: a start, an end, active time, and a clean break after a long absence."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from worksession import HEARTBEAT_MAX_S, LEGACY_ID, STALE_AFTER_S, WorkSessions  # noqa: E402

T0 = datetime(2026, 11, 8, 9, 0, 0)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    return WorkSessions(tmp_path / "work_sessions.json", now=clock), clock


def test_there_is_no_session_until_one_is_started(env):
    ws, _ = env
    assert ws.current() is None and ws.current_id() == "" and ws.list() == []


def test_ensure_opens_a_session_and_is_idempotent(env):
    ws, clock = env
    first = ws.ensure()
    clock.tick(30)
    assert ws.ensure()["id"] == first["id"] and len(ws.list()) == 1
    assert first["started"] == "2026-11-08T09:00:00" and first["ended"] is None and first["active_s"] == 0


def test_heartbeats_add_active_time(env):
    ws, clock = env
    ws.ensure()
    for _ in range(3):
        clock.tick(10)
        ws.heartbeat()
    assert ws.current()["active_s"] == 30


def test_time_while_the_server_was_down_is_not_counted(env):
    ws, clock = env
    ws.ensure()
    clock.tick(10)
    ws.heartbeat()
    clock.tick(3 * 3600)                     # the server was not running
    ws.heartbeat()
    assert ws.current()["active_s"] == 10 + HEARTBEAT_MAX_S


def test_active_seconds_includes_the_time_since_the_last_heartbeat(env):
    ws, clock = env
    cur = ws.ensure()
    clock.tick(20)
    assert ws.active_seconds(cur) == 20
    clock.tick(500)
    assert ws.active_seconds(cur) == HEARTBEAT_MAX_S


def test_ending_freezes_the_totals_and_opens_a_fresh_session(env):
    ws, clock = env
    first = ws.ensure()
    clock.tick(40)
    out = ws.end(totals={"cost_usd": 1.25})
    assert out["ended"]["id"] == first["id"] and out["ended"]["ended_by"] == "user" and out["ended"]["totals"] == {"cost_usd": 1.25}
    assert out["ended"]["active_s"] == 40 and out["ended"]["ended"] == "2026-11-08T09:00:40"
    assert out["current"]["id"] != first["id"] and out["current"]["ended"] is None and ws.current_id() == out["current"]["id"]
    assert [s["id"] for s in ws.list()] == [out["current"]["id"], first["id"]]


def test_sessions_started_in_the_same_second_get_distinct_ids(env):
    ws, _ = env
    ws.ensure()
    a = ws.end()["ended"]["id"]
    b = ws.end()["ended"]["id"]
    assert len({a, b, ws.current_id()}) == 3


def test_ending_without_an_open_session_is_an_error(env):
    ws, _ = env
    with pytest.raises(ValueError, match="no session"):
        ws.end()


def test_a_session_whose_server_is_long_gone_is_closed_where_it_was_last_seen(env):
    ws, clock = env
    old = ws.ensure()
    clock.tick(50)
    ws.heartbeat()
    clock.tick(STALE_AFTER_S + 1)            # overnight
    fresh = ws.ensure()
    closed = ws.get(old["id"])
    assert closed["ended"] == "2026-11-08T09:00:50" and closed["ended_by"] == "idle" and closed["active_s"] == 50
    assert fresh["id"] != old["id"] and fresh["started"] != old["started"]


def test_a_short_restart_keeps_the_same_session(env):
    ws, clock = env
    old = ws.ensure()
    clock.tick(600)
    assert ws.ensure()["id"] == old["id"]


def test_the_legacy_placeholder_is_added_once_and_never_becomes_current(env):
    ws, _ = env
    a = ws.add_legacy("2026-11-01T00:00:00", "2026-11-07T00:00:00")
    assert ws.add_legacy("x", "y") == a and a["id"] == LEGACY_ID and a["legacy"] is True
    assert ws.current() is None
    ws.ensure()
    assert ws.get(LEGACY_ID)["ended_by"] == "migration" and ws.current_id() != LEGACY_ID


def test_a_corrupt_file_is_treated_as_no_sessions(env, tmp_path):
    ws, _ = env
    (tmp_path / "work_sessions.json").write_text("{not json", encoding="utf-8")
    assert ws.list() == [] and ws.ensure()["ended"] is None
