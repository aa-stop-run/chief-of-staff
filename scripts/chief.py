#!/usr/bin/env python3
"""Personal Chief of Staff: project registry, status, workers. Standard library only.

  python scripts/chief.py doctor                  # check the dependencies
  python scripts/chief.py costs [--project P] [--json]   # estimated AI cost
  python scripts/chief.py session [end]           # the open work session
  python scripts/chief.py plan                    # plan usage (5-hour, 7-day) and how to connect it
  python scripts/chief.py home                    # where the application and your data live
  python scripts/chief.py projects
  python scripts/chief.py status [project]
  python scripts/chief.py dispatch <project> --task "..." [--dry-run] [--model M]
  python scripts/chief.py workers
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import home
import migrations
import planusage
import report
import usage
import worksession

ROOT = home.APP_ROOT                  # the application's folder: code only
HOME = home.default_home()            # the user's data: configuration, state, backups (see home.py)
CFG = home.read_config(HOME)          # reading never writes; home.ensure() creates and migrates the data at start-up
VAULT = Path(CFG["vault"])
STATE = home.state_dir(HOME)


def work_sessions() -> "worksession.WorkSessions":
    """The store of work sessions in the current data folder."""
    return worksession.WorkSessions(STATE / "work_sessions.json")


def vault_enabled() -> bool:
    """Is there a vault to use? It is optional: without one the Chief works the same, minus the notes and the curation."""
    return str(VAULT) not in ("", ".") and VAULT.is_dir()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ACTIVE = ("working", "running", "blocked", "idle", "waiting")  # session open
BUSY = ("working", "running")  # working right now

# The vault is an Obsidian-style knowledge base. Its conventions (folders, frontmatter keys, draft status) depend on
# `vault_language` in projects.json ("en" by default; "pt" for a Portuguese vault).
VAULT_LANG = "pt" if str(CFG.get("vault_language", "en")).lower().startswith("pt") else "en"
VAULT_SCHEMA = {
    "en": {"hub_dirs": ("Projects", "Areas"), "domain": "domain", "readme": "README.md", "review_dir": "_review",
           "fm": {"project": "project", "kind": "type", "decision": "decision", "status": "status", "draft": "draft", "date": "date"},
           "name": "English", "project_kind": "project"},
    "pt": {"hub_dirs": ("Projetos", "Areas"), "domain": "dominio", "readme": "LEIA-ME.md", "review_dir": "_revisao",
           "fm": {"project": "projeto", "kind": "tipo", "decision": "decisao", "status": "estado", "draft": "rascunho", "date": "data"},
           "name": "Portuguese (European)", "project_kind": "projeto"},
}
SCHEMA = VAULT_SCHEMA[VAULT_LANG]
DRAFT_TEXTS = {
    "en": {"added": "The user put the folder `{stored}` {what}.\nNo vault note yet: create the project hub (domain and stack to be confirmed).\n",
           "title": "project added to Chief",
           "in_chief": "in the Chief's projects (cloned from {url})",
           "via_dashboard": "into the Chief's projects (dashboard: «+ Add project»)",
           "archived": "{label} “{title}” (request `{fid}`); copy in the repo at `{where}`. Promote to the project hub: decision, scope and acceptance criteria.\n\n",
           "labels": {"spec": "Approved spec", "plan": "Approved plan", "analysis": "Approved analysis"},
           "fallback": "project"},
    "pt": {"added": "O utilizador pôs a pasta `{stored}` {what}.\nAinda sem nota no vault: criar o hub do projeto (domínio e stack a confirmar).\n",
           "title": "projeto adicionado ao Chief",
           "in_chief": "nos projetos do Chief (clonado de {url})",
           "via_dashboard": "aos projetos do Chief (dashboard: «+ Adicionar projeto»)",
           "archived": "{label} «{title}» (pedido `{fid}`); cópia no repo em `{where}`. Promover para o hub do projeto: decisão, âmbito e critérios de aceitação.\n\n",
           "labels": {"spec": "Spec aprovada", "plan": "Plano aprovado", "analysis": "Análise aprovada"},
           "fallback": "projeto"},
}
DRAFT_TEXT = DRAFT_TEXTS[VAULT_LANG]

PREAMBLE_NO_VAULT = """You were launched by the personal Chief of Staff to work on the project "{name}" ({cwd}).
Rules (mandatory):
1. Before starting, read the repo's README and CLAUDE.md, if they exist.
2. Work only inside {cwd}. Do not push, do not delete branches or files you did not create, do not touch secrets (.env, keys, tokens) and do not print them.
3. If you need a decision from the user, stop and write the question in your final result instead of guessing.
4. Do not create notes or files outside what the task asks for: your final message is the record.
5. Your last message starts with [DONE], [BLOCKED] or [QUESTION] and summarises in 5 lines.
6. If you find follow-up work that deserves its own request (for example a commit or a pull request after an analysis), you may SUGGEST it: python "{chief}/scripts/proposals.py" add --project "{name}" --kind <feature|fix|maintenance|analysis|ship> --title "..." --desc "..." --by "{worker}". It only creates a card on the dashboard that the user accepts or dismisses; you cannot start work with it. Describe what and why; never put secrets in it.

## Task
{task}
"""

PREAMBLE = """You were launched by the personal Chief of Staff to work on the project "{name}" ({cwd}).
Rules (mandatory):
1. Before starting, read the project's note in the vault ({hub}) and the repo's CLAUDE.md, if they exist.
2. Work only inside {cwd}. Do not push, do not delete branches or files you did not create, do not touch secrets (.env, keys, tokens) and do not print them.
3. If you need a decision from the user, stop and write the question in your final result instead of guessing.
4. At the end, create ONE note in {vault}/inbox/ named 'YYYY-MM-DD {name} - <subject>.md' (format in {vault}/inbox/{readme}) with what you did, what you decided and what is missing. Write the note in {note_lang}.
5. Your last message starts with [DONE], [BLOCKED] or [QUESTION] and summarises in 5 lines.
6. If you find follow-up work that deserves its own request (for example a commit or a pull request after an analysis), you may SUGGEST it: python "{chief}/scripts/proposals.py" add --project "{name}" --kind <feature|fix|maintenance|analysis|ship> --title "..." --desc "..." --by "{worker}". It only creates a card on the dashboard that the user accepts or dismisses; you cannot start work with it. Describe what and why; never put secrets in it.

## Task
{task}
"""


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def norm(p):
    return str(Path(p)).replace("\\", "/").rstrip("/").lower()


def hubs():
    out = []
    if not vault_enabled():
        return out
    for folder in SCHEMA["hub_dirs"]:
        for f in (VAULT / folder).glob("*.md"):
            t = f.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"^local:\s*(.+?)\s*$", t, re.M)
            d = re.search(rf"^{SCHEMA['domain']}:\s*(\w+)", t, re.M)
            if m:
                out.append({"name": f.stem, "local": norm(m.group(1).split(" (")[0].strip('" ')),
                            "domain": d[1] if d else "", "file": str(f)})
    return out


def load_projects():
    paths = []
    for r in CFG["roots"]:
        for d in sorted(Path(r).iterdir()):
            if d.is_dir() and d.name not in CFG["ignore"]:
                paths.append(d)
    paths += [Path(x) for x in CFG["extras"] if Path(x).is_dir()]
    hs = sorted(hubs(), key=lambda h: -len(h["local"]))
    projs = []
    for p in paths:
        n = norm(p)
        hub = next((h for h in hs if n == h["local"] or h["local"].startswith(n + "/") or n.startswith(h["local"] + "/")), None)
        name = hub["name"] if hub else p.name
        domain = (CFG.get("domains") or {}).get(name) or (hub["domain"] if hub else "")
        projs.append({"name": name, "slug": slug(name), "path": str(p), "domain": domain,
                      "hub": hub["file"] if hub else None})
    return visible_projects(projs, CFG.get("hide_domains", []))


def visible_projects(projs, hide_domains):
    """Drop projects in hidden domains (projects.json -> hide_domains): they are not shown and get no workers."""
    hidden = {d.lower() for d in hide_domains}
    return [p for p in projs if (p.get("domain") or "").lower() not in hidden]


SENSITIVE_DIRS = (".ssh", ".aws", ".gnupg", ".kube")
SENSITIVE_PARTS = tuple(str(x).lower() for x in CFG.get("sensitive_paths", []))   # extra folder-name fragments never accepted as projects


def _covered_paths():
    """Folders that already are projects: direct children of the roots (minus ignored ones) and the extras."""
    out = []
    for r in CFG["roots"]:
        out += [norm(d) for d in Path(r).iterdir() if d.is_dir() and d.name not in CFG["ignore"]]
    return out + [norm(x) for x in CFG["extras"]]


def _inside(child, parent) -> bool:
    c, p = norm(child), norm(parent)
    return c == p or c.startswith(p + "/")


def check_new_project_path(raw) -> Path:
    """Validate the path of a new project; return the resolved folder or raise ValueError (a user-facing message)."""
    text = raw.strip().strip('"').strip("'").strip() if isinstance(raw, str) else ""
    if not text or len(text) > 400 or "\x00" in text:
        raise ValueError("give the full path of the folder")
    if not Path(text).is_absolute():
        raise ValueError("the path must be absolute (e.g. D:/Projects/my-repo)")
    path = Path(text)
    if not path.is_dir():
        raise ValueError("that folder does not exist (or is not a folder)")
    path = path.resolve()
    if len(path.parts) <= 1:
        raise ValueError("a drive root is not accepted")
    low = norm(path)
    if path.name.lower() in SENSITIVE_DIRS or any(x in low for x in SENSITIVE_PARTS):
        raise ValueError("sensitive folder (keys, secrets or personal documents): not adding it")
    if _inside(path, VAULT):
        raise ValueError("this is the Vault (or inside it): knowledge lives there, it is not a project")
    if _inside(path, ROOT) or _inside(path, HOME):
        raise ValueError("this is the Chief (or inside it): the Chief does not launch workers in it")
    for r in CFG["roots"]:
        if norm(path.parent) == norm(r) and path.name in CFG["ignore"]:
            raise ValueError("this folder is in the ignore list (projects.json): remove it from there first if you want it")
    for c in _covered_paths():
        if low == c:
            raise ValueError("it already appears as a project")
        if low.startswith(c + "/"):
            raise ValueError("it is already part of an existing project")
    return path


def _write_config(cfg: dict) -> None:
    """projects.json (in the home) with one key per line, LF line endings, written atomically."""
    body = ",\n".join(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in cfg.items())
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = HOME / "projects.json.tmp"
    tmp.write_text("{\n" + body + "\n}\n", encoding="utf-8", newline="\n")
    tmp.replace(home.config_path(HOME))


def draft_header(project: str, day: str) -> str:
    """Frontmatter of a draft in the vault inbox, using the vault's conventions (see VAULT_SCHEMA)."""
    fm = SCHEMA["fm"]
    return (f'---\n{fm["project"]}: "[[{project}]]"\n{fm["kind"]}: {fm["decision"]}\n{fm["status"]}: {fm["draft"]}\n'
            f'{fm["date"]}: {day}\n---\n')


def _draft_note(path: Path, stored: str, what: str) -> None:
    """Inbox draft asking for the project hub (an extra: failing must not break the registration)."""
    if not vault_enabled():
        return
    safe = re.sub(r'[<>:"/\\|?*]', "", path.name).strip() or DRAFT_TEXT["fallback"]
    day = f"{datetime.now():%Y-%m-%d}"
    note = VAULT / "inbox" / f"{day} {safe} - {DRAFT_TEXT['title']}.md"
    text = draft_header(safe, day) + DRAFT_TEXT["added"].format(stored=stored, what=what)
    try:
        if not note.exists():
            note.write_text(text, encoding="utf-8", newline="\n")
    except OSError:
        pass


CLONE_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
CLONE_TIMEOUT = 300
_CLONE_URL = re.compile(r"https://([a-z0-9.-]+)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", re.I)


_SSH_URL = re.compile(r"git@([a-z0-9.-]+):([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?", re.I)
DOMAIN_RE = re.compile(r"^[a-zà-ú0-9][a-zà-ú0-9 _-]{0,29}$")


DEFAULT_MODELS = ("opus", "sonnet", "haiku")


EFFORTS = ("low", "medium", "high", "xhigh", "max")      # what `claude --effort` accepts
DEFAULT_PHASE_MODELS = {"qa": "sonnet"}                  # read-only questions are cheap work; a request's own model or `phase_models` outrank this
_MODEL_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,79}$")


def route(phase: str, request_model: str = "") -> tuple:
    """(model, effort) for a worker doing `phase` of a request; None means "whatever Claude Code uses".

    Model: `phase_models[phase]` in projects.json wins, then the model chosen for the request, then a built-in default (only
    read-only questions: Sonnet). A phase set to "" in `phase_models` follows the request. Effort: `phase_efforts[phase]`, else
    Claude Code's own default. Invalid values are ignored, never passed to the command line."""
    pm, pe = CFG.get("phase_models"), CFG.get("phase_efforts")
    pm, pe = (pm if isinstance(pm, dict) else {}), (pe if isinstance(pe, dict) else {})
    ok = lambda m: bool(m) and isinstance(m, str) and bool(_MODEL_OK.match(m))   # noqa: E731
    model = next((m for m in (pm.get(phase), request_model, None if phase in pm else DEFAULT_PHASE_MODELS.get(phase)) if ok(m)), None)
    return model, (pe.get(phase) if pe.get(phase) in EFFORTS else None)


# Permission rules handed to `claude --allowedTools / --disallowedTools` for the phases that must run git commands a background worker
# cannot ask about. Keyed "<kind>:<stage>"; `phase_tools` in projects.json replaces an entry (an empty list switches it off). They are
# narrow and only exist in those phases: the commit phase of «ship», and its publication phase, which starts only after the user's click.
# Claude Code's own `settings.json` is never touched.
DEFAULT_PHASE_TOOLS = {
    "ship:build": {"allow": ["Bash(git add:*)", "Bash(git commit:*)", "Bash(git switch -c:*)", "Bash(git checkout -b:*)"],
                   "deny": ["Bash(git push:*)", "Bash(git remote add:*)", "Bash(git remote set-url:*)", "Bash(git config:*)",
                            "Bash(git reset --hard:*)", "Bash(git commit --amend:*)", "Bash(git commit*--amend*)"]},
    "ship:review": {"allow": ["Bash(git push -u:*)", "Bash(gh pr create:*)"],
                    "deny": ["Bash(git push*--force*)", "Bash(git push*-f *)", "Bash(git push*--delete*)", "Bash(git push*--mirror*)",
                             "Bash(git push*+*)", "Bash(git remote add:*)", "Bash(git remote set-url:*)", "Bash(git config:*)",
                             "Bash(gh pr merge:*)", "Bash(gh auth:*)"]},
}
_TOOL_OK = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\([^\n,]{1,200}\))?$")
MAX_TOOL_RULES = 24


def _tool_list(v) -> list:
    return [x for x in v if isinstance(x, str) and _TOOL_OK.match(x)][:MAX_TOOL_RULES] if isinstance(v, list) else []


def tools_for(kind: str, stage: str) -> tuple:
    """(allow, deny) permission rules for a worker doing `stage` of a request of `kind`: the entry in `phase_tools` of projects.json
    (`{"ship:review": {"allow": [...], "deny": [...]}}`) or the built-in default. Invalid rules are dropped, never passed on."""
    key = f"{kind}:{stage}"
    cfg = CFG.get("phase_tools")
    entry = cfg[key] if isinstance(cfg, dict) and isinstance(cfg.get(key), dict) else DEFAULT_PHASE_TOOLS.get(key, {})
    return _tool_list(entry.get("allow")), _tool_list(entry.get("deny"))


def route_step(step: dict, request_model: str = "") -> tuple:
    """(model, effort) for a worker doing a pipeline step. `phase_models` / `phase_efforts` in projects.json win, then the step's own
    `model` / `effort`, then the request's model, then the built-in default (the same order `route` gives for a phase)."""
    phase = step.get("phase", step.get("id", ""))
    model, effort = route(phase, request_model)
    pm, pe = CFG.get("phase_models"), CFG.get("phase_efforts")
    if not (isinstance(pm, dict) and phase in pm) and step.get("model") and _MODEL_OK.match(str(step["model"])):
        model = step["model"]
    if not (isinstance(pe, dict) and pe.get(phase) in EFFORTS) and step.get("effort") in EFFORTS:
        effort = step["effort"]
    return model, effort


def tools_for_step(step: dict, kind: str = "") -> tuple:
    """(allow, deny) permission rules for a pipeline step: the user's `phase_tools` entry or the built-in default for `<kind>:<phase>`,
    else the step's own `permissions`; the pipeline engine's fixed denials (force-push, merging, `gh auth`) are always added."""
    import engine
    phase = step.get("phase", step.get("id", ""))
    key = f"{kind}:{phase}"
    cfg = CFG.get("phase_tools")
    if (isinstance(cfg, dict) and isinstance(cfg.get(key), dict)) or key in DEFAULT_PHASE_TOOLS:
        allow, deny = tools_for(kind, phase)
    else:
        allow, deny = engine.effective_permissions(step)
        allow, deny = _tool_list(allow), _tool_list(deny)
    deny = list(deny) + [d for d in engine.FIXED_DENY if d not in deny]
    return allow, deny


def models() -> list:
    """Models offered in the dashboard (`models` in projects.json). The empty choice always means "whatever Claude Code uses"."""
    return list(CFG.get("models") or DEFAULT_MODELS)


def clone_hosts() -> list:
    return list(CFG.get("clone_hosts") or CLONE_HOSTS)


def clean_domain(raw) -> str:
    """A project's category (domain): lowercase letters, digits, spaces and hyphens; empty = the vault's own."""
    text = raw.strip().lower() if isinstance(raw, str) else ""
    if text and not DOMAIN_RE.match(text):
        raise ValueError("invalid category: use letters, numbers, spaces or hyphens (max. 30)")
    return text


def _store_domain(name: str, domain: str) -> None:
    global CFG
    fresh = home.read_config(HOME)
    domains = {**(fresh.get("domains") or {}), name: domain}
    _write_config({**fresh, "domains": domains})
    CFG = {**CFG, "domains": domains}


def parse_clone_url(raw) -> tuple[str, str]:
    """Validate a clone URL: https or git@host:owner/repo, allowed host, no credentials. Return (clean url, folder name)."""
    text = raw.strip() if isinstance(raw, str) else ""
    m, ssh = _CLONE_URL.fullmatch(text), False
    if not m:
        m, ssh = _SSH_URL.fullmatch(text), True
    if not m:
        raise ValueError("invalid URL: use https://github.com/owner/repo or git@github.com:owner/repo (no credentials or parameters)")
    host, owner, repo = m.group(1).lower(), m.group(2), m.group(3)
    if host not in clone_hosts():
        raise ValueError("I only clone from " + ", ".join(clone_hosts()))
    if owner.startswith(".") or repo.startswith(".") or repo.lower().endswith(".git"):
        raise ValueError("invalid repo name")
    return (f"git@{host}:{owner}/{repo}" if ssh else f"https://{host}/{owner}/{repo}"), repo


def clone_project(raw, domain=None) -> dict:
    """Clone a repo (public, or private if git already has a login) into the first projects root and leave a draft in the inbox.

    Never asks for or stores credentials and never pushes; the category is optional."""
    url, name = parse_clone_url(raw)
    domain = clean_domain(domain)
    dest = Path(CFG["roots"][0]) / name
    if name in CFG["ignore"]:
        raise ValueError("that name is in the ignore list (projects.json)")
    if dest.exists():
        raise ValueError(f"the folder {dest.as_posix()} already exists: if it is the same repo, use «Add project» with that path")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
    cmd = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "protocol.ssh.allow=always",
           "-c", "core.symlinks=false",
           "clone", "--no-recurse-submodules", "--", url, str(dest)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=CLONE_TIMEOUT, env=env, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError("the clone took too long and was cancelled") from None
    except OSError as e:
        raise ValueError(f"could not run git: {e}") from e
    if r.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError("the clone failed (private repo without login, missing repo, or no network; for private repos log in once with “gh auth login”): "
                         + (r.stderr.strip().splitlines() or ["no detail"])[-1][:200])
    _draft_note(dest, dest.as_posix(), DRAFT_TEXT["in_chief"].format(url=url))
    p = next(p for p in load_projects() if norm(p["path"]) == norm(dest))
    if domain:
        _store_domain(p["name"], domain)
        p = next(p for p in load_projects() if norm(p["path"]) == norm(dest))
    return p


def add_project(raw, domain=None) -> dict:
    """Add an existing folder to the projects (extras in projects.json) and leave a draft in the vault inbox."""
    global CFG
    path = check_new_project_path(raw)
    domain = clean_domain(domain)
    stored = str(path).replace("\\", "/")
    fresh = home.read_config(HOME)
    _write_config({**fresh, "extras": [*fresh.get("extras", []), stored]})
    CFG = {**CFG, "extras": [*CFG["extras"], stored]}
    _draft_note(path, stored, DRAFT_TEXT["via_dashboard"])
    p = next(p for p in load_projects() if norm(p["path"]) == norm(path))
    if domain:
        _store_domain(p["name"], domain)
        p = next(p for p in load_projects() if norm(p["path"]) == norm(path))
    return p


def find(name, projs):
    s = slug(name)
    exact = [p for p in projs if p["slug"] == s or slug(Path(p["path"]).name) == s]
    cand = exact or [p for p in projs if s in p["slug"] or s in slug(Path(p["path"]).name)]
    if len(cand) != 1:
        sys.exit(f"project '{name}' " + ("is ambiguous: " + ", ".join(p["name"] for p in cand) if cand else "not found"))
    return cand[0]


def git(path, *args):
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=15,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def git_info(path):
    if git(path, "rev-parse", "--is-inside-work-tree") != "true":
        return {"git": False}
    st = git(path, "--no-optional-locks", "status", "--porcelain") or ""   # background polling must not take .git/index.lock
    last = git(path, "log", "-1", "--format=%cs|%s") or "|"
    ahead = git(path, "rev-list", "--count", "@{u}..HEAD")
    return {"git": True, "branch": git(path, "branch", "--show-current") or "?",
            "dirty": len([ln for ln in st.splitlines() if ln.strip()]),
            "last_date": last.split("|")[0], "last_msg": last.split("|", 1)[1][:70],
            "ahead": int(ahead) if ahead and ahead.isdigit() else None}


def inbox_notes():
    if not vault_enabled():
        return []
    d = VAULT / "inbox"
    return [f for f in d.glob("*.md") if f.name != SCHEMA["readme"]] if d.is_dir() else []


def sessions_checked():
    """Background sessions, or None if claude could not be queried."""
    try:
        r = subprocess.run(["claude", "agents", "--json", "--all"], capture_output=True, text=True, timeout=30,
                           encoding="utf-8", creationflags=NO_WINDOW)
        return [s for s in json.loads(r.stdout) if s.get("kind") == "background"]
    except Exception:
        return None


def sessions():
    return sessions_checked() or []


def session_state(s: dict) -> str:
    """The state of a background session as the Chief understands it. Claude Code can report `state: working` for a session whose main
    thread has stopped (`status: idle`) because a background sub-agent it started is still running: the worker's result is already
    written, and waiting for the sub-agent would hold the request for as long as it lives. That is `idle` here, which is what a
    session left open after its work is. A session without a `state` falls back to its `status`."""
    st = s.get("state")
    if st in BUSY and s.get("status") == "idle":
        return "idle"
    return st if st is not None else {"busy": "working", "idle": "idle"}.get(s.get("status"), "finished")


def load_workers():
    try:
        return json.loads((STATE / "workers.json").read_text(encoding="utf-8"))
    except Exception:
        return []


def full_state(live_list=None, with_git=True):
    projs = load_projects()
    notes = inbox_notes()
    live = {s["id"]: s for s in (sessions() if live_list is None else live_list)}
    workers = load_workers()
    for w in workers:
        s = live.get(w["id"])
        w["state"] = session_state(s) if s else "finished"
    if with_git:
        with ThreadPoolExecutor(max_workers=8) as pool:
            infos = list(pool.map(lambda q: git_info(q["path"]), projs))
        for p, info in zip(projs, infos):
            p.update(info)
        p["inbox"] = sum(1 for n in notes if slug(p["name"]) in slug(n.stem))
        p["workers"] = sum(1 for w in workers if w["project"] == p["name"] and w["state"] in ACTIVE)
    return {"projects": projs, "workers": workers[::-1][:500], "inbox": sorted(n.name for n in notes)[::-1],
            "updated": datetime.now().strftime("%H:%M:%S")}


MIN_PYTHON = (3, 10)
PROVIDER_ENV = ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")


def _first_line(cmd: list) -> str | None:
    """First line printed by a command, or None if it is missing or fails."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (r.stdout or r.stderr or "").strip()
    return text.splitlines()[0] if r.returncode == 0 and text else None


def doctor_checks() -> list:
    """Everything the Chief needs from the machine. Each check: {level: ok|warn|fail, name, detail, hint}.

    `fail` = required and missing; `warn` = optional and missing. Only reads; never installs or changes anything."""
    out = []

    def add(level, name, detail, hint=""):
        out.append({"level": level, "name": name, "detail": detail, "hint": hint})

    v = sys.version_info
    add("ok" if (v.major, v.minor) >= MIN_PYTHON else "fail", "Python", f"{v.major}.{v.minor}.{v.micro}",
        "" if (v.major, v.minor) >= MIN_PYTHON else f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required: https://www.python.org/downloads/")
    git_v = _first_line(["git", "--version"]) if shutil.which("git") else None
    add("ok" if git_v else "fail", "git", git_v or "not found", "" if git_v else "Required (worktrees and commits): https://git-scm.com/downloads")
    claude_v = _first_line(["claude", "--version"]) if shutil.which("claude") else None
    if not claude_v:
        add("fail", "Claude Code", "not found", "Required: every worker is a `claude --bg` session. Install it: https://code.claude.com/docs")
    else:
        help_text = ""
        try:
            help_text = subprocess.run(["claude", "--help"], capture_output=True, text=True, timeout=20, encoding="utf-8",
                                       errors="replace", creationflags=NO_WINDOW).stdout
        except (OSError, subprocess.SubprocessError):
            pass
        bg = "--bg" in help_text
        add("ok" if bg else "fail", "Claude Code", claude_v + ("" if bg else " (no background sessions: `--bg` is missing)"),
            "" if bg else "Update Claude Code to a version that supports `claude --bg`.")
    used = [k for k in PROVIDER_ENV if os.environ.get(k)]
    add("ok", "Model / provider", ("set in the environment: " + ", ".join(used)) if used else
        "Claude Code's own configuration (nothing overridden in the environment)",
        "Workers use whatever model and provider your Claude Code is configured for; `models` in projects.json only fills the dashboard's menu.")
    gh_v = _first_line(["gh", "--version"]) if shutil.which("gh") else None
    add("ok" if gh_v else "warn", "GitHub CLI (gh)", gh_v or "not found",
        "" if gh_v else "Optional (cloning private repos, showing the GitHub account): https://cli.github.com")
    node_v = _first_line(["node", "--version"]) if shutil.which("node") else None
    add("ok" if node_v else "warn", "Node.js", node_v or "not found", "" if node_v else "Optional, only for running the tests: https://nodejs.org")
    try:
        import pytest  # noqa: F401
        add("ok", "pytest", "installed")
    except ImportError:
        add("warn", "pytest", "not installed", "Optional, only for running the tests: pip install pytest")
    add("ok", "Data folder", f"{HOME} (schema {home.schema_version(HOME)})")
    add("ok" if CFG.get("roots") else "warn", "Projects folders", ", ".join(CFG.get("roots") or []) or "none configured yet",
        "" if CFG.get("roots") else "Add one in the dashboard's Settings (or `roots` in projects.json).")
    if not CFG.get("vault"):
        add("ok", "Vault", "none (optional): the Chief works without one")
    elif VAULT.is_dir():
        add("ok", "Vault", CFG["vault"])
    else:
        add("warn", "Vault", f"{CFG['vault']} (folder not found)", "Fix the path in Settings, or leave it empty to work without a vault.")
    return out


def cmd_doctor(_):
    marks = {"ok": "[ ok ]", "warn": "[ -- ]", "fail": "[FAIL]"}
    checks = doctor_checks()
    print("Chief of Staff: environment check\n")
    for c in checks:
        print(f"{marks[c['level']]} {c['name']}: {c['detail']}")
        if c["hint"]:
            print(f"         {c['hint']}")
    failed = [c["name"] for c in checks if c["level"] == "fail"]
    print("\n" + ("Something required is missing: " + ", ".join(failed) if failed else "Ready. Start the dashboard with: python app/server.py"))
    if failed:
        sys.exit(1)


_NOTICES = planusage.NoticeCache()


def worker_limit(worker_id: str):
    """The usage-limit notice currently stopping a worker (read from the end of its Claude Code log), or None."""
    w = next((x for x in load_workers() if x.get("id") == worker_id), None)
    return _NOTICES.notice(usage.find_transcript(worker_id, w.get("cwd", ""))) if w else None


def plan_view() -> dict:
    return planusage.view(STATE / planusage.STATUS_FILE)


def plan_setup() -> dict:
    """What to add to ~/.claude/settings.json so Claude Code feeds the plan percentages to the Chief. The Chief never edits that file."""
    script = (ROOT / "scripts" / "chief.py").as_posix()
    python = Path(sys.executable).as_posix()
    return {"snippet": {"statusLine": {"type": "command", "command": f'"{python}" "{script}" statusline'}},
            "file": "~/.claude/settings.json",
            "note": "If you already have a statusLine command, keep it: append --then \"<your command>\" and it still runs first."}


def _read_features() -> list:
    try:
        return json.loads((STATE / "features.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _cost_report() -> dict:
    """Measure the workers' logs now and build the sessions-and-costs report."""
    ledger = usage.Ledger(STATE / "usage.json")
    ledger.refresh(load_workers(), CFG.get("prices"))
    data = ledger.load()
    return report.build(work_sessions(), data, _read_features(), load_workers(), CFG.get("prices"), report.plan_summary(data, plan_view()))


def cmd_costs(a):
    rep = _cost_report()
    if a.project:
        t = usage.aggregate(usage.Ledger(STATE / "usage.json").load(), project=a.project)
        rep = {"project": a.project, "cost_usd": t["cost_usd"], "tokens": t["tokens"], "by_model": t["by_model"], "workers": t["workers"]}
    print(json.dumps(rep, indent=1) if a.json else (report.render_text(rep) if "current" in rep else
          f"{a.project}: {report.money(rep['cost_usd'])} over {rep['workers']} workers\n" + "\n".join(
              f"    {m:34} {report.money(v['cost_usd']):>10}" for m, v in sorted(rep["by_model"].items(), key=lambda kv: -kv[1]["cost_usd"]))))


def cmd_statusline(a):
    """The command Claude Code runs as its status line: save the plan percentages it is given and print them. Never fails."""
    raw = ""
    try:
        raw = sys.stdin.read()
        planusage.record_statusline(json.loads(raw), STATE / planusage.STATUS_FILE)
    except Exception:  # noqa: BLE001 - a status line must never break the user's session
        pass
    shown = ""
    if a.then:
        try:
            shown = subprocess.run(a.then, shell=True, input=raw, capture_output=True, text=True, timeout=10, encoding="utf-8",
                                   errors="replace", creationflags=NO_WINDOW).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            shown = ""
    try:
        text = planusage.statusline_text(plan_view())
    except Exception:  # noqa: BLE001
        text = ""
    print(" ".join(x for x in (shown, text) if x))


def cmd_plan(_):
    v, data = plan_view(), usage.Ledger(STATE / "usage.json").load()
    now = datetime.now(timezone.utc)
    if not v["connected"]:
        print("Plan usage is not connected. Add this to ~/.claude/settings.json (the Chief never edits it):\n")
        print(json.dumps(plan_setup()["snippet"], indent=2))
        print("\nThen it shows up once an interactive Claude Code session has answered, and the dashboard shows the two meters.")
    for key, label in (("five_hour", "5-hour"), ("seven_day", "7-day")):
        w = v["windows"].get(key)
        if w:
            print(f"{label:7} {w['percent']:5.1f}% used" + ("  (window has reset)" if w["expired"] else f"  resets in {report.duration(w['resets_in_s'])}"))
    if v["connected"]:
        print(f"as of {report.duration(v['age_s'])} ago")
    print(f"Chief workers, estimated: last 5 hours {report.money(usage.spend_in_window(data, now - timedelta(hours=5), now))}"
          f" · last 7 days {report.money(usage.spend_in_window(data, now - timedelta(days=7), now))}")


def cmd_session(a):
    ws = work_sessions()
    cur = ws.current()
    if a.action == "end":
        if cur is None:
            sys.exit("no session is open (start the dashboard, or `python app/server.py`)")
        ledger = usage.Ledger(STATE / "usage.json")
        ledger.refresh(load_workers(), CFG.get("prices"))
        out = ws.end(report.totals_now(ledger.load(), cur["id"]))
        print(f"ended {out['ended']['id']}: {report.duration(out['ended']['active_s'])} active, {report.money(out['ended']['totals']['cost_usd'])} estimated")
        return
    if cur is None:
        print("no session is open: it starts with the dashboard (`python app/server.py`)")
        return
    t = usage.aggregate(_ledger_data(), session=cur["id"])
    print(f"{cur['id']}: started {cur['started']}, {report.duration(ws.active_seconds(cur))} active, {report.money(t['cost_usd'])} estimated")


def _ledger_data() -> dict:
    return usage.Ledger(STATE / "usage.json").load()


def cmd_home(_):
    print(f"application: {ROOT}\nhome:        {HOME}\nconfig:      {home.config_path(HOME)}\nstate:       {STATE}\n"
          f"schema:      {home.schema_version(HOME)} (this version reads {migrations.CURRENT})")


def cmd_projects(_):
    for p in load_projects():
        print(f"{p['name']:32} {p['domain'] or '-':12} {p['path']}" + ("" if p["hub"] or not vault_enabled() else "   (no note in the vault)"))


def cmd_status(a):
    st = full_state()
    only = find(a.project, st["projects"])["slug"] if a.project else None
    for p in st["projects"]:
        if only and p["slug"] != only:
            continue
        g = f"{p['branch']} ±{p['dirty']} {p['last_date']} {p['last_msg']}" if p["git"] else "sem git"
        print(f"{p['name']:30} {g}  | inbox {p['inbox']} | workers {p['workers']}")


def build_command(p, task, mode="auto", model=None, extra_dirs=(), label=None, effort=None, allow=(), deny=()):
    base = slug(label)[:40].strip("-") if label else ""
    prefix = base or f"cos-{p['slug']}"
    n = sum(1 for w in load_workers() if w["name"] == prefix or w["name"].startswith(prefix + "-")) + 1
    name = f"{prefix}-{n}" if not base or n > 1 else base
    prompt = (PREAMBLE if vault_enabled() else PREAMBLE_NO_VAULT).format(name=p["name"], cwd=p["path"], hub=p["hub"] or "(no note in the vault)",
                             vault=str(VAULT).replace("\\", "/"), readme=SCHEMA["readme"],
                             note_lang=SCHEMA["name"], task=task.strip(),
                             chief=str(ROOT).replace("\\", "/"), worker=name)
    cmd = ["claude", "--bg", "-n", name]
    if vault_enabled():                       # the vault is optional
        cmd += ["--add-dir", str(VAULT)]
    for d in extra_dirs:
        cmd += ["--add-dir", str(d)]
    cmd += ["--permission-mode", mode]
    if model:
        cmd += ["--model", model]
    if effort in EFFORTS:
        cmd += ["--effort", effort]
    allow, deny = _tool_list(list(allow)), _tool_list(list(deny))
    if allow:                                  # one `=` argument each, then «--»: these options are variadic and must not swallow the prompt
        cmd += ["--allowedTools=" + ",".join(allow)]
    if deny:
        cmd += ["--disallowedTools=" + ",".join(deny)]
    return name, cmd + (["--"] if allow or deny else []) + [prompt], prompt


_LAUNCH_LOCK = threading.Lock()  # unique names and lossless workers.json when several are launched at once


def launch_worker(project, task, model=None, extra_dirs=(), mode="auto", label=None, effort=None, allow=(), deny=()):
    """Launch a `claude --bg` worker in the project folder and record it in state/workers.json."""
    with _LAUNCH_LOCK:
        return _launch_worker_locked(project, task, model, extra_dirs, mode, label, effort, allow, deny)


def _launch_worker_locked(project, task, model, extra_dirs, mode, label=None, effort=None, allow=(), deny=()):
    p = find(project, load_projects())
    if not task.strip():
        raise ValueError("empty task")
    name, cmd, _ = build_command(p, task, mode, model, extra_dirs, label, effort, allow, deny)
    r = subprocess.run(cmd, cwd=p["path"], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       creationflags=NO_WINDOW)
    m = re.search(r"backgrounded\s*·\s*([0-9a-f]{6,})", r.stdout + r.stderr)
    if not m:
        raise RuntimeError("claude --bg did not start: " + (r.stdout + r.stderr)[-600:])
    w = {"name": name, "id": m[1], "project": p["name"], "cwd": p["path"], "task": task.strip()[:2000],
         "started": datetime.now().isoformat(timespec="seconds"), "model": model or "default",
         "session": work_sessions().current_id()}
    if effort in EFFORTS:
        w["effort"] = effort
    if _tool_list(list(allow)):
        w["allowed_tools"] = _tool_list(list(allow))       # what this worker was explicitly allowed to run, for the record
    if label and label.startswith("extraction-"):
        w["phase"] = "extraction"
    record_worker(w)
    return w


def _save_workers(workers: list) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / "workers.json.tmp"
    tmp.write_text(json.dumps(workers, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE / "workers.json")


def record_worker(w: dict) -> None:
    """Add a launched worker to state/workers.json."""
    _save_workers(load_workers() + [w])


def tag_worker(worker_id: str, **fields) -> bool:
    """Attach what a worker is for (the request and the phase) so its cost can be attributed. False if it is not recorded."""
    with _LAUNCH_LOCK:
        workers = load_workers()
        hit = next((w for w in workers if w.get("id") == worker_id), None)
        if hit is None:
            return False
        hit.update({k: v for k, v in fields.items() if v})
        _save_workers(workers)
        return True


def cmd_dispatch(a):
    if a.dry_run:
        p = find(a.project, load_projects())
        _, cmd, prompt = build_command(p, a.task, a.mode, a.model)
        print(f"cwd: {p['path']}\n" + " ".join(cmd[:-1]) + "\n--- prompt ---\n" + prompt)
        return
    try:
        w = launch_worker(a.project, a.task, a.model, mode=a.mode)
    except (ValueError, RuntimeError) as e:
        sys.exit(str(e))
    print(f"{w['name']} started · {w['id']} · view: claude attach {w['id']}")


SESSION_RE = re.compile(r"^[0-9a-f-]{8,40}$")


def open_session(project, session_id):
    """Open a new terminal window resuming a Claude Code session in the project folder."""
    if not SESSION_RE.match(session_id or ""):
        raise ValueError("invalid session id")
    p = next((q for q in load_projects() if q["name"] == project), None)
    if p is None:
        raise ValueError("unknown project")
    try:
        subprocess.Popen(["claude", "--resume", session_id], cwd=p["path"],
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    except OSError as e:
        raise RuntimeError(f"could not open the terminal: {e}") from e


CURATION_PROJECT = "Vault curation"      # the "project" the curator's workers (and their cost) are filed under


def launch_curator(prompt):
    """Launch the vault curator: a `claude --bg` with access to the vault.

    It starts from the Chief folder (already trusted): in the vault folder claude refuses background sessions until the
    user accepts the folder trust. The prompt tells it to read the vault's CLAUDE.md first."""
    if not vault_enabled():
        raise RuntimeError("no vault configured")
    cmd = ["claude", "--bg", "-n", "cos-curation", "--add-dir", str(VAULT), "--permission-mode", "auto", prompt]
    with _LAUNCH_LOCK:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=120,
                           creationflags=NO_WINDOW)
    m = re.search(r"backgrounded\s*·\s*([0-9a-f]{6,})", r.stdout + r.stderr)
    if not m:
        raise RuntimeError("claude --bg did not start: " + (r.stdout + r.stderr)[-400:])
    with _LAUNCH_LOCK:                                   # recorded so that its cost is counted too
        record_worker({"name": "cos-curation", "id": m[1], "project": CURATION_PROJECT, "cwd": str(ROOT), "task": "Vault curation",
                       "started": datetime.now().isoformat(timespec="seconds"), "model": "default", "phase": "curation",
                       "session": work_sessions().current_id()})
    return {"id": m[1], "name": "cos-curation"}


ARCHIVE_DIRS = {"spec": "specs", "plan": "plans", "analysis": "analyses"}   # docs/specs, docs/plans and docs/analyses in each project
ARCHIVE_LABELS = {"spec": ("Spec aprovada", "Spec"), "plan": ("Plano aprovado", "Plan"),
                  "analysis": ("Análise aprovada", "Analysis")}   # (repo label in Portuguese, repo label in English)


def archive_doc(kind, project, title, fid, created, text, lang="en"):
    """Archive an approved spec, plan or analysis: copy it to <project>/docs/{specs,plans,analyses}/ and leave a draft in the vault inbox.

    Never overwrites, commits or pushes. Returns a sentence describing what it did; raises ValueError if it cannot."""
    if kind not in ARCHIVE_DIRS or not (text or "").strip():
        raise ValueError("nothing to archive")
    p = next((x for x in load_projects() if x["name"] == project), None)
    if p is None:
        raise ValueError("unknown project")
    root = Path(p["path"])
    if not root.is_dir():
        raise ValueError("the project folder does not exist")
    day = (created or f"{datetime.now():%Y-%m-%d}")[:10]
    base = f"{day}-{slug(title)[:50].strip('-') or 'feature'}"
    pt_label, en_label = ARCHIVE_LABELS[kind]
    note = (f"> {en_label} approved in the Chief of Staff (request `{fid}`, project {p['name']}).\n\n" if lang == "en"
            else f"> {pt_label} no Chief of Staff (pedido `{fid}`, projeto {p['name']}).\n\n")
    dest = root / "docs" / ARCHIVE_DIRS[kind] / f"{base}.md"
    where = f"docs/{ARCHIVE_DIRS[kind]}/{base}.md"
    if dest.exists():
        result = f"it already existed at {where}: not overwritten"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(note + text.strip() + "\n", encoding="utf-8", newline="\n")
        result = f"copied to {where}"
    safe = re.sub(r'[<>:"/\\|?*]', "", p["name"]).strip() or DRAFT_TEXT["fallback"]
    draft = VAULT / "inbox" / f"{day} {safe} - {kind} {base[11:]}.md"
    if not vault_enabled():
        return result
    head = draft_header(safe, f"{datetime.now():%Y-%m-%d}") + DRAFT_TEXT["archived"].format(
        label=DRAFT_TEXT["labels"][kind], title=title, fid=fid, where=where)
    try:
        if not draft.exists():
            draft.write_text(head + text.strip() + "\n", encoding="utf-8", newline="\n")
            result += " and a draft in the vault"
    except OSError:
        result += " (no draft in the vault)"
    return result


def commit_worktree(path, message):
    """Commit everything in a feature's worktree (only after the review passes). Return (ok, sha or reason).

    Refuses folders without their own `.git`, so it never commits into a parent repository by mistake. Never pushes."""
    p = Path(path)
    if not p.is_dir() or not (p / ".git").exists():
        return False, "not a git repository (no .git of its own)"

    def run(*args):
        return subprocess.run(["git", "-C", str(p), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120, creationflags=NO_WINDOW)

    try:
        if run("add", "-A").returncode != 0:
            return False, "git add failed"
        if not run("status", "--porcelain").stdout.strip():
            return True, "no changes to commit"
        r = run("commit", "-m", message)
        if r.returncode != 0:
            return False, ((r.stderr or r.stdout).strip() or "git commit failed")[-300:]
        return True, run("rev-parse", "--short", "HEAD").stdout.strip() or "ok"
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def stop_worker(worker_id):
    """Stop a worker session (`claude stop`; the conversation is kept). Never raises: returns whether it worked."""
    if not SESSION_RE.match(worker_id or ""):
        return False
    try:
        r = subprocess.run(["claude", "stop", worker_id], capture_output=True, text=True, timeout=30,
                           creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(r, "returncode", 0) == 0


def cmd_workers(_):
    live = {s["id"]: s for s in sessions()}
    for w in load_workers():
        print(f"{w['name']:34} {w['id']} {live.get(w['id'], {}).get('state', 'terminado'):10} {w['task'][:60]!r}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("home", help="where the application and the user's data live").set_defaults(f=cmd_home)
    sp.add_parser("doctor", help="check the dependencies and the configuration").set_defaults(f=cmd_doctor)
    c = sp.add_parser("costs", help="estimated AI cost per work session, project and model")
    c.add_argument("--project")
    c.add_argument("--json", action="store_true")
    c.set_defaults(f=cmd_costs)
    st = sp.add_parser("statusline", help="the Claude Code status line command that feeds the plan percentages to the Chief")
    st.add_argument("--then", help="another status line command to run first (its output is shown before ours)")
    st.set_defaults(f=cmd_statusline)
    sp.add_parser("plan", help="plan usage (5-hour and 7-day) and how to connect it").set_defaults(f=cmd_plan)
    se = sp.add_parser("session", help="show the open work session, or end it")
    se.add_argument("action", nargs="?", choices=("show", "end"), default="show")
    se.set_defaults(f=cmd_session)
    sp.add_parser("projects").set_defaults(f=cmd_projects)
    s = sp.add_parser("status")
    s.add_argument("project", nargs="?")
    s.set_defaults(f=cmd_status)
    d = sp.add_parser("dispatch")
    d.add_argument("project")
    d.add_argument("--task", required=True)
    d.add_argument("--model")
    d.add_argument("--mode", default="auto")
    d.add_argument("--dry-run", action="store_true")
    d.set_defaults(f=cmd_dispatch)
    sp.add_parser("workers").set_defaults(f=cmd_workers)
    a = ap.parse_args()
    if a.cmd == "statusline":            # runs on every status line refresh: no start-up work, and it must never fail
        return cmd_statusline(a)
    try:
        home.ensure(HOME)
    except home.HomeError as e:
        sys.exit(str(e))
    a.f(a)


if __name__ == "__main__":
    main()
