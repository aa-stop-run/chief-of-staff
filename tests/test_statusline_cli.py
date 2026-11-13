"""The status line command Claude Code runs: it feeds the plan percentages to the Chief and must never break the session."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CHIEF = str(Path(__file__).resolve().parent.parent / "scripts" / "chief.py")


def run(home, payload, *extra):
    env = {**os.environ, "CHIEF_HOME": str(home)}
    return subprocess.run([sys.executable, CHIEF, "statusline", *extra], input=payload, capture_output=True, text=True, env=env, timeout=60)


def payload(five=23.4, seven=41.6):
    t = int(time.time())
    return json.dumps({"rate_limits": {"five_hour": {"used_percentage": five, "resets_at": t + 3600}, "seven_day": {"used_percentage": seven, "resets_at": t + 86400}}})


def test_it_prints_the_percentages_and_saves_them(tmp_path):
    r = run(tmp_path, payload())
    assert r.returncode == 0 and r.stdout.strip() == "5h 23% · 7d 42%"
    saved = json.loads((tmp_path / "state" / "plan_usage.json").read_text(encoding="utf-8"))
    assert saved["five_hour"]["used_percentage"] == 23.4


def test_it_never_fails_on_garbage_or_empty_input(tmp_path):
    for bad in ("", "not json", "[]", '{"rate_limits": 3}'):
        r = run(tmp_path, bad)
        assert r.returncode == 0 and r.stdout.strip() == ""


def test_it_shows_another_status_line_first(tmp_path):
    r = run(tmp_path, payload(), "--then", f'"{sys.executable}" -c "print(\'mine\')"')
    assert r.returncode == 0 and r.stdout.strip() == "mine 5h 23% · 7d 42%"
