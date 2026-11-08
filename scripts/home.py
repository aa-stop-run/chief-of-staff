"""Where the user's data lives, kept apart from the application's code.

The application (this repository) is code only. Everything that belongs to the user — the configuration (`projects.json`),
the state (`state/`: features, to-dos, workers, the worktrees of the implementations) and the automatic backups — lives in
the Chief's *home*:

    $CHIEF_HOME                              if set (also used to run a second, throwaway instance for development)
    %APPDATA%\\Chief                          on Windows
    ~/Library/Application Support/Chief       on macOS
    $XDG_DATA_HOME/chief or ~/.local/share/chief   elsewhere

Nothing here runs when the module is imported. `ensure()` is called by the entry points (the dashboard and the CLI): it creates
the home, imports data left by older versions inside the application folder, backs up and migrates data written by an older
schema, and refuses to start when the data is newer than the code.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import migrations

APP_ROOT = Path(__file__).resolve().parent.parent
CONFIG_NAME = "projects.json"
EXAMPLE = APP_ROOT / "projects.example.json"
SCHEMA_NAME = "schema.json"
LEGACY_MARK = ".migrated"      # suffix given to the in-tree files after they are imported into the home
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class HomeError(Exception):
    """The data cannot be used by this version (newer schema) or could not be migrated."""


def default_home() -> Path:
    env = os.environ.get("CHIEF_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Chief"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Chief"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "chief"


def config_path(home: Path) -> Path:
    return Path(home) / CONFIG_NAME


def state_dir(home: Path) -> Path:
    return Path(home) / "state"


def read_config(home: Path) -> dict:
    """The user's configuration, or the example's defaults when there is none yet. Never writes."""
    for p in (config_path(home), EXAMPLE):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def schema_version(home: Path) -> int:
    """Version of the data in `home`: 0 for data written before versions existed, CURRENT for a brand-new home."""
    try:
        return int(json.loads((Path(home) / SCHEMA_NAME).read_text(encoding="utf-8"))["schema_version"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0 if has_data(home) else migrations.CURRENT


def has_data(home: Path) -> bool:
    return config_path(home).exists() or state_dir(home).exists()


def _write_schema(home: Path, version: int) -> None:
    tmp = Path(home) / (SCHEMA_NAME + ".tmp")
    tmp.write_text(json.dumps({"schema_version": version}, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(Path(home) / SCHEMA_NAME)


def backup(home: Path, tag: str) -> Path:
    """Copy the configuration, the schema marker and the state (except git worktrees) to <home>/backups/<time>-<tag>/."""
    dest = Path(home) / "backups" / f"{datetime.now():%Y%m%d-%H%M%S}-{tag}"
    dest.mkdir(parents=True, exist_ok=True)
    for name in (CONFIG_NAME, SCHEMA_NAME):
        if (Path(home) / name).is_file():
            shutil.copy2(Path(home) / name, dest / name)
    if state_dir(home).is_dir():
        shutil.copytree(state_dir(home), dest / "state", ignore=shutil.ignore_patterns("work"))
    return dest


# ---------------------------------------------------------------- data left inside the application folder by older versions
def legacy_present(home: Path) -> bool:
    """Is there data in the application folder (the old layout) that has not been imported into `home` yet?"""
    if Path(home).resolve() == APP_ROOT.resolve():
        return False
    return (APP_ROOT / CONFIG_NAME).is_file() or (APP_ROOT / "state").is_dir()


def _git(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=60, creationflags=NO_WINDOW)


def _move_worktree(old: Path, new: Path) -> None:
    """Move a git worktree directory and repair the repository's link to it. Undo and raise if it does not work."""
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old), str(new))
    r = _git("worktree", "repair", str(new), cwd=new)
    ok = r.returncode == 0 and _git("status", "--porcelain", cwd=new).returncode == 0
    if not ok:
        shutil.move(str(new), str(old))
        _git("worktree", "repair", str(old), cwd=old)
        raise HomeError(f"could not move the git worktree {old} (git said: {(r.stderr or r.stdout).strip()[:200]})")


def import_legacy(home: Path) -> list:
    """Bring the data an older version kept inside the application folder into `home`. Returns what was done.

    Everything is copied first; the git worktrees (which cannot be copied) are moved and repaired. Only when all of it worked
    are the in-tree originals renamed with the `.migrated` suffix, so there is never more than one live copy."""
    home = Path(home)
    done = []
    home.mkdir(parents=True, exist_ok=True)
    cfg, st = APP_ROOT / CONFIG_NAME, APP_ROOT / "state"
    copied_cfg, moved = False, []
    try:
        if cfg.is_file() and not config_path(home).exists():
            shutil.copy2(cfg, config_path(home))
            copied_cfg = True
            done.append(f"copied {CONFIG_NAME}")
        if st.is_dir() and not state_dir(home).exists():
            shutil.copytree(st, state_dir(home), ignore=shutil.ignore_patterns("work"))
            done.append("copied state/")
            for old in sorted(st.glob("features/*/work")):
                new = state_dir(home) / old.relative_to(st)
                _move_worktree(old, new)
                moved.append((old, new))
                done.append(f"moved worktree {old.parent.name}")
    except Exception:
        for old, new in reversed(moved):              # put the worktrees back and leave the home as it was: a retry starts clean
            _move_worktree(new, old)
        shutil.rmtree(state_dir(home), ignore_errors=True)
        if copied_cfg:
            config_path(home).unlink(missing_ok=True)
        raise
    for p in (cfg, st):
        if p.exists():
            p.rename(p.with_name(p.name + LEGACY_MARK))
            done.append(f"renamed {p.name} to {p.name + LEGACY_MARK}")
    return done


# ---------------------------------------------------------------- entry point
def ensure(home=None) -> Path:
    """Prepare the home for use: create it, import old in-tree data, migrate older schemas. Returns the home folder."""
    home = Path(home) if home else default_home()
    log = []
    if not has_data(home) and legacy_present(home):
        log += import_legacy(home)
        _write_schema(home, 0)                         # data from before versions existed
    home.mkdir(parents=True, exist_ok=True)
    version = schema_version(home)
    if version > migrations.CURRENT:
        raise HomeError(f"the data in {home} was written by a newer version of the Chief (schema {version}, this one reads up to "
                        f"{migrations.CURRENT}): update the application, or point CHIEF_HOME at other data")
    if version < migrations.CURRENT:
        dest = backup(home, f"schema{version}")
        log.append(f"backup in {dest}")
        for step in migrations.pending(version):
            step.run(state_dir(home))
            log.append(f"migrated to schema {step.version}: {step.name}")
    if not config_path(home).exists():
        shutil.copyfile(EXAMPLE, config_path(home))
        log.append(f"created {CONFIG_NAME} from the example")
    if not (home / SCHEMA_NAME).exists() or schema_version(home) != migrations.CURRENT:
        _write_schema(home, migrations.CURRENT)
    for line in log:
        print(f"[home] {line}", file=sys.stderr)
    return home
