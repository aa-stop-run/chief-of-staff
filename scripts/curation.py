"""Automatic vault curation: promotes the inbox drafts without the user clicking anything.

Flow (everything reversible with git): vault checkpoint -> background curator worker (working inside the vault itself, to
load its rules) -> commit of the result. State is kept in <state>/curation.json.
"""
import json
import threading
import time
from pathlib import Path

QUIET_S = 600            # the inbox must be "calm" (no new draft) for 10 min: a worker may still be writing
GAP_S = 1800             # minimum interval between curations
TIMEOUT_S = 2 * 3600     # a stuck curator is abandoned
START_GRACE_S = 90       # right after launch the worker is not yet in claude's list: do not treat it as finished
MAX_ATTEMPTS = 3         # consecutive failures before the automation stops (the "Curate now" button keeps working)
BUSY = ("working", "running")

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
                 schema=None):
        """launch(prompt)->{id,name}; worker_state(id)->claude state or None; commit(path, msg)->(ok, info); release(id)."""
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
        return {"running": False, "worker_id": "", "started": 0.0, "drafts_before": 0, "attempts": 0,
                "finished_at": 0.0, "last": None, **s}

    def _save(self, s: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    def drafts(self) -> list:
        inbox = self.vault / "inbox"
        return sorted(p for p in inbox.glob("*.md") if p.name != self._schema["readme"]) if inbox.is_dir() else []

    def _due(self, s: dict):
        ds = self.drafts()
        if not ds:
            return False, "inbox up to date"
        if s["attempts"] >= self._max:
            return False, f"automation stopped after {self._max} consecutive failures: use “Curate now”"
        if self._now() - s["finished_at"] < self._gap:
            return False, "minimum interval between curations"
        quiet_for = self._now() - max(p.stat().st_mtime for p in ds)
        if quiet_for < self._quiet:
            return False, f"waiting {max(1, round((self._quiet - quiet_for) / 60))} min of calm before curating"
        return True, "ready to curate"

    def status(self) -> dict:
        with self._lock:
            s = self._load()
            n = len(self.drafts())
            info = f"curating {s['drafts_before']} draft(s)…" if s["running"] else self._due(s)[1]
            return {"drafts": n, "running": s["running"], "attempts": s["attempts"], "last": s["last"], "info": info}

    # ---- actions ----
    def tick(self) -> None:
        """Called periodically by the server: follows the running curator or starts a new one when it makes sense."""
        with self._lock:
            s = self._load()
            if s["running"]:
                return self._poll(s)
            if self._due(s)[0]:
                try:
                    self._start(s)
                except CurationError:
                    pass  # already recorded in the state

    def start(self, force: bool = False) -> None:
        with self._lock:
            s = self._load()
            if s["running"]:
                raise CurationError("a curation is already running")
            if not self.drafts():
                raise CurationError("the inbox is empty: no drafts to process")
            if force:
                s["attempts"] = 0
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
            return self._fail(s, f"could not launch the curator: {e}")
        s.update(running=True, worker_id=w["id"], started=self._now(), drafts_before=len(ds))
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
        state = self._worker_state(s["worker_id"])
        if state is None or state in BUSY:
            return None  # still working (or unknown): wait
        return self._finish(s, "")

    def _finish(self, s: dict, note: str) -> None:
        before, after = s["drafts_before"], len(self.drafts())
        handled = max(0, before - after)
        noun = "draft handled" if handled == 1 else "drafts handled"
        ok, info = self._commit(self.vault, f"automatic curation (Chief): {handled} {noun}, {after} left")
        try:
            self._release(s["worker_id"])
        except Exception:  # noqa: BLE001 - stopping the session is an extra
            pass
        s["attempts"] = s["attempts"] + 1 if handled == 0 else 0
        s.update(running=False, finished_at=self._now())
        s["last"] = {"at": self._now(), "handled": handled, "remaining": after, "ok": bool(ok) and not note,
                     "commit": info, "info": note or f"{handled} {noun}; commit {info}"}
        self._save(s)
