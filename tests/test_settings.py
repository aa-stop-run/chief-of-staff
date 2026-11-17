"""Settings: vault, project folders, categories and git/GitHub status (no secrets)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chief  # noqa: E402
import settings  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    root, vault, proj_root = tmp_path / "Chief", tmp_path / "Vault", tmp_path / "Projects"
    (vault / "inbox").mkdir(parents=True)
    (vault / "Projects").mkdir()
    (vault / "wiki").mkdir()
    root.mkdir()
    for n in ("alfa", "beta", "hidden"):
        (proj_root / n).mkdir(parents=True)
    (vault / "Projects" / "Alfa.md").write_text(f'---\nlocal: "{(proj_root / "alfa").as_posix()}"\ndomain: home\n---\n', encoding="utf-8")
    cfg = {"vault": str(vault), "roots": [str(proj_root)], "extras": [], "ignore": ["hidden"], "hide_domains": [], "dashboard_port": 8780}
    (root / "projects.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(chief, "ROOT", root)
    monkeypatch.setattr(chief, "HOME", root)               # the data folder (configuration is written here)
    monkeypatch.setattr(chief, "VAULT", vault)
    monkeypatch.setattr(chief, "CFG", cfg)
    return tmp_path, root, vault, proj_root


def saved(root):
    return json.loads((root / "projects.json").read_text(encoding="utf-8"))


def test_domain_chosen_by_the_user_beats_the_vaults_and_never_touches_the_vault(env):
    _, root, vault, _ = env
    assert next(p for p in chief.load_projects() if p["name"] == "Alfa")["domain"] == "home"
    settings.set_domain("Alfa", " Banking ")
    assert saved(root)["domains"] == {"Alfa": "banking"}
    assert next(p for p in chief.load_projects() if p["name"] == "Alfa")["domain"] == "banking"
    assert list((vault / "inbox").iterdir()) == []                    # the category is the Chief's: nothing goes to the vault
    assert "domain: home" in (vault / "Projects" / "Alfa.md").read_text(encoding="utf-8")   # the hub stays as it was
    settings.set_domain("Alfa", "")                                   # empty = the vault's own applies again
    assert next(p for p in chief.load_projects() if p["name"] == "Alfa")["domain"] == "home"


def test_a_portuguese_vault_is_read_with_its_own_folder_and_key_names(env, monkeypatch):
    _, _, vault, proj_root = env
    (vault / "Projetos").mkdir()
    (vault / "Projetos" / "Beta.md").write_text(f'---\nlocal: "{(proj_root / "beta").as_posix()}"\ndominio: casa\n---\n', encoding="utf-8")
    monkeypatch.setattr(chief, "SCHEMA", chief.VAULT_SCHEMA["pt"])
    assert next(p for p in chief.load_projects() if p["name"] == "Beta")["domain"] == "casa"


@pytest.mark.parametrize("bad", ["../x", "a;b", "x" * 40, "!"])
def test_invalid_categories_are_refused(env, bad):
    with pytest.raises(ValueError, match="category"):
        settings.set_domain("Alfa", bad)


def test_unknown_project_is_refused(env):
    with pytest.raises(ValueError, match="unknown"):
        settings.set_domain("DoesNotExist", "home")


def test_hidden_domains_respect_the_chosen_category(env):
    _, root, _, _ = env
    chief.CFG = {**chief.CFG, "hide_domains": ["banking"]}
    settings.set_domain("beta", "banking")
    assert "beta" not in [p["name"] for p in chief.load_projects()]


def test_vault_must_look_like_a_vault_and_asks_for_a_restart(env):
    tmp, root, _, _ = env
    new = tmp / "OtherVault"
    new.mkdir()
    with pytest.raises(ValueError, match="vault"):
        settings.set_vault(str(new))
    (new / "wiki").mkdir()
    out = settings.set_vault(str(new))
    assert out["restart"] is True and saved(root)["vault"].endswith("OtherVault")
    for bad in ("relative", str(tmp / "does_not_exist")):
        with pytest.raises(ValueError):
            settings.set_vault(bad)


def test_chief_folder_and_sensitive_folders_are_never_accepted(env):
    tmp, root, _, _ = env
    ssh = tmp / "home" / ".ssh"
    ssh.mkdir(parents=True)
    with pytest.raises(ValueError, match="sensitive"):
        settings.add_root(str(ssh))
    with pytest.raises(ValueError, match="Chief"):
        settings.add_root(str(root))


def test_roots_can_be_added_and_removed_but_one_must_remain(env):
    tmp, root, _, proj_root = env
    other = tmp / "Other"
    (other / "gamma").mkdir(parents=True)
    settings.add_root(str(other))
    assert "gamma" in [p["name"] for p in chief.load_projects()]
    with pytest.raises(ValueError, match="already in the list"):
        settings.add_root(str(other))
    settings.remove_root(str(other))
    assert "gamma" not in [p["name"] for p in chief.load_projects()]
    with pytest.raises(ValueError, match="at least one"):
        settings.remove_root(str(proj_root))


def test_ignored_folders_can_be_shown_again(env):
    assert "hidden" not in [p["name"] for p in chief.load_projects()]
    settings.set_ignored("hidden", False)
    assert "hidden" in [p["name"] for p in chief.load_projects()]


def test_removing_a_project_never_touches_the_disk(env):
    tmp, root, _, proj_root = env
    extra = tmp / "Outside" / "extra1"
    extra.mkdir(parents=True)
    chief.add_project(str(extra))
    assert settings.remove_project("extra1")["how"] == "extra"
    assert extra.is_dir() and "extra1" not in [p["name"] for p in chief.load_projects()]
    assert settings.remove_project("beta")["how"] == "ignored"
    assert (proj_root / "beta").is_dir() and "beta" in saved(root)["ignore"]


def test_moving_an_extra_updates_its_path_and_roots_children_cannot_be_moved(env):
    tmp, root, _, _ = env
    a, b = tmp / "A" / "p", tmp / "B" / "p"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    chief.add_project(str(a))
    settings.move_extra("p", str(b))
    assert saved(root)["extras"] == [str(b).replace("\\", "/")]
    with pytest.raises(ValueError, match="move the folder"):
        settings.move_extra("beta", str(b))


def test_clone_hosts_are_validated(env):
    assert settings.set_clone_hosts("GitHub.com, git.example.org")["hosts"] == ["github.com", "git.example.org"]
    for bad in ("", "not a host", "https://github.com", "a" * 80 + ".com"):
        with pytest.raises(ValueError):
            settings.set_clone_hosts(bad)


@pytest.mark.parametrize("url,shown,host", [
    ("https://github.com/owner/repo.git", "github.com/owner/repo", "github.com"),
    ("https://someuser:secret123@github.com/owner/repo", "github.com/owner/repo", "github.com"),
    ("https://ghp_abcdef@github.com/owner/repo.git", "github.com/owner/repo", "github.com"),
    ("git@github.com:owner/repo.git", "github.com/owner/repo", "github.com"),
    ("", "", ""), ("garbage", "", ""),
])
def test_remote_urls_are_shown_without_credentials(url, shown, host):
    assert settings.clean_remote(url) == (shown, host)


def test_git_remote_of_a_real_repo_hides_the_credentials(env):
    tmp, *_ = env
    repo = tmp / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://u:SECRET@github.com/owner/private.git"], cwd=repo, check=True)
    out = settings.git_remote(str(repo))
    assert out == {"remote": "github.com/owner/private", "host": "github.com", "git": True}
    assert "SECRET" not in json.dumps(out)
    assert settings.git_remote(str(tmp)) == {"remote": "", "host": "", "git": False}


def test_github_status_reports_only_account_and_helpers_never_tokens(monkeypatch):
    out = ("github.com\n  ✓ Logged in to github.com account octocat (keyring)\n  - Active account: true\n"
           "  - Token: gho_************************************\n  - Token scopes: 'repo'")

    def fake_run(cmd, cwd=None, timeout=8):
        return out if "auth" in cmd else "manager\n"

    monkeypatch.setattr(settings, "_run", fake_run)
    monkeypatch.setattr(settings.shutil, "which", lambda n: "C:/gh.exe")
    monkeypatch.setitem(settings._gh, "val", None)
    s = settings.github_status()
    assert s == {"gh": True, "logged_in": True, "account": "octocat", "helpers": ["manager"]}
    assert "gho_" not in json.dumps(s)


def test_github_status_without_gh_or_login(monkeypatch):
    monkeypatch.setattr(settings, "_run", lambda cmd, cwd=None, timeout=8: "")
    monkeypatch.setattr(settings.shutil, "which", lambda n: None)
    monkeypatch.setitem(settings._gh, "val", None)
    assert settings.github_status() == {"gh": False, "logged_in": False, "account": "", "helpers": []}


def test_snapshot_lists_projects_with_category_origin_and_remote(env, monkeypatch):
    _, _, _, proj_root = env
    monkeypatch.setattr(settings, "github_status", lambda: {"gh": False, "logged_in": False, "account": "", "helpers": []})
    settings.set_domain("beta", "product")
    snap = settings.snapshot()
    rows = {r["name"]: r for r in snap["projects"]}
    assert rows["Alfa"]["domain"] == "home" and rows["Alfa"]["custom"] is False and rows["Alfa"]["hub"] is True
    assert rows["beta"]["domain"] == "product" and rows["beta"]["custom"] is True and rows["beta"]["kind"] == "root"
    assert snap["ignore"] == ["hidden"] and snap["hosts"][0] == "github.com" and "home" in snap["domains"]


# ---------------------------------------------------------------- plan usage: connecting the status line, the auto-resume switch
@pytest.fixture
def claude_dir(env, monkeypatch):
    d = env[0] / "claude"
    d.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(d))
    return d


def claude_file(d):
    return json.loads((d / "settings.json").read_text(encoding="utf-8"))


def test_connecting_sets_the_status_line_keeps_everything_else_and_leaves_a_backup(env, claude_dir):
    (claude_dir / "settings.json").write_text(json.dumps({"model": "opus", "permissions": {"allow": ["Bash(ls)"]}}), encoding="utf-8")
    assert settings.snapshot()["plan"]["connection"] == "none"
    settings.connect_plan()
    d = claude_file(claude_dir)
    assert d["model"] == "opus" and d["permissions"] == {"allow": ["Bash(ls)"]}
    assert d["statusLine"]["type"] == "command" and d["statusLine"]["command"].endswith(" statusline")
    assert json.loads((claude_dir / "settings.json.chief-backup").read_text(encoding="utf-8"))["model"] == "opus"
    assert settings.snapshot()["plan"]["connection"] == "connected"
    settings.connect_plan()                                                                  # twice does not stack
    assert claude_file(claude_dir) == d


def test_connecting_keeps_the_users_own_status_line_and_disconnecting_restores_it(env, claude_dir):
    (claude_dir / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": 'echo "hi $USER"'}}), encoding="utf-8")
    assert settings.snapshot()["plan"]["connection"] == "other"
    settings.connect_plan()
    cmd = claude_file(claude_dir)["statusLine"]["command"]
    assert "--then" in cmd and r'echo \"hi \$USER\"' in cmd
    settings.disconnect_plan()
    assert claude_file(claude_dir)["statusLine"]["command"] == 'echo "hi $USER"'


def test_disconnecting_without_a_previous_status_line_removes_ours(env, claude_dir):
    settings.connect_plan()                                                                  # no settings.json yet: it is created
    assert "statusLine" in claude_file(claude_dir)
    settings.disconnect_plan()
    assert "statusLine" not in claude_file(claude_dir)


def test_a_broken_claude_settings_file_is_reported_not_overwritten(env, claude_dir):
    (claude_dir / "settings.json").write_text("{not json", encoding="utf-8")
    assert settings.snapshot()["plan"]["connection"] == "unreadable"
    with pytest.raises(ValueError):
        settings.connect_plan()
    assert (claude_dir / "settings.json").read_text(encoding="utf-8") == "{not json"


def test_the_auto_resume_switch_is_saved_and_defaults_to_on(env):
    _, root, *_ = env
    assert settings.snapshot()["plan"]["resume_after_limit"] is True
    settings.set_resume_after_limit(False)
    assert saved(root)["resume_after_limit"] is False and settings.snapshot()["plan"]["resume_after_limit"] is False
    settings.set_resume_after_limit(True)
    assert saved(root)["resume_after_limit"] is True


def test_add_project_applies_the_optional_remote(env):
    tmp, root, _, _ = env
    repo = tmp / "NewProject" / "alfa"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    # set_remote uses `git remote set-url`, which requires an existing origin;
    # add a placeholder so the set-url call succeeds.
    subprocess.run(["git", "remote", "add", "origin", "https://placeholder.com/none"], cwd=repo, check=True)
    p = chief.add_project(str(repo), domain="home", remote="https://github.com/acme/alfa")
    assert p["name"] == "alfa"
    assert settings.git_remote(str(repo)) == {"remote": "github.com/acme/alfa", "host": "github.com", "git": True}
    assert "SECRET" not in json.dumps(settings.git_remote(str(repo)))


def test_add_project_skips_remote_for_a_plain_folder(env):
    tmp, root, _, _ = env
    plain = tmp / "PlainProject" / "plain"
    plain.mkdir(parents=True)
    # a remote given for a non-git folder is skipped, not an error
    p = chief.add_project(str(plain), remote="https://github.com/acme/plain")
    assert p["name"] == "plain"


def test_add_project_rejects_an_invalid_remote(env):
    tmp, root, _, _ = env
    repo = tmp / "BadRemote" / "alfa"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    with pytest.raises(ValueError, match="give the full remote URL"):
        chief.add_project(str(repo), remote="https://bad url")
