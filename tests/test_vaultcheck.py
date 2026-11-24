"""scripts/vaultcheck.py: the checks that need no worker, the ledger, what a worker's findings must satisfy, and the one way a note changes."""
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import vaultcheck as vc  # noqa: E402

SCHEMA = {"hub_dirs": ("Projetos", "Areas"), "fm": {"kind": "tipo", "status": "estado", "project": "projeto"}}
HUB = "---\ntipo: projeto\nestado: ativo\nstack: [Python]\nlocal: {local}\nrepo: (sem remoto)\natualizado: 2026-11-13\n---\n# {name}\nO repo não tem remoto.\n"


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "Vault"
    (v / "Projetos" / "Alfa").mkdir(parents=True)
    (v / "wiki").mkdir()
    (v / "inbox").mkdir()
    proj = tmp_path / "alfa-repo"
    proj.mkdir()
    (v / "Projetos" / "Alfa.md").write_text(HUB.format(local=proj, name="Alfa"), encoding="utf-8")
    (v / "Projetos" / "Alfa" / "Estado.md").write_text("# Estado\nA porta é 8780.\n", encoding="utf-8")
    (v / "00-Index.md").write_text("- [[Alfa]]\n", encoding="utf-8")
    (v / "Mapa.canvas").write_text('{"nodes":[{"text":"Alfa"}]}', encoding="utf-8")
    (v / "wiki" / "hot.md").write_text("---\nupdated: 2026-11-13\n---\n# Hot\n", encoding="utf-8")
    (v / "wiki" / "log.md").write_text("---\ntitle: Log\n---\n# Wiki Log\n\nNewest first.\n\n## 2026-11-13 — curadoria\n- [[old]]\n", encoding="utf-8")
    return v


def kinds(findings):
    return sorted(f["kind"] for f in findings)


# ------------------------------------------------------------------ L1, no worker

def test_a_consistent_vault_has_no_structure_findings(vault):
    assert vc.structure_findings(vault, SCHEMA) == []


def test_a_hub_whose_local_folder_is_gone_or_that_is_missing_from_the_index_is_reported(vault, tmp_path):
    hub = vault / "Projetos" / "Alfa.md"
    hub.write_text(HUB.format(local=tmp_path / "nope", name="Alfa"), encoding="utf-8")
    (vault / "00-Index.md").write_text("- nothing\n", encoding="utf-8")
    out = vc.structure_findings(vault, SCHEMA)
    assert kinds(out) == ["local-missing", "not-in-index"] and all(f["status"] == "open" and f["evidence"] for f in out)
    assert next(f for f in out if f["kind"] == "local-missing")["quote"].startswith("local: ")


def test_a_wrong_local_is_fixed_with_the_path_the_chief_knows_and_a_missing_index_entry_by_adding_it(vault, tmp_path):
    real = tmp_path / "alfa-repo"
    hub = vault / "Projetos" / "Alfa.md"
    hub.write_text(HUB.format(local=tmp_path / "old-place", name="Alfa"), encoding="utf-8")
    (vault / "00-Index.md").write_text("- [[Other]]\n- [[Another]]\n", encoding="utf-8")
    out = {f["kind"]: f for f in vc.structure_findings(vault, SCHEMA, known_paths={"Alfa": str(real)})}
    loc, idx = out["local-missing"], out["not-in-index"]
    assert loc["replace"]["new"] == f"local: {real}" and idx["note"] == "00-Index.md" and idx["replace"]["new"].endswith("\n- [[Alfa]]")
    for f in (loc, idx):                                                                          # the user's click applies them
        vc.apply_replace(vault, f["note"], f["replace"]["old"], f["replace"]["new"], f["base_hash"])
    assert f"local: {real}" in hub.read_text(encoding="utf-8") and "- [[Alfa]]" in (vault / "00-Index.md").read_text(encoding="utf-8")
    assert vc.structure_findings(vault, SCHEMA, known_paths={"Alfa": str(real)}) == []
    hub.write_text(HUB.format(local=tmp_path / "gone", name="Alfa"), encoding="utf-8")
    assert vc.structure_findings(vault, SCHEMA)[0]["replace"] is None                               # a project the Chief does not know: no guess


def test_missing_frontmatter_keys_of_a_hub_are_reported(vault):
    (vault / "Projetos" / "Alfa.md").write_text("---\ntipo: projeto\nlocal: " + str(vault) + "\n---\n# Alfa\n", encoding="utf-8")
    out = vc.structure_findings(vault, SCHEMA)
    assert kinds(out) == ["frontmatter", "frontmatter", "frontmatter"]                              # estado, stack, repo


def test_a_secret_or_a_private_ip_or_an_email_in_a_note_is_high_and_never_quoted(vault):
    (vault / "Projetos" / "Alfa" / "Estado.md").write_text("# Estado\nservidor 192.168.1.10\nchave sk-abcdefghijklmnopqrstuvwx\nmail a@b.pt\n", encoding="utf-8")
    out = vc.structure_findings(vault, SCHEMA)
    secrets = [f for f in out if f["kind"] == "secret"]
    assert len(secrets) == 3 and all(f["severity"] == "high" for f in secrets)
    assert not any("192.168" in json.dumps(f) or "sk-abc" in json.dumps(f) or "a@b.pt" in json.dumps(f) for f in secrets)


def test_the_inbox_backlog_is_a_health_finding(vault):
    out = vc.structure_findings(vault, SCHEMA, drafts=17, oldest_draft_s=10 * 86400)
    assert kinds(out) == ["backlog"] and "17 draft(s)" in out[0]["evidence"][0] and "2026-11-13" in out[0]["evidence"][1]
    assert vc.structure_findings(vault, SCHEMA, drafts=2, oldest_draft_s=3600) == []


def test_the_plugin_linter_is_normalised_and_optional(vault):
    report = {"dangling_links": [{"path": "wiki/a.md", "target": "Ghost", "reason": "no such note"}], "orphans": [{"path": "wiki/b.md"}], "x": []}
    out = vc.plugin_lint(vault, lint_fn=lambda root: report)
    assert kinds(out) == ["lint-dangling_links", "lint-orphans"] and out[0]["note"] == "wiki/a.md" and out[0]["severity"] == "low"
    assert vc.plugin_lint(vault, lint_fn=lambda root: 1 / 0) == []                                  # a broken helper adds nothing


def test_notes_and_projects(vault):
    notes = vc.note_paths(vault, SCHEMA["hub_dirs"])
    assert notes == ["Projetos/Alfa.md", "Projetos/Alfa/Estado.md"]
    assert {vc.project_of(n, SCHEMA["hub_dirs"]) for n in notes} == {"Alfa"} and vc.project_of("wiki/hot.md", SCHEMA["hub_dirs"]) == ""


# ------------------------------------------------------------------ the ledger and fingerprints

def test_a_note_is_stale_when_its_text_the_head_or_the_facts_changed():
    entry = {"hash": "a", "head": "h1", "facts": "f1"}
    assert vc.is_stale(None, "a", "h1", "f1") and vc.is_stale({}, "a", "h1", "f1")
    assert not vc.is_stale(entry, "a", "h1", "f1")
    assert vc.is_stale(entry, "b", "h1", "f1") and vc.is_stale(entry, "a", "h2", "f1") and vc.is_stale(entry, "a", "h1", "f2")


def test_a_fingerprint_ignores_spacing_and_follows_the_evidence():
    a = vc.fingerprint("stale", "n.md", "no  remote", ["x  y"])
    assert a == vc.fingerprint("stale", "n.md", "no remote", ["x y"])
    assert a != vc.fingerprint("stale", "n.md", "no remote", ["other evidence"]) and a != vc.fingerprint("stale", "m.md", "no remote", ["x y"])


# ------------------------------------------------------------------ what a worker's findings must satisfy

STAGED = {"Projetos/Alfa.md": "O repo não tem remoto.\nA porta é 8780.\n"}


def good(**kw):
    f = {"kind": "stale", "severity": "medium", "note": "Projetos/Alfa.md", "quote": "O repo não tem remoto.",
         "evidence": ["`git remote -v` shows origin"], "replace": {"old": "O repo não tem remoto.", "new": "O repo tem o remoto origin."}}
    f.update(kw)
    return {"findings": [f]}


def test_a_valid_finding_is_normalised():
    [f] = vc.validate_findings(good(), STAGED)
    assert f["layer"] == "audit" and f["status"] == "open" and f["id"] and f["replace"]["new"].startswith("O repo tem")


@pytest.mark.parametrize("mutate,why", [
    (dict(kind="other"), "kind"), (dict(severity="urgent"), "kind"), (dict(note="Projetos/Ghost.md"), "not a note"),
    (dict(quote="not in the note"), "quoted text"), (dict(evidence=[]), "evidence"), (dict(evidence=["x", ""]), "evidence"),
    (dict(replace={"old": "absent text", "new": "x"}), "exactly once"), (dict(replace={"old": "e", "new": "x"}), "exactly once"),
    (dict(replace={"old": "", "new": "x"}), "replace"), (dict(evidence=["ssh to 10.0.0.5"]), "secret"),
    (dict(replace={"old": "O repo não tem remoto.", "new": "mail me at a@b.pt"}), "secret")])
def test_a_bad_finding_refuses_the_whole_file(mutate, why):
    with pytest.raises(vc.FindingsError, match=why):
        vc.validate_findings(good(**mutate), STAGED)


def test_the_shape_and_the_size_are_checked():
    for bad in ([], {"findings": "x"}, {"findings": [1]}, {"findings": [good()["findings"][0]] * 41}):
        with pytest.raises(vc.FindingsError):
            vc.validate_findings(bad, STAGED)
    assert vc.validate_findings({"findings": []}, STAGED) == []                                      # a clean note: no finding


# ------------------------------------------------------------------ the only way a note changes

def test_apply_replaces_exactly_once_and_only_if_the_note_is_as_it_was(vault):
    rel = "Projetos/Alfa.md"
    before = vc.sha(vc.read_note(vault, rel))
    after = vc.apply_replace(vault, rel, "O repo não tem remoto.", "O repo tem o remoto origin.", before)
    text = vc.read_note(vault, rel)
    assert "O repo tem o remoto origin." in text and "não tem remoto" not in text and after == vc.sha(text)
    with pytest.raises(vc.ApplyError, match="changed"):
        vc.apply_replace(vault, rel, "origin", "x", before)                                          # the hash no longer matches


def test_apply_refuses_what_is_not_there_once_or_is_a_secret_or_outside_notes(vault):
    rel = "Projetos/Alfa.md"
    h = vc.sha(vc.read_note(vault, rel))
    with pytest.raises(vc.ApplyError, match="exactly once"):
        vc.apply_replace(vault, rel, "absent", "x", h)
    with pytest.raises(vc.ApplyError, match="secret"):
        vc.apply_replace(vault, rel, "O repo não tem remoto.", "ver 192.168.0.9", h)
    (vault / "inbox" / "d.md").write_text("draft", encoding="utf-8")
    for bad in ("inbox/d.md", "../Vault/Projetos/Alfa.md", "Projetos/missing.md", "00-Index.md.bak"):
        with pytest.raises(vc.ApplyError):
            vc.apply_replace(vault, bad, "x", "y", "h")
    assert vc.read_note(vault, rel).count("O repo não tem remoto.") == 1                             # nothing changed


def test_the_log_gets_a_new_entry_at_the_top_newest_first(vault):
    vc.log_entry(vault, "vault sanitizer: 1 note fixed", ["Projetos/Alfa.md"], today=date(2026, 11, 24))
    text = (vault / "wiki" / "log.md").read_text(encoding="utf-8")
    assert text.index("## 2026-11-24 — vault sanitizer") < text.index("## 2026-11-13 — curadoria")
    assert "- [[Projetos/Alfa]]" in text and text.startswith("---\ntitle: Log\n---\n# Wiki Log")
    vc.log_entry(vault / "nope", "x", [])                                                            # no log: nothing happens
