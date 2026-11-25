"""The reviewer must run the tests again: without the “Tests” section with its own real result, nothing is committed."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import parse_tests, review_tests_gate  # noqa: E402
from test_auto_review import feature_in_build, finish_stage  # noqa: E402
from test_auto_review import auto  # noqa: E402,F401  (fixture)
from test_questions_js import run  # noqa: E402

MAX_REVIEW_RETRIES = 2   # the built-in pipeline's `retry` (data, no longer a constant of features.py)

REPORT = ("## Execution status\nEXECUTED AND VERIFIED\n\n## Tests\nNO REGRESSIONS\n"
          "BASELINE: 100 passed, 2 failed\nFINAL RESULT: 104 passed, 2 failed\nNEW TESTS: 4\n")
REPORT_PT = ("## Estado da execução\nEXECUTADO E VERIFICADO\n\n## Testes\nSEM REGRESSÕES\n"
             "LINHA DE BASE: 100 passam, 2 falham\nRESULTADO FINAL: 104 passam, 2 falham\nTESTES NOVOS: 4\n")


def review(tests: str, verdict: str = "APPROVED — ok.") -> str:
    return f"## Verdict\n{verdict}\n\n## Tests\n{tests}\n\n## Findings\nNone.\n"


def test_parse_tests_reads_states_and_numbers_in_both_languages():
    state, nums = parse_tests(REPORT)
    assert state == "ok" and nums == {"base": (100, 2), "final": (104, 2)}
    assert parse_tests(REPORT_PT) == ("ok", {"base": (100, 2), "final": (104, 2)})
    assert parse_tests(review("CONFIRMED\nREVIEWER RESULT: 7 passed, 0 failed")) == ("confirmed", {"reviewer": (7, 0)})
    assert parse_tests("## Verdict\nAPPROVED")[0] == "missing" and parse_tests("")[0] == "missing"


@pytest.mark.parametrize("tests,gate", [
    ("CONFIRMED\nREVIEWER RESULT: 104 passed, 2 failed", "ok"),
    ("CONFIRMED\nREVIEWER RESULT: 99 passed, 7 failed", "diverged"),   # numbers different from the implementer's
    ("CONFIRMED", "missing"),                                          # says it confirmed but shows nothing it ran
    ("DIVERGENT — an old test fails", "diverged"),
    ("NOT RUN — no environment", "notrun"),
    ("NO TESTS IN PROJECT — documentation only", "ok"),
    ("maybe", "missing"),
    ("CONFIRMADOS\nRESULTADO DO REVISOR: 104 passam, 2 falham", "ok"),   # a Portuguese reviewer is understood too
])
def test_gate_compares_the_reviewers_numbers_with_the_implementers(tests, gate):
    assert review_tests_gate(REPORT, review(tests)) == gate


def test_gate_requires_the_section_to_exist():
    assert review_tests_gate(REPORT, "## Verdict\nAPPROVED — ok.\n") == "missing"


def run_review(auto, text, work=True):
    fs, fake, root, commit = auto
    f = feature_in_build(fs, fake, root, work=work)
    f = finish_stage(fs, fake, root, f["id"], "report.md", REPORT)          # starts the automatic review
    return fs, fake, root, commit, finish_stage(fs, fake, root, f["id"], "review.md", text)


def test_review_confirming_the_tests_commits(auto):
    _, _, _, commit, f = run_review(auto, review("CONFIRMED\nREVIEWER RESULT: 104 passed, 2 failed"))
    assert len(commit.calls) == 1 and any(h["event"] == "committed" for h in f["history"])


def test_review_without_the_tests_section_is_repeated_and_nothing_is_committed(auto):
    fs, fake, root, commit, f = run_review(auto, "## Verdict\nAPPROVED — ok.\n\n## Findings\nNone.\n")
    assert commit.calls == [] and f["status"] == "working" and f["auto"]["retries"] == 1
    assert any("“Tests” section" in h["note"] for h in f["history"] if h["event"] == "review repeated")
    for _ in range(MAX_REVIEW_RETRIES):
        f = finish_stage(fs, fake, root, f["id"], "review.md", "## Verdict\nAPPROVED — ok.\n")
    assert commit.calls == [] and f["status"] == "awaiting"                   # exhausted: the user decides
    assert any(h["event"] == "review without verdict" and "“Tests” section" in h["note"] for h in f["history"])


@pytest.mark.parametrize("tests", ["CONFIRMED\nREVIEWER RESULT: 1 passed, 9 failed", "DIVERGENT — a test fails",
                                   "NOT RUN — no environment"])
def test_unconfirmed_tests_stop_the_automatic_commit(auto, tests):
    _, _, _, commit, f = run_review(auto, review(tests))
    assert commit.calls == [] and f["status"] == "awaiting"
    assert any(h["event"] == "reviewer's tests not confirmed" for h in f["history"])


def test_features_without_a_worktree_are_not_gated(auto):
    _, _, _, commit, f = run_review(auto, "## Verdict\nAPPROVED — ok.\n\n## Findings\nNone.\n", work=False)
    assert commit.calls == [] and any(h["event"] == "nothing to commit" for h in f["history"])   # nothing to commit, no repeat


def test_prompts_force_the_reviewer_to_run_the_suite_himself(auto):
    fs, fake, root, _ = auto
    for lang, needles in (("pt", ["Corres TU a suite COMPLETA", "RESULTADO DO REVISOR: <P> passam, <F> falham", "CONFIRMADOS"]),
                          ("en", ["YOU run the FULL suite", "REVIEWER RESULT: <P> passed, <F> failed", "CONFIRMED"])):
        f = fs.create("Alfa", f"Login {lang}", "x", None, lang=lang)
        finish_stage(fs, fake, root, f["id"], "spec.md")
        fs.approve(f["id"])
        finish_stage(fs, fake, root, f["id"], "plan.md")
        finish_stage(fs, fake, root, f["id"], "plan-review.md", "## Verdict\nAPPROVED\n")
        fs.approve(f["id"])
        build = fake.calls[-1]["task"]
        assert ("LINHA DE BASE: <P> passam, <F> falham" if lang == "pt" else "BASELINE: <P> passed, <F> failed") in build
        (root / "features" / f["id"] / "work").mkdir(parents=True)
        finish_stage(fs, fake, root, f["id"], "report.md")
        task = fake.calls[-1]["task"]
        for n in needles:
            assert n in task, (lang, n)


def test_page_shows_the_numbers_and_flags_a_mismatch():
    build = REPORT
    out = run(f"""
      const f={{artifacts:{{build:{json.dumps(build)},review:{json.dumps(review("CONFIRMED\nREVIEWER RESULT: 104 passed, 2 failed"))}}}}};
      const bad={{artifacts:{{build:{json.dumps(build)},review:{json.dumps(review("CONFIRMED\nREVIEWER RESULT: 90 passed, 16 failed"))}}}}};
      console.log(JSON.stringify({{n:parseTestNumbers({json.dumps(build)}), st:parseTests(f.artifacts.review),
        misses:parseTests("## Verdict\\nx"), ok:parseTestNumbers(f.artifacts.review).reviewer, bad:parseTestNumbers(bad.artifacts.review).reviewer}}))""")
    assert out["n"] == {"base": [100, 2], "final": [104, 2], "novos": 4}
    assert out["st"] == "confirmed" and out["misses"] == "unknown" and out["ok"] == [104, 2] and out["bad"] == [90, 16]
