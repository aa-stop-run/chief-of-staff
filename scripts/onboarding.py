"""Initial extraction of a new project into the vault.

When a project is added or cloned, a read-only worker is launched at once to study the repo and leave a complete note in the
inbox; when the worker finishes, a curation is forced so the note reaches the vault without waiting for the inbox to be calm.
State in state/onboarding.json (survives server restarts)."""
import json
import threading
import time
from pathlib import Path

RETRY_S = 60             # project not yet trusted by claude: retry every minute
WAIT_TRUST_MAX_S = 24 * 3600
START_GRACE_S = 60       # the worker just started: it may not be in claude's list yet
TIMEOUT_S = 90 * 60      # give up waiting for a stuck worker

# Vault conventions the task refers to; the Chief passes its own (chief.SCHEMA).
DEFAULT_SCHEMA = {"readme": "README.md", "name": "English", "fm": {"kind": "type"}, "project_kind": "project"}

TASK = """Initial extraction of the project "{name}" into the vault. The project has just been added to the Chief and has no note yet.
The repo is READ-ONLY: do not change it, do not install anything, do not run the project, do not commit. You may run read
commands (list folders, git log, git status, read files). Do not read or print .env*, keys, tokens, SECRETS*, credential files.

Study: README and CLAUDE.md, manifests (package.json, pyproject.toml, requirements, Cargo.toml, go.mod, etc.), folder structure,
tests and CI, scripts, and the last 30 commits (git log --oneline). Then write ONE note in {vault}/inbox/ named
'YYYY-MM-DD {name} - initial extraction.md' (format in {vault}/inbox/{readme}, {kind_key}: {project_kind}), written in {note_lang}, with these sections:
- What it is (2 to 3 lines) and domain (to be confirmed if unclear)
- Stack and main dependencies
- Architecture: main modules and where they are (paths relative to the repo)
- How to run and test (commands you see documented or in the scripts)
- Current state: branch, last commit, uncommitted changes, visible leftovers (relevant TODO/FIXME)
- Decisions and conventions visible in the code or docs
- External integrations (service names; no private URLs, IPs, e-mails or credentials)
- Risks and gaps
Mark "(to be confirmed)" anything you did not verify. Never invent."""


class Onboarding:
    def __init__(self, state_path, launch, is_active, curate, curator_busy, release, now=time.time,
                 grace_s=START_GRACE_S, timeout_s=TIMEOUT_S, schema=None):
        """launch(project, task, label)->{id}; is_active(id)->bool; curate() forces the curation (raises if it cannot);
        curator_busy()->bool; release(id)."""
        self._schema = {**DEFAULT_SCHEMA, **(schema or {})}
        self.path, self._launch, self._active = Path(state_path), launch, is_active
        self._curate, self._busy, self._release, self._now = curate, curator_busy, release, now
        self._grace, self._timeout = grace_s, timeout_s
        self._lock = threading.Lock()

    def _load(self) -> list:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [x for x in data if isinstance(x, dict) and (x.get("id") or x.get("waiting"))] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, items: list) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def pending(self) -> list:
        with self._lock:
            return self._load()

    def start(self, project: dict, vault) -> dict:
        """Launch the extraction worker; return {id, name}, or {waiting: True} if claude does not trust the folder yet (it is
        queued and starts by itself once the user accepts the trust). Other errors go up to the caller."""
        name = project["name"]
        try:
            w = self._launch(name, self._task(name, vault), label="extraction-" + name)
        except Exception as e:  # noqa: BLE001
            if "not trusted" not in str(e).lower():
                raise
            with self._lock:
                self._save([*self._load(), {"waiting": True, "project": name, "vault": str(vault), "started": self._now(),
                                            "next": self._now() + RETRY_S}])
            return {"waiting": True}
        with self._lock:
            self._save([*self._load(), {"id": w["id"], "project": name, "started": self._now()}])
        return w

    def _task(self, name, vault) -> str:
        sc = self._schema
        return TASK.format(name=name, vault=str(vault).replace("\\", "/"), readme=sc["readme"], note_lang=sc["name"],
                           kind_key=sc["fm"]["kind"], project_kind=sc["project_kind"])

    def _retry(self, it: dict):
        """Try to launch a queued worker. Return the new entry, or None if it should disappear."""
        now = self._now()
        if now - it.get("started", 0) > WAIT_TRUST_MAX_S:
            return None
        if now < it.get("next", 0):
            return it
        try:
            w = self._launch(it["project"], self._task(it["project"], it["vault"]), label="extraction-" + it["project"])
        except Exception:  # noqa: BLE001 - still untrusted (or something else failed): try again in a minute
            return {**it, "next": now + RETRY_S}
        return {"id": w["id"], "project": it["project"], "started": now}

    def tick(self) -> None:
        """When an extraction worker finishes, launch the curation; if it cannot yet (another is running), try on the next tick."""
        with self._lock:
            items = self._load()
            keep = []
            for it in items:
                if it.get("waiting"):
                    nxt = self._retry(it)
                    if nxt:
                        keep.append(nxt)
                    continue
                age = self._now() - it.get("started", 0)
                if age < self._grace or (age < self._timeout and self._active(it["id"])):
                    keep.append(it)
                    continue
                try:
                    self._curate()
                except Exception:  # noqa: BLE001 - curation busy or inbox empty: in the first case, try again
                    if self._busy():
                        keep.append(it)
                        continue
                try:
                    self._release(it["id"])
                except Exception:  # noqa: BLE001
                    pass
            if keep != items:
                self._save(keep)
