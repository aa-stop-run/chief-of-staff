"""`chief.py doctor`: reports what the machine has and what is missing, without changing anything."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chief  # noqa: E402


class Fake:
    """Pretend which programs exist: name -> (version line, help text)."""

    def __init__(self, tools):
        self.tools = tools

    def which(self, name):
        return f"/bin/{name}" if name in self.tools else None

    def run(self, cmd, **kw):
        tool, arg = cmd[0], cmd[1] if len(cmd) > 1 else ""
        version, help_text = self.tools.get(tool, ("", ""))
        out = help_text if arg == "--help" else version
        return subprocess.CompletedProcess(cmd, 0, out, "")


def checks(monkeypatch, tools):
    fake = Fake(tools)
    monkeypatch.setattr(chief.shutil, "which", fake.which)
    monkeypatch.setattr(chief.subprocess, "run", fake.run)
    return {c["name"]: c for c in chief.doctor_checks()}


FULL = {"git": ("git version 2.45.0", ""), "claude": ("2.1.319 (Claude Code)", "  --bg, --background  Start the session in the background"),
        "gh": ("gh version 2.60.0", ""), "node": ("v24.0.0", "")}


def test_a_complete_machine_has_no_failures(monkeypatch):
    c = checks(monkeypatch, FULL)
    assert {k: v["level"] for k, v in c.items() if k in ("Python", "git", "Claude Code", "GitHub CLI (gh)", "Node.js")} == {
        "Python": "ok", "git": "ok", "Claude Code": "ok", "GitHub CLI (gh)": "ok", "Node.js": "ok"}
    assert "2.1.319" in c["Claude Code"]["detail"]


def test_missing_required_tools_fail_with_an_install_hint(monkeypatch):
    c = checks(monkeypatch, {"gh": FULL["gh"]})
    assert c["git"]["level"] == "fail" and "git-scm.com" in c["git"]["hint"]
    assert c["Claude Code"]["level"] == "fail" and "background" not in c["Claude Code"]["hint"] and c["Claude Code"]["hint"]


def test_optional_tools_only_warn(monkeypatch):
    c = checks(monkeypatch, {k: FULL[k] for k in ("git", "claude")})
    assert c["GitHub CLI (gh)"]["level"] == "warn" and c["Node.js"]["level"] == "warn"


def test_a_claude_without_background_sessions_fails(monkeypatch):
    c = checks(monkeypatch, {**FULL, "claude": ("1.0.0 (Claude Code)", "  --model <model>")})
    assert c["Claude Code"]["level"] == "fail" and "--bg" in c["Claude Code"]["detail"]


def test_the_provider_environment_is_reported_by_name_never_by_value(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://user:secret@gateway.example/v1")
    monkeypatch.setenv("ANTHROPIC_MODEL", "my-model")
    detail = checks(monkeypatch, FULL)["Model / provider"]["detail"]
    assert "ANTHROPIC_BASE_URL" in detail and "ANTHROPIC_MODEL" in detail and "secret" not in detail and "gateway.example" not in detail


def test_the_command_exits_non_zero_when_something_required_is_missing(monkeypatch, capsys):
    checks(monkeypatch, {})
    with pytest.raises(SystemExit) as e:
        chief.cmd_doctor(None)
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "[FAIL] git" in out and "Something required is missing" in out


def test_the_command_says_how_to_start_when_everything_is_there(monkeypatch, capsys):
    checks(monkeypatch, FULL)
    chief.cmd_doctor(None)
    assert "Ready. Start the dashboard with: python app/server.py" in capsys.readouterr().out
