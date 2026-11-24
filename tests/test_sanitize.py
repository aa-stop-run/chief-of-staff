"""scripts/sanitize.py: the orchestrator, with a fake launcher: staging (copies only), one run at a time, the findings, apply, dismissals."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sanitize as sz  # noqa: E402
import vaultcheck as vc  # noqa: E402

SCHEMA = {"hub_dirs": ("Projetos", "Areas"), "fm": {"kind": "tipo", "status": "estado", "project": "projeto"}}
HUB = "---\ntipo: projeto\nestado: ativo\nstack: [Python]\nlocal: {local}\nrepo: (sem remoto)\n---\n# Alfa\nO repo não tem remoto.\n"


class Env:
    def __init__(self, tmp):
        self.t = 1_800_000_000.0
        self.vault = tmp / "Vault"
        (self.vault / "Projetos" / "Alfa").mkdir(parents=True)
        (self.vault / "wiki").mkdir()
        self.repo = tmp / "alfa"
        self.repo.mkdir()
        (self.vault / "Projetos" / "Alfa.md").write_text(HUB.format(local=self.repo), encoding="utf-8")
        (self.vault / "Projetos" / "Alfa" / "Estado.md").write_text("# Estado\nA porta é 8780.\n", encoding="utf-8")
        (self.vault / "wiki" / "log.md").write_text("# Log\n\n## 2026-11-13 — x\n- [[a]]\n", encoding="utf-8")
        self.facts = {"exists": True, "head": "h1", "remote": ["https://github.com/o/r.git"]}
        self.launched, self.states, self.commits, self.curation, self.cfg = [], {}, [], {}, {}
        self.s = sz.Sanitizer(self.vault, tmp / "sanitize.json", tmp / "stage", self.launch, lambda i: self.states.get(i, "working"),
                              lambda p, m: (self.commits.append(m) or (True, "abc123")), lambda i: None, SCHEMA,
                              curator=lambda: self.curation, config=lambda: self.cfg, now=lambda: self.t, collect=lambda f: dict(self.facts))

    def launch(self, prompt, add_dir):
        w = {"id": f"w{len(self.launched)}", "name": "cos-sanitize"}
        self.launched.append({"prompt": prompt, "stage": Path(add_dir), **w})
        return w

    def finish(self, findings=None, raw=None):
        run = self.launched[-1]
        (run["stage"] / "findings.json").write_text(raw if raw is not None else json.dumps({"findings": findings or []}), encoding="utf-8")
        self.states[run["id"]] = "finished"
        self.t += 120
        self.s.tick()


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def remote_finding():
    return {"kind": "stale", "severity": "medium", "note": "Projetos/Alfa.md", "quote": "O repo não tem remoto.",
            "evidence": ["facts/Alfa.json: remote is origin"], "replace": {"old": "O repo não tem remoto.", "new": "O repo tem o remoto origin."}}


def test_the_worker_gets_copies_and_facts_never_the_vault(env):
    out = env.s.start(force=True)
    assert out["launched"] and out["jobs"] == 2                                   # freshness + contradictions (two notes)
    run = env.launched[0]
    stage = run["stage"]
    assert env.vault not in stage.parents and str(env.vault).replace("\\", "/") not in run["prompt"]
    assert (stage / "notes" / "Projetos" / "Alfa.md").read_text(encoding="utf-8").startswith("---")
    assert json.loads((stage / "facts" / "Alfa.json").read_text(encoding="utf-8"))["remote"] == ["https://github.com/o/r.git"]
    assert "facts/Alfa.json" in run["prompt"] and "Projetos/Alfa/Estado.md" in run["prompt"] and "findings.json" in run["prompt"]


def test_one_run_at_a_time_and_nothing_when_off(env):
    env.s.start(force=True)
    with pytest.raises(sz.SanitizeError, match="running"):
        env.s.start(force=True)
    env.cfg = {"enabled": False}
    env.s2 = None
    assert env.s.status()["enabled"] is False


def test_it_waits_for_the_curation(env):
    env.curation = {"running": True}
    with pytest.raises(sz.SanitizeError, match="curation is running"):
        env.s.start(force=True)
    env.curation = {"blocked": {"why": "trust"}}
    with pytest.raises(sz.SanitizeError, match="trust"):
        env.s.start(force=True)
    assert env.launched == []


def test_a_finding_is_stored_and_the_ledger_stops_a_second_check(env):
    env.s.start(force=True)
    env.finish([remote_finding()])
    st = env.s.status()
    assert st["open"] == 1 and st["findings"][0]["replace"] is True and st["last"]["ok"] and not st["running"]
    assert not (env.launched[0]["stage"]).exists()                                # staging removed
    env.t += 4000
    assert env.s.start(force=True)["launched"] is False                          # nothing is stale: no worker
    env.facts["head"] = "h2"                                                      # the project moved
    assert env.s.start(force=True)["launched"] is True


def test_a_refused_findings_file_is_a_failure_and_stores_nothing(env):
    env.s.start(force=True)
    bad = remote_finding() | {"quote": "text that is not in the note"}
    env.finish([bad])
    st = env.s.status()
    assert st["open"] == 0 and st["last"]["ok"] is False and "refused" in st["last"]["info"] and st["attempts"] == 1
    env.t += 4000
    env.s.start()                                                                 # not forced: the failures add up
    env.finish(raw="not json")
    assert env.s.status()["attempts"] == 2 and "no readable" in env.s.status()["last"]["info"]


def test_three_failures_stop_the_automation_until_a_manual_check(env):
    for _ in range(3):
        env.s.start()
        env.finish(raw="{")
        env.t += 4000
    env.s.tick()
    assert len(env.launched) == 3 and "stopped" in env.s.status()["info"]
    assert env.s.start(force=True)["launched"]                                    # the button resets it


def test_apply_changes_the_note_with_two_commits_and_a_log_line(env):
    env.s.start(force=True)
    env.finish([remote_finding()])
    fid = env.s.status()["findings"][0]["id"]
    out = env.s.apply(fid)
    assert out["note"] == "Projetos/Alfa.md" and out["commit"] == "abc123"
    assert "O repo tem o remoto origin." in vc.read_note(env.vault, "Projetos/Alfa.md")
    assert env.commits[0].startswith("checkpoint") and env.commits[1].startswith("vault sanitizer: Projetos/Alfa.md")
    assert "vault sanitizer" in (env.vault / "wiki" / "log.md").read_text(encoding="utf-8")
    assert env.s.status()["open"] == 0


def test_apply_refuses_a_note_that_changed_and_asks_for_a_new_check(env):
    env.s.start(force=True)
    env.finish([remote_finding()])
    fid = env.s.status()["findings"][0]["id"]
    p = env.vault / "Projetos" / "Alfa.md"
    p.write_text(p.read_text(encoding="utf-8") + "edited in Obsidian\n", encoding="utf-8")
    with pytest.raises(sz.SanitizeError, match="changed"):
        env.s.apply(fid)
    assert "edited in Obsidian" in p.read_text(encoding="utf-8") and "não tem remoto" in p.read_text(encoding="utf-8")
    assert env.s.status()["open"] == 0 and env.s.start(force=True)["launched"]    # forgotten: it is checked again


def test_apply_waits_for_the_curation(env):
    env.s.start(force=True)
    env.finish([remote_finding()])
    env.curation = {"running": True}
    with pytest.raises(sz.SanitizeError, match="curation"):
        env.s.apply(env.s.status()["findings"][0]["id"])
    assert env.commits == []


def test_a_dismissed_finding_does_not_come_back_but_a_changed_one_does(env):
    env.s.start(force=True)
    env.finish([remote_finding()])
    env.s.dismiss(env.s.status()["findings"][0]["id"])
    env.facts["head"] = "h2"
    env.t += 4000
    env.s.start(force=True)
    env.finish([remote_finding()])
    assert env.s.status()["open"] == 0                                            # same quote and evidence: stays dismissed
    env.facts["head"] = "h3"
    env.t += 4000
    env.s.start(force=True)
    env.finish([remote_finding() | {"evidence": ["a different proof"]}])
    assert env.s.status()["open"] == 1


def test_an_accepted_finding_is_kept_but_not_open(env):
    env.s.start(force=True)
    env.finish([remote_finding()])
    env.s.accept(env.s.status()["findings"][0]["id"])
    assert env.s.status()["open"] == 0
    with pytest.raises(sz.SanitizeError):
        env.s.accept("nope")


def test_max_notes_limits_a_run_and_the_rest_waits(env):
    env.cfg = {"max_notes": 1}
    out = env.s.start(force=True)
    assert out["leftovers"] == 1
    env.finish([])
    assert "1 more waiting" in env.s.status()["last"]["info"]


def test_a_closed_request_queues_its_project_once(env):
    reqs = [{"id": "f1", "closed": {"at": 1}, "path": str(env.repo)}, {"id": "f2", "path": str(env.repo)}]
    assert env.s.note_closed(reqs) == 1 and env.s.note_closed(reqs) == 0
    assert env.s.status()["queued"] == 1
    env.s.start()                                                                 # due because a project changed
    env.finish([])
    assert env.s.status()["queued"] == 0


def test_a_usage_limit_pauses_without_counting_a_failure(env):
    env.s._limit = lambda wid: {"resume_at": "2030-01-01T00:00:00"}
    env.s.start(force=True)
    env.t += 120
    env.s.tick()
    st = env.s.status()
    assert st["running"] is False and st["attempts"] == 0 and st["last"]["ok"] and "usage limit" in st["last"]["info"]


def test_the_structure_layer_needs_no_worker(env):
    (env.vault / "Projetos" / "Alfa" / "Estado.md").write_text("servidor 192.168.1.5\n", encoding="utf-8")
    found = env.s.refresh_structure()
    assert any(f["kind"] == "secret" for f in found) and env.s.status()["counts"].get("structure", 0) >= 1 and env.launched == []


def test_a_structure_finding_with_a_replacement_is_applied_like_any_other(env):
    (env.vault / "00-Index.md").write_text("- [[Other]]" + chr(10), encoding="utf-8")
    env.s.refresh_structure()
    [f] = [x for x in env.s.status()["findings"] if x["note"] == "00-Index.md"]
    assert f["replace"] is True
    env.s.apply(f["id"])
    assert "- [[Alfa]]" in (env.vault / "00-Index.md").read_text(encoding="utf-8")
    env.s.refresh_structure()
    assert not [x for x in env.s.status()["findings"] if x["note"] == "00-Index.md"]
