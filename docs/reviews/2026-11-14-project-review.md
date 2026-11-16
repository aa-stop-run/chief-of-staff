# Project review — 2026-11-14

Reviewed at `1a8f9e4` (clean tree). Sources: the vault notes of the project, every module in `scripts/` and `app/server.py`
read in full, `app/index.html` scanned (not read line by line), and the real data folder (counts only).

- Test suite: **1000 passed, 0 failed** (`python -m pytest tests -q`, 5 min 40 s).
- Bugs marked **reproduced** were run against temporary folders; the others were found by reading and are marked as such.
- The review itself changed nothing. The fixes and the ideas were implemented afterwards on the branch
  `review-fixes-2026-11-14`; section 6 says what was done and what was left.

## 1. Why the project exists (from the vault)

One terminal and one dashboard for every personal project: answer questions from the vault and the real repo state, and
launch background Claude Code workers with a human gate. Decisions that shape everything else:

- The only human control point is the dispatch, because workers run in `auto` mode. Each «Approve» is that «yes».
- The dashboard shows only what needs a decision; knowledge lives in the vault, which agents write only through the inbox.
- A claim is not proof: the implementer never commits, an independent reviewer runs the suite, the server compares numbers.
- Parallel work is allowed, so each implementation has its own worktree, and the merge and the push stay with the user.
- The app is code only; the user's data lives in a versioned home, so the app can be changed while it is in use.

The design matches these goals well. The gaps below are mostly at the edges: what happens when a launch fails, when a
project changes under an open request, and what happens after «Accept implementation».

## 2. Bugs

### High

| # | Bug | Where | Status |
|---|-----|-------|--------|
| H1 | `chief.py status` crashes with `KeyError: 'inbox'` for any setup with more than one project. The two lines that set `inbox` and `workers` are outside the `for` loop, so only the last project gets them. This is the documented second source of truth for the terminal Chief. No test covers `cmd_status`. | `scripts/chief.py:488-491`, `:703` | reproduced on the real data |
| H2 | `chief.find` calls `sys.exit` for an unknown or ambiguous project, and `SystemExit` is not an `Exception`. It passes through `Features._start` and through the ticker's `except Exception`. The ticker thread dies without a message (no automatic review, no resume, no curation until restart), and every `GET /api/state` dies without an answer, so the dashboard freezes. Triggers: removing a project in Settings while it has an open request (nothing checks), hiding its domain, renaming its hub note in the vault, «Create request» from a to-do of a removed project. | `scripts/chief.py:411`, `scripts/features.py:857`, `app/server.py:517`, `scripts/settings.py:120` | reproduced |
| H3 | With «run until the review», if the launch of the next phase fails, the request stays on the **next** stage as `awaiting` with no artifact. The next «Approve» then skips that phase (a build starts with no plan). `_approve` moves the stage before `_start`, and `_autopilot` only logs the error. | `scripts/features.py:697-698`, `:360-363` | reproduced |
| H4 | The secret guard of the pipeline's commit misses files in folders with non-ASCII names. `git diff --cached --name-only` quotes such paths, the file name ends in `"`, and the pattern no longer matches. `configuração/.env` was committed in the reproduction. | `scripts/chief.py:917`, `:897` | reproduced |
| H5 | The `/` shortcut calls `preventDefault` whenever the focus is not on the filter box, including inside text areas and inputs. `/` cannot be typed in a comment, a description or the clone URL (pasting works). No handler stops the event earlier. | `app/index.html:2523` | read in the code, not run in a browser |

### Medium

| # | Bug | Where | Status |
|---|-----|-------|--------|
| M1 | «Try again» (and the automatic resume after a usage limit) during a fix round starts the implementer without the review findings: the prompt no longer points to `review.md`. `_pending_feedback` returns only the user's comment, and `auto_resume` passes an empty feedback. | `scripts/features.py:751`, `:756-764`, `:314` | reproduced |
| M2 | `--home=<folder>` (with `=`) is accepted by argparse but ignored by the early check, so a «throw-away» second instance runs on the real data. Only `--home <folder>` works. | `app/server.py:29` | read in the code |
| M3 | Curation counts a good run as a failure when new drafts arrive while it runs (`handled = before - after` by count). Three such runs stop the automation, and this happens exactly when many workers are active. | `scripts/curation.py:218-233` | reproduced |
| M4 | Worktrees and `feat/<id>` branches are never removed, on «done» or on «cancel». The real data has the worktree of a finished request from 2026-11-06. | no code does it | confirmed on the real data |
| M5 | «Create request» from a to-do fails when the title has 121 to 140 characters (to-dos allow 140, requests 120) or when the to-do has no summary («description required»). | `app/server.py:400-404`, `scripts/pending.py:15`, `scripts/features.py:25` | read in the code |
| M6 | A project is identified by its display name, which changes when the curator creates its hub note or the note is renamed. Requests, workers, costs and categories keep the old name: the project page and the filters stop showing them (`_mine`, `w["project"] == p["name"]`). | `scripts/chief.py:145`, `scripts/projectpage.py:103` | read in the code |
| M7 | The tests gate passes when the report has no `FINAL RESULT` line (`final and final != …`). The documentation says that line is mandatory and checked. | `scripts/features.py:179-180` | read in the code |
| M8 | Every state build reads the artifacts, questions and answers of **all** requests ever created, then filters by session; in-scope artifacts (up to 40 KB each) are sent on every 8 s poll. Cost grows with history. | `app/server.py:251-256` | read in the code |
| M9 | `load_projects` raises if one configured root does not exist (unplugged drive, renamed folder). The whole state fails. | `scripts/chief.py:136`, `:166` | read in the code |

### Low

| # | Bug | Where |
|---|-----|-------|
| L1 | Archived questions are overwritten: the spec's questions are saved as `questions.plan.v1.json`, and the first plan revision writes the same name. **Reproduced.** | `scripts/features.py:853` |
| L2 | The pipeline's commit message is always `feat:`, also for bug fixes and maintenance. | `scripts/features.py:495` |
| L3 | A failed request flips to `working` and back each time `claude agents` cannot be queried (`_is_active` answers True), adding history lines and re-arming the alert. | `app/server.py:65-67`, `scripts/features.py:256-261` |
| L4 | Several state files are not written through `atomicio`, against what `CLAUDE.md` says: proposals, work sessions, usage ledger, curation, onboarding, plan usage and `projects.json` use a fixed `.tmp` name or no lock. Two processes write some of them (a worker running `proposals.py` and the server; `chief.py session end` and the heartbeat). | `scripts/proposals.py:61`, `worksession.py:44`, `usage.py:197`, `curation.py:82`, `chief.py:206` |
| L5 | Portuguese left in the English CLI: `sem git`, `terminado`. `cmd_workers` also shows the raw state, not `session_state`. | `scripts/chief.py:702`, `:944` |
| L6 | Settings: `from_vault` shows the Chief's own category when one is set, because `load_projects` already applied it. | `scripts/settings.py:307` |
| L7 | «Resume» opens no terminal outside Windows (`CREATE_NEW_CONSOLE` is 0 there). | `scripts/chief.py:813` |
| L8 | Request ids and archive names drop accented letters (`configur-veis`). | `scripts/features.py:187`, `scripts/chief.py:110` |
| L9 | Documentation drift: `CLAUDE.md` says the Chief never edits Claude Code's `settings.json`, but Settings → «connect» writes the status line there (with a backup, on the user's click). The `features.py` docstring still says nothing advances by itself. | `scripts/settings.py:221-235`, `scripts/features.py:4` |
| L10 | Size: `features.py` 941 lines, `chief.py` 986, `index.html` 2552, and a leftover `if True:` block in `_approve`. | `scripts/features.py:661` |

### To be confirmed with a real run

- The deny rules are text patterns (`Bash(git push:*)`). `git -C <path> push` or `cd x && git push` may not match. The vault
  already lists a real run on a throw-away repo as a next step; these two forms should be part of it.
- The worst-case worker command line measures about 19 400 characters (limit on Windows: 32 767). It is safe with
  `claude.exe`; an installation through a `.cmd` shim would be limited to 8 191.

## 3. Are the flows well thought out?

What works well:

- The gates are in the server, not in the prompts: ship, release, tests and verdict parsing are code with tests.
- Reviewer independence and «no commit before the review» are enforced by construction (worktree, no context shared).
- The rules that came from real incidents are visible in the code (execution status, one plan-fix round, limit = pause).

Where the flows are weak:

1. **Failure paths are less careful than success paths.** H2, H3, M1 and L3 are all «a launch failed or the worker
   vanished». In the real data 6 of 45 launches ended in `failed`, so these paths run often.
2. **The flow stops one step early.** After «Accept implementation» the card says the merge is yours and nothing more:
   no commands, no check that the branch was merged, no cleanup (M4).
3. **A request can outlive its project.** Nothing links removing, hiding or renaming a project to its open requests (H2, M6).
4. **Large requests do not converge.** The vault records it: a 48 KB plan, a build of 30 to 60 minutes, two BLOCKED
   reviews because the honest report said «partially executed». The pipeline has no notion of request size.
5. **The terminal Chief cannot drive the pipeline.** Requests exist only in the dashboard; `chief.py` has no
   `requests`, `approve` or `ask`, and its `status` is broken (H1).
6. **Waiting is silent.** A finished phase is seen only when the user looks at the tab; alerts are printed in the
   server's terminal.

## 4. Ideas

### Workflow

| Idea | Why |
|------|-----|
| A «Finish» step after acceptance: show the exact merge commands, detect that `feat/<id>` is merged, then offer to remove the worktree and the branch. | Closes the flow and fixes M4. |
| Block «remove project» and «hide domain» while the project has open requests, and give projects a stable id (the path) with the name as a label. | Removes the cause of H2 and M6. |
| Size check in the plan: the planner states the number of tasks and the validator proposes a split into linked requests above a limit. Add «next request» links (parent and child). | The vault's own learning on non-convergence. |
| Show the worker's last message (`[BLOCKED]` or `[QUESTION]` and its 5 lines) on a failed card, instead of «the worker stopped without the result». | 13 % of launches fail; the reason is already in the log. |
| Before each launch, warn when the 5-hour window is above 90 %, and show the request's cost so far on the «Approve» button. | Avoids starting a long build that will be paused. |
| Pipeline commands in the CLI: `chief.py requests`, `approve <id>`, `ask <id> "…"`. | Lets the terminal Chief do what its `CLAUDE.md` promises. |
| A «Scan sessions» button (or a slow ticker) for the session to-dos. | Today it needs the terminal and a manual comparison. |
| Change a request's kind, and create a feature or fix from a finished analysis (already in the vault's next steps). | Analyses are 1 of the 4 open cards. |

### User experience

| Idea | Why |
|------|-----|
| Browser notification and a count in the tab title when a card starts waiting for the user. | Fixes «waiting is silent». |
| One «Needs you» queue sorted by how long each card has waited, with the wait shown. | 4 cards are `awaiting` now; nothing says which is oldest. |
| Diff summary on the review card (`git diff --stat` of the worktree, link to the folder). | The user accepts code without seeing its size. |
| Replace the native `confirm()` boxes with the arm-to-confirm pattern already used by «End session». | Consistency; native boxes block the page. |
| Send artifacts only when they change (a hash per card), not on every poll. | Fixes M8 and makes the page lighter. |
| Environment check (`doctor`) inside Settings. | Today it is terminal only. |

### Engineering

- Add a GitHub Actions run of the suite. It takes 5 min 40 s locally; mark the slow server tests so a fast subset exists.
- Add a smoke test for every `chief.py` subcommand (H1 would have been caught).
- Finish the configurable-pipelines work before adding more kinds: `features.py` is at the point where each new kind
  costs more than the last, which is what its spec says.

## 5. Suggested order of fixes

1. H1, H5 (one line each) and H2 (raise instead of `sys.exit` in the library path, catch `BaseException` in the ticker).
2. H3, H4, M1, M2: each has a reproduction that becomes the failing test.
3. M3, M5, M7, L1, L2.
4. The «Finish» step (M4) and project identity (M6) as their own requests.

## 6. What was implemented (branch `review-fixes-2026-11-14`)

Tests for all of it: `tests/test_review_fixes.py`, `test_review_features.py`, `test_page_review.py`, `test_review_server.py`.

| Item | Done | How |
|------|------|-----|
| H1 | yes | The two lines are inside the loop; a smoke test runs every `chief.py` subcommand. |
| H2 | yes | `chief.lookup` raises `ProjectNotFound`; only the CLI's `find` exits. `Features._spawn` turns a launcher's `SystemExit` into a failed launch; the ticker also survives it. A project with open requests cannot be removed in Settings. |
| H3 | yes | `_approve` puts the stage back when the next phase cannot be launched. |
| H4 | yes | `git diff --cached --name-only -z`. |
| H5 | yes | `slashFocuses`: never in a field, never with a dialog open. Verified in a browser on the demo. |
| M1 | yes | `_start` keeps what the run was told to change; retry and limit-resume repeat it. |
| M2 | yes | `app/launchargs.py` reads both forms of `--home`. |
| M3 | yes | The curator's run counts the drafts it was given, by name. |
| M4 | yes | Finish step: merge commands, merged detection, «Remove worktree and branch», «Discard the work». |
| M5 | yes | `features.request_from_todo` cuts the title and falls back for the description. |
| M6 | yes | Requests keep their project's folder and follow a rename, with their workers and costs. To-dos, suggestions and categories are still keyed by name. |
| M7 | partly | The history says «tests not compared» when the report has no final result. The commit still goes ahead: making it block would change the pipeline's behaviour for every report without numbers, which is a decision for the user. |
| M8 | yes | Artifacts are read only for the cards shown; unchanged cards are left out of the poll answer. |
| M9 | yes | A missing projects folder is skipped. |
| L1 to L9 | yes | Archive names never collide; commit type follows the kind; no flipping while sessions are unknown; every state file goes through `atomicio` (with a lock where two processes write); English CLI; `from_vault`; a terminal on macOS and Linux (not run there); accents transliterated; documentation corrected. |
| L10 | no | File sizes grew instead (`features.py`, `index.html`). The split belongs to the configurable-pipelines work. |
| Deny rules (`git -C … push`) | no | Still needs a real run to know how Claude Code matches the patterns. |
| Ideas: workflow | yes | Finish step, project identity, plan size and follow-up requests, the worker's last words, allowance warning and cost before approving, CLI commands, session scan, kind change. Hiding a domain with open requests is not blocked (there is no control for it in the dashboard). |
| Ideas: user experience | yes | Notifications and tab count, queue by wait, change size on the card, second-click confirmations, lighter polling, environment check in Settings. |
| Ideas: engineering | yes | GitHub Actions workflow (Windows only: the suite has not been run elsewhere), `slow` marker, subcommand smoke test. |
