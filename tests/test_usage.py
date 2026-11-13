"""Cost estimates: read from Claude Code's session logs, priced at list price, stored per worker, totalled per session and project."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import usage  # noqa: E402


def u(inp=0, out=0, read=0, w5=0, w1=0, **extra):
    """A usage block as Claude Code logs it."""
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read, "cache_creation_input_tokens": w5 + w1,
            "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}, **extra}


def line(model, usage_block, mid, req="r1", at="2026-11-08T10:00:00Z", **kw):
    return json.dumps({"type": "assistant", "uuid": f"u-{mid}", "requestId": req, "timestamp": at,
                       "message": {"id": mid, "model": model, "usage": usage_block}, **kw})


def write(path: Path, *lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- model names and prices
@pytest.mark.parametrize("name,key", [
    ("claude-sonnet-5-5", "sonnet-5-5"), ("claude-sonnet-4-5-20250929", "sonnet-4-5"), ("claude-sonnet-4-20250514", "sonnet-4"),
    ("claude-opus-4-1-20250805", "opus-4-1"), ("us.anthropic.claude-haiku-4-5-v1:0", "haiku-4-5"), ("claude-fable-5-1", "fable-5-1"),
    ("claude-opus-5-5[1m]", "opus-5-5"), ("my-gateway/qwen3-coder", ""), ("", "")])
def test_model_names_are_reduced_to_a_price_key(name, key):
    assert usage.model_key(name) == key


def test_a_message_is_priced_per_token_category():
    # sonnet 5.5: input $2, output $10, 1 h cache write $4, cache read $0.10 per million
    cost = usage.message_cost("claude-sonnet-5-5", u(inp=1000, out=500, read=10000, w1=2000))
    assert cost == pytest.approx((1000 * 2 + 500 * 10 + 2000 * 4 + 10000 * 0.10) / 1e6) == pytest.approx(0.016)


def test_the_five_minute_and_the_one_hour_cache_writes_have_their_own_prices():
    five = usage.message_cost("claude-sonnet-5-5", u(w5=1_000_000))
    hour = usage.message_cost("claude-sonnet-5-5", u(w1=1_000_000))
    assert (five, hour) == (pytest.approx(2.5), pytest.approx(4.0))


def test_an_old_log_without_the_cache_breakdown_counts_cache_writes_at_the_five_minute_rate():
    block = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 1_000_000}
    assert usage.message_cost("claude-sonnet-5-5", block) == pytest.approx(2.5)


def test_haiku_5_5_costs_more_when_the_prompt_is_over_100k_tokens():
    short = usage.message_cost("claude-haiku-5-5", u(inp=90_000, out=1000))
    long_ = usage.message_cost("claude-haiku-5-5", u(inp=10_000, read=95_000, out=1000))      # the cache counts towards the prompt
    assert short == pytest.approx((90_000 * 0.10 + 1000 * 0.50) / 1e6)
    assert long_ == pytest.approx((10_000 * 0.50 + 95_000 * 0.05 + 1000 * 2.50) / 1e6)


def test_fast_mode_doubles_the_price_only_on_the_models_that_have_it():
    base = usage.message_cost("claude-opus-5-5", u(inp=1000, out=1000))
    assert usage.message_cost("claude-opus-5-5", u(inp=1000, out=1000, speed="fast")) == pytest.approx(base * 2)
    plain = usage.message_cost("claude-sonnet-5-5", u(inp=1000, out=1000))
    assert usage.message_cost("claude-sonnet-5-5", u(inp=1000, out=1000, speed="fast")) == pytest.approx(plain)


def test_us_only_inference_adds_ten_percent_from_claude_4_6_on():
    base = usage.message_cost("claude-sonnet-5-5", u(inp=1000, out=1000))
    assert usage.message_cost("claude-sonnet-5-5", u(inp=1000, out=1000, inference_geo="us")) == pytest.approx(base * 1.1)
    old = usage.message_cost("claude-sonnet-4-5", u(inp=1000, out=1000))
    assert usage.message_cost("claude-sonnet-4-5", u(inp=1000, out=1000, inference_geo="us")) == pytest.approx(old)


def test_a_model_without_a_price_is_unpriced_not_free():
    assert usage.message_cost("my-gateway/qwen3-coder", u(inp=1000, out=1000)) is None


def test_the_users_prices_price_any_model_and_override_the_defaults():
    mine = {"my-gateway/qwen3-coder": {"input": 1, "output": 2, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0}}
    assert usage.message_cost("my-gateway/qwen3-coder", u(inp=1_000_000, out=1_000_000), mine) == pytest.approx(3.0)
    cheap = {"sonnet-5-5": {"input": 1, "output": 1, "cache_write_5m": 1, "cache_write_1h": 1, "cache_read": 1}}
    assert usage.message_cost("claude-sonnet-5-5", u(inp=1_000_000), cheap) == pytest.approx(1.0)
    incomplete = {"sonnet-5-5": {"input": 1}}                                        # missing fields: ignored, defaults apply
    assert usage.message_cost("claude-sonnet-5-5", u(inp=1_000_000), incomplete) == pytest.approx(2.0)


def test_the_published_prices_stay_internally_consistent():
    for key, (inp, out, w5, w1, read) in usage.DEFAULT_PRICES.items():
        assert w5 == pytest.approx(inp * 1.25) and w1 == pytest.approx(inp * 2) and out > inp and read < inp, key


# ---------------------------------------------------------------- reading a transcript
def test_a_streamed_message_logged_several_times_is_counted_once_with_its_final_numbers(tmp_path):
    p = write(tmp_path / "t.jsonl",
              line("claude-sonnet-5-5", u(inp=2, out=10, w1=500), "m1"), line("claude-sonnet-5-5", u(inp=2, out=120, w1=500), "m1"),
              line("claude-sonnet-5-5", u(inp=2, out=300, w1=500), "m1"), line("claude-sonnet-5-5", u(inp=4, out=50, read=500), "m2", "r2"))
    msgs = usage.read_messages(p)
    assert len(msgs) == 2 and sorted(m["usage"]["output_tokens"] for m in msgs) == [50, 300]


def test_synthetic_entries_bad_lines_and_other_record_types_are_ignored(tmp_path):
    p = write(tmp_path / "t.jsonl", "not json", json.dumps({"type": "user", "message": {"content": "hi"}}),
              line("<synthetic>", u(inp=5), "s1"), line("claude-sonnet-5-5", u(inp=7), "m1"))
    assert [m["usage"]["input_tokens"] for m in usage.read_messages(p)] == [7]
    assert usage.read_messages(tmp_path / "missing.jsonl") == []


def test_summarize_totals_tokens_and_cost_per_model(tmp_path):
    p = write(tmp_path / "t.jsonl",
              line("claude-sonnet-5-5", u(inp=1000, out=500, read=10000, w1=2000), "a", at="2026-11-08T10:00:00Z"),
              line("claude-opus-5-5", u(inp=100, out=100), "b", "r2", at="2026-11-08T10:30:00Z"),
              line("claude-sonnet-5-5", u(inp=1000, out=500, read=10000, w1=2000), "c", "r3", at="2026-11-08T11:00:00Z"),
              line("local/odd-model", u(inp=50, out=5), "d", "r4"))
    s = usage.summarize(p)
    son = s["models"]["claude-sonnet-5-5"]
    assert son["messages"] == 2 and son["input"] == 2000 and son["output"] == 1000 and son["cache_read"] == 20000 and son["cache_write_1h"] == 4000
    assert son["cost_usd"] == pytest.approx(0.032)
    assert s["models"]["claude-opus-5-5"]["cost_usd"] == pytest.approx((100 * 4 + 100 * 20) / 1e6)
    assert s["models"]["local/odd-model"]["unpriced_messages"] == 1 and s["models"]["local/odd-model"]["cost_usd"] == 0
    assert s["first_at"] == "2026-11-08T10:00:00Z" and s["last_at"] == "2026-11-08T11:00:00Z"


# ---------------------------------------------------------------- finding the transcript of a worker
def test_the_transcript_is_found_by_the_start_of_the_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    log = write(tmp_path / "projects" / "d--some-project" / "ebaaed87-4670-41d1-a588-723bec1131aa.jsonl", line("claude-sonnet-5-5", u(inp=1), "m"))
    assert usage.find_transcript("ebaaed87") == log
    assert usage.find_transcript("ebaaed87", r"D:\some\project") is None or usage.find_transcript("ebaaed87", r"D:\some\project") == log
    assert usage.find_transcript("ebaaed87", r"D:\Some Project") == log           # the folder hint is only a shortcut
    assert usage.find_transcript("ffffffff") is None


@pytest.mark.parametrize("bad", ["", "../x", "ab", "ebaaed87/../../x", None])
def test_a_worker_id_that_is_not_a_session_id_finds_nothing(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert usage.find_transcript(bad) is None


# ---------------------------------------------------------------- the ledger
@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    log = tmp_path / "claude" / "projects" / "d--p" / "aaaa1111-0000-0000-0000-000000000000.jsonl"
    write(log, line("claude-sonnet-5-5", u(inp=1000, out=500, read=10000, w1=2000), "m1"))
    worker = {"id": "aaaa1111", "project": "Alfa", "cwd": r"D:\p", "session": "ws-1", "request": "req-1", "phase": "spec", "name": "w"}
    return usage.Ledger(tmp_path / "state" / "usage.json"), log, worker


def test_a_worker_is_measured_once_and_again_only_when_its_log_changes(world):
    ledger, log, worker = world
    assert ledger.refresh([worker]) == 1 and ledger.refresh([worker]) == 0
    e = ledger.load()["aaaa1111"]
    assert (e["project"], e["session"], e["request"], e["phase"]) == ("Alfa", "ws-1", "req-1", "spec")
    assert e["models"]["claude-sonnet-5-5"]["cost_usd"] == pytest.approx(0.016)
    with log.open("a", encoding="utf-8") as f:
        f.write(line("claude-sonnet-5-5", u(inp=1000, out=500, read=10000, w1=2000), "m2", "r2") + "\n")
    assert ledger.refresh([worker]) == 1 and ledger.load()["aaaa1111"]["models"]["claude-sonnet-5-5"]["cost_usd"] == pytest.approx(0.032)


def test_changing_the_prices_reprices_what_is_still_on_disk(world):
    ledger, _, worker = world
    ledger.refresh([worker])
    cheap = {"sonnet-5-5": {"input": 0, "output": 0, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0}}
    assert ledger.refresh([worker], cheap) == 1 and ledger.load()["aaaa1111"]["models"]["claude-sonnet-5-5"]["cost_usd"] == 0


def test_what_was_measured_survives_the_transcript_being_deleted(world):
    ledger, log, worker = world
    ledger.refresh([worker])
    log.unlink()
    assert ledger.refresh([worker]) == 0
    assert ledger.load()["aaaa1111"]["models"]["claude-sonnet-5-5"]["cost_usd"] == pytest.approx(0.016)


def test_a_worker_without_a_log_is_not_invented(world):
    ledger, _, worker = world
    assert ledger.refresh([{**worker, "id": "bbbb2222"}]) == 0 and ledger.load() == {}


def test_the_session_and_request_of_a_worker_can_be_corrected_later(world):
    ledger, _, worker = world
    ledger.refresh([worker])
    assert ledger.refresh([{**worker, "request": "req-2"}]) == 1 and ledger.load()["aaaa1111"]["request"] == "req-2"


# ---------------------------------------------------------------- totals
def entry(project, session, request, **models):
    return {"project": project, "session": session, "request": request, "models": models}


def row(cost, inp=0, out=0, unpriced=0):
    return {"messages": 1, "input": inp, "output": out, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "cost_usd": cost,
            "unpriced_messages": unpriced}


LEDGER = {
    "w1": entry("Alfa", "ws-1", "r1", **{"claude-sonnet-5-5": row(1.0, 100, 10)}),
    "w2": entry("Alfa", "ws-1", "r1", **{"claude-opus-5-5": row(2.0, 50, 5)}),
    "w3": entry("Beta", "ws-1", "r2", **{"claude-sonnet-5-5": row(0.5, 10, 1)}),
    "w4": entry("Beta", "ws-2", "r3", **{"local/odd": row(0.0, 7, 1, unpriced=1)}),
}


def test_totals_by_session_project_model_and_request():
    t = usage.aggregate(LEDGER, session="ws-1")
    assert t["cost_usd"] == pytest.approx(3.5) and t["workers"] == 3 and t["tokens"]["input"] == 160
    assert t["by_model"]["claude-sonnet-5-5"]["cost_usd"] == pytest.approx(1.5)
    assert t["by_project"]["Alfa"]["cost_usd"] == pytest.approx(3.0) and t["by_project"]["Beta"]["cost_usd"] == pytest.approx(0.5)
    assert t["by_request"]["r1"]["cost_usd"] == pytest.approx(3.0) and t["by_request"]["r1"]["project"] == "Alfa"
    assert t["unpriced_models"] == []


def test_totals_for_one_project_across_sessions():
    t = usage.aggregate(LEDGER, project="Beta")
    assert t["workers"] == 2 and t["cost_usd"] == pytest.approx(0.5) and t["unpriced_models"] == ["local/odd"] and t["unpriced_tokens"] == 8


def test_totals_of_an_empty_ledger_are_zero():
    t = usage.aggregate({})
    assert t["cost_usd"] == 0 and t["workers"] == 0 and t["by_model"] == {} and t["by_project"] == {}


# ---------------------------------------------------------------- the cost of a request (a card), whatever the session or phase
@pytest.mark.parametrize("task,request_id", [
    ("Feature «Login» (id login-261107101010) in project Alfa.\n\n## User request", "login-261107101010"),
    ("Feature «Login» (id login-261107101010) no projeto Alfa.", "login-261107101010"),
    ("Look at the failing test", ""), ("", ""), (None, ""), ("mentions (id Upper-Case) only", "")])
def test_the_request_a_worker_belongs_to_is_read_from_its_task(task, request_id):
    assert usage.request_from_task(task) == request_id


def test_workers_launched_before_they_were_tagged_are_attributed_from_their_task(world):
    ledger, _, worker = world
    old = {**worker, "request": "", "task": "Feature «Login» (id req-9) in project Alfa."}
    ledger.refresh([old])
    assert ledger.load()["aaaa1111"]["request"] == "req-9"
    ledger.refresh([{**old, "task": "something else"}])                          # the entry already measured is corrected, not duplicated
    assert ledger.load()["aaaa1111"]["request"] == ""


def req_entry(project, session, request, phase, cost, unpriced=0, model="claude-sonnet-5-5"):
    return {"project": project, "session": session, "request": request, "phase": phase,
            "models": {model: {"messages": 1, "input": 10, "output": 5, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 100,
                               "cost_usd": cost, "unpriced_messages": unpriced}}}


REQ_LEDGER = {
    "w1": req_entry("Alfa", "ws-1", "r1", "spec", 0.5),
    "w2": req_entry("Alfa", "ws-1", "r1", "plan", 1.0),
    "w3": req_entry("Alfa", "ws-2", "r1", "build", 3.0, model="claude-opus-5-5"),                    # the next day: another work session
    "w4": req_entry("Alfa", "ws-2", "r1", "build", 1.5, model="claude-opus-5-5"),                    # a fix round: another worker, same phase
    "w5": req_entry("Alfa", "ws-2", "r1", "review", 0.25),
    "w6": req_entry("Beta", "ws-2", "r2", "spec", 0.1, unpriced=1, model="local/odd"),
    "w7": req_entry("Beta", "ws-2", "", "extraction", 9.9),                                         # not for any request
}


def test_a_request_is_totalled_across_phases_rounds_and_work_sessions():
    r1 = usage.request_costs(REQ_LEDGER)["r1"]
    assert r1["cost_usd"] == pytest.approx(6.25) and r1["workers"] == 5 and r1["sessions"] == 2 and r1["project"] == "Alfa"
    assert r1["by_phase"]["build"] == {"cost_usd": pytest.approx(4.5), "workers": 2}                  # both fix rounds
    assert {p: v["workers"] for p, v in r1["by_phase"].items()} == {"spec": 1, "plan": 1, "build": 2, "review": 1}
    assert r1["by_model"]["claude-opus-5-5"]["cost_usd"] == pytest.approx(4.5) and r1["tokens"]["input"] == 50 and r1["unpriced"] is False


def test_workers_not_tied_to_a_request_are_left_out_of_every_request():
    costs = usage.request_costs(REQ_LEDGER)
    assert set(costs) == {"r1", "r2"} and sum(c["cost_usd"] for c in costs.values()) == pytest.approx(6.35)


def test_a_request_with_an_unpriced_model_says_so():
    r2 = usage.request_costs(REQ_LEDGER)["r2"]
    assert r2["unpriced"] is True and r2["by_model"]["local/odd"]["unpriced"] is True and r2["cost_usd"] == pytest.approx(0.1)


def test_no_requests_no_costs():
    assert usage.request_costs({}) == {} and usage.request_costs({"w": req_entry("A", "s", "", "x", 1.0)}) == {}


# ---------------------------------------------------------------- the rolling windows (5 hours, 7 days)
from datetime import datetime, timedelta, timezone  # noqa: E402


def test_the_cost_of_each_message_is_kept_per_hour(tmp_path):
    p = write(tmp_path / "t.jsonl",
              line("claude-sonnet-5-5", u(inp=1_000_000), "a", at="2026-11-08T10:05:00Z"),
              line("claude-sonnet-5-5", u(inp=1_000_000), "b", "r2", at="2026-11-08T10:55:00Z"),
              line("claude-sonnet-5-5", u(out=1_000_000), "c", "r3", at="2026-11-08T11:10:00Z"),
              line("local/odd", u(inp=999), "d", "r4", at="2026-11-08T11:20:00Z"))                   # unpriced: adds no cost
    assert usage.summarize(p)["hourly"] == {"2026-11-08T10": pytest.approx(4.0), "2026-11-08T11": pytest.approx(10.0)}


LEDGER_H = {"w1": {"hourly": {"2026-11-08T10": 6.0, "2026-11-08T11": 3.0}}, "w2": {"hourly": {"2026-11-08T11": 1.0, "2026-11-01T13": 100.0}}, "w3": {}}


def at(h, m=0, day=8):
    return datetime(2026, 11, day, h, m, tzinfo=timezone.utc)


def test_a_window_adds_up_what_fell_inside_it():
    assert usage.spend_in_window(LEDGER_H, at(10), at(12)) == pytest.approx(10.0)                 # both whole hours
    assert usage.spend_in_window(LEDGER_H, at(0), at(9)) == 0


def test_an_hour_only_partly_inside_the_window_counts_for_the_part_that_is():
    assert usage.spend_in_window(LEDGER_H, at(10, 30), at(11)) == pytest.approx(3.0)              # half of the 10 o'clock hour's 6.0
    assert usage.spend_in_window(LEDGER_H, at(11, 30), at(23)) == pytest.approx(2.0)              # half of 3.0 + 1.0


def test_a_seven_day_window_reaches_back_but_not_further():
    now = at(12)
    assert usage.spend_in_window(LEDGER_H, now - timedelta(days=7), now) == pytest.approx(110.0)  # the 100.0 from 7 days ago is inside
    assert usage.spend_in_window(LEDGER_H, now - timedelta(days=6), now) == pytest.approx(10.0)


def test_entries_without_hours_and_bad_keys_add_nothing():
    assert usage.spend_in_window({"w": {"hourly": {"bad-key": 5.0}}, "x": {"hourly": None}}, at(0), at(23, 59)) == 0


def test_the_prices_signature_changes_so_old_ledger_entries_gain_their_hours(world):
    ledger, _, worker = world
    ledger.refresh([worker])
    entry = ledger.load()["aaaa1111"]
    assert "hourly" in entry and sum(entry["hourly"].values()) == pytest.approx(0.016)
