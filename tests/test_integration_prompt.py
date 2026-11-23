"""The integration worker's prompt and the report reader (scripts/prompts_integration.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import prompts_integration as pi  # noqa: E402

PARENT = {"id": "export-trips-1", "title": "Export trips"}
PARTS = [{"id": "export-trips-1-api-1", "title": "Endpoint", "branch": "feat/export-trips-1-api-1", "report": "/s/api/review.md"},
         {"id": "export-trips-1-ui-1", "title": "Button", "branch": "feat/export-trips-1-ui-1", "report": "/s/ui/review.md"}]


def render(lang):
    return pi.render(lang, PARENT, PARTS, "/s/int/combine.json", "/s/int/integration", "/s/int/report.md", "/s/plan.md")


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_the_prompt_names_every_path_and_part(lang):
    text = render(lang)
    for needle in ("/s/int/combine.json", "/s/int/integration", "/s/int/report.md", "/s/plan.md", "/s/api/review.md", "/s/ui/review.md",
                   "feat/export-trips-1-api-1", "feat/export-trips-1-ui-1", "Export trips", "export-trips-1-ui-1"):
        assert needle in text
    for title in pi.REPORT_SECTIONS[lang]:
        assert title in text
    assert text.rstrip().endswith("[BLOCKED] if you could not run the suite." if lang == "en" else "[BLOCKED] se não conseguiste correr a suite.")
    assert "[DONE]" in text


def test_the_en_prompt_gives_the_rules():
    text = render("en")
    assert "CURRENT HEAD" in text and "full suite" in text and "BASELINE" in text and "FINAL RESULT" in text
    assert "no commit, no push" in text and "Change nothing except the report" in text
    assert "do NOT create" not in text and "worktrees and do NOT" not in text


def test_the_pt_prompt_gives_the_rules():
    text = render("pt")
    assert "HEAD ATUAL" in text and "suite completa" in text and "LINHA DE BASE" in text and "RESULTADO FINAL" in text
    assert "nem commit, nem push" in text and "NÃO cries worktrees" not in text


EN_REPORT = """## Execution status
EXECUTED AND VERIFIED
- export-trips-1-api-1: APPROVED
- export-trips-1-ui-1: WITH RESERVATIONS
## Cross-part conflicts
- api and ui both rename `Trip`
- config key clash
## Combined tests
BASELINE: pytest -q: 8 passed
FINAL RESULT: 10 passed, 0 failed
## Look at first
the Trip rename
"""
PT_REPORT = """## Estado da execução
EXECUTADO E VERIFICADO
export-trips-1-api-1: APROVADO
export-trips-1-ui-1: FALHOU
## Conflitos entre partes
Nenhum.
## Testes combinados
LINHA DE BASE: pytest -q: 8 passou
RESULTADO FINAL: 9 passaram, 1 falhou
"""


def test_parse_report_en():
    assert pi.parse_report(EN_REPORT) == {"parts": {"export-trips-1-api-1": "APPROVED", "export-trips-1-ui-1": "WITH RESERVATIONS"},
                                          "conflicts": 2, "tests": "10 passed, 0 failed"}


def test_parse_report_pt():
    assert pi.parse_report(PT_REPORT) == {"parts": {"export-trips-1-api-1": "APROVADO", "export-trips-1-ui-1": "FALHOU"},
                                          "conflicts": 0, "tests": "9 passaram, 1 falhou"}


def test_parse_report_garbage():
    empty = {"parts": {}, "conflicts": None, "tests": ""}
    for junk in ("", "nothing useful", None, 42):
        assert pi.parse_report(junk) == empty


def test_none_first_means_no_conflicts_even_with_notes_after_it():
    real = ("## Execution status\n- p-1: APPROVED\n\n## Cross-part conflicts\n- None.\n- combine.json: conflicts `{}`.\n- Seams are disjoint.\n\n"
            "## Combined tests\n- FINAL RESULT: 3 passed, 0 failed, 0 skipped\n")
    assert pi.parse_report(real) == {"parts": {"p-1": "APPROVED"}, "conflicts": 0, "tests": "3 passed, 0 failed, 0 skipped"}
    assert pi.parse_report("## Cross-part conflicts\n- a clashes with b\n- c clashes with d\n")["conflicts"] == 2
