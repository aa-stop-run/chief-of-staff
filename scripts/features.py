"""The Chief's feature pipeline: spec -> plan -> implementation -> review, with human approval between phases.

Each phase is a worker (`claude --bg`) that writes an artifact in <root>/features/<id>/.
State is kept in <root>/features.json. Nothing advances by itself: every step is an explicit approval.
"""
import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

import atomicio
import prompts
import prompts_kinds
import prompts_pt
import prompts_qa

STAGES = ("spec", "plan", "build", "review")
ARTIFACT = {"spec": "spec.md", "plan": "plan.md", "build": "report.md", "review": "review.md"}
# Any model name the user's Claude Code understands (an alias such as "opus", a full model id, a gateway's model name).
# It is only ever passed as an argument to `claude --model`, never to a shell; this just keeps obvious junk out.
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,79}$")
KINDS = prompts_kinds.KINDS   # feature | fix | analysis | maintenance | ship (each one's phases: prompts_kinds.KIND_STAGES)
LANGS = ("en", "pt")   # language the workers write the artifacts in (the dashboard's when the request was created)
MAX_TITLE, MAX_DESC, MAX_COMMENT, MAX_ARTIFACT = 120, 4000, 6000, 40_000
MAX_AUTO_FIXES, MAX_REVIEW_RETRIES = 3, 2   # automatic fix rounds; retries of a review without a verdict
QUESTIONS_FILE = "questions.json"
QA_DIR = "qa"                 # «Ask the worker»: one answer file per question (qa/<n>.md); the exchange itself is in feature["qa"]
MAX_QUESTION, MAX_QA_CONTEXT = 2000, 3000   # characters; how much of an earlier answer a new question sees
ARCHIVE_EVENTS = {"spec": ("spec archived", "spec archiving failed"), "plan": ("plan archived", "plan archiving failed"),
                  "analysis": ("analysis archived", "analysis archiving failed")}
PLAN_REVIEW = "plan-review.md"   # independent plan validation (a sub-step of the plan phase)
MAX_PLAN_FIXES = 1               # automatic rounds plan -> validation -> plan fix (one: a validator always finds something)
AUTOPILOT_STAGES = ("spec", "plan")   # what «run until the review» may approve by itself; never the last phase, never ship/release
MAX_LIMIT_RESUMES = 3            # how many times a request's phase is restarted after a usage limit
LIMIT_GRACE_S = 180              # after a limit resets, wait this long for Claude Code to continue by itself before restarting
MAX_QUESTIONS, MAX_OPTIONS, MAX_Q_TITLE, MAX_Q_TEXT, MAX_Q_OPTION = 20, 8, 120, 1500, 300
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
WORKING, AWAITING, FAILED, DONE, CANCELLED = "working", "awaiting", "failed", "done", "cancelled"

class FeatureError(Exception):
    pass


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


def parse_review(text: str):
    """(verdict, count by severity) of a review.md. Verdict: approved | reserves | blocked | unknown."""
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for m in re.finditer(r"\*\*\[\s*(CRITICAL|HIGH|MEDIUM|LOW)\s*\]", text or ""):
        counts[m.group(1)] += 1
    m = re.search(r"^#{2,3}\s+(?:veredicto|verdict)[^\n]*\n+(.*?)(?=^#{1,3}\s|\Z)", text or "", re.I | re.M | re.S)
    head = re.sub(r"[*`_]", "", m.group(1)).strip().upper() if m else ""
    if head.startswith(("BLOQUEAD", "BLOCKED")):
        return "blocked", counts
    if re.match(r"COM\s+RESERVAS|WITH\s+RESERVATIONS", head):
        return "reserves", counts
    if head.startswith(("APROVAD", "APPROVED")):
        return "approved", counts
    return "unknown", counts


_RESULT = r"(\d+)\s*(?:passam|passed|pass)\s*[,;]?\s*(\d+)\s*(?:falham|falhas|failed|fail)"
_TEST_KEYS = {"base": r"(?:linha\s+de\s+base|baseline)", "final": r"(?:resultado\s+final|final\s+result)",
              "reviewer": r"(?:resultado\s+do\s+revisor|reviewer\s+result)"}
_TEST_STATES = (("regress", r"(?:COM\s+REGRESS|REGRESSIONS)"), ("ok", r"(?:SEM\s+REGRESS|NO\s+REGRESSIONS)"),
                ("confirmed", r"(?:CONFIRMADOS|CONFIRMED)"), ("diverged", r"(?:DIVERGENTES|DIVERGENT)"),
                ("notrun", r"(?:N[ÃA]O\s+CORRIDOS|NOT\s+RUN)"), ("notests", r"(?:SEM\s+TESTES|NO\s+TESTS)"))


def parse_tests(text: str):
    """(state, numbers) of the "Tests" section of a report or a review.

    State from the first line: ok | regress | confirmed | diverged | notrun | notests | missing. Numbers: {base, final, reviewer}
    as (passed, failed), only those written in the format "KEY: P passed, F failed"."""
    m = re.search(r"^#{2,3}\s+(?:testes|tests)\s*$\n(.*?)(?=^#{1,3}\s|\Z)", text or "", re.I | re.M | re.S)
    if not m:
        return "missing", {}
    body = m.group(1)
    first = next((re.sub(r"[*`_]", "", ln).strip().upper() for ln in body.splitlines() if ln.strip()), "")
    state = next((s for s, rx in _TEST_STATES if re.match(rx, first)), "missing")
    nums = {}
    for key, rx in _TEST_KEYS.items():
        n = re.search(rf"^\W*{rx}\s*:\s*{_RESULT}", body, re.I | re.M)
        if n:
            nums[key] = (int(n.group(1)), int(n.group(2)))
    return state, nums


SHIP_PUSH_OK = re.compile(r"^git push -u [A-Za-z0-9][A-Za-z0-9._-]* [A-Za-z0-9][A-Za-z0-9._/-]*$")
SHIP_DEFAULT_BRANCHES = {"main", "master", "develop", "trunk", "production", "prod", "release"}


def ship_gate(report: str) -> str:
    """Is a prepared commit's report fit to be approved for publication? ok | missing (no «Ready to publish» section) |
    notexecuted (the commit was not made) | badpush (a `git push` that is not `git push -u <remote name> <branch>` on a non-default branch)."""
    m = re.search(r"^#{2,3}[ \t]+Ready to publish[ \t]*$(.*?)(?=^#{2,3}[ \t]|\Z)", report or "", re.M | re.S | re.I)
    if not m:
        return "missing"
    if re.search(r"^#{1,3}[ \t]+Execution status[ \t]*\n+\W*(?:NOT EXECUTED|NÃO EXECUTADO)", report, re.M | re.I):
        return "notexecuted"
    for cmd in re.findall(r"git\s+push\b[^`\n]*", m.group(1)):
        c = " ".join(cmd.split())
        if not SHIP_PUSH_OK.match(c) or c.split()[-1].lower() in SHIP_DEFAULT_BRANCHES:
            return "badpush"
    return "ok"


RELEASE_SECTIONS = ("Candidate", "Regression tests", "Promotion steps", "Post-checks", "Rollback")
RELEASE_SHA = re.compile(r"Candidate commit:\s*`?([0-9a-f]{7,40})\b", re.I)


def release_plan_gate(plan: str) -> str:
    """Is a promotion plan complete enough to be approved? ok | missing (a required section) | nosha (no `Candidate commit: <sha>`)."""
    for name in RELEASE_SECTIONS:
        if not re.search(rf"^#{{2,3}}[ \t]+{re.escape(name)}\b", plan or "", re.M | re.I):
            return "missing"
    return "ok" if RELEASE_SHA.search(plan) else "nosha"


def release_verdict(report: str) -> str:
    """The independent regression's verdict: positive | negative | unknown (missing, unreadable or contradictory)."""
    m = re.search(r"^#{2,3}[ \t]+Regression verdict[ \t]*$(.*?)(?=^#{2,3}[ \t]|\Z)", report or "", re.M | re.S | re.I)
    line = next((x for x in (m.group(1).splitlines() if m else []) if x.strip()), "")
    words = {w.upper() for w in re.findall(r"\b(POSITIVE|NEGATIVE|POSITIVO|NEGATIVO)\b", line, re.I)}
    pos, neg = words & {"POSITIVE", "POSITIVO"}, words & {"NEGATIVE", "NEGATIVO"}
    return "positive" if pos and not neg else "negative" if neg and not pos else "unknown"


def release_gate(plan: str, report: str) -> str:
    """May the promotion be approved? ok | negative | unknown (no readable verdict) | mismatch (the tested commit is not the planned one)."""
    verdict = release_verdict(report)
    if verdict != "positive":
        return verdict
    a, b = RELEASE_SHA.search(plan or ""), RELEASE_SHA.search(report or "")
    if not a or not b:
        return "mismatch"
    x, y = a.group(1).lower(), b.group(1).lower()
    return "ok" if x.startswith(y) or y.startswith(x) else "mismatch"


def review_tests_gate(report: str, review: str) -> str:
    """Do the tests the reviewer ran confirm the report? ok | missing | diverged | notrun | regress."""
    state, got = parse_tests(review)
    if state in ("diverged", "notrun", "regress", "missing"):
        return state
    if state == "notests":
        return "ok"
    if "reviewer" not in got:
        return "missing"                       # "CONFIRMED" without the real result the reviewer got
    final = parse_tests(report)[1].get("final")
    return "diverged" if final and final != got["reviewer"] else "ok"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "feature"


class Features:
    def __init__(self, root, launcher, is_active, grace_s: int = 0, is_busy=None, on_release=None,
                 auto_review: bool = False, committer=None, publisher=None, note_lang: str = "English", has_vault: bool = True,
                 session=None, on_launch=None, limit=None, route=None, tools=None):
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
        self._on_launch = on_launch      # on_launch(worker_id, request=, phase=): lets the cost of a worker be attributed to a request
        self._limit = limit              # limit(worker_id) -> the usage-limit notice stopping that worker, or None
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
                        self._start(fs, f, "")
                    restarted.append(f["id"])
                except FeatureError as e:
                    f["status"] = FAILED
                    self._log(f, "resume failed", f["stage"], str(e))
            if dirty:
                self._save(fs)
        return restarted

    # ---- automatic review: implementation -> review -> (fixes -> review)* -> commit ----
    def _advance(self, fs: list, f: dict) -> None:
        if f.get("kind") in ("ship", "release"):   # commit / push / PR and production promotions: no code review, no plan validation; the user approves each phase
            return
        if f["stage"] == "build":
            f["stage"] = "review"
            self._log(f, "automatic review", "review")
            try:
                self._start(fs, f, "")
            except FeatureError as e:   # stays waiting on the report: the “Send to review” button keeps working
                f["stage"], f["status"] = "build", AWAITING
                self._log(f, "automatic review failed", "build", str(e))
        elif f["stage"] == "review":
            self._after_review(fs, f)
        elif f["stage"] == "plan":
            if f.get("pv", {}).get("checking"):
                self._after_plancheck(fs, f)
            else:
                self._begin_plancheck(fs, f)
        elif f["stage"] == "spec":
            self._autopilot(fs, f)

    # ---- «run until the review»: the user's choice, per request, to skip the approval clicks that carry no risk ----
    def _autopilot(self, fs: list, f: dict, validated: bool = True) -> None:
        """Approve a finished spec or plan by itself when the request asked for it. Stops (the user decides, as always) when the
        worker left questions, when the plan was not validated, at the last phase and for every publish or production request."""
        stages = prompts_kinds.KIND_STAGES[f.get("kind", "feature")]
        if (not f.get("autopilot") or f.get("kind") in ("ship", "release") or f["stage"] not in AUTOPILOT_STAGES
                or f["status"] != AWAITING or stages.index(f["stage"]) + 1 >= len(stages)):
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

    # ---- independent plan validation: plan -> validator -> (plan fixes -> validator)* -> user ----
    @staticmethod
    def _pv(f: dict) -> dict:
        return f.setdefault("pv", {"checking": False, "fixes": 0, "retries": 0, "rev": 0})

    def _begin_plancheck(self, fs: list, f: dict) -> None:
        self._pv(f).update(retries=0)
        try:
            self._start_plancheck(fs, f)
        except FeatureError as e:   # the plan waits for the user, without validation
            self._pv(f)["checking"] = False
            self._log(f, "plan validation failed", "plan", str(e))

    def _start_plancheck(self, fs: list, f: dict) -> None:
        d = self._dir(f["id"])
        pv = self._pv(f)
        old, moved = d / PLAN_REVIEW, None
        if old.is_file():
            pv["rev"] = pv.get("rev", 0) + 1
            moved = d / f"plan-review.v{pv['rev']}.md"
            old.replace(moved)
        lang, kind, P = self._lang(f), f.get("kind", "feature"), self._prompts(f)
        base = P.language_rule(self._note_lang) + P.PLANCHECK
        extra = prompts_kinds.EXTRA.get((lang, kind, "plancheck"))
        if extra:
            base = base.replace(P.OUT_MARKER, extra + "\n" + P.OUT_MARKER, 1)
        task = base.format(title=f["title"], fid=f["id"], project=f["project"], desc=f["desc"],
                           spec=self._spec_ref(f, d), plan=d / ARTIFACT["plan"], out=old) + P.BRIEF
        try:
            w = self._spawn(f, "plan-validation", task, [str(d)], f"{_slug(f['title'])[:30].strip('-')}-validate-plan")
        except Exception as e:
            if moved:
                moved.replace(old)
            raise FeatureError(f"could not launch the validator: {e}") from e
        self._tag(w, f, "plan-validation")
        previous = f["worker_id"]
        pv["checking"] = True
        f.update(status=WORKING, worker_id=w["id"], worker_name=w.get("name", ""))
        if previous and previous != w["id"]:
            self._release(previous)
        self._log(f, "plan validation", "plan", w.get("name", ""))

    def _after_plancheck(self, fs: list, f: dict) -> None:
        path = self._dir(f["id"]) / PLAN_REVIEW
        verdict, counts = parse_review(path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "")
        pv = self._pv(f)
        if verdict == "blocked" and not (counts["CRITICAL"] or counts["HIGH"]) and (counts["MEDIUM"] or counts["LOW"]):
            verdict = "reserves"             # only minor findings: they inform the user, they do not send the plan back
        if verdict in ("approved", "reserves"):
            pv["checking"] = False
            self._log(f, "plan validated", "plan", "approved" if verdict == "approved" else "with reservations")
            self._autopilot(fs, f)
        elif verdict == "blocked":
            pv["checking"] = False
            if pv["fixes"] >= MAX_PLAN_FIXES:
                self._log(f, "plan fixes limit", "plan", f"{MAX_PLAN_FIXES} rounds without approval: it is up to you")
                self._autopilot(fs, f, validated=False)
                return
            pv["fixes"] += 1
            self._log(f, "plan fixes", "plan", "the validator blocked the plan")
            feedback = self._prompts(f).FEEDBACK_PLAN.format(path=path)
            try:
                self._start(fs, f, feedback)
            except FeatureError as e:
                self._log(f, "plan fixes failed", "plan", str(e))
        elif pv["retries"] >= MAX_REVIEW_RETRIES:
            pv["checking"] = False
            self._log(f, "validation without verdict", "plan", "could not read a verdict: read the plan and decide")
            self._autopilot(fs, f, validated=False)
        else:
            pv["retries"] += 1
            self._log(f, "validation repeated", "plan", "no recognisable verdict")
            try:
                self._start_plancheck(fs, f)
            except FeatureError as e:
                pv["checking"] = False
                self._log(f, "validation without verdict", "plan", str(e))

    def _after_review(self, fs: list, f: dict) -> None:
        path = self._dir(f["id"]) / ARTIFACT["review"]
        verdict, _ = parse_review(path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "")
        auto = f.setdefault("auto", {"fixes": 0, "retries": 0})
        why = "no recognisable verdict"
        if verdict in ("approved", "reserves"):
            gate = self._tests_gate(f)
            if gate in ("diverged", "notrun", "regress"):   # the reviewer did not confirm the tests: nothing is committed automatically
                reason = {"diverged": "the reviewer's numbers do not match the report's",
                          "notrun": "the reviewer could not run the full suite",
                          "regress": "the reviewer reported regressions"}[gate]
                self._log(f, "reviewer's tests not confirmed", "review", f"{reason}: read the review and decide")
                return
            if gate == "missing":                            # review without the “Tests” section with the real result: it is repeated
                verdict, why = "unknown", "the review has no “Tests” section with the full-suite result"
        if verdict in ("approved", "reserves"):
            self._commit(f, verdict)
        elif verdict == "blocked":
            if auto["fixes"] >= MAX_AUTO_FIXES:
                self._log(f, "automatic fixes limit", "review", f"{MAX_AUTO_FIXES} rounds without approval: it is up to you")
                return
            auto["fixes"] += 1
            try:
                self._send_fixes(fs, f, "", automatic=True)
            except FeatureError as e:
                f["stage"] = "review"
                self._log(f, "automatic fixes failed", "review", str(e))
        elif auto["retries"] >= MAX_REVIEW_RETRIES:
            reason = why if why.startswith("the review") else "could not read a verdict"
            self._log(f, "review without verdict", "review", f"{reason}: read the review and decide")
        else:
            auto["retries"] += 1
            self._log(f, "review repeated", "review", why)
            try:
                self._start(fs, f, "")
            except FeatureError as e:
                self._log(f, "review without verdict", "review", str(e))

    def _tests_gate(self, f: dict) -> str:
        """There is code (and so tests) only when the feature's worktree exists; without it there is nothing to confirm."""
        d = self._dir(f["id"])
        if not (d / "work").is_dir():
            return "ok"
        read = lambda name: (d / name).read_text(encoding="utf-8", errors="replace") if (d / name).is_file() else ""  # noqa: E731
        return review_tests_gate(read(ARTIFACT["build"]), read(ARTIFACT["review"]))

    def _commit(self, f: dict, verdict: str) -> None:
        """The commit only happens here, after a review that passes (the implementer leaves the work uncommitted)."""
        work = self._dir(f["id"]) / "work"
        if not self._committer or not work.is_dir():
            self._log(f, "nothing to commit", "review", "no code worktree")
            return
        message = f"feat: {f['title']}\n\nFeature {f['id']}. Independent review: {verdict}."
        try:
            ok, info = self._committer(work, message)
        except Exception as e:  # noqa: BLE001 - the commit is an extra: it never brings the feature down
            ok, info = False, str(e)
        self._log(f, "committed" if ok else "commit failed", "review", info)

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
               autopilot: bool = False) -> dict:
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
        kind = kind or "feature"
        with self._lock:
            fs = self._load()
            fid = f"{_slug(title)}-{datetime.now():%y%m%d%H%M%S}"
            f = {"id": fid, "title": title, "desc": desc, "project": project, "model": model or "",
                 "lang": lang or "en", "kind": kind, "stage": prompts_kinds.KIND_STAGES[kind][0], "status": WORKING, "worker_id": "", "worker_name": "",
                 "created": _now(), "history": [], "rev": {s: 0 for s in STAGES}, "auto": {"fixes": 0, "retries": 0}}
            if autopilot and kind not in ("ship", "release"):
                f["autopilot"] = True
            self._dir(fid).mkdir(parents=True, exist_ok=True)
            self._start(fs, f, "")
            fs.append(f)
            self._save(fs)
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
        fid = f["id"]
        if True:   # (kept as a block so the gates below read as before)
            if f.get("kind") == "ship" and f["stage"] == "build":         # the click authorises the reported commands: they must be sane
                path = self._dir(fid) / ARTIFACT["build"]
                gate = ship_gate(path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "")
                if gate == "missing":
                    raise FeatureError("the report has no “Ready to publish” section: ask for changes so the worker writes it")
                if gate == "notexecuted":
                    raise FeatureError("the report says the commit was not made: ask for changes or cancel")
                if gate == "badpush":
                    raise FeatureError("the report's push command is not allowed: only “git push -u <remote name> <branch>” on a branch that is not main or master")
            if f.get("kind") == "release":
                read = lambda a: (self._dir(fid) / ARTIFACT[a]).read_text(encoding="utf-8", errors="replace") if (self._dir(fid) / ARTIFACT[a]).is_file() else ""  # noqa: E731
                if f["stage"] == "plan":
                    gate = release_plan_gate(read("plan"))
                    if gate == "missing":
                        raise FeatureError("the promotion plan lacks a required section (Candidate, Regression tests, Promotion steps, Post-checks, Rollback): ask for changes")
                    if gate == "nosha":
                        raise FeatureError("the promotion plan has no “Candidate commit: <sha>” line: ask for changes")
                elif f["stage"] == "build":
                    gate = release_gate(read("plan"), read("build"))
                    if gate == "negative":
                        raise FeatureError("the regression verdict is NEGATIVE: the promotion cannot be approved; run the tests again or cancel")
                    if gate == "unknown":
                        raise FeatureError("the regression report has no readable POSITIVE or NEGATIVE verdict: run the tests again")
                    if gate == "mismatch":
                        raise FeatureError("the commit that was tested is not the planned candidate: run the tests again")
            if by_user:
                self._log(f, "approved", f["stage"])
            self._publish(f, f["stage"])
            stages = prompts_kinds.KIND_STAGES[f.get("kind", "feature")]
            nxt = stages.index(f["stage"]) + 1
            if nxt >= len(stages):
                f["status"] = DONE
                f["closed"] = self._closed()
                self._release(f["worker_id"])
            else:
                f["stage"] = stages[nxt]
                self._start(fs, f, "")

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
        self._log(f, "automatic fixes" if automatic else "fixes requested", "review", comment)
        f["stage"] = "build"
        P = self._prompts(f)
        if f.get("kind") == "analysis":
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
            self._log(f, "changes requested", f["stage"], comment)
            if f["stage"] == "plan":
                self._pv(f).update(fixes=0, retries=0)
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
        """The changes the user asked for in the round that failed, so that trying again does not lose them: the last request for
        changes or fixes, looking back over the launches and failures that followed it and stopping at anything else."""
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

    # ---- archive of the approved specs and plans ----
    @staticmethod
    def _archive_target(f: dict, stage: str):
        """(document kind, artifact phase) to archive when `stage` is approved; None if nothing is archived."""
        if f.get("kind", "feature") in ("ship", "release"):        # nothing is archived in the user's repo for these
            return None
        if f.get("kind", "feature") == "analysis":
            return ("analysis", "build") if stage == "review" else None
        return (stage, stage) if stage in ("spec", "plan") else None

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
        keep_q = f.get("kind") == "analysis" and stage == "review" and not feedback
        if old_q.is_file() and not keep_q:  # the previous round's questions are archived; the new worker writes its own
            moved_q = d / f"questions.{stage}.v{f['rev'].get(stage, 0) + (0 if moved else 1)}.json"
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
        if "pv" in f:
            f["pv"]["checking"] = False
        if previous and previous != w["id"]:
            self._release(previous)  # the previous worker is obsolete: its session is closed (the conversation is kept)
        self._log(f, "worker launched", stage, w.get("name", ""))

    def _closed(self) -> dict:
        """When and in which work session a request was closed (finished or cancelled)."""
        return {"at": _now(), "session": self._session_id()}

    def _spawn(self, f: dict, phase: str, task: str, extra_dirs: list, label: str) -> dict:
        """Launch the worker for one phase of a request with the model and effort the routing gives that phase (by default the
        request's own model and Claude Code's default effort, which is what every phase used before)."""
        model, effort = self._route(phase, f["model"] or "") if self._route else (f["model"] or None, None)
        extra = {"effort": effort} if effort else {}
        allow, deny = self._tools(f.get("kind", "feature"), phase) if self._tools else ((), ())
        if allow:
            extra["allow"] = list(allow)
        if deny:
            extra["deny"] = list(deny)
        return self._launch(f["project"], task, model or None, extra_dirs, label=label, **extra)

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

    def _spec_ref(self, f: dict, d: Path):
        """Path of the spec; for requests without a spec (maintenance, analysis) it says the requirement is the user's request."""
        if f.get("kind", "feature") in ("feature", "fix"):
            return d / ARTIFACT["spec"]
        return prompts_kinds.NO_SPEC[self._lang(f)]

    def _task(self, f: dict, feedback: str) -> str:
        d = self._dir(f["id"])
        stage = f["stage"]
        P, lang = self._prompts(f), self._lang(f)
        text = P.language_rule(self._note_lang) + P.COMMON.format(
            title=f["title"], fid=f["id"], project=f["project"], desc=f["desc"],
            path=d / ARTIFACT[stage], qpath=d / QUESTIONS_FILE) + "\n\n"
        kind = f.get("kind", "feature")
        template = prompts_kinds.OVERRIDE.get((lang, kind, stage)) or (
            P.BUILD_RESUME if stage == "build" and (d / "work").exists() else P.STAGE_TEXT[stage])
        extra = prompts_kinds.EXTRA.get((lang, kind, stage))
        if extra:
            template += "\n" + extra
        text += template.format(fid=f["id"], spec=self._spec_ref(f, d), plan=d / ARTIFACT["plan"],
                                report=d / ARTIFACT["build"], work=d / "work")
        text += P.BRIEF
        if not self._has_vault:
            text += P.NO_VAULT_NOTE
        if feedback:
            prev = d / f"{Path(ARTIFACT[stage]).stem}.v{f['rev'].get(stage, 0)}.md"
            text += P.CHANGES_BLOCK.format(feedback=feedback, prev=prev, path=d / ARTIFACT[stage])
        return text
