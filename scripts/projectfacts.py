"""The facts of a project, read from its folder, for the vault audit: what a note about the project can be compared with.

Read-only (git queries and the few documents at the root), redacted, and small. Never the project's code, never `.env*`, keys or tokens."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

from projectview import redact

DOCS = ("README.md", "CLAUDE.md", "ARCHITECTURE.md")
MAX_FILES, MAX_COMMITS, MAX_HEADINGS, MAX_COMMANDS, MAX_BYTES = 80, 10, 30, 20, 12_000
_CRED = re.compile(r"(?<=//)[^/@\s]+@")
_HEAD = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.M)
_CMD = re.compile(r"^\s*(?:\$ )?((?:python3?|pytest|npm|node|git|claude|cargo|make|pip)\b[^\n]{0,120})$", re.M)
_SKIP_NAMES = (".env", "secret", "credential", "id_rsa", ".pem", ".key", "token")


def _git(folder: Path, *args) -> str:
    try:
        r = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _doc(folder: Path, name: str) -> dict:
    p = folder / name
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8", errors="replace")[:60_000]
    return {"headings": [h for h in _HEAD.findall(text)][:MAX_HEADINGS], "commands": list(dict.fromkeys(c.strip() for c in _CMD.findall(text)))[:MAX_COMMANDS]}


def collect(folder) -> dict:
    """The facts of the project at `folder` (a git repository or a plain folder). Always JSON-serialisable and under MAX_BYTES."""
    folder = Path(folder)
    if not folder.is_dir():
        return {"exists": False}
    out = {"exists": True, "repo": bool(_git(folder, "rev-parse", "--git-dir"))}
    if out["repo"]:
        remotes = sorted({_CRED.sub("", line.split()[1]) for line in _git(folder, "remote", "-v").splitlines() if len(line.split()) >= 2})
        log = [ln.split("|", 2) for ln in _git(folder, "log", f"-{MAX_COMMITS}", "--format=%h|%cs|%s").splitlines() if ln.count("|") >= 2]
        out.update(remote=remotes, branch=_git(folder, "branch", "--show-current"), head=_git(folder, "rev-parse", "HEAD")[:12],
                   commits=[{"sha": a, "date": b, "subject": c[:100]} for a, b, c in log],
                   uncommitted=len([ln for ln in _git(folder, "status", "--porcelain").splitlines() if ln.strip()]))
    out["files"] = sorted(n.name for n in folder.iterdir() if not n.name.startswith(".git") and not any(s in n.name.lower() for s in _SKIP_NAMES))[:MAX_FILES]
    out["docs"] = {n: d for n in DOCS if (d := _doc(folder, n))}
    text = redact(json.dumps(out, ensure_ascii=False, sort_keys=True))
    while len(text.encode("utf-8")) > MAX_BYTES and out.get("files"):             # an enormous folder: drop the file list first
        out["files"] = out["files"][: len(out["files"]) // 2]
        text = redact(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return json.loads(text)


def digest(facts: dict) -> str:
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
