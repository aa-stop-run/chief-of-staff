"""Plan usage: the 5-hour and 7-day percentages from the status line, and workers stopped by a usage limit."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import planusage  # noqa: E402

NOW = datetime(2026, 11, 8, 12, 0, 0)


def epoch(minutes_from_now: float) -> int:
    return int((NOW + timedelta(minutes=minutes_from_now)).timestamp())


def payload(five=None, seven=None, **extra):
    rl = {}
    if five is not None:
        rl["five_hour"] = {"used_percentage": five[0], "resets_at": epoch(five[1])}
    if seven is not None:
        rl["seven_day"] = {"used_percentage": seven[0], "resets_at": epoch(seven[1])}
    return {"model": {"id": "x"}, "session_id": "secret-session", "rate_limits": rl, **extra}


# ---------------------------------------------------------------- the percentages
def test_the_status_line_numbers_are_saved_and_shown(tmp_path):
    path = tmp_path / "plan.json"
    planusage.record_statusline(payload((23.5, 120), (41.2, 4000)), path, NOW)
    v = planusage.view(path, NOW)
    assert v["connected"] is True and v["age_s"] == 0
    assert v["windows"]["five_hour"] == {"percent": 23.5, "level": "ok", "expired": False, "resets_in_s": 7200}
    assert v["windows"]["seven_day"]["percent"] == 41.2 and v["windows"]["seven_day"]["resets_in_s"] == 4000 * 60


def test_nothing_but_the_percentages_and_reset_times_is_stored(tmp_path):
    path = tmp_path / "plan.json"
    planusage.record_statusline(payload((10, 60), (20, 60), cwd="D:/secret/project", transcript_path="C:/x.jsonl"), path, NOW)
    text = path.read_text(encoding="utf-8")
    assert "secret" not in text and "transcript" not in text and "model" not in text and "session_id" not in text
    assert set(json.loads(text)) == {"updated", "five_hour", "seven_day"}


def test_a_payload_without_rate_limits_keeps_the_last_good_numbers(tmp_path):
    path = tmp_path / "plan.json"
    planusage.record_statusline(payload((50, 100)), path, NOW)
    assert planusage.record_statusline({"model": {"id": "x"}}, path, NOW) is None               # an API-key user, or before the first response
    assert planusage.record_statusline(None, path, NOW) is None and planusage.record_statusline("junk", path, NOW) is None
    assert planusage.view(path, NOW)["windows"]["five_hour"]["percent"] == 50


def test_a_window_may_be_missing_on_its_own(tmp_path):
    path = tmp_path / "plan.json"
    planusage.record_statusline(payload(None, (30, 500)), path, NOW)
    w = planusage.view(path, NOW)["windows"]
    assert w["five_hour"] is None and w["seven_day"]["percent"] == 30


def test_badly_typed_windows_are_ignored(tmp_path):
    path = tmp_path / "plan.json"
    planusage.record_statusline({"rate_limits": {"five_hour": {"used_percentage": "lots", "resets_at": 1}, "seven_day": "x"}}, path, NOW)
    assert planusage.view(path, NOW)["windows"] == {"five_hour": None, "seven_day": None}


@pytest.mark.parametrize("pct,level", [(0, "ok"), (69.9, "ok"), (70, "warn"), (89.9, "warn"), (90, "high"), (99.9, "high"), (100, "limit")])
def test_the_level_follows_how_much_is_used(tmp_path, pct, level):
    planusage.record_statusline(payload((pct, 60)), tmp_path / "p.json", NOW)
    assert planusage.view(tmp_path / "p.json", NOW)["windows"]["five_hour"]["level"] == level


def test_a_window_that_has_reset_is_shown_empty_not_with_its_old_percentage(tmp_path):
    path = tmp_path / "plan.json"
    planusage.record_statusline(payload((95, 30), (60, 3000)), path, NOW)
    later = NOW + timedelta(minutes=31)
    w = planusage.view(path, later)["windows"]
    assert w["five_hour"] == {"percent": 0.0, "level": "ok", "expired": True, "resets_in_s": 0}
    assert w["seven_day"]["percent"] == 60 and w["seven_day"]["expired"] is False


def test_the_age_of_the_numbers_is_reported(tmp_path):
    planusage.record_statusline(payload((1, 60)), tmp_path / "p.json", NOW)
    assert planusage.view(tmp_path / "p.json", NOW + timedelta(minutes=7))["age_s"] == 420


def test_without_a_status_line_feed_it_is_simply_not_connected(tmp_path):
    v = planusage.view(tmp_path / "missing.json", NOW)
    assert v == {"connected": False, "age_s": None, "windows": {"five_hour": None, "seven_day": None}}
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert planusage.view(tmp_path / "bad.json", NOW)["connected"] is False


def test_the_status_line_text_is_compact(tmp_path):
    planusage.record_statusline(payload((23.4, 60), (41.6, 600)), tmp_path / "p.json", NOW)
    assert planusage.statusline_text(planusage.view(tmp_path / "p.json", NOW)) == "5h 23% · 7d 42%"
    planusage.record_statusline(payload(None, (5, 600)), tmp_path / "q.json", NOW)
    assert planusage.statusline_text(planusage.view(tmp_path / "q.json", NOW)) == "7d 5%"
    assert planusage.statusline_text(planusage.view(tmp_path / "none.json", NOW)) == ""


# ---------------------------------------------------------------- reading a limit notice
AT = datetime(2026, 11, 6, 11, 29, 1)


def test_the_real_notice_from_the_logs_gives_the_time_it_continues_at():
    n = planusage.parse_notice("Usage limit reached · continuing automatically at 1:30pm · esc to cancel", AT)
    assert n["kind"] == "session" and n["auto"] is True and n["resume_at"] == "2026-11-06T13:30:00"


@pytest.mark.parametrize("text,at,resume", [
    ("Usage limit reached · continuing automatically at 11:30pm · esc to cancel", AT, "2026-11-06T23:30:00"),
    ("Usage limit reached · continuing automatically at 6:30pm · esc to cancel", AT, "2026-11-06T18:30:00"),
    ("Usage limit reached · continuing automatically at 12:15am · esc to cancel", AT, "2026-11-07T00:15:00"),     # tomorrow
    ("Usage limit reached · continuing automatically at 12:00pm", AT, "2026-11-06T12:00:00"),
    ("You've hit your session limit · resets 3:45pm", AT, "2026-11-06T15:45:00"),
    ("You've hit your weekly limit · resets Mon 12:00am", datetime(2026, 11, 5, 9, 0), "2026-11-09T00:00:00"),    # the next Monday
    ("You've hit your weekly limit · resets Thu 12:00am", datetime(2026, 11, 5, 9, 0), "2026-11-12T00:00:00")])  # the same weekday has passed
def test_the_time_a_limit_resets_is_worked_out_from_the_clock_time_in_the_message(text, at, resume):
    assert planusage.parse_notice(text, at)["resume_at"] == resume


def test_the_kind_of_limit_and_whether_claude_code_continues_by_itself():
    s = planusage.parse_notice("You've hit your session limit · resets 3:45pm", AT)
    assert (s["kind"], s["auto"]) == ("session", False)
    assert planusage.parse_notice("You've hit your weekly limit · resets Mon 12:00am", AT)["kind"] == "weekly"
    assert planusage.parse_notice("You've hit your Opus limit · resets 3:45pm", AT)["kind"] == "model"
    assert planusage.parse_notice("Usage limit reached", AT)["resume_at"] is None                      # no time given


@pytest.mark.parametrize("text", ["", "all good", "You've used 85% of your session limit · resets 3:45pm", None])
def test_other_text_is_not_a_limit_notice(text):
    assert planusage.parse_notice(text, AT) is None


def zulu(local: datetime) -> str:
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def log_line(kind, local, content=None, **kw):
    if kind == "system":
        return json.dumps({"type": "system", "subtype": "informational", "level": "notice", "content": content, "timestamp": zulu(local), **kw})
    return json.dumps({"type": kind, "timestamp": zulu(local), "message": {"role": kind, "content": content or "x"}, **kw})


NOTICE = "Usage limit reached · continuing automatically at 1:30pm · esc to cancel"


def test_a_notice_at_the_end_of_a_log_is_found(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([log_line("user", AT - timedelta(minutes=5)), log_line("assistant", AT - timedelta(minutes=1)),
                            log_line("system", AT, NOTICE)]), encoding="utf-8")
    n = planusage.tail_notice(p)
    assert n["resume_at"] == "2026-11-06T13:30:00" and n["auto"] is True and n["at"] == "2026-11-06T11:29:01"


def test_a_notice_the_session_has_moved_on_from_is_not_reported(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([log_line("system", AT, NOTICE), log_line("assistant", AT + timedelta(hours=2), "continuing...")]), encoding="utf-8")
    assert planusage.tail_notice(p) is None


def test_meta_lines_after_a_notice_do_not_count_as_the_session_continuing(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([log_line("system", AT, NOTICE), log_line("user", AT + timedelta(minutes=1), isMeta=True),
                            json.dumps({"type": "last-prompt", "timestamp": zulu(AT + timedelta(minutes=2))})]), encoding="utf-8")
    assert planusage.tail_notice(p)["resume_at"] == "2026-11-06T13:30:00"


def test_only_the_end_of_a_big_log_is_read(tmp_path):
    p = tmp_path / "big.jsonl"
    filler = [log_line("assistant", AT - timedelta(hours=3), "padding " * 200) for _ in range(900)]       # well over the tail size
    p.write_text("\n".join([*filler, log_line("system", AT, NOTICE)]), encoding="utf-8")
    assert p.stat().st_size > planusage.TAIL_BYTES and planusage.tail_notice(p)["auto"] is True


@pytest.mark.parametrize("content", [None, "", "{not json\n\x00\x00"])
def test_logs_without_a_notice_or_with_garbage_give_nothing(tmp_path, content):
    p = tmp_path / "s.jsonl"
    p.write_text(content or log_line("assistant", AT), encoding="utf-8")
    assert planusage.tail_notice(p) is None
    assert planusage.tail_notice(tmp_path / "missing.jsonl") is None


def test_the_cache_reads_a_log_again_only_when_it_changes(tmp_path, monkeypatch):
    p = tmp_path / "s.jsonl"
    p.write_text(log_line("system", AT, NOTICE), encoding="utf-8")
    calls = []
    real = planusage.tail_notice
    monkeypatch.setattr(planusage, "tail_notice", lambda path: calls.append(1) or real(path))
    cache = planusage.NoticeCache()
    assert cache.notice(p) and cache.notice(p) and len(calls) == 1
    with p.open("a", encoding="utf-8") as f:
        f.write("\n" + log_line("assistant", AT + timedelta(hours=2)))
    assert cache.notice(p) is None and len(calls) == 2
    assert cache.notice(None) is None and cache.notice(tmp_path / "gone") is None


def test_how_long_until_a_notice_resets():
    n = {"resume_at": "2026-11-08T13:00:00"}
    assert planusage.resumes_in(n, NOW) == 3600 and planusage.resumes_in(n, NOW + timedelta(hours=2)) == 0
    assert planusage.resumes_in({"resume_at": None}, NOW) is None and planusage.resumes_in(None, NOW) is None


# ---- the real shape: an assistant entry flagged as an API error, not a system notice ----
FIVE_HOUR = "You've hit your 5-hour limit · resets 3:50pm (Europe/Lisbon)"


def api_error(local, text, as_list=True, **kw):
    content = [{"type": "text", "text": text}] if as_list else text
    return json.dumps({"type": "assistant", "timestamp": zulu(local), "isApiErrorMessage": True, "apiError": "usage_limit_reached",
                       "error": "rate_limit", "message": {"role": "assistant", "model": "<synthetic>", "content": content}, **kw})


def turn_duration(local):
    return json.dumps({"type": "system", "subtype": "turn_duration", "timestamp": zulu(local)})


@pytest.mark.parametrize("as_list", [True, False])
def test_an_api_error_entry_with_the_five_hour_limit_is_a_session_notice(tmp_path, as_list):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([log_line("user", AT - timedelta(minutes=1)), api_error(AT, FIVE_HOUR, as_list),
                            turn_duration(AT + timedelta(seconds=1))]), encoding="utf-8")
    n = planusage.tail_notice(p)
    assert n["kind"] == "session" and n["auto"] is False and n["resume_at"] == "2026-11-06T15:50:00"


def test_an_api_error_entry_with_the_seven_day_limit_is_weekly(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([log_line("user", AT - timedelta(minutes=1)),
                            api_error(AT, "You've hit your 7-day limit · resets Mon 12:00am"), turn_duration(AT)]), encoding="utf-8")
    n = planusage.tail_notice(p)
    assert n["kind"] == "weekly" and n["resume_at"] == "2026-11-09T00:00:00"


def test_an_api_error_followed_by_a_normal_message_is_not_reported(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([api_error(AT, FIVE_HOUR), turn_duration(AT), log_line("assistant", AT + timedelta(hours=5), "back at it")]),
                 encoding="utf-8")
    assert planusage.tail_notice(p) is None


def test_prose_that_mentions_the_limit_is_not_a_notice(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([log_line("user", AT - timedelta(minutes=1)),
                            log_line("assistant", AT, "The log says: " + FIVE_HOUR + " and that is what we analyse.")]), encoding="utf-8")
    assert planusage.tail_notice(p) is None


@pytest.mark.parametrize("text,kind", [("You've hit your 5-hour limit · resets 3:50pm (Europe/Lisbon)", "session"),
                                       ("You've hit your 7-day limit · resets 3:50pm", "weekly"),
                                       ("You've hit your Sonnet limit · resets 3:50pm", "model"),
                                       ("You've hit your Fable limit · resets 3:50pm", "model")])
def test_limit_names_and_kinds(text, kind):
    n = planusage.parse_notice(text, AT)
    assert n["kind"] == kind and n["auto"] is False and n["resume_at"] == "2026-11-06T15:50:00"
