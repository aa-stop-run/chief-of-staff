"""Automatic vault curation: checkpoint -> background curator -> commit of the result."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from curation import CurationError, Curator, curator_prompt  # noqa: E402

NOW = 1_800_000_000.0


class Env:
    """A fake vault + fake launcher/commit/worker states."""

    def __init__(self, tmp_path):
        self.vault = tmp_path / "Vault"
        (self.vault / "inbox").mkdir(parents=True)
        (self.vault / "inbox" / "README.md").write_text("rules", encoding="utf-8")
        self.state = tmp_path / "state" / "curation.json"
        self.clock = NOW
        self.launched, self.commits, self.released = [], [], []
        self.worker_state = {}            # id -> claude state
        self.launch_error = None
        self.curator = Curator(self.vault, self.state, self.launch, lambda wid: self.worker_state.get(wid), self.commit,
                               self.released.append, now=lambda: self.clock)

    def draft(self, name="2026-11-06 Proj - note.md", age_s=3600):
        p = self.vault / "inbox" / name
        p.write_text("---\nproject: x\n---\nbody", encoding="utf-8")
        os.utime(p, (self.clock - age_s, self.clock - age_s))
        return p

    def launch(self, prompt):
        if self.launch_error:
            raise RuntimeError(self.launch_error)
        wid = f"c{len(self.launched) + 1}"
        self.launched.append({"id": wid, "prompt": prompt})
        self.worker_state[wid] = "working"
        return {"id": wid, "name": "cos-curation"}

    def commit(self, path, message):
        self.commits.append((str(path), message))
        return True, f"sha{len(self.commits)}"

    def finish_worker(self, remove_drafts=True):
        if remove_drafts:
            for f in (self.vault / "inbox").glob("*.md"):
                if f.name != "README.md":
                    f.unlink()
        self.worker_state[self.launched[-1]["id"]] = "blocked"   # session open but stopped: it no longer works


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_drafts_ignore_the_readme_and_subfolders(env):
    env.draft("a.md")
    (env.vault / "inbox" / "_review").mkdir()
    (env.vault / "inbox" / "_review" / "b.md").write_text("x", encoding="utf-8")
    (env.vault / "inbox" / "Inbox.base").write_text("x", encoding="utf-8")
    assert [p.name for p in env.curator.drafts()] == ["a.md"]


def test_a_portuguese_vault_uses_its_own_readme_name(tmp_path):
    e = Env(tmp_path)
    pt = Curator(e.vault, e.state, e.launch, lambda w: None, e.commit, e.released.append, now=lambda: e.clock,
                 schema={"readme": "LEIA-ME.md", "review_dir": "_revisao"})
    (e.vault / "inbox" / "LEIA-ME.md").write_text("regras", encoding="utf-8")
    e.draft("a.md")
    assert sorted(p.name for p in pt.drafts()) == ["README.md", "a.md"]       # README.md is a draft for this vault; LEIA-ME.md is not


def test_nothing_to_curate_without_drafts(env):
    env.curator.tick()
    assert env.launched == []
    assert env.curator.status()["drafts"] == 0


def test_waits_for_a_quiet_period_after_the_newest_draft(env):
    env.draft(age_s=60)                         # still warm: a worker may be writing
    env.curator.tick()
    assert env.launched == [] and "calm" in env.curator.status()["info"]
    env.clock += 600
    env.curator.tick()
    assert len(env.launched) == 1


def test_start_makes_a_checkpoint_commit_before_launching_the_curator(env):
    env.draft()
    env.curator.tick()
    assert env.commits and "checkpoint" in env.commits[0][1].lower() and env.commits[0][0] == str(env.vault)
    assert len(env.launched) == 1 and env.curator.status()["running"]


def test_curator_prompt_lists_drafts_and_has_the_safety_rules(env):
    env.draft("2026-11-06 Smart Home - x.md")
    p = curator_prompt(env.vault, env.curator.drafts())
    for needle in ("Smart Home - x.md", "/CLAUDE.md", "inbox/_review", "wiki/hot.md", "wiki/log.md", "contains secrets", "(to be confirmed)",
                   "[DONE]", "inbox/README.md"):
        assert needle in p, needle
    assert str(env.vault).replace("\\", "/") in p


def test_curator_prompt_follows_the_vault_conventions_it_is_given(env):
    env.draft("a.md")
    p = curator_prompt(env.vault, env.curator.drafts(), {"readme": "LEIA-ME.md", "review_dir": "_revisao",
                                                          "hub_dirs": ("Projetos", "Areas"), "name": "Portuguese (European)"})
    assert "inbox/LEIA-ME.md" in p and "inbox/_revisao" in p and "Projetos/ or Areas/" in p and "in Portuguese (European)" in p


def test_only_one_run_at_a_time(env):
    env.draft()
    env.curator.tick()
    env.draft("another.md")
    env.clock += 4000
    env.curator.tick()
    assert len(env.launched) == 1
    with pytest.raises(CurationError):
        env.curator.start(force=True)


def test_finishing_commits_the_result_and_stops_the_session(env):
    env.draft("a.md")
    env.draft("b.md")
    env.curator.tick()
    env.finish_worker()
    env.clock += 120
    env.curator.tick()
    st = env.curator.status()
    assert not st["running"] and st["last"]["handled"] == 2 and st["last"]["remaining"] == 0
    assert "2 drafts" in env.commits[-1][1] and env.commits[-1][0] == str(env.vault)
    assert env.released == ["c1"]


def test_keeps_waiting_while_the_curator_is_working_or_state_unknown(env):
    env.draft()
    env.curator.tick()
    env.curator.tick()
    assert env.curator.status()["running"] and len(env.commits) == 1       # only the checkpoint
    env.worker_state["c1"] = None                                          # claude did not answer
    env.curator.tick()
    assert env.curator.status()["running"]


def test_a_stuck_curator_is_given_up_after_a_timeout(env):
    env.draft()
    env.curator.tick()
    env.clock += 3 * 3600
    env.curator.tick()
    assert not env.curator.status()["running"] and env.released == ["c1"]
    assert "limit" in env.curator.status()["last"]["info"].lower()


def test_minimum_gap_between_runs(env):
    env.draft("a.md")
    env.curator.tick()
    env.finish_worker()
    env.clock += 120
    env.curator.tick()                          # finished
    env.clock += 120
    env.draft("b.md", age_s=3600)
    env.curator.tick()
    assert len(env.launched) == 1               # still within the minimum gap
    env.clock += 3600
    env.curator.tick()
    assert len(env.launched) == 2


def test_failed_launch_counts_as_an_attempt_and_three_failures_stop_the_automation(env):
    env.draft()
    env.launch_error = "claude did not start"
    for _ in range(3):
        env.curator.tick()
        env.clock += 4000
    assert env.launched == [] and env.curator.status()["attempts"] == 3
    env.launch_error = None
    env.curator.tick()
    assert env.launched == [] and "stopped" in env.curator.status()["info"].lower()


def test_force_bypasses_quiet_gap_and_failures_and_resets_attempts(env):
    env.draft(age_s=5)
    env.launch_error = "x"
    for _ in range(3):
        env.curator.tick()
        env.clock += 4000
    env.launch_error = None
    env.curator.start(force=True)
    assert len(env.launched) == 1 and env.curator.status()["attempts"] == 0


def test_a_run_that_handles_nothing_counts_as_failed_attempt(env):
    env.draft()
    env.curator.tick()
    env.finish_worker(remove_drafts=False)
    env.clock += 120
    env.curator.tick()
    st = env.curator.status()
    assert st["last"]["handled"] == 0 and st["attempts"] == 1


def test_state_survives_a_restart(env):
    env.draft()
    env.curator.tick()
    again = Curator(env.vault, env.state, env.launch, lambda wid: env.worker_state.get(wid), env.commit, env.released.append,
                    now=lambda: env.clock)
    assert again.status()["running"]
    assert json.loads(env.state.read_text(encoding="utf-8"))["worker_id"] == "c1"


def test_start_without_drafts_is_refused(env):
    with pytest.raises(CurationError):
        env.curator.start(force=True)


def test_a_worker_missing_from_the_list_right_after_launch_is_not_taken_as_finished(env):
    """Real race: claude's session list is a cache and does not yet include the curator that was just launched."""
    env.draft()
    env.curator.tick()
    env.worker_state["c1"] = "finished"           # the server returns this when the id is not in the list
    env.curator.tick()
    assert env.curator.status()["running"] and env.released == []
    env.clock += 120                              # after the grace period, "finished" counts
    env.curator.tick()
    assert not env.curator.status()["running"] and env.released == ["c1"]
