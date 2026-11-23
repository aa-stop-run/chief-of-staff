"""The Chief's feature pipeline: spec -> plan -> implementation -> review, with human approval between phases.

Each phase is a worker (`claude --bg`) that writes an artifact in <root>/features/<id>/.
State is kept in <root>/features.json. The user approves every phase; what runs by itself is what informs that decision (the
plan validation, the independent review and its fix rounds) and, only when the request asked for it, the approval of a spec or
a plan that left no questions («run until the review»). The last phase is always the user's.
"""
import json
import re
import shutil
import threading
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import atomicio
import combine
import engine
import fanout
import gates
import pipelines
import prompt_refs
import prompts
import prompts_kinds
import prompts_integration
import prompts_pt
import prompts_qa
import splitplan
from feature_checks import (AWAITING, CANCELLED, CHECK_EVENTS, DONE, FAILED, GATE_REASONS, PLAN_REVIEW, WORKING,  # noqa: F401
                           CheckFlow, COMMIT_TYPES, FeatureError, _now, _slug, commit_message)  # noqa: F401

STAGES = ("spec", "plan", "build", "review")
ARTIFACT = {"spec": "spec.md", "plan": "plan.md", "build": "report.md", "review": "review.md"}
# Any model name the user's Claude Code understands (an alias such as "opus", a full model id, a gateway's model name).
# It is only ever passed as an argument to `claude --model`, never to a shell; this just keeps obvious junk out.
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,79}$")
KINDS = prompts_kinds.KINDS   # feature | fix | analysis | maintenance | ship (each one's phases are the steps of its pipeline: scripts/pipelines_builtin/<type>.json)
LANGS = ("en", "pt")   # language the workers write the artifacts in (the dashboard's when the request was created)
MAX_TITLE, MAX_DESC, MAX_COMMENT, MAX_ARTIFACT = 120, 4000, 6000, 40_000
QUESTIONS_FILE = "questions.json"
QA_DIR = "qa"                 # «Ask the worker»: one answer file per question (qa/<n>.md); the exchange itself is in feature["qa"]
MAX_QUESTION, MAX_QA_CONTEXT = 2000, 3000   # characters; how much of an earlier answer a new question sees
ARCHIVE_EVENTS = {"spec": ("spec archived", "spec archiving failed"), "plan": ("plan archived", "plan archiving failed"),
                  "analysis": ("analysis archived", "analysis archiving failed")}
PLAN_REVIEW = "plan-review.md"   # independent plan validation (a sub-step of the plan phase)
MAX_PLAN_FIXES = 1               # automatic rounds plan -> validation -> plan fix (one: a validator always finds something)
AUTOPILOT_STAGES = ("spec", "plan")   # what «run until the review» may approve by itself; never the last phase, never ship/release
MAX_PLAN_TASKS = prompt_refs.MAX_PLAN_TASKS   # above this many steps the planner plans a first part and the validator proposes a split
PIPELINE_SNAPSHOT = "pipeline.json"   # the definition a request was created with (immune to later edits of the pipeline files)
# What a refused approval says, by gate and code (the gate catalogue is closed: a pipeline only names the gate).
GATE_REFUSALS = {
    "ship_ready": {
        "missing": "the report has no “Ready to publish” section: ask for changes so the worker writes it",
        "notexecuted": "the report says the commit was not made: ask for changes or cancel",
        "badpush": "the report's push command is not allowed: only “git push -u <remote name> <branch>” on a branch that is not main or master"},
    "release_plan": {
        "missing": "the promotion plan lacks a required section (Candidate, Regression tests, Promotion steps, Post-checks, Rollback): ask for changes",
        "nosha": "the promotion plan has no “Candidate commit: <sha>” line: ask for changes"},
    "release_verdict": {
        "negative": "the regression verdict is NEGATIVE: the promotion cannot be approved; run the tests again or cancel",
        "unknown": "the regression report has no readable POSITIVE or NEGATIVE verdict: run the tests again",
        "mismatch": "the commit that was tested is not the planned candidate: run the tests again"}}
MAX_LIMIT_RESUMES = 3            # how many times a request's phase is restarted after a usage limit
LIMIT_GRACE_S = 180              # after a limit resets, wait this long for Claude Code to continue by itself before restarting
MAX_QUESTIONS, MAX_OPTIONS, MAX_Q_TITLE, MAX_Q_TEXT, MAX_Q_OPTION = 20, 8, 120, 1500, 300
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")

def _clip(value, limit: int) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def load_questions(path):
    """Read and normalise a worker's questions file. None if it is missing or invalid; [] if there are no questions."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    out, seen = [], set()
    for i, q in enumerate(data[:MAX_QUESTIONS]):
        if not isinstance(q, dict):
            return None
        text, title = _clip(q.get("text"), MAX_Q_TEXT), _clip(q.get("title"), MAX_Q_TITLE)
        if not text:  # a question without text cannot be shown
            return None
        qid = re.sub(r"[^A-Za-z0-9_-]", "", _clip(q.get("id"), 20)) or f"Q{i + 1}"
        while qid in seen:
            qid += "-" + str(i + 1)
        seen.add(qid)
        opts = [o for o in (q.get("options") or []) if isinstance(o, (dict, str))][:MAX_OPTIONS]
        options = [{"label": _clip(o.get("label") if isinstance(o, dict) else o, MAX_Q_OPTION),
                    "rec": bool(isinstance(o, dict) and o.get("recommended"))} for o in opts]
        out.append({"id": qid, "title": title or text[:60], "text": text, "options": [o for o in options if o["label"]]})
    return out


from gates import (parse_review, parse_tests, ship_gate, release_plan_gate, release_verdict, release_gate,  # noqa: F401,E402
                   review_tests_gate, SHIP_PUSH_OK, SHIP_DEFAULT_BRANCHES, RELEASE_SECTIONS, RELEASE_SHA)



_TASKS = re.compile(r"^\W*(?:TASKS|TAREFAS)\s*:\s*(\d{1,4})\b", re.I | re.M)


def plan_tasks(plan: str):
    """How many ordered steps a plan says it has (its «TASKS: <n>» line), or None if it does not say."""
    m = _TASKS.search(plan or "")
    return int(m.group(1)) if m else None


def _same_path(a, b) -> bool:
    norm = lambda x: str(x or "").replace("\\", "/").rstrip("/").lower()   # noqa: E731
    return bool(a) and norm(a) == norm(b)


def open_requests(feats: list, project: str) -> int:
    """How many requests of a project are still open (not finished and not cancelled)."""
    return sum(1 for f in feats if f.get("project") == project and f.get("status") not in (DONE, CANCELLED))


def request_from_todo(item: dict, missing_label: str) -> tuple:
    """(title, description) of the request a session to-do becomes. A to-do's title may be longer than a request's, and it may
    have no summary: the title is cut and the description falls back to the title, so the request can always be created."""
    title = " ".join(str(item.get("title") or "").split())
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE - 1].rstrip() + "…"
    summary, missing = str(item.get("summary") or "").strip(), str(item.get("missing") or "").strip()
    desc = summary + (f"\n\n{missing_label}: {missing}" if missing else "") if summary else (f"{missing_label}: {missing}" if missing else "")
    return title, (desc or title)[:MAX_DESC]


class Features(CheckFlow):
    def __init__(self, root, launcher, is_active, grace_s: int = 0, is_busy=None, on_release=None,
                 auto_review: bool = False, committer=None, publisher=None, note_lang: str = "English", has_vault: bool = True,
                 session=None, on_launch=None, limit=None, route=None, tools=None, route_step=None, tools_for_step=None,
                 live_known=None, cleaner=None):
        """is_active: the worker session exists; is_busy: it is really working (defaults to is_active).

        A worker may finish the phase and keep the session open waiting for input: active, but not busy."""
        self.root = Path(root)
        self._grace = grace_s
        self._is_busy = is_busy or is_active
        self._on_release = on_release  # called with the id of a worker that is no longer needed (e.g. stop the session)
        self._launch = launcher
        self._is_active = is_active
        self._auto_review = auto_review  # the review starts by itself and serious problems go back to the implementer
        self._committer = committer      # committer(worktree: Path, message: str) -> (ok: bool, info: str)
        self._publisher = publisher      # publisher(kind, project, title, fid, created, text, lang) -> sentence; raises on failure
        self._note_lang = note_lang      # language of the notes left in the vault (the vault's own)
        self._has_vault = has_vault      # the vault is optional: without one the prompts say so
        self._session_id = session or (lambda: "")    # id of the open work session (what "Done" and the history are scoped to)
        self._tools = tools              # tools(kind, stage) -> (allow, deny): narrow permission rules for the phases that run git commands
        self._route = route              # route(phase, request_model) -> (model, effort): which model and effort a phase runs with
        self._route_step = route_step    # route_step(step, request_model) -> (model, effort); wins over `route` when given
        self._tools_for_step = tools_for_step  # tools_for_step(step, kind) -> (allow, deny); wins over `tools` when given
        self._pipes = {}                 # request id -> (Pipeline, Engine)
        self._on_launch = on_launch      # on_launch(worker_id, request=, phase=): lets the cost of a worker be attributed to a request
        self._limit = limit              # limit(worker_id) -> the usage-limit notice stopping that worker, or None
        self._live_known = live_known or (lambda: True)   # False while the host cannot ask Claude Code for its sessions
        self._cleaner = cleaner          # cleaner(project_path, worktree, branch, force) -> (ok, info): removes what a request left in git
        self._lock = threading.RLock()

    # ---- persistence ----
    @property
    def _file(self) -> Path:
        return self.root / "features.json"

    def _load(self) -> list:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a missing or corrupt file means no features yet
            return []

    def _save(self, fs: list) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        atomicio.write_text(self._file, json.dumps(fs, ensure_ascii=False, indent=1))

    def _dir(self, fid: str) -> Path:
        return self.root / "features" / fid

    def _get(self, fs: list, fid: str) -> dict:
        if not isinstance(fid, str) or not ID_RE.match(fid):
            raise FeatureError("invalid id")
        for f in fs:
            if f["id"] == fid:
                return f
        raise FeatureError("feature not found")

    # ---- reading ----
    def list(self) -> list:
        with self._lock:
            fs = self._load()
            changed = self._refresh(fs)
            if self._note_limits(fs) or changed:
                self._save(fs)
            return fs

    def _refresh(self, fs: list) -> bool:
        changed = False
        for f in fs:
            if self._settle_qa(f):
                changed = True
            if f["status"] not in (WORKING, FAILED) or (f["status"] == WORKING and self._recent(f)):
                continue
            if f["status"] == FAILED and not self._live_known():
                continue            # the sessions cannot be listed right now: «active» would be a guess, and the request would flip
            new = self._settle(f)
            if new != f["status"]:
                f["status"] = new
                self._log(f, {AWAITING: "finished", FAILED: "failed", WORKING: "worker still active"}[new], f["stage"])
                changed = True
                if new == AWAITING and self._auto_review:
                    self._advance(fs, f)
        return changed

    # ---- usage limits: a subscription's allowance can run out in the middle of a phase ----
    def _note_limits(self, fs: list) -> bool:
        """Remember which requests a usage limit has interrupted and until when; forget it once they carry on."""
        if not self._limit:
            return False
        changed = False
        for f in fs:
            if f["status"] not in (WORKING, FAILED):
                if f.pop("paused", None):
                    changed = True
                continue
            n = self._limit(f.get("worker_id", ""))
            if n and n.get("resume_at"):
                new = {"until": n["resume_at"], "kind": n.get("kind", "session"), "auto": bool(n.get("auto"))}
                if f.get("paused") != new:
                    if not f.get("paused"):
                        self._log(f, "paused by usage limit", f["stage"], new["kind"])
                    f["paused"] = new
                    changed = True
            elif f.get("paused") and f["status"] == WORKING and not n:        # the worker carried on by itself
                f.pop("paused")
                changed = True
        return changed

    def auto_resume(self, now: datetime | None = None) -> list:
        """Restart the phases a usage limit interrupted, once the limit has reset (and Claude Code had a moment to continue by
        itself). Each request is restarted at most MAX_LIMIT_RESUMES times. Returns the ids restarted."""
        now = now or datetime.now()
        restarted, dirty = [], False
        with self._lock:
            fs = self._load()
            for f in fs:
                p = f.get("paused")
                if not p or f["status"] not in (WORKING, FAILED) or f.get("limit_resumes", 0) >= MAX_LIMIT_RESUMES:
                    continue
                if now < datetime.fromisoformat(p["until"]) + timedelta(seconds=LIMIT_GRACE_S):
                    continue
                if f["status"] == WORKING and self._limit and self._limit(f.get("worker_id", "")) is None:
                    continue                                                   # it is moving again: leave it alone
                f["limit_resumes"] = f.get("limit_resumes", 0) + 1
                f.pop("paused", None)
                dirty = True
                self._log(f, "resumed after usage limit", f["stage"], f"try {f['limit_resumes']} of {MAX_LIMIT_RESUMES}")
                try:
                    if f.get("pv", {}).get("checking"):
                        self._start_plancheck(fs, f)
                    else:
                        self._start(fs, f, self._pending_feedback(f))
                    restarted.append(f["id"])
                except FeatureError as e:
                    f["status"] = FAILED
                    self._log(f, "resume failed", f["stage"], str(e))
            if dirty:
                self._save(fs)
        return restarted

    # ---- the request's pipeline: a definition (data) interpreted by the engine ----
    def _pipe(self, f: dict):
        """(Pipeline, Engine) of a request: the snapshot it was created with, else the built-in pipeline of its type (data from
        before pipelines were files)."""
        got = self._pipes.get(f["id"])
        if got:
            return got
        p = None
        snap = self._dir(f["id"]) / PIPELINE_SNAPSHOT
        if snap.is_file():
            try:
                data = json.loads(snap.read_text(encoding="utf-8"))
                p = pipelines.load(data, builtin=data.get("id") in pipelines.BUILTIN_IDS)
            except (OSError, ValueError, pipelines.PipelineError):
                p = None
        if p is None:
            kind = f.get("kind", "feature")
            kind = kind if kind in pipelines.BUILTIN_IDS else "feature"
            p = pipelines.load(pipelines.builtin_dir() / f"{kind}.json", builtin=True)
        self._pipes[f["id"]] = got = (p, engine.Engine(p))
        return got

    def _step_by_phase(self, f: dict, phase: str):
        return next((s for s in self._pipe(f)[0].steps if s["type"] != "approval" and s.get("phase") == phase), None)

    def _stage_step(self, f: dict):
        """The worker or check step of the phase the request is at (ignoring a running plan validation)."""
        return self._step_by_phase(f, f["stage"])

    def _cur(self, f: dict):
        """The step the request is on: the plan validation while it runs, else the step of its stage."""
        return self._step_by_phase(f, "plan-validation" if f.get("pv", {}).get("checking") else f["stage"])

    def _rounds(self, f: dict) -> dict:
        """f["rounds"]: automatic rounds used, by `<step>:<verdict>`. Requests from before it existed get it from pv / auto."""
        if "rounds" not in f:
            pv, auto = f.get("pv", {}), f.get("auto", {})
            ids = {st["id"] for st in self._pipe(f)[0].steps}
            old = {"plancheck:blocked": pv.get("fixes", 0), "plancheck:unknown": pv.get("retries", 0),
                   "review:blocked": auto.get("fixes", 0), "review:unknown": auto.get("retries", 0)}
            f["rounds"] = {k: v for k, v in old.items() if v and k.split(":")[0] in ids}
        return f["rounds"]

    def _mirror(self, f: dict) -> None:
        """Keep the older pv / auto counters (what the page and the project view read) equal to the engine's rounds."""
        r = self._rounds(f)
        for st in self._pipe(f)[0].steps:
            if st["type"] != "check":
                continue
            counts = {"fixes": r.get(f"{st['id']}:blocked", 0), "retries": r.get(f"{st['id']}:unknown", 0)}
            if st.get("phase") == "plan-validation":
                self._pv(f).update(counts)
            elif st.get("phase") == "review":
                f.setdefault("auto", {}).update(counts)

    def _advance(self, fs: list, f: dict) -> None:
        """A worker finished: when the next step is a check it starts by itself, when it is an approval the user decides."""
        step = self._cur(f)
        if step is None:
            return
        if step["type"] == "check":
            self._after_check(fs, f, step)
            return
        d = self._pipe(f)[1].on_worker_finished(f, step)
        nxt = self._pipe(f)[0].step(d.step) if d.kind == "launch" else None
        if nxt and nxt["type"] == "check":
            self._begin_check(fs, f, nxt)
        elif f["stage"] == "spec":
            self._autopilot(fs, f)

    # ---- «run until the review»: the user's choice, per request, to skip the approval clicks that carry no risk ----
    def _autopilot(self, fs: list, f: dict, validated: bool = True) -> None:
        """Approve a finished spec or plan by itself when the request asked for it. Stops (the user decides, as always) when the
        worker left questions, when the plan was not validated, at the last phase and for every publish or production request."""
        step = self._stage_step(f)
        if (not f.get("autopilot") or self._pipe(f)[0].id in pipelines.NO_AUTOPILOT or f["stage"] not in AUTOPILOT_STAGES
                or f["status"] != AWAITING or step is None or self._next_after_approval(f, step).kind != "launch"):
            return                                # never the last phase: accepting the implementation is the user's
        if f["stage"] == "plan" and fanout.autopilot_blocks(f, self._read_artifact(f, "plan")):
            self._log(f, "autopilot stopped", f["stage"], "the plan has a split: approve it yourself to fan out")
            return
        if not validated:
            self._log(f, "autopilot stopped", f["stage"], "the plan was not validated: read it and decide")
            return
        if self.questions(f["id"]) != []:        # open questions, or no readable questions file: only the user can answer
            self._log(f, "autopilot stopped", f["stage"], "the worker left questions for you")
            return
        self._log(f, "approved automatically", f["stage"], "run until the review")
        try:
            self._approve(fs, f)
        except FeatureError as e:
            self._log(f, "autopilot stopped", f["stage"], str(e))

    def _read_artifact(self, f: dict, stage: str) -> str:
        p = self._dir(f["id"]) / ARTIFACT[stage]
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

    # ---- the integration request: one analysis request that merges the parts' branches and tests them together ----
    CLEANUP_BACKOFF_S = 600

    def maybe_integrate(self) -> None:
        """Create the integration request of a fanned-out parent once every non-cancelled part is ready. Three steps so the lock
        is never held during git: reserve the id (a second tick then does nothing), merge the branches, create the request."""
        reserved = self._reserve_integration()
        if not reserved:
            return
        parent_id, fid, path, ready, kw = reserved
        error = self._combine_parts(fid, path, ready)
        with self._lock:
            fs = self._load()
            parent = self._get(fs, parent_id)
            if error:
                shutil.rmtree(self._dir(fid), ignore_errors=True)
                parent["split"].update(integration="", integration_error=error)
                self._log(parent, "integration failed", parent["stage"], error)
            else:
                self._create_integration(fs, parent, fid, ready, kw)
            self._save(fs)

    def _reserve_integration(self):
        with self._lock:
            fs = self._load()
            hit = fanout.due_parent(fs, _now(), lambda fid: bool(fid) and (self._dir(fid) / "integration").exists())
            if not hit:
                return None
            parent, due = hit
            split = parent["split"]
            if not due["due"]:                          # a reservation that never became a request (the server died in between)
                split["integration_error"] = due["reason"]
                self._log(parent, "integration failed", parent["stage"], due["reason"])
                self._save(fs)
                return None
            fid = fanout.reserve_id(parent["id"], {x["id"] for x in fs}, datetime.now())
            split["integration"], split["integration_reserved_at"] = fid, _now()
            kw = fanout.integration_kwargs(parent, due, fs)
            self._save(fs)
            return parent["id"], fid, parent.get("path", ""), due["ready"], kw

    def _combine_parts(self, fid: str, path: str, ready: list) -> str:
        """Merge the parts' branches into `<request>/integration/` and keep what the worker reads next to it. "" or the error."""
        d = self._dir(fid)
        try:
            if not path:
                raise combine.CombineError("the project's folder is not known")
            d.mkdir(parents=True, exist_ok=True)
            result = combine.run(path, [f"feat/{i}" for i in ready], str(d / "integration"))
            (d / "combine.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            (d / "parts").mkdir(exist_ok=True)
            for i in ready:
                for name in ("review.md", "report.md"):
                    src = self._dir(i) / name
                    if src.is_file():
                        shutil.copyfile(src, d / "parts" / f"{i}.{name}")
            return ""
        except (combine.CombineError, OSError) as e:
            if path:
                combine.remove(path, str(d / "integration"))
            return str(e) or "the parts could not be combined"

    def _create_integration(self, fs: list, parent: dict, fid: str, ready: list, kw: dict) -> None:
        by_id = {x["id"]: x for x in fs}
        d = self._dir(fid)
        info = {"title": parent["title"], "parts": [
            {"id": i, "title": by_id[i]["title"], "branch": f"feat/{i}", "report": str(d / "parts" / f"{i}.review.md")} for i in ready]}
        f = self._new_request(fs, kw.pop("project"), kw.pop("title"), kw.pop("desc"), kw.pop("model"), kind="analysis", fid=fid,
                              start=False, extra={"integration": True, "int": info}, **kw)
        try:
            self._start(fs, f, "")
        except Exception as e:  # noqa: BLE001 - the request is kept (failed) so «Try again» works
            f["status"] = FAILED
            self._log(f, "integration launch failed", f["stage"], str(e))
        self._log(parent, "integration created", parent["stage"], fid)

    def _integration_task(self, f: dict, feedback: str) -> str:
        """The integration worker's prompt: it runs the suite in the merged worktree and reports; it is not the read-only analysis."""
        P, d = self._prompts(f), self._dir(f["id"])
        info = f["int"]
        text = P.language_rule(self._note_lang) + prompts_integration.render(
            self._lang(f), {"id": f["parent"], "title": info["title"]}, info["parts"], str(d / "combine.json"),
            str(d / "integration"), str(d / ARTIFACT["build"]), str(self._dir(f["parent"]) / ARTIFACT["plan"])) + P.BRIEF
        if not self._has_vault:
            text += P.NO_VAULT_NOTE
        if feedback:
            prev = d / f"{Path(ARTIFACT['build']).stem}.v{f['rev'].get('build', 0)}.md"
            text += P.CHANGES_BLOCK.format(feedback=feedback, prev=prev, path=d / ARTIFACT["build"])
        return text

    def remove_integration_worktrees(self) -> None:
        """Remove `integration/` of integration requests that are finished or cancelled. A removal that fails (a lock on Windows)
        is logged once and not tried again for ten minutes; this never raises into the ticker."""
        try:
            with self._lock:
                fs, changed = self._load(), False
                for f in fs:
                    wt = self._dir(f["id"]) / "integration"
                    if not f.get("integration") or f["status"] not in (DONE, CANCELLED) or not wt.exists():
                        continue
                    failed = f.get("integration_cleanup_failed_at")
                    if failed and (datetime.now() - datetime.fromisoformat(failed)).total_seconds() < self.CLEANUP_BACKOFF_S:
                        continue
                    combine.remove(f.get("path", ""), str(wt))
                    if wt.exists():
                        if not failed:
                            self._log(f, "integration cleanup failed", f["stage"], "the worktree could not be removed; it will be tried again")
                        f["integration_cleanup_failed_at"] = _now()
                        changed = True
                if changed:
                    self._save(fs)
        except Exception:  # noqa: BLE001 - housekeeping must never bring the server's ticker down
            pass

    def _split_decision(self, f: dict) -> dict:
        """What approving this plan means (`fanout.decide`); a refusal raises, so it leaves no log and no archive behind."""
        d = fanout.decide(f, self._read_artifact(f, "plan"), self.questions(f["id"]), splitplan.repo_files(f.get("path", "")))
        if d["action"] == "refuse":
            raise FeatureError(d["reason"])
        return d

    def _split_parts(self, fs: list, f: dict, split: dict) -> list:
        return fanout.build_parts(f, split, self._read_artifact(f, "spec"), {x["id"] for x in fs}, datetime.now(), self._validate_request_fields)

    def _fan_out(self, fs: list, f: dict, parts: list, split: dict) -> None:
        """Create and start one request per part, then close the parent (it leads to its parts). A part that cannot start is
        `failed` on its own and can be retried; the others keep running."""
        made = []
        for p in parts:
            kw = dict(p)
            spec = kw.pop("spec")
            child = self._new_request(fs, kw.pop("project"), kw.pop("title"), kw.pop("desc"), kw.pop("model"), start=False, **kw)
            if spec:
                (self._dir(child["id"]) / ARTIFACT["spec"]).write_text(spec, encoding="utf-8")
            try:
                self._start(fs, child, "")
            except Exception as e:  # noqa: BLE001 - one part failing to launch never stops the others
                child["status"] = FAILED
                self._log(child, "fan-out launch failed", child["stage"], str(e))
            made.append(child)
        f["split"] = {"tasks": [{**t, "part_id": c["id"]} for t, c in zip(split["tasks"], made)], "status": "fanned-out",
                      "integration": "", "integration_error": ""}
        f["status"], f["closed"] = DONE, self._closed()
        self._log(f, "fanned out", f["stage"], f"{len(made)} parts")
        self._release(f["worker_id"])

    def _tests_compared(self, f: dict) -> bool:
        """Did the gate really compare two results? Not when there is code, the reviewer gave numbers and the report gave none."""
        d = self._dir(f["id"])
        if not (d / "work").is_dir():
            return True
        read = lambda name: (d / name).read_text(encoding="utf-8", errors="replace") if (d / name).is_file() else ""  # noqa: E731
        state, got = parse_tests(read(ARTIFACT["review"]))
        return state == "notests" or "reviewer" not in got or "final" in parse_tests(read(ARTIFACT["build"]))[1]

    def _settle(self, f: dict) -> str:
        """The phase's real state: artifact written and worker stopped = waiting for a decision."""
        wid = f["worker_id"]
        name = PLAN_REVIEW if f.get("pv", {}).get("checking") else ARTIFACT[f["stage"]]
        has = (self._dir(f["id"]) / name).is_file()
        if has and not self._is_busy(wid):
            return AWAITING
        return WORKING if self._is_active(wid) else FAILED

    def _recent(self, f: dict) -> bool:
        if not self._grace or not f["history"]:
            return False
        last = datetime.fromisoformat(f["history"][-1]["at"])
        return (datetime.now() - last).total_seconds() < self._grace

    def questions(self, fid: str):
        """Structured questions the worker wrote for the current phase (None if missing or invalid)."""
        return load_questions(self._dir(fid) / QUESTIONS_FILE)

    def artifacts(self, fid: str) -> dict:
        out = {}
        for stage, name in ARTIFACT.items():
            p = self._dir(fid) / name
            if p.is_file():
                t = p.read_text(encoding="utf-8", errors="replace")
                out[stage] = t if len(t) <= MAX_ARTIFACT else t[:MAX_ARTIFACT] + "\n… (truncated)"
        p = self._dir(fid) / PLAN_REVIEW
        if p.is_file():
            t = p.read_text(encoding="utf-8", errors="replace")
            out["planreview"] = t if len(t) <= MAX_ARTIFACT else t[:MAX_ARTIFACT] + "\n… (truncated)"
        return out

    # ---- questions to the worker: a read-only worker answers about the request; the request itself never changes ----
    def _answer_file(self, fid: str, n: int) -> Path:
        return self._dir(fid) / QA_DIR / f"{n}.md"

    def qa(self, fid: str) -> list:
        """The questions asked about a request, oldest first, each with its answer text ('' until there is one)."""
        return self.qa_of(next((f for f in self._load() if f["id"] == fid), {"id": fid}))

    def qa_of(self, f: dict) -> list:
        """Same, for a request already in hand (the state builder has them all loaded: no second read of features.json)."""
        out = []
        for e in f.get("qa", []):
            p = self._answer_file(f["id"], e["n"])
            text = p.read_text(encoding="utf-8", errors="replace") if e["status"] == "answered" and p.is_file() else ""
            out.append({**e, "answer": text if len(text) <= MAX_ARTIFACT else text[:MAX_ARTIFACT] + "\n… (truncated)"})
        return out

    def _settle_qa(self, f: dict) -> bool:
        """Moves a question whose worker has stopped to answered (the answer file exists) or failed (it does not)."""
        changed = False
        for e in f.get("qa", []):
            if e["status"] != "working":
                continue
            wid = e["worker_id"]
            if self._answer_file(f["id"], e["n"]).is_file() and not self._is_busy(wid):
                e["status"] = "answered"
                self._release(wid)
            elif not self._is_busy(wid) and not self._grace_left(e["at"]):
                e["status"] = "failed"          # stopped, idle or waiting for input, and no answer: it will not come
                self._release(wid)
            else:
                continue
            changed = True
        return changed

    def _grace_left(self, at: str) -> bool:
        return bool(self._grace) and (datetime.now() - datetime.fromisoformat(at)).total_seconds() < self._grace

    def _qa_task(self, f: dict, n: int, question: str) -> str:
        d, lang, P = self._dir(f["id"]), self._lang(f), self._prompts(f)
        names = list(ARTIFACT.values()) + [PLAN_REVIEW]
        files = [f"- {d / name}" for name in names if (d / name).is_file()]
        if (d / "work").is_dir():
            files.append(f"- {d / 'work'} (the implementation's git worktree, uncommitted)")
        history = ""
        done = [e for e in f.get("qa", []) if e["status"] == "answered"][-5:]
        if done:
            parts = []
            for e in done:
                p = self._answer_file(f["id"], e["n"])
                a = p.read_text(encoding="utf-8", errors="replace")[:MAX_QA_CONTEXT] if p.is_file() else ""
                parts.append(f"Q{e['n']}: {e['q']}\nA{e['n']}: {a}\n")
            history = prompts_qa.HISTORY_HEAD[lang] + "\n".join(parts)
        text = P.language_rule(self._note_lang) + prompts_qa.TASK[lang].format(
            title=f["title"], kind=f.get("kind", "feature"), fid=f["id"], project=f["project"], desc=f["desc"],
            read_only=prompts_qa.READ_ONLY[lang], files="\n".join(files) or prompts_qa.NO_FILES[lang],
            history=history, question=question, out=self._answer_file(f["id"], n))
        return text + P.BRIEF + ("" if self._has_vault else P.NO_VAULT_NOTE)

    def ask(self, fid: str, question: str) -> dict:
        """Launches a read-only worker that answers a question about this request. Any kind, any phase, any status."""
        question = (question or "").strip()
        if not question or len(question) > MAX_QUESTION:
            raise FeatureError(f"question required (max. {MAX_QUESTION} characters)")
        with self._lock:
            fs = self._load()
            f = self._get(fs, fid)
            self._settle_qa(f)
            if any(e["status"] == "working" for e in f.get("qa", [])):
                raise FeatureError("the previous question is still being answered")
            n = max((e["n"] for e in f.get("qa", [])), default=0) + 1
            d = self._dir(fid)
            (d / QA_DIR).mkdir(parents=True, exist_ok=True)
            try:
                w = self._spawn(f, "qa", self._qa_task(f, n, question), [str(d)], f"{_slug(f['title'])[:24].strip('-')}-q{n}")
            except Exception as e:
                raise FeatureError(f"could not launch the worker: {e}") from e
            self._tag(w, f, "qa")
            entry = {"n": n, "q": question, "at": _now(), "worker_id": w["id"], "worker_name": w.get("name", ""),
                     "status": "working"}
            f.setdefault("qa", []).append(entry)
            self._save(fs)
            return entry

    # ---- actions ----
    def create(self, project: str, title: str, desc: str, model, lang: str = "en", kind: str = "feature",
               autopilot: bool = False, path: str = "", parent: str = "") -> dict:
        """`path`: the project's folder, kept so the request still finds its project if the project's name changes.
        `parent`: the id of the request this one follows (a part split from a plan, the fix an analysis asked for)."""
        title, desc, kind = self._validate_request_fields(project, title, desc, model, lang, kind)
        with self._lock:
            fs = self._load()
            if parent and not any(x["id"] == parent for x in fs):
                raise FeatureError("the request this one follows was not found")
            f = self._new_request(fs, project, title, desc, model, lang=lang, kind=kind, path=path, parent=parent,
                                  autopilot=autopilot)
            self._save(fs)
            return f

    @staticmethod
    def _validate_request_fields(project: str, title: str, desc: str, model, lang: str, kind: str) -> tuple:
        """The field checks of a new request, shared by `create` and by the parts of a split (a bad part fails before anything
        is written). Returns the cleaned (title, description, kind)."""
        title, desc = (title or "").strip(), (desc or "").strip()
        if not project:
            raise FeatureError("missing project")
        if not title or len(title) > MAX_TITLE:
            raise FeatureError(f"title required (max. {MAX_TITLE} characters)")
        if not desc or len(desc) > MAX_DESC:
            raise FeatureError(f"description required (max. {MAX_DESC} characters)")
        if model and not MODEL_RE.match(str(model)):
            raise FeatureError("invalid model name")
        if (lang or "en") not in LANGS:
            raise FeatureError("unknown language")
        if (kind or "feature") not in KINDS:
            raise FeatureError("unknown request type")
        return title, desc, kind or "feature"

    def _new_request(self, fs: list, project: str, title: str, desc: str, model, *, lang: str = "en", kind: str = "feature",
                     path: str = "", parent: str = "", autopilot: bool = False, fid: str = "", stage: str = "",
                     extra: dict | None = None, start: bool = True) -> dict:
        """Build a request, append it to `fs` (the caller's loaded list) and return it. It never loads or saves the file: the
        caller holds the lock and saves, so two updates cannot be lost. `start=False` leaves it waiting (status `awaiting`)
        for the caller to start; `stage` begins at that phase; `extra` keys are merged before the request starts."""
        fid = fid or f"{_slug(title)}-{datetime.now():%y%m%d%H%M%S}"
        f = {"id": fid, "title": title, "desc": desc, "project": project, "model": model or "",
             "lang": lang or "en", "kind": kind, "status": WORKING, "worker_id": "", "worker_name": "",
             "created": _now(), "history": [], "rev": {s: 0 for s in STAGES}, "auto": {"fixes": 0, "retries": 0}}
        if autopilot and kind not in pipelines.NO_AUTOPILOT and not (extra or {}).get("part"):
            f["autopilot"] = True
        if path:
            f["path"] = str(path)
        if parent:
            f["parent"] = parent
        f.update(extra or {})
        self._dir(fid).mkdir(parents=True, exist_ok=True)
        self._attach_pipeline(f, kind)
        if stage:
            step = self._step_by_phase(f, stage)
            if step is None:
                raise FeatureError(f"this type of request has no {stage} phase")
            f.update(stage=stage, step=step["id"])
        if start:
            self._start(fs, f, "")
            fs.append(f)
        else:
            f["status"] = AWAITING
            fs.append(f)
        return f

    def approve(self, fid: str) -> dict:
        with self._lock:
            fs = self._load()
            f = self._get(fs, fid)
            self._need(f, AWAITING)
            self._approve(fs, f, by_user=True)
            self._save(fs)
            return f

    def _approve(self, fs: list, f: dict, by_user: bool = False) -> None:
        """Check the phase's gates and move the request on. `by_user`: the click (the automatic approval logs its own event)."""
        step = self._stage_step(f)
        self._check_report_gates(f, step)
        decision = parts = None
        if by_user and f["stage"] == "plan":          # a plan with a split: refuse or fan out, before anything is logged or written
            decision = self._split_decision(f)
            if decision["action"] == "fan_out":
                parts = self._split_parts(fs, f, decision["split"])
        if by_user:
            self._log(f, "approved", f["stage"])
        self._publish(f, f["stage"])
        if parts is not None:
            self._fan_out(fs, f, parts, decision["split"])
            return
        d = self._next_after_approval(f, step)
        if d.kind != "launch":
            f["status"] = DONE
            f["closed"] = self._closed()
            self._release(f["worker_id"])
            return
        approved = f["stage"]
        f["stage"] = self._pipe(f)[0].step(d.step)["phase"]
        try:
            self._start(fs, f, "")
        except FeatureError:
            f["stage"] = approved        # the next phase did not start: the request waits on the approved one, never on a phase without its artifact
            raise

    def _check_report_gates(self, f: dict, step) -> None:
        """The click on «Approve» authorises what the report says: the step's named gates must accept it."""
        gate = (step or {}).get("report_gate")
        d = self._dir(f["id"])
        ctx = gates.GateCtx(d, (d / "work").is_dir())
        for g in ([gate] if isinstance(gate, dict) else (gate or [])):
            res = gates.run(g["use"], g.get("args", {}), ctx)
            if res.code != "ok":
                raise FeatureError(GATE_REFUSALS.get(g["use"], {}).get(res.code) or res.message or f"gate {g['use']}: {res.code}")

    def _next_after_approval(self, f: dict, step) -> "engine.Decision":
        """What follows the user's approval of the step the request is at: the approval step that names it, else the next step."""
        p, eng = self._pipe(f)
        if step is None:
            return engine.Decision("done")
        ap = next((s for s in p.steps if s["type"] == "approval" and s.get("of") == step["id"]), None)
        return eng.on_approve(f, ap) if ap else eng.advance(step)

    def request_fixes(self, fid: str, comment: str = "", selected: bool = False) -> dict:
        """After the review: send the findings back to the implementation worker (which continues in the same worktree)."""
        comment = (comment or "").strip()
        if len(comment) > MAX_COMMENT:
            raise FeatureError(f"comment too long (max. {MAX_COMMENT} characters)")
        with self._lock:
            fs = self._load()
            f = self._get(fs, fid)
            self._need(f, AWAITING)
            if f["stage"] != "review":
                raise FeatureError("fixes can only be requested after the review")
            self._send_fixes(fs, f, comment, automatic=False, selected=selected)
            self._save(fs)
            return f

    def _send_fixes(self, fs: list, f: dict, comment: str, automatic: bool, selected: bool = False) -> None:
        """Send the review findings back to the implementer (same worktree) and return to the implementation phase."""
        review = self._dir(f["id"]) / ARTIFACT["review"]
        f.pop("commit", None)        # back at work: the earlier commit no longer describes the request
        self._log(f, "automatic fixes" if automatic else "fixes requested", "review", comment)
        p = self._pipe(f)[0]
        cur = self._stage_step(f)
        back = ((cur or {}).get("on") or {}).get("blocked")
        target = back.get("goto") if isinstance(back, dict) else None
        if not target:       # a step without a fix loop: the closest worker step before it
            i = p.steps.index(cur) if cur in p.steps else len(p.steps)
            target = next(s["id"] for s in reversed(p.steps[:i]) if s["type"] == "worker")
        tstep = p.step(target)
        f["stage"] = tstep["phase"]
        P = self._prompts(f)
        if tstep.get("mode") == "read_only":       # the work is a report, not code: the findings go back as a report review
            feedback = prompts_kinds.FEEDBACK_ANALYSIS[self._lang(f)].format(review=review)
        else:
            feedback = (P.FEEDBACK_SELECTED if selected else P.FEEDBACK_REVIEW).format(review=review)
        extra = P.USER_INSTRUCTIONS.format(comment=comment) if comment else ""
        self._start(fs, f, feedback + extra)

    def revise(self, fid: str, comment: str) -> dict:
        comment = (comment or "").strip()
        if not comment or len(comment) > MAX_COMMENT:
            raise FeatureError(f"comment required (max. {MAX_COMMENT} characters)")
        with self._lock:
            fs = self._load()
            f = self._get(fs, fid)
            self._need(f, AWAITING)
            f.pop("commit", None)
            self._log(f, "changes requested", f["stage"], comment)
            check = self._step_by_phase(f, "plan-validation")
            if check and f["stage"] == "plan":
                for v in ("blocked", "unknown"):
                    self._rounds(f).pop(f"{check['id']}:{v}", None)
                self._mirror(f)
            self._start(fs, f, comment)
            self._save(fs)
            return f

    def retry(self, fid: str) -> dict:
        with self._lock:
            fs = self._load()
            f = self._get(fs, fid)
            self._need(f, FAILED)
            if f.get("pv", {}).get("checking"):
                self._start_plancheck(fs, f)
            else:
                self._start(fs, f, self._pending_feedback(f))
            self._save(fs)
            return f

    @staticmethod
    def _pending_feedback(f: dict) -> str:
        """What the phase that is running (or failed, or was stopped by a usage limit) was told to change, so that starting it
        again does not lose it: the feedback kept by `_start` (the user's comment, or the pointer to the review's findings).
        Requests from before that was kept fall back to the history: the last request for changes or fixes, looking back over
        the launches and failures that followed it and stopping at anything else."""
        kept = f.get("feedback")
        if isinstance(kept, dict):
            return kept.get("text", "") if kept.get("stage") == f.get("stage") else ""
        for h in reversed(f.get("history", [])):
            if h["event"] in ("changes requested", "fixes requested"):
                return h.get("note", "") or ""
            if h["event"] not in ("worker launched", "failed", "worker still active"):
                break
        return ""

    def cancel(self, fid: str) -> dict:
        with self._lock:
            fs = self._load()
            f = self._get(fs, fid)
            if f["status"] in (DONE, CANCELLED):
                raise FeatureError("already finished")
            f["status"] = CANCELLED
            f["closed"] = self._closed()
            self._log(f, "cancelled", f["stage"])
            for e in f.get("qa", []):             # a question still being answered stops with its request
                if e["status"] == "working":
                    e["status"] = "failed"
                    self._release(e["worker_id"])
            self._save(fs)
            self._release(f["worker_id"])
            return f

    # ---- a request and its project: the name may change (a vault note is created or renamed), the folder is what identifies it ----
    def reconcile(self, projects: list) -> int:
        """Keep every request attached to its project. A request remembers the project's folder: when a project with that
        folder now has another name, the request follows it; a request from before folders were kept learns its folder from
        the project that still has its name. Returns how many requests changed."""
        by_name = {p["name"]: p for p in projects}
        changed = 0
        with self._lock:
            fs = self._load()
            for f in fs:
                if f.get("path"):
                    now = next((p for p in projects if _same_path(p.get("path"), f["path"])), None)
                    if now and now["name"] != f["project"]:
                        self._log(f, "project renamed", f["stage"], f"{f['project']} -> {now['name']}")
                        f["project"] = now["name"]
                        changed += 1
                elif f["project"] in by_name and by_name[f["project"]].get("path"):
                    f["path"] = str(by_name[f["project"]]["path"])
                    changed += 1
            if changed:
                self._save(fs)
        return changed

    # ---- changing what kind of request this is, before its first phase is approved ----
    def set_kind(self, fid: str, kind: str) -> dict:
        """Turn a request into another kind (a feature that is really a bug fix, an analysis that should be a feature). Only
        while it is on its first phase and waiting or failed: nothing approved is thrown away. The first phase of the new kind
        starts again. Publishing and release requests are never converted, in either direction."""
        with self._lock:
            fs = self._load()
            f = self._get(fs, fid)
            p = self._pipe(f)[0]
            old = p.id
            if kind not in pipelines.CONVERTIBLE or old not in pipelines.CONVERTIBLE:
                raise FeatureError("this kind of request cannot be converted")
            if old == kind:
                raise FeatureError("the request already is of that kind")
            if f["status"] not in (AWAITING, FAILED) or f["stage"] != p.steps[0]["phase"]:
                raise FeatureError("the kind can only be changed while the request waits on its first phase")
            self._log(f, "kind changed", f["stage"], f"{old} -> {kind}")
            before = {k: f.get(k) for k in ("kind", "pipeline", "step", "stage", "rounds")}
            f["kind"] = kind
            self._attach_pipeline(f, kind)      # the request follows the new type's definition from its first step
            f.pop("pv", None)
            try:
                self._start(fs, f, "")
            except FeatureError:
                self._attach_pipeline(f, old)
                f.update(before)
                raise
            self._save(fs)
            return f

    # ---- after the end: the worktree and the branch a request left in the project's repo ----
    def cleanup(self, fid: str, force: bool = False) -> dict:
        """Remove a finished or cancelled request's worktree and branch. Without `force` git refuses uncommitted work and an
        unmerged branch (and says so); `force` is the user's choice to throw the work away."""
        with self._lock:
            fs = self._load()
            f = self._get(fs, fid)
            if f["status"] not in (DONE, CANCELLED):
                raise FeatureError("only a finished or cancelled request can be cleaned up")
            if not self._cleaner or not f.get("path"):
                raise FeatureError("this request's project folder is not known: remove the worktree by hand")
            ok, info = self._cleaner(f["path"], self._dir(fid) / "work", f"feat/{fid}", bool(force))
            self._log(f, "cleaned up" if ok else "clean-up failed", f["stage"], info)
            self._save(fs)
            if not ok:
                raise FeatureError(info)
            return f

    # ---- archive of the approved specs and plans ----
    def _archive_target(self, f: dict, stage: str):
        """(document kind, artifact phase) to archive when `stage` is approved; None if the step archives nothing."""
        step = self._step_by_phase(f, stage)
        doc = (step or {}).get("archive")
        if not doc:
            return None
        return (doc, "build" if "report" in step.get("inputs", []) else stage)

    def _publish(self, f: dict, stage: str) -> None:
        """Approved document (spec, plan or analysis): a copy in the project's docs/ and a draft in the vault. Once per document; never fails the feature."""
        target = self._archive_target(f, stage)
        if not target or not self._publisher:
            return
        doc, art = target
        done, fail = ARCHIVE_EVENTS[doc]
        if any(h["event"] == done and h["stage"] == stage for h in f["history"]):
            return
        path = self._dir(f["id"]) / ARTIFACT[art]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            info = self._publisher(doc, f["project"], f["title"], f["id"], f["created"], text, self._lang(f))
            self._log(f, done, stage, info)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - archiving is an extra (the old chief.py exits with SystemExit)
            self._log(f, fail, stage, str(e))

    def publish_approved(self) -> int:
        """Archive what was approved before the archive existed (idempotent). Return how many new documents."""
        n = 0
        with self._lock:
            fs = self._load()
            for f in fs:
                for stage in ("spec", "plan", "review"):
                    target = self._archive_target(f, stage)
                    approved = any(h["event"] == "approved" and h["stage"] == stage for h in f["history"])
                    if target and approved and not any(h["event"] == ARCHIVE_EVENTS[target[0]][1] for h in f["history"]):
                        before = len(f["history"])
                        self._publish(f, stage)
                        n += len(f["history"]) - before
            if n:
                self._save(fs)
        return n

    # ---- internals ----
    @staticmethod
    def _need(f: dict, status: str) -> None:
        if f["status"] != status:
            raise FeatureError(f"current state «{f['status']}»; this action needs «{status}»")

    @staticmethod
    def _log(f: dict, event: str, stage: str, note: str = "") -> None:
        f["history"].append({"at": _now(), "event": event, "stage": stage, "note": note})

    def _start(self, fs: list, f: dict, feedback: str) -> None:
        d = self._dir(f["id"])
        d.mkdir(parents=True, exist_ok=True)
        stage = f["stage"]
        old = d / ARTIFACT[stage]
        moved = None
        if old.is_file():
            f["rev"][stage] = f["rev"].get(stage, 0) + 1
            moved = d / f"{old.stem}.v{f['rev'][stage]}{old.suffix}"
            old.replace(moved)
        old_q = d / QUESTIONS_FILE
        moved_q = None
        # An analysis goes straight from its report to the verification, so the report's questions stay in place for the
        # user to answer next to the verdict (archiving them would leave the verification panel with no answer boxes).
        steps = self._pipe(f)[0].steps
        cur = self._stage_step(f)
        keep_q = bool(not feedback and cur in steps and steps.index(cur) > 0 and steps[steps.index(cur) - 1].get("keep_questions_on_next"))
        if old_q.is_file() and not keep_q:  # the previous round's questions are archived; the new worker writes its own
            n = f["rev"].get(stage, 0) + (0 if moved else 1)
            while (d / f"questions.{stage}.v{n}.json").exists():       # never over an archive that is already there
                n += 1
            moved_q = d / f"questions.{stage}.v{n}.json"
            old_q.replace(moved_q)
        try:
            w = self._spawn(f, stage, self._task(f, feedback), [str(d)], f"{_slug(f['title'])[:30].strip('-')}-{stage}")
        except Exception as e:
            if moved_q:
                moved_q.replace(old_q)
            if moved:
                moved.replace(old)
            raise FeatureError(f"could not launch the worker: {e}") from e
        self._tag(w, f, stage)
        previous = f["worker_id"]
        f.update(status=WORKING, worker_id=w["id"], worker_name=w.get("name", ""))
        f["feedback"] = {"stage": stage, "text": feedback}     # what this run was told to change: a retry or a resume repeats it
        if "pv" in f:
            f["pv"]["checking"] = False
        f["step"] = cur["id"] if cur else stage
        if previous and previous != w["id"]:
            self._release(previous)  # the previous worker is obsolete: its session is closed (the conversation is kept)
        self._log(f, "worker launched", stage, w.get("name", ""))

    def _closed(self) -> dict:
        """When and in which work session a request was closed (finished or cancelled)."""
        return {"at": _now(), "session": self._session_id()}

    def _spawn(self, f: dict, phase: str, task: str, extra_dirs: list, label: str) -> dict:
        """Launch the worker for one phase of a request with the model and effort the routing gives that phase (by default the
        request's own model and Claude Code's default effort, which is what every phase used before)."""
        step = None if phase == "qa" else self._step_by_phase(f, phase)
        if self._route_step and step:
            model, effort = self._route_step(step, f["model"] or "")
        else:
            model, effort = self._route(phase, f["model"] or "") if self._route else (f["model"] or None, None)
        extra = {"effort": effort} if effort else {}
        kind = f.get("kind", "feature")
        if self._tools_for_step and step:
            allow, deny = self._tools_for_step(step, kind)
        else:
            allow, deny = self._tools(kind, phase) if self._tools else ((), ())
        if allow:
            extra["allow"] = list(allow)
        if deny:
            extra["deny"] = list(deny)
        try:
            return self._launch(f["project"], task, model or None, extra_dirs, label=label, **extra)
        except SystemExit as e:           # a launcher must never end the server's thread: it is a failed launch like any other
            raise RuntimeError(str(e) or "the launcher exited") from e

    def _tag(self, w: dict, f: dict, phase: str) -> None:
        """Tell the host which request and phase a freshly launched worker is for. Never fails the feature."""
        if not self._on_launch or not w.get("id"):
            return
        try:
            self._on_launch(w["id"], request=f["id"], phase=phase)
        except Exception:  # noqa: BLE001 - bookkeeping is an extra
            pass

    def _release(self, worker_id: str) -> None:
        """Never lets a failure while cleaning up sessions break the feature flow."""
        if not (self._on_release and worker_id):
            return
        try:
            self._on_release(worker_id)
        except Exception:  # noqa: BLE001 - cleanup is an extra, it must not fail the feature
            pass

    @staticmethod
    def _lang(f: dict) -> str:
        """The request's language (a missing `lang` is read as Portuguese, as in data from before the English default)."""
        return "en" if f.get("lang", "pt") == "en" else "pt"

    def _prompts(self, f: dict):
        return prompts if self._lang(f) == "en" else prompts_pt

    def _attach_pipeline(self, f: dict, kind: str) -> None:
        """A new request keeps the definition it starts with (a snapshot file and its identity) and starts at its first step."""
        p = pipelines.load(pipelines.builtin_dir() / f"{kind}.json", builtin=True)
        (self._dir(f["id"]) / PIPELINE_SNAPSHOT).write_text(json.dumps(pipelines.snapshot(p), ensure_ascii=False, indent=1), encoding="utf-8")
        self._pipes[f["id"]] = (p, engine.Engine(p))
        first = p.steps[0]
        f.update(pipeline={"id": p.id, "version": p.data.get("version", 1), "hash": pipelines.digest(p)}, step=first["id"],
                 stage=first["phase"], rounds={})

    def _task(self, f: dict, feedback: str) -> str:
        if f.get("integration") and f["stage"] == "build":
            return self._integration_task(f, feedback)
        step = self._stage_step(f)
        ref = ((step or {}).get("prompt") or {}).get("ref")
        if not ref:
            raise FeatureError("this step has no built-in prompt")
        return prompt_refs.render(ref, self._lang(f), {
            "title": f["title"], "fid": f["id"], "project": f["project"], "desc": f["desc"], "dir": self._dir(f["id"]),
            "rev": f["rev"].get(f["stage"], 0), "feedback": feedback, "note_lang": self._note_lang, "has_vault": self._has_vault,
            "part": bool(f.get("part")), "base": f.get("base", "")})
