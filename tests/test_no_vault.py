"""The vault is optional: without one nothing refers to it, reads it, or writes into a folder that merely looks like one."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chief  # noqa: E402
import settings  # noqa: E402
from features import Features  # noqa: E402
from test_auto_review import Fake  # noqa: E402

PROJECT = {"name": "Alfa", "slug": "alfa", "path": "x", "hub": None}


@pytest.fixture
def nowhere(tmp_path, monkeypatch):
    """No vault configured (the default), and a working directory that happens to contain `inbox/` and `Projects/` folders."""
    for d in ("inbox", "Projects"):
        (tmp_path / d).mkdir()
    (tmp_path / "Projects" / "Alfa.md").write_text('---\nlocal: "D:/x"\ndomain: home\n---\n', encoding="utf-8")
    (tmp_path / "inbox" / "stray.md").write_text("not a draft", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(chief, "VAULT", Path(""))
    monkeypatch.setattr(chief, "CFG", {**chief.CFG, "vault": "", "roots": [], "extras": []})
    monkeypatch.setattr(chief, "load_workers", lambda: [])
    return tmp_path


def test_a_missing_or_empty_vault_is_not_enabled(nowhere, tmp_path, monkeypatch):
    assert chief.vault_enabled() is False
    monkeypatch.setattr(chief, "VAULT", tmp_path / "does-not-exist")
    assert chief.vault_enabled() is False
    monkeypatch.setattr(chief, "VAULT", tmp_path)
    assert chief.vault_enabled() is True


def test_nothing_is_read_from_the_working_directory(nowhere):
    assert chief.hubs() == [] and chief.inbox_notes() == []


def test_the_worker_prompt_without_a_vault_has_no_vault_rules_and_no_add_dir(nowhere):
    _, cmd, prompt = chief.build_command(PROJECT, "do it")
    assert "--add-dir" not in cmd
    assert "vault" not in prompt.lower() and "inbox" not in prompt.lower()
    assert "5. Your last message starts with [DONE]" in prompt and "do it" in prompt


def test_the_worker_prompt_with_a_vault_asks_for_a_note_and_gets_the_folder(nowhere, tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(chief, "VAULT", vault)
    _, cmd, prompt = chief.build_command(PROJECT, "do it")
    assert cmd[cmd.index("--add-dir") + 1] == str(vault)
    assert "inbox/" in prompt and "README.md" in prompt


def test_adding_a_project_leaves_no_draft_anywhere(nowhere, tmp_path, monkeypatch):
    monkeypatch.setattr(chief, "HOME", tmp_path / "home")
    folder = tmp_path / "elsewhere" / "app"
    folder.mkdir(parents=True)
    chief.add_project(str(folder))
    assert [p.name for p in (tmp_path / "inbox").iterdir()] == ["stray.md"]


def test_archiving_a_spec_still_copies_it_into_the_project_but_writes_no_draft(nowhere, tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setattr(chief, "load_projects", lambda: [{"name": "Alfa", "path": str(project)}])
    info = chief.archive_doc("spec", "Alfa", "Login", "x", "2026-11-07T10:00:00", "# Spec")
    assert (project / "docs" / "specs" / "2026-11-07-login.md").is_file()
    assert info == "copied to docs/specs/2026-11-07-login.md"
    assert [p.name for p in (tmp_path / "inbox").iterdir()] == ["stray.md"]


def test_the_curator_refuses_to_start_without_a_vault(nowhere):
    with pytest.raises(RuntimeError, match="no vault"):
        chief.launch_curator("x")


def test_settings_can_turn_the_vault_off_with_an_empty_value(nowhere, tmp_path, monkeypatch):
    monkeypatch.setattr(chief, "HOME", tmp_path / "home")
    settings._save({"vault": "D:/Old"})
    for empty in ("", "   ", None):
        out = settings.set_vault(empty)
        assert out == {"vault": "", "restart": True}
        assert json.loads((tmp_path / "home" / "projects.json").read_text(encoding="utf-8"))["vault"] == ""
    assert settings.snapshot.__name__ and chief.CFG["vault"] == ""


def test_the_prompts_tell_workers_to_ignore_the_vault_when_there_is_none(tmp_path):
    fake = Fake()
    without = Features(tmp_path / "a", fake.launch, fake.is_active, has_vault=False)
    without.create("Alfa", "t", "d", None)
    assert "No knowledge vault is configured" in fake.calls[-1]["task"]
    without.create("Alfa", "t2", "d", None, lang="pt")
    assert "Não há vault configurado" in fake.calls[-1]["task"]
    Features(tmp_path / "b", fake.launch, fake.is_active).create("Alfa", "t", "d", None)
    assert "No knowledge vault" not in fake.calls[-1]["task"]


def test_the_doctor_distinguishes_no_vault_from_a_vault_that_is_gone(nowhere, tmp_path, monkeypatch):
    def vault_check():
        return next(c for c in chief.doctor_checks() if c["name"] == "Vault")
    assert vault_check()["level"] == "ok" and "works without one" in vault_check()["detail"]
    monkeypatch.setattr(chief, "CFG", {**chief.CFG, "vault": "D:/Gone"})
    monkeypatch.setattr(chief, "VAULT", Path("D:/Gone"))
    assert vault_check()["level"] == "warn" and "not found" in vault_check()["detail"]
