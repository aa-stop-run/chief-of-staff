"""Permission rules per phase (`phase_tools` in projects.json): narrow git/gh rules for the «ship» phases, nothing for any other phase."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chief  # noqa: E402
from features import Features  # noqa: E402
from test_auto_review import Fake, finish_stage  # noqa: E402

P = {"slug": "a", "name": "Alfa", "path": ".", "hub": ""}


@pytest.fixture
def cfg(monkeypatch):
    def set_(value):
        monkeypatch.setitem(chief.CFG, "phase_tools", value)
    set_({})
    return set_


def test_only_the_publication_phase_may_push_and_open_a_pr(cfg):
    allow, deny = chief.tools_for("ship", "review")
    assert "Bash(git push -u:*)" in allow and "Bash(gh pr create:*)" in allow
    assert not any("push" in a for a in chief.tools_for("ship", "build")[0])
    assert "Bash(git push:*)" in chief.tools_for("ship", "build")[1]                # the commit phase can never push
    assert any("--force" in d for d in deny) and any("gh pr merge" in d for d in deny) and any("gh auth" in d for d in deny)


@pytest.mark.parametrize("kind,stage", [("feature", "build"), ("feature", "review"), ("fix", "build"), ("analysis", "review"),
                                        ("maintenance", "build"), ("ship", "qa"), ("release", "review"), ("feature", "plan-validation")])
def test_every_other_phase_gets_no_extra_permissions(cfg, kind, stage):
    assert chief.tools_for(kind, stage) == ([], [])


def test_the_configuration_replaces_an_entry_and_can_switch_it_off(cfg):
    cfg({"ship:review": {"allow": ["Bash(git push -u:*)"], "deny": []}, "release:review": {"allow": ["Bash(./deploy.sh:*)"]}, "ship:build": {}})
    assert chief.tools_for("ship", "review") == (["Bash(git push -u:*)"], [])
    assert chief.tools_for("release", "review") == (["Bash(./deploy.sh:*)"], [])
    assert chief.tools_for("ship", "build") == ([], [])                              # an empty entry switches the default off


def test_invalid_rules_are_dropped_and_the_list_is_capped(cfg):
    cfg({"ship:review": {"allow": ["Bash(git push -u:*)", "rm -rf /", "Bash(a,b)", "", 7, "Bash(" + "x" * 300 + ")"] + [f"Tool{i}" for i in range(40)]}})
    allow, _ = chief.tools_for("ship", "review")
    assert allow[0] == "Bash(git push -u:*)" and "rm -rf /" not in allow and "Bash(a,b)" not in allow and len(allow) == chief.MAX_TOOL_RULES
    cfg("everything")
    assert chief.tools_for("ship", "review") == (chief.tools_for("ship", "review")[0], chief.tools_for("ship", "review")[1])
    cfg({"ship:review": "yes"})
    assert chief.tools_for("ship", "review")[0] == chief.DEFAULT_PHASE_TOOLS["ship:review"]["allow"]


def test_the_command_line_keeps_the_prompt_out_of_the_variadic_options():
    _, cmd, prompt = chief.build_command(P, "task", allow=["Bash(git push -u:*)", "Bash(gh pr create:*)"], deny=["Bash(git push*--force*)"])
    assert "--allowedTools=Bash(git push -u:*),Bash(gh pr create:*)" in cmd and "--disallowedTools=Bash(git push*--force*)" in cmd
    assert cmd[-2:] == ["--", prompt] and cmd.index("--") > cmd.index("--permission-mode")
    _, plain, prompt = chief.build_command(P, "task")
    assert "--" not in plain and plain[-1] == prompt and not any(c.startswith("--allowedTools") for c in plain)


def test_features_pass_the_rules_of_the_phase_they_launch(tmp_path):
    calls, fake = [], Fake()

    def launch(project, task, model, extra_dirs, label=None, **kw):
        calls.append((label, kw))
        return fake.launch(project, task, model, extra_dirs, label=label)

    fs = Features(tmp_path, launch, fake.is_active, tools=lambda kind, stage: chief.tools_for(kind, stage))
    f = fs.create("Alfa", "Commit", "Commit the website.", None, kind="ship")
    assert calls[-1][1]["allow"] == chief.tools_for("ship", "build")[0] and "git push" in " ".join(calls[-1][1]["deny"])
    finish_stage(fs, fake, tmp_path, f["id"], "report.md", "## Execution status\nEXECUTED\n\n## Ready to publish\n`git push -u origin feat/x`\n")
    fs.approve(f["id"])
    assert "Bash(git push -u:*)" in calls[-1][1]["allow"]
    g = fs.create("Alfa", "Login", "x", None, kind="feature")
    assert "allow" not in calls[-1][1] and g["stage"] == "spec"


def test_features_without_a_tools_hook_behave_as_before(tmp_path):
    calls, fake = [], Fake()

    def launch(project, task, model, extra_dirs, label=None):                         # the old signature: no extra arguments allowed
        calls.append(label)
        return fake.launch(project, task, model, extra_dirs, label=label)

    Features(tmp_path, launch, fake.is_active).create("Alfa", "Commit", "x", None, kind="ship")
    assert len(calls) == 1
