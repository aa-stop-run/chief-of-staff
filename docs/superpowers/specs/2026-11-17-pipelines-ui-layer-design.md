# Spec: Pipelines UI layer — the Pipelines page, the step editor, and create/see/delete

> Status: draft for user review. Language: English (PT optional translation).
> Built on top of the committed phase 0: `feat/pipelines-configur-veis-261113102242` (commit `ab6ab2c`),
> which adds `scripts/engine.py`, `scripts/gates.py`, `scripts/pipelines.py`, `scripts/prompt_refs.py`,
> `scripts/pipelines_builtin/*.json` (6 pipelines), the `features.py` rewiring, the schema-3 migration,
> and 58 golden parity traces (1386 tests). This spec is the part the original
> `2026-11-13-pipelines-configur-veis` spec left as phases 1–3.

## 1. Goal

Let the user, from the dashboard:

- **See** every pipeline: the 6 built-ins plus any custom ones, with valid/invalid state.
- **Create** a new pipeline (a new *kind* of request), with a full step editor.
- **Edit** a pipeline (built-in → fork to a copy you own; or edit a custom one in place).
- **Delete** a custom pipeline.

A new request type is simply a new pipeline file. The engine (`scripts/engine.py`) already runs
any valid pipeline; this spec adds the **data layer** (read/write custom pipelines), the **API**,
the **Pipelines page**, and the **step editor**. It does **not** re-implement the engine.

The hard rule from the original spec stays: **only the user edits pipelines** — never a worker, never
autopilot, never the curator. The engine still enforces every security invariant (no push, tests gate,
no commit without APPROVED, ship/release authorisation, `FIXED_DENY`, write-deny on `pipelines/`).

## 2. Out of scope

- **Engine changes.** `scripts/engine.py`, `gates.py`, `prompt_refs.py` and the built-in JSONs are
  frozen inputs. If a step the editor produces is invalid, the editor rejects it (using the same
  `pipelines.validate`), it does not relax the engine.
- **Per-request overrides** (`Features.create(..., pipeline=…, overrides=…)`). The `kind` in
  «+ New request» maps to a built-in of the same name as today. Picking a different pipeline for one
  request, and per-step model/effort overrides, is a later request.
- **Export/import** of pipelines as files. (The page will be read-only-friendly; import is deferred.)
- **Cost ceiling** (`limits.max_cost_usd`). Accepted and stored by the validator, still no runtime effect.
- **Prompt authoring.** Prompts stay in Python (`prompts*.py`, `prompts_kinds.py`), referenced by
  `prompt.ref = "builtin:<kind>.<phase>"`. The editor can point a step at an existing ref; free-form
  prompt text is not in this spec (a later request).

## 3. Data layer — `scripts/pipelines_store.py` (new)

A thin, atomic, token-guarded module over `<home>/pipelines/`. The engine's `pipelines.load`/`validate`
already do all the checking; the store only handles files and the id/uniqueness rules.

- `list(home) -> list[Entry]` — delegate to `pipelines.list_all(home)` (built-in + user, each valid or
  not, with the reason). This is the "see" source of truth and is reused verbatim.
- `get(home, id) -> Pipeline` — load one (built-in or user); raise `PipelineError` if unknown/invalid.
- `create(home, data: dict, *, forked_from: str | None) -> Entry` — validate (`pipelines.validate`),
  then write `<home>/pipelines/<id>.json`.
- `update(home, id, data: dict) -> Entry` — validate, then replace `<home>/pipelines/<id>.json`
  (user pipelines only; built-ins are never written — editing a built-in is a *fork*, §4).
- `delete(home, id) -> None` — delete `<home>/pipelines/<id>.json` (user only, §5).

Rules, enforced here:

- **id rules:** 3–48 chars, `[a-z0-9-]` (kebab-case). Built-in ids are reserved; a custom pipeline may
  not reuse one.
- **Writes go through `scripts/atomicio.py`** (temporary file + replace), same as every other state
  file. `home.ensure()` is not involved (pipelines are not versioned data), but the folder is created
  on first write if missing.
- **Built-ins are read-only.** `create`/`update`/`delete` refuse `origin == "builtin"` with a clear
  error; the UI instead offers "Edit" → **fork** (a new custom `id`, pre-filled from the built-in,
  `forked_from` recorded).
- **Delete is refused** if any request (open **or** closed) in `state/features.json` has
  `pipeline.id == id`. The error names how many requests use it and how to fix it (re-point them, or
  cancel them — the page shows the count; re-pointing is a later request, so the practical fix today is
  cancel/closed-only).

The store never writes prompts, never touches secrets, and never pushes. It is pure file I/O.

## 4. API — routes in `app/server.py`

All behind the existing `?t=` token for GET; POST requires the `X-Token` header, the dashboard's own
`Origin`, and a small JSON body (the standard write-action contract).

- `GET /api/pipelines` — `list_all(home)` → `[{id, origin, valid, reason}]`, user entries first.
- `GET /api/pipelines/<id>` — the full pipeline (steps, limits, inputs, gates) for the editor, plus
  `origin` and `digest` (a short hash shown in the card, like `features_hash`).
- `POST /api/pipelines` — `{id, data, forked_from?}` → `create`. 409 if id taken; 422 with the
  validator's message(s) if invalid.
- `POST /api/pipelines/<id>/update` — `{data}` → `update`. 422 if invalid; 403 if built-in.
- `POST /api/pipelines/<id>/delete` — body `{}` → `delete`. 409 with the in-use count if refused.

The response to a create/update includes `valid` and, if false, `reason`, so the page can show
"saved, but invalid: …". The list endpoint is what «Today» and the new-request dialog use to render
kinds (today that's a hard-coded list from `prompts_kinds.KINDS`; this request switches it to the
built-in subset of `list_all`, so a *new built-in* appears automatically — see §6, note on kinds).

## 5. UI — the Pipelines page (`app/index.html`, new tab or Settings sub-panel)

A single page, reachable from the header (next to "Projects"), **and** linked from the new-request
dialog ("Manage pipelines…"). It has three views:

**List (default).** One row per pipeline from `GET /api/pipelines`:

- name, `origin` badge (Built-in / Custom), valid state (✓ / ⚠ reason).
- for Custom: **Edit**, **Delete** (armed second click).
- for Built-in: **Use as template** (open editor pre-filled, saved as a fork) and **Details** (read-only).
- a **New pipeline** button (empty editor).

**Details (read-only).** The steps rendered top-to-bottom as a vertical list — type, phase, mode,
gate(s), model/effort, artifact, archive target. This is the "see" for built-ins. Same renderer the
editor uses, so what you see is what the editor edits.

**Editor (create / edit).** A form over the pipeline JSON, one panel per step plus a top panel for
`id`, `name`, `version`, `limits`. Per step the user edits:

- `type` (worker / check / approval), and for worker: `phase`, `mode` (read_only / worktree /
  local_commit / publish / production), `artifact`, `archive`, `questions`, `prompt.ref`, `inputs`,
  `keep_questions_on_next`, `gates`, model/effort.
- for check: the gate(s) and the pass/fail→next-step mapping.
- for approval: `of` (which step) and `requires_user`.
- add / remove / reorder steps; the `on_approve` / `on_request_changes` / `on_check` transitions are
  edited as a small "then go to" selector per step.

The editor is **client-side validated** against a copy of `pipelines.validate`'s rules (imported as
plain data: allowed step types, allowed modes, the gate catalog, the publish-needs-approval rule) so
the user gets instant feedback, and the **server re-validates** on save (the authoritative check). If
the server rejects, the page shows the validator message and keeps the user's edits.

**Security of the editor surface:** the editor may only reference prompts that exist in
`prompt_refs` (the menu is generated from the known refs), may only reference gates that exist in
`gates.py`, and the server's `validate` is the final gate. The engine's invariants are not things the
editor can remove: `mode: publish/production` always forces an approval step before it (enforced by
`validate` and re-checked by the engine), `FIXED_DENY` is added by the launcher regardless of what
the JSON says, and `check` can never advance an `approval` (engine rule). The editor makes these
hard, it doesn't gate them.

## 6. Kinds in «+ New request» and on the card

- The kind selector lists the **built-in** pipelines (`origin == "builtin"`). This request does not let
  the user pick a custom pipeline for a request (that's the per-request override, §2 out of scope).
- The card's stepper already reads the pipeline snapshot (`pipeline.json`) from phase 0 — unchanged.
- A newly created **custom** pipeline is visible and manageable on the Pipelines page immediately,
  but is **not yet runnable by a request** in this spec: the kind selector only lists built-ins
  (§2 defers per-request overrides). (Note for the plan: if the user expects "I created a kind, can I
  use it in a request now", the answer in this spec is "you can see/edit/delete it; running a request
  on it is the per-request-override request." The page will say so.)

## 7. Migration / state

- No `schema_version` bump. Pipelines are new data in `<home>/pipelines/`, absent by default;
  `list_all` already handles a missing folder (returns built-ins only). Old requests keep their
  phase-0 `pipeline` snapshot and are unaffected.
- No worker reads or writes `<home>/pipelines/` (the write-deny is already in phase 0, T6).

## 8. Testing

Suite: `python -m pytest tests`. Baseline before any change; final at the end; report with
`BASELINE`, `FINAL RESULT`, `NEW TESTS`.

New tests (file::name):

- `test_pipelines_store.py`:
  - `test_create_valid_writes_file` / `test_create_invalid_rejected`
  - `test_create_id_rules` (reserved builtin id, bad charset, too long)
  - `test_update_user_ok_builtin_refused`
  - `test_delete_user_ok_builtin_refused` / `test_delete_refused_when_request_uses_it`
  - `test_fork_builtin_creates_custom`
  - `test_list_marks_valid_and_invalid`
- `test_pipeline_routes.py` (server, 127.0.0.1, token + Origin on POST):
  - `test_list` / `test_get` / `test_create` / `test_update` / `test_delete`
  - `test_create_409_duplicate` / `test_update_builtin_403` / `test_delete_inuse_409`
  - `test_post_requires_token_and_origin`
- `test_page_pipelines.py` (node, pure JS helpers in the `// <review>` region of `app/index.html`):
  - `test_kind_selector_uses_builtins` / `test_editor_reorder_steps`
  - `test_editor_publish_requires_approval_step` / `test_delete_arms_on_second_click`
  - `test_list_shows_invalid_reason`

Acceptance: a new request's kind menu is unchanged in behaviour (the 6 built-ins appear, in order);
the Pipelines page lists built-ins + custom; create/edit/delete round-trips through the API; delete
is blocked while a request uses it; the server re-validates and rejects an invalid step set; no
existing `test_page_*` / `test_pipeline_*` test changes.

## 9. Risks

- **R-1 (editor/validator divergence).** The client-side validator and the server `validate` can
  drift. Mitigation: the client validator is generated from the same constants (a small JSON the
  server exposes or a checked-in mirror), and the server is authoritative. A test asserts the two
  agree on a handful of known-valid and known-invalid pipelines.
- **R-2 (deleting a used pipeline).** Refusal is by count, not by re-pointing; the user must cancel
  or close requests first. Mitigation: the 409 names the count and the page lists which requests.
  Re-pointing is a later request.
- **R-3 (editor is large).** A full step editor is a lot of UI in `app/index.html`. Mitigation: the
  editor reuses the phase-0 snapshot renderer (the card already renders steps), so the list/detail/
  editor share one renderer. The page helpers stay in the `// <review>` region and are tested with
  node like the other page tests.
- **R-4 (kinds menu coupling).** Switching the kind menu from the hard-coded list to `list_all`
  built-ins changes a live code path. Mitigation: built-in order and ids are stable; a test asserts
  the menu equals the 6 built-ins in the current order.
