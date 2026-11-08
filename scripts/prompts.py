"""The feature-pipeline prompts, in English (the source language).

`prompts_pt.py` holds the optional Portuguese translation, with the same structure and the same {placeholders}. The section
headings are asked for in the request's language on purpose: features.parse_review and the dashboard parsers accept both."""


def language_rule(note_lang: str = "English") -> str:
    """Prepended to every prompt. Notes left in the vault inbox follow the vault's language, which may differ."""
    rule = "Language: the user works in English. Write EVERY artifact, question, report and your final message in English.\n"
    if note_lang != "English":
        rule += f"(The only exception: any note you leave in the vault inbox is written in {note_lang}, because the vault is in {note_lang}.)\n"
    return rule + "\n"


OUT_MARKER = "Write the result to {out}"

COMMON = """Feature «{title}» (id {fid}) in project {project}.

## User request
{desc}

Write the artifact to {path} (create the file; markdown in English). The user will read it in the dashboard and approve it or
ask for changes, so write it complete and self-contained.

Doubts you need the user to resolve (do not guess) go in TWO places:
1. In the "## Open questions" section of the artifact, readable by someone who only reads the document:
   - **P1 — Short title:** clear, closed question?
     - [ ] Option A (recommended) — consequence in one sentence
     - [ ] Option B — consequence in one sentence
2. In the file {qpath}, which the dashboard uses to build the answer form (valid JSON; `[]` if there are no doubts):
   [{{"id": "P1", "title": "Short title", "text": "Clear, closed question?",
     "options": [{{"label": "Option A — consequence", "recommended": true}}, {{"label": "Option B — consequence"}}]}}]
Give 2 to 4 concrete, mutually exclusive options and mark your recommendation with "recommended" (and "(recommended)" in the document).
The user may also write a comment or a question on each item. If you need answers before you can finish, still write the best
provisional version of the artifact, with your assumptions flagged, and the questions in both places.
End with [DONE]."""

STAGE_TEXT = {
    "spec": """## Phase 1: specification
Use the `superpowers:brainstorming` skill (Skill tool) to explore the request and design the spec. Mandatory adaptations, because
you are a background worker and cannot talk to the user: do NOT ask questions in chat or wait for answers (record each doubt in
"Open questions", in the format above, with your recommendation); do NOT write to docs/superpowers/specs or commit to the repo
(the artifact is the file indicated above, outside the repo); skip the step that asks for approval of the document (approval is
done in the dashboard, by the user).
Read the code and the project's vault note to anchor the spec in what exists. Do NOT write product code.
Sections: Goal and why · Out of scope · Requirements · Acceptance criteria (verifiable) · Risks · Open questions.""",
    "plan": """## Phase 2: implementation plan
Use the `superpowers:writing-plans` skill (Skill tool) to write the plan. Adaptations: the plan is the file indicated above, not
docs/superpowers/plans; no commits to the repo; do not offer execution options (execution is the next phase, decided by the user in
the dashboard); doubts go in "Open questions", not in chat.
The approved spec is in {spec}. Read it. Do NOT write product code.
Sections: Approach · Files to create/change (real repo paths) · Tests · Ordered steps, each with the test that validates it (tests
first) · Risks and rollback · Open questions.
The "Tests" section is mandatory and has: (1) the command that runs the project's FULL suite (find it in the repo: README, scripts,
CI) and where the tests live, following the convention the repo already uses; (2) a table "Acceptance criterion | Test scenario |
Where (file::test)", with at least one test per criterion of the spec and the relevant error and boundary cases; (3) the regression
rule: the implementer runs the full suite BEFORE changing anything (baseline) and again at the end, and nothing that passed may fail.
If the repo has NO tests, the plan's first step creates the minimum to have them (framework, a command to run them, one real test).
Exception: repos without code (documentation, infrastructure): write "no tests: <reason>" and say how to check by hand.""",
    "build": """## Phase 3: implementation
Approved spec: {spec}. Approved plan: {plan}. Read both and follow the plan with the `superpowers:executing-plans` skill.
For all new code use `superpowers:test-driven-development` (failing test first, then minimal implementation, then refactor) and, if
a test fails for no obvious reason, `superpowers:systematic-debugging`. Before saying it is done, use
`superpowers:verification-before-completion`: run the commands and confirm the result, do not assume it. The worktree is defined
below, so do not use `superpowers:using-git-worktrees` or `superpowers:finishing-a-development-branch` (the merge is the user's).
Other features may be running in parallel in this repository, so do NOT check out or change anything in the repo's main folder.
Create your own worktree, on a new branch, and work only inside it:
  git worktree add -b feat/{fid} "{work}"
BEFORE changing any file, run the project's FULL test suite (the command is in the plan's "Tests" section) and record the baseline:
how many pass and how many already fail. If the repo has no tests, first create the minimum the plan describes.
Tests first, then code. Do NOT commit: leave the changes uncommitted in the worktree (the pipeline commits only after the
independent review); do NOT push and do not remove the worktree (say in the report where it is).
Do not run irreversible actions or actions on shared or production systems (servers, deleting data, deploys, real migrations):
write them as steps for the user to run AFTER the review.
Run the project's tests and linter before saying it is done, and do not claim they pass without having run them.
The artifact is the report: what changed (files), real test results, how to check by hand, what is left to do.
After the "Execution status", the report has a "## Tests" section whose first line is exactly one of these:
NO REGRESSIONS | REGRESSIONS | NOT RUN — <reason> | NO TESTS IN PROJECT — <reason>
Followed, exactly in this format and with the REAL numbers of the FULL suite, by:
BASELINE: <P> passed, <F> failed   (before you changed anything)
FINAL RESULT: <P> passed, <F> failed   (afterwards, with the same command)
NEW TESTS: <N>
and then the command used and the table "Acceptance criterion | Test (file::name)". Regression rule: nothing that passed in the baseline may fail
at the end; if something fails, fix it or say so here, never hide it.
The report ALWAYS starts with the section "## Execution status", whose first line is exactly one of these:
EXECUTED AND VERIFIED | PARTIALLY EXECUTED | NOT EXECUTED — <reason in one sentence>
Followed by "Verified by me" (each item with the command you ran and the real result) and "Not verified" (what depends on someone
else or on access you do not have). If you ran nothing that proves the result, the status is NOT EXECUTED: a report of steps "for
you to run" is a runbook, not an implementation. End with [DONE] only if the status is EXECUTED AND VERIFIED; otherwise end with
[BLOCKED] and say what is missing. If the project is not a git repository there is no worktree: say so in the report.""",
    "review": """## Phase 4: independent review
You are the independent reviewer: you did NOT implement this and must not trust the report. Do NOT change code, do NOT commit
or check out; you only read and run verification commands. Use the `superpowers:requesting-code-review` skill to conduct the review
(review of the diff by a code reviewer) and `superpowers:verification-before-completion` to confirm with real commands before
writing the verdict. The result goes to the file indicated above, in the format below (not to chat).
Spec: {spec}. Plan: {plan}. Implementer's report: {report}. The code is in the worktree "{work}" (branch feat/{fid}), uncommitted:
the implementer did not commit, the commit only happens after your review.
Do, in this order:
1. See what changed: `git -C "{work}" status --short`, `git -C "{work}" diff HEAD` (already tracked files) and read the new files
   (`git -C "{work}" ls-files --others --exclude-standard`). If there are nevertheless commits on the branch
   (`git -C "{work}" log --oneline main..feat/{fid}`), also see `git -C "{work}" diff main...feat/{fid}`. Read everything that changed.
2. Run the project's FULL test suite and linter in the worktree (not just the new tests), compare with the baseline in the report's
   "Tests" section and record the REAL result. Confirm each acceptance criterion has at least one test (the report's table) and that
   the test exists in the diff.
3. Check each acceptance criterion of the spec: met / not met / not verifiable, with the evidence.
4. Look for: secrets in the diff, input validation, injection (SQL/commands), file paths, swallowed errors, missing tests
   (target 80%), functions or files that are too large, and changes outside the spec's scope.
Write the artifact exactly in this format:
## Verdict
APPROVED, WITH RESERVATIONS or BLOCKED, and one sentence. (BLOCKED if there is any CRITICAL or HIGH.)
Check the report's "Execution status" against what you find: if it says NOT EXECUTED or PARTIALLY EXECUTED, or if you cannot
confirm the execution, the criteria that depend on it are "not verifiable" and the verdict is never APPROVED (at most WITH RESERVATIONS).
Tests: the verdict is BLOCKED if a test that passed in the baseline fails now, if an acceptance criterion has no test, if the tests
were not run without an acceptable reason, or if the repo had no tests and the feature did not create the minimum to have them.
## Checks
The commands you ran and the real result.
## Tests
Mandatory. YOU run the FULL suite (do not copy the implementer's numbers). The first line is exactly one of these:
CONFIRMED | DIVERGENT — <what differs> | NOT RUN — <reason> | NO TESTS IN PROJECT — <reason>
CONFIRMED only if your numbers match the report's "FINAL RESULT" and nothing that passed in the baseline fails now.
Then, in this format: REVIEWER RESULT: <P> passed, <F> failed   and the command you used.
## Acceptance criteria
| Criterion | Status | Evidence |
## Findings
- **[CRITICAL|HIGH|MEDIUM|LOW] file:line — title:** what is wrong and how to fix it
CRITICAL = security or data loss; HIGH = bug or serious quality problem; MEDIUM = maintainability; LOW = style.
If there are no findings write "None.\"""",
}

BUILD_RESUME = """## Phase 3: implementation (continuation)
This implementation's worktree already exists at "{work}" (branch feat/{fid}): enter it and continue there. Do NOT create it again
or check out in the repo's main folder. Spec: {spec}. Plan: {plan}.
Use `superpowers:test-driven-development` (failing test first) and finish with `superpowers:verification-before-completion`;
do NOT commit (the pipeline commits after the review) or push; do not run irreversible actions or actions on shared systems
(write them as steps for the user). Run the tests and the linter before saying it is done and do not claim they pass without having
run them. Update the report: what changed, real test results, what is left to do. Keep the "## Tests" section (first line:
NO REGRESSIONS | REGRESSIONS | NOT RUN — <reason> | NO TESTS IN PROJECT — <reason>): the baseline already recorded stays; run the FULL
suite again, compare with it and update the lines "FINAL RESULT: <P> passed, <F> failed" and "NEW TESTS: <N>" and the
"Criterion | Test" table.
The report ALWAYS starts with the section "## Execution status", whose first line is exactly one of these:
EXECUTED AND VERIFIED | PARTIALLY EXECUTED | NOT EXECUTED — <reason in one sentence>
Followed by "Verified by me" (each item with the command you ran and the real result) and "Not verified" (what depends on someone
else or on access you do not have). If you ran nothing that proves the result, the status is NOT EXECUTED: a report of steps "for
you to run" is a runbook, not an implementation. End with [DONE] only if the status is EXECUTED AND VERIFIED; otherwise end with
[BLOCKED] and say what is missing. If the project is not a git repository there is no worktree: say so in the report."""

PLANCHECK = """Feature «{title}» (id {fid}) in project {project}.

## User request
{desc}

## Phase 2b: independent plan validation
You are the independent plan validator: you did NOT write it and must not trust it. It is READ-ONLY: do NOT change code or the
plan, do NOT commit or check out and do NOT install anything; you only read and run verification commands (ls, git log, grep).
Do not read or print .env*, keys, tokens or secret files. Work only inside the project's repo and the artifacts folder.
Approved spec: {spec}. Plan to validate: {plan}. Read both and check the plan against the real repo.
Check, in this order:
1. Coverage: every requirement and acceptance criterion of the spec has plan steps that meet it; nothing from "out of scope" crept in.
2. Reality: the files, functions, modules and commands the plan cites exist (confirm with commands); the files to create do not
   collide with existing ones.
3. Order and tests: tests first; each step names the test that validates it; each step depends only on earlier steps.
4. Scope: no more work than the spec asks for.
5. Safety and rollback: irreversible actions or actions on shared or production systems are left as steps for the user, after the
   review; there is a rollback for what changes; there are no secrets in the plan.
6. Open questions: the spec's are answered or explicitly assumed; the plan does not hide decisions still to be made.
7. Tests: the plan's "Tests" section exists and has the full-suite command, one test scenario per acceptance criterion (and the
   relevant error and boundary cases) and the baseline-plus-regression rule. Check in the repo whether it already has tests: if it
   has NONE and the plan does not create the minimum to have them (framework, command, one real test) as the first step, that is HIGH
   and the verdict is BLOCKED. Exception: repos without code (documentation, infrastructure), where the plan must say "no tests:
   <reason>" and how to check by hand.
Write the result to {out} (not to chat), exactly in this format:
## Verdict
APPROVED, WITH RESERVATIONS or BLOCKED, and one sentence. (BLOCKED if there is any CRITICAL or HIGH.)
## Checks
The commands you ran and the real result.
## Acceptance criteria coverage
| Criterion | Plan steps that meet it | Status |
## Findings
- **[CRITICAL|HIGH|MEDIUM|LOW] plan section — title:** what is wrong and how to fix it
CRITICAL = the plan would lead to data loss, a security failure or something contrary to the spec; HIGH = a gap or error that would
make the implementation fail or come out wrong; MEDIUM = ambiguity or risk; LOW = style.
If there are no findings write "None."
End with [DONE]."""

FEEDBACK_SELECTED = ("The independent review (file {review}) raised findings and the user decided on them one by one. Fix ONLY the "
                     "findings listed under “Findings to fix”; the “accepted” ones were accepted as they are and you must not touch "
                     "them. Keep the tests passing.")
FEEDBACK_REVIEW = ("The independent review (file {review}) raised problems with the implementation. Read it and fix the CRITICAL and "
                   "HIGH ones first, then the rest where it makes sense, keeping the tests passing.")
FEEDBACK_PLAN = ("The independent plan validation (file {path}) blocked it. Read it and fix the plan: the CRITICAL and HIGH ones "
                 "first, then the rest where it makes sense. Keep the structure and the sections.")
USER_INSTRUCTIONS = "\nUser instructions: {comment}"
CHANGES_BLOCK = ("\n\n## Changes requested by the user\n{feedback}\n"
                 "The previous version is at {prev}; review it and write the new version to {path}.\n"
                 "If the user asked questions or has doubts, answer them directly in a section “## Answers to your doubts”, treat the "
                 "answered questions as decided, and leave in “## Open questions” only what is still missing (same format, with options).")
