"""Chief settings: vault, project folders, each project's category and the git/GitHub connection.

Everything is saved to projects.json (the file format, LF, written atomically). It never stores secrets: for GitHub it only
shows, via status commands, whether there is a login (account and host) and which credential manager git uses. Logging in is
always up to the user."""
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import chief

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HOST_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,60}\.[a-z]{2,}$")
MAX_HOSTS = 12


# ---------------------------------------------------------------- saving
def _save(patch: dict) -> None:
    fresh = chief.home.read_config(chief.HOME)
    chief._write_config({**fresh, **patch})
    chief.CFG = {**chief.CFG, **patch}


def _project(name: str) -> dict:
    p = next((x for x in chief.load_projects() if x["name"] == name), None)
    if p is None:
        raise ValueError("unknown project")
    return p


clean_domain = chief.clean_domain


# ---------------------------------------------------------------- each project's category
def set_domain(name: str, raw) -> dict:
    """A project's category, a Chief setting (empty = the vault hub's own applies again). The vault is not touched."""
    p = _project(name)
    dom = clean_domain(raw)
    domains = {k: v for k, v in chief.CFG.get("domains", {}).items() if k != name}
    if dom:
        domains[name] = dom
    _save({"domains": domains})
    return {"name": name, "domain": dom or p["domain"]}


# ---------------------------------------------------------------- vault and folders
def _guard(path: Path) -> None:
    low = chief.norm(path)
    if len(path.parts) <= 1:
        raise ValueError("a drive root is not accepted")
    if path.name.lower() in chief.SENSITIVE_DIRS or any(x in low for x in chief.SENSITIVE_PARTS):
        raise ValueError("sensitive folder (keys, secrets or personal documents): not adding it")
    if chief._inside(path, chief.ROOT) or chief._inside(path, chief.HOME):
        raise ValueError("this is the Chief (or inside it): the Chief does not launch workers in it")


def _dir(raw) -> Path:
    text = raw.strip().strip('"').strip("'").strip() if isinstance(raw, str) else ""
    if not text or len(text) > 400 or "\x00" in text:
        raise ValueError("give the full path of the folder")
    if not Path(text).is_absolute():
        raise ValueError("the path must be absolute (e.g. D:/Projects/my-repo)")
    path = Path(text)
    if not path.is_dir():
        raise ValueError("that folder does not exist (or is not a folder)")
    return path.resolve()


def set_vault(raw) -> dict:
    path = _dir(raw)
    _guard(path)
    if not ((path / "wiki").is_dir() or (path / "00-Index.md").is_file()):
        raise ValueError("this does not look like a vault: the wiki folder or the 00-Index.md file is missing")
    _save({"vault": str(path).replace("\\", "/")})
    return {"vault": str(path), "restart": True}


def add_root(raw) -> dict:
    path = _dir(raw)
    _guard(path)
    if chief._inside(path, chief.VAULT):
        raise ValueError("this is the Vault (or inside it): knowledge lives there, it is not a project")
    stored = str(path).replace("\\", "/")
    if any(chief.norm(r) == chief.norm(stored) for r in chief.CFG["roots"]):
        raise ValueError("that folder is already in the list")
    _save({"roots": [*chief.CFG["roots"], stored]})
    return {"roots": chief.CFG["roots"]}


def remove_root(raw) -> dict:
    roots = [r for r in chief.CFG["roots"] if chief.norm(r) != chief.norm(raw or "")]
    if len(roots) == len(chief.CFG["roots"]):
        raise ValueError("that folder is not in the list")
    if not roots:
        raise ValueError("at least one projects folder must remain (it is where repos are cloned)")
    _save({"roots": roots})
    return {"roots": roots}


def set_ignored(name: str, ignored: bool) -> dict:
    cur = list(chief.CFG["ignore"])
    if ignored and name not in cur:
        cur.append(name)
    if not ignored:
        cur = [x for x in cur if x != name]
    _save({"ignore": cur})
    return {"ignore": cur}


def remove_project(name: str) -> dict:
    """Take a project off the list: an extra stops being one; a child of a projects folder becomes ignored. Nothing is deleted from disk."""
    p = _project(name)
    n = chief.norm(p["path"])
    extras = [x for x in chief.CFG["extras"] if chief.norm(x) != n]
    if len(extras) != len(chief.CFG["extras"]):
        _save({"extras": extras})
        return {"removed": name, "how": "extra"}
    if any(chief.norm(Path(p["path"]).parent) == chief.norm(r) for r in chief.CFG["roots"]):
        set_ignored(Path(p["path"]).name, True)
        return {"removed": name, "how": "ignored"}
    raise ValueError("I could not tell where this project comes from")


def move_extra(name: str, raw) -> dict:
    """Change the path of a hand-added project (an extra). Those coming from a projects folder are changed by moving the folder on disk."""
    p = _project(name)
    old = chief.norm(p["path"])
    if not any(chief.norm(x) == old for x in chief.CFG["extras"]):
        raise ValueError("this project comes from a projects folder: move the folder on disk")
    saved = chief.CFG
    chief.CFG = {**saved, "extras": [x for x in saved["extras"] if chief.norm(x) != old]}
    try:
        new = chief.check_new_project_path(raw)
    finally:
        chief.CFG = saved
    stored = str(new).replace("\\", "/")
    _save({"extras": [stored if chief.norm(x) == old else x for x in saved["extras"]]})
    return {"name": name, "path": stored}


def set_clone_hosts(raw) -> dict:
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw or ""))
    hosts = []
    for h in items:
        h = str(h).strip().lower()
        if not h:
            continue
        if not HOST_RE.match(h):
            raise ValueError(f"invalid host: {h}")
        if h not in hosts:
            hosts.append(h)
    if not hosts or len(hosts) > MAX_HOSTS:
        raise ValueError(f"give between 1 and {MAX_HOSTS} hosts (e.g. github.com)")
    _save({"clone_hosts": hosts})
    return {"hosts": hosts}


# ---------------------------------------------------------------- git and GitHub (read-only, no secrets)
def _run(cmd: list, cwd=None, timeout: int = 8) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW)
        return (r.stdout + "\n" + r.stderr) if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def clean_remote(url: str) -> tuple[str, str]:
    """(text to show, host) for a remote URL, without any user or password the URL carries."""
    u = (url or "").strip()
    m = re.match(r"^[a-z+]+://(?:[^@/]*@)?([^/:]+)(?::\d+)?/(.+?)(?:\.git)?/?$", u, re.I) or re.match(r"^[\w.-]+@([^:]+):(.+?)(?:\.git)?$", u)
    return (f"{m.group(1).lower()}/{m.group(2)}", m.group(1).lower()) if m else ("", "")


def git_remote(path: str) -> dict:
    if not (Path(path) / ".git").exists():
        return {"remote": "", "host": "", "git": False}
    shown, host = clean_remote(_run(["git", "-C", path, "remote", "get-url", "origin"], timeout=4).strip())
    return {"remote": shown, "host": host, "git": True}


_gh = {"at": 0.0, "val": None}
_gh_lock = threading.Lock()


def github_status() -> dict:
    """Is there `gh` and a login? Which credential manager does git use? Only names and account: never tokens (cached for 60 s)."""
    with _gh_lock:
        if _gh["val"] is not None and time.monotonic() - _gh["at"] < 60:
            return _gh["val"]
        gh = shutil.which("gh")
        text = _run([gh, "auth", "status", "--hostname", "github.com"]) if gh else ""
        m = re.search(r"Logged in to github\.com (?:account|as) (\S+)", text)
        helpers = [h.strip() for h in _run(["git", "config", "--get-all", "credential.helper"], timeout=4).splitlines() if h.strip()]
        val = {"gh": bool(gh), "logged_in": bool(m), "account": m.group(1) if m else "", "helpers": helpers[:4]}
        _gh.update(at=time.monotonic(), val=val)
        return val


def hosts() -> list:
    return list(chief.CFG.get("clone_hosts") or chief.CLONE_HOSTS)


def snapshot() -> dict:
    projs = chief.load_projects()
    extras = {chief.norm(x) for x in chief.CFG["extras"]}
    with ThreadPoolExecutor(max_workers=8) as ex:
        remotes = list(ex.map(lambda p: git_remote(p["path"]), projs))
    domains = chief.CFG.get("domains", {})
    rows = []
    for p, g in zip(projs, remotes):
        over = domains.get(p["name"], "")
        rows.append({"name": p["name"], "path": p["path"], "domain": over or p["domain"], "from_vault": p["domain"],
                     "custom": bool(over), "kind": "extra" if chief.norm(p["path"]) in extras else "root", "hub": bool(p["hub"]), **g})
    known = sorted({r["domain"] for r in rows if r["domain"]} | {h["domain"] for h in chief.hubs() if h["domain"]})
    return {"vault": str(chief.VAULT), "roots": list(chief.CFG["roots"]), "ignore": list(chief.CFG["ignore"]),
            "hosts": hosts(), "domains": known, "projects": rows, "github": github_status()}
