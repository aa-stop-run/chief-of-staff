"""The application is code only; the user's data lives in its own home, which is created, imported and migrated on start-up."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chief  # noqa: E402
import home  # noqa: E402
import migrations  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A fake application folder (the old layout keeps data inside it) and a separate, empty home."""
    root = tmp_path / "app"
    root.mkdir()
    (root / "projects.example.json").write_text(json.dumps({"vault": "", "roots": []}), encoding="utf-8")
    monkeypatch.setattr(home, "APP_ROOT", root)
    monkeypatch.setattr(home, "EXAMPLE", root / "projects.example.json")
    return root, tmp_path / "data"


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------- where the data lives
def test_chief_home_overrides_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("CHIEF_HOME", str(tmp_path / "mine"))
    assert home.default_home() == (tmp_path / "mine").resolve()


@pytest.mark.parametrize("platform,env,expected", [
    ("win32", {"APPDATA": "C:/Users/x/AppData/Roaming"}, Path("C:/Users/x/AppData/Roaming/Chief")),
    ("linux", {"XDG_DATA_HOME": "/data"}, Path("/data/chief")),
])
def test_default_home_is_a_per_user_folder_outside_the_application(monkeypatch, platform, env, expected):
    monkeypatch.delenv("CHIEF_HOME", raising=False)
    for k in ("APPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "platform", platform)
    assert home.default_home() == expected
    assert home.APP_ROOT not in home.default_home().parents


def test_reading_the_configuration_never_writes(app):
    root, data = app
    assert home.read_config(data) == {"vault": "", "roots": []}       # the example's defaults
    assert not data.exists()


def test_the_chief_module_has_no_data_inside_the_application_folder():
    assert chief.HOME != chief.ROOT and chief.ROOT not in chief.HOME.parents
    assert chief.STATE.parent == chief.HOME


# ---------------------------------------------------------------- a fresh install
def test_a_fresh_install_creates_the_home_from_the_example_at_the_current_schema(app):
    _, data = app
    home.ensure(data)
    assert json.loads((data / "projects.json").read_text(encoding="utf-8")) == {"vault": "", "roots": []}
    assert json.loads((data / "schema.json").read_text(encoding="utf-8")) == {"schema_version": migrations.CURRENT}
    assert not (data / "backups").exists()                           # nothing to migrate, so nothing to back up


def test_ensure_is_idempotent_and_never_touches_data_that_is_current(app):
    _, data = app
    home.ensure(data)
    (data / "projects.json").write_text('{"vault": "D:/V", "roots": ["D:/P"]}', encoding="utf-8")
    home.ensure(data)
    assert json.loads((data / "projects.json").read_text(encoding="utf-8"))["roots"] == ["D:/P"]
    assert not (data / "backups").exists()


# ---------------------------------------------------------------- data left inside the application by older versions
def old_layout(root: Path) -> None:
    write(root / "projects.json", {"vault": "D:/V", "roots": ["D:/P"]})
    write(root / "state" / "features.json", [{"id": "f1", "history": [{"event": "aprovado", "stage": "spec"}]}])
    write(root / "state" / "pendentes.json", [{"id": "p-1", "project": "A", "title": "t", "estado": "blocked"}])
    write(root / "state" / "features" / "f1" / "spec.md", "x")


def test_old_in_tree_data_is_imported_migrated_and_backed_up(app):
    root, data = app
    old_layout(root)
    home.ensure(data)
    assert json.loads((data / "projects.json").read_text(encoding="utf-8"))["roots"] == ["D:/P"]
    assert (data / "state" / "features" / "f1" / "spec.md").read_text(encoding="utf-8") == '"x"'
    feats = json.loads((data / "state" / "features.json").read_text(encoding="utf-8"))
    assert feats[0]["history"][0]["event"] == "approved" and feats[0]["lang"] == "pt"
    assert json.loads((data / "state" / "pending.json").read_text(encoding="utf-8"))[0]["status"] == "blocked"
    backups = list((data / "backups").iterdir())
    assert len(backups) == 1 and backups[0].name.endswith("schema0")
    assert "aprovado" in (backups[0] / "state" / "features.json").read_text(encoding="utf-8")   # the backup is the pre-migration data


def test_the_in_tree_originals_are_renamed_so_there_is_only_one_live_copy(app):
    root, data = app
    old_layout(root)
    home.ensure(data)
    assert not (root / "projects.json").exists() and not (root / "state").exists()
    assert (root / "projects.json.migrated").is_file() and (root / "state.migrated").is_dir()
    home.ensure(data)                                                 # a second start imports nothing again
    assert len(list((data / "backups").iterdir())) == 1


def test_a_home_that_already_has_data_is_not_overwritten_by_in_tree_leftovers(app):
    root, data = app
    write(data / "projects.json", {"vault": "D:/Mine", "roots": []})
    write(root / "projects.json", {"vault": "D:/Old", "roots": []})
    home.ensure(data)
    assert json.loads((data / "projects.json").read_text(encoding="utf-8"))["vault"] == "D:/Mine"
    assert (root / "projects.json").exists()                          # untouched


def test_keeping_the_old_layout_on_purpose_is_allowed(app):
    root, _ = app
    old_layout(root)
    home.ensure(root)                                                 # CHIEF_HOME pointing at the application folder
    assert (root / "projects.json").exists() and not (root / "projects.json.migrated").exists()


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout


def make_repo_with_worktree(root: Path, project: Path):
    project.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=project)
    git("config", "user.email", "t@t.t", cwd=project)
    git("config", "user.name", "T", cwd=project)
    (project / "a.txt").write_text("a", encoding="utf-8")
    git("add", "-A", cwd=project)
    git("commit", "-q", "-m", "base", cwd=project)
    work = root / "state" / "features" / "f1" / "work"
    work.parent.mkdir(parents=True)
    git("worktree", "add", "-q", "-b", "feat/f1", str(work), cwd=project)
    (work / "new.txt").write_text("uncommitted work", encoding="utf-8")        # the implementer's uncommitted work
    return work


def test_git_worktrees_are_moved_and_still_work_afterwards(app, tmp_path):
    root, data = app
    write(root / "projects.json", {"vault": "", "roots": []})
    project = tmp_path / "proj"
    make_repo_with_worktree(root, project)
    home.ensure(data)
    new = data / "state" / "features" / "f1" / "work"
    assert (new / "new.txt").read_text(encoding="utf-8") == "uncommitted work"          # nothing was lost
    assert "new.txt" in git("status", "--porcelain", cwd=new)                           # git still knows the worktree
    listed = git("worktree", "list", "--porcelain", cwd=project)
    assert Path(new).as_posix().lower() in listed.replace("\\", "/").lower()


def test_a_worktree_that_cannot_be_moved_leaves_everything_as_it_was(app, tmp_path, monkeypatch):
    root, data = app
    write(root / "projects.json", {"vault": "", "roots": []})
    make_repo_with_worktree(root, tmp_path / "proj")
    real = home._git

    def failing(*args, cwd):
        if args[:2] == ("worktree", "repair") and Path(cwd) == data / "state" / "features" / "f1" / "work":
            return subprocess.CompletedProcess(args, 1, "", "boom")
        return real(*args, cwd=cwd)

    monkeypatch.setattr(home, "_git", failing)
    with pytest.raises(home.HomeError, match="could not move"):
        home.ensure(data)
    assert (root / "state" / "features" / "f1" / "work" / "new.txt").is_file()          # still where it was
    assert (root / "projects.json").exists() and not (data / "state").exists()          # nothing half-imported
    assert not (data / "projects.json").exists()                                         # so a retry imports everything again
    assert git("status", "--porcelain", cwd=root / "state" / "features" / "f1" / "work")


# ---------------------------------------------------------------- upgrades and downgrades
def test_an_older_schema_is_backed_up_then_migrated(app):
    _, data = app
    write(data / "projects.json", {"vault": "", "roots": []})
    write(data / "state" / "pendentes.json", [{"id": "p-1", "estado": "blocked"}])
    home.ensure(data)
    assert (data / "state" / "pending.json").is_file() and not (data / "state" / "pendentes.json").exists()
    backup = next((data / "backups").iterdir())
    assert (backup / "state" / "pendentes.json").is_file()
    assert home.schema_version(data) == migrations.CURRENT


def test_the_backup_skips_git_worktrees(app):
    _, data = app
    write(data / "projects.json", {"vault": "", "roots": []})
    write(data / "state" / "features" / "f1" / "work" / "big.bin", "x")
    write(data / "state" / "features" / "f1" / "spec.md", "x")
    dest = home.backup(data, "test")
    assert (dest / "state" / "features" / "f1" / "spec.md").is_file() and not (dest / "state" / "features" / "f1" / "work").exists()


def test_data_from_a_newer_version_is_refused_and_left_alone(app):
    _, data = app
    write(data / "projects.json", {"vault": "", "roots": []})
    write(data / "schema.json", {"schema_version": migrations.CURRENT + 1})
    with pytest.raises(home.HomeError, match="newer version"):
        home.ensure(data)
    assert not (data / "backups").exists()
    assert json.loads((data / "schema.json").read_text(encoding="utf-8")) == {"schema_version": migrations.CURRENT + 1}


def test_the_cli_reports_where_everything_lives(capsys):
    chief.cmd_home(None)
    out = capsys.readouterr().out
    assert str(chief.ROOT) in out and str(chief.HOME) in out and "schema:" in out
