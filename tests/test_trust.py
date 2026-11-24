"""Folders Claude Code does not trust: recognising the refusal, the alerts, and adding a folder only on the user's confirmation."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chief  # noqa: E402
import trust  # noqa: E402

REFUSED = "Added folder not trusted: D:\\Vault. Leave out its --add-dir or, if you trust it and everything in its repository or below it, start `claude` there once."


@pytest.fixture
def cfg(tmp_path):
    """A stand-in for ~/.claude.json: never the real one."""
    p = tmp_path / "claude.json"
    p.write_text(json.dumps({"userID": "secret-id", "oauthAccount": {"emailAddress": "x@y.z"},
                             "projects": {"D:/Chief": {"hasTrustDialogAccepted": True, "allowedTools": ["Read"]}}}), encoding="utf-8")
    return p


# ------------------------------------------------------------------ recognising the refusal

def test_the_refusal_and_the_folder_it_names_are_recognised():
    assert trust.looks_untrusted(REFUSED) and trust.looks_untrusted("Workspace not trusted: open claude here once")
    assert not trust.looks_untrusted("backgrounded · abc123") and not trust.looks_untrusted("")
    assert trust.path_from(REFUSED, "x") == "D:\\Vault"
    assert trust.path_from("Workspace not trusted", "D:\\proj") == "D:\\proj"            # no folder named: the one it was started in


# ------------------------------------------------------------------ reading the trust flags only

def test_a_folder_is_trusted_when_it_or_a_folder_above_it_is(cfg, tmp_path):
    assert trust.is_trusted("D:/Chief", cfg) and trust.is_trusted("D:\\Chief", cfg)
    assert trust.is_trusted("D:/Chief/scripts/deep", cfg)                                # below a trusted folder
    assert not trust.is_trusted("D:/Vault", cfg) and not trust.is_trusted("D:/ChiefOther", cfg)
    assert not trust.is_trusted("D:/Chief", tmp_path / "missing.json")                   # no config: nothing is trusted


def test_an_entry_that_is_not_accepted_does_not_count(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"projects": {"D:/Vault": {"hasTrustDialogAccepted": False}}}), encoding="utf-8")
    assert not trust.is_trusted("D:/Vault", p)


# ------------------------------------------------------------------ granting

def test_grant_sets_only_that_flag_keeps_everything_else_and_leaves_a_backup(cfg, tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    before = json.loads(cfg.read_text(encoding="utf-8"))
    out = trust.grant(vault, cfg)
    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert out["granted"] and trust.is_trusted(vault, cfg)
    assert after["projects"][trust.norm(vault)] == {"hasTrustDialogAccepted": True}
    assert {k: v for k, v in after.items() if k != "projects"} == {k: v for k, v in before.items() if k != "projects"}
    assert after["projects"]["D:/Chief"] == before["projects"]["D:/Chief"]
    assert json.loads(cfg.with_name(cfg.name + trust.BACKUP_SUFFIX).read_text(encoding="utf-8")) == before


def test_grant_keeps_the_other_fields_of_an_existing_entry(cfg, tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["projects"][trust.norm(vault)] = {"hasTrustDialogAccepted": False, "allowedTools": ["Bash(ls:*)"]}
    cfg.write_text(json.dumps(data), encoding="utf-8")
    trust.grant(vault, cfg)
    assert json.loads(cfg.read_text(encoding="utf-8"))["projects"][trust.norm(vault)] == {"hasTrustDialogAccepted": True, "allowedTools": ["Bash(ls:*)"]}


def test_grant_refuses_a_missing_folder_and_an_unreadable_config(cfg, tmp_path):
    with pytest.raises(trust.TrustError, match="does not exist"):
        trust.grant(tmp_path / "nope", cfg)
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    d = tmp_path / "d"
    d.mkdir()
    with pytest.raises(trust.TrustError, match="could not be read"):
        trust.grant(d, bad)
    assert bad.read_text(encoding="utf-8") == "not json"                                # never overwritten


def test_grant_creates_the_config_when_there_is_none(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    p = tmp_path / "new.json"
    trust.grant(d, p)
    assert trust.is_trusted(d, p)


# ------------------------------------------------------------------ the alerts

def make_alerts(tmp_path, cfg, managed):
    return trust.TrustAlerts(tmp_path / "trust.json", lambda: managed, trusted=lambda p: trust.is_trusted(p, cfg))


def test_an_alert_is_kept_until_the_folder_is_trusted(tmp_path, cfg):
    vault = tmp_path / "Vault"
    vault.mkdir()
    al = make_alerts(tmp_path, cfg, [vault])
    assert al.add(vault, "Vault", "vault", REFUSED) is True
    assert al.add(vault, "Vault", "vault", REFUSED) is True and len(al.items()) == 1      # not twice
    assert al.items()[0]["kind"] == "vault"
    trust.grant(vault, cfg)                                                               # trusted meanwhile (e.g. by hand)
    assert al.items() == []


def test_only_folders_the_chief_manages_get_an_alert(tmp_path, cfg):
    proj, other = tmp_path / "proj", tmp_path / "other"
    proj.mkdir()
    other.mkdir()
    al = make_alerts(tmp_path, cfg, [proj])
    assert al.add(other, "x") is False and al.add(proj / "sub", "p") is True and al.add("", "p") is False
    assert al.add("D:/Chief", "c") is False                                               # already trusted


def test_confirm_adds_only_a_folder_that_was_refused(tmp_path, cfg):
    vault, other = tmp_path / "Vault", tmp_path / "other"
    vault.mkdir()
    other.mkdir()
    al = make_alerts(tmp_path, cfg, [vault, other])
    al.add(vault, "Vault", "vault")
    with pytest.raises(trust.TrustError, match="not waiting"):
        trust.confirm(al, other, cfg)                                                     # managed, but no launch was refused for it
    assert not trust.is_trusted(other, cfg)
    assert trust.confirm(al, vault, cfg)["granted"] and trust.is_trusted(vault, cfg) and al.items() == []


# ------------------------------------------------------------------ the launchers tell the dashboard

def test_a_refused_worker_launch_raises_the_alert_and_still_fails(monkeypatch, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    told = []
    monkeypatch.setattr(chief, "UNTRUSTED_HOOK", lambda *a: told.append(a))
    monkeypatch.setattr(chief, "lookup", lambda name, projects: {"name": "proj", "path": str(proj)})
    monkeypatch.setattr(chief, "build_command", lambda *a, **k: ("n", ["claude"], None))

    class R:
        stdout, stderr = "Workspace not trusted", ""

    monkeypatch.setattr(chief.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(RuntimeError):
        chief.launch_worker("proj", "do it")
    assert told and told[0][0] == str(proj) and told[0][1] == "proj" and told[0][2] == "project"


def test_a_launch_that_fails_for_another_reason_raises_no_alert(monkeypatch, tmp_path):
    told = []
    monkeypatch.setattr(chief, "UNTRUSTED_HOOK", lambda *a: told.append(a))
    chief._refused("some other error", str(tmp_path), "p", "project")
    assert told == []


def test_the_doctor_warns_about_an_untrusted_vault(monkeypatch, tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    monkeypatch.setattr(chief, "VAULT", vault)
    monkeypatch.setattr(chief, "CFG", {**chief.CFG, "vault": str(vault)})
    monkeypatch.setattr(chief.trust, "is_trusted", lambda p, cfg_path=None: False)
    names = {c["name"]: c for c in chief.doctor_checks()}
    assert names["Vault trusted by Claude Code"]["level"] == "warn"
    monkeypatch.setattr(chief.trust, "is_trusted", lambda p, cfg_path=None: True)
    assert "Vault trusted by Claude Code" not in {c["name"] for c in chief.doctor_checks()}
