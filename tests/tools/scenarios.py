"""Scenarios for the golden traces (see capture_golden.py). Plain data: kind -> name -> {"actions": [...], "lang": "en"}."""
TESTS_OK = "\n## Tests\nCONFIRMED\nREVIEWER RESULT: 10 passed, 0 failed\n"
OK = "## Verdict\nAPPROVED — all good.\n\n## Findings\nNone.\n" + TESTS_OK
OK_NO_TESTS = "## Verdict\nAPPROVED — all good.\n\n## Findings\nNone.\n"
RESERVES = "## Verdict\nWITH RESERVATIONS — style only.\n\n## Findings\n- **[LOW] a.py:1 — name:** rename\n" + TESTS_OK
BLOCKED = "## Verdict\nBLOCKED — input validation is missing.\n\n## Findings\n- **[HIGH] app.py:9 — no validation:** validate\n"
UNKNOWN = "# Review\nI do not know\n"
DIVERGED = "## Verdict\nAPPROVED — ok.\n\n## Findings\nNone.\n\n## Tests\nDIVERGENT\nREVIEWER RESULT: 3 passed, 4 failed\n"
REPORT = ("## Execution status\nEXECUTED AND VERIFIED\n\n## Tests\nNO REGRESSIONS\nBASELINE: 10 passed, 0 failed\n"
          "FINAL RESULT: 10 passed, 0 failed\nNEW TESTS: 2\n")
RELEASE_PLAN = ("## Candidate\nCandidate commit: abc1234def5678\n\n## Regression tests\n`pytest -q`\n\n## Promotion steps\n"
                "```\ngit push origin main\n```\n\n## Post-checks\n`curl -I https://example.test`\n\n## Rollback\n`git revert abc1234def5678`\n")


def release_report(verdict="POSITIVE", sha="abc1234def5678"):
    return f"## Regression verdict\n{verdict}\n\nCandidate commit: {sha}\n\n| cmd | result |\n|---|---|\n| pytest -q | 120 passed |\n"


def F(art, text="# ok"):
    return ("finish", art, text)


# the early phases shared by the kinds that have a spec and a validated plan
_SPEC_PLAN = [F("spec.md"), ("approve",), F("plan.md"), F("plan-review.md", OK), ("approve",), ("mkwork",)]
_PLAN_ONLY = [F("plan.md"), F("plan-review.md", OK), ("approve",), ("mkwork",)]


def _build_review(reviews, closing=True):
    """report -> review(s) in order -> (optionally) the user accepts."""
    acts = [F("report.md", REPORT)]
    for i, r in enumerate(reviews):
        acts.append(F("review.md", r))
        if r is BLOCKED and i < len(reviews) - 1:         # the implementer fixes it by itself and writes a new report
            acts.append(F("report.md", REPORT))
    return acts + ([("approve",)] if closing else [])


def _kind(prefix):
    return {
        "approve-all": {"actions": prefix + _build_review([OK])},
        "reserves": {"actions": prefix + _build_review([RESERVES])},
        "review-blocked-1": {"actions": prefix + _build_review([BLOCKED, OK])},
        "review-blocked-4": {"actions": prefix + _build_review([BLOCKED] * 4, closing=False)},
        "review-unknown-3": {"actions": prefix + _build_review([UNKNOWN] * 3, closing=False)},
        "review-no-tests-section": {"actions": prefix + _build_review([OK_NO_TESTS, OK])},
        "tests-diverged": {"actions": prefix + _build_review([DIVERGED], closing=False)},
        "commit-fails": {"actions": prefix + [("commit_fails",)] + _build_review([OK], closing=False)},
        "request-fixes": {"actions": prefix + _build_review([OK], closing=False) + [("fixes", "please also handle X"), F("report.md", REPORT), F("review.md", OK)]},
        "cancel-mid": {"actions": prefix + [F("report.md", REPORT), ("cancel",)]},
        "portuguese": {"lang": "pt", "actions": prefix + _build_review([OK])},
    }


SCENARIOS = {"feature": _kind(_SPEC_PLAN), "fix": _kind(_SPEC_PLAN), "maintenance": _kind(_PLAN_ONLY)}
for _k in ("feature", "fix"):
    SCENARIOS[_k].update({
        "spec-changes": {"actions": [F("spec.md"), ("revise", "make it shorter"), F("spec.md"), ("approve",)]},
        "plan-blocked-1": {"actions": [F("spec.md"), ("approve",), F("plan.md"), F("plan-review.md", BLOCKED), F("plan.md"),
                                       F("plan-review.md", OK), ("approve",)]},
        "plan-blocked-3": {"actions": [F("spec.md"), ("approve",), F("plan.md")] + [F("plan-review.md", BLOCKED), F("plan.md")] * 2
                           + [F("plan-review.md", BLOCKED)]},
        "plan-unknown-3": {"actions": [F("spec.md"), ("approve",), F("plan.md")] + [F("plan-review.md", UNKNOWN)] * 3},
    })
SCENARIOS["maintenance"]["plan-blocked-1"] = {"actions": [F("plan.md"), F("plan-review.md", BLOCKED), F("plan.md"), F("plan-review.md", OK), ("approve",)]}

_ANALYSIS_Q = ('[{"id":"P1","title":"Scope","text":"Which scope?","options":[{"label":"A","recommended":true},{"label":"B"}]}]')
SCENARIOS["analysis"] = {
    "approve-all": {"actions": [F("report.md", "## Findings\nx\n"), F("review.md", OK), ("approve",)]},
    "blocked-1": {"actions": [F("report.md", "## Findings\nx\n"), F("review.md", BLOCKED), F("report.md", "## Findings\ny\n"), F("review.md", OK), ("approve",)]},
    "questions": {"actions": [F("report.md", "## Findings\nx\n"), F("questions.json", _ANALYSIS_Q), F("review.md", OK)]},
    "unknown-3": {"actions": [F("report.md", "## Findings\nx\n")] + [F("review.md", UNKNOWN)] * 3},
    "cancel": {"actions": [("cancel",)]},
}
SCENARIOS["ship"] = {
    "approve-all": {"actions": [F("report.md", "## Execution status\nEXECUTED AND VERIFIED\n## Ready to publish\n`git push -u origin feat/x`"),
                                ("approve",), F("review.md", "## Publication\npushed"), ("approve",)]},
    "bad-report": {"actions": [F("report.md", "## Execution status\nEXECUTED AND VERIFIED\n"), ("approve",)]},
    "push-force": {"actions": [F("report.md", "## Ready to publish\n`git push --force origin main`"), ("approve",)]},
    "cancel-before-publish": {"actions": [F("report.md", "## Ready to publish\n`git push -u origin feat/x`"), ("cancel",)]},
    "changes": {"actions": [F("report.md", "## Ready to publish\n`git push -u origin feat/x`"), ("revise", "other branch"),
                            F("report.md", "## Ready to publish\n`git push -u origin feat/y`"), ("approve",)]},
}
SCENARIOS["release"] = {
    "approve-all": {"actions": [F("plan.md", RELEASE_PLAN), ("approve",), F("report.md", release_report()), ("approve",),
                                F("review.md", "## Promotion\ndone"), ("approve",)]},
    "negative": {"actions": [F("plan.md", RELEASE_PLAN), ("approve",), F("report.md", release_report("NEGATIVE")), ("approve",)]},
    "unreadable": {"actions": [F("plan.md", RELEASE_PLAN), ("approve",), F("report.md", "no verdict here"), ("approve",)]},
    "commit-differs": {"actions": [F("plan.md", RELEASE_PLAN), ("approve",), F("report.md", release_report(sha="ffffff9999")), ("approve",)]},
    "incomplete-plan": {"actions": [F("plan.md", "## Candidate\nCandidate commit: abc1234\n"), ("approve",)]},
    "rerun-tests": {"actions": [F("plan.md", RELEASE_PLAN), ("approve",), F("report.md", release_report("NEGATIVE")),
                                ("revise", "run the tests again"), F("report.md", release_report()), ("approve",)]},
}
