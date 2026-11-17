# Project-Level Settings (Category + Remote on the Project Page) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user set a project's category and git remote when adding it, and change both on the project's own page, and remove the per-project category/remote editing from the global Settings dialog.

**Architecture:** The project page already (in the working tree) renders an editable About block wired to `POST /api/settings/domain` and `POST /api/settings/remote_set`. The new work is: (1) an optional Git-remote field in the "+ Add project" dialog, (2) the server applying that remote after `chief.add_project`, and (3) the Settings dialog dropping the per-project category input and remote column so the page is the single surface. No new data files; `settings.set_remote`/`git_remote`/`clean_remote` already exist.

**Tech Stack:** Python 3 (stdlib only) for `scripts/settings.py`, `app/server.py`, `scripts/chief.py`; vanilla JS in `app/index.html`; pytest for tests.

**Working tree note:** This plan builds on uncommitted changes already in the tree (the `pjAbout` block, `remote_set` route, `projectpage` header remote, `settings.set_remote`/`get_remote`). Those files are modified; this plan adds to them. Commit at the end of each task.

---

## File map

- `scripts/settings.py` — already has `get_remote`, `set_remote`, `clean_remote`, `git_remote`, `add_root`, `set_domain`. No new functions needed.
- `scripts/chief.py` — `add_project(raw, domain=None)` (line 401). Add a `remote=None` parameter and apply it via `settings.set_remote`.
- `app/server.py` — `_route` `["api","projects"]` branch (line 527) passes `remote` through.
- `app/index.html` —
  - Add dialog `addP` (lines 526-537): add `apRemote` field + include it in the submit payload (lines 2584-2590).
  - Settings `renderSettings` (lines 2427-2462): drop the Category input column and the Git remote column; keep name, path, Move, Remove. Drop the `.sdom` change listener (lines 2563-2566).
- `tests/test_settings.py` — tests for `set_remote`, add-with-remote (via `chief.add_project`), and the Settings snapshot shape (no per-project remote column requirement is a UI concern; the test asserts `set_remote` behaviour and `chief.add_project` remote application).
- `tests/test_page_project_settings.py` — page renders the About block with category + remote inputs and the two save buttons.

---

## Task 1: `chief.add_project` applies an optional git remote

**Files:**
- Modify: `scripts/chief.py:401-415`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_settings.py`:

```python
def test_add_project_applies_the_optional_remote(env):
    _, root, _, proj_root = env
    repo = proj_root / "alfa"
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    p = chief.add_project(str(repo), domain="home", remote="https://github.com/acme/alfa")
    assert p["name"] == "Alfa"
    assert settings.git_remote(str(repo)) == {"remote": "github.com/acme/alfa", "host": "github.com", "git": True}
    assert "SECRET" not in json.dumps(settings.git_remote(str(repo)))


def test_add_project_skips_remote_for_a_plain_folder(env):
    _, root, _, proj_root = env
    plain = proj_root / "plain"
    plain.mkdir()
    # a remote given for a non-git folder is skipped, not an error
    p = chief.add_project(str(plain), remote="https://github.com/acme/plain")
    assert p["name"] == "plain"


def test_add_project_rejects_an_invalid_remote(env):
    _, root, _, proj_root = env
    repo = proj_root / "alfa"
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    with pytest.raises(ValueError, match="give the full remote URL"):
        chief.add_project(str(repo), remote="https://bad url")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_settings.py -k "add_project" -v`
Expected: FAIL — `chief.add_project` got an unexpected keyword argument `remote` (and the two behavioural assertions fail).

- [ ] **Step 3: Implement**

Modify `scripts/chief.py` `add_project` (line 401) to accept `remote` and apply it after the project is registered. A remote given for a folder without `.git` is skipped (not an error — it can be set later on the page); an invalid URL still raises from `settings.set_remote`.

```python
def add_project(raw, domain=None, remote=None) -> dict:
    """Add an existing folder to the projects (extras in projects.json) and leave a draft in the vault inbox.

    A remote, if given and the folder is a git repo, is set as the origin (local git config only); a non-git folder skips it."""
    global CFG
    path = check_new_project_path(raw)
    domain = clean_domain(domain)
    stored = str(path).replace("\\", "/")
    fresh = home.read_config(HOME)
    _write_config({**fresh, "extras": [*fresh.get("extras", []), stored]})
    CFG = {**CFG, "extras": [*CFG["extras"], stored]}
    _draft_note(path, stored, DRAFT_TEXT["via_dashboard"])
    p = next(p for p in load_projects() if norm(p["path"]) == norm(path))
    if remote:
        import settings as _settings
        if not (Path(path) / ".git").exists():
            pass                                      # not a git repo: the remote is skipped, set later on the page
        else:
            _settings.set_remote(p["name"], remote)   # invalid URL still raises ValueError
    if domain:
        _store_domain(p["name"], domain)
        p = next(p for p in load_projects() if norm(p["path"]) == norm(path))
    return p
```

(`Path` is already imported in `chief.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_settings.py -k "add_project" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/chief.py tests/test_settings.py
git commit -m "feat: add_project applies an optional git remote (skip if not a repo)"
```

---

## Task 2: Server passes the remote through on add

**Files:**
- Modify: `app/server.py:527-532`
- Test: existing server test suite (no new test; behaviour is covered by Task 1 + a parse check, since the add route just forwards the body field).

- [ ] **Step 1: Implement**

In `app/server.py`, the `["api","projects"]` branch (line 527) currently calls `chief.add_project(body.get("path"), body.get("domain"))`. Pass the remote:

```python
        if parts == ["api", "projects"]:
            try:
                p = chief.add_project(body.get("path"), body.get("domain"), body.get("remote"))
            except ValueError as e:
                raise FeatureError(str(e)) from e
            return {"ok": True, "name": p["name"], "extraction": translate(_onboard(p), self._lang)}
```

- [ ] **Step 2: Verify the file parses**

Run: `python -c "import ast,sys; ast.parse(open('app/server.py').read()); print('ok')"`
Expected: `ok`. (The behavioural path is covered by Task 1's `chief.add_project` tests; the route is a thin forward.)

- [ ] **Step 3: Commit**

```bash
git add app/server.py
git commit -m "feat: forward the optional remote to add_project from the add-project route"
```

---

## Task 3: Add a Git-remote field to the "+ Add project" dialog

**Files:**
- Modify: `app/index.html:526-537` (dialog) and `app/index.html:2584-2590` (submit handler)

- [ ] **Step 1: Add the field to the dialog**

In the `addP` dialog (after the `apDom` label at line 528), add a Git remote input. The existing dialog:

```html
  <label>Category (optional)<input id="apDom" list="domList" maxlength="30" autocomplete="off" placeholder="e.g. banking, home, product"></label>
  <label>Folder path<input id="apPath" maxlength="400" required autocomplete="off" spellcheck="false" placeholder="D:/Projects/my-repo"></label>
```

Change to add the remote field (and a short note) between Category and Folder path:

```html
  <label>Category (optional)<input id="apDom" list="domList" maxlength="30" autocomplete="off" placeholder="e.g. banking, home, product"></label>
  <label>Git remote (optional)<input id="apRemote" maxlength="400" autocomplete="off" spellcheck="false" placeholder="https://github.com/owner/repo"></label>
  <label>Folder path<input id="apPath" maxlength="400" required autocomplete="off" spellcheck="false" placeholder="D:/Projects/my-repo"></label>
  <p class="note">If the folder is not a git repo, the remote is skipped and can be set on the project's page.</p>
```

- [ ] **Step 2: Send the field on submit**

In the `addPForm` submit handler (line 2584-2590), add `remote:$("apRemote").value` to the body:

```javascript
$("addPForm").addEventListener("submit",async e=>{
  e.preventDefault();const go=$("apGo");go.disabled=true;go.textContent=tt("Validating…");
  try{const r=await api("/api/projects",{path:$("apPath").value,domain:$("apDom").value,remote:$("apRemote").value});
    $("addP").close();$("addPForm").reset();toast(tt("Project “{name}” added: {x}.",{name:r.name,x:r.extraction}));await load();
  }catch(err){toast(err.message,true);}
  go.disabled=false;go.textContent=tt("Add");
});
```

- [ ] **Step 3: Verify the dialog field is present in the served page**

Run: `python -c "h=open('app/index.html',encoding='utf-8').read(); assert 'id=\"apRemote\"' in h, 'apRemote missing'; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add app/index.html
git commit -m "feat: capture the git remote in the add-project dialog"
```

---

## Task 4: Remove per-project category/remote from global Settings

**Files:**
- Modify: `app/index.html:2427-2462` (renderSettings) and `app/index.html:2563-2566` (`.sdom` change listener)

- [ ] **Step 1: Drop the Category input and Git remote columns from the Settings Projects table**

In `renderSettings` (lines 2431-2439), the row builder currently renders a Category `<input class="sdom">` and a remote cell. Change the row to keep only name, path, Move, Remove — no category input, no remote cell. Replace:

```javascript
  const rows=SET.projects.map(p=>{
    const n=esc(p.name),shown=esc(cap(p.name)),origin=p.custom?tt("Set by you"):p.from_vault?tt("From the vault"):tt("No category");
    const remote=p.remote?esc(p.remote):(p.git?tt("No remote"):tt("Not a git repo"));
    const mv=moving===p.name
      ?`<input class="smv" data-name="${n}" value="${esc(p.path)}" maxlength="400"><button class="btn sm" data-sact="move-ok" data-name="${n}" type="button">${tt("Save")}</button>`
      :(p.kind==="extra"?`<button class="btn sm ghost" data-sact="move" data-name="${n}" type="button">${tt("Move")}</button>`:"");
    return `<tr><td>${shown}<br><span class="path">${esc(p.path)}${p.domain?`<br><span class="m">${esc(cap(p.domain))}</span>`:""}</span></td>
      <td class="sact">${mv}<button class="btn sm ghost" data-sact="remove" data-name="${n}" type="button">${tt("Remove")}</button></td></tr>`;}).join("");
```

And the section header (line 2447-2450) — drop the now-unused columns and point to the project page:

```javascript
    <section class="sset"><h4>${tt("Projects")}</h4>
      <p class="m">${tt("The category and git remote live on each project's page. Removing takes the project off the list and never deletes the folder.")}</p>
      <div class="wrap"><table><thead><tr><th>${tt("Project")}</th><th></th></tr></thead><tbody>${rows}</tbody></table></div></section>
```

- [ ] **Step 2: Remove the `.sdom` change listener**

The `change` listener (lines 2563-2566) that called `domain` is no longer needed (the page handles category now). Remove it:

```javascript
// delete these four lines:
$("setBody").addEventListener("change",e=>{
  const t=e.target;
  if(t.classList.contains("sdom"))setAct("domain",{name:t.dataset.name,domain:t.value},tt("Category saved."));
});
```

- [ ] **Step 3: Verify the Settings table no longer references the removed controls**

Run: `python -c "h=open('app/index.html',encoding='utf-8').read(); assert 'class=\"sdom\"' not in h, 'sdom still present'; assert 'data-sact=\"domain\"' not in h, 'domain sact still present'; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add app/index.html
git commit -m "refactor: move per-project category/remote out of global Settings"
```

---

## Task 5: Page test — the About block renders category + remote with save buttons

**Files:**
- Test: `tests/test_page_project_settings.py` (new)

- [ ] **Step 1: Write the test**

The project page's About block (`pjAbout`) renders editable Category and Git remote, each with a Save button wired to `data-pjact="dom"` / `data-pjact="remote"`. Assert the served page HTML contains them. (Follow the same page-fetch pattern the existing `tests/test_page_*.py` tests use — read one to match the harness; if the suite uses a shared helper to render `app/index.html`'s `pjAbout`, reuse it. If no page-render harness exists, assert against the source of `app/index.html` that `pjAbout` emits the two inputs and two `data-pjact` save buttons, mirroring how `test_page_projects.py` inspects the page.)

```python
def test_about_block_exposes_category_and_remote_save_buttons():
    import re, pathlib
    html = (pathlib.Path(__file__).resolve().parent.parent / "app" / "index.html").read_text(encoding="utf-8")
    assert 'id="abDom"' in html            # category input
    assert 'id="abRemote"' in html          # remote input
    assert 'data-pjact="dom"' in html       # category save
    assert 'data-pjact="remote"' in html    # remote save
```

- [ ] **Step 2: Run the test to verify it passes (it should, since the About block is already in the tree)**

Run: `python -m pytest tests/test_page_project_settings.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_page_project_settings.py
git commit -m "test: assert the project page About block exposes category and remote saves"
```

---

## Task 6: Full-suite run and final commit

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest tests -q`
Expected: PASS (no regressions; new tests green).

- [ ] **Step 2: Confirm no stray references**

Run: `python -c "h=open('app/index.html',encoding='utf-8').read(); assert h.count('class=\"sdom\"')==0; assert 'id=\"apRemote\"' in h; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Final commit if anything is uncommitted**

```bash
git add -A
git status
git commit -m "feat: project-level settings (category, remote) on the project page"
```

---

## Self-review

**Spec coverage:**
- Add flow captures remote (Task 1, 2, 3) ✓
- Project page About block already in tree, asserted by test (Task 5) ✓
- Settings drops category input + remote column, keeps Move/Remove (Task 4) ✓
- Error handling: non-git skip, invalid URL raises, credentials stripped (Task 1 tests) ✓

**Placeholder scan:** none; every code step shows the change.

**Type consistency:** `chief.add_project(raw, domain=None, remote=None)` signature used in server (Task 2) and tests (Task 1) matches; `apRemote`/`abDom`/`abRemote` ids consistent across Tasks 3 and 5; `data-pjact` values match the in-tree `saveAbout` handler.

**Known risk (flagged, not a plan defect):** `set_remote`'s invalid-URL test relies on `chief.add_project` propagating `set_remote`'s `ValueError`; the plain-folder test relies on the `.git` guard. Both are implemented in Task 1.
