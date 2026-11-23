"""The automatic checks of a request (independent plan validation and review): what happens after a check step, applied on the
engine's decision. `Features` inherits these methods; the shared names (statuses, FeatureError) live here so both import cleanly."""
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import engine
import gates
import prompt_refs
from gates import parse_review

PLAN_REVIEW = "plan-review.md"   # independent plan validation (a sub-step of the plan phase)
WORKING, AWAITING, FAILED, DONE, CANCELLED = "working", "awaiting", "failed", "done", "cancelled"


class FeatureError(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


COMMIT_TYPES = {"fix": "fix", "maintenance": "chore"}        # conventional-commit type per kind of request; anything else is «feat»


def commit_message(f: dict, verdict: str) -> str:
    """The message of the pipeline's commit: the conventional type follows the kind of request."""
    kind = COMMIT_TYPES.get(f.get("kind", "feature"), "feat")
    return f"{kind}: {f['title']}\n\nRequest {f['id']}. Independent review: {verdict}."


def _slug(s: str) -> str:
    plain = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")      # «correção» -> «correcao»
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:40] or "feature"


# Why the reviewer's tests were not confirmed (history note), by gate code.
GATE_REASONS = {"diverged": "the reviewer's numbers do not match the report's",
                "notrun": "the reviewer could not run the full suite", "regress": "the reviewer reported regressions"}
# History events of a check step, by its phase (the names the dashboard and the old data already use).
CHECK_EVENTS = {
    "review": {"gate": "reviewer's tests not confirmed", "limit": "automatic fixes limit", "fixes_failed": "automatic fixes failed",
               "no_verdict": "review without verdict", "repeated": "review repeated", "read": "read the review and decide"},
    "plan-validation": {"ok": "plan validated", "fixes": "plan fixes", "limit": "plan fixes limit", "fixes_failed": "plan fixes failed",
                        "no_verdict": "validation without verdict", "repeated": "validation repeated", "read": "read the plan and decide"}}


class CheckFlow:
    def _begin_check(self, fs: list, f: dict, step: dict) -> None:
        if step.get("phase") == "plan-validation":
            self._begin_plancheck(fs, f)
            return
        before = f["stage"]
        f["stage"] = step["phase"]
        self._log(f, "automatic review", f["stage"])
        try:
            self._start(fs, f, "")
        except FeatureError as e:   # stays waiting on the report: the “Send to review” button keeps working
            f["stage"], f["status"] = before, AWAITING
            self._log(f, "automatic review failed", before, str(e))

    # ---- independent plan validation: plan -> validator -> (plan fixes -> validator)* -> user ----
    @staticmethod
    def _pv(f: dict) -> dict:
        return f.setdefault("pv", {"checking": False, "fixes": 0, "retries": 0, "rev": 0})

    def _begin_plancheck(self, fs: list, f: dict) -> None:
        step = self._step_by_phase(f, "plan-validation")
        self._pv(f)
        self._rounds(f).pop(f"{step['id']}:unknown", None)
        self._mirror(f)
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
        step = self._step_by_phase(f, "plan-validation")
        task = prompt_refs.render(step["prompt"]["ref"], self._lang(f), {
            "title": f["title"], "fid": f["id"], "project": f["project"], "desc": f["desc"], "dir": d,
            "note_lang": self._note_lang})
        try:
            w = self._spawn(f, "plan-validation", task, [str(d)], f"{_slug(f['title'])[:30].strip('-')}-validate-plan")
        except Exception as e:
            if moved:
                moved.replace(old)
            raise FeatureError(f"could not launch the validator: {e}") from e
        self._tag(w, f, "plan-validation")
        previous = f["worker_id"]
        pv["checking"] = True
        f.update(status=WORKING, worker_id=w["id"], worker_name=w.get("name", ""), step=step["id"])
        if previous and previous != w["id"]:
            self._release(previous)
        self._log(f, "plan validation", "plan", w.get("name", ""))

    # ---- after a check step (the independent review or plan validation): the engine decides, this applies it ----
    def _gate_codes(self, f: dict, step: dict) -> list:
        """The step's catalogue gates; there is code (and so tests to confirm) only when the request's worktree exists."""
        d = self._dir(f["id"])
        return engine.gate_codes(step, gates.GateCtx(d, (d / "work").is_dir()))

    def _after_check(self, fs: list, f: dict, step: dict) -> None:
        ev = CHECK_EVENTS.get(step.get("phase"), CHECK_EVENTS["review"])
        plan_check = step.get("phase") == "plan-validation"
        path = self._dir(f["id"]) / step["artifact"]
        verdict, counts = parse_review(path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "")
        if plan_check and verdict == "blocked" and not (counts["CRITICAL"] or counts["HIGH"]) and (counts["MEDIUM"] or counts["LOW"]):
            verdict = "reserves"             # only minor findings: they inform the user, they do not send the plan back
        why, codes = "no recognisable verdict", []
        if verdict in ("approved", "reserves"):
            codes = [c for c in self._gate_codes(f, step) if c != "ok"]
            if "missing" in codes:     # a review without the “Tests” section with the real result is repeated
                why = "the review has no “Tests” section with the full-suite result"
        d = self._pipe(f)[1].on_check(f, step, verdict, codes)
        self._mirror(f)
        blocking = [c for c in codes if c in engine.BLOCKING_GATES]
        stage = f["stage"]
        retry = d.kind == "launch" and d.step == step["id"]
        if plan_check and not retry:
            self._pv(f)["checking"] = False
        if d.kind == "await":
            if blocking:     # the reviewer did not confirm the tests: nothing is committed automatically
                self._log(f, ev["gate"], stage, f"{GATE_REASONS[blocking[0]]}: {ev['read']}")
            elif verdict == "blocked":
                n = ((step.get("on") or {}).get("blocked") or {}).get("max_rounds", 0)
                self._log(f, ev["limit"], stage, f"{n} rounds without approval: it is up to you")
            else:
                reason = why if why.startswith("the review") else "could not read a verdict"
                self._log(f, ev["no_verdict"], stage, f"{reason}: {ev['read']}")
            if plan_check:
                self._autopilot(fs, f, validated=False)
        elif retry:
            self._log(f, ev["repeated"], stage, "no recognisable verdict" if plan_check else why)
            try:
                if plan_check:
                    self._start_plancheck(fs, f)
                else:
                    self._start(fs, f, "")
            except FeatureError as e:
                if plan_check:
                    self._pv(f)["checking"] = False
                self._log(f, ev["no_verdict"], stage, str(e))
        elif d.kind == "launch":
            self._send_back(fs, f, step, plan_check, path, ev)
        else:
            if d.action == "commit_worktree":
                if not self._tests_compared(f):
                    self._log(f, "tests not compared", "review",
                              "the report has no «FINAL RESULT» line: the reviewer's numbers were not compared with the implementer's")
                self._commit(f, verdict)
            if plan_check:
                self._log(f, ev["ok"], stage, "approved" if verdict == "approved" else "with reservations")
                self._autopilot(fs, f)

    def _send_back(self, fs: list, f: dict, step: dict, plan_check: bool, path: Path, ev: dict) -> None:
        """A check blocked the work: its findings go back to the step it names (an automatic round)."""
        if plan_check:
            self._log(f, ev["fixes"], f["stage"], "the validator blocked the plan")
            try:
                self._start(fs, f, self._prompts(f).FEEDBACK_PLAN.format(path=path))
            except FeatureError as e:
                self._log(f, ev["fixes_failed"], f["stage"], str(e))
            return
        try:
            self._send_fixes(fs, f, "", automatic=True)
        except FeatureError as e:
            f["stage"] = step["phase"]
            self._log(f, ev["fixes_failed"], step["phase"], str(e))

    def _commit(self, f: dict, verdict: str) -> None:
        """The commit only happens here, after a review that passes (the implementer leaves the work uncommitted)."""
        work = self._dir(f["id"]) / "work"
        if not self._committer or not work.is_dir():
            self._log(f, "nothing to commit", "review", "no code worktree")
            return
        try:
            ok, info = self._committer(work, commit_message(f, verdict))
        except Exception as e:  # noqa: BLE001 - the commit is an extra: it never brings the feature down
            ok, info = False, str(e)
        f["commit"] = {"ok": bool(ok), "at": _now()}      # D4 of the split plan reads this: «ready» = committed by the pipeline
        self._log(f, "committed" if ok else "commit failed", "review", info)
