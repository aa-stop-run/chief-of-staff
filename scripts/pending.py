"""Session leftovers: tasks that were left unfinished in a project, shown on the dashboard.

They do not launch workers: they are only cards with the state the session was left in. State in <root>/pending.json.
"""
import hashlib
import json
import re
import threading
from datetime import datetime
from pathlib import Path

import atomicio

STATES = ("awaiting", "blocked", "running", "optional")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
MAX_TITLE, MAX_TEXT = 140, 1200


class PendingError(Exception):
    pass


class Pending:
    def __init__(self, root):
        self.root = Path(root)
        self._lock = threading.RLock()

    @property
    def _file(self) -> Path:
        return self.root / "pending.json"

    def _load(self) -> list:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a missing or corrupt file means no items yet
            return []

    def _save(self, items: list) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        atomicio.write_text(self._file, json.dumps(items, ensure_ascii=False, indent=1))

    def list(self) -> list:
        with self._lock:
            return [i for i in self._load() if not i.get("dismissed")]

    def add(self, project: str, title: str, status: str, summary: str, missing: str, session: str, date: str) -> dict:
        project, title = (project or "").strip(), (title or "").strip()
        summary, missing = (summary or "").strip(), (missing or "").strip()
        if not project:
            raise PendingError("missing project")
        if not title or len(title) > MAX_TITLE:
            raise PendingError(f"title required (max. {MAX_TITLE} characters)")
        if status not in STATES:
            raise PendingError("unknown state")
        if len(summary) > MAX_TEXT or len(missing) > MAX_TEXT:
            raise PendingError(f"text too long (max. {MAX_TEXT} characters)")
        pid = "p-" + hashlib.sha1(f"{project}|{session}|{title}".encode("utf-8")).hexdigest()[:10]
        with self._lock:
            items = self._load()
            for i in items:
                if i["id"] == pid:
                    return i
            item = {"id": pid, "project": project, "title": title, "status": status, "summary": summary,
                    "missing": missing, "session": session, "date": date,
                    "created": datetime.now().isoformat(timespec="seconds"), "dismissed": False}
            self._save(items + [item])
            return item

    def get(self, pid: str) -> dict:
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise PendingError("invalid id")
        for i in self.list():
            if i["id"] == pid:
                return i
        raise PendingError("pending item not found")

    def dismiss(self, pid: str) -> dict:
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise PendingError("invalid id")
        with self._lock:
            items = self._load()
            for i in items:
                if i["id"] == pid:
                    i["dismissed"] = True
                    self._save(items)
                    return i
        raise PendingError("pending item not found")


def main() -> None:
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import chief  # only here: validates the project name against the registry

    ap = argparse.ArgumentParser(description="Session leftovers (dashboard cards).")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list")
    d = sp.add_parser("dismiss")
    d.add_argument("id")
    a = sp.add_parser("add")
    a.add_argument("--project", required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--status", required=True, choices=STATES)
    a.add_argument("--summary", default="")
    a.add_argument("--missing", default="")
    a.add_argument("--session", default="", help="full session id (for “Resume”)")
    a.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    store = Pending(chief.STATE)
    try:
        if args.cmd == "list":
            for i in store.list():
                print(f"{i['id']}  {i['status']:9} {i['project']:22} {i['title']}")
        elif args.cmd == "dismiss":
            print("dismissed:", store.dismiss(args.id)["title"])
        else:
            if args.project not in {p["name"] for p in chief.load_projects()}:
                raise PendingError(f"project “{args.project}” does not exist (see: python scripts/chief.py projects)")
            c = store.add(args.project, args.title, args.status, args.summary, args.missing, args.session, args.date)
            print("created:", c["id"], c["title"])
    except PendingError as e:
        raise SystemExit(f"error: {e}") from e


if __name__ == "__main__":
    main()
