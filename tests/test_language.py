"""Feature language: English by default, with Portuguese as an option; the readers understand both."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FeatureError, Features, parse_review  # noqa: E402
from test_auto_review import Fake, finish_stage  # noqa: E402
from test_questions_js import run  # noqa: E402

REVIEW_EN_OK = "## Verdict\nAPPROVED — all good.\n\n## Findings\nNone.\n"
REVIEW_EN_RES = "## Verdict\nWITH RESERVATIONS — style only.\n\n## Findings\n- **[LOW] a.py:1 — name:** rename\n"
REVIEW_EN_BAD = "## Verdict\n**BLOCKED** — input is not validated.\n\n## Findings\n- **[HIGH] app.py:9 — no validation:** validate\n"
REVIEW_PT_BAD = "## Veredicto\n**BLOQUEADO** — falta validar o input.\n\n## Achados\n- **[HIGH] app.py:9 — sem validação:** validar\n"


@pytest.fixture
def auto(tmp_path):
    fake = Fake()
    return Features(tmp_path, fake.launch, fake.is_active, auto_review=True), fake, tmp_path


def test_english_feature_gets_english_prompts_and_headings(auto):
    fs, fake, _ = auto
    f = fs.create("Alfa", "Login with 2FA", "I want 2FA", None, lang="en")
    task = fake.calls[-1]["task"]
    assert f["lang"] == "en" and task.startswith("Language: the user works in English")
    assert "## Open questions" in task and "Goal and why" in task and "(recommended)" in task
    assert "Perguntas em aberto" not in task and "Escreve o artefacto" not in task


def test_english_is_the_default(auto):
    fs, fake, _ = auto
    f = fs.create("Alfa", "Login with 2FA", "I want 2FA", None)
    assert f["lang"] == "en" and "Open questions" in fake.calls[-1]["task"] and "Perguntas em aberto" not in fake.calls[-1]["task"]


def test_portuguese_is_available_as_a_translation(auto):
    fs, fake, _ = auto
    f = fs.create("Alfa", "Login com 2FA", "Quero 2FA", None, lang="pt")
    task = fake.calls[-1]["task"]
    assert f["lang"] == "pt" and "Perguntas em aberto" in task and "Language:" not in task


def test_the_vault_language_decides_the_language_of_the_notes_left_in_the_vault(tmp_path):
    fake = Fake()
    fs = Features(tmp_path, fake.launch, fake.is_active, note_lang="Portuguese (European)")
    fs.create("Alfa", "Login with 2FA", "I want 2FA", None, lang="en")
    assert "written in Portuguese (European)" in fake.calls[-1]["task"]
    fs.create("Alfa", "Another", "x", None, lang="pt")
    assert "Português" not in fake.calls[-1]["task"] and "Exceção" not in fake.calls[-1]["task"]   # already Portuguese
    fs2 = Features(tmp_path / "b", fake.launch, fake.is_active, note_lang="English")
    fs2.create("Alfa", "Login com 2FA", "x", None, lang="pt")
    assert "escreve-a em English" in fake.calls[-1]["task"]


def test_features_saved_before_the_english_default_are_still_portuguese(auto):
    fs, fake, root = auto
    f = fs.create("Alfa", "Login", "x", None, lang="en")
    data = fs._load()
    del data[0]["lang"]                                  # as saved by older versions
    fs._save(data)
    finish_stage(fs, fake, root, f["id"], "spec.md")
    fs.approve(f["id"])
    assert "Plano de implementação" in fake.calls[-1]["task"] or "plano" in fake.calls[-1]["task"].lower()


def test_unknown_language_is_rejected(auto):
    fs, _, _ = auto
    with pytest.raises(FeatureError, match="language"):
        fs.create("Alfa", "x", "y", None, lang="fr")


def test_every_phase_speaks_english(auto):
    fs, fake, root = auto
    f = fs.create("Alfa", "Login with 2FA", "I want 2FA", None, lang="en")
    finish_stage(fs, fake, root, f["id"], "spec.md")
    fs.approve(f["id"])
    assert "implementation plan" in fake.calls[-1]["task"] and "Approach" in fake.calls[-1]["task"]
    finish_stage(fs, fake, root, f["id"], "plan.md")                     # the plan validator starts
    assert "independent plan validation" in fake.calls[-1]["task"] and "## Verdict" in fake.calls[-1]["task"]
    finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_EN_OK)
    fs.approve(f["id"])
    assert "## Execution status" in fake.calls[-1]["task"] and "EXECUTED AND VERIFIED" in fake.calls[-1]["task"]
    (root / "features" / f["id"] / "work").mkdir(parents=True)
    finish_stage(fs, fake, root, f["id"], "report.md")                   # the automatic review starts
    assert "independent reviewer" in fake.calls[-1]["task"] and "## Findings" in fake.calls[-1]["task"]


def test_english_change_requests_and_fixes_are_in_english(auto):
    fs, fake, root = auto
    f = fs.create("Alfa", "Login with 2FA", "I want 2FA", None, lang="en")
    finish_stage(fs, fake, root, f["id"], "spec.md")
    fs.revise(f["id"], "## Answers to the open questions\n- **P1 — X:** Yes")
    task = fake.calls[-1]["task"]
    assert "## Changes requested by the user" in task and "Answers to your doubts" in task and "Alterações pedidas" not in task


def test_portuguese_change_requests_are_in_portuguese(auto):
    fs, fake, root = auto
    f = fs.create("Alfa", "Login com 2FA", "Quero 2FA", None, lang="pt")
    finish_stage(fs, fake, root, f["id"], "spec.md")
    fs.revise(f["id"], "usa outra abordagem")
    task = fake.calls[-1]["task"]
    assert "## Alterações pedidas pelo utilizador" in task and "Respostas às tuas dúvidas" in task


def test_blocked_english_plan_goes_back_with_an_english_instruction(auto):
    fs, fake, root = auto
    f = fs.create("Alfa", "Login with 2FA", "I want 2FA", None, lang="en")
    finish_stage(fs, fake, root, f["id"], "spec.md")
    fs.approve(f["id"])
    finish_stage(fs, fake, root, f["id"], "plan.md")
    f = finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_EN_BAD)
    assert f["status"] == "working" and "independent plan validation" in fake.calls[-1]["task"] and "blocked it" in fake.calls[-1]["task"]


def test_blocked_portuguese_plan_goes_back_with_a_portuguese_instruction(auto):
    fs, fake, root = auto
    f = fs.create("Alfa", "Login com 2FA", "Quero 2FA", None, lang="pt")
    finish_stage(fs, fake, root, f["id"], "spec.md")
    fs.approve(f["id"])
    finish_stage(fs, fake, root, f["id"], "plan.md")
    f = finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_PT_BAD)
    assert f["status"] == "working" and "bloqueou-o" in fake.calls[-1]["task"]


@pytest.mark.parametrize("text,verdict", [(REVIEW_EN_OK, "approved"), (REVIEW_EN_RES, "reserves"), (REVIEW_EN_BAD, "blocked"),
                                           (REVIEW_PT_BAD, "blocked")])
def test_parse_review_understands_both_languages(text, verdict):
    assert parse_review(text)[0] == verdict


def test_page_parsers_understand_english_artifacts():
    out = run("""console.log(JSON.stringify({
      verdict: parseReview(%s).verdict,
      exec: [parseExecution("## Execution status\\nEXECUTED AND VERIFIED"), parseExecution("## Execution status\\nPARTIALLY EXECUTED — x"),
             parseExecution("## Execution status\\nNOT EXECUTED — no access")],
      findings: parseFindings(%s).map(f => f.sev + ":" + f.title),
      qs: parseQuestions(%s).map(q => [q.id, q.options.map(o => [o.label, o.rec])]),
      none: questionsStatus("## Open questions\\nNone.", [])
    }))""" % (json.dumps(REVIEW_EN_BAD), json.dumps(REVIEW_EN_BAD),
              json.dumps("## Open questions\n### P1 — Remote\nDelete remote too?\n- [ ] Yes (recommended) — clean\n- [ ] No — safer\n")))
    assert out["verdict"] == "blocked"
    assert out["exec"] == ["done", "partial", "none"]
    assert out["findings"] == ["HIGH:app.py:9 — no validation"]
    assert out["qs"] == [["P1", [["Yes — clean", True], ["No — safer", False]]]]
    assert out["none"] == "none"


def test_page_builds_worker_text_in_the_feature_language():
    out = run("""const qs=[{id:"P1",title:"X",options:[{label:"Yes"}]}];
      const fds=[{id:"A1",sev:"HIGH",title:"t",body:"b"},{id:"A2",sev:"LOW",title:"u",body:"c"}];
      console.log(JSON.stringify({
        en: compileAnswers(qs,{P1:{opt:0,note:"why"}},"hi","en"), pt: compileAnswers(qs,{P1:{opt:0}},"","pt"),
        fixEn: compileFixes(fds,{},"extra","en"), fixPt: compileFixes(fds,{},"","pt")}))""")
    assert out["en"].startswith("## Answers to the open questions") and "Comment/question: why" in out["en"] and "## General note\nhi" in out["en"]
    assert out["pt"].startswith("## Respostas às perguntas em aberto")
    assert "## Findings to fix (chosen by the user)" in out["fixEn"] and "## Findings accepted by the user (do not change)" in out["fixEn"]
    assert "## Achados a corrigir (escolhidos pelo utilizador)" in out["fixPt"]


# --- the “Tests” section of the report and the testing rules in the prompts -----------------------------------------

@pytest.mark.parametrize("head,state", [
    ("SEM REGRESSÕES", "ok"), ("NO REGRESSIONS", "ok"), ("COM REGRESSÕES — 2 testes", "regress"), ("REGRESSIONS — 1 test", "regress"),
    ("NÃO CORRIDOS — sem ambiente", "notrun"), ("NOT RUN — no access", "notrun"),
    ("SEM TESTES NO PROJETO — só documentação", "notests"), ("NO TESTS IN PROJECT — docs only", "notests"), ("maybe", "unknown"),
])
def test_report_tests_section_is_read_in_both_languages(head, state):
    section = "Testes" if "SEM" in head or "COM" in head or "NÃO" in head else "Tests"
    md = f"## Execution status\nEXECUTED AND VERIFIED\n\n## {section}\n{head}\nbaseline: 10 passed\n"
    assert run(f"console.log(JSON.stringify(parseTests({json.dumps(md)})))") == state


def test_missing_tests_section_is_flagged():
    md = "## Estado da execução\nEXECUTADO E VERIFICADO\n"
    assert run(f"console.log(JSON.stringify(parseTests({json.dumps(md)})))") == "unknown"


@pytest.mark.parametrize("lang,needles", [
    ("pt", ["linha de base", "suite COMPLETA", "SEM REGRESSÕES", "SEM TESTES NO PROJETO", "Critério de aceitação"]),
    ("en", ["baseline", "FULL", "NO REGRESSIONS", "NO TESTS IN PROJECT", "Acceptance criterion"]),
])
def test_build_prompt_demands_a_baseline_and_a_full_regression_run(auto, lang, needles):
    fs, fake, root = auto
    f = fs.create("Alfa", "Login", "x", None, lang=lang)
    finish_stage(fs, fake, root, f["id"], "spec.md")
    fs.approve(f["id"])
    plan_task = fake.calls[-1]["task"]
    finish_stage(fs, fake, root, f["id"], "plan.md")
    finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_EN_OK)
    fs.approve(f["id"])
    build_task = fake.calls[-1]["task"]
    for n in needles:
        assert n in build_task, n
    assert ("«Testes»" if lang == "pt" else '"Tests"') in plan_task


@pytest.mark.parametrize("lang,needle,blocked", [("pt", "NÃO tem e o plano não cria", "BLOQUEADO"), ("en", "NONE and the plan does not create", "BLOCKED")])
def test_plan_validator_blocks_a_plan_that_leaves_a_repo_without_tests(auto, lang, needle, blocked):
    fs, fake, root = auto
    f = fs.create("Alfa", "Login", "x", None, lang=lang)
    finish_stage(fs, fake, root, f["id"], "spec.md")
    fs.approve(f["id"])
    finish_stage(fs, fake, root, f["id"], "plan.md")
    task = " ".join(fake.calls[-1]["task"].split())
    assert needle in task and blocked in task


def test_reviewer_blocks_regressions_and_criteria_without_tests(auto):
    fs, fake, root = auto
    for lang, needles in (("pt", ["algum teste que passava na linha de base falha", "não tem teste"]),
                          ("en", ["a test that passed in the baseline fails now", "has no test"])):
        f = fs.create("Alfa", f"Login {lang}", "x", None, lang=lang)
        finish_stage(fs, fake, root, f["id"], "spec.md")
        fs.approve(f["id"])
        finish_stage(fs, fake, root, f["id"], "plan.md")
        finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_EN_OK)
        fs.approve(f["id"])
        (root / "features" / f["id"] / "work").mkdir(parents=True)
        finish_stage(fs, fake, root, f["id"], "report.md")
        task = " ".join(fake.calls[-1]["task"].split())
        for n in needles:
            assert n in task, (lang, n)
