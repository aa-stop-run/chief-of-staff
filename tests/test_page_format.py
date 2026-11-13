"""The page's formatters: durations, money, tokens and "how long ago" (the dashboard never shows a time of day)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "app" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(script: str):
    src = HTML.read_text(encoding="utf-8")
    code = src[src.index("// <format>"):src.index("// </format>")] + "\n" + script
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def call(expr: str):
    return run(f"console.log(JSON.stringify({expr}))")


@pytest.mark.parametrize("seconds,text", [(0, "<1m"), (20, "<1m"), (60, "1m"), (59 * 60, "59m"), (3600, "1h 00m"), (7500, "2h 05m"), (19000, "5h 16m"),
                                          (None, "<1m"), (-5, "<1m")])
def test_durations(seconds, text):
    assert call(f"fmtDuration({json.dumps(seconds)})") == text


@pytest.mark.parametrize("amount,text", [(0, "$0.00"), (1.5, "$1.50"), (12.345, "$12.35"), (0.016, "$0.02"), (0.004, "$0.0040"), (None, "$0.00")])
def test_money(amount, text):
    assert call(f"fmtMoney({json.dumps(amount)})") == text


@pytest.mark.parametrize("n,text", [(0, "0"), (999, "999"), (1500, "2k"), (46000, "46k"), (2_500_000, "2.5M")])
def test_tokens(n, text):
    assert call(f"fmtTokens({n})") == text


NOW = "Date.parse('2026-11-08T12:00:00')"


@pytest.mark.parametrize("then,text", [
    ("2026-11-08T11:59:45", "just now"), ("2026-11-08T11:55:00", "5 min ago"), ("2026-11-08T10:30:00", "1 h ago"),
    ("2026-11-08T08:00:00", "4 h ago"), ("2026-11-07T11:00:00", "1 day ago"), ("2026-11-05T08:00:00", "3 days ago"), ("2026-11-09T12:00:00", "")])
def test_how_long_ago_never_shows_a_time_of_day(then, text):
    assert call(f"ago('{then}', {NOW})") == text


def test_how_long_ago_can_be_translated():
    out = call(f"ago('2026-11-08T11:55:00', {NOW}, (k, v) => k.replace('{{n}}', v.n).replace('min ago', 'min atrás'))")
    assert out == "5 min atrás"


def test_the_dashboard_no_longer_has_a_clock():
    html = HTML.read_text(encoding="utf-8")
    assert "S.updated" not in html and 'tt("Updated")' not in html
    assert "toLocaleTimeString" not in html and ".slice(11,16)" not in html and 'replace("T"," ")' not in html


def test_the_cost_tooltip_of_a_card_lists_the_phases_and_how_far_it_ran():
    c = {"workers": 5, "sessions": 2, "by_phase": {"build": {"cost_usd": 4.5, "workers": 2}, "spec": {"cost_usd": 0.5, "workers": 1}, "review": {"cost_usd": 0.25, "workers": 1}}}
    assert call(f"costTip({json.dumps(c)})") == "Spec $0.50 · Implementation $4.50 (×2) · Review $0.25 — 5 workers, 2 work sessions"


def test_the_cost_tooltip_uses_the_singular_and_can_be_translated():
    c = {"workers": 1, "sessions": 1, "by_phase": {"plan-validation": {"cost_usd": 0.01, "workers": 1}}}
    assert call(f"costTip({json.dumps(c)})") == "Plan validation $0.01 — 1 worker, 1 work session"
    assert call(f"costTip({json.dumps(c)}, (k, v) => ({{'worker': 'worker', 'work session': 'sessão de trabalho', 'Plan validation': 'Validação'}}[k] || k))") == \
        "Validação $0.01 — 1 worker, 1 sessão de trabalho"
