"""Prompt for a follow-up question about a request («Ask the worker»), in English and Portuguese.

The worker is read-only and answers into one file. It never changes the request: the user decides on the card."""

READ_ONLY = {
    "en": ("READ-ONLY: do NOT change anything in the repo or outside it, do NOT commit, do NOT install anything and do NOT write\n"
           "product code; you only read and run verification commands (ls, git log, grep, the tests that already exist without\n"
           "modifying them). The ONLY file you create is the answer file indicated below: never write to any other file, not even\n"
           "in the request's folder, and ignore rules 4 and 6 of the rules above (leave NO note in the vault inbox and make NO suggestions for a question). Do not\n"
           "read or print .env*, keys, tokens or secret files."),
    "pt": ("SÓ LEITURA: NÃO alteres nada no repo nem fora dele, NÃO faças commits, NÃO instales nada e NÃO escrevas código de produto;\n"
           "só lês e corres comandos de verificação (ls, git log, grep, os testes que já existem sem os modificar). O ÚNICO ficheiro\n"
           "que crias é o da resposta, indicado abaixo: não escrevas em mais nenhum ficheiro, nem na pasta do pedido, e ignora as\n"
           "regras 4 e 6 das regras acima (NÃO deixes nota na inbox do vault nem sugestões por uma pergunta). Não leias nem imprimas .env*, chaves,\n"
           "tokens ou ficheiros de segredos."),
}

TASK = {
    "en": """## Follow-up question about a request
The user is reading request «{title}» (type {kind}, id {fid}) in project {project} and has a question about it. {read_only}

## The request, as the user wrote it
{desc}

## What exists so far (read what you need; these are the request's artifacts)
{files}
{history}
## The user's question
{question}

Answer it: be direct, start with the answer, then the evidence (file:line, or the command and its result). Re-check the facts
yourself instead of repeating what an earlier document says; if you cannot confirm something, say so. If the question shows
the earlier work was wrong, say so plainly. Do NOT start the next phase and do NOT redo the work: this is a question, and the
user will decide what happens next on the card.
Write the answer (markdown, in English, readable on its own) to {out} and end with [DONE].""",
    "pt": """## Pergunta de seguimento sobre um pedido
O utilizador está a ler o pedido «{title}» (tipo {kind}, id {fid}) do projeto {project} e tem uma dúvida sobre ele. {read_only}

## O pedido, como o utilizador o escreveu
{desc}

## O que já existe (lê o que precisares; são os artefactos do pedido)
{files}
{history}
## A pergunta do utilizador
{question}

Responde: direto, começa pela resposta e depois a evidência (ficheiro:linha, ou o comando e o resultado). Volta a confirmar tu os
factos em vez de repetires o que um documento anterior diz; o que não conseguires confirmar, di-lo. Se a pergunta mostrar que o
trabalho anterior estava errado, di-lo com clareza. NÃO comeces a fase seguinte e NÃO refaças o trabalho: isto é uma pergunta, e o
utilizador decide no cartão o que acontece a seguir.
Escreve a resposta (markdown, em português europeu, que se perceba sozinha) em {out} e termina com [DONE].""",
}

HISTORY_HEAD = {"en": "\n## Earlier questions and answers on this request\n", "pt": "\n## Perguntas e respostas anteriores deste pedido\n"}
NO_FILES = {"en": "(no artifact has been written yet; answer from the request and the project)",
            "pt": "(ainda não há artefactos; responde com base no pedido e no projeto)"}
