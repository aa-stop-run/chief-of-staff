"""Automatic vault curation: checkpoint -> background curator -> commit of the result."""
import json
import os
import sys
from datetime import datetime
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
        self.limits = {}                  # worker id -> limit notice (what the worker's log shows)
        self.curator = Curator(self.vault, self.state, self.launch, lambda wid: self.worker_state.get(wid), self.commit,
                               self.released.append, now=lambda: self.clock, limit=lambda wid: self.limits.get(wid))

    def notice(self, resets_in=None, kind="session"):
        """A usage-limit notice as planusage.tail_notice returns it; resets_in = seconds from the fake clock, None = no time given."""
        at = datetime.fromtimestamp(self.clock)
        return {"kind": kind, "auto": False, "text": "You've hit your limit", "at": at.isoformat(),
                "resume_at": None if resets_in is None else datetime.fromtimestamp(self.clock + resets_in).isoformat()}

    def hit_limit(self, resets_in=3600, kind="session"):
        """The last launched worker stops at a limit: nothing handled, its log shows the notice."""
        self.limits[self.launched[-1]["id"]] = self.notice(resets_in, kind)
        self.finish_worker(remove_drafts=False)

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


# ---- usage limits are pauses, never failures ----
GRACE = 180


def _run_into_limit(env, resets_in=3600):
    env.draft()
    env.curator.tick()
    env.hit_limit(resets_in)
    env.clock += 120
    env.curator.tick()


def test_a_limit_notice_at_the_end_of_a_run_pauses_instead_of_failing(env):
    _run_into_limit(env)
    st = env.curator.status()
    assert not st["running"] and st["attempts"] == 0
    assert st["paused"]["kind"] == "session" and 0 < st["paused"]["until_s"] <= 3600 - 120 + GRACE
    assert st["last"]["handled"] == 0 and not st["last"]["ok"] and "limit" in st["last"]["info"].lower()


def test_no_new_curation_before_reset_plus_grace_and_one_after(env):
    _run_into_limit(env, resets_in=3600)
    env.clock += 3600 - 120 + GRACE - 5            # just before reset + grace (the minimum gap is long over)
    env.curator.tick()
    assert len(env.launched) == 1
    env.clock += 10
    env.curator.tick()
    assert len(env.launched) == 2 and env.curator.status()["paused"] is None


def test_a_pause_that_spans_many_ticks_counts_nothing(env):
    _run_into_limit(env, resets_in=4 * 3600)
    for _ in range(20):
        env.clock += 600
        env.curator.tick()
    assert env.curator.status()["attempts"] == 0 and len(env.launched) == 1


def test_three_limit_paused_runs_never_stop_the_automation(env):
    env.draft()
    for _ in range(5):
        env.curator.tick()
        env.hit_limit(600)
        env.clock += 120
        env.curator.tick()
        env.clock += 1900                              # past the pause and the minimum gap
    env.curator.tick()
    st = env.curator.status()
    assert st["attempts"] == 0 and len(env.launched) == 6 and "stopped" not in st["info"].lower()


def test_three_real_failures_still_stop_the_automation(env):
    env.draft()
    for _ in range(3):
        env.curator.tick()
        env.finish_worker(remove_drafts=False)       # no notice in the log
        env.clock += 120
        env.curator.tick()
        env.clock += 4000
    assert env.curator.status()["attempts"] == 3 and env.curator.status()["paused"] is None
    env.curator.tick()
    assert len(env.launched) == 3


def test_a_timeout_without_a_notice_is_a_failure_but_with_one_it_is_a_pause(env):
    env.draft()
    env.curator.tick()
    env.clock += 3 * 3600
    env.curator.tick()
    assert env.curator.status()["attempts"] == 1
    env.clock += 4000
    env.curator.tick()                               # c2 launched
    env.clock += 3 * 3600
    env.limits["c2"] = env.notice(1800)              # the worker was blocked by a limit the whole time
    env.curator.tick()
    st = env.curator.status()
    assert st["attempts"] == 1 and "limit" in st["last"]["info"].lower()


def test_a_notice_without_a_time_pauses_for_a_conservative_default(env):
    env.draft()
    env.curator.tick()
    env.limits["c1"] = env.notice(None)
    env.finish_worker(remove_drafts=False)
    env.clock += 120
    env.curator.tick()
    st = env.curator.status()
    assert st["attempts"] == 0 and 1500 <= st["paused"]["until_s"] <= 1800 + GRACE
    env.clock += 1800 + GRACE + 10
    env.curator.tick()
    assert len(env.launched) == 2


def test_a_live_worker_with_a_notice_is_reported_paused(env):
    env.draft()
    env.curator.tick()
    env.clock += 200
    env.limits["c1"] = env.notice(1000)
    st = env.curator.status()
    assert st["running"] and st["paused"]["kind"] == "session" and 0 < st["paused"]["until_s"] <= 1000 + GRACE
    env.curator.tick()                                # the run is closed as a pause, not left to time out as a failure
    st = env.curator.status()
    assert not st["running"] and st["attempts"] == 0 and st["paused"] and env.released == ["c1"]


def test_paused_has_durations_only(env):
    _run_into_limit(env)
    p = env.curator.status()["paused"]
    assert set(p) == {"until_s", "kind"} and isinstance(p["until_s"], int)


def test_not_paused_is_none(env):
    env.draft()
    assert env.curator.status()["paused"] is None


def test_a_notice_the_session_moved_on_from_is_a_normal_finish(env):
    env.draft()
    env.curator.tick()
    env.finish_worker()                               # tail_notice -> None: the lookup finds nothing
    env.clock += 120
    env.curator.tick()
    st = env.curator.status()
    assert st["paused"] is None and st["last"]["handled"] == 1 and st["attempts"] == 0


def test_curate_now_works_while_paused(env):
    _run_into_limit(env)
    env.curator.start(force=True)
    assert len(env.launched) == 2 and env.curator.status()["paused"] is None
    env.limits["c2"] = env.notice(3000)               # limit still on: it reports it again at once
    env.finish_worker(remove_drafts=False)
    env.clock += 120
    env.curator.tick()
    assert env.curator.status()["paused"] and env.curator.status()["attempts"] == 0


def _stuck_state(env):
    env.draft()
    env.state.parent.mkdir(parents=True)
    env.state.write_text(json.dumps({"running": False, "worker_id": "old", "attempts": 3, "finished_at": env.clock - 7200,
                                     "last": {"at": env.clock - 7200, "handled": 0, "remaining": 1, "ok": False, "info": "x"}}),
                         encoding="utf-8")


def test_attempts_from_past_limit_failures_recover_instead_of_stopping(env):
    """State left by the old behaviour: 3 failures, but the last worker's log shows a limit notice that has since reset."""
    _stuck_state(env)
    env.limits["old"] = env.notice(-3600)
    assert "stopped" not in env.curator.status()["info"].lower() and env.curator.status()["attempts"] == 0
    env.curator.tick()
    assert len(env.launched) == 1


def test_recovery_waits_if_that_limit_has_not_reset_yet(env):
    _stuck_state(env)
    env.limits["old"] = env.notice(1800)
    env.curator.tick()
    st = env.curator.status()
    assert st["attempts"] == 0 and st["paused"] and env.launched == []


def test_real_failures_without_a_notice_stay_stopped(env):
    _stuck_state(env)
    env.curator.tick()
    assert env.launched == [] and env.curator.status()["attempts"] == 3
