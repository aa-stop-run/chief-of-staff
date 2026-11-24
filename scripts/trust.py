"""Folders Claude Code does not trust yet: `claude --bg` refuses to start a background worker in a folder (or with an added folder)
the user has not accepted, so a request, the vault curation or a project's first extraction fails. This module

- recognises that refusal (`looks_untrusted`, `path_from`),
- keeps the alerts the dashboard shows (`TrustAlerts`, `state/trust.json`), each one gone as soon as the folder is trusted, and
- adds a folder to Claude Code's trusted folders, but ONLY when the user confirms it in the dashboard (`grant`): it sets that one
  flag in Claude Code's own config file (`~/.claude.json`, or `$CLAUDE_CONFIG_DIR/.claude.json`), leaves a backup next to it and
  touches nothing else. It never reads or returns anything from that file except the trust flags.

The server only grants a path the Chief itself manages (the vault, a project's folder, its own state folder) and that a launch
really refused."""
import json
import os
import re
import shutil
import time
from pathlib import Path

import atomicio

BACKUP_SUFFIX = ".chief-backup"


class TrustError(Exception):
    pass


def config_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) / ".claude.json") if base else Path.home() / ".claude.json"


def norm(path) -> str:
    """The key Claude Code files a folder under: absolute, forward slashes (`D:/Vault`), no trailing slash."""
    return str(Path(path).resolve()).replace("\\", "/").rstrip("/") or "/"


def _same(a: str, b: str) -> bool:
    return a.lower() == b.lower() if os.name == "nt" else a == b


def _read(cfg_path=None) -> dict:
    try:
        data = json.loads(Path(cfg_path or config_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def repo_root(path):
    """The git repository a folder belongs to (the nearest folder above it, or itself, with a `.git`), or None."""
    p = Path(path).resolve()
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def _accepted(cfg_path=None) -> list:
    """The folders the user accepted the trust prompt for (only the trust flags of Claude Code's config are looked at)."""
    projects = _read(cfg_path).get("projects")
    if not isinstance(projects, dict):
        return []
    return [str(k).replace("\\", "/").rstrip("/") for k, v in projects.items() if isinstance(v, dict) and v.get("hasTrustDialogAccepted") is True]


def target(path) -> str:
    """The folder whose trust decides about `path`: its git repository (Claude Code trusts a repository as a whole, and a nested
    repository on its own), else the folder itself."""
    root = repo_root(path)
    return norm(root if root else path)


def is_trusted(path, cfg_path=None) -> bool:
    """Would `claude --bg` accept this folder (as its working folder or with `--add-dir`)? As the installed Claude Code does it
    (checked with real launches, tests/real/test_real_trust.py): inside a git repository the REPOSITORY must be accepted itself
    (a trusted folder above it does not count, nor does a repository nested in a trusted one); outside any repository the folder
    or a folder above it."""
    key, accepted = target(path), _accepted(cfg_path)
    if repo_root(path) is not None:
        return any(_same(key, k) for k in accepted)
    low = (lambda t: t.lower()) if os.name == "nt" else (lambda t: t)
    return any(_same(key, k) or low(key).startswith(low(k) + "/") for k in accepted)


# ---------------------------------------------------------------- recognising the refusal
_REPO = re.compile(r"part of the repository at\s+(.+?)\)", re.I)
_RUN_IN = re.compile(r"Run `claude` in\s+(.+?)\s+once", re.I)
_PATH = re.compile(r"not trusted:\s*(.+?)(?:\s\(|\.\s|\.$|$)", re.I | re.M)


def looks_untrusted(message: str) -> bool:
    """Does what `claude --bg` printed say it refused because of a folder it does not trust?"""
    return "not trusted" in (message or "").lower()


def path_from(message: str, default: str = "") -> str:
    """The folder to trust that the refusal names: the repository it says the folder is part of, else the folder
    («Added folder not trusted: D:\\Vault.», «Run `claude` in <folder> once»), else `default` (where the worker was started)."""
    for rx in (_REPO, _RUN_IN, _PATH):
        m = rx.search(message or "")
        if m:
            return m.group(1).strip().strip('"')
    return default


# ---------------------------------------------------------------- the user's confirmation: add the folder
def grant(path, cfg_path=None) -> dict:
    """Mark `path` as trusted in Claude Code's config. Leaves `<file>.chief-backup`, writes atomically, keeps every other field,
    and checks afterwards that the folder is trusted. Raises TrustError if it cannot."""
    folder = Path(path)
    if not folder.is_dir():
        raise TrustError("the folder does not exist")
    cfg = Path(cfg_path or config_path())
    key = target(folder)                    # inside a repository, the repository: that is what Claude Code records
    for _ in range(3):                      # Claude Code rewrites this file too: re-read if it changed under us
        try:
            raw = cfg.read_text(encoding="utf-8") if cfg.exists() else "{}"
            before = cfg.stat().st_mtime_ns if cfg.exists() else 0
            data = json.loads(raw or "{}")
        except (OSError, ValueError) as e:
            raise TrustError(f"Claude Code's config could not be read: {e}") from e
        if not isinstance(data, dict):
            raise TrustError("Claude Code's config is not a JSON object")
        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise TrustError("Claude Code's config has an unexpected «projects» entry")
        entry = projects.get(key)
        if not isinstance(entry, dict):
            entry = projects[key] = {}
        entry["hasTrustDialogAccepted"] = True
        if cfg.exists():
            shutil.copy2(cfg, cfg.with_name(cfg.name + BACKUP_SUFFIX))
        if (cfg.stat().st_mtime_ns if cfg.exists() else 0) != before:
            continue
        atomicio.write_text(cfg, json.dumps(data, ensure_ascii=False, indent=2))
        if is_trusted(folder, cfg):
            return {"path": key, "granted": True}
        time.sleep(0.2)
    raise TrustError("Claude Code's config kept changing: try again")


def confirm(alerts: "TrustAlerts", path, cfg_path=None) -> dict:
    """The user clicked «Trust this folder»: only a folder a launch was really refused for (and that the Chief manages) is added."""
    key = target(path)
    if not any(_same(norm(i["path"]), key) for i in alerts.items()):
        raise TrustError("that folder is not waiting for your trust")
    out = grant(key, cfg_path)
    alerts.remove(key)
    return out


# ---------------------------------------------------------------- the alerts the dashboard shows
class TrustAlerts:
    """Folders a launch was refused for, until they are trusted. `state_path`: a JSON file in the Chief's state folder."""

    def __init__(self, state_path, allowed, trusted=is_trusted, now=time.time):
        """allowed() -> the folders the Chief manages (vault, projects, its state): anything else is ignored."""
        self.path, self._allowed, self._trusted, self._now = Path(state_path), allowed, trusted, now

    def _load(self) -> list:
        try:
            items = json.loads(self.path.read_text(encoding="utf-8")).get("items", [])
        except (OSError, ValueError, AttributeError):
            return []
        return [i for i in items if isinstance(i, dict) and i.get("path")]

    def _save(self, items: list) -> None:
        atomicio.write_text(self.path, json.dumps({"items": items}, ensure_ascii=False, indent=1))

    def manages(self, path) -> bool:
        key = norm(path)
        return any(_same(key, norm(a)) or key.lower().startswith(norm(a).lower() + "/") for a in self._allowed() if a)

    def add(self, path, name: str = "", kind: str = "project", reason: str = "") -> bool:
        """Record that a launch was refused for `path`. False when the Chief does not manage that folder or it is already trusted."""
        path = str(path or "")
        if not path or not self.manages(path) or self._trusted(path):
            return False
        items = self._load()
        key = target(path)                  # what gets trusted: the folder's repository, shown to the user as it is
        if any(_same(norm(i["path"]), key) for i in items):
            return True
        items.append({"path": key, "name": name or Path(path).name, "kind": kind, "reason": reason[:300], "since": self._now()})
        self._save(items)
        return True

    def items(self) -> list:
        """The folders still waiting for the user (those trusted meanwhile, or no longer managed, are dropped)."""
        items = self._load()
        keep = [i for i in items if not self._trusted(i["path"])]
        if len(keep) != len(items):
            self._save(keep)
        return keep

    def remove(self, path) -> None:
        key = norm(path)
        self._save([i for i in self._load() if not _same(norm(i["path"]), key)])
