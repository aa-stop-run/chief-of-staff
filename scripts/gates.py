"""Catalogue of named gates a pipeline may choose and parametrise (never arbitrary code).

Each gate reads artifacts and answers with a short code. `run(name, args, ctx)` is the single entry point used by the
engine; the original functions keep their names (and are re-exported by `features`) so nothing that used them changes."""
import re
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class GateResult:
    code: str       # "ok" | a gate-specific blocking code | "unknown"
    message: str


@dataclass(frozen=True)
class GateCtx:
    dir: Path       # the request's artifact folder
    has_worktree: bool = False

    def read(self, name: str) -> str:
        p = self.dir / name
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def required_sections(text: str, sections) -> list:
    """The section headings (## or ###) in `sections` that `text` lacks."""
    return [s for s in sections if not re.search(rf"^#{{2,3}}[ \t]+{re.escape(s)}\b", text or "", re.M | re.I)]


def _g_execution_status(a, c):
    return GateResult("ok", "")


def _g_review_tests(a, c):
    if not c.has_worktree:
        return GateResult("ok", "")
    code = review_tests_gate(c.read(a.get("report", "report.md")), c.read(a.get("review", "review.md")))
    return GateResult(code, "" if code == "ok" else f"tests not confirmed: {code}")


def _g_ship_ready(a, c):
    code = ship_gate(c.read(a.get("file", "report.md")))
    return GateResult(code, "" if code == "ok" else f"report not ready to publish: {code}")


def _g_release_plan(a, c):
    code = release_plan_gate(c.read(a.get("file", "plan.md")))
    return GateResult(code, "" if code == "ok" else f"promotion plan: {code}")


def _g_release_verdict(a, c):
    code = release_gate(c.read(a.get("plan", "plan.md")), c.read(a.get("file", "report.md")))
    return GateResult(code, "" if code == "ok" else f"regression: {code}")


def _g_required_sections(a, c):
    missing = required_sections(c.read(a["file"]), a["sections"])
    return GateResult("blocked", "missing sections: " + ", ".join(missing)) if missing else GateResult("ok", "")


def _g_no_critical(a, c):
    text = c.read(a.get("file", "review.md"))
    verdict, counts = parse_review(text)
    if verdict == "unknown":
        return GateResult("unknown", "no readable verdict")
    return GateResult("blocked", "a CRITICAL finding is open") if counts["CRITICAL"] else GateResult("ok", "")


GATES = {"execution_status": _g_execution_status, "review_tests": _g_review_tests, "ship_ready": _g_ship_ready,
         "release_plan": _g_release_plan, "release_verdict": _g_release_verdict,
         "required_sections": _g_required_sections, "no_critical_findings": _g_no_critical}


def run(name: str, args: dict, ctx: GateCtx) -> GateResult:
    """Run a catalogue gate by name. Unknown names are an error: a pipeline cannot invent a gate."""
    if name not in GATES:
        raise KeyError(f"unknown gate: {name}")
    return GATES[name](args or {}, ctx)
