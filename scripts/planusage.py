"""Plan usage: how much of a Claude subscription's allowance is used, and which workers a usage limit has stopped.

A Claude Pro/Max/Ultra plan has a 5-hour allowance and a 7-day one, shared by everything you do with Claude (claude.ai, Claude Code,
the desktop app). Claude Code hands the two percentages (and when each window resets) to its *status line* command, and nowhere
else that a script may read. So the Chief offers a status line command (`chief.py statusline`) that you opt into: it saves those
numbers here and the dashboard shows them. The Chief never reads your credentials and never calls Anthropic for this.

The other half is what a limit does to a worker. Claude Code writes a notice into the session log when it hits one ("Usage limit
reached · continuing automatically at 1:30pm"); `tail_notice` finds the latest one, so the dashboard can say that a worker is paused,
until when, and whether Claude Code will continue it by itself.
"""
import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

STATUS_FILE = "plan_usage.json"
WARN_AT, HIGH_AT, LIMIT_AT = 70.0, 90.0, 100.0
WINDOWS = ("five_hour", "seven_day")
TAIL_BYTES = 262_144           # the notice is always near the end of a log: only the last quarter of a megabyte is read

_NOTICE = re.compile(r"Usage limit reached|You've hit your ([\w-]+(?: [\w-]+)?) limit", re.I)
_MODELS = ("opus", "sonnet", "fable", "mythos", "haiku")
_TIME = re.compile(r"(?:continuing automatically at|resets)\s+(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)", re.I)
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _local(ts: str):
    """A log timestamp ('2026-11-06T11:29:01.822Z', UTC) as a naive local datetime."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


# ---------------------------------------------------------------- the percentages (from the status line)
def record_statusline(payload, path, now: datetime | None = None):
    """Save the plan windows from a status line payload. Returns what was saved, or None when the payload has no `rate_limits`
    (not a subscription, or before the first response): the last good numbers are then kept. Only percentages and reset times
    are stored; nothing else from the payload."""
    rl = payload.get("rate_limits") if isinstance(payload, dict) else None
    if not isinstance(rl, dict):
        return None
    now = now or datetime.now()
    out = {"updated": _iso(now)}
    for key in WINDOWS:
        w = rl.get(key)
        if isinstance(w, dict) and isinstance(w.get("used_percentage"), (int, float)):
            resets = w.get("resets_at")
            out[key] = {"used_percentage": float(w["used_percentage"]), "resets_at": int(resets) if isinstance(resets, (int, float)) else None}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    tmp.replace(path)
    return out


def _level(pct: float) -> str:
    return "limit" if pct >= LIMIT_AT else "high" if pct >= HIGH_AT else "warn" if pct >= WARN_AT else "ok"


def view(path, now: datetime | None = None) -> dict:
    """What the dashboard shows: {connected, age_s, windows: {five_hour|seven_day: None | {percent, level, resets_in_s, expired}}}.

    A window whose reset time has passed is shown as 0 % and `expired`: Claude Code drops it, and a new one only starts with the
    next message, so its old percentage would be wrong."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(data["updated"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"connected": False, "age_s": None, "windows": {k: None for k in WINDOWS}}
    now = now or datetime.now()
    ts = now.timestamp()
    wins = {}
    for key in WINDOWS:
        w = data.get(key)
        if not isinstance(w, dict):
            wins[key] = None
            continue
        resets = w.get("resets_at")
        expired = resets is not None and resets <= ts
        pct = 0.0 if expired else float(w["used_percentage"])
        wins[key] = {"percent": round(pct, 1), "level": _level(pct), "expired": bool(expired),
                     "resets_in_s": 0 if (expired or resets is None) else int(resets - ts)}
    return {"connected": True, "age_s": max(0, int((now - updated).total_seconds())), "windows": wins}


def statusline_text(v: dict) -> str:
    """The compact text the status line command prints: '5h 23% · 7d 41%'."""
    parts = [f"{label} {w['percent']:.0f}%" for key, label in (("five_hour", "5h"), ("seven_day", "7d")) if (w := v["windows"].get(key))]
    return " · ".join(parts)


# ---------------------------------------------------------------- a worker stopped by a limit (from its session log)
def parse_notice(text: str, at: datetime):
    """Read a limit notice: {kind, auto, resume_at, text}, or None if the text is not one. `resume_at` is a local datetime
    (naive) worked out from the clock time in the message and the moment it was written; None if the message has no time."""
    m = _NOTICE.search(text or "")
    if not m:
        return None
    low = text.lower()
    name = (m.group(1) or "").lower()
    kind = "weekly" if "weekly" in low or "7-day" in name or "week" in name else "model" if name.split(" ")[0] in _MODELS else "session"
    resume = None
    t = _TIME.search(text)
    if t and at:
        day, hour, minute, ampm = t.groups()
        h = int(hour) % 12 + (12 if ampm.lower() == "pm" else 0)
        resume = at.replace(hour=h, minute=int(minute or 0), second=0, microsecond=0)
        if day:
            ahead = (_DAYS.index(day[:3].lower()) - at.weekday()) % 7
            resume += timedelta(days=ahead)
            if resume <= at:
                resume += timedelta(days=7)
        elif resume <= at:
            resume += timedelta(days=1)
    return {"kind": kind, "auto": "continuing automatically" in low, "resume_at": _iso(resume) if resume else None, "text": text.strip()[:200]}


def _notice_text(o: dict):
    """The text of a log entry that is a limit notice, or None. Two shapes: a system informational line with string `content`, and
    (what Claude Code writes for the 5-hour/7-day limits) an assistant entry flagged as an API error whose message.content is the
    text. A normal assistant message that merely quotes a notice is not one."""
    if o.get("type") == "system":
        content = o.get("content")
        return content if isinstance(content, str) and _NOTICE.search(content) else None
    if o.get("type") == "assistant" and (o.get("isApiErrorMessage") is True or o.get("apiError") == "usage_limit_reached"
                                         or o.get("error") == "rate_limit"):
        content = (o.get("message") or {}).get("content")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        return content if isinstance(content, str) and _NOTICE.search(content) else None
    return None


def tail_notice(path):
    """The latest limit notice in a session log that the session has not moved on from, or None.

    {at, kind, auto, resume_at, text}. A notice is left behind once anything is said after it (the session continued)."""
    try:
        size = Path(path).stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = chunk.splitlines()[1:] if size > TAIL_BYTES else chunk.splitlines()
    found = None
    for line in reversed(lines):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        at = _local(o.get("timestamp"))
        if found is None:
            content = _notice_text(o)
            if content and at:
                n = parse_notice(content, at)
                if n:
                    found = {**n, "at": _iso(at)}
            continue
        # older than the notice, nothing more to learn
        break
    if found is None:
        return None
    notice_at = datetime.fromisoformat(found["at"])
    for line in lines:                                   # did the session speak after the notice? then it has continued
        try:
            o = json.loads(line)
        except ValueError:
            continue
        at = _local(o.get("timestamp"))
        if at and at > notice_at + timedelta(seconds=2) and o.get("type") in ("assistant", "user") and not o.get("isMeta"):
            return None
    return found


class NoticeCache:
    """tail_notice with a cache keyed on the file's size and modification time, so refreshing the dashboard is cheap."""

    def __init__(self):
        self._seen = {}
        self._lock = threading.Lock()

    def notice(self, path):
        if path is None:
            return None
        try:
            st = Path(path).stat()
        except OSError:
            return None
        key = (st.st_size, st.st_mtime_ns)
        with self._lock:
            hit = self._seen.get(str(path))
            if hit and hit[0] == key:
                return hit[1]
        n = tail_notice(path)
        with self._lock:
            self._seen[str(path)] = (key, n)
        return n


def resumes_in(notice: dict, now: datetime | None = None):
    """Seconds until a limit notice's reset (never negative), or None if the notice has no time."""
    if not notice or not notice.get("resume_at"):
        return None
    return max(0, int((datetime.fromisoformat(notice["resume_at"]) - (now or datetime.now())).total_seconds()))
