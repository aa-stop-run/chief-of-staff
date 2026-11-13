"""What the workers cost: tokens and an estimated price per worker, model, project, request and work session.

Claude Code writes every session's conversation to ~/.claude/projects/<folder>/<session>.jsonl, and each assistant message carries
the model and its token usage (input, output, cache writes and reads). It records no dollar amount for background sessions, and
`claude agents --json` has none either. So, like Claude Code's own `/usage` and status line, the cost is computed here from the
tokens at **list price**: an estimate, never a bill (subscriptions, discounts, batch and contracted rates are not known).

Prices are USD per million tokens. `DEFAULT_PRICES` is Anthropic's published list (see PRICES_SOURCE, as of PRICES_AS_OF);
`prices` in projects.json overrides or extends it per model, which is how gateway or third-party models get a price. A model with
no price is still counted in tokens and is reported as unpriced, never silently as free.

The ledger (<state>/usage.json) stores what was measured per worker, so the numbers survive Claude Code deleting old transcripts.
"""
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

PRICES_AS_OF = "2026-11-08"
PRICES_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
LONG_PROMPT_TOKENS = 100_000          # Haiku 5.5 charges more for a request whose prompt is longer than this
US_MULTIPLIER = 1.1                   # `inference_geo: "us"` on Claude 4.6 and later
FAST_MULTIPLIER = 2.0                 # fast mode on Opus 5.5, Opus 5 and Opus 4.8 (published as exactly double)

# (input, output, cache write 5 min, cache write 1 h, cache read), USD per million tokens
DEFAULT_PRICES = {
    "fable-5-1": (10, 50, 12.5, 20, 0.25), "mythos-5-1": (10, 50, 12.5, 20, 0.25),
    "fable-5": (10, 50, 12.5, 20, 1), "mythos-5": (10, 50, 12.5, 20, 1),
    "opus-5-5": (4, 20, 5, 8, 0.20),
    "opus-5": (5, 25, 6.25, 10, 0.5), "opus-4-8": (5, 25, 6.25, 10, 0.5), "opus-4-7": (5, 25, 6.25, 10, 0.5),
    "opus-4-6": (5, 25, 6.25, 10, 0.5), "opus-4-5": (5, 25, 6.25, 10, 0.5),
    "opus-4-1": (15, 75, 18.75, 30, 1.5), "opus-4": (15, 75, 18.75, 30, 1.5),
    "sonnet-5-5": (2, 10, 2.5, 4, 0.10), "sonnet-5": (2, 10, 2.5, 4, 0.20),
    "sonnet-4-6": (3, 15, 3.75, 6, 0.30), "sonnet-4-5": (3, 15, 3.75, 6, 0.30), "sonnet-4": (3, 15, 3.75, 6, 0.30),
    "haiku-5-5": (0.10, 0.50, 0.125, 0.20, 0.01), "haiku-4-5": (1, 5, 1.25, 2, 0.10),
}
HAIKU_5_5_LONG = (0.50, 2.50, 0.625, 1, 0.05)
FAST_MODELS = ("opus-5-5", "opus-5", "opus-4-8")
NO_GEO_MODELS = ("opus-4-5", "opus-4-1", "opus-4", "sonnet-4-5", "sonnet-4", "haiku-4-5")      # older than 4.6: no `inference_geo`
PRICE_FIELDS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")
TOKEN_FIELDS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")

_REQUEST_IN_TASK = re.compile(r"\(id ([a-z0-9][a-z0-9-]{0,80})\)")      # every pipeline prompt says "(id <request id>)"
_MODEL_RE = re.compile(r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:[-.](\d{1,2}))?(?!\d)")


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def model_key(name: str) -> str:
    """'claude-sonnet-4-5-20250929' -> 'sonnet-4-5'; provider prefixes and date suffixes are ignored. '' if not recognised."""
    m = _MODEL_RE.search(str(name or "").lower())
    if not m:
        return ""
    fam, major, minor = m.groups()
    return f"{fam}-{major}" + (f"-{minor}" if minor else "")


def _as_tuple(p) -> tuple:
    return tuple(float(p[f]) for f in PRICE_FIELDS) if isinstance(p, dict) else tuple(float(x) for x in p)


def prices_for(model: str, overrides: dict | None = None, prompt_tokens: int = 0):
    """The five prices for `model` (a tuple), or None. `overrides` (projects.json `prices`) may be keyed by the exact model
    name or by its key, each a dict with input, output, cache_write_5m, cache_write_1h and cache_read."""
    overrides = overrides or {}
    key = model_key(model)
    for k in (model, key):
        if k and isinstance(overrides.get(k), dict) and all(f in overrides[k] for f in PRICE_FIELDS):
            return _as_tuple(overrides[k])
    if key == "haiku-5-5" and prompt_tokens > LONG_PROMPT_TOKENS:
        return HAIKU_5_5_LONG
    return DEFAULT_PRICES.get(key)


def message_cost(model: str, usage: dict, overrides: dict | None = None):
    """Estimated USD for one assistant message, or None if the model has no price."""
    inp, out, read = (int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"))
    cc = usage.get("cache_creation") or {}
    w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:                 # older logs: one number, written at the 5-minute rate
        w5, w1 = int(usage.get("cache_creation_input_tokens") or 0), 0
    w5, w1 = int(w5 or 0), int(w1 or 0)
    p = prices_for(model, overrides, inp + read + w5 + w1)
    if p is None:
        return None
    cost = (inp * p[0] + out * p[1] + w5 * p[2] + w1 * p[3] + read * p[4]) / 1_000_000
    key = model_key(model)
    if usage.get("speed") == "fast" and key in FAST_MODELS:
        cost *= FAST_MULTIPLIER
    if usage.get("inference_geo") == "us" and key and key not in NO_GEO_MODELS:
        cost *= US_MULTIPLIER
    return cost


def request_from_task(task: str) -> str:
    """The request a worker was launched for, read from its task text. Workers launched before they were tagged have only this."""
    m = _REQUEST_IN_TASK.search(task or "")
    return m.group(1) if m else ""


def prices_signature(overrides: dict | None) -> str:
    """Changes when the prices in use change, so stored costs are recomputed (while the transcript still exists)."""
    blob = json.dumps(["hourly-v1", PRICES_AS_OF, DEFAULT_PRICES, HAIKU_5_5_LONG, overrides or {}], sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------- reading the transcripts
def find_transcript(worker_id: str, cwd: str = ""):
    """The log of a worker session: <claude dir>/projects/<folder>/<id...>.jsonl (the id is the start of the session id)."""
    if not re.fullmatch(r"[0-9a-f-]{6,40}", worker_id or ""):
        return None
    base = claude_dir() / "projects"
    if cwd:
        folder = base / re.sub(r"[^A-Za-z0-9]", "-", str(cwd)).lower()
        hits = sorted(folder.glob(f"{worker_id}*.jsonl")) if folder.is_dir() else []
        if hits:
            return hits[0]
    hits = sorted(base.glob(f"*/{worker_id}*.jsonl")) if base.is_dir() else []
    return hits[0] if hits else None


def read_messages(path: Path) -> list:
    """The assistant messages of a transcript as [{model, usage, at}], each message once.

    Claude Code logs a streamed message several times as it grows; the last copy (the one with the most output tokens) is the
    real one. Synthetic entries (no model call) are skipped."""
    best = {}
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for i, line in enumerate(lines):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        m = o.get("message") if o.get("type") == "assistant" else None
        if not isinstance(m, dict) or not isinstance(m.get("usage"), dict) or not m.get("model") or str(m["model"]).startswith("<"):
            continue
        key = (m.get("id") or o.get("uuid") or f"line-{i}", o.get("requestId") or "")
        if key not in best or int(m["usage"].get("output_tokens") or 0) >= int(best[key]["usage"].get("output_tokens") or 0):
            best[key] = {"model": m["model"], "usage": m["usage"], "at": o.get("timestamp") or ""}
    return list(best.values())


def summarize(path: Path, overrides: dict | None = None) -> dict:
    """Tokens and estimated cost per model for one transcript: {model: {messages, input, ..., cost_usd, unpriced_messages}}."""
    models = {}
    hourly = {}                      # estimated cost per UTC hour ("2026-11-08T10"), for the rolling 5-hour and 7-day totals
    first = last = ""
    for msg in read_messages(path):
        u, model = msg["usage"], msg["model"]
        cc = u.get("cache_creation") or {}
        w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
        if w5 is None and w1 is None:
            w5, w1 = u.get("cache_creation_input_tokens") or 0, 0
        row = models.setdefault(model, {"messages": 0, **{f: 0 for f in TOKEN_FIELDS}, "cost_usd": 0.0, "unpriced_messages": 0})
        row["messages"] += 1
        row["input"] += int(u.get("input_tokens") or 0)
        row["output"] += int(u.get("output_tokens") or 0)
        row["cache_write_5m"] += int(w5 or 0)
        row["cache_write_1h"] += int(w1 or 0)
        row["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        c = message_cost(model, u, overrides)
        if c is None:
            row["unpriced_messages"] += 1
        else:
            row["cost_usd"] = round(row["cost_usd"] + c, 6)
            if msg["at"]:
                hour = msg["at"][:13]
                hourly[hour] = round(hourly.get(hour, 0.0) + c, 6)
        if msg["at"]:
            first, last = min(first or msg["at"], msg["at"]), max(last, msg["at"])
    return {"models": models, "first_at": first, "last_at": last, "hourly": hourly}


# ---------------------------------------------------------------- the ledger
class Ledger:
    """<state>/usage.json: what each worker cost, kept even after Claude Code removes the transcript."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data["workers"] if isinstance(data, dict) and isinstance(data.get("workers"), dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, workers: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "workers": workers}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def refresh(self, workers: list, overrides: dict | None = None) -> int:
        """Measure every worker whose transcript is new or changed (or priced with other prices). Returns how many were updated."""
        sig = prices_signature(overrides)
        changed = 0
        with self._lock:
            ledger = self.load()
            for w in workers:
                path = find_transcript(w.get("id", ""), w.get("cwd", ""))
                if path is None:
                    continue
                st = path.stat()
                old = ledger.get(w["id"])
                request = w.get("request") or request_from_task(w.get("task", ""))
                same = old and old.get("size") == st.st_size and old.get("mtime") == st.st_mtime_ns and old.get("sig") == sig
                if same and old.get("session") == w.get("session") and old.get("request") == request:
                    continue
                s = old if same else {**summarize(path, overrides), "size": st.st_size, "mtime": st.st_mtime_ns, "sig": sig}
                ledger[w["id"]] = {**s, "project": w.get("project", ""), "session": w.get("session", ""), "request": request,
                                   "phase": w.get("phase", ""), "name": w.get("name", "")}
                changed += 1
            if changed:
                self._save(ledger)
        return changed

    def put(self, worker_id: str, entry: dict) -> None:
        with self._lock:
            ledger = self.load()
            ledger[worker_id] = entry
            self._save(ledger)


# ---------------------------------------------------------------- totals
def _blank() -> dict:
    return {"cost_usd": 0.0, "workers": 0, "unpriced_tokens": 0, **{f: 0 for f in TOKEN_FIELDS}}


def _add(acc: dict, row: dict) -> None:
    for f in TOKEN_FIELDS:
        acc[f] += row.get(f, 0)
    acc["cost_usd"] = round(acc["cost_usd"] + row.get("cost_usd", 0.0), 6)
    if row.get("unpriced_messages"):
        acc["unpriced_tokens"] += sum(row.get(f, 0) for f in TOKEN_FIELDS)


def spend_in_window(ledger: dict, start: datetime, end: datetime) -> float:
    """Estimated cost of the workers' messages between two moments (timezone-aware datetimes). The ledger keeps one figure per UTC
    hour, so an hour only partly inside the window counts for the part that is."""
    total = 0.0
    for entry in ledger.values():
        for hour, cost in (entry.get("hourly") or {}).items():
            try:
                b = datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            lo, hi = max(b, start), min(b + timedelta(hours=1), end)
            if hi > lo:
                total += cost * (hi - lo).total_seconds() / 3600
    return round(total, 6)


def request_costs(ledger: dict) -> dict:
    """What each request cost in all, whatever the work session or the phase: {request: {project, cost_usd, workers, sessions,
    unpriced, tokens, by_phase: {phase: {cost_usd, workers}}, by_model: {model: {cost_usd, unpriced}}}}.

    A request runs many workers (the spec, the plan, its validation, the implementation, the review, every fix round), and may
    stretch over several work sessions: it is the request that is totalled, not the session."""
    out = {}
    for entry in ledger.values():
        req = entry.get("request") or ""
        if not req:
            continue
        r = out.setdefault(req, {"project": entry.get("project") or "", "cost_usd": 0.0, "workers": 0, "unpriced": False,
                                 "tokens": {f: 0 for f in TOKEN_FIELDS}, "by_phase": {}, "by_model": {}, "_sessions": set()})
        r["workers"] += 1
        r["_sessions"].add(entry.get("session") or "")
        ph = r["by_phase"].setdefault(entry.get("phase") or "other", {"cost_usd": 0.0, "workers": 0})
        ph["workers"] += 1
        for model, row in (entry.get("models") or {}).items():
            cost = row.get("cost_usd", 0.0)
            r["cost_usd"] = round(r["cost_usd"] + cost, 6)
            ph["cost_usd"] = round(ph["cost_usd"] + cost, 6)
            for f in TOKEN_FIELDS:
                r["tokens"][f] += row.get(f, 0)
            m = r["by_model"].setdefault(model, {"cost_usd": 0.0, "unpriced": False})
            m["cost_usd"] = round(m["cost_usd"] + cost, 6)
            if row.get("unpriced_messages"):
                m["unpriced"] = r["unpriced"] = True
    for r in out.values():
        r["sessions"] = len(r.pop("_sessions"))
    return out


def aggregate(ledger: dict, session: str | None = None, project: str | None = None) -> dict:
    """Totals over the ledger, optionally for one work session and/or one project, with breakdowns by model, project and request."""
    total = _blank()
    by_model, by_project, by_request = {}, {}, {}
    unpriced = set()
    for entry in ledger.values():
        if session is not None and entry.get("session") != session:
            continue
        if project is not None and entry.get("project") != project:
            continue
        total["workers"] += 1
        proj, req = entry.get("project") or "", entry.get("request") or ""
        by_project.setdefault(proj, _blank())["workers"] += 1
        if req:
            by_request.setdefault(req, {**_blank(), "project": proj})["workers"] += 1
        for model, row in (entry.get("models") or {}).items():
            _add(total, row)
            _add(by_model.setdefault(model, _blank()), row)
            _add(by_project[proj], row)
            if req:
                _add(by_request[req], row)
            if row.get("unpriced_messages"):
                unpriced.add(model)
    return {"cost_usd": total["cost_usd"], "tokens": {f: total[f] for f in TOKEN_FIELDS}, "workers": total["workers"],
            "unpriced_tokens": total["unpriced_tokens"], "unpriced_models": sorted(unpriced),
            "by_model": by_model, "by_project": by_project, "by_request": by_request}
