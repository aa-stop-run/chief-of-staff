# Chief of Staff

A personal "chief of staff" for your coding projects. It runs next to [Claude Code](https://claude.com/claude-code) and gives you:

- **A base terminal** that knows every project in your folders: ask it about any of them, or tell it to launch a background worker (`claude --bg`) in one.
- **A local dashboard** (127.0.0.1 only) that shows just what needs you:
  - a **request pipeline** (feature, bug fix, maintenance, analysis) where each phase — spec, plan, implementation, review — is done by a worker and waits for your approval;
  - **session to-dos**: tasks left unfinished in your Claude Code sessions;
  - the running **workers**.
- **Independent checks built in**: every plan is validated, and every implementation is reviewed and re-tested by a fresh worker that never saw the implementer's context, *before* anything is committed. Nothing is pushed, ever; merging is yours.
- **An optional knowledge vault** (Obsidian-style markdown). Workers leave notes in its `inbox/` and an automatic curator promotes them, with a checkpoint commit before and after so everything can be reverted.

Everything runs on your machine with the Python standard library; there is nothing to install.

## Requirements

- Python 3.10+
- [Claude Code](https://claude.com/claude-code) (`claude`) on your PATH, already signed in
- git (and optionally the GitHub CLI `gh` for private repos)
- Node.js, only to run the tests that exercise the dashboard's JavaScript

## Quick start

```bash
git clone <this repo> chief && cd chief
python app/server.py          # http://127.0.0.1:8780
```

On first start the Chief creates your **data folder** (see below) with a `projects.json` made from `projects.example.json`. Open the dashboard and use **⚙ Settings** to point it at your projects folder and, if you use one, your vault.

## The application and your data are separate

This repository is the application: code only. Everything that is yours lives in the Chief's *home*, outside the repository:

| | |
|---|---|
| Windows | `%APPDATA%\Chief` |
| macOS | `~/Library/Application Support/Chief` |
| Linux | `$XDG_DATA_HOME/chief` or `~/.local/share/chief` |
| anywhere | set `CHIEF_HOME`, or start the dashboard with `--home <folder>` |

`python scripts/chief.py home` shows where everything is. The home holds `projects.json` (your settings), `state/` (requests, to-dos, workers and the implementations' git worktrees), `schema.json` and `backups/`.

Because of this you can update, switch branches in, or re-clone the application without touching your data, and you can try a change on a throw-away copy while you keep using the real one:

```bash
python app/server.py --home /tmp/chief-dev --port 8790     # empty data, nothing of yours is touched
```

Your data has a schema version. When a newer version of the application needs a different format, it backs your data up to `backups/` and migrates it on start-up (`scripts/migrations.py`); it refuses to start on data written by a newer version than it understands. If you used an older version that kept data inside the repository, it is imported into the home on first start and the old files are renamed `*.migrated`.

Open Claude Code in this folder to talk to the Chief; `CLAUDE.md` tells it how to behave. A few commands:

```bash
python scripts/chief.py projects                 # list projects and their vault note
python scripts/chief.py status [project]         # branch, changes, last commit, workers
python scripts/chief.py dispatch <project> --task "..." [--dry-run]
python scripts/chief.py workers
```

Before it launches a worker the Chief shows you the project, the task and the model and waits for a "yes". In the dashboard, clicking **Approve** is that "yes".

## Configuration (`projects.json`)

| Key | Meaning |
|---|---|
| `vault` | Path of your knowledge vault (leave empty if you do not use one). Changing it needs a restart. |
| `vault_language` | `en` (default) or `pt`. Decides the vault's conventions and the language of the notes workers write there (see below). |
| `roots` | Folders whose sub-folders are all projects. New clones go into the first one. |
| `extras` | Individual project folders outside the roots. |
| `ignore` | Sub-folder names of the roots to skip. |
| `hide_domains` | Categories to hide from the dashboard and from workers. |
| `domains` | Per-project category overrides. |
| `clone_hosts` | Hosts allowed for "clone a repo" (default: GitHub, GitLab, Bitbucket, Codeberg). |
| `sensitive_paths` | Name fragments of folders that must never be added as projects (e.g. `"tax documents"`). `.ssh`, `.aws`, `.gnupg` and `.kube` are always refused. |
| `dashboard_port` | Port of the dashboard (default 8780). |

Everything above lives in `projects.json` in your home and can be edited in the dashboard's **⚙ Settings**.

### The vault

The vault is optional. If you use one, the Chief expects an Obsidian-style folder with `wiki/` and an `inbox/`, and project "hub" notes with a `local:` frontmatter key pointing at the project's folder. Its conventions follow `vault_language`:

| | `en` (default) | `pt` |
|---|---|---|
| hub folders | `Projects/`, `Areas/` | `Projetos/`, `Areas/` |
| domain key | `domain:` | `dominio:` |
| inbox rules file | `README.md` | `LEIA-ME.md` |
| draft frontmatter | `project`, `type: decision`, `status: draft`, `date` | `projeto`, `tipo: decisao`, `estado: rascunho`, `data` |

Outside the automatic curation the Chief never edits existing vault notes; it only adds drafts to the inbox.

## Languages

English is the source language of the code, prompts, messages and dashboard. Portuguese is an optional translation: switch with the **PT / EN** buttons in the dashboard. A request remembers the language it was created in, and its workers write their artifacts in it. Adding another language means adding a `prompts_<lang>.py`, a dictionary in `scripts/i18n.py` and one in `app/index.html`.

## How the pipeline works

1. **Spec** → you approve (or ask for changes).
2. **Plan** → an independent validator checks it against the spec and the real repo *before* you see it; you approve.
3. **Implementation** in its own `git worktree` (branch `feat/<id>`), tests first, full suite run before and after. Not committed.
4. **Review** by a fresh worker that re-runs the full test suite itself. Only if it passes is the work committed, locally. You accept and merge.

Bug fixes start with a root-cause diagnosis and a failing test; maintenance keeps behavior identical; analyses are read-only and verified independently. The details are in `CLAUDE.md`.

## Safety model

- The dashboard listens on `127.0.0.1` only; writes need a per-start token, the dashboard's own `Origin` and a small JSON body.
- Workers run with `--permission-mode auto` and are told not to push, not to delete what they did not create, and not to touch secrets.
- The Chief never reads `.env*`, keys or tokens, never stores credentials and never logs in to GitHub for you; cloning uses the login git already has.
- It never pushes and never changes remotes.

## Tests

```bash
python -m pytest tests
```

The suite runs against a throw-away data folder; it never reads or writes your real data.

## License

[MIT](LICENSE).
