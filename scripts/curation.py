"""Automatic vault curation: promotes the inbox drafts without the user clicking anything.

Flow (everything reversible with git): vault checkpoint -> background curator worker (working inside the vault itself, to
load its rules) -> commit of the result. State is kept in <state>/curation.json.
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import atomicio

QUIET_S = 600            # the inbox must be "calm" (no new draft) for 10 min: a worker may still be writing
GAP_S = 1800             # minimum interval between curations
TIMEOUT_S = 2 * 3600     # a stuck curator is abandoned
START_GRACE_S = 90       # right after launch the worker is not yet in claude's list: do not treat it as finished
MAX_ATTEMPTS = 3         # consecutive failures before the automation stops (the "Curate now" button keeps working)
LIMIT_GRACE_S = 180      # after a usage limit resets, wait this long before curating again (same size as features.LIMIT_GRACE_S)
LIMIT_DEFAULT_PAUSE_S = 1800   # a limit notice with no readable reset time: pause this long rather than hammering
BUSY = ("working", "running")
WAITING = ("blocked", "idle", "waiting")      # the session is alive but waiting for input (a permission or trust prompt)
BLOCK_RETRY_S = 600      # a vault Claude Code does not trust cannot be curated: try again every 10 min, without counting failures
TRUST_MARK = "not trusted"   # what `claude --bg --add-dir <folder>` says about a folder the user has not accepted yet

# Vault conventions the prompt refers to; the Chief passes its own (chief.SCHEMA) so that `vault_language` decides them.
DEFAULT_SCHEMA = {"readme": "README.md", "review_dir": "_review", "hub_dirs": ("Projects", "Areas"), "name": "English"}


class CurationError(Exception):
    pass


def curator_prompt(vault, drafts, schema=None) -> str:
    sc = {**DEFAULT_SCHEMA, **(schema or {})}
    v = str(vault).replace("\\", "/")
    names = "\n".join(f"- {d.name}" for d in drafts)
    hubs = " or ".join(f"{d}/" for d in sc["hub_dirs"])
    return f"""You are the curator of the knowledge vault at {v}. The user authorised you, in this session and only in it, to promote inbox
drafts into the vault and to edit existing notes. This is your only task: ignore the "chief" rules of the CLAUDE.md in the folder
where you were started. BEFORE STARTING read {v}/CLAUDE.md (the vault's rules are mandatory: frontmatter, unique names, wikilinks,
index) and {v}/wiki/hot.md. Write the notes in {sc["name"]}, as the vault's own rules say.

Drafts to process (in {v}/inbox):
{names}

For each draft:
1. Read it (format in inbox/{sc["readme"]}) and the note of the project it refers to.
2. Promote the knowledge: add to the project's hub or detail notes ({hubs}) what is state, decision or architecture, and create
   or update notes in wiki/entities, wiki/concepts or wiki/sources, following the vault's schema and linked to the hub.
   Knowledge used by several projects goes into the shared note (create it if it does not exist).
3. Only delete the draft when ALL its useful content has been promoted. If in doubt (unknown project, contradictory
   information, an existing note you cannot update without losing information), move it to inbox/{sc["review_dir"]}/ with a line
   at the top saying why, and do not delete it.

Rules: never write passwords, tokens, keys, private IPs, private URLs or e-mails (omit them and, if relevant, write
"contains secrets: omitted"); do not invent: anything unconfirmed gets "(to be confirmed)"; do not change anything outside {v};
do not make commits (the Chief commits at the end, so that you can be reverted); edit only what is needed, without rewriting whole notes.

At the end: update wiki/index.md (new notes), wiki/hot.md (Recent Changes) and add an entry at the top of wiki/log.md.
Your last message starts with [DONE] and summarises in 5 lines what you promoted, what you moved to inbox/{sc["review_dir"]} and why."""


class Curator:
    def __init__(self, vault, state_path, launch, worker_state, commit, release, now=time.time,
                 quiet_s=QUIET_S, gap_s=GAP_S, timeout_s=TIMEOUT_S, max_attempts=MAX_ATTEMPTS, grace_s=START_GRACE_S,
                 schema=None, limit=None):
        """launch(prompt)->{id,name}; worker_state(id)->claude state or None; commit(path, msg)->(ok, info); release(id).
        limit(id)->usage-limit notice (planusage.tail_notice shape) or None: a limit is a pause, never a failure; None = not checked."""
        self._limit = limit
        self._schema = {**DEFAULT_SCHEMA, **(schema or {})}
        self.vault, self.state_path = Path(vault), Path(state_path)
        self._launch, self._worker_state, self._commit, self._release = launch, worker_state, commit, release
        self._now, self._quiet, self._gap, self._timeout, self._max = now, quiet_s, gap_s, timeout_s, max_attempts
        self._grace = grace_s
        self._lock = threading.RLock()

    # ---- state ----
    def _load(self) -> dict:
        try:
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            s = {}
        return {"running": False, "worker_id": "", "started": 0.0, "drafts_before": 0, "draft_names": None, "attempts": 0,
                "finished_at": 0.0, "last": None, "paused_until": 0.0, "paused_kind": "session", "blocked": None, **s}

    def _save(self, s: dict) -> None:
        atomicio.write_text(self.state_path, json.dumps(s, ensure_ascii=False, indent=1))

    def drafts(self) -> list:
        inbox = self.vault / "inbox"
        return sorted(p for p in inbox.glob("*.md") if p.name != self._schema["readme"]) if inbox.is_dir() else []

    def _due(self, s: dict):
        ds = self.drafts()
        if not ds:
            return False, "inbox up to date"
        if s["paused_until"] > self._now():
            return False, "paused by a usage limit: continues by itself after it resets"
        if s["blocked"] and s["blocked"]["until"] > self._now():
            return False, "Claude Code does not trust the vault folder yet: " + self._trust_hint()
        if s["attempts"] >= self._max:
            return False, f"automation stopped after {self._max} consecutive failures: use “Curate now”"
        if self._now() - s["finished_at"] < self._gap:
            return False, "minimum interval between curations"
        quiet_for = self._now() - max(p.stat().st_mtime for p in ds)
        if quiet_for < self._quiet:
            return False, f"waiting {max(1, round((self._quiet - quiet_for) / 60))} min of calm before curating"
        return True, "ready to curate"

    def _trust_hint(self) -> str:
        return f"trust it from the dashboard's alert, or run `cd {self.vault}` then `claude` once and accept the prompt"

    def status(self) -> dict:
        with self._lock:
            s = self._recover(self._load())
            n = len(self.drafts())
            live = self._live_notice(s) if s["running"] else None
            info = f"curating {s['drafts_before']} draft(s)…" if s["running"] else self._due(s)[1]
            if live:
                info = "paused by a usage limit"
            blocked = None if not s["blocked"] else {"kind": s["blocked"]["kind"], "path": str(self.vault)}
            return {"drafts": n, "running": s["running"], "attempts": s["attempts"], "last": s["last"], "info": info,
                    "paused": self._paused(s, live), "blocked": blocked}

    # ---- usage limits ----
    def _notice(self, worker_id: str):
        if not self._limit or not worker_id:
            return None
        try:
            return self._limit(worker_id)
        except Exception:  # noqa: BLE001 - an unreadable log is "no notice"
            return None

    def _live_notice(self, s: dict):
        return self._notice(s["worker_id"])

    def _pause_until(self, notice: dict) -> float:
        """Epoch second when curating may resume: the notice's reset + grace, or a conservative default without a readable time."""
        try:
            return datetime.fromisoformat(notice["resume_at"]).timestamp() + LIMIT_GRACE_S
        except (KeyError, TypeError, ValueError):
            return self._now() + LIMIT_DEFAULT_PAUSE_S

    def _paused(self, s: dict, live=None):
        """None, or {"until_s": seconds until curating resumes (None if unknown), "kind"}. Durations only, never clock times."""
        if live:
            return {"until_s": max(0, int(self._pause_until(live) - self._now())), "kind": live.get("kind", "session")}
        if s["paused_until"] > self._now():
            return {"until_s": int(s["paused_until"] - self._now()), "kind": s["paused_kind"]}
        return None

    def _recover(self, s: dict) -> dict:
        """State left by the old behaviour: usage-limit runs were counted as failures and the automation stopped. If the failures
        were really a limit (the last worker's log still shows the notice, and the last run was not a launch/checkpoint failure),
        clear them and pause until that limit resets (no pause if it already has)."""
        if s["running"] or s["attempts"] < self._max or s["paused_until"] > 0 and s["paused_until"] > s["finished_at"]:
            return s
        last = s["last"] or {}
        notice = self._notice(s["worker_id"]) if not str(last.get("info", "")).startswith("could not") else None
        if not notice:
            return s
        s.update(attempts=0, paused_until=self._pause_until(notice), paused_kind=notice.get("kind", "session"))
        self._save(s)
        return s

    # ---- actions ----
    def tick(self) -> None:
        """Called periodically by the server: follows the running curator or starts a new one when it makes sense."""
        with self._lock:
            s = self._recover(self._load())
            if s["running"]:
                return self._poll(s)
            if self._due(s)[0]:
                try:
                    self._start(s)
                except CurationError:
                    pass  # already recorded in the state

    def start(self, force: bool = False) -> None:
        with self._lock:
            s = self._recover(self._load())
            if s["running"]:
                raise CurationError("a curation is already running")
            if not self.drafts():
                raise CurationError("the inbox is empty: no drafts to process")
            if force:
                s.update(attempts=0, paused_until=0.0, blocked=None)
            elif not self._due(s)[0]:
                raise CurationError(self._due(s)[1])
            self._start(s)

    def _start(self, s: dict) -> None:
        ds = self.drafts()
        ok, info = self._commit(self.vault, "checkpoint before automatic curation")
        if not ok:
            return self._fail(s, f"could not checkpoint the vault: {info}")
        try:
            w = self._launch(curator_prompt(self.vault, ds, self._schema))
        except Exception as e:  # noqa: BLE001
            if TRUST_MARK in str(e):         # a setting of the user's, not a failure of the run: wait for the trust, never count it
                s["blocked"] = {"kind": "trust", "until": self._now() + BLOCK_RETRY_S}
                s["last"] = {"at": self._now(), "handled": 0, "remaining": len(ds), "ok": False,
                             "info": "Claude Code does not trust the vault folder: " + self._trust_hint()}
                self._save(s)
                raise CurationError(s["last"]["info"]) from e
            return self._fail(s, f"could not launch the curator: {e}")
        s.update(blocked=None, running=True, worker_id=w["id"], started=self._now(), drafts_before=len(ds), draft_names=[d.name for d in ds],
                 paused_until=0.0)
        self._save(s)

    def _fail(self, s: dict, why: str) -> None:
        s["attempts"] += 1
        s["finished_at"] = self._now()
        s["last"] = {"at": self._now(), "handled": 0, "remaining": len(self.drafts()), "ok": False, "info": why}
        self._save(s)
        raise CurationError(why)

    def _poll(self, s: dict) -> None:
        elapsed = self._now() - s["started"]
        if elapsed > self._timeout:
            return self._finish(s, "time limit reached: curator abandoned")
        if elapsed < self._grace:
            return None  # just started: it may not be in the session list yet
        if self._live_notice(s):
            return self._finish(s, "")               # stopped by a usage limit: close the run as a pause
        state = self._worker_state(s["worker_id"])
        if state is None or state in BUSY:
            return None  # still working (or unknown): wait
        return self._finish(s, "")

    def _finish(self, s: dict, note: str) -> None:
        left = {d.name for d in self.drafts()}
        wstate = self._worker_state(s["worker_id"])      # before the session is stopped: was it waiting for input?
        before, after = s["drafts_before"], len(left)
        given = s.get("draft_names")
        # the drafts this run was given and that are gone; drafts that arrived meanwhile are the next run's (older state: by count)
        handled = sum(1 for n in given if n not in left) if isinstance(given, list) else max(0, before - after)
        noun = "draft handled" if handled == 1 else "drafts handled"
        ok, info = self._commit(self.vault, f"automatic curation (Chief): {handled} {noun}, {after} left")
        try:
            self._release(s["worker_id"])
        except Exception:  # noqa: BLE001 - stopping the session is an extra
            pass
        notice = self._notice(s["worker_id"])
        if notice:                                  # a usage limit is a pause: nothing counts as a failure
            if handled:
                s["attempts"] = 0
            s.update(paused_until=self._pause_until(notice), paused_kind=notice.get("kind", "session"))
            note = note or "paused by a usage limit"
        else:
            s["attempts"] = s["attempts"] + 1 if handled == 0 else 0
            if handled == 0 and not note and after:        # drafts waited and none was promoted: say why it looks like it ended
                wid = s["worker_id"]
                note = (f"the curator stopped waiting for input (a permission or trust prompt) before promoting anything: read `claude logs {wid}`"
                        if wstate in WAITING
                        else f"the curator ended without promoting any draft: read what it said with `claude logs {wid}`")
        s.update(running=False, finished_at=self._now())
        s["last"] = {"at": self._now(), "handled": handled, "remaining": after, "ok": bool(ok) and not note,
                     "commit": info, "info": note or f"{handled} {noun}; commit {info}"}
        if notice:
            s["last"]["info"] = "paused by a usage limit" + (f" ({note})" if note != "paused by a usage limit" else "")
        self._save(s)
