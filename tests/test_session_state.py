"""A background session whose main thread has stopped but whose sub-agent is still running must not hold a request that already has its result."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chief  # noqa: E402
from features import Features  # noqa: E402
from test_auto_review import Fake  # noqa: E402


@pytest.mark.parametrize("row,expected", [
    ({"state": "working", "status": "idle"}, "idle"),                      # the reported case: «← 1 agent» keeps `state` at working
    ({"state": "working", "status": "busy"}, "working"),
    ({"state": "working"}, "working"),
    ({"state": "running"}, "running"),
    ({"state": "done"}, "done"),
    ({"state": "blocked", "status": "idle"}, "blocked"),                   # only a working session is reinterpreted
    ({"status": "busy"}, "working"),                                        # no `state` at all: fall back to `status`
    ({"status": "idle"}, "idle"),
    ({}, "finished"),
])
def test_the_state_the_chief_uses(row, expected):
    assert chief.session_state(row) == expected


def test_an_idle_session_is_not_busy_but_is_still_active():
    assert chief.session_state({"state": "working", "status": "idle"}) in chief.ACTIVE
    assert chief.session_state({"state": "working", "status": "idle"}) not in chief.BUSY


def test_a_request_with_its_result_moves_on_even_while_a_sub_agent_keeps_the_session_open(tmp_path):
    fake = Fake()
    live = {}
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True, is_busy=lambda wid: chief.session_state(live.get(wid, {})) in chief.BUSY)
    f = fs.create("Alfa", "Why", "x", None, kind="analysis")
    wid = f["worker_id"]
    live[wid] = {"state": "working", "status": "busy"}
    d = tmp_path / "features" / f["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text("# Analysis", encoding="utf-8")
    assert next(x for x in fs.list() if x["id"] == f["id"])["status"] == "working"        # really working: the result alone is not enough
    live[wid] = {"state": "working", "status": "idle"}                                      # main thread stopped, sub-agent still alive
    calls = len(fake.calls)
    got = next(x for x in fs.list() if x["id"] == f["id"])
    assert got["stage"] == "review" and len(fake.calls) == calls + 1        # the report counted as finished and the verification started
