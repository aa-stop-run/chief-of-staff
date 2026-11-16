#!/usr/bin/env python3
"""Read each project's Claude Code sessions and summarise the state they were left in. Read-only, standard library only.

  python scripts/sessions.py                    # every project with sessions (3 most recent each)
  python scripts/sessions.py --project myrepo   # one project
  python scripts/sessions.py --limit 5 --since 14 --json
  python scripts/sessions.py --all              # include folders without a project (e.g. C:\\Users\\<you>)

Reads ~/.claude/projects/<folder>/*.jsonl. Secrets, e-mails and private IPs are removed from the summary.
The summary helps decide which leftovers to record: see `python scripts/pending.py`.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

SESSIONS_DIR = Path.home() / ".claude" / "projects"
_SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{12,}"
                     r"|(?i:password|palavra-passe|passwd|token|secret|api[_-]?key)\s*[=:]\s*\S+)")
_PRIVATE_IP = re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))"
                         r"\.\d{1,3}(?:\.\d{1,3})?\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def encode_path(path) -> str:
    """Name of the folder Claude Code uses to store the sessions of a path."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path)).lower()


def scrub(text, limit: int = 300) -> str:
    t = " ".join(str(text).split())
    for rx, rep in ((_SECRET, "[secret]"), (_PRIVATE_IP, "[private ip]"), (_EMAIL, "[e-mail]")):
        t = rx.sub(rep, t)
    return t if len(t) <= limit else t[:limit] + "…"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_real_user_text(o: dict, t: str) -> bool:
    return o.get("type") == "user" and not o.get("isMeta") and bool(t.strip()) and not t.startswith(("<", "Caveat"))


def summarize(f: Path) -> dict:
    first = last_user = last_assistant = ""
    todos, msgs = None, 0
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            msgs += 1
            content = (o.get("message") or {}).get("content")
            if _is_real_user_text(o, _text(content)):
                last_user = _text(content)
                first = first or last_user
            if o.get("type") == "assistant" and isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_use" and b.get("name") == "TodoWrite":
                        todos = b.get("input", {}).get("todos")
                    if b.get("type") == "text" and b.get("text", "").strip():
                        last_assistant = b["text"]
    open_todos = [t for t in (todos or []) if t.get("status") != "completed"]
    return {"id": f.stem, "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="minutes"),
            "messages": msgs, "first": scrub(first, 160), "last_user": scrub(last_user, 240),
            "last_assistant": scrub(last_assistant, 600),
            "open_todos": [{"status": t.get("status"), "content": scrub(t.get("content", ""), 140)} for t in open_todos]}


def scan(projects: list, project: str = "", limit: int = 3, since_days: int = 0, include_unmapped: bool = False) -> list:
    """Return [{project, path, folder, sessions:[...]}] per sessions folder, most recent first."""
    by_folder = {encode_path(p["path"]): p for p in projects}
    cutoff = datetime.now() - timedelta(days=since_days) if since_days else None
    out = []
    for d in sorted(SESSIONS_DIR.iterdir()) if SESSIONS_DIR.is_dir() else []:
        p = by_folder.get(d.name.lower())
        if (p is None and not include_unmapped) or (project and (p is None or project.lower() not in p["name"].lower())):
            continue
        files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
        if cutoff:
            files = [f for f in files if datetime.fromtimestamp(f.stat().st_mtime) >= cutoff]
        if files:
            out.append({"project": p["name"] if p else None, "path": p["path"] if p else None, "folder": d.name,
                        "sessions": [summarize(f) for f in files[:limit]]})
    return out


LIVE_S = 600      # a session written to this recently is probably still open: it is not «left unfinished» yet


def candidates(report: list, known_sessions, worker_ids, now: datetime | None = None, live_s: int = LIVE_S) -> list:
    """The sessions of a `scan` that could become to-do cards: not the Chief's own workers (their logs sit in the same folders),
    not sessions that already have a card (dismissed ones included) and not sessions still in use (written to in the last
    `live_s` seconds). Sessions with unfinished to-dos lead; each group newest first."""
    known, workers = set(known_sessions), tuple(w for w in worker_ids if w)
    recent = ((now or datetime.now()) - timedelta(seconds=live_s)).isoformat(timespec="minutes")
    out = []
    for r in report:
        for s in r["sessions"]:
            if not r["project"] or s["id"] in known or (workers and s["id"].startswith(workers)) or not s["first"] or s["modified"] > recent:
                continue
            out.append({"project": r["project"], "session": s["id"], "modified": s["modified"], "messages": s["messages"],
                        "first": s["first"], "last": s["last_assistant"][:240], "open_todos": len(s["open_todos"])})
    newest = sorted(out, key=lambda c: c["modified"], reverse=True)
    return sorted(newest, key=lambda c: c["open_todos"] == 0)      # stable: unfinished to-dos first, each group newest first


def todo_from_session(s: dict) -> dict:
    """The card a session becomes: what was asked, how it ended, and its unfinished to-dos as what is missing."""
    missing = "; ".join(t["content"] for t in s["open_todos"][:8])
    return {"title": (s["first"] or "Session")[:140], "status": "awaiting", "summary": s["last_assistant"][:1200],
            "missing": missing[:1200], "date": s["modified"][:10]}


def find_session(projects: list, project: str, session_id: str):
    """The summary of one session of one project, or None. The file is looked up by its exact name inside that project's folder."""
    if not re.fullmatch(r"[0-9a-f-]{8,40}", session_id or ""):
        return None
    p = next((x for x in projects if x["name"] == project), None)
    f = SESSIONS_DIR / encode_path(p["path"]) / f"{session_id}.jsonl" if p else None
    if f is None or not f.is_file():        # Claude Code keeps the folder's own letter case; the scan compares lowercase
        folder = next((d for d in SESSIONS_DIR.iterdir() if p and d.name.lower() == encode_path(p["path"])), None) if SESSIONS_DIR.is_dir() else None
        f = folder / f"{session_id}.jsonl" if folder else None
    return summarize(f) if f is not None and f.is_file() else None


def _print(report: list) -> None:
    for r in report:
        print(f"\n=== {r['project'] or '(no project) ' + r['folder']}  {r['path'] or ''}")
        for s in r["sessions"]:
            print(f"- {s['id'][:8]}  {s['modified'].replace('T', ' ')}  {s['messages']} msgs")
            print(f"    asked:       {s['first']}")
            print(f"    last:        {s['last_user']}")
            print(f"    ended with:  {s['last_assistant']}")
            for t in s["open_todos"][:6]:
                print(f"    unfinished [{t['status']}]: {t['content']}")


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import chief  # only here, so the tests do not depend on projects.json

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default="", help="part of the project name")
    ap.add_argument("--limit", type=int, default=3, help="sessions per project")
    ap.add_argument("--since", type=int, default=0, help="only sessions from the last N days")
    ap.add_argument("--all", action="store_true", help="include folders without a project")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    report = scan(chief.load_projects(), a.project, a.limit, a.since, a.all)
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print(report or [])
        if not report:
            print("No sessions found.")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
