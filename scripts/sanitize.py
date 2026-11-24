"""The vault sanitizer: keeps the notes about projects matching the projects, and not contradicting each other.

Layers (cheapest first): the vault's own structure and the inbox backlog (no worker, `vaultcheck`); freshness of a project's notes against
the facts of its folder, and contradictions inside a project's notes (one read-only worker on COPIES, never on the vault). Findings carry
evidence and, when possible, an exact replacement that the SERVER applies on the user's click, with a checkpoint commit and a result
commit (so `git revert` undoes it). State is kept in `<state>/sanitize.json`; nothing in the vault's formats changes.

The flow mirrors `curation.py` (one run at a time, usage-limit pauses, a stuck worker is given up) and waits while the curation runs."""
import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import atomicio
import projectfacts
import prompts_sanitize
import vaultcheck

GAP_S = 1800              # minimum interval between runs
WEEK_S = 7 * 86400
STRUCTURE_EVERY_S = 3600  # the cheap layers are refreshed at most hourly by the ticker
TIMEOUT_S = 2 * 3600
START_GRACE_S = 90
MAX_ATTEMPTS = 3
LIMIT_GRACE_S = 180
LIMIT_DEFAULT_PAUSE_S = 1800
BUSY = ("working", "running")
DEFAULTS = {"enabled": True, "weekly": True, "max_notes": 12, "ignore": []}


class SanitizeError(Exception):
    pass


class Sanitizer:
    def __init__(self, vault, state_path, stage_root, launch, worker_state, commit, release, schema, curator=None, config=None,
                 now=time.time, limit=None, collect=projectfacts.collect, drafts=None, note_lang="English"):
        """launch(prompt, add_dir)->{id,name}; worker_state(id)->claude state or None; commit(path, msg)->(ok, info); release(id);
        curator()->{running, blocked, ...} (the curation's status: the two never work on the vault together); config()->the user's
        `sanitizer` settings; drafts()->(count, oldest age in seconds) of the inbox; limit(id)->a usage-limit notice or None."""
        self.vault, self.state_path, self.stage_root, self.schema = Path(vault), Path(state_path), Path(stage_root), schema
        self._launch, self._worker_state, self._commit, self._release = launch, worker_state, commit, release
        self._curator, self._config, self._now, self._limit, self._collect = curator, config, now, limit, collect
        self._drafts, self._note_lang = drafts, note_lang
        self._lock = threading.RLock()

    # ---- state ----
    def _load(self) -> dict:
        try:
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a missing or damaged file is a fresh start
            s = {}
        return {"ledger": {}, "findings": [], "dismissed": {}, "accepted": {}, "run": None, "last": None, "last_full": 0.0, "last_structure": 0.0,
                "queue": [], "closed_seen": [], "attempts": 0, "paused_until": 0.0, "finished_at": 0.0, **s}

    def _save(self, s: dict) -> None:
        atomicio.write_text(self.state_path, json.dumps(s, ensure_ascii=False, indent=1))

    def cfg(self) -> dict:
        c = {**DEFAULTS, **((self._config() if self._config else None) or {})}
        c["max_notes"] = max(1, min(int(c["max_notes"]) if str(c["max_notes"]).isdigit() else 12, 60))
        return c

    # ---- the cheap layers ----
    def _merge(self, s: dict, layers: tuple, new: list) -> None:
        """Replace the findings of `layers` by `new`, keeping what the user already decided (by fingerprint) and what is still open."""
        keep = [f for f in s["findings"] if f["layer"] not in layers]
        old = {f["fingerprint"]: f for f in s["findings"] if f["layer"] in layers}
        out = []
        for f in new:
            fp = f["fingerprint"]
            if fp in s["dismissed"]:
                continue
            if fp in s["accepted"]:
                f = {**f, "status": "accepted"}
            elif fp in old:
                f = {**f, "status": old[fp]["status"], "base_hash": old[fp].get("base_hash", f.get("base_hash", ""))}
            out.append(f)
        s["findings"] = keep + out

    def refresh_structure(self) -> list:
        """Run the layers that need no worker and merge them into the findings. Returns the new structure findings."""
        with self._lock:
            s = self._load()
            n, oldest = self._drafts() if self._drafts else (0, 0.0)
            found = vaultcheck.structure_findings(self.vault, self.schema, n, oldest, datetime.fromtimestamp(self._now())) + vaultcheck.plugin_lint(self.vault)
            self._merge(s, ("structure", "health"), found)
            s["last_structure"] = self._now()
            self._save(s)
            return found

    # ---- planning what a worker should look at ----
    def _hubs(self) -> dict:
        """{hub name: {"rel", "local"}} for every project hub (a note with `local:`) that has a folder."""
        out = {}
        for rel in vaultcheck.note_paths(self.vault, self.schema["hub_dirs"]):
            if rel.count("/") != 1:
                continue
            local = vaultcheck.frontmatter(vaultcheck.read_note(self.vault, rel)).get("local", "").strip().strip("\"'")
            if local:
                out[Path(rel).stem] = {"rel": rel, "local": local}
        return out

    def plan(self, s: dict, force_all: bool = False) -> dict:
        """What to verify now: the stale notes of each project (queued projects first), within `max_notes`; and every project whose notes
        changed gets a contradictions job. Returns {jobs, staged, facts, leftovers}."""
        cfg, hubs = self.cfg(), self._hubs()
        queued = {str(Path(p).resolve()).lower() for p in s["queue"]}
        order = sorted(hubs, key=lambda n: (str(Path(hubs[n]["local"]).resolve()).lower() not in queued, n))
        ignore = [g for g in cfg["ignore"] if isinstance(g, str)]
        budget, jobs, staged, facts, leftovers = cfg["max_notes"], [], {}, {}, 0
        for name in order:
            folder = Path(hubs[name]["local"])
            f = self._collect(folder)
            fdigest, head = projectfacts.digest(f), f.get("head", "")
            notes = [r for r in vaultcheck.note_paths(self.vault, self.schema["hub_dirs"]) if vaultcheck.project_of(r, self.schema["hub_dirs"]) == name
                     and not any(Path(r).match(g) for g in ignore)]
            texts = {r: vaultcheck.read_note(self.vault, r) for r in notes}
            stale = [r for r in notes if force_all or vaultcheck.is_stale(s["ledger"].get(r), vaultcheck.sha(texts[r]), head, fdigest)]
            if not stale:
                continue
            take = stale[:max(0, budget)]
            leftovers += len(stale) - len(take)
            if not take:
                continue
            budget -= len(take)
            changed = [r for r in take if not s["ledger"].get(r) or s["ledger"][r].get("hash") != vaultcheck.sha(texts[r])]
            for r in (take + [x for x in notes if x not in take] if changed and len(notes) > 1 else take):
                staged[r] = texts[r]
            if f.get("exists"):
                facts[name] = {"data": f, "digest": fdigest, "head": head}
                jobs.append({"mode": "freshness", "project": name, "notes": take, "facts": f"facts/{name}.json"})
            if changed and len(notes) > 1:
                jobs.append({"mode": "contradictions", "project": name, "notes": sorted(r for r in staged if vaultcheck.project_of(r, self.schema["hub_dirs"]) == name), "facts": ""})
        return {"jobs": jobs, "staged": staged, "facts": facts, "leftovers": leftovers}

    # ---- running ----
    def _curation_busy(self) -> str:
        st = (self._curator() if self._curator else None) or {}
        if st.get("running"):
            return "the vault curation is running: the sanitizer waits for it"
        if st.get("blocked"):
            return "the vault curation is blocked (Claude Code does not trust the vault): trust it first"
        return ""

    def _due(self, s: dict):
        cfg = self.cfg()
        if not cfg["enabled"]:
            return False, "the sanitizer is off"
        if s["run"]:
            return False, "a check is running"
        if s["paused_until"] > self._now():
            return False, "paused by a usage limit"
        if s["attempts"] >= MAX_ATTEMPTS:
            return False, f"stopped after {MAX_ATTEMPTS} consecutive failures: use «Check the vault now»"
        if self._now() - s["finished_at"] < GAP_S:
            return False, "minimum interval between checks"
        if self._curation_busy():
            return False, self._curation_busy()
        if s["queue"]:
            return True, "a project changed"
        if cfg["weekly"] and self._now() - s["last_full"] >= WEEK_S:
            return True, "the weekly check is due"
        return False, "nothing to check"

    def start(self, force: bool = False) -> dict:
        """Run the cheap layers, then launch ONE worker for what is stale. Returns {launched, jobs, leftovers} (launched False: nothing to verify)."""
        with self._lock:
            s = self._load()
            if s["run"]:
                raise SanitizeError("a check is already running")
            if not self.cfg()["enabled"] and not force:
                raise SanitizeError("the sanitizer is off")
            busy = self._curation_busy()
            if busy:
                raise SanitizeError(busy)
            if force:
                s.update(attempts=0, paused_until=0.0)
            elif not self._due(s)[0]:
                raise SanitizeError(self._due(s)[1])
            self._save(s)
        self.refresh_structure()
        with self._lock:
            s = self._load()
            plan = self.plan(s, force_all=False)
            if not plan["jobs"]:
                s.update(last_full=self._now(), finished_at=self._now(), queue=[],
                         last={"at": self._now(), "ok": True, "checked": 0, "info": "nothing needed checking"})
                self._save(s)
                return {"launched": False, "jobs": 0, "leftovers": 0}
            rid = datetime.fromtimestamp(self._now()).strftime("%y%m%d%H%M%S")
            stage = self.stage_root / rid
            shutil.rmtree(stage, ignore_errors=True)
            for rel, text in plan["staged"].items():
                (stage / "notes" / rel).parent.mkdir(parents=True, exist_ok=True)
                (stage / "notes" / rel).write_text(text, encoding="utf-8")
            (stage / "facts").mkdir(parents=True, exist_ok=True)
            for name, f in plan["facts"].items():
                (stage / "facts" / f"{name}.json").write_text(json.dumps(f["data"], ensure_ascii=False, indent=1), encoding="utf-8")
            prompt = prompts_sanitize.render(str(stage).replace("\\", "/"), plan["jobs"], self._note_lang)
            try:
                w = self._launch(prompt, str(stage))
            except Exception as e:  # noqa: BLE001
                shutil.rmtree(stage, ignore_errors=True)
                s["attempts"] += 1
                s.update(finished_at=self._now(), last={"at": self._now(), "ok": False, "info": f"could not launch the audit: {e}"})
                self._save(s)
                raise SanitizeError(s["last"]["info"]) from e
            heads = {j["project"]: plan["facts"][j["project"]] for j in plan["jobs"] if j["project"] in plan["facts"]}
            s["run"] = {"id": rid, "worker_id": w["id"], "started": self._now(), "stage": str(stage), "leftovers": plan["leftovers"],
                        "notes": {r: {"hash": vaultcheck.sha(t), "project": vaultcheck.project_of(r, self.schema["hub_dirs"])} for r, t in plan["staged"].items()},
                        "facts": {n: {"digest": h["digest"], "head": h["head"]} for n, h in heads.items()}}
            self._save(s)
            return {"launched": True, "jobs": len(plan["jobs"]), "leftovers": plan["leftovers"]}

    def tick(self) -> None:
        """Called by the server's ticker: follows a running audit, refreshes the cheap layers now and then, starts a check when it is due."""
        with self._lock:
            s = self._load()
            if s["run"]:
                return self._poll(s)
            if self.cfg()["enabled"] and self._now() - s["last_structure"] >= STRUCTURE_EVERY_S:
                pass
            else:
                due = self._due(s)[0]
                if not due:
                    return
        if self.cfg()["enabled"] and self._now() - self._load()["last_structure"] >= STRUCTURE_EVERY_S:
            self.refresh_structure()
        with self._lock:
            if self._due(self._load())[0]:
                try:
                    self.start()
                except SanitizeError:
                    pass                                    # recorded in the state

    def _poll(self, s: dict) -> None:
        run = s["run"]
        elapsed = self._now() - run["started"]
        if elapsed > TIMEOUT_S:
            return self._finish(s, "time limit reached: the audit was abandoned")
        if elapsed < START_GRACE_S:
            return
        if self._notice(run["worker_id"]):
            return self._finish(s, "")
        state = self._worker_state(run["worker_id"])
        if state is None or state in BUSY:
            return
        return self._finish(s, "")

    def _notice(self, wid: str):
        if not self._limit or not wid:
            return None
        try:
            return self._limit(wid)
        except Exception:  # noqa: BLE001
            return None

    def _finish(self, s: dict, note: str) -> None:
        run, stage = s["run"], Path(s["run"]["stage"])
        wid, notice = run["worker_id"], self._notice(run["worker_id"])
        try:
            self._release(wid)
        except Exception:  # noqa: BLE001 - stopping the session is an extra
            pass
        new, why = [], note
        if notice:                                          # a usage limit is a pause, never a failure
            try:
                from datetime import datetime as _dt
                until = _dt.fromisoformat(notice["resume_at"]).timestamp() + LIMIT_GRACE_S
            except (KeyError, TypeError, ValueError):
                until = self._now() + LIMIT_DEFAULT_PAUSE_S
            s.update(paused_until=until, run=None, finished_at=self._now(), last={"at": self._now(), "ok": True, "info": "paused by a usage limit"})
            self._save(s)
            shutil.rmtree(stage, ignore_errors=True)
            return
        if not why:
            try:
                data = json.loads((stage / "findings.json").read_text(encoding="utf-8"))
                staged = {r: (stage / "notes" / r).read_text(encoding="utf-8") for r in run["notes"]}
                new = vaultcheck.validate_findings(data, staged)
            except (OSError, ValueError) as e:
                why = f"the audit left no readable findings file ({e.__class__.__name__}): read its log with `claude logs {wid}`"
            except vaultcheck.FindingsError as e:
                why = f"the findings were refused: {e}"
        if why:
            s["attempts"] += 1
            s.update(run=None, finished_at=self._now(), last={"at": self._now(), "ok": False, "info": why})
        else:
            checked = set(run["notes"])
            for f in new:
                f["base_hash"] = run["notes"][f["note"]]["hash"]
            self._merge_audit(s, checked, new)
            for rel, meta in run["notes"].items():
                cur = vaultcheck.sha(vaultcheck.read_note(self.vault, rel)) if (self.vault / rel).is_file() else ""
                if cur == meta["hash"]:                     # edited meanwhile: not verified, it will be checked again
                    fx = run["facts"].get(meta["project"], {})
                    s["ledger"][rel] = {"hash": meta["hash"], "head": fx.get("head", ""), "facts": fx.get("digest", ""), "at": self._now()}
            projects = {m["project"] for m in run["notes"].values()}
            s["queue"] = [p for p in s["queue"] if Path(p).name not in projects and not self._queued_for(p, projects)]
            s.update(attempts=0, run=None, finished_at=self._now(), last_full=self._now() if not run["leftovers"] else s["last_full"],
                     last={"at": self._now(), "ok": True, "checked": len(run["notes"]), "new": len(new),
                           "info": f"{len(run['notes'])} note(s) checked, {len(new)} finding(s)" + (f", {run['leftovers']} more waiting" if run["leftovers"] else "")})
        self._save(s)
        shutil.rmtree(stage, ignore_errors=True)

    def _queued_for(self, path: str, projects: set) -> bool:
        hubs = self._hubs()
        return any(n in projects and str(Path(h["local"]).resolve()).lower() == str(Path(path).resolve()).lower() for n, h in hubs.items())

    def _merge_audit(self, s: dict, checked: set, new: list) -> None:
        """The audit findings of the notes just verified are replaced by the new ones; those of other notes are kept."""
        keep = [f for f in s["findings"] if f["layer"] != "audit" or f["note"] not in checked]
        old = {f["fingerprint"]: f for f in s["findings"] if f["layer"] == "audit" and f["note"] in checked}
        out = []
        for f in new:
            fp = f["fingerprint"]
            if fp in s["dismissed"]:
                continue
            out.append({**f, "status": "accepted"} if fp in s["accepted"] else ({**f, "status": old[fp]["status"]} if fp in old else f))
        s["findings"] = keep + out

    # ---- what the user does with a finding ----
    def _find(self, s: dict, fid: str) -> dict:
        f = next((x for x in s["findings"] if x["id"] == fid), None)
        if f is None:
            raise SanitizeError("that finding is no longer there")
        return f

    def accept(self, fid: str) -> None:
        with self._lock:
            s = self._load()
            f = self._find(s, fid)
            s["accepted"][f["fingerprint"]] = self._now()
            f["status"] = "accepted"
            self._save(s)

    def dismiss(self, fid: str) -> None:
        with self._lock:
            s = self._load()
            f = self._find(s, fid)
            s["dismissed"][f["fingerprint"]] = self._now()
            s["findings"] = [x for x in s["findings"] if x["id"] != fid]
            self._save(s)

    def apply(self, fid: str) -> dict:
        """Apply a finding's exact replacement to the note: checkpoint commit, the change, a result commit, a line in wiki/log.md."""
        with self._lock:
            s = self._load()
            f = self._find(s, fid)
            if f["status"] != "open" or not f.get("replace"):
                raise SanitizeError("this finding has no replacement to apply")
            busy = self._curation_busy()
            if busy:
                raise SanitizeError(busy)
            ok, info = self._commit(self.vault, "checkpoint before vault sanitizer")
            if not ok:
                raise SanitizeError(f"could not checkpoint the vault: {info}")
            try:
                new_hash = vaultcheck.apply_replace(self.vault, f["note"], f["replace"]["old"], f["replace"]["new"], f["base_hash"])
            except vaultcheck.ApplyError as e:                 # the note moved on: forget what was verified so it is checked again
                s["ledger"].pop(f["note"], None)
                s["findings"] = [x for x in s["findings"] if x["id"] != fid]
                self._save(s)
                raise SanitizeError(str(e)) from e
            vaultcheck.log_entry(self.vault, "vault sanitizer: 1 note fixed", [f["note"]])
            ok, info = self._commit(self.vault, f"vault sanitizer: {f['note']} ({f['kind']})")
            f["status"] = "applied"
            if f["note"] in s["ledger"]:
                s["ledger"][f["note"]]["hash"] = new_hash
            s["findings"] = [x for x in s["findings"] if x["id"] != fid]
            self._save(s)
            return {"note": f["note"], "commit": info if ok else ""}

    # ---- triggers and the dashboard ----
    def note_closed(self, requests: list) -> int:
        """Requests that have just closed change their project: queue it for a check. Returns how many projects were queued."""
        with self._lock:
            s = self._load()
            seen, n = set(s["closed_seen"]), 0
            for f in requests:
                if f.get("closed") and f.get("path") and f["id"] not in seen:
                    seen.add(f["id"])
                    if f["path"] not in s["queue"]:
                        s["queue"].append(f["path"])
                        n += 1
            s["closed_seen"] = sorted(seen)[-500:]
            if n or len(seen) != len(s["closed_seen"]):
                self._save(s)
            return n

    def queue_project(self, path: str) -> None:
        with self._lock:
            s = self._load()
            if path not in s["queue"]:
                s["queue"].append(path)
                self._save(s)

    def status(self) -> dict:
        with self._lock:
            s = self._load()
            open_ = [f for f in s["findings"] if f["status"] == "open"]
            counts = {}
            for f in open_:
                counts[f["layer"]] = counts.get(f["layer"], 0) + 1
            trimmed = [{k: f.get(k) for k in ("id", "layer", "kind", "severity", "note", "quote", "evidence", "detail")} | {"replace": bool(f.get("replace")),
                       "old": (f.get("replace") or {}).get("old", ""), "new": (f.get("replace") or {}).get("new", "")} for f in
                       sorted(open_, key=lambda f: (vaultcheck.SEVERITIES.index(f["severity"]), f["note"]))]
            due, why = self._due(s)
            return {"vault_name": self.vault.name, "enabled": self.cfg()["enabled"], "running": bool(s["run"]), "open": len(open_), "counts": counts, "findings": trimmed[:60],
                    "last": s["last"], "info": "checking the notes…" if s["run"] else why, "queued": len(s["queue"]), "attempts": s["attempts"]}
