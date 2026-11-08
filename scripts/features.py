"""The Chief's feature pipeline: spec -> plan -> implementation -> review, with human approval between phases.

Each phase is a worker (`claude --bg`) that writes an artifact in <root>/features/<id>/.
State is kept in <root>/features.json. Nothing advances by itself: every step is an explicit approval.
"""
import json
import re
import threading
from datetime import datetime
from pathlib import Path

import prompts
import prompts_kinds
import prompts_pt

STAGES = ("spec", "plan", "build", "review")
ARTIFACT = {"spec": "spec.md", "plan": "plan.md", "build": "report.md", "review": "review.md"}
MODELS = ("", "opus", "sonnet", "haiku")
KINDS = prompts_kinds.KINDS   # feature | fix | analysis | maintenance (each one's phases: prompts_kinds.KIND_STAGES)
LANGS = ("en", "pt")   # language the workers write the artifacts in (the dashboard's when the request was created)
MAX_TITLE, MAX_DESC, MAX_COMMENT, MAX_ARTIFACT = 120, 4000, 6000, 40_000
MAX_AUTO_FIXES, MAX_REVIEW_RETRIES = 3, 2   # automatic fix rounds; retries of a review without a verdict
QUESTIONS_FILE = "questions.json"
ARCHIVE_EVENTS = {"spec": ("spec archived", "spec archiving failed"), "plan": ("plan archived", "plan archiving failed"),
                  "analysis": ("analysis archived", "analysis archiving failed")}
PLAN_REVIEW = "plan-review.md"   # independent plan validation (a sub-step of the plan phase)
MAX_PLAN_FIXES = 2               # automatic rounds plan -> validation -> plan fix
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
                 auto_review: bool = False, committer=None, publisher=None, note_lang: str = "English"):
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
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(fs, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._file)

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
            if self._refresh(fs):
                self._save(fs)
            return fs

    def _refresh(self, fs: list) -> bool:
        changed = False
        for f in fs:
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

    # ---- automatic review: implementation -> review -> (fixes -> review)* -> commit ----
    def _advance(self, fs: list, f: dict) -> None:
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
                           spec=self._spec_ref(f, d), plan=d / ARTIFACT["plan"], out=old)
        try:
            w = self._launch(f["project"], task, f["model"] or None, [str(d)],
                             label=f"{_slug(f['title'])[:30].strip('-')}-validate-plan")
        except Exception as e:
            if moved:
                moved.replace(old)
            raise FeatureError(f"could not launch the validator: {e}") from e
        previous = f["worker_id"]
        pv["checking"] = True
        f.update(status=WORKING, worker_id=w["id"], worker_name=w.get("name", ""))
        if previous and previous != w["id"]:
            self._release(previous)
        self._log(f, "plan validation", "plan", w.get("name", ""))

    def _after_plancheck(self, fs: list, f: dict) -> None:
        path = self._dir(f["id"]) / PLAN_REVIEW
        verdict, _ = parse_review(path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "")
        pv = self._pv(f)
        if verdict in ("approved", "reserves"):
            pv["checking"] = False
            self._log(f, "plan validated", "plan", "approved" if verdict == "approved" else "with reservations")
        elif verdict == "blocked":
            pv["checking"] = False
            if pv["fixes"] >= MAX_PLAN_FIXES:
                self._log(f, "plan fixes limit", "plan", f"{MAX_PLAN_FIXES} rounds without approval: it is up to you")
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

    # ---- actions ----
    def create(self, project: str, title: str, desc: str, model, lang: str = "en", kind: str = "feature") -> dict:
        title, desc = (title or "").strip(), (desc or "").strip()
        if not project:
            raise FeatureError("missing project")
        if not title or len(title) > MAX_TITLE:
            raise FeatureError(f"title required (max. {MAX_TITLE} characters)")
        if not desc or len(desc) > MAX_DESC:
            raise FeatureError(f"description required (max. {MAX_DESC} characters)")
        if (model or "") not in MODELS:
            raise FeatureError("unknown model")
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
            self._log(f, "approved", f["stage"])
            self._publish(f, f["stage"])
            stages = prompts_kinds.KIND_STAGES[f.get("kind", "feature")]
            nxt = stages.index(f["stage"]) + 1
            if nxt >= len(stages):
                f["status"] = DONE
                self._release(f["worker_id"])
            else:
                f["stage"] = stages[nxt]
                self._start(fs, f, "")
            self._save(fs)
            return f

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
                self._start(fs, f, "")
            self._save(fs)
            return f

    def cancel(self, fid: str) -> dict:
        with self._lock:
            fs = self._load()
            f = self._get(fs, fid)
            if f["status"] in (DONE, CANCELLED):
                raise FeatureError("already finished")
            f["status"] = CANCELLED
            self._log(f, "cancelled", f["stage"])
            self._save(fs)
            self._release(f["worker_id"])
            return f

    # ---- archive of the approved specs and plans ----
    @staticmethod
    def _archive_target(f: dict, stage: str):
        """(document kind, artifact phase) to archive when `stage` is approved; None if nothing is archived."""
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
        if old_q.is_file():  # the previous round's questions are archived; the new worker writes its own
            moved_q = d / f"questions.{stage}.v{f['rev'].get(stage, 0) + (0 if moved else 1)}.json"
            old_q.replace(moved_q)
        try:
            w = self._launch(f["project"], self._task(f, feedback), f["model"] or None, [str(d)], label=f"{_slug(f['title'])[:30].strip('-')}-{stage}")
        except Exception as e:
            if moved_q:
                moved_q.replace(old_q)
            if moved:
                moved.replace(old)
            raise FeatureError(f"could not launch the worker: {e}") from e
        previous = f["worker_id"]
        f.update(status=WORKING, worker_id=w["id"], worker_name=w.get("name", ""))
        if "pv" in f:
            f["pv"]["checking"] = False
        if previous and previous != w["id"]:
            self._release(previous)  # the previous worker is obsolete: its session is closed (the conversation is kept)
        self._log(f, "worker launched", stage, w.get("name", ""))

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
        if feedback:
            prev = d / f"{Path(ARTIFACT[stage]).stem}.v{f['rev'].get(stage, 0)}.md"
            text += P.CHANGES_BLOCK.format(feedback=feedback, prev=prev, path=d / ARTIFACT[stage])
        return text
