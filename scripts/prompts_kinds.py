"""Prompts per request kind (bug fix, analysis, maintenance), in English and Portuguese.

OVERRIDE[(language, kind, phase)] replaces the phase text; EXTRA[(language, kind, phase)] is appended to the normal text. The
"plancheck" phase is the plan validation. Features use the texts of prompts.py / prompts_pt.py unchanged."""

KINDS = ("feature", "fix", "analysis", "maintenance")
# phases of each kind (the files are the usual ones: spec.md, plan.md, report.md and review.md)
KIND_STAGES = {"feature": ("spec", "plan", "build", "review"), "fix": ("spec", "plan", "build", "review"),
               "maintenance": ("plan", "build", "review"), "analysis": ("build", "review")}

NO_SPEC = {"pt": "o pedido do utilizador acima (neste tipo de pedido não há spec)",
           "en": "the user's request above (this kind of request has no spec)"}

_READ_ONLY_PT = ("SÓ LEITURA: NÃO alteres nada no repo nem fora dele, NÃO faças commits, NÃO instales nada, NÃO cries worktrees e NÃO\n"
                 "escrevas código de produto; só lês e corres comandos de verificação (ls, git log, grep, os testes que já existem sem os\n"
                 "modificar). Não leias nem imprimas .env*, chaves, tokens ou ficheiros de segredos.")
_READ_ONLY_EN = ("READ-ONLY: do NOT change anything in the repo or outside it, do NOT commit, do NOT install anything, do NOT create\n"
                 "worktrees and do NOT write product code; you only read and run verification commands (ls, git log, grep, the tests\n"
                 "that already exist without modifying them). Do not read or print .env*, keys, tokens or secret files.")

OVERRIDE = {
    # ---------------------------------------------------------------- Portuguese (translation)
    ("pt", "fix", "spec"): """## Fase 1: diagnóstico
Usa a skill `superpowers:systematic-debugging` (ferramenta Skill) para investigar. Adaptações obrigatórias, porque és um worker em segundo
plano: NÃO faças perguntas em chat (regista cada dúvida em «Perguntas em aberto», no formato indicado, com a tua recomendação); NÃO
escrevas em docs/superpowers nem faças commits no repo (o artefacto é o ficheiro indicado acima, fora do repo).
Reproduz o problema (lê o código e corre só comandos de leitura ou os testes que já existem, sem alterar nada) e encontra a causa
raiz com evidência. NÃO escrevas a correção.
Secções: Sintoma · Comportamento esperado · Como reproduzir · Causa raiz (com evidência: ficheiro:linha e comandos) · Âmbito e impacto ·
Critérios de aceitação (o teste que reproduz o bug falha antes e passa depois; a suite completa sem regressões) · Riscos ·
Perguntas em aberto.""",
    ("pt", "maintenance", "plan"): """## Fase 1: plano de manutenção
Usa a skill `superpowers:writing-plans` (ferramenta Skill). Adaptações: o plano é o ficheiro indicado acima; sem commits no repo; não
ofereças opções de execução (a execução é a fase seguinte, decidida pelo utilizador no dashboard); dúvidas vão para «Perguntas em
aberto», não para o chat. Não há spec: o pedido do utilizador acima é o requisito. NÃO escrevas código de produto.
Manutenção significa que o comportamento NÃO muda (refatorar, atualizar dependências, limpar, documentação).
Secções: Objetivo e âmbito (o que muda e o que NÃO muda) · Ficheiros a alterar (caminhos reais do repo) · Testes · Passos ordenados,
cada um com o teste que o valida · Riscos e reversão · Perguntas em aberto.
A secção «Testes» é obrigatória e traz: o comando que corre a suite COMPLETA do projeto (descobre-o no repo) e como se prova que o
comportamento não mudou (a suite existente passa igual; se faltar cobertura onde vais mexer, os testes de caracterização que a
cobrem, escritos antes). Se o repo não tem testes e o código é relevante, o primeiro passo cria o mínimo; em repos sem código escreve
«sem testes: <motivo>» e diz como se verifica à mão. Ações irreversíveis ou em sistemas partilhados ficam como passos para o utilizador.""",
    ("pt", "analysis", "build"): """## Fase 1: análise
És um worker de ANÁLISE. """ + _READ_ONLY_PT + """
Responde ao pedido do utilizador acima com evidência. Lê a nota do projeto no vault e o código. Não há spec, plano nem worktree.
O artefacto é o relatório da análise, com as secções: Pergunta · Resposta resumida (3 a 5 linhas) · Evidência (cada afirmação com a
fonte: ficheiro:linha, ou o comando e o resultado) · O que não consegui confirmar · Opções e próximos passos (se houver; cada um com
custo e risco) · Perguntas em aberto. Nada de afirmações sem evidência: o que não verificaste vai em «O que não consegui confirmar».""",
    ("pt", "analysis", "review"): """## Fase 2: verificação independente da análise
És o verificador independente: NÃO fizeste a análise e não deves confiar nela. """ + _READ_ONLY_PT + """
Relatório a verificar: {report}. O pedido do utilizador está acima. Faz, por esta ordem:
1. Lista as afirmações factuais do relatório (pelo menos as que sustentam a resposta resumida) e volta a verificá-las TU, com comandos
   e leitura do código; não aceites a evidência citada sem a confirmares.
2. Confere se a resposta responde mesmo à pergunta e se há conclusões sem evidência ou que a evidência não sustenta.
3. Procura o que falta: ficheiros, casos ou riscos relevantes que a análise ignorou.
Escreve o artefacto exatamente neste formato:
## Veredicto
APROVADO, COM RESERVAS ou BLOQUEADO, e uma frase. (BLOQUEADO se alguma conclusão principal estiver errada ou sem evidência.)
## Verificações
Os comandos que correste e o resultado real.
## Afirmações
| Afirmação | Estado (confirmada / não confirmada / não verificável) | Evidência |
## Achados
- **[CRITICAL|HIGH|MEDIUM|LOW] secção do relatório — título:** o que está mal e como corrigir
CRITICAL = conclusão principal errada; HIGH = afirmação importante sem evidência ou errada; MEDIUM = lacuna; LOW = estilo.
Se não houver achados escreve «Nenhum.»""",
    # ---------------------------------------------------------------- English (source)
    ("en", "fix", "spec"): """## Phase 1: diagnosis
Use the `superpowers:systematic-debugging` skill (Skill tool) to investigate. Mandatory adaptations, because you are a background
worker: do NOT ask questions in chat (record each doubt in "Open questions", in the format above, with your recommendation); do NOT
write to docs/superpowers or commit to the repo (the artifact is the file indicated above, outside the repo).
Reproduce the problem (read the code and run only read-only commands or the tests that already exist, changing nothing) and find the
root cause with evidence. Do NOT write the fix.
Sections: Symptom · Expected behavior · How to reproduce · Root cause (with evidence: file:line and commands) · Scope and impact ·
Acceptance criteria (the test that reproduces the bug fails before and passes after; the full suite has no regressions) · Risks ·
Open questions.""",
    ("en", "maintenance", "plan"): """## Phase 1: maintenance plan
Use the `superpowers:writing-plans` skill (Skill tool). Adaptations: the plan is the file indicated above; no commits to the repo; do
not offer execution options (execution is the next phase, decided by the user in the dashboard); doubts go in "Open questions", not in
chat. There is no spec: the user's request above is the requirement. Do NOT write product code.
Maintenance means behavior does NOT change (refactoring, updating dependencies, cleanup, documentation).
Sections: Goal and scope (what changes and what does NOT) · Files to change (real repo paths) · Tests · Ordered steps, each with the
test that validates it · Risks and rollback · Open questions.
The "Tests" section is mandatory and has: the command that runs the project's FULL suite (find it in the repo) and how to prove the
behavior did not change (the existing suite passes the same; if coverage is missing where you will touch, characterization tests that
cover it, written first). If the repo has no tests and the code matters, the first step creates the minimum; for repos without code
write "no tests: <reason>" and say how to check by hand. Irreversible actions or actions on shared systems are left as steps for the user.""",
    ("en", "analysis", "build"): """## Phase 1: analysis
You are an ANALYSIS worker. """ + _READ_ONLY_EN + """
Answer the user's request above with evidence. Read the project's vault note and the code. There is no spec, plan or worktree.
The artifact is the analysis report, with the sections: Question · Short answer (3 to 5 lines) · Evidence (every claim with its source:
file:line, or the command and its result) · What I could not confirm · Options and next steps (if any; each with cost and risk) ·
Open questions. No claim without evidence: what you did not verify goes under "What I could not confirm".""",
    ("en", "analysis", "review"): """## Phase 2: independent verification of the analysis
You are the independent verifier: you did NOT do the analysis and must not trust it. """ + _READ_ONLY_EN + """
Report to verify: {report}. The user's request is above. Do, in this order:
1. List the report's factual claims (at least those that support the short answer) and verify them again YOURSELF, with commands and
   by reading the code; do not accept the cited evidence without confirming it.
2. Check that the answer really answers the question and whether there are conclusions without evidence or that the evidence does not support.
3. Look for what is missing: relevant files, cases or risks the analysis ignored.
Write the artifact exactly in this format:
## Verdict
APPROVED, WITH RESERVATIONS or BLOCKED, and one sentence. (BLOCKED if a main conclusion is wrong or without evidence.)
## Checks
The commands you ran and the real result.
## Claims
| Claim | Status (confirmed / not confirmed / not verifiable) | Evidence |
## Findings
- **[CRITICAL|HIGH|MEDIUM|LOW] report section — title:** what is wrong and how to fix it
CRITICAL = a main conclusion is wrong; HIGH = an important claim without evidence or wrong; MEDIUM = a gap; LOW = style.
If there are no findings write "None.\"""",
}

EXTRA = {
    # ---------------------------------------------------------------- Portuguese (translation)
    ("pt", "fix", "plan"): """Correção de bug: o plano ataca a CAUSA RAIZ do diagnóstico (não o sintoma), com a alteração mínima, sem refactors nem
funcionalidades extra. O PRIMEIRO passo tem de ser um teste que reproduz o bug e falha (diz que saída esperas dele), e só depois a correção.""",
    ("pt", "fix", "build"): """Correção de bug: escreve PRIMEIRO o teste que reproduz o bug, corre-o e confirma que FALHA (guarda a saída); só então corriges,
com a alteração mínima e sem refactors. No relatório, a seguir à secção «Testes», acrescenta a linha «TESTE DO BUG: ficheiro::nome»
com a saída de quando falhava e a de agora que passa.""",
    ("pt", "fix", "review"): """Correção de bug: confirma pelo diff que existe um teste que reproduz exatamente o sintoma do diagnóstico, que o relatório mostra a
saída de quando falhava, que passa agora, que a alteração é mínima e ataca a causa raiz (não o sintoma), e que não entrou trabalho extra.""",
    ("pt", "fix", "plancheck"): """8. Correção de bug: o plano ataca a causa raiz do diagnóstico (não o sintoma), com alteração mínima; o PRIMEIRO passo é um teste que
   reproduz o bug e falha. Se faltar, é HIGH e o veredicto é BLOQUEADO.""",
    ("pt", "maintenance", "build"): """Manutenção: o comportamento NÃO pode mudar. A suite existente tem de passar exatamente como antes (a linha de base manda); não
acrescentes funcionalidades nem corrijas bugs que apareçam pelo caminho: regista-os no relatório como pendentes.""",
    ("pt", "maintenance", "review"): """Manutenção: confirma que o comportamento não mudou (diff sem alterações funcionais, suite igual à linha de base) e que não entrou
trabalho fora do âmbito. Uma alteração de comportamento é HIGH.""",
    ("pt", "maintenance", "plancheck"): """8. Manutenção: o plano não muda comportamento e diz como se prova isso (suite igual à linha de base, ou testes de caracterização
   escritos antes). Se muda comportamento ou não prova que não muda, é HIGH e o veredicto é BLOQUEADO.""",
    # ---------------------------------------------------------------- English (source)
    ("en", "fix", "plan"): """Bug fix: the plan targets the ROOT CAUSE from the diagnosis (not the symptom), with the minimal change, no refactors or extra
features. The FIRST step must be a test that reproduces the bug and fails (say what output you expect from it), and only then the fix.""",
    ("en", "fix", "build"): """Bug fix: FIRST write the test that reproduces the bug, run it and confirm it FAILS (keep the output); only then fix, with the
minimal change and no refactors. In the report, right after the "Tests" section, add the line "BUG TEST: file::name" with the output
from when it failed and the one from now that it passes.""",
    ("en", "fix", "review"): """Bug fix: confirm from the diff that a test exists that reproduces exactly the diagnosis' symptom, that the report shows the output
from when it failed, that it passes now, that the change is minimal and targets the root cause (not the symptom), and that no extra work crept in.""",
    ("en", "fix", "plancheck"): """8. Bug fix: the plan targets the root cause from the diagnosis (not the symptom), with a minimal change; the FIRST step is a test that
   reproduces the bug and fails. If missing, it is HIGH and the verdict is BLOCKED.""",
    ("en", "maintenance", "build"): """Maintenance: behavior must NOT change. The existing suite must pass exactly as before (the baseline rules); do not add features or
fix bugs you find along the way: record them in the report as pending.""",
    ("en", "maintenance", "review"): """Maintenance: confirm behavior did not change (diff with no functional changes, suite equal to the baseline) and that no out-of-scope
work crept in. A behavior change is HIGH.""",
    ("en", "maintenance", "plancheck"): """8. Maintenance: the plan does not change behavior and says how that is proven (suite equal to the baseline, or characterization tests
   written first). If it changes behavior or does not prove it does not, it is HIGH and the verdict is BLOCKED.""",
}

FEEDBACK_ANALYSIS = {
    "pt": ("A verificação independente (ficheiro {review}) apontou afirmações erradas, sem evidência ou lacunas na análise. Lê-a e corrige "
           "o relatório: primeiro os CRITICAL e HIGH, depois o resto que fizer sentido. Continua só de leitura."),
    "en": ("The independent verification (file {review}) found wrong claims, claims without evidence or gaps in the analysis. Read it and "
           "fix the report: the CRITICAL and HIGH ones first, then the rest where it makes sense. Stay read-only."),
}
