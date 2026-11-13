"""Prompts per request kind (bug fix, analysis, maintenance), in English and Portuguese.

OVERRIDE[(language, kind, phase)] replaces the phase text; EXTRA[(language, kind, phase)] is appended to the normal text. The
"plancheck" phase is the plan validation. Features use the texts of prompts.py / prompts_pt.py unchanged."""

KINDS = ("feature", "fix", "analysis", "maintenance", "ship", "release")
# phases of each kind (the files are the usual ones: spec.md, plan.md, report.md and review.md)
KIND_STAGES = {"feature": ("spec", "plan", "build", "review"), "fix": ("spec", "plan", "build", "review"),
               "maintenance": ("plan", "build", "review"), "analysis": ("build", "review"),
               "release": ("plan", "build", "review"),   # release: plan = what goes to production and how; build = independent regression; review = the promotion
               "ship": ("build", "review")}   # ship: build = prepare the commit locally; review = publish (push / PR) after the user approves

NO_SPEC = {"pt": "o pedido do utilizador acima (neste tipo de pedido não há spec)",
           "en": "the user's request above (this kind of request has no spec)"}

_READ_ONLY_PT = ("SÓ LEITURA: NÃO alteres nada no repo nem fora dele, NÃO faças commits, NÃO instales nada, NÃO cries worktrees e NÃO\n"
                 "escrevas código de produto; só lês e corres comandos de verificação (ls, git log, grep, os testes que já existem sem os\n"
                 "modificar). Não leias nem imprimas .env*, chaves, tokens ou ficheiros de segredos. A única coisa que podes correr que escreve\n"
                 "algo é o comando de sugestão da regra 6 (cria um cartão no dashboard; não altera nada no projeto).")
_READ_ONLY_EN = ("READ-ONLY: do NOT change anything in the repo or outside it, do NOT commit, do NOT install anything, do NOT create\n"
                 "worktrees and do NOT write product code; you only read and run verification commands (ls, git log, grep, the tests\n"
                 "that already exist without modifying them). Do not read or print .env*, keys, tokens or secret files. The only thing you may run\n"
                 "that writes anything is the suggestion command of rule 6 (it creates a dashboard card; it changes nothing in the project).")

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
A ÚNICA exceção à regra de só leitura é o ficheiro do artefacto indicado acima: TENS de o criar lá (não no chat; sem esse ficheiro a
verificação conta como falhada).
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
The ONLY exception to the read-only rule is the artifact file indicated above: you MUST create it there (not in chat; without that
file the verification counts as failed).
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


# ---------------------------------------------------------------- «ship»: commit, push, pull request (two phases, the second one only after the user approves)
SHIP_PT_BUILD = """## Fase 1: preparar o commit (SÓ local)
Trabalhas no repo da pasta atual (NÃO há worktree: é o repo real do utilizador, por isso cuidado). O pedido do utilizador está acima e diz
o que deve ser comitado e, se disser, se o resultado deve ir para um PR ou para o remoto.
1. Vê o estado: `git status`, `git diff --stat`, `git branch -a`, `git remote -v`, `git log --oneline -10`. Se o que o pedido quer comitar
   estiver FORA do git (pastas ignoradas ou fora do repo), diz isso no relatório em vez de forçar.
2. Se estiveres no ramo principal (main/master) ou num ramo que não é o do pedido, cria um ramo novo com um nome claro (ou o que o pedido indicar).
3. Junta SÓ os ficheiros que pertencem ao pedido, um a um (`git add <caminho>`; nunca `git add -A` nem `git add .`). Nunca juntes .env*, chaves,
   tokens, ficheiros de segredos, binários grandes nem nada que o pedido não mencione; deixa o resto como está e lista-o no relatório.
4. Comita com uma mensagem clara (tipo: assunto, e corpo curto). Podes fazer mais do que um commit se o pedido o justificar.
5. NÃO faças push, NÃO abras PR, NÃO mudes remotos nem a configuração do git (`git remote add/set-url`, `git config`), NÃO uses --force, --amend,
   reset --hard, rebase, stash nem apagues ramos, e NÃO mudes para outro ramo que não seja o do pedido. Esta fase só cria o commit no repo local.
   Se os commits do pedido já existem no ramo (a fase repete-se), não os dupliques: descreve-os no relatório.
6. Corre os testes/linter que o projeto já tem, se existirem, e regista o resultado.
O relatório (ficheiro indicado acima) abre com «## Execution status» (EXECUTED AND VERIFIED / PARTIALLY EXECUTED / NOT EXECUTED — motivo) e
tem uma secção «## Ready to publish» com: caminho do repo, ramo atual e ramo base, remoto (nome e URL SEM credenciais), os commits
(`git log --oneline <base>..HEAD`), os ficheiros alterados (`git diff --stat`), o que ficou de fora e porquê, o resultado dos testes, o título e o
corpo propostos para o PR e os comandos EXATOS que a publicação vai correr: `git push -u <nome do remoto> <ramo>` (o NOME de um remoto que já
existe em `git remote`, nunca um URL, e um ramo novo que NÃO seja main/master/develop) e, se o pedido quiser PR, `gh pr create ...`. Escreve o
comando do push sozinho numa linha. O servidor recusa a aprovação se o comando for outro.
Se o pedido é só de commit, diz «Publicação: não pedida» nessa secção. Termina com [DONE] só se o commit existe; senão [BLOCKED]."""

SHIP_PT_REVIEW = """## Fase 2: publicar (aprovada pelo utilizador)
O utilizador viu o relatório {report} e carregou em aprovar: isto autoriza EXATAMENTE os comandos da secção «Ready to publish» desse relatório
e nada mais (a regra do preâmbulo «não fazer push» não se aplica a esses comandos, e só a eles).
1. Confirma que o repo ainda está como o relatório diz: mesmo ramo, `git rev-parse HEAD` igual ao último commit listado e sem commits novos.
   Se algo mudou, NÃO publiques: escreve o que mudou no ficheiro de resultado e termina com [BLOCKED].
2. Se o relatório diz «Publicação: não pedida», não faças nada e escreve isso.
3. Corre `git push -u <remoto> <ramo>` tal como está no relatório, só se o remoto for o NOME de um remoto já configurado e o ramo não for
   main/master/develop. Nunca --force nem --force-with-lease, nem refspecs (`+x`, `HEAD:outro`), nem --delete, --mirror, --all, --tags ou
   --no-verify, nunca outro ramo, nunca outro remoto, nunca mudes remotos. Se o comando do relatório não cumpre isto, NÃO o corras e termina com [BLOCKED].
4. Se o relatório pede PR e a `gh` estiver autenticada, corre o `gh pr create` do relatório (título e corpo dele). Se a `gh` não estiver
   autenticada, NÃO faças login: escreve que falta e termina. Não faças merge do PR nem peças revisões.
O ficheiro de resultado (indicado acima) abre com «## Publication»: o que correste (comandos), o resultado de cada um, o URL do PR se existir e o
que NÃO fizeste. Termina com [DONE] só se tudo o que o relatório previa foi feito; senão [BLOCKED]."""

SHIP_EN_BUILD = """## Phase 1: prepare the commit (local ONLY)
You work in the repo of the current folder (there is NO worktree: this is the user's real repo, so be careful). The user's request is above and says
what to commit and, if it says so, whether the result should go to a PR or to the remote.
1. Look at the state: `git status`, `git diff --stat`, `git branch -a`, `git remote -v`, `git log --oneline -10`. If what the request wants committed is
   OUTSIDE git (ignored folders or outside the repo), say so in the report instead of forcing it.
2. If you are on the main branch (main/master) or on a branch that is not the request's, create a new branch with a clear name (or the one the request gives).
3. Stage ONLY the files that belong to the request, one by one (`git add <path>`; never `git add -A` or `git add .`). Never stage .env*, keys, tokens,
   secret files, large binaries or anything the request does not mention; leave the rest as it is and list it in the report.
4. Commit with a clear message (type: subject, and a short body). You may make more than one commit if the request justifies it.
5. Do NOT push, do NOT open a PR, do NOT change remotes or git configuration (`git remote add/set-url`, `git config`), do NOT use --force, --amend,
   reset --hard, rebase, stash or delete branches, and do NOT switch to a branch other than the request's. This phase only creates the commit in the local repo.
   If the request's commits already exist on the branch (the phase is being repeated), do not duplicate them: describe them in the report.
6. Run the tests/linter the project already has, if any, and record the result.
The report (the file indicated above) opens with «## Execution status» (EXECUTED AND VERIFIED / PARTIALLY EXECUTED / NOT EXECUTED — reason) and has a
«## Ready to publish» section with: the repo path, the current and base branch, the remote (name and URL WITHOUT credentials), the commits
(`git log --oneline <base>..HEAD`), the changed files (`git diff --stat`), what was left out and why, the test result, the proposed PR title and
body, and the EXACT commands that publishing will run: `git push -u <remote name> <branch>` (the NAME of a remote that already exists in
`git remote`, never a URL, and a new branch that is NOT main/master/develop) and, if the request wants a PR, `gh pr create ...`. Write the push
command alone on a line. The server refuses the approval if the command is anything else.
If the request is commit-only, write «Publication: not requested» in that section. End with [DONE] only if the commit exists; otherwise [BLOCKED]."""

SHIP_EN_REVIEW = """## Phase 2: publish (approved by the user)
The user saw the report {report} and clicked approve: this authorises EXACTLY the commands in that report's «Ready to publish» section and nothing
else (the preamble's «do not push» rule does not apply to those commands, and only to them).
1. Confirm the repo is still as the report says: same branch, `git rev-parse HEAD` equal to the last listed commit and no new commits.
   If anything changed, do NOT publish: write what changed in the result file and end with [BLOCKED].
2. If the report says «Publication: not requested», do nothing and write that.
3. Run `git push -u <remote> <branch>` exactly as in the report, only if the remote is the NAME of an already configured remote and the branch is
   not main/master/develop. Never --force or --force-with-lease, no refspecs (`+x`, `HEAD:other`), no --delete, --mirror, --all, --tags or
   --no-verify, never another branch, never another remote, never change remotes. If the report's command does not meet this, do NOT run it and end with [BLOCKED].
4. If the report asks for a PR and `gh` is authenticated, run the report's `gh pr create` (its title and body). If `gh` is not authenticated, do NOT
   log in: write that it is missing and stop. Do not merge the PR or request reviewers.
The result file (indicated above) opens with «## Publication»: the commands you ran, the result of each, the PR URL if any and what you did NOT do.
End with [DONE] only if everything the report foresaw was done; otherwise [BLOCKED]."""

OVERRIDE.update({("pt", "ship", "build"): SHIP_PT_BUILD, ("pt", "ship", "review"): SHIP_PT_REVIEW,
                 ("en", "ship", "build"): SHIP_EN_BUILD, ("en", "ship", "review"): SHIP_EN_REVIEW})


# ---------------------------------------------------------------- «release»: passagem a produção (plano -> regressão independente -> passagem)
# The regression verdict is a gate the server enforces (features.release_gate): only a POSITIVE verdict for the planned candidate lets
# the user approve the promotion, and the promotion only runs the plan's steps.
REL_PT_PLAN = """## Fase 1: plano da passagem a produção (SÓ LEITURA)
""" + _READ_ONLY_PT + """
A ÚNICA exceção à regra de só leitura é o ficheiro do plano indicado acima: TENS de o criar lá. NÃO executes nenhum passo da passagem agora.
O pedido do utilizador está acima e diz o que sobe a produção. Inspeciona o repo (git log, ramos, PRs que o pedido cite, CI/CD, README/CLAUDE.md,
scripts de deploy) para escrever um plano que outro worker, sem o teu contexto, consegue verificar e executar. Secções OBRIGATÓRIAS (títulos em inglês):
## Candidate — a linha `Candidate commit: <sha completo>` (o commit exato que vai subir; `git rev-parse`), o ramo, o ramo/ambiente de destino, os PRs e
   commits incluídos e o que muda para os utilizadores.
## Regression tests — os comandos EXATOS que o verificador independente vai correr no código candidato, em local (suite completa, E2E, linter, build) e,
   para cada um, o resultado esperado; diz também o que o repo tem e NÃO dá para correr em local.
## Promotion steps — os comandos EXATOS da passagem, por ordem, um por linha em blocos de código, com o resultado esperado de cada um. Sem --force, sem
   outros ramos ou remotos além dos do plano, sem ler ou imprimir segredos; se a passagem depende de CI/CD, diz qual é o passo que o dispara.
## Post-checks — como confirmar depois da passagem que está tudo bem (comandos ou URLs a verificar, só leitura).
## Rollback — os passos EXATOS para voltar atrás (são para o utilizador: o worker da passagem NUNCA os executa sozinho).
## Risks — o que pode correr mal e o que NÃO foi possível confirmar.
Sem estas secções e a linha do commit o servidor recusa a aprovação. Termina com [DONE]."""

REL_PT_BUILD = """## Fase 2: regressão independente (SÓ LEITURA em produção)
És o verificador independente: NÃO escreveste o plano e não deves confiar nele. """ + _READ_ONLY_PT + """
Exceções à regra de só leitura: (a) o relatório indicado acima, que TENS de criar lá; (b) um worktree descartável do commit candidato em {work}
(`git worktree add --detach {work} <sha>`), onde corres os testes. Não toques em produção, em ambientes partilhados nem em dados reais.
Plano a verificar: {plan}. Faz, por esta ordem:
1. Confirma que o commit `Candidate commit` do plano existe e cria o worktree nesse commit; confirma com `git -C {work} rev-parse HEAD`.
2. Corre os comandos da secção «Regression tests» do plano e o que mais o CI do repo corre e seja relevante, EM LOCAL. Corre também a suite no ramo
   base/destino quando for barato, para separar falhas que já existiam das novas.
3. Regista, para cada comando: o comando, o resultado (passou/falhou, contagens) e a evidência (saída relevante). Nada de afirmações sem evidência.
4. Veredicto: POSITIVE só se TODOS os testes necessários correram e não há falhas novas em relação à base. NEGATIVE se algum falhou (nova), se algum
   teste necessário NÃO correu, se o commit não existe ou se não conseguiste confirmar algo essencial. Na dúvida, NEGATIVE.
O relatório abre com `## Regression verdict` e, na linha seguinte, só a palavra POSITIVE ou NEGATIVE; depois `Candidate commit: <sha testado>`, a
tabela de comandos e resultados, as falhas já existentes na base, o que não correu e porquê, e as razões do veredicto. Termina com [DONE]."""

REL_PT_REVIEW = """## Fase 3: passagem a produção (aprovada pelo utilizador)
O utilizador viu o plano {plan} e o relatório de regressão {report} (veredicto POSITIVE) e carregou em aprovar: isto autoriza EXATAMENTE os comandos da
secção «Promotion steps» do plano e os «Post-checks», e nada mais (a regra do preâmbulo «não fazer push» não se aplica a esses comandos, e só a eles).
1. Confirma antes de tudo: o `Candidate commit` do plano e o do relatório são o mesmo e o repo/ramo está como o plano diz (`git rev-parse HEAD`, sem
   commits novos). Se algo diverge, NÃO executes nada: escreve o que diverge e termina com [BLOCKED].
2. Executa os passos por ordem, um a um, e verifica o resultado esperado de cada um antes do seguinte. Para ao primeiro que falhe ou divirja.
3. Nunca --force nem --force-with-lease, nem refspecs, nem outros ramos ou remotos, nem passos que não estejam no plano, nem leitura ou impressão de
   segredos. Não executes o rollback por tua conta: se algo falhar, pára, descreve o estado e deixa os passos de «Rollback» para o utilizador.
4. Se tudo correu, corre os «Post-checks» (só leitura) e regista o resultado.
O ficheiro de resultado (indicado acima) abre com `## Promotion`: cada comando que correste e o seu resultado, os post-checks, e o que NÃO fizeste.
Termina com [DONE] só se todos os passos e post-checks passaram; senão [BLOCKED]."""

REL_EN_PLAN = """## Phase 1: production promotion plan (READ-ONLY)
""" + _READ_ONLY_EN + """
The ONLY exception to the read-only rule is the plan file indicated above: you MUST create it there. Do NOT run any promotion step now.
The user's request is above and says what goes to production. Inspect the repo (git log, branches, the PRs the request cites, CI/CD, README/CLAUDE.md,
deploy scripts) to write a plan that another worker, without your context, can verify and carry out. REQUIRED sections (headings in English):
## Candidate — the line `Candidate commit: <full sha>` (the exact commit that goes up; `git rev-parse`), the branch, the target branch/environment, the PRs
   and commits included and what changes for users.
## Regression tests — the EXACT commands the independent verifier will run on the candidate code, locally (full suite, E2E, linter, build) and, for
   each, the expected result; also say what the repo has that can NOT be run locally.
## Promotion steps — the EXACT promotion commands, in order, one per line in code blocks, with the expected result of each. No --force, no branches or
   remotes other than the plan's, no reading or printing secrets; if the promotion depends on CI/CD, say which step triggers it.
## Post-checks — how to confirm after the promotion that all is well (commands or URLs to check, read-only).
## Rollback — the EXACT steps to go back (they are for the user: the promotion worker NEVER runs them by itself).
## Risks — what can go wrong and what could NOT be confirmed.
Without these sections and the commit line the server refuses the approval. End with [DONE]."""

REL_EN_BUILD = """## Phase 2: independent regression (READ-ONLY on production)
You are the independent verifier: you did NOT write the plan and must not trust it. """ + _READ_ONLY_EN + """
Exceptions to the read-only rule: (a) the report indicated above, which you MUST create there; (b) a throw-away worktree of the candidate commit at {work}
(`git worktree add --detach {work} <sha>`), where you run the tests. Do not touch production, shared environments or real data.
Plan to verify: {plan}. Do, in this order:
1. Confirm the plan's `Candidate commit` exists and create the worktree at it; confirm with `git -C {work} rev-parse HEAD`.
2. Run the commands in the plan's «Regression tests» section and whatever else the repo's CI runs that is relevant, LOCALLY. Also run the suite on the
   base/target branch when it is cheap, to tell failures that already existed from new ones.
3. Record, for each command: the command, the result (pass/fail, counts) and the evidence (relevant output). No claims without evidence.
4. Verdict: POSITIVE only if ALL the necessary tests ran and there are no new failures against the base. NEGATIVE if any failed (new), if any necessary
   test did NOT run, if the commit does not exist or if you could not confirm something essential. When in doubt, NEGATIVE.
The report opens with `## Regression verdict` and, on the next line, only the word POSITIVE or NEGATIVE; then `Candidate commit: <tested sha>`, the table
of commands and results, the failures that already existed on the base, what did not run and why, and the reasons for the verdict. End with [DONE]."""

REL_EN_REVIEW = """## Phase 3: production promotion (approved by the user)
The user saw the plan {plan} and the regression report {report} (verdict POSITIVE) and clicked approve: this authorises EXACTLY the commands in the plan's
«Promotion steps» section and its «Post-checks», and nothing else (the preamble's «do not push» rule does not apply to those commands, and only to them).
1. First confirm: the plan's `Candidate commit` and the report's are the same and the repo/branch is as the plan says (`git rev-parse HEAD`, no new
   commits). If anything diverges, run NOTHING: write what diverges and end with [BLOCKED].
2. Run the steps in order, one at a time, and check each one's expected result before the next. Stop at the first that fails or diverges.
3. Never --force or --force-with-lease, no refspecs, no other branches or remotes, no steps that are not in the plan, no reading or printing secrets. Do
   not run the rollback by yourself: if something fails, stop, describe the state and leave the «Rollback» steps to the user.
4. If everything ran, run the «Post-checks» (read-only) and record the result.
The result file (indicated above) opens with `## Promotion`: each command you ran and its result, the post-checks, and what you did NOT do.
End with [DONE] only if all steps and post-checks passed; otherwise [BLOCKED]."""

OVERRIDE.update({("pt", "release", "plan"): REL_PT_PLAN, ("pt", "release", "build"): REL_PT_BUILD, ("pt", "release", "review"): REL_PT_REVIEW,
                 ("en", "release", "plan"): REL_EN_PLAN, ("en", "release", "build"): REL_EN_BUILD, ("en", "release", "review"): REL_EN_REVIEW})
