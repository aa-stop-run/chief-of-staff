"""Model and effort routing per phase of a request, and the cost-by-phase view that lets it be judged."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chief  # noqa: E402
import report  # noqa: E402
from features import Features  # noqa: E402
from test_auto_review import Fake  # noqa: E402


@pytest.fixture
def cfg(monkeypatch):
    def set_(**kw):
        monkeypatch.setitem(chief.CFG, "phase_models", kw.get("models", {}))
        monkeypatch.setitem(chief.CFG, "phase_efforts", kw.get("efforts", {}))
    set_()
    return set_


def test_without_configuration_every_phase_follows_the_request_and_only_questions_default_to_sonnet(cfg):
    assert chief.route("build", "") == (None, None) and chief.route("review", "opus") == ("opus", None)
    assert chief.route("qa", "") == ("sonnet", None)
    assert chief.route("qa", "opus") == ("opus", None)            # a model chosen for the request outranks the built-in default


def test_a_configured_phase_outranks_the_request_and_an_empty_one_follows_it(cfg):
    cfg(models={"plan": "opus", "qa": "", "review": "sonnet"})
    assert chief.route("plan", "haiku") == ("opus", None)
    assert chief.route("review", "") == ("sonnet", None)
    assert chief.route("qa", "") == (None, None)                  # "" switches the built-in default off
    assert chief.route("qa", "haiku") == ("haiku", None)


def test_efforts_are_validated_and_models_never_carry_junk(cfg):
    cfg(models={"plan": "bad name; rm -rf /", "build": 7}, efforts={"plan": "high", "build": "turbo", "qa": "low"})
    assert chief.route("plan", "") == (None, "high")
    assert chief.route("build", "opus") == ("opus", None)
    assert chief.route("qa", "") == ("sonnet", "low")


def test_malformed_sections_are_ignored(monkeypatch):
    monkeypatch.setitem(chief.CFG, "phase_models", ["opus"])
    monkeypatch.setitem(chief.CFG, "phase_efforts", "high")
    assert chief.route("build", "opus") == ("opus", None)


def test_the_command_line_carries_the_effort_only_when_valid():
    p = {"slug": "a", "name": "A", "path": ".", "hub": ""}
    _, cmd, _ = chief.build_command(p, "task", "auto", "opus", (), "x", "high")
    assert cmd[cmd.index("--effort") + 1] == "high" and cmd[cmd.index("--model") + 1] == "opus"
    _, cmd, _ = chief.build_command(p, "task", "auto", None, (), "x", "turbo")
    assert "--effort" not in cmd and "--model" not in cmd
    _, cmd, _ = chief.build_command(p, "task", "auto", None, (), "x")
    assert "--effort" not in cmd


def test_features_launch_each_phase_with_what_the_route_says(tmp_path):
    calls, fake = [], Fake()

    def launch(project, task, model, extra_dirs, label=None, **kw):
        calls.append((label, model, kw))
        return fake.launch(project, task, model, extra_dirs, label=label)

    route = lambda phase, m: ({"spec": ("opus", "high"), "qa": ("sonnet", None)}.get(phase, (m or None, None)))  # noqa: E731
    fs = Features(tmp_path, launch, fake.is_active, route=route)
    f = fs.create("Alfa", "Why", "x", "haiku", kind="feature")
    fs.ask(f["id"], "Why?")
    assert calls[0][1:] == ("opus", {"effort": "high"})            # no effort => no extra argument at all
    assert calls[1][1:] == ("sonnet", {})


def test_features_without_a_route_behave_as_before(tmp_path):
    calls, fake = [], Fake()

    def launch(project, task, model, extra_dirs, label=None):     # the old signature: it must not receive an effort
        calls.append(model)
        return fake.launch(project, task, model, extra_dirs, label=label)

    fs = Features(tmp_path, launch, fake.is_active)
    fs.create("Alfa", "Why", "x", "opus", kind="feature")
    fs.create("Alfa", "Why2", "x", None, kind="feature")
    assert calls == ["opus", None]


def _row(cost, model="m"):
    return {"models": {model: {"cost_usd": cost}}}


def test_cost_by_phase_sums_across_requests_and_models():
    ledger = {"a": {**_row(1.0, "opus"), "phase": "build", "request": "r1"}, "b": {**_row(0.5, "sonnet"), "phase": "build", "request": "r2"},
              "c": {**_row(0.25, "sonnet"), "phase": "qa", "request": "r1"}, "d": _row(0.1)}
    rows = {r["phase"]: r for r in report.phases_view(ledger)}
    assert rows["build"]["cost_usd"] == 1.5 and rows["build"]["workers"] == 2 and rows["build"]["by_model"] == {"opus": 1.0, "sonnet": 0.5}
    assert rows["qa"]["cost_usd"] == 0.25 and rows["other"]["workers"] == 1
    assert [r["phase"] for r in report.phases_view(ledger)][0] == "build"        # most expensive first
