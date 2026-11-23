"""The worker prompt of an integration request and the reader of its report.

The server has already merged the parts' branches into a throw-away worktree (combine.py) and left `combine.json`
next to it. The worker only runs the full suite there and reports; it changes no product code and makes no commit.
"""
import re

REPORT_SECTIONS = {
    "en": ["## Execution status", "<part id>: APPROVED|WITH RESERVATIONS|FAILED (one line per part)", "## Cross-part conflicts",
           "## Combined tests", "## Look at first"],
    "pt": ["## Estado da execução", "<id da parte>: APROVADO|COM RESERVAS|FALHOU (uma linha por parte)", "## Conflitos entre partes",
           "## Testes combinados", "## Ver primeiro"],
}
_TESTS_LINES = {"en": ("BASELINE", "FINAL RESULT"), "pt": ("LINHA DE BASE", "RESULTADO FINAL")}

_EN = """You are the INTEGRATION worker of the request «{title}» ({pid}). Its plan was split into parts that were built and reviewed on
their own; the server has already merged their branches, in order, into a throw-away worktree. You check that the parts work together.

Read first:
- {combine}: what was merged ("merged"), what conflicted ("conflicts", with the files) and what was missing ("missing"). The
  combined tree is based on the project's CURRENT HEAD ("base"), which may differ from the base the parts started from: say so in the report.
- The parts:
{parts}
- The parent's plan: {plan}. Take the full-suite command from its Tests section.

Work ONLY inside the integration worktree {worktree} (it is a git worktree; creating or removing worktrees is not your job):
1. Run the full suite there BEFORE judging anything, and write the BASELINE line.
2. Look at every conflict listed in combine.json and at the seams between parts (shared imports, names, data formats, configuration).
3. Run the full suite again for the FINAL RESULT.
Change nothing except the report: no product code, no commit, no push, no deleting. Anything that needs a change becomes a finding.

Write the report to {report}, exactly in this format:
{sections}
Under «## Combined tests» write the lines «BASELINE: <command and result>» and «FINAL RESULT: <passed, failed, skipped>».
Under «## Cross-part conflicts» list ONE bullet per real conflict, or write exactly "None." and nothing else there (put notes under «## Look at first»). Under «## Look at first» put what the user should check first.
End with [DONE], or [BLOCKED] if you could not run the suite."""

_PT = """És o worker de INTEGRAÇÃO do pedido «{title}» ({pid}). O plano foi dividido em partes que foram construídas e revistas cada uma
por si; o servidor já fundiu os ramos delas, por ordem, num worktree descartável. Confirmas que as partes funcionam em conjunto.

Lê primeiro:
- {combine}: o que foi fundido ("merged"), o que deu conflito ("conflicts", com os ficheiros) e o que faltava ("missing"). A árvore
  combinada parte do HEAD ATUAL do projeto ("base"), que pode diferir da base de onde as partes arrancaram: di-lo no relatório.
- As partes:
{parts}
- O plano do pedido pai: {plan}. Tira o comando da suite completa da sua secção de Testes.

Trabalha SÓ dentro do worktree de integração {worktree} (é um git worktree; criar ou remover worktrees não é contigo):
1. Corre lá a suite completa ANTES de julgar o que quer que seja e escreve a linha LINHA DE BASE.
2. Vê cada conflito de combine.json e as junções entre partes (imports partilhados, nomes, formatos de dados, configuração).
3. Corre outra vez a suite completa para o RESULTADO FINAL.
Não alteres nada além do relatório: nem código do produto, nem commit, nem push, nem apagar. O que precisar de alteração é um achado.

Escreve o relatório em {report}, exatamente neste formato:
{sections}
Em «## Testes combinados» escreve as linhas «LINHA DE BASE: <comando e resultado>» e «RESULTADO FINAL: <passaram, falharam, saltados>».
Em «## Conflitos entre partes» lista UM ponto por conflito real, ou escreve exatamente "Nenhum." e mais nada aí (as notas vão para «## Ver primeiro»). Em «## Ver primeiro» põe o que o utilizador deve ver primeiro.
Termina com [DONE], ou [BLOCKED] se não conseguiste correr a suite."""


def render(lang: str, parent: dict, parts: list, combine_json: str, worktree: str, report: str, plan: str) -> str:
    """The integration worker's prompt. `parts` is a list of {id, title, branch, report}; every path is absolute."""
    lang = "pt" if lang == "pt" else "en"
    lines = "\n".join(f"  - {p['id']} — {p.get('title', '')} (branch {p.get('branch', '')}; its review: {p.get('report', '')})"
                      for p in parts)
    template = _PT if lang == "pt" else _EN
    return template.format(title=parent.get("title", ""), pid=parent.get("id", ""), combine=combine_json, parts=lines, plan=plan,
                           worktree=worktree, report=report, sections="\n".join(REPORT_SECTIONS[lang]))


_PART_LINE = re.compile(r"^[-*\s]*([a-z0-9][a-z0-9-]*):\s*(APPROVED|WITH RESERVATIONS|FAILED|APROVADO|COM RESERVAS|FALHOU)\b", re.M)
_HEADING = re.compile(r"^#{1,6}\s", re.M)


def _section(text: str, titles: tuple) -> str:
    m = re.search(r"^#{2,3}\s*(?:%s)\s*$" % "|".join(re.escape(t) for t in titles), text, re.I | re.M)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = _HEADING.search(rest)
    return rest[:nxt.start()] if nxt else rest


def parse_report(text: str) -> dict:
    """{"parts": {id: verdict}, "conflicts": int|None, "tests": "<final result>"}; garbage gives the empty shape."""
    text = text if isinstance(text, str) else ""
    parts = {m.group(1): m.group(2) for m in _PART_LINE.finditer(text)}
    conflicts = None
    sec = _section(text, ("Cross-part conflicts", "Conflitos entre partes"))
    if sec.strip():
        items = [ln for ln in sec.splitlines() if ln.strip()]
        first = re.sub(r"^[-*\s]+", "", items[0]).strip().lower().rstrip(".")
        if first in ("none", "nenhum", "nenhuma"):      # «None.» first: any notes after it are not conflicts
            conflicts = 0
        else:
            conflicts = sum(1 for ln in items if re.match(r"\s*([-*]|\d+[.)])\s", ln)) or len(items)
    tests = ""
    m = re.search(r"^[-*\s]*(?:FINAL RESULT|RESULTADO FINAL)\s*:\s*(.+)$", text, re.M)
    if m:
        tests = m.group(1).strip()
    return {"parts": parts, "conflicts": conflicts, "tests": tests}
