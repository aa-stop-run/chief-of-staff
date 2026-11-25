"""Keeps the generated vault map (`Mapa automático.canvas`) up to date.

The server collects the hubs and the links between them from the notes (`vaultmap`), writes the canvas (with a checkpoint commit before and
a result commit after, so `git revert` undoes it) and, for the pairs whose supporting sentences are new or changed, launches ONE read-only
worker on a copy of those sentences to write the short labels, which the server validates before using. The hand-drawn `Mapa.canvas` and
every note are untouched. Same shape as `sanitize.py`: one run at a time, usage-limit pause, 3 failures stop it, waits for the curation."""
import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import atomicio
import prompts_map
import vaultmap
from vaultcheck import FindingsError

CHECK_S = 3600
TIMEOUT_S = 2 * 3600
START_GRACE_S = 90
MAX_ATTEMPTS = 3
MAX_PAIRS_PER_RUN = 40
LIMIT_GRACE_S = 180
LIMIT_DEFAULT_PAUSE_S = 1800
BUSY = ("working", "running")
DEFAULTS = {"enabled": True, "labels": True}


class MapError(Exception):
    pass


class Mapper:
    def __init__(self, vault, state_path, stage_root, launch, worker_state, commit, release, hub_dirs, busy=None, config=None,
                 now=time.time, limit=None, note_lang="Portuguese (European)"):
        """launch(prompt, stage)->{id,name}; worker_state(id); commit(path, msg)->(ok, info); release(id); busy()->a reason to wait
        (the curation or the sanitizer is working on the vault) or ""; config()->the user's `vault_map` settings."""
        self.vault, self.state_path, self.stage_root, self.hub_dirs = Path(vault), Path(state_path), Path(stage_root), tuple(hub_dirs)
        self._launch, self._worker_state, self._commit, self._release = launch, worker_state, commit, release
        self._busy, self._config, self._now, self._limit, self._lang = busy, config, now, limit, note_lang
        self._lock = threading.RLock()

    def _load(self) -> dict:
        try:
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a missing or damaged file is a fresh start
            s = {}
        return {"labels": {}, "run": None, "last": None, "attempts": 0, "paused_until": 0.0, "finished_at": 0.0, "checked_at": 0.0, "poked": True,
                "pairs": 0, "hubs": 0, **s}

    def _save(self, s: dict) -> None:
        atomicio.write_text(self.state_path, json.dumps(s, ensure_ascii=False, indent=1))

    def cfg(self) -> dict:
        return {**DEFAULTS, **((self._config() if self._config else None) or {})}

    def poke(self) -> None:
        """The links may have changed (a curation just finished): look again at the next tick."""
        with self._lock:
            s = self._load()
            s["poked"] = True
            self._save(s)

    # ---- writing the canvas ----
    def _current_labels(self, s: dict, mp: dict) -> dict:
        hashes = {vaultmap.pair_key(p["a"], p["b"]): p["hash"] for p in mp["pairs"]}
        return {k: v for k, v in s["labels"].items() if hashes.get(k) == v.get("hash")}

    def write(self, s: dict, mp: dict, why: str) -> bool:
        """Write the canvas for the labels that are still valid. True when the file changed (and was committed)."""
        text = vaultmap.canvas_text(mp, self._current_labels(s, mp), vaultmap.lang_of(self._lang))
        path = self.vault / vaultmap.AUTO_NAME
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return False
        ok, info = self._commit(self.vault, "checkpoint before the vault map")
        if not ok:
            raise MapError(f"could not checkpoint the vault: {info}")
        vaultmap.write_canvas(self.vault, vaultmap.AUTO_NAME, text)
        ok, info = self._commit(self.vault, f"vault map: {why}")
        if not ok:
            raise MapError(f"could not commit the map: {info}")
        return True

    def _waiting(self) -> str:
        return (self._busy() if self._busy else "") or ""

    def _changed(self, s: dict, mp: dict) -> list:
        return [p for p in mp["pairs"] if s["labels"].get(vaultmap.pair_key(p["a"], p["b"]), {}).get("hash") != p["hash"]]

    # ---- a run ----
    def start(self, force: bool = False) -> dict:
        """Write the canvas now and, if some pairs need labels and labels are on, launch one worker. Returns {written, launched, pairs}."""
        with self._lock:
            s = self._load()
            if s["run"]:
                raise MapError("the map is being updated")
            if not self.cfg()["enabled"] and not force:
                raise MapError("the vault map is off")
            if self._waiting():
                raise MapError(self._waiting())
            if force:
                s.update(attempts=0, paused_until=0.0)
            elif s["attempts"] >= MAX_ATTEMPTS:
                raise MapError(f"stopped after {MAX_ATTEMPTS} failures: use «Update the map»")
            elif s["paused_until"] > self._now():
                raise MapError("paused by a usage limit")
            mp = vaultmap.collect(self.vault, self.hub_dirs)
            written = self.write(s, mp, "links of the notes")
            s.update(checked_at=self._now(), poked=False, pairs=len(mp["pairs"]), hubs=len(mp["hubs"]))
            todo = self._changed(s, mp)[:MAX_PAIRS_PER_RUN]
            if not todo or not self.cfg()["labels"]:
                note = " (map updated)" if written else ""
                s["last"] = {"at": self._now(), "ok": True, "info": f"{len(mp['hubs'])} hubs, {len(mp['pairs'])} relations{note}"}
                self._save(s)
                return {"written": written, "launched": False, "pairs": len(todo)}
            rid = datetime.fromtimestamp(self._now()).strftime("%y%m%d%H%M%S")
            stage = self.stage_root / rid
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(parents=True)
            payload = {"pairs": [{"a": p["a"], "b": p["b"], "sentences": p["sentences"]} for p in todo]}
            (stage / "pairs.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            try:
                w = self._launch(prompts_map.render(str(stage).replace("\\", "/"), self._lang), str(stage))
            except Exception as e:  # noqa: BLE001
                shutil.rmtree(stage, ignore_errors=True)
                s["attempts"] += 1
                s.update(finished_at=self._now(), last={"at": self._now(), "ok": False, "info": f"could not launch the labeller: {e}"})
                self._save(s)
                raise MapError(s["last"]["info"]) from e
            s["run"] = {"id": rid, "worker_id": w["id"], "started": self._now(), "stage": str(stage),
                        "hashes": {vaultmap.pair_key(p["a"], p["b"]): p["hash"] for p in todo}}
            self._save(s)
            return {"written": written, "launched": True, "pairs": len(todo)}

    def tick(self) -> None:
        with self._lock:
            s = self._load()
            if s["run"]:
                return self._poll(s)
            if not self.cfg()["enabled"] or s["attempts"] >= MAX_ATTEMPTS or s["paused_until"] > self._now() or self._waiting():
                return
            if not s["poked"] and self._now() - s["checked_at"] < CHECK_S:
                return
        try:
            self.start()
        except MapError:
            pass                                          # recorded in the state

    def _notice(self, wid: str):
        if not self._limit or not wid:
            return None
        try:
            return self._limit(wid)
        except Exception:  # noqa: BLE001
            return None

    def _poll(self, s: dict) -> None:
        run = s["run"]
        elapsed = self._now() - run["started"]
        if elapsed > TIMEOUT_S:
            return self._finish(s, "time limit reached: the labeller was abandoned")
        if elapsed < START_GRACE_S:
            return
        if self._notice(run["worker_id"]):
            return self._finish(s, "")
        state = self._worker_state(run["worker_id"])
        if state is None or state in BUSY:
            return
        self._finish(s, "")

    def _finish(self, s: dict, note: str) -> None:
        run, stage = s["run"], Path(s["run"]["stage"])
        notice = self._notice(run["worker_id"])
        try:
            self._release(run["worker_id"])
        except Exception:  # noqa: BLE001
            pass
        if notice:
            try:
                until = datetime.fromisoformat(notice["resume_at"]).timestamp() + LIMIT_GRACE_S
            except (KeyError, TypeError, ValueError):
                until = self._now() + LIMIT_DEFAULT_PAUSE_S
            s.update(paused_until=until, run=None, finished_at=self._now(), last={"at": self._now(), "ok": True, "info": "paused by a usage limit"})
            self._save(s)
            shutil.rmtree(stage, ignore_errors=True)
            return
        why, new = note, []
        if not why:
            try:
                pairs = json.loads((stage / "pairs.json").read_text(encoding="utf-8"))["pairs"]
                new = vaultmap.validate_labels(json.loads((stage / "labels.json").read_text(encoding="utf-8")), pairs)
            except (OSError, ValueError, KeyError) as e:
                why = f"the labeller left no readable labels file ({e.__class__.__name__}): read its log with `claude logs {run['worker_id']}`"
            except FindingsError as e:
                why = f"the labels were refused: {e}"
        if why:
            s["attempts"] += 1
            s.update(run=None, finished_at=self._now(), last={"at": self._now(), "ok": False, "info": why})
            self._save(s)
        else:
            for item in new:
                s["labels"][item["key"]] = {"label": item["label"], "direction": item["direction"], "hash": run["hashes"].get(item["key"], "")}
            s.update(attempts=0, run=None, finished_at=self._now())
            try:
                mp = vaultmap.collect(self.vault, self.hub_dirs)
                changed = self.write(s, mp, f"{len(new)} label(s) from the notes' sentences")
                s["last"] = {"at": self._now(), "ok": True, "info": f"{len(new)} label(s) written" + (", map updated" if changed else "")}
            except MapError as e:
                s["attempts"] += 1
                s["last"] = {"at": self._now(), "ok": False, "info": str(e)}
            self._save(s)
        shutil.rmtree(stage, ignore_errors=True)

    def status(self) -> dict:
        with self._lock:
            s = self._load()
            return {"enabled": self.cfg()["enabled"], "running": bool(s["run"]), "hubs": s["hubs"], "pairs": s["pairs"], "labelled": len(s["labels"]),
                    "last": s["last"], "attempts": s["attempts"], "file": vaultmap.AUTO_NAME}
