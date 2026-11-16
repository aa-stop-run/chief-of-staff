"""Requests suggested by workers.

A worker that finds follow-up work (for example a commit or a pull request after an analysis) can SUGGEST a request here. A
suggestion starts nothing: it is a card on the dashboard, with the name of the worker that made it, and only the user's click
turns it into a request (the new-request dialog opens already filled in). State in <root>/proposals.json.

    python scripts/proposals.py add --project P --kind ship --title "..." --desc "..." --by <session name>
"""
import hashlib
import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

import atomicio

KINDS = ("feature", "fix", "maintenance", "analysis", "ship")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
MAX_TITLE, MAX_DESC, MAX_BY = 140, 4000, 80
MAX_OPEN, MAX_OPEN_PER_WORKER = 50, 5          # a worker (or a loop) cannot flood the dashboard
COOLDOWN_DAYS, KEEP_DISMISSED_DAYS = 7, 30       # a dismissed suggestion cannot come straight back; old dismissed ones are forgotten
SECRET_RE = re.compile(
    r"(?i)(?:(?:api[_-]?key|secret|token|passw(?:or)?d|authorization)\s*[:=]\s*[^\n]*|\bbearer\s+\S+|[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)|\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b)")


class ProposalError(Exception):
    pass


def clean(text: str) -> str:
    """The suggestion's text with anything that looks like a credential removed (a worker must not carry secrets onto the dashboard)."""
    return SECRET_RE.sub("[removed]", text or "").strip()


class Proposals:
    def __init__(self, root):
        self.root = Path(root)
        self._lock = threading.RLock()

    @property
    def _file(self) -> Path:
        return self.root / "proposals.json"

    def _load(self, strict: bool = False) -> list:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001 - a damaged file shows no suggestions, and is never overwritten by an add
            if strict:
                raise ProposalError("the suggestions file is damaged: fix or remove it") from None
            return []
        return data if isinstance(data, list) else []

    def _save(self, items: list) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        cutoff = (datetime.now() - timedelta(days=KEEP_DISMISSED_DAYS)).isoformat(timespec="seconds")
        items = [i for i in items if not (i.get("dismissed") and (i.get("dismissed_at") or i.get("created", "")) < cutoff)]
        atomicio.write_text(self._file, json.dumps(items, ensure_ascii=False, indent=1))

    def list(self) -> list:
        with self._lock:
            return [i for i in self._load() if not i.get("dismissed")]

    def add(self, project: str, kind: str, title: str, desc: str, by: str = "") -> dict:
        project, title, desc, by = (project or "").strip(), clean(title), clean(desc), clean(by)[:MAX_BY]
        if not project:
            raise ProposalError("missing project")
        if kind not in KINDS:
            raise ProposalError("unknown kind (" + ", ".join(KINDS) + ")")
        if not title or len(title) > MAX_TITLE:
            raise ProposalError(f"title required (max. {MAX_TITLE} characters)")
        if not desc or len(desc) > MAX_DESC:
            raise ProposalError(f"description required (max. {MAX_DESC} characters)")
        pid = "s-" + hashlib.sha1(f"{project}|{kind}|{title}".encode("utf-8")).hexdigest()[:10]
        with self._lock, atomicio.locked(self._file):       # a worker's `proposals.py add` and the server both write this file
            items = self._load(strict=True)
            open_ = [i for i in items if not i.get("dismissed")]
            for i in open_:
                if i["id"] == pid:
                    return i                                     # the same suggestion twice is one card
            recent = (datetime.now() - timedelta(days=COOLDOWN_DAYS)).isoformat(timespec="seconds")
            if any(i["id"] == pid and (i.get("dismissed_at") or "") >= recent for i in items):
                raise ProposalError("this suggestion was dismissed recently")
            if len(open_) >= MAX_OPEN:
                raise ProposalError("too many open suggestions: dismiss some on the dashboard first")
            if sum(1 for i in open_ if (i.get("by") or "") == by) >= MAX_OPEN_PER_WORKER:      # a worker that gives no name shares one bucket
                raise ProposalError(f"this worker already has {MAX_OPEN_PER_WORKER} open suggestions")
            item = {"id": pid, "project": project, "kind": kind, "title": title, "desc": desc, "by": by,
                    "created": datetime.now().isoformat(timespec="seconds"), "dismissed": False}
            self._save(items + [item])
            return item

    def get(self, pid: str) -> dict:
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise ProposalError("invalid id")
        for i in self.list():
            if i["id"] == pid:
                return i
        raise ProposalError("suggestion not found")

    def dismiss(self, pid: str) -> dict:
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise ProposalError("invalid id")
        with self._lock, atomicio.locked(self._file):
            items = self._load()
            for i in items:
                if i["id"] == pid and not i.get("dismissed"):
                    i["dismissed"] = True
                    i["dismissed_at"] = datetime.now().isoformat(timespec="seconds")
                    self._save(items)
                    return i
        raise ProposalError("suggestion not found")


def main() -> None:
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import chief  # only here: validates the project name against the registry

    ap = argparse.ArgumentParser(description="Suggest a request to the user (a dashboard card; nothing starts until they accept).")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list")
    a = sp.add_parser("add")
    a.add_argument("--project", required=True)
    a.add_argument("--kind", required=True, choices=KINDS)
    a.add_argument("--title", required=True)
    a.add_argument("--desc", required=True)
    a.add_argument("--by", default="", help="your session name")
    args = ap.parse_args()
    store = Proposals(chief.STATE)
    try:
        if args.cmd == "list":
            for i in store.list():
                print(f"{i['id']}  {i['kind']:11} {i['project']:22} {i['title']}")
        else:
            if args.project not in {p["name"] for p in chief.load_projects()}:
                raise ProposalError(f"project “{args.project}” does not exist (see: python scripts/chief.py projects)")
            i = store.add(args.project, args.kind, args.title, args.desc, args.by)
            print("suggested:", i["id"], "-", i["title"], "(the user decides on the dashboard)")
    except ProposalError as e:
        raise SystemExit(f"error: {e}") from e


if __name__ == "__main__":
    main()
