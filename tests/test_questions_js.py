"""Tests the dashboard's “Open questions” reader (JavaScript) with node, if it is installed."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "app" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

LEGACY = """# Spec

## Open questions

- **P1 — Remote branch:** do you authorize deleting `docs/x` **also on GitHub**? (Verified: same as main.)
- **P2 — Apply or wait:** apply now (recommended) or postpone?
- **P4 — Skip reason:** how do you want to record the reason? Options: (a) field `phases_skip_reason` in the front matter; (b) argument `--skip-phases "<reason>"` in `cos.py`, stored in `ledger/events.jsonl`; (c) do not support it (as `ledger/specs/` is empty) and drop AC3.

## Another section
- this is not a question
"""

LEGACY_PT = """# Spec

## Perguntas em aberto

- **P1 — Ramo remoto:** autorizas apagar `docs/x` **também no GitHub**? (Verificado: igual ao main.)
- **P2 — Aplicar ou esperar:** aplicar já (recomendado) ou adiar?
- **P4 — Skip reason:** como queres registar o motivo? Opções: (a) campo `phases_skip_reason` no front matter; (b) argumento `--skip-phases "<motivo>"` no `cos.py`; (c) não suportar e retirar o AC3.

## Outra secção
- isto não é uma pergunta
"""

STRUCTURED = """## Open questions

- **P1 — Remote branch:** delete on GitHub too?
  - [ ] Yes, delete on the remote too (recommended) — nothing is lost
  - [ ] No, only the local branch
- **P2 — Scope:** only SPEC-1..3?
  - [ ] Yes
"""

HEADINGS = """## Open questions

### P1 — Who runs it
Do you confirm the implementation happens in a separate session?
- [ ] Yes (recommended)
- [ ] No

### P2 — Variant
Port it to the production variant as well?
"""


def run(script: str):
    src = HTML.read_text(encoding="utf-8")
    a, b = src.index("// <questions>"), src.index("// </questions>")
    code = src[a:b] + "\n" + script
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def parse(md: str):
    return run(f"console.log(JSON.stringify(parseQuestions({json.dumps(md)})))")


def test_legacy_free_text_questions_get_best_effort_options():
    qs = parse(LEGACY)
    assert [q["id"] for q in qs] == ["P1", "P2", "P4"]
    assert qs[0]["title"] == "Remote branch" and [o["label"] for o in qs[0]["options"]] == ["Yes", "No"]
    assert qs[1]["options"] == []  # no clear options: free field only
    assert [o["label"][:12] for o in qs[2]["options"]] == ["field `phase", "argument `--", "do not suppo"]
    assert "Options" not in qs[2]["text"]


def test_legacy_portuguese_questions_are_still_understood():
    qs = parse(LEGACY_PT)
    assert [q["id"] for q in qs] == ["P1", "P2", "P4"]
    assert [o["label"] for o in qs[0]["options"]] == ["Yes", "No"]          # the page shows “Sim/Não” when it is in Portuguese
    assert qs[1]["options"] == []
    assert [o["label"][:12] for o in qs[2]["options"]] == ["campo `phase", "argumento `-", "não suportar"]
    assert "Opções" not in qs[2]["text"]


def test_section_ends_at_next_heading():
    assert len(parse(LEGACY)) == 3


def test_structured_options_and_recommendation():
    qs = parse(STRUCTURED)
    assert [q["id"] for q in qs] == ["P1", "P2"]
    first = qs[0]["options"]
    assert first[0] == {"label": "Yes, delete on the remote too — nothing is lost", "rec": True}
    assert first[1]["rec"] is False


def test_heading_style_questions():
    qs = parse(HEADINGS)
    assert [(q["id"], q["title"]) for q in qs] == [("P1", "Who runs it"), ("P2", "Variant")]
    assert [o["rec"] for o in qs[0]["options"]] == [True, False]
    assert "separate session" in qs[0]["text"]


def test_no_section_or_empty_gives_no_questions():
    assert parse("# Spec\n\nno questions") == []
    assert parse("") == []


def test_compile_answers_includes_only_answered_and_general_note():
    out = run(f"""
      const qs=parseQuestions({json.dumps(STRUCTURED)});
      console.log(JSON.stringify(compileAnswers(qs,{{P1:{{opt:0,note:"what about GitHub Pages?"}}}},"  Go fast  ")));""")
    assert "## Answers to the open questions" in out
    assert "- **P1 — Remote branch:** Yes, delete on the remote too — nothing is lost" in out
    assert "  - Comment/question: what about GitHub Pages?" in out
    assert "P2" not in out and "## General note\nGo fast" in out


def test_compile_answers_in_portuguese_when_the_feature_is_portuguese():
    out = run(f"""
      const qs=parseQuestions({json.dumps(STRUCTURED)});
      console.log(JSON.stringify(compileAnswers(qs,{{P1:{{opt:0}}}},"ok","pt")));""")
    assert "## Respostas às perguntas em aberto" in out and "## Nota geral\nok" in out


def test_compile_note_without_option_and_nothing_answered():
    out = run(f"""
      const qs=parseQuestions({json.dumps(STRUCTURED)});
      console.log(JSON.stringify([compileAnswers(qs,{{P2:{{opt:null,note:"I did not understand"}}}},""),compileAnswers(qs,{{}},"")]));""")
    assert "(no option chosen)" in out[0] and "I did not understand" in out[0]
    assert out[1] == ""


def status(md: str):
    return run(f"console.log(JSON.stringify(questionsStatus({json.dumps(md)},parseQuestions({json.dumps(md)}))))")


def test_status_form_when_questions_are_parsed():
    assert status(STRUCTURED) == "form"


def test_status_none_when_spec_says_there_are_no_questions():
    assert status("## Open questions\n\nNone. If you approve, follow the plan.\n") == "none"
    assert status("## Open questions\n\nNo open questions.\n\n## Other\ntext") == "none"
    assert status("## Open questions\n") == "none"
    assert status("## Perguntas em aberto\n\nNenhuma. Se aprovares, segue o plano.\n") == "none"


def test_status_none_when_there_is_no_section():
    assert status("# Spec\n\nno section") == "none"


def test_status_unparsed_when_section_has_text_that_is_not_a_list():
    md = "## Open questions\n\nI need to know whether you want option A or B before going ahead.\n"
    assert status(md) == "unparsed"


def section(md: str, pattern: str = r"^#{2,3}\s+(?:answers\s+to\s+your\s+doubts|respostas\s+[àa]s\s+tuas\s+d[úu]vidas)"):
    return run(f"console.log(JSON.stringify(extractSection({json.dumps(md)},/{pattern}/i)))")


def test_extract_section_returns_body_until_next_heading():
    md = "# Spec\n\n## Answers to your doubts\n\n- P2: cos-setup is X.\n- P3: yes.\n\n## Requirements\n- r1\n"
    assert section(md) == "- P2: cos-setup is X.\n- P3: yes."
    assert section("## Respostas às tuas dúvidas\n\n- P2: sim.\n\n## Requisitos\n- r1\n") == "- P2: sim."


def test_extract_section_keeps_subheadings_and_handles_absence():
    md = "## Answers to your doubts\ntext\n### detail\nmore\n## End\nx"
    assert section(md) == "text\n### detail\nmore"
    assert section("# Spec\n\nnothing") == ""


def review(md: str):
    return run(f"console.log(JSON.stringify(parseReview({json.dumps(md)})))")


def test_parse_review_verdict_and_counts():
    md = """## Verdict
WITH RESERVATIONS — tests pass but the input is not validated.

## Findings
- **[HIGH] app/server.py:120 — unbounded body:** fix it
- **[MEDIUM] x.py:3 — name:** rename
- **[MEDIUM] y.py:9 — duplication:** extract
"""
    r = review(md)
    assert r["verdict"] == "reserves" and r["counts"] == {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 0}


def test_parse_review_blocked_approved_and_unknown():
    assert review("## Verdict\nBLOCKED — there is a CRITICAL.\n")["verdict"] == "blocked"
    assert review("## Verdict\n**APPROVED** — no findings.\n\n## Findings\nNone.\n")["verdict"] == "approved"
    assert review("## Veredicto\nBLOQUEADO — há um CRITICAL.\n")["verdict"] == "blocked"
    assert review("# Review\nno verdict section")["verdict"] == "unknown"
    assert review("")["counts"] == {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}


def questions_for(feature: dict):
    return run(f"console.log(JSON.stringify(questionsFor({json.dumps(feature)})))")


def test_questions_for_prefers_the_structured_file_over_the_markdown():
    f = {"stage": "spec", "artifacts": {"spec": STRUCTURED},
         "questions": [{"id": "Q1", "title": "T", "text": "x?", "options": [{"label": "Yes", "rec": True}]}]}
    r = questions_for(f)
    assert r["status"] == "form" and [q["id"] for q in r["qs"]] == ["Q1"]


def test_questions_for_empty_structured_list_means_none_even_if_markdown_has_text():
    f = {"stage": "spec", "artifacts": {"spec": STRUCTURED}, "questions": []}
    assert questions_for(f)["status"] == "none"


def test_questions_for_falls_back_to_markdown_when_there_is_no_file():
    f = {"stage": "spec", "artifacts": {"spec": STRUCTURED}, "questions": None}
    r = questions_for(f)
    assert r["status"] == "form" and [q["id"] for q in r["qs"]] == ["P1", "P2"]


def execution(md: str):
    return run(f"console.log(JSON.stringify(parseExecution({json.dumps(md)})))")


def test_parse_execution_states():
    assert execution("## Execution status\n**EXECUTED AND VERIFIED** — tests passing.\n") == "done"
    assert execution("## Execution status\nPARTIALLY EXECUTED — step 3 is missing.\n") == "partial"
    assert execution("## Execution status\nNOT EXECUTED — no access to the server.\n\n## Other\nx") == "none"
    assert execution("## Estado da execução\n**EXECUTADO E VERIFICADO** — testes a passar.\n") == "done"
    assert execution("## Estado da execução\nNÃO EXECUTADO — sem acesso.\n") == "none"


def test_parse_execution_unknown_for_old_reports():
    assert execution("# Report\n\n## Summary\nS01 has not been deleted yet.\n") == "unknown"
    assert execution("") == "unknown"


REVIEW = """## Verdict
WITH RESERVATIONS

## Findings
- **[MEDIUM] main repo — branch to delete (AC3):** requirement R1 unmet; `git branch -d` is safe.
- **[LOW] 7c57f16 — says “9 files”, there were 10:** no impact.
- **[HIGH] module x:** input validation is missing.

Security: not applicable.
"""


def test_parse_findings_reads_severity_title_and_body():
    fds = run(f"console.log(JSON.stringify(parseFindings({json.dumps(REVIEW)})))")
    assert [(x["id"], x["sev"]) for x in fds] == [("A1", "MEDIUM"), ("A2", "LOW"), ("A3", "HIGH")]
    assert fds[0]["title"] == "main repo — branch to delete (AC3)" and "git branch -d" in fds[0]["body"]
    assert fds[2]["title"] == "module x"


def test_parse_findings_none():
    assert run(f"console.log(JSON.stringify([parseFindings(''),parseFindings({json.dumps('## Findings' + chr(10) + 'None.')})]))") == [[], []]


def test_compile_fixes_defaults_fix_high_accept_rest_and_user_choice_wins():
    out = run(f"""
      const f=parseFindings({json.dumps(REVIEW)});
      console.log(JSON.stringify([compileFixes(f,{{}},""),compileFixes(f,{{A1:true,A3:false}}," be more careful ")]));""")
    assert "## Findings to fix" in out[0] and "A3 [HIGH]" in out[0].split("## Findings accepted")[0]
    assert "A1 [MEDIUM]" in out[0].split("## Findings accepted")[1]
    assert "A1 [MEDIUM]" in out[1].split("## Findings accepted")[0] and "**A3 " not in out[1].split("## Findings accepted")[0]
    assert out[1].endswith("## General note\nbe more careful")
