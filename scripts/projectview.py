"""Read-only, sandboxed access to a project's documents for the project pages.

Pure functions: nothing here touches the network or writes anything. Every path
is resolved and must stay inside the project folder; secret-like names are
refused; text is redacted with the same patterns as `sessions.py`.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from sessions import _PRIVATE_IP, _SECRET

MAX_DOC = 200_000
TRUNCATED_NOTICE = "\n\n[truncated]"

# Secret-like names, matched against every component of a relative path.
FORBIDDEN = re.compile(
    r"^(?:\.env.*|.*\.(?:pem|key|p12|pfx)|id_(?:rsa|dsa|ecdsa|ed25519).*"
    r"|.*token.*|secrets.*|ssh access.*|.*codes.*|settings\.local\.json|claude\.local\.md)$",
    re.IGNORECASE,
)

# Bounded variant of sessions._EMAIL: the original is quadratic on long word runs.
_EMAIL_BOUNDED = re.compile(r"[\w.+-]{1,64}@[\w-]{1,255}\.[\w.-]{1,255}")
_SHORT_NAME = re.compile(r"~\d")

_DOC_DIRS = (("docs/specs", "spec"), ("docs/plans", "plan"), ("docs/analyses", "analysis"))
_ROOT_DOCS = (("README.md", "readme"), ("CLAUDE.md", "claude"), ("ARCHITECTURE.md", "other"))


def _is_forbidden(component: str) -> bool:
    """Forbidden after the Windows name normalisation (dots, spaces, streams, 8.3)."""
    if ":" in component or _SHORT_NAME.search(component):
        return True
    return bool(FORBIDDEN.match(component.rstrip(" .")))


def safe_path(root: Path, rel: str) -> Path:
    """Resolve `rel` inside `root` or raise ValueError."""
    if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
        raise ValueError("invalid path")
    norm = rel.replace("\\", "/")
    parts = norm.split("/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or Path(rel).is_absolute():
        raise ValueError("absolute paths are not allowed")
    if ".." in parts:
        raise ValueError("parent references are not allowed")
    if any(_is_forbidden(p) for p in parts if p):
        raise ValueError("forbidden name")
    base = root.resolve()
    target = (base / norm).resolve()
    if not target.is_relative_to(base):
        raise ValueError("path leaves the project")
    if any(FORBIDDEN.match(p) for p in target.relative_to(base).parts):
        raise ValueError("forbidden name")
    if not target.is_file():
        raise ValueError("not a file")
    return target


# Values of keys named like a secret, in JSON / YAML / INI / header forms (quoted key, spaces around the separator).
# Every quantifier before the separator is bounded, so the scan stays linear on hostile single-line input.
_KEYED_SECRET = re.compile(
    r"""(?ix)
    ((?:key|secret|token|password|passwd|pwd|senha|\bpass\b|auth(?!ors?\b)|credential|bearer)   # a word inside the key name
     [\w.-]{0,40}["']?[ \t]{0,20}[:=][ \t]{0,20}                              # rest of the key, separator
     (?:(?:bearer|basic)[ \t]+)?)                                             # Authorization: Bearer <value>
    ("[^"\n]{0,2000}"|'[^'\n]{0,2000}'|[^\s,;}\]'"]{1,2000})""")


# scheme://user:password@host: the password goes; scheme, user and host stay (bounded, so the scan stays linear)
_URL_CREDENTIAL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{0,20}://[^\s:/@]{1,200}:)([^\s@/]{1,200})(?=@)")


def redact(text: str) -> str:
    """Remove secrets, private IPs and e-mails from `text`."""
    text = _URL_CREDENTIAL.sub(lambda m: m.group(1) + "[secret]", text)
    text = _KEYED_SECRET.sub(lambda m: m.group(1) + "[secret]", text)
    for rx, rep in ((_SECRET, "[secret]"), (_PRIVATE_IP, "[private ip]"), (_EMAIL_BOUNDED, "[e-mail]")):
        text = rx.sub(rep, text)
    return text


def _title(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh.readlines(4096):
                if line.startswith("#"):
                    return redact(line.lstrip("#").strip())
    except OSError:
        pass
    return path.stem


def _entry(root: Path, rel: str, kind: str, now: float) -> dict | None:
    try:
        target = safe_path(root, rel)
        age = max(0.0, now - target.stat().st_mtime)
    except (ValueError, OSError):
        return None
    return {"path": rel, "kind": kind, "title": _title(target), "age_s": int(age)}


def _candidates(root: Path) -> list[tuple[str, str]]:
    found = [(name, kind) for name, kind in _ROOT_DOCS]
    for folder, kind in _DOC_DIRS:
        base = root / folder
        try:
            names = sorted(p.name for p in base.iterdir() if p.suffix.lower() == ".md")
        except OSError:
            continue
        found.extend((f"{folder}/{n}", kind) for n in names)
    return found


def list_docs(root: Path) -> list[dict]:
    """Documents of a project: `{path, kind, title, age_s}`."""
    now = time.time()
    entries = (_entry(root, rel, kind, now) for rel, kind in _candidates(root))
    return [e for e in entries if e]


def read_doc(root: Path, rel: str) -> dict:
    """Redacted text of one document, cut at MAX_DOC characters."""
    target = safe_path(root, rel)
    with target.open("rb") as fh:
        raw = fh.read(MAX_DOC * 4 + 1)
    text = redact(raw.decode("utf-8", errors="replace"))
    truncated = len(text) > MAX_DOC or target.stat().st_size > len(raw)
    text = text[:MAX_DOC]
    return {"path": rel.replace("\\", "/"), "text": text + (TRUNCATED_NOTICE if truncated else ""),
            "truncated": truncated}


# ---- overview, architecture and tools (names only, never values) ----

MAX_COMMANDS = 20
_MAX_CONFIG = 1_000_000
_ARCH_HEADING = re.compile(r"architect|arquitet|design", re.IGNORECASE)
_CMD_HEADING = re.compile(r"\b(?:tests?|testing|build|lint|commands?|comandos?)\b", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*:(?!=)")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,47}$")
_RUNNERS = {"npm", "pnpm", "yarn", "make", "python", "python3", "cargo", "go", "npx"}
_SUBCOMMANDS = {"test", "build", "lint", "start", "dev", "check", "run", "format", "typecheck", "tsc",
                "install", "serve", "clean", "all"}


def _safe_name(name: str) -> str:
    """The name itself when it is short and made of plain characters, else empty."""
    return name if _SAFE_NAME.match(name) and redact(name) == name else ""


def _read_text(root: Path, rel: str, limit: int = MAX_DOC) -> str:
    """Safe, bounded read of a project file; empty string on any problem."""
    try:
        with safe_path(root, rel).open("rb") as fh:
            return fh.read(limit).decode("utf-8", errors="replace")
    except (ValueError, OSError):
        return ""


def _read_json(root: Path, rel: str) -> dict:
    try:
        data = json.loads(_read_text(root, rel, _MAX_CONFIG))
    except (ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _section(text: str, pattern: re.Pattern) -> str:
    """Body of the first heading matching `pattern`, up to the next heading of the same or higher level."""
    level, body, fenced = 0, [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            if level:
                body.append(line)
            continue
        m = None if fenced else _HEADING.match(line)
        if level == 0:
            if m and pattern.search(m.group(2)):
                level = len(m.group(1))
            continue
        if m and len(m.group(1)) <= level:
            break
        body.append(line)
    return "\n".join(body).strip()


def overview(root: Path, vault_note: Path | None) -> dict:
    """Project summary from the vault note, else the README; plus its architecture section.

    `vault_note` is trusted to be inside the vault: the caller must pass a path it already
    resolved there. It is still bounded, must be a regular file and must not be secret-like.
    """
    source, text = None, ""
    if vault_note is not None:
        try:
            if vault_note.is_file() and not _is_forbidden(vault_note.name):
                with vault_note.open("rb") as fh:
                    text = fh.read(MAX_DOC * 4).decode("utf-8", errors="replace")
                source = "vault"
        except OSError:
            text = ""
    if not text.strip():
        text, source = _read_text(root, "README.md"), "readme"
    if not text.strip():
        return {"source": None, "text": "", "architecture": ""}
    text = redact(text)
    return {"source": source, "text": text[:MAX_DOC], "architecture": _section(text, _ARCH_HEADING)[:MAX_DOC]}


def _command_name(line: str) -> str:
    parts = line.strip().lstrip("$> ").split()
    if not parts or not _safe_name(parts[0]):
        return ""
    name = parts[0]
    if name in _RUNNERS and len(parts) > 1:
        rest = parts[1:]
        if rest[0] == "run" and len(rest) > 1:
            rest = rest[1:]
        if rest[0] in _SUBCOMMANDS:
            name += " " + rest[0]
    return name


def _doc_commands(text: str) -> list[str]:
    names, active, fenced = [], False, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING.match(line)
        if m:
            active = bool(_CMD_HEADING.search(m.group(2)))
        elif fenced and active:
            name = _command_name(line)
            if name:
                names.append(name)
    return names


def _pyproject_commands(root: Path) -> list[tuple[str, str]]:
    if sys.version_info < (3, 11):
        return []
    import tomllib
    try:
        data = tomllib.loads(_read_text(root, "pyproject.toml", _MAX_CONFIG))
    except (ValueError, RecursionError):
        return []
    found = []
    if isinstance(data.get("tool"), dict) and "pytest" in data["tool"]:
        found.append(("pytest", "pyproject.toml"))
    scripts = data.get("project", {}).get("scripts") if isinstance(data.get("project"), dict) else None
    if isinstance(scripts, dict):
        found.extend((str(k), "pyproject.toml") for k in scripts)
    return found


def _commands(root: Path) -> list[dict]:
    found: list[tuple[str, str]] = []
    scripts = _read_json(root, "package.json").get("scripts")
    if isinstance(scripts, dict):
        found.extend((str(k), "package.json") for k in scripts)
    found.extend(_pyproject_commands(root))
    for line in _read_text(root, "Makefile").splitlines():
        m = _MAKE_TARGET.match(line)
        if m and not m.group(1).startswith("."):
            found.append((m.group(1), "Makefile"))
    for doc in ("README.md", "CLAUDE.md"):
        found.extend((n, doc) for n in _doc_commands(_read_text(root, doc)))
    seen, out = set(), []
    for name, source in found:
        name = _safe_name(name) if " " not in name else " ".join(_safe_name(w) for w in name.split())
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "source": source})
    return out[:MAX_COMMANDS]


def _names(root: Path, folder: Path, want_dirs: bool) -> list[str]:
    """Plain names inside `folder`; nothing that resolves outside the project root."""
    try:
        base = root.resolve()
        if not folder.resolve().is_relative_to(base):
            return []
        entries = sorted(folder.iterdir())
    except OSError:
        return []
    out = []
    for p in entries:
        try:
            if FORBIDDEN.match(p.name) or not p.resolve().is_relative_to(base):
                continue
            if want_dirs and p.is_dir():
                out.append(p.name)
            elif not want_dirs and p.is_file() and p.suffix.lower() == ".md":
                out.append(p.stem)
        except OSError:
            continue
    return [n for n in map(_safe_name, out) if n]


def _keys(data: dict, key: str) -> list[str]:
    value = data.get(key)
    return [n for n in map(_safe_name, map(str, value)) if n] if isinstance(value, dict) else []


def tools(root: Path) -> dict:
    """Commands and Claude Code setup of a project: names only, never values."""
    base = root / ".claude"
    settings = _read_json(root, ".claude/settings.json")
    mcp = _keys(_read_json(root, ".mcp.json"), "mcpServers") + _keys(settings, "mcpServers")
    return {
        "commands": _commands(root),
        "claude": {
            "skills": _names(root, base / "skills", True),
            "commands": _names(root, base / "commands", False),
            "agents": _names(root, base / "agents", False),
            "mcp": list(dict.fromkeys(mcp)),
            "hooks": _keys(settings, "hooks"),
        },
    }


# ---- identified fixes and request summaries ----

MAX_FINDING = 300
_FINDING = re.compile(r"\*\*\[\s*(CRITICAL|HIGH)\s*\]")
_CLOSED = {"done", "cancelled"}
_FIXABLE = {"awaiting", "failed", "blocked"}


_HEAD_END = re.compile(r"^(.*?)(?::\*\*|\*\*:)\s*(.*)$")   # the head ends at the first ":**" (or "**:")


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


_LEADING_VALUE = re.compile(r"""(?i)^\s*(?:(?:bearer|basic)[ \t]+)?("[^"\n]{0,2000}"|'[^'\n]{0,2000}'|\S{1,2000})""")


def _split_redacted(head: str, body: str) -> tuple[str, str]:
    """Redact head and body, also catching a secret whose key ends the head and whose value starts the body.

    `head: body` is redacted as ONE string; if that differs from redacting the two parts apart, a secret
    straddles the split (the redaction may swallow the separator, so the result cannot be split again):
    the value at the start of the body is masked.
    """
    head_r, body_r = redact(head), redact(body)
    if not body or redact(f"{head}: {body}") == f"{head_r}: {body_r}":
        return head_r, body_r
    rest = _LEADING_VALUE.sub("", body, count=1).strip()
    return head_r, "[secret]" + (" " + redact(rest) if rest else "")


def _finding(severity: str, rest: str) -> dict:
    """One `- **[SEV] head:** body` line, after the severity: redacted as a whole, then head and body clipped."""
    m = _HEAD_END.match(rest)
    head, body = (m.group(1), m.group(2)) if m else (rest, "")
    head, body = _split_redacted(head.replace("**", "").strip().rstrip(":").strip(), body.strip())
    head, body = _clip(head, MAX_FINDING), _clip(body, MAX_FINDING)
    return {"severity": severity, "head": head, "body": body,
            "text": f"{head} — {body}" if head and body else head or body}


def findings(text: str) -> list[dict]:
    """CRITICAL and HIGH findings of a review: `{severity, head, body, text}`.

    `text` is a clean one-line "head — body" (no `**`, no `[SEV]`) kept for older readers.
    """
    out = []
    for line in (text or "").splitlines():
        m = _FINDING.search(line)
        if m:
            out.append(_finding(m.group(1), line[m.end():]))
    return out


def fixes(project: str, feats: list, artifacts, pending: list) -> list[dict]:
    """Identified fixes of a project. Nothing is stored.

    Findings come only from requests of `project` that are awaiting, failed or blocked: from the
    `planreview` artifact when the request is at stage `plan`, else from `review`.
    Plus the project's session to-dos with status `blocked`.
    """
    out = []
    for f in feats:
        if f.get("project") != project or f.get("status") not in _FIXABLE:
            continue    # a working request is being fixed by the pipeline: its artifact still holds the previous round
        plan = f.get("stage") == "plan"
        key, source = ("planreview", "plan-review") if plan else ("review", "review")
        for item in findings((artifacts(f["id"]) or {}).get(key, "")):
            out.append({"source": source, "request": f["id"], "title": redact(str(f.get("title", ""))),
                        **item})
    for p in pending:
        if p.get("project") == project and p.get("status") == "blocked":
            title = redact(str(p.get("title", "")))[:MAX_FINDING]
            out.append({"source": "todo", "request": p.get("id"), "title": title, "severity": "HIGH",
                        "head": title, "body": "", "text": title})
    return out


def requests_view(project: str, feats: list) -> dict:
    """Requests of a project split into open and closed (done/cancelled)."""
    view = {"open": [], "closed": []}
    for f in feats:
        if f.get("project") != project:
            continue
        cost = f.get("cost")
        row = {"id": f.get("id"), "title": redact(str(f.get("title", ""))), "kind": f.get("kind"),
               "stage": f.get("stage"), "status": f.get("status"),
               "cost_usd": cost.get("cost_usd") if isinstance(cost, dict) else None}
        view["closed" if f.get("status") in _CLOSED else "open"].append(row)
    return view
