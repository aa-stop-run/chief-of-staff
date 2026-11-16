"""Work sessions: the span of time you spend working with the Chief, with a start and an end.

A work session is the unit the dashboard's history is about: the requests finished and the workers launched *in this session* are
what "Done" and the workers' history show, and the time and the AI cost are recorded per session. (It is not a Claude Code
session: those are the individual conversations, and the Chief's "session to-dos" are about them.)

How a session starts and ends:
  - the dashboard starts one when it starts, if none is open;
  - you end it with "End session": the totals are frozen into the record and a fresh session starts at once;
  - a session whose server has been gone for longer than STALE_AFTER_S (say, overnight) is closed at its last heartbeat.

Time is *active* time: every heartbeat adds the time since the previous one, but never more than HEARTBEAT_MAX_S, so the hours the
server was not running are not counted. State lives in <state>/work_sessions.json.
"""
import json
import threading
from datetime import datetime
from pathlib import Path

import atomicio

HEARTBEAT_MAX_S = 60            # a longer gap between two heartbeats means the server was down: that time is not counted
STALE_AFTER_S = 12 * 3600       # a session whose server has been gone this long is closed at its last heartbeat
LEGACY_ID = "ws-legacy"         # everything recorded before sessions existed


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


class WorkSessions:
    def __init__(self, path, now=datetime.now):
        self.path = Path(path)
        self._now = now
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- storage
    def _load(self) -> list:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data["sessions"] if isinstance(data, dict) and isinstance(data.get("sessions"), list) else []
        except (OSError, ValueError):
            return []

    def _save(self, sessions: list) -> None:
        atomicio.write_text(self.path, json.dumps({"sessions": sessions}, ensure_ascii=False, indent=1))

    @staticmethod
    def _open(sessions: list):
        return next((s for s in reversed(sessions) if not s.get("ended")), None)

    # ---------------------------------------------------------------- reading
    def current(self):
        with self._lock:
            return self._open(self._load())

    def current_id(self) -> str:
        c = self.current()
        return c["id"] if c else ""

    def get(self, sid: str):
        with self._lock:
            return next((s for s in self._load() if s["id"] == sid), None)

    def list(self) -> list:
        """Every session, newest first."""
        with self._lock:
            return sorted(self._load(), key=lambda s: s.get("started", ""), reverse=True)

    # ---------------------------------------------------------------- the life of a session
    def _new(self, sessions: list) -> dict:
        now = self._now()
        base = f"ws-{now:%Y%m%d-%H%M%S}"
        sid, n = base, 1
        taken = {s["id"] for s in sessions}
        while sid in taken:
            n += 1
            sid = f"{base}-{n}"
        s = {"id": sid, "started": _iso(now), "last_seen": _iso(now), "ended": None, "ended_by": None, "active_s": 0, "totals": None}
        sessions.append(s)
        return s

    def ensure(self) -> dict:
        """Called when the server starts: close a stale session, and open one if none is open. Returns the open session."""
        with self._lock, atomicio.locked(self.path):   # the server's heartbeat and `chief.py session end` both write
            sessions = self._load()
            cur = self._open(sessions)
            if cur and (self._now() - datetime.fromisoformat(cur["last_seen"])).total_seconds() > STALE_AFTER_S:
                cur.update(ended=cur["last_seen"], ended_by="idle")
                cur = None
            if cur is None:
                cur = self._new(sessions)
            self._save(sessions)
            return cur

    def heartbeat(self):
        """Count the time since the last heartbeat (at most HEARTBEAT_MAX_S) as active time. Returns the open session."""
        with self._lock, atomicio.locked(self.path):   # the server's heartbeat and `chief.py session end` both write
            sessions = self._load()
            cur = self._open(sessions)
            if cur is None:
                return None
            now = self._now()
            gap = (now - datetime.fromisoformat(cur["last_seen"])).total_seconds()
            cur["active_s"] = round(cur.get("active_s", 0) + max(0.0, min(gap, HEARTBEAT_MAX_S)), 1)
            cur["last_seen"] = _iso(now)
            self._save(sessions)
            return cur

    def end(self, totals=None, by: str = "user") -> dict:
        """Close the open session, freeze its totals into the record, and open a fresh one. Returns {ended, current}."""
        with self._lock:
            self.heartbeat()
        with self._lock, atomicio.locked(self.path):
            sessions = self._load()
            cur = self._open(sessions)
            if cur is None:
                raise ValueError("no session is open")
            cur.update(ended=_iso(self._now()), ended_by=by, totals=totals)
            fresh = self._new(sessions)
            self._save(sessions)
            return {"ended": cur, "current": fresh}

    def add_legacy(self, started: str, ended: str) -> dict:
        """The placeholder session that owns everything recorded before sessions existed (used by the data migration)."""
        with self._lock, atomicio.locked(self.path):   # the server's heartbeat and `chief.py session end` both write
            sessions = self._load()
            old = next((s for s in sessions if s["id"] == LEGACY_ID), None)
            if old:
                return old
            s = {"id": LEGACY_ID, "started": started, "last_seen": ended, "ended": ended, "ended_by": "migration", "active_s": 0,
                 "totals": None, "legacy": True}
            sessions.insert(0, s)
            self._save(sessions)
            return s

    def active_seconds(self, session: dict) -> float:
        """Active time of a session, including what has passed since its last heartbeat (up to the heartbeat limit)."""
        base = session.get("active_s", 0)
        if session.get("ended"):
            return base
        gap = (self._now() - datetime.fromisoformat(session["last_seen"])).total_seconds()
        return base + max(0.0, min(gap, HEARTBEAT_MAX_S))
