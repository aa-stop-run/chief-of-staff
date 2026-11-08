"""User-facing server messages, in English (the source language) and Portuguese (optional translation).

English is the key: `translate(msg, "pt")` returns the Portuguese version, or the message itself if there is no translation.
`{0}`, `{1}` mark the variable parts (names, numbers, errors from other programs), which are kept as they are."""
import re

PAIRS = [
    # server
    ("origin not allowed", "origem não permitida"),
    ("invalid token; reload the page", "token inválido; recarrega a página"),
    ("JSON expected", "esperado JSON"),
    ("empty or too large body", "corpo vazio ou grande demais"),
    ("invalid JSON", "JSON inválido"),
    ("object expected", "objeto esperado"),
    ("internal error (see the server console)", "erro interno (ver consola do servidor)"),
    ("unknown route", "rota desconhecida"),
    ("unknown project", "projeto desconhecido"),
    ("unknown action", "ação desconhecida"),
    # features
    ("invalid id", "id inválido"),
    ("feature not found", "feature não encontrada"),
    ("missing project", "falta o projeto"),
    ("title required (max. {0} characters)", "título obrigatório (máx. {0} caracteres)"),
    ("description required (max. {0} characters)", "descrição obrigatória (máx. {0} caracteres)"),
    ("unknown model", "modelo desconhecido"),
    ("unknown language", "idioma desconhecido"),
    ("unknown request type", "tipo desconhecido"),
    ("comment too long (max. {0} characters)", "comentário grande demais (máx. {0} caracteres)"),
    ("comment required (max. {0} characters)", "comentário obrigatório (máx. {0} caracteres)"),
    ("fixes can only be requested after the review", "as correções pedem-se depois da revisão"),
    ("already finished", "já terminada"),
    ("current state «{0}»; this action needs «{1}»", "estado atual «{0}»; esta ação precisa de «{1}»"),
    ("could not launch the worker: {0}", "não consegui lançar o worker: {0}"),
    ("could not launch the validator: {0}", "não consegui lançar o validador: {0}"),
    # pending items
    ("text too long (max. {0} characters)", "texto grande demais (máx. {0} caracteres)"),
    ("unknown state", "estado desconhecido"),
    ("pending item not found", "pendente não encontrado"),
    ("project “{0}” does not exist (see: python scripts/chief.py projects)", "projeto «{0}» não existe (ver: python scripts/chief.py projects)"),
    # curation
    ("a curation is already running", "já há uma curadoria a correr"),
    ("the inbox is empty: no drafts to process", "a inbox está vazia: não há rascunhos para tratar"),
    # projects
    ("give the full path of the folder", "indica o caminho completo da pasta"),
    ("the path must be absolute (e.g. D:/Projects/my-repo)", "o caminho tem de ser completo (ex.: D:/Projetos/meu-repo)"),
    ("that folder does not exist (or is not a folder)", "essa pasta não existe (ou não é uma pasta)"),
    ("a drive root is not accepted", "não aceito a raiz de um disco"),
    ("sensitive folder (keys, secrets or personal documents): not adding it", "pasta sensível (chaves, segredos ou documentos pessoais): não a adiciono"),
    ("this is the Vault (or inside it): knowledge lives there, it is not a project", "é o Vault (ou fica dentro dele): o conhecimento fica lá, não é um projeto"),
    ("this is the Chief (or inside it): the Chief does not launch workers in it", "é o Chief (ou fica dentro dele): o Chief não lança workers nele"),
    ("this folder is in the ignore list (projects.json): remove it from there first if you want it", "esta pasta está na lista de ignoradas (projects.json): tira-a de lá primeiro, se a queres"),
    ("it already appears as a project", "já aparece como projeto"),
    ("it is already part of an existing project", "já faz parte de um projeto existente"),
    ("invalid URL: use https://github.com/owner/repo or git@github.com:owner/repo (no credentials or parameters)", "URL inválido: usa https://github.com/dono/repo ou git@github.com:dono/repo (sem credenciais nem parâmetros)"),
    ("I only clone from {0}", "só clono de {0}"),
    ("invalid repo name", "nome de repo inválido"),
    ("that name is in the ignore list (projects.json)", "esse nome está na lista de ignoradas (projects.json)"),
    ("the folder {0} already exists: if it is the same repo, use «Add project» with that path", "já existe a pasta {0}: se é o mesmo repo, usa «Adicionar projeto» com esse caminho"),
    ("the clone took too long and was cancelled", "o clone demorou demasiado e foi cancelado"),
    ("could not run git: {0}", "não consegui correr o git: {0}"),
    ("the clone failed (private repo without login, missing repo, or no network; for private repos log in once with “gh auth login”): {0}", "o clone falhou (repo privado sem login, inexistente ou sem rede; nos privados faz login uma vez com «gh auth login»): {0}"),
    ("invalid category: use letters, numbers, spaces or hyphens (max. 30)", "categoria inválida: usa letras, números, espaços ou hífenes (máx. 30)"),
    ("this does not look like a vault: the wiki folder or the 00-Index.md file is missing", "não parece um vault: falta a pasta wiki ou o ficheiro 00-Index.md"),
    ("that folder is already in the list", "essa pasta já está na lista"),
    ("that folder is not in the list", "essa pasta não está na lista"),
    ("at least one projects folder must remain (it is where repos are cloned)", "tem de ficar pelo menos uma pasta de projetos (é onde se clonam os repos)"),
    ("I could not tell where this project comes from", "não consegui perceber de onde vem este projeto"),
    ("this project comes from a projects folder: move the folder on disk", "este projeto vem de uma pasta de projetos: move a pasta no disco"),
    ("invalid host: {0}", "anfitrião inválido: {0}"),
    ("give between 1 and {0} hosts (e.g. github.com)", "indica entre 1 e {0} anfitriões (ex.: github.com)"),
    ("empty task", "tarefa vazia"),
    ("nothing to archive", "nada para arquivar"),
    ("the project folder does not exist", "a pasta do projeto não existe"),
    ("invalid session id", "id de sessão inválido"),
    ("could not open the terminal: {0}", "não consegui abrir o terminal: {0}"),
    ("claude --bg did not start: {0}", "claude --bg não arrancou: {0}"),
    # extraction into the vault
    ("extracting the project's information into the vault", "a extrair a informação do projeto para o vault"),
    ("to extract the information into the vault, open claude once in {0} and accept the folder trust: the extraction then starts by itself", "para extrair a informação para o vault, abre o claude uma vez em {0} e aceita a confiança na pasta: a extração arranca sozinha"),
    ("could not launch the extraction into the vault ({0})", "não consegui lançar a extração para o vault ({0})"),
]



def _compile(en: str):
    parts = re.split(r"\{(\d+)\}", en)
    rx = "".join(re.escape(p) if i % 2 == 0 else f"(?P<g{p}>.*)" for i, p in enumerate(parts))
    return re.compile(rx, re.S)


_EXACT = {en: pt for en, pt in PAIRS if "{" not in en}
_PATTERNS = [(_compile(en), pt) for en, pt in PAIRS if "{" in en]


def translate(msg, lang: str) -> str:
    """The message in the requested language ("en" returns it as is; so does anything without a translation)."""
    msg = str(msg)
    if lang != "pt":
        return msg
    if msg in _EXACT:
        return _EXACT[msg]
    for rx, pt in _PATTERNS:
        m = rx.fullmatch(msg)
        if m:
            return pt.format(*[m.group(f"g{i}") for i in range(len(m.groupdict()))])
    return msg


def request_lang(header) -> str:
    """Language requested by the X-Lang header: only "pt" is different; everything else is English."""
    return "pt" if str(header or "").strip().lower().startswith("pt") else "en"
