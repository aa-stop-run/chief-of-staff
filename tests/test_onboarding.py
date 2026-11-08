"""Initial extraction of a new project: launches a read-only worker and, when it finishes, forces the curation."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from onboarding import Onboarding  # noqa: E402


class Env:
    def __init__(self, tmp_path):
        self.t, self.active, self.busy, self.curated, self.released, self.launched = 1000.0, True, False, 0, [], []
        self.fail_curate = None
        self.ob = Onboarding(tmp_path / "onboarding.json", self.launch, lambda i: self.active, self.curate,
                             lambda: self.busy, self.released.append, now=lambda: self.t, grace_s=60, timeout_s=3600)

    def launch(self, project, task, label=None):
        self.launched.append((project, task, label))
        return {"id": "abc123", "name": label}

    def curate(self):
        if self.fail_curate:
            raise self.fail_curate
        self.curated += 1


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_start_launches_a_read_only_extraction_worker(env):
    env.ob.start({"name": "new-app"}, "D:/Vault")
    project, task, label = env.launched[0]
    assert project == "new-app" and label == "extraction-new-app"
    assert "READ-ONLY" in task and "D:/Vault/inbox/" in task and "new-app - initial extraction.md" in task
    assert "README.md" in task and "written in English" in task
    assert [x["id"] for x in env.ob.pending()] == ["abc123"]


def test_the_task_follows_the_vault_conventions_it_is_given(tmp_path):
    schema = {"readme": "LEIA-ME.md", "name": "Portuguese (European)", "fm": {"kind": "tipo"}, "project_kind": "projeto"}
    launched = []
    ob = Onboarding(tmp_path / "o.json", lambda p, t, label=None: launched.append(t) or {"id": "x1"}, lambda i: True,
                    lambda: None, lambda: False, lambda i: None, schema=schema)
    ob.start({"name": "app"}, "D:/Vault")
    assert "LEIA-ME.md" in launched[0] and "tipo: projeto" in launched[0] and "Portuguese (European)" in launched[0]


def test_forces_the_curation_once_the_worker_is_done(env):
    env.ob.start({"name": "x"}, "D:/Vault")
    env.t += 30
    env.active = False
    env.ob.tick()                                   # still within the start-up grace period
    assert env.curated == 0 and env.ob.pending()
    env.t += 60
    env.ob.tick()
    assert env.curated == 1 and env.released == ["abc123"] and env.ob.pending() == []


def test_waits_while_the_worker_is_active(env):
    env.ob.start({"name": "x"}, "D:/Vault")
    env.t += 600
    env.ob.tick()
    assert env.curated == 0 and len(env.ob.pending()) == 1


def test_retries_when_a_curation_is_already_running(env):
    env.ob.start({"name": "x"}, "D:/Vault")
    env.t += 120
    env.active, env.busy, env.fail_curate = False, True, RuntimeError("a curation is already running")
    env.ob.tick()
    assert len(env.ob.pending()) == 1 and env.released == []     # kept for the next tick
    env.busy, env.fail_curate = False, None
    env.ob.tick()
    assert env.curated == 1 and env.ob.pending() == []


def test_gives_up_cleanly_when_there_is_nothing_to_curate(env):
    env.ob.start({"name": "x"}, "D:/Vault")
    env.t += 120
    env.active, env.fail_curate = False, RuntimeError("the inbox is empty")
    env.ob.tick()
    assert env.ob.pending() == [] and env.released == ["abc123"]


def test_a_stuck_worker_times_out(env):
    env.ob.start({"name": "x"}, "D:/Vault")
    env.t += 7200
    env.ob.tick()                                   # still "active", but the limit has passed
    assert env.curated == 1 and env.ob.pending() == []


def test_survives_a_corrupt_state_file(env, tmp_path):
    (tmp_path / "onboarding.json").write_text("{not json", encoding="utf-8")
    env.ob.tick()
    assert env.ob.pending() == []


def test_untrusted_folder_queues_the_extraction_and_retries_until_trusted(env):
    trusted = {"ok": False}
    real = env.launch

    def launch(project, task, label=None):
        if not trusted["ok"]:
            raise RuntimeError("claude --bg did not start: Workspace not trusted. Run `claude` once")
        return real(project, task, label=label)

    env.ob._launch = launch
    assert env.ob.start({"name": "fresh"}, "D:/Vault") == {"waiting": True}
    assert env.launched == [] and len(env.ob.pending()) == 1
    env.t += 70
    env.ob.tick()                                   # still untrusted: stays queued
    assert len(env.ob.pending()) == 1 and env.launched == []
    trusted["ok"] = True
    env.t += 70
    env.ob.tick()                                   # now it starts
    assert env.launched[0][0] == "fresh" and env.ob.pending()[0]["id"] == "abc123"


def test_other_launch_errors_are_not_swallowed(env):
    env.ob._launch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network"))
    with pytest.raises(RuntimeError):
        env.ob.start({"name": "x"}, "D:/Vault")
    assert env.ob.pending() == []


def test_gives_up_waiting_for_trust_after_a_day(env):
    env.ob._launch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Workspace not trusted"))
    env.ob.start({"name": "x"}, "D:/Vault")
    env.t += 25 * 3600
    env.ob.tick()
    assert env.ob.pending() == []
