"""Portuguese (European) versions of the feature-pipeline prompts: the optional translation of prompts.py.

Used when a request was created with the dashboard in Portuguese. Same structure and same {placeholders} as the English
originals in prompts.py. The section headings are Portuguese here on purpose: features.parse_review and the dashboard parsers
accept both languages."""

NOTE_LANG_PT = "Portuguese (European)"


def language_rule(note_lang: str = "English") -> str:
    """Prepended to every prompt. Only needed when the vault notes are not in Portuguese."""
    if note_lang == NOTE_LANG_PT:
        return ""
    return f"(Exceção: qualquer nota que deixes na inbox do vault escreve-a em {note_lang}, porque o vault está nessa língua.)\n\n"


OUT_MARKER = "Escreve o resultado em {out}"

NO_VAULT_NOTE = ("\n\nNão há vault configurado: ignora qualquer menção acima a uma nota do vault ou à inbox do vault, "
                 "e não cries notas nem ficheiros para além do que esta fase pede.")

COMMON = """Feature «{title}» (id {fid}) no projeto {project}.

## Pedido do utilizador
{desc}

Escreve o artefacto em {path} (cria o ficheiro; markdown em português europeu). O utilizador vai lê-lo no dashboard
e aprovar ou pedir alterações, por isso escreve-o completo e autónomo.

Dúvidas que precises que o utilizador resolva (não adivinhes) escreves-as em DOIS sítios:
1. Na secção «## Perguntas em aberto» do artefacto, legível por quem lê só o documento:
   - **P1 — Título curto:** pergunta clara e fechada?
     - [ ] Opção A (recomendada) — consequência numa frase
     - [ ] Opção B — consequência numa frase
2. No ficheiro {qpath}, que o dashboard usa para montar o formulário de respostas (JSON válido; `[]` se não houver dúvidas):
   [{{"id": "P1", "title": "Título curto", "text": "Pergunta clara e fechada?",
     "options": [{{"label": "Opção A — consequência", "recommended": true}}, {{"label": "Opção B — consequência"}}]}}]
Dá 2 a 4 opções concretas e mutuamente exclusivas e marca a tua recomendação com «recommended» (e «(recomendada)» no documento).
O utilizador pode ainda escrever um comentário ou uma pergunta sobre cada questão. Se precisas de respostas antes de poderes concluir,
escreve na mesma a melhor versão provisória do artefacto, com as tuas suposições assinaladas, e as perguntas nos dois sítios.
Termina com [DONE]."""

STAGE_TEXT = {
    "spec": """## Fase 1: especificação
Usa a skill `superpowers:brainstorming` (ferramenta Skill) para explorar o pedido e desenhar a spec. Adaptações obrigatórias, porque
és um worker em segundo plano e não podes conversar com o utilizador: NÃO faças perguntas em chat nem esperes por respostas (regista
cada dúvida em «Perguntas em aberto», no formato indicado, com a tua recomendação); NÃO escrevas em docs/superpowers/specs nem faças
commits no repo (o artefacto é o ficheiro indicado acima, fora do repo); salta o passo de pedir a aprovação do documento (a aprovação
é feita no dashboard, pelo utilizador).
Lê o código e a nota do vault do projeto para ancorar a spec no que existe. NÃO escrevas código de produto.
Secções: Objetivo e porquê · Fora de âmbito · Requisitos · Critérios de aceitação (verificáveis) · Riscos · Perguntas em aberto.""",
    "plan": """## Fase 2: plano de implementação
Usa a skill `superpowers:writing-plans` (ferramenta Skill) para escrever o plano. Adaptações: o plano é o ficheiro indicado acima,
não docs/superpowers/plans; sem commits no repo; não ofereças opções de execução (a execução é a fase seguinte, decidida pelo
utilizador no dashboard); dúvidas vão para «Perguntas em aberto», não para o chat.
A spec aprovada está em {spec}. Lê-a. NÃO escrevas código de produto.
Secções: Abordagem · Ficheiros a criar/alterar (caminhos reais do repo) · Testes · Passos ordenados, cada um com o teste que o valida
(testes primeiro) · Riscos e reversão · Perguntas em aberto.
A secção «Testes» é obrigatória e traz: (1) o comando que corre a suite COMPLETA do projeto (descobre-o no repo: README, scripts, CI) e
onde ficam os testes, seguindo a convenção que o repo já usa; (2) uma tabela «Critério de aceitação | Cenário de teste | Onde
(ficheiro::teste)», com pelo menos um teste por critério da spec e os casos de erro e limites relevantes; (3) a regra de regressão: o
implementador corre a suite completa ANTES de alterar nada (linha de base) e outra vez no fim, e nada que passava pode falhar.
Se o repo NÃO tem testes, o primeiro passo do plano cria o mínimo para os ter (framework, comando para os correr, um teste real).
Exceção: repos sem código (documentação, infraestrutura): escreve «sem testes: <motivo>» e diz como se verifica à mão.""",
    "build": """## Fase 3: implementação
Spec aprovada: {spec}. Plano aprovado: {plan}. Lê ambos e segue o plano com a skill `superpowers:executing-plans`.
Em todo o código novo usa `superpowers:test-driven-development` (teste falhado primeiro, depois a implementação mínima, depois
refatorar) e, se um teste falhar sem razão óbvia, `superpowers:systematic-debugging`. Antes de dizeres que está feito, usa
`superpowers:verification-before-completion`: corre os comandos e confirma o resultado, não o presumas. O worktree está definido
abaixo, por isso não uses `superpowers:using-git-worktrees` nem `superpowers:finishing-a-development-branch` (o merge é do utilizador).
Podem estar outras features a correr em paralelo neste repositório, por isso NÃO faças checkout nem alterações na pasta
principal do repo. Cria o teu próprio worktree, num ramo novo, e trabalha só lá dentro:
  git worktree add -b feat/{fid} "{work}"
ANTES de alterares qualquer ficheiro, corre a suite COMPLETA de testes do projeto (o comando está na secção «Testes» do plano) e regista
a linha de base: quantos passam e quantos já falham. Se o repo não tem testes, cria primeiro o mínimo que o plano prevê.
Testes primeiro, depois código. NÃO faças commits: deixa as alterações por comitar no worktree (o pipeline comita só depois da
revisão independente); NÃO faças push e não removas o worktree (diz no relatório onde está).
Não executes ações irreversíveis nem em sistemas partilhados ou de produção (servidores, apagar dados, deploys, migrações reais):
escreve-as como passos para o utilizador executar DEPOIS da revisão.
Corre os testes e o linter do projeto antes de dizeres que está feito, e não afirmes que passa sem o ter corrido.
O artefacto é o relatório: o que mudou (ficheiros), resultado real dos testes, como verificar à mão, o que ficou por fazer.
A seguir ao «Estado da execução», o relatório tem a secção «## Testes», cuja primeira linha é exatamente uma destas:
SEM REGRESSÕES | COM REGRESSÕES | NÃO CORRIDOS — <motivo> | SEM TESTES NO PROJETO — <motivo>
Seguida, exatamente neste formato e com os números REAIS da suite COMPLETA, de:
LINHA DE BASE: <P> passam, <F> falham   (antes de alterares nada)
RESULTADO FINAL: <P> passam, <F> falham   (depois, com o mesmo comando)
TESTES NOVOS: <N>
e depois o comando usado e a tabela «Critério de aceitação | Teste (ficheiro::nome)». Regra de regressão: nada do que passava na linha de base pode
falhar no fim; se algo falha, corrige-o ou diz-o aqui, nunca o escondas.
O relatório começa SEMPRE pela secção «## Estado da execução», cuja primeira linha é exatamente uma destas:
EXECUTADO E VERIFICADO | PARCIALMENTE EXECUTADO | NÃO EXECUTADO — <motivo numa frase>
Seguida de «Verificado por mim» (cada coisa com o comando que correste e o resultado real) e «Não verificado» (o que depende de
outra pessoa ou de acesso que não tens). Se não correste nada que prove o resultado, o estado é NÃO EXECUTADO: um relatório de
passos «para executares» é um runbook, não uma implementação. Só termina com [DONE] se o estado for EXECUTADO E VERIFICADO;
caso contrário termina com [BLOCKED] e diz o que falta. Se o projeto não é um repositório git, não há worktree: diz-o no relatório.""",
    "review": """## Fase 4: revisão independente
És o revisor independente: NÃO foste tu que implementaste e não deves confiar no relatório. NÃO alteres código, NÃO faças commits
nem checkouts; só lês e corres comandos de verificação. Usa a skill `superpowers:requesting-code-review` para conduzir a revisão
(revisão do diff por um revisor de código) e `superpowers:verification-before-completion` para confirmares com comandos reais antes
de escreveres o veredicto. O resultado vai para o ficheiro indicado acima, no formato abaixo (não para o chat).
Spec: {spec}. Plano: {plan}. Relatório do implementador: {report}. O código está no worktree "{work}" (ramo feat/{fid}), por comitar:
o implementador não comitou, o commit só acontece depois da tua revisão.
Faz, por esta ordem:
1. Vê o que mudou: `git -C "{work}" status --short`, `git -C "{work}" diff HEAD` (ficheiros já seguidos) e lê os ficheiros novos
   (`git -C "{work}" ls-files --others --exclude-standard`). Se mesmo assim houver commits no ramo
   (`git -C "{work}" log --oneline main..feat/{fid}`), vê também `git -C "{work}" diff main...feat/{fid}`. Lê tudo o que mudou.
2. Corre a suite COMPLETA de testes e o linter do projeto no worktree (não só os testes novos), compara com a linha de base da secção
   «Testes» do relatório e regista o resultado REAL. Confirma que cada critério de aceitação tem pelo menos um teste (tabela do
   relatório) e que esse teste existe no diff.
3. Confere cada critério de aceitação da spec: cumprido / não cumprido / não verificável, com a evidência.
4. Procura: segredos no diff, validação de input, injeção (SQL/comandos), caminhos de ficheiros, erros engolidos, testes em falta
   (alvo 80%), funções ou ficheiros demasiado grandes, e alterações fora do âmbito da spec.
Escreve o artefacto exatamente neste formato:
## Veredicto
APROVADO, COM RESERVAS ou BLOQUEADO, e uma frase. (BLOQUEADO se houver algum CRITICAL ou HIGH.)
Confere o «Estado da execução» do relatório com o que encontras: se diz NÃO EXECUTADO ou PARCIALMENTE EXECUTADO, ou se não consegues
comprovar a execução, os critérios que dependem dela ficam «não verificável» e o veredicto nunca é APROVADO (no máximo COM RESERVAS).
Testes: o veredicto é BLOQUEADO se algum teste que passava na linha de base falha agora, se um critério de aceitação não tem teste,
se os testes não foram corridos sem motivo aceitável, ou se o repo não tinha testes e a feature não criou o mínimo para os ter.
## Verificações
Os comandos que correste e o resultado real.
## Testes
Obrigatória. Corres TU a suite COMPLETA (não copies os números do implementador). A primeira linha é exatamente uma destas:
CONFIRMADOS | DIVERGENTES — <o que difere> | NÃO CORRIDOS — <motivo> | SEM TESTES NO PROJETO — <motivo>
CONFIRMADOS só se os teus números coincidem com o «RESULTADO FINAL» do relatório e nada do que passava na linha de base falha agora.
Segue-se, neste formato: RESULTADO DO REVISOR: <P> passam, <F> falham   e o comando que usaste.
## Critérios de aceitação
| Critério | Estado | Evidência |
## Achados
- **[CRITICAL|HIGH|MEDIUM|LOW] ficheiro:linha — título:** o que está mal e como corrigir
CRITICAL = segurança ou perda de dados; HIGH = bug ou problema sério de qualidade; MEDIUM = manutenção; LOW = estilo.
Se não houver achados escreve «Nenhum.»""",
}

PLANCHECK = """Feature «{title}» (id {fid}) no projeto {project}.

## Pedido do utilizador
{desc}

## Fase 2b: validação independente do plano
És o validador independente do plano: NÃO foste tu que o escreveste e não deves confiar nele. É SÓ LEITURA: NÃO alteres código nem o
plano, NÃO faças commits nem checkouts e NÃO instales nada; só lês e corres comandos de verificação (ls, git log, grep). Não leias nem
imprimas .env*, chaves, tokens ou ficheiros de segredos. Trabalha só dentro do repo do projeto e da pasta dos artefactos.
Spec aprovada: {spec}. Plano a validar: {plan}. Lê ambos e confere o plano com o repo real.
Verifica, por esta ordem:
1. Cobertura: cada requisito e cada critério de aceitação da spec tem passos no plano que o cumprem; nada do «fora de âmbito» entrou.
2. Realidade: os ficheiros, funções, módulos e comandos que o plano cita existem (confirma-o com comandos); os ficheiros a criar não
   colidem com outros já existentes.
3. Ordem e testes: testes primeiro; cada passo indica o teste que o valida; cada passo só depende de passos anteriores.
4. Âmbito: sem trabalho a mais do que a spec pede.
5. Segurança e reversão: ações irreversíveis ou em sistemas partilhados ou de produção ficam como passos para o utilizador, depois
   da revisão; há reversão para o que muda; não há segredos no plano.
6. Perguntas em aberto: as da spec estão respondidas ou assumidas de forma explícita; o plano não esconde decisões por tomar.
7. Testes: a secção «Testes» do plano existe e traz o comando da suite completa, um cenário de teste por critério de aceitação (e os
   erros e limites relevantes) e a regra da linha de base mais regressão. Vê no repo se já tem testes: se NÃO tem e o plano não cria o
   mínimo para os ter (framework, comando, um teste real) como primeiro passo, é HIGH e o veredicto é BLOQUEADO. Exceção: repos sem
   código (documentação, infraestrutura), onde o plano tem de dizer «sem testes: <motivo>» e como se verifica à mão.
Escreve o resultado em {out} (não no chat), exatamente neste formato:
## Veredicto
APROVADO, COM RESERVAS ou BLOQUEADO, e uma frase. (BLOQUEADO se houver algum CRITICAL ou HIGH.)
## Verificações
Os comandos que correste e o resultado real.
## Cobertura dos critérios de aceitação
| Critério | Passos do plano que o cumprem | Estado |
## Achados
- **[CRITICAL|HIGH|MEDIUM|LOW] secção do plano — título:** o que está mal e como corrigir
CRITICAL = o plano levaria a perda de dados, falha de segurança ou a algo contrário à spec; HIGH = lacuna ou erro que faria a
implementação falhar ou sair errada; MEDIUM = ambiguidade ou risco; LOW = estilo.
Se não houver achados escreve «Nenhum.»
Termina com [DONE]."""

BUILD_RESUME = """## Fase 3: implementação (continuação)
O worktree desta implementação já existe em "{work}" (ramo feat/{fid}): trabalha lá com `cd "{work}"` ou caminhos absolutos. NÃO uses a ferramenta EnterWorktree (pede licença ao utilizador e um worker em segundo plano não a pode dar, ficaria bloqueado). NÃO o voltes a criar nem
faças checkout na pasta principal do repo. Spec: {spec}. Plano: {plan}.
Usa `superpowers:test-driven-development` (teste falhado primeiro) e termina com `superpowers:verification-before-completion`;
NÃO faças commits (o pipeline comita depois da revisão) nem push; não executes ações irreversíveis nem em sistemas partilhados
(escreve-as como passos para o utilizador). Corre os testes e o linter antes de dizeres que está feito e não afirmes que passa sem o
ter corrido. Atualiza o relatório: o que mudou, resultado real dos testes, o que ficou por fazer. Mantém a secção «## Testes» (primeira
linha: SEM REGRESSÕES | COM REGRESSÕES | NÃO CORRIDOS — <motivo> | SEM TESTES NO PROJETO — <motivo>): a linha de base já registada
mantém-se; corre a suite COMPLETA outra vez, compara com ela e atualiza as linhas «RESULTADO FINAL: <P> passam, <F> falham» e
«TESTES NOVOS: <N>» e a tabela «Critério | Teste».
O relatório começa SEMPRE pela secção «## Estado da execução», cuja primeira linha é exatamente uma destas:
EXECUTADO E VERIFICADO | PARCIALMENTE EXECUTADO | NÃO EXECUTADO — <motivo numa frase>
Seguida de «Verificado por mim» (cada coisa com o comando que correste e o resultado real) e «Não verificado» (o que depende de
outra pessoa ou de acesso que não tens). Se não correste nada que prove o resultado, o estado é NÃO EXECUTADO: um relatório de
passos «para executares» é um runbook, não uma implementação. Só termina com [DONE] se o estado for EXECUTADO E VERIFICADO;
caso contrário termina com [BLOCKED] e diz o que falta. Se o projeto não é um repositório git, não há worktree: diz-o no relatório."""

# O tamanho que um plano pode ter antes de dever ser partido (ver prompts.PLAN_SIZE).
PLAN_SIZE = ("\nTamanho: depois dos passos ordenados escreve a linha «TASKS: <número de passos ordenados>». Se o trabalho precisar de "
             "mais de {max} passos, ou de uma implementação que não se revê de uma vez, NÃO planeies tudo: planeia a primeira parte "
             "autónoma e lista o resto em «## Pedidos seguintes» (uma linha cada: um título e o que cobre), para que cada parte seja "
             "implementada e revista por si.")
PLANCHECK_SIZE = ("8. Tamanho: o plano tem uma linha «TASKS: <n>». Mais de {max} passos, ou partes que não se verificam de forma "
                  "independente, é um achado MEDIUM com o título «tamanho» que propõe como partir o trabalho em pedidos separados.")

# Um plano que se divide em partes independentes declara-as numa secção «Divisão»; o servidor confere o bloco e, quando o
# utilizador aprova o plano, cada parte passa a ser um pedido próprio. SPLIT_FORMAT tem chavetas JSON: juntar DEPOIS de .format(...).
SPLIT_FORMAT = ("\nDivisão (opcional): quando o trabalho se divide em partes que têm cada uma os seus ficheiros e testes, "
                "acrescenta uma secção com o título \"## Divisão\" com UM só bloco json delimitado, e nenhum outro bloco nessa secção:\n"
                "```json\n"
                "{\"tarefas\": [{\"id\": \"api\", \"title\": \"Endpoint de exportação\", "
                "\"ficheiros\": [\"src/export.py\", {\"new\": \"tests/test_export.py\"}], \"depende_de\": []}, "
                "{\"id\": \"ui\", \"title\": \"Botão de exportar\", \"ficheiros\": [\"app/page.html\"], \"depende_de\": []}]}\n"
                "```\n"
                "Regras: os ids são slugs curtos, únicos, em minúsculas (letras, dígitos, hífenes, no máximo 20 caracteres); "
                "2 a 10 partes; `depende_de` fica VAZIO numa parte independente (as independentes correm em paralelo) e, caso contrário, lista os "
                "ids de partes ANTERIORES de cujo código a parte precisa (só arranca depois de essas estarem comitadas e revistas, e "
                "parte do código delas); nenhum ficheiro "
                "aparece em duas partes; um ficheiro que ainda não existe escreve-se {\"new\": \"caminho\"}; o plano continua a "
                "precisar da linha «TASKS: <n>» e da secção de Testes. Um bloco de divisão substitui a lista «Follow-up requests» "
                "acima: usa uma ou outra, nunca as duas. Não uses o bloco num plano que já é uma parte de uma divisão, nem numa "
                "passagem a produção (release).")
SPLIT_CHECK = ("9. Divisão: se o plano tem uma secção «Divisão», o servidor já confere ids, ficheiros e sobreposição; tu só "
               "confirmas que cada parte é autónoma (pode ser construída e revista sozinha) e assinalas estado oculto partilhado "
               "entre partes como achado MEDIUM com o título \"split-coupling\".")

FEEDBACK_SELECTED = ("A revisão independente (ficheiro {review}) apontou achados e o utilizador decidiu um a um. Corrige SÓ os "
                     "achados listados em «Achados a corrigir»; os «aceites» foram aceites como estão e não lhes tocas. "
                     "Mantém os testes a passar.")
FEEDBACK_REVIEW = ("A revisão independente (ficheiro {review}) apontou problemas na implementação. Lê-a e corrige primeiro os "
                   "CRITICAL e HIGH, depois o resto que fizer sentido, mantendo os testes a passar.")
FEEDBACK_PLAN = ("A validação independente do plano (ficheiro {path}) bloqueou-o. Lê-a e corrige o plano: primeiro os "
                 "CRITICAL e HIGH, depois o resto que fizer sentido. Mantém a estrutura e as secções.")
USER_INSTRUCTIONS = "\nInstruções do utilizador: {comment}"
CHANGES_BLOCK = ("\n\n## Alterações pedidas pelo utilizador\n{feedback}\n"
                 "A versão anterior está em {prev}; revê-a e escreve a nova versão em {path}.\n"
                 "Se o utilizador fez perguntas ou tem dúvidas, responde-lhes de forma direta numa secção "
                 "«## Respostas às tuas dúvidas», dá as questões respondidas como decididas e deixa em "
                 "«## Perguntas em aberto» só o que ainda falta (no mesmo formato, com opções).")

# Acrescentado ao prompt de todas as fases (features._task, validação do plano e «Perguntar ao worker»): o cartão mostra o artefacto
# tal como está, por isso o artefacto é curto e feito de tópicos. O que o worker seguinte precisa por extenso vai para um ficheiro ao lado.
BRIEF = """

## Como escrever (o utilizador lê isto num cartão pequeno)
- Só tópicos: sem parágrafos de prosa, sem introdução, sem resumo final, e sem repetir o pedido do utilizador nem estas instruções.
- Uma linha por tópico, no máximo 25 palavras; subtópicos só quando trazem um facto (um caminho, um comando, um número).
- Mantém todas as secções, títulos, tabelas e linhas fixas que esta fase pede, exatamente como pedido: são lidos pelo servidor.
  Tabelas só onde a fase pede uma.
- Tamanho: aponta para 40 linhas, nunca mais de 80 (perguntas incluídas). Diz cada coisa uma vez.
- Material longo (saída de comandos, código, detalhe passo a passo de que o worker seguinte precisa, logs) NÃO vai no artefacto:
  escreve-o num ficheiro ao lado chamado `<nome do artefacto>-details.md` e aponta para ele com um tópico. Nunca coles segredos em nenhum dos dois.
- A tua mensagem final no chat: [DONE], [BLOCKED] ou [QUESTION] e no máximo 5 tópicos curtos."""
