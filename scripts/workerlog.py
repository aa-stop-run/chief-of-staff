"""What a worker said last.

Every worker ends with a message that starts with [DONE], [BLOCKED] or [QUESTION] (the preamble asks for it). When a request
fails, that message is the reason, and it is already in the worker's Claude Code log: the dashboard shows it on the card instead
of only «the worker stopped without the result». Only the end of the log is read, the text is cut short and anything that looks
like a credential is removed.
"""
import json
import re
import threading
from pathlib import Path

TAIL_BYTES = 262_144
MAX_TEXT = 700
MARK_RE = re.compile(r"\[(DONE|BLOCKED|QUESTION)\]")
SECRET_RE = re.compile(
    r"(?i)(?:(?:api[_-]?key|secret|token|passw(?:or)?d|authorization)\s*[:=]\s*\S+|\bbearer\s+\S+|[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)|\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b)")


def clean(text: str) -> str:
    """One paragraph, without credentials, at most MAX_TEXT characters."""
    text = SECRET_RE.sub("[removed]", " ".join(str(text or "").split()))
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1].rstrip() + "…"


def _texts(entry: dict) -> str:
    if entry.get("type") != "assistant" or entry.get("isApiErrorMessage") is True:
        return ""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def last_message(path) -> dict | None:
    """{mark, text} of the last thing the worker said in its log: `mark` is DONE, BLOCKED, QUESTION or '' (it said something
    else, usually because it was cut off). None when the log cannot be read or the worker never spoke."""
    try:
        size = Path(path).stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = chunk.splitlines()[1:] if size > TAIL_BYTES else chunk.splitlines()
    for line in reversed(lines):
        try:
            text = _texts(json.loads(line)).strip()
        except (ValueError, AttributeError):
            continue
        if text:
            m = MARK_RE.search(text)
            return {"mark": m.group(1) if m else "", "text": clean(text[m.start():] if m else text)}
    return None


class LastMessages:
    """`last_message` cached on the log's size and modification time, so drawing the dashboard does not reread logs."""

    def __init__(self):
        self._seen, self._lock = {}, threading.Lock()

    def get(self, path):
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
        value = last_message(path)
        with self._lock:
            self._seen[str(path)] = (key, value)
        return value
