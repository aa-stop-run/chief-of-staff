# Project-level settings (category, repo/remote) on the project page

**Date:** 2026-11-17
**Status:** Design (approved in dialogue)
**Supersedes part of:** global Settings' per-project rows (category + remote)

## Problem

Project-level settings (category/domain, the git remote) are edited in the global
**Settings** dialog, in a table of every project. They should live at the **project
level**: the user sets them when adding a project, and changes them later on the
project's own page.

## Goals

1. Category and git remote are captured when a project is added (existing-folder and
   clone paths).
2. Category and git remote are edited on the project's page, not in global Settings.
3. Global Settings no longer offers per-project category/remote editing (the page is
   the single surface). Add still happens in Settings; Remove/Move stay in Settings.

## Non-goals

- No change to how the vault note/domain is derived; the Chief's `domains` setting
  still outranks the vault hub's, and never writes the vault.
- No network side effects: remote changes touch only the project's local `git config`.
- No change to the clone host allow-list or clone security rules.

## Current state (working tree, already uncommitted)

- `app/index.html`: `pjAbout(header)` renders an editable **About** block on the
  project page — Category (input + Save, `data-pjact="dom"`) and Git remote
  (input + Save, `data-pjact="remote"`), plus a `saveAbout()` handler that calls
  `/api/settings/domain` and `/api/settings/remote_set`.
- `app/server.py`: routes `remote_set` → `settings.set_remote`.
- `scripts/projectpage.py`: `project_detail` adds `remote`/`host`/`git` to the page
  header (via `settings.git_remote`), so the About block can display the remote.
- `scripts/settings.py`: `get_remote`, `set_remote` (set-url / remove origin,
  credentials never stored or echoed), and `clean_remote`/`git_remote` helpers.

## Changes

### 1. Add-project flow captures remote (new)

The "+ Add project" dialog already has a **Category** field. Add an optional
**Git remote** field next to it.

- **Add existing folder** (`POST /api/projects`): after the folder + domain are
  registered, if a remote was given, apply it with `settings.set_remote`.
  - If the folder is **not** a git repo, skip the remote silently (do not fail the
    add); it can be set later on the project page.
  - If the remote URL is invalid, the existing `set_remote` validation error is
    surfaced as a toast and the add is still reported (the folder is registered;
    only the remote step is noted).
- **Clone** (`POST /api/projects/clone`): the URL **is** the remote, so no new field
  is used; domain is already passed. (Documented so it is not mistaken for missing.)

New dialog field: `<input id="apRemote">` (optional, `maxlength=400`), label
"Git remote (optional)", placeholder `https://github.com/owner/repo`.

### 2. Project page About block (already in working tree)

- Editable **Category** and **Git remote**, each with a Save button, in an **About**
  section at the top of the project page.
- Remote shows the project's origin with credentials stripped; empty value removes
  the origin. Only the project's local git config is touched.
- Save calls the existing `remote_set` / `domain` routes, then refreshes the page.

### 3. Global Settings: remove per-project category/remote (new)

- Remove the **Category** editable input (`.sdom`) and the **Git remote** column from
  the Settings Projects table (render + the `change` listener that calls `domain`).
- Keep in Settings: project name, path, **Move** (for extras), and **Remove**.
- The Projects section note is adjusted to point to the project page for
  category/remote.

## Data flow

- Add: `addP` form → `POST /api/projects {path, domain, remote}` → register
  folder+domain → (if git repo & remote given) `set_remote` → toast → reload.
- Page edit: `saveAbout` → `POST /api/settings/{domain|remote_set}` → reload page.
- Settings: list-only (name, path, Move, Remove); category/remote are gone.

## Error handling

- Non-git folder + remote given on add: skip remote, no error (toast notes it).
- Invalid remote URL: `set_remote` raises; surfaced as a toast; folder still added.
- `set_remote` on a project with no `.git`: raises "not a git repository" (only
  reachable from the page if the user types a remote on a non-git project; the
  About block already shows "Not a git repo" and the save is then rejected).
- Credentials: never stored or echoed (`clean_remote` strips them).

## Testing

- `scripts/settings.py`: `test_settings.py` — `set_remote` set/remove, non-git
  error, credential stripping, URL validation.
- Add-with-remote: server-level test that `POST /api/projects` with a `remote`
  applies the origin on a git repo and skips it on a plain folder.
- Page: `test_page_*` — the About block renders category/remote and the save
  buttons wire to `domain` / `remote_set`.
- Settings: assert the Projects table no longer renders the category input or the
  remote column; Move/Remove remain.

## Rollout

- Code change reaches the running instance on restart (except `app/index.html`,
  which is read per request). The uncommitted working-tree changes plus these
  changes form one feature; commit when tests pass.
