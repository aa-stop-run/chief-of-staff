"""Checks over a knowledge vault that need no worker: structure, secrets, the verification ledger, what a worker's findings must satisfy, and
the one way a note is ever changed (an exact replacement the user confirmed).

Pure functions over a folder: nothing here launches anything or writes except `apply_replace` and `log_entry`."""
import hashlib
import importlib.util
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from sessions import _PRIVATE_IP, _SECRET

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---", re.S)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
NEVER_EDIT = ("inbox/", ".obsidian/", ".git/", ".trash/")
KINDS = ("stale", "contradiction")
SEVERITIES = ("high", "medium", "low")
MAX_FINDINGS, MAX_TEXT, MAX_EVIDENCE = 40, 400, 5
DRAFT_AGE_S = 3 * 86400
HOT_STALE_DAYS = 14
LINT_CATEGORIES = ("dangling_links", "ambiguous_targets", "orphans", "empty_sections", "stale_index_entries")


class FindingsError(Exception):
    pass


class ApplyError(Exception):
    pass


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def frontmatter(text: str) -> dict:
    """The first-level `key: value` lines of a note's frontmatter (values as written, trimmed)."""
    m = FRONTMATTER.match(text or "")
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        k, sep, v = line.partition(":")
        if sep and k.strip() and not line.startswith((" ", "\t", "-")):
            out[k.strip()] = v.strip()
    return out


def has_secret(text: str) -> bool:
    return bool(_SECRET.search(text or "") or _PRIVATE_IP.search(text or "") or _EMAIL.search(text or ""))


def fingerprint(kind: str, note: str, quote: str, evidence=()) -> str:
    """Identifies a finding across runs (so a dismissed one stays dismissed until the note or the evidence changes)."""
    key = "|".join([kind, note, " ".join((quote or "").split()), " ".join(" ".join(evidence).split())])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def finding(layer: str, kind: str, severity: str, note: str, quote: str, evidence=(), replace=None, detail: str = "") -> dict:
    ev = [str(e)[:MAX_TEXT] for e in evidence][:MAX_EVIDENCE]
    fp = fingerprint(kind, note, quote, ev)
    return {"id": fp[:10], "fingerprint": fp, "layer": layer, "kind": kind, "severity": severity, "note": note,
            "quote": (quote or "")[:MAX_TEXT], "evidence": ev, "replace": replace, "detail": (detail or "")[:MAX_TEXT], "status": "open"}


# ---------------------------------------------------------------- which notes
def note_paths(vault: Path, hub_dirs) -> list:
    """The project and area notes (hubs and the detail notes in their folders), as vault-relative posix paths."""
    out = []
    for d in hub_dirs:
        base = Path(vault) / d
        if base.is_dir():
            out += sorted(p.relative_to(vault).as_posix() for p in base.rglob("*.md"))
    return out


def read_note(vault: Path, rel: str) -> str:
    return (Path(vault) / rel).read_text(encoding="utf-8", errors="replace")


def project_of(rel: str, hub_dirs) -> str:
    """The project a note belongs to: `Projetos/<name>.md` and `Projetos/<name>/…` both give <name>."""
    parts = rel.split("/")
    if len(parts) < 2 or parts[0] not in hub_dirs:
        return ""
    return parts[1][:-3] if parts[1].endswith(".md") else parts[1]


# ---------------------------------------------------------------- L0 / L1: no worker needed
def structure_findings(vault, schema: dict, drafts: int = 0, oldest_draft_s: float = 0.0, now=None) -> list:
    """Deterministic findings about the vault's own consistency. `drafts`/`oldest_draft_s`: the inbox backlog (the caller knows)."""
    vault, out = Path(vault), []
    hub_dirs, fm = tuple(schema["hub_dirs"]), schema["fm"]
    index = (vault / "00-Index.md").read_text(encoding="utf-8", errors="replace") if (vault / "00-Index.md").is_file() else None
    canvas = (vault / "Mapa.canvas").read_text(encoding="utf-8", errors="replace") if (vault / "Mapa.canvas").is_file() else None
    for rel in note_paths(vault, hub_dirs):
        text = read_note(vault, rel)
        props = frontmatter(text)
        is_hub = rel.count("/") == 1 and "local" in props
        name = Path(rel).stem
        if is_hub:
            local = props.get("local", "").strip().strip('"\'')
            if local and not Path(local).exists():
                out.append(finding("structure", "local-missing", "medium", rel, f"local: {local}", [f"the folder {local} does not exist on this machine"]))
            for key in (fm["kind"], fm["status"], "stack", "repo"):
                if key not in props:
                    out.append(finding("structure", "frontmatter", "low", rel, "", [f"the hub has no `{key}:` in its frontmatter"]))
            if index is not None and name not in index:
                out.append(finding("structure", "not-in-index", "low", rel, "", [f"«{name}» is not in 00-Index.md"]))
            if canvas is not None and name not in canvas:
                out.append(finding("structure", "not-in-canvas", "low", rel, "", [f"«{name}» is not in Mapa.canvas"]))
        for n, line in enumerate(text.splitlines(), 1):
            if has_secret(line):
                out.append(finding("structure", "secret", "high", rel, f"[line {n}: redacted]", ["a token, a private IP or an e-mail address is written here"]))
    hot = vault / "wiki" / "hot.md"
    if drafts and oldest_draft_s >= DRAFT_AGE_S:
        updated = frontmatter(hot.read_text(encoding="utf-8", errors="replace")).get("updated", "") if hot.is_file() else ""
        out.append(finding("health", "backlog", "medium", "inbox", "", [f"{drafts} draft(s) wait in the inbox, the oldest for {int(oldest_draft_s // 86400)} days",
                                                                  f"wiki/hot.md was last updated {updated or 'never'}"]))
    elif hot.is_file():
        try:
            age = ((now or datetime.now()).date() - date.fromisoformat(frontmatter(hot.read_text(encoding="utf-8", errors="replace")).get("updated", ""))).days
        except ValueError:
            age = 0
        if age > HOT_STALE_DAYS and drafts:
            out.append(finding("health", "backlog", "low", "wiki/hot.md", "", [f"wiki/hot.md was last updated {age} days ago and the inbox has drafts"]))
    return out


def plugin_lint(vault, lint_fn=None) -> list:
    """The claude-obsidian linter's findings (links, orphans, empty sections, the index), normalised. [] when the plugin is not there."""
    if lint_fn is None:
        found = sorted(Path.home().glob(".claude/plugins/marketplaces/*/claude_obsidian/lint_engine.py"))
        if not found:
            return []
        root = found[-1].parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            spec = importlib.util.spec_from_file_location("claude_obsidian.lint_engine", found[-1])
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            lint_fn = mod.lint_vault
        except Exception:  # noqa: BLE001 - an optional helper that is missing or incompatible simply adds nothing
            return []
    try:
        report = lint_fn(vault)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for cat in LINT_CATEGORIES:
        for e in (report.get(cat) or [])[:50]:
            if isinstance(e, dict):
                note = str(e.get("path") or e.get("source") or e.get("page") or "")
                out.append(finding("structure", "lint-" + cat, "low", note, str(e.get("target") or e.get("heading") or "")[:200],
                                   [str(e.get("reason") or cat.replace("_", " "))]))
    return out


# ---------------------------------------------------------------- the ledger: what was verified, against what
def is_stale(entry, content_hash: str, head: str, facts_hash: str) -> bool:
    """A note needs checking when it was never verified, or its text, the project's HEAD or the facts changed since."""
    return not entry or entry.get("hash") != content_hash or entry.get("head") != head or entry.get("facts") != facts_hash


# ---------------------------------------------------------------- what a worker's findings must satisfy
def validate_findings(data, staged: dict) -> list:
    """The findings of a worker, checked against the copies it was given (`staged`: note path -> text). A bad file is refused whole:
    an unknown note, a quote that is not in the note, a replacement that does not occur exactly once, a secret anywhere."""
    if not isinstance(data, dict) or not isinstance(data.get("findings", []), list):
        raise FindingsError("the findings file is not an object with a «findings» list")
    items = data.get("findings", [])
    if len(items) > MAX_FINDINGS:
        raise FindingsError(f"more than {MAX_FINDINGS} findings")
    if has_secret(json.dumps(data, ensure_ascii=False)):
        raise FindingsError("the findings contain something that looks like a secret, a private IP or an e-mail address")
    out = []
    for i, f in enumerate(items):
        where = f"finding {i + 1}"
        if not isinstance(f, dict):
            raise FindingsError(f"{where} is not an object")
        kind, sev, note = f.get("kind"), f.get("severity"), f.get("note")
        if kind not in KINDS or sev not in SEVERITIES:
            raise FindingsError(f"{where}: kind must be one of {KINDS} and severity one of {SEVERITIES}")
        if note not in staged:
            raise FindingsError(f"{where}: «{note}» is not a note it was given")
        quote = str(f.get("quote", ""))
        if not quote or quote not in staged[note]:
            raise FindingsError(f"{where}: the quoted text is not in {note}")
        ev = f.get("evidence")
        if not isinstance(ev, list) or not ev or not all(isinstance(e, str) and e.strip() for e in ev):
            raise FindingsError(f"{where}: evidence is mandatory (a list of short sentences)")
        rep = f.get("replace")
        if rep is not None:
            if not (isinstance(rep, dict) and isinstance(rep.get("old"), str) and isinstance(rep.get("new"), str) and rep["old"]):
                raise FindingsError(f"{where}: replace must be {{old, new}}")
            if len(rep["old"]) > 2000 or len(rep["new"]) > 2000 or staged[note].count(rep["old"]) != 1:
                raise FindingsError(f"{where}: replace.old must occur exactly once in {note}")
            rep = {"old": rep["old"], "new": rep["new"]}
        out.append(finding("audit", kind, sev, note, quote, ev, rep, str(f.get("detail", ""))))
    return out


# ---------------------------------------------------------------- the only way a note changes
def safe_note(vault, rel: str) -> Path:
    vault = Path(vault).resolve()
    p = (vault / rel).resolve()
    if vault not in p.parents or not p.is_file() or p.suffix != ".md" or any(rel.replace("\\", "/").startswith(n) for n in NEVER_EDIT):
        raise ApplyError("that is not a note this can change")
    return p


def apply_replace(vault, rel: str, old: str, new: str, expect_hash: str) -> str:
    """Replace `old` by `new` in one note, only if the note is exactly as it was when verified and `old` occurs once. Returns the new hash."""
    p = safe_note(vault, rel)
    text = p.read_text(encoding="utf-8")
    if sha(text) != expect_hash:
        raise ApplyError("the note changed since it was checked: it will be checked again")
    if text.count(old) != 1:
        raise ApplyError("the text to replace is no longer there exactly once: it will be checked again")
    if has_secret(new):
        raise ApplyError("the new text looks like a secret, a private IP or an e-mail address")
    updated = text.replace(old, new, 1)
    p.write_text(updated, encoding="utf-8", newline="")
    return sha(updated)


def log_entry(vault, title: str, notes: list, today=None) -> None:
    """Add «## <date> — <title>» with one bullet per note at the top of wiki/log.md (newest first), as the vault's rules ask."""
    log = Path(vault) / "wiki" / "log.md"
    if not log.is_file():
        return
    day = (today or date.today()).isoformat()
    entry = f"## {day} — {title}\n" + "".join(f"- [[{n[:-3] if n.endswith('.md') else n}]]\n" for n in notes) + "\n"
    text = log.read_text(encoding="utf-8")
    m = re.search(r"^## ", text, re.M)
    log.write_text(text[:m.start()] + entry + text[m.start():] if m else text.rstrip("\n") + "\n\n" + entry, encoding="utf-8", newline="")
