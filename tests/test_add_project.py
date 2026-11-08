"""“+ Add project”: validates the folder, adds it to `extras` in projects.json and leaves a note in the inbox."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chief  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    root, vault, proj_root = tmp_path / "Chief", tmp_path / "Vault", tmp_path / "Projects"
    (vault / "inbox").mkdir(parents=True)
    (vault / "Projects").mkdir()
    root.mkdir()
    (proj_root / "already-there").mkdir(parents=True)
    (proj_root / "Babelstone").mkdir()
    cfg = {"vault": str(vault), "roots": [str(proj_root)], "extras": [], "ignore": ["Babelstone"], "hide_domains": [],
           "dashboard_port": 8780}
    (root / "projects.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(chief, "ROOT", root)
    monkeypatch.setattr(chief, "HOME", root)               # the data folder (configuration is written here)
    monkeypatch.setattr(chief, "VAULT", vault)
    monkeypatch.setattr(chief, "CFG", cfg)
    return tmp_path, root, vault, proj_root


def test_adds_a_folder_to_extras_and_it_becomes_a_project(env):
    tmp, root, vault, _ = env
    new = tmp / "SomewhereElse" / "new-app"
    new.mkdir(parents=True)
    p = chief.add_project(str(new))
    assert p["name"] == "new-app" and Path(p["path"]) == new
    saved = json.loads((root / "projects.json").read_text(encoding="utf-8"))
    assert saved["extras"] == [str(new).replace("\\", "/")] and saved["vault"] == str(vault)   # the rest stays as it was
    assert "new-app" in [x["name"] for x in chief.load_projects()]
    assert "\r" not in (root / "projects.json").read_text(encoding="utf-8")                    # the repo is LF


def test_leaves_a_draft_note_in_the_inbox_without_secrets(env):
    tmp, _, vault, _ = env
    new = tmp / "x" / "new-app"
    new.mkdir(parents=True)
    chief.add_project(f'  "{new}"  ')                          # accepts quotes and spaces (copying a path from Explorer)
    notes = list((vault / "inbox").glob("* new-app - *.md"))
    assert len(notes) == 1
    t = notes[0].read_text(encoding="utf-8")
    assert t.startswith("---\nproject:") and "status: draft" in t and "new-app" in t


@pytest.mark.parametrize("bad", ["", "   ", "relative/folder", "C:\\does\\not\\exist\\at\\all", "x\0y"])
def test_rejects_empty_relative_missing_or_weird_paths(env, bad):
    with pytest.raises(ValueError):
        chief.add_project(bad)


def test_rejects_a_file(env):
    tmp, *_ = env
    f = tmp / "file.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="folder"):
        chief.add_project(str(f))


def test_rejects_vault_chief_and_their_children(env):
    _, root, vault, _ = env
    (vault / "wiki").mkdir()
    for bad in (vault, root, vault / "wiki"):
        with pytest.raises(ValueError, match="Vault|Chief"):
            chief.add_project(str(bad))


def test_rejects_drive_roots_and_sensitive_folders(env):
    tmp, *_ = env
    with pytest.raises(ValueError):
        chief.add_project(Path(tmp.anchor).as_posix())
    ssh = tmp / "home" / ".ssh"
    ssh.mkdir(parents=True)
    with pytest.raises(ValueError, match="sensitive"):
        chief.add_project(str(ssh))


def test_rejects_duplicates_and_folders_already_covered(env):
    tmp, _, _, proj_root = env
    with pytest.raises(ValueError, match="already"):
        chief.add_project(str(proj_root / "already-there"))                     # already shows up (inside a root)
    with pytest.raises(ValueError, match="already"):
        chief.add_project(str(proj_root / "already-there").upper())             # Windows ignores case
    sub = proj_root / "already-there" / "sub"
    sub.mkdir()
    with pytest.raises(ValueError, match="already"):
        chief.add_project(str(sub))                                             # part of an existing project
    new = tmp / "z" / "new"
    new.mkdir(parents=True)
    chief.add_project(str(new))
    with pytest.raises(ValueError, match="already"):
        chief.add_project(str(new))                                             # not duplicated in extras


def test_rejects_a_folder_the_user_chose_to_ignore(env):
    _, _, _, proj_root = env
    with pytest.raises(ValueError, match="ignore"):
        chief.add_project(str(proj_root / "Babelstone"))


def test_a_portuguese_vault_gets_a_portuguese_draft(env, monkeypatch):
    tmp, _, vault, _ = env
    monkeypatch.setattr(chief, "SCHEMA", chief.VAULT_SCHEMA["pt"])
    monkeypatch.setattr(chief, "DRAFT_TEXT", chief.DRAFT_TEXTS["pt"])
    new = tmp / "x" / "app-pt"
    new.mkdir(parents=True)
    chief.add_project(str(new))
    t = next((vault / "inbox").glob("* app-pt - *.md")).read_text(encoding="utf-8")
    assert t.startswith("---\nprojeto:") and "estado: rascunho" in t and "O utilizador pôs a pasta" in t


# --- cloning from a URL --------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "", "git@evil.com:owner/repo.git", "ssh://git@github.com/owner/repo", "x@github.com:owner/repo", "http://github.com/owner/repo",
    "file:///D:/x/repo", "ext::sh -c id",
    "https://user:tok@github.com/owner/repo", "https://github.com/owner/repo?x=1", "https://github.com/owner",
    "https://evil.example.com/owner/repo", "https://github.com/owner/repo/extra", "https://github.com/../repo",
    "https://github.com/owner/.hidden", "https://github.com/owner/-oupload-pack=x", None,
])
def test_clone_rejects_unsafe_or_foreign_urls(env, bad):
    with pytest.raises(ValueError):
        chief.clone_project(bad)


def test_clone_normalises_url_and_name():
    assert chief.parse_clone_url(" https://GitHub.com/owner/repo.git/ ") == ("https://github.com/owner/repo", "repo")


def test_clone_runs_a_locked_down_git_into_the_first_root(env, monkeypatch):
    _, _, vault, proj_root = env
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"], seen["timeout"] = cmd, kw["env"], kw["timeout"]
        Path(cmd[-1]).mkdir()                                            # git creates the folder
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(chief.subprocess, "run", fake_run)
    p = chief.clone_project("https://github.com/owner/new-repo")
    assert p["name"] == "new-repo" and Path(p["path"]) == proj_root / "new-repo"
    cmd = seen["cmd"]
    assert cmd[0] == "git" and "--" in cmd and cmd[cmd.index("--") + 1] == "https://github.com/owner/new-repo"
    assert "protocol.allow=never" in cmd and "--no-recurse-submodules" in cmd
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and seen["timeout"] == chief.CLONE_TIMEOUT
    assert len(list((vault / "inbox").glob("* new-repo - *.md"))) == 1


def test_clone_failure_cleans_up_and_reports(env, monkeypatch):
    _, _, _, proj_root = env

    def fake_run(cmd, **kw):
        Path(cmd[-1]).mkdir()
        return type("R", (), {"returncode": 128, "stderr": "fatal: repository not found\n"})()

    monkeypatch.setattr(chief.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="failed"):
        chief.clone_project("https://github.com/owner/private")
    assert not (proj_root / "private").exists()


def test_clone_refuses_existing_folder_and_ignored_names(env, monkeypatch):
    _, _, _, proj_root = env
    monkeypatch.setattr(chief.subprocess, "run", lambda *a, **k: pytest.fail("git should not have run"))
    with pytest.raises(ValueError, match="already exists"):
        chief.clone_project("https://github.com/owner/already-there")
    with pytest.raises(ValueError, match="ignore list"):
        chief.clone_project("https://github.com/owner/Babelstone")


def test_ssh_urls_of_allowed_hosts_are_accepted_and_normalised():
    assert chief.parse_clone_url(" git@GitHub.com:owner/repo.git ") == ("git@github.com:owner/repo", "repo")
    assert chief.parse_clone_url("https://github.com/owner/repo") == ("https://github.com/owner/repo", "repo")


def test_allowed_hosts_come_from_the_settings(env):
    cfg = {**chief.CFG, "clone_hosts": ["git.example.org"]}
    chief.CFG = cfg
    assert chief.parse_clone_url("https://git.example.org/a/b")[1] == "b"
    with pytest.raises(ValueError, match="I only clone"):
        chief.parse_clone_url("https://github.com/a/b")


def test_private_clone_uses_non_interactive_git_with_ssh_batch_mode(env, monkeypatch):
    _, _, vault, proj_root = env
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        Path(cmd[-1]).mkdir()
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(chief.subprocess, "run", fake_run)
    p = chief.clone_project("git@github.com:owner/private.git", domain=" Banking ")
    assert p["domain"] == "banking" and Path(p["path"]) == proj_root / "private"
    assert seen["cmd"][seen["cmd"].index("--") + 1] == "git@github.com:owner/private"
    assert "protocol.ssh.allow=always" in seen["cmd"]
    assert seen["env"]["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes" and seen["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_a_failed_private_clone_tells_how_to_log_in(env, monkeypatch):
    def fake_run(cmd, **kw):
        return type("R", (), {"returncode": 128, "stderr": "fatal: could not read Username"})()

    monkeypatch.setattr(chief.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="gh auth login"):
        chief.clone_project("https://github.com/owner/private")


def test_category_chosen_when_adding_overrides_the_vault_and_is_validated(env):
    tmp, root, vault, _ = env
    new = tmp / "x" / "new-app"
    new.mkdir(parents=True)
    p = chief.add_project(str(new), domain="Product")
    assert p["domain"] == "product"
    assert json.loads((root / "projects.json").read_text(encoding="utf-8"))["domains"] == {"new-app": "product"}
    other = tmp / "y" / "other"
    other.mkdir(parents=True)
    with pytest.raises(ValueError, match="category"):
        chief.add_project(str(other), domain="not/allowed;here")


def test_folders_the_user_marks_as_sensitive_are_refused(env, monkeypatch):
    tmp, *_ = env
    monkeypatch.setattr(chief, "SENSITIVE_PARTS", ("tax documents",))
    folder = tmp / "Home" / "Tax Documents 2025"
    folder.mkdir(parents=True)
    with pytest.raises(ValueError, match="sensitive"):
        chief.add_project(str(folder))
    other = tmp / "Home" / "ordinary"
    other.mkdir()
    assert chief.add_project(str(other))["name"] == "ordinary"
