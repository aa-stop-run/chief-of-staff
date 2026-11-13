"""Demo mode: sample data and a simulated Claude Code, so the dashboard can be explored (and photographed) safely.

`python app/server.py --demo` builds a throw-away folder with sample projects, a sample vault and sample requests at every stage
of the pipeline, points the Chief's data folder at it, and replaces everything that would talk to the real machine
(`claude`, the GitHub CLI, terminals, cloning) with harmless stand-ins. Nothing of the user's is read, and nothing is launched.

`seed()` only needs the standard library, so it can run before the `chief` module is imported (the data folder must be known
by then); `install()` is called afterwards with the already-imported modules.
"""
import json
import secrets
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import report
import usage

SESSION_NOW, SESSION_YESTERDAY, SESSION_BEFORE = "ws-demo-today", "ws-demo-yesterday", "ws-demo-before"
SCHEMA_VERSION = 3       # the schema the sample data is written in (kept equal to migrations.CURRENT by a test)
DEMO_PORT = 8790
NOTICE = "Demo mode: sample data and simulated workers. Nothing here runs a real worker or touches your projects."

# (folder, hub name, category, one-line description)
PROJECTS = [
    ("storefront", "Storefront", "product", "Online shop: catalogue, cart and checkout."),
    ("payments-api", "Payments API", "banking", "Service that talks to the payment provider and sends webhooks."),
    ("home-automation", "Home Automation", "home", "Scripts and dashboards for the house."),
    ("dotfiles", "Dotfiles", "tools", "Shell, editor and git configuration."),
    ("travel-journal", "Travel Journal", "personal", "A private journal of trips with photos and maps."),
    ("rd-lab", "R&D Lab #1", "tools", "Scratchpad for experiments; the name has symbols on purpose."),
]

FAKE_MCP_TOKEN = "sk-demo-0123456789abcdef"       # a made-up value: the project pages must never show it
ARCHITECTURE = {
    "storefront": "Three layers: a Flask web app (`web/`), a service layer (`services/`) and a PostgreSQL schema. Checkout talks to Payments API.",
    "payments-api": "A FastAPI service with a queue-backed webhook sender; every provider call goes through `provider.py`.",
    "rd-lab": "Single script per experiment under `experiments/`; results are written to `out/`.",
}
RICH = {                   # folders that get a lived-in tree: docs, tools and commands (all sample text)
    "storefront": {"docs": {"specs/two-factor-login.md": "# Two-factor login\n\nCustomers can protect their account with a TOTP app.\n",
                            "plans/checkout-rework.md": "# Checkout rework plan\n\n1. Split the cart total from the invoice total.\n2. Add tests.\n",
                            "analyses/cart-abandonment.md": "# Cart abandonment\n\nMost carts are dropped at the shipping step.\n"},
                   "skills": ["release-notes"], "scripts": {"test": "pytest -q", "build": "python -m build", "lint": "ruff check ."}},
    "payments-api": {"docs": {"specs/webhook-retry.md": "# Webhook retry\n\nRetry failed deliveries with exponential back-off.\n",
                              "plans/provider-migration.md": "# Provider migration\n\nMove to the v2 provider API in two steps.\n"},
                     "skills": ["incident-review"], "scripts": {"test": "pytest -q", "build": "docker build .", "lint": "ruff check ."}},
}

SPEC_2FA = """# Two-factor login (TOTP)

## Goal and why
Let customers protect their account with a time-based one-time password (TOTP) from any authenticator app. Account takeovers are
our most common support case.

## Out of scope
- SMS codes
- Hardware security keys (WebAuthn): a separate request

## Requirements
1. Customers can turn 2FA on from *Account → Security* by scanning a QR code and confirming one code.
2. Login asks for a code after the password when 2FA is on.
3. Single-use recovery codes are shown once, at enrolment.
4. 2FA can be turned off only after the password is entered again.

## Acceptance criteria
- A valid code logs the customer in; a wrong or reused code does not.
- Each recovery code works exactly once.
- Enrolment survives a page reload before the first code is confirmed.
- At most 5 wrong codes per 10 minutes per account.

## Risks
Clock drift between the server and the phone. Mitigation: accept one time step before and after the current one.

## Open questions
- **P1 — Recovery:** how should a customer get back in after losing their phone?
  - [ ] Ten single-use recovery codes (recommended) — simple and standard
  - [ ] Email a reset link — easier, but a weaker second factor
- **P2 — Staff accounts:** should 2FA be mandatory for staff?
  - [ ] Yes, enforce it for staff (recommended) — protects the admin panel
  - [ ] No, optional for everyone — less friction
"""
QUESTIONS_2FA = [
    {"id": "P1", "title": "Recovery", "text": "How should a customer get back in after losing their phone?",
     "options": [{"label": "Ten single-use recovery codes — simple and standard", "recommended": True},
                 {"label": "Email a reset link — easier, but a weaker second factor"}]},
    {"id": "P2", "title": "Staff accounts", "text": "Should 2FA be mandatory for staff?",
     "options": [{"label": "Yes, enforce it for staff — protects the admin panel", "recommended": True},
                 {"label": "No, optional for everyone — less friction"}]},
]
SPEC_WEBHOOKS = """# Retry failed webhooks

## Goal and why
Merchants miss payment events when their endpoint is down for a few minutes. Retry deliveries with back-off instead of dropping them.

## Requirements
1. A delivery that fails (timeout or a 5xx answer) is retried with exponential back-off.
2. Deliveries older than 24 hours are abandoned and reported to the merchant.
3. Every attempt is recorded and visible in the merchant dashboard.

## Acceptance criteria
- An endpoint that is down for 10 minutes receives every event once it is back.
- No event is delivered twice after a success.
- After 24 hours the delivery is marked *abandoned* and an email is sent.

## Open questions
None.
"""
PLAN_WEBHOOKS = """# Plan: retry failed webhooks

## Approach
Add a `webhook_attempts` table and a small worker that picks due attempts, delivers them and schedules the next one.

## Files to create/change
- `payments/webhooks/queue.py` (new): back-off schedule and the picking query
- `payments/webhooks/deliver.py`: record every attempt, mark success or abandon
- `migrations/0042_webhook_attempts.sql` (new)
- `tests/test_webhook_retry.py` (new)

## Tests
Full suite: `pytest -q` (212 tests today). One scenario per acceptance criterion, in `tests/test_webhook_retry.py`; baseline first, nothing that passed may fail.

## Ordered steps
1. Test: an endpoint that is down for 10 minutes receives the event afterwards (fails first).
2. Migration and queue module.
3. Delivery worker records attempts.
4. Delete attempt rows older than 90 days.
5. Test: no duplicate delivery after a success; test: abandoned after 24 h.

## Risks and rollback
The migration is additive; rollback is dropping the table.

## Open questions
None.
"""
PLAN_REVIEW_WEBHOOKS = """## Verdict
WITH RESERVATIONS — the plan covers the spec and the files exist, but one step is unsafe and one case has no test.

## Checks
- `git ls-files payments/webhooks` — `deliver.py` exists; `queue.py` does not (it is a new file, as the plan says).
- `grep -rn "webhook" tests | wc -l` — 14 existing tests, none cover retries.
- `pytest -q` — 212 passed, 0 failed (the baseline the plan relies on).

## Acceptance criteria coverage
| Criterion | Plan steps that meet it | Status |
|---|---|---|
| Down for 10 minutes, then delivered | 1, 2, 3 | covered |
| No duplicate delivery after a success | 3, 5 | covered |
| Abandoned after 24 hours, email sent | 3, 5 | partly: the email is not tested |

## Findings
- **[MEDIUM] Steps — step 4 deletes rows with no rollback:** The clean-up job removes data and cannot be undone; keep it behind a flag or move it to a separate request.
- **[LOW] Tests — the 24-hour email has no test:** Add a scenario that checks the email is sent when a delivery is abandoned.
"""
SPEC_CART = """# Cart total is off by one cent

## Symptom
A cart with three items priced 3.335 shows a total of 10.00 at checkout but the receipt says 10.01.

## Root cause
`src/cart.py:88` rounds each line with `round()` (banker's rounding) while `src/invoice.py:41` rounds the sum half-up.
Evidence: `python -c "from cart import total; print(total([3.335]*3))"` prints `10.0`; `invoice.total` prints `10.01`.

## Acceptance criteria
- The test that reproduces the bug fails before the fix and passes after.
- The full suite has no regressions.

## Open questions
None.
"""
REPORT_CART = """## Execution status
EXECUTED AND VERIFIED

**Verified by me**
- `pytest tests/test_cart.py::test_total_rounds_half_up` — failed before the change (`10.0 != 10.01`), passes now.
- `pytest -q` — 215 passed, 0 failed.

**Not verified**
- Behaviour with the payment provider's sandbox (needs credentials I do not have).

## Tests
NO REGRESSIONS
BASELINE: 212 passed, 0 failed
FINAL RESULT: 215 passed, 0 failed
NEW TESTS: 3

BUG TEST: tests/test_cart.py::test_total_rounds_half_up

| Acceptance criterion | Test |
|---|---|
| Bug reproduced, then fixed | tests/test_cart.py::test_total_rounds_half_up |
| Rounding is half-up for every line | tests/test_cart.py::test_each_line_rounds_half_up |
| Cart and invoice agree | tests/test_cart.py::test_cart_total_equals_invoice_total |

## What changed
`src/cart.py`: one shared `round_money()` (half-up), used by the cart and the invoice.
"""
QA_CART_ANSWER = """It does not: the fix uses the receipt's own rule, round half to even, in one place.

## Evidence
- `cart/totals.py:42` now calls `receipt.round_cents()`, the function the receipt already used; before, the checkout rounded each line and the receipt rounded the sum.
- `pytest tests/test_totals.py -q` → 14 passed, including `test_total_matches_receipt` (it failed before the fix).

## What I could not confirm
- Whether the payment provider rounds the same way on its side; that needs a real sandbox payment.
"""

REVIEW_CART = """## Verdict
WITH RESERVATIONS — the fix is correct and tested; two small things to tidy.

## Checks
- `git diff HEAD` — 2 files changed, 41 lines added, 9 removed.
- `pytest -q` in the worktree — 215 passed, 0 failed.

## Tests
CONFIRMED
REVIEWER RESULT: 215 passed, 0 failed
Command: `pytest -q`

## Acceptance criteria
| Criterion | Status | Evidence |
|---|---|---|
| Bug test fails before, passes after | met | report shows both runs; I re-ran it |
| No regressions | met | 215 passed, 0 failed |

## Findings
- **[HIGH] src/cart.py:88 — rounding is still half-even for refunds:** `refund_total()` keeps the old `round()`, so a refund of a 3.335 line is one cent short; use `round_money()` there too.
- **[MEDIUM] src/invoice.py:41 — duplicated rounding helper:** The old helper is still copied in `invoice.py`; delete it and import `round_money`.
- **[LOW] tests/test_cart.py:12 — magic number:** Name the 3.335 price in a constant so the intent is clear.
"""


MARKER = ".chief-demo"


def prepare_dir(path) -> Path:
    """The folder the sample world is built in. An existing folder is only reused (and later deleted) if it is empty or was made
    by a previous demo, so a typo in CHIEF_DEMO_DIR can never point the demo at somebody's real files."""
    path = Path(path)
    if path.exists() and any(path.iterdir()) and not (path / MARKER).exists():
        raise SystemExit(f"{path} exists and is not a Chief demo folder: choose another CHIEF_DEMO_DIR")
    if path.exists():
        for child in path.iterdir():
            _remove(child)
    path.mkdir(parents=True, exist_ok=True)
    (path / MARKER).write_text("created by `python app/server.py --demo`; safe to delete\n", encoding="utf-8")
    return path


def _remove(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)


def _usage_rows(rows) -> dict:
    """Token rows -> the per-model entry of the cost ledger, priced with the real price table."""
    out = {}
    for model, n, inp, o, read, w1 in rows:
        per = {"input_tokens": inp // n, "output_tokens": o // n, "cache_read_input_tokens": read // n,
               "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": w1 // n}}
        c = usage.message_cost(model, per)
        out[model] = {"messages": n, "input": inp // n * n, "output": o // n * n, "cache_write_5m": 0, "cache_write_1h": w1 // n * n,
                      "cache_read": read // n * n, "cost_usd": round((c or 0) * n, 6), "unpriced_messages": n if c is None else 0}
    return out


def _iso(now: datetime, minutes_ago: float) -> str:
    return (now - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _event(now, minutes_ago, event, stage, note=""):
    return {"at": _iso(now, minutes_ago), "event": event, "stage": stage, "note": note}


def _feature(now, fid, title, project, desc, kind, stage, status, history, rev=None, pv=None, worker="", model="", created_min=600):
    f = {"id": fid, "title": title, "desc": desc, "project": project, "model": model, "lang": "en", "kind": kind, "stage": stage,
         "status": status, "worker_id": worker, "worker_name": f"cos-{worker}" if worker else "", "created": _iso(now, created_min),
         "history": history, "rev": {"spec": 0, "plan": 0, "build": 0, "review": 0, **(rev or {})}, "auto": {"fixes": 0, "retries": 0}}
    if pv is not None:
        f["pv"] = pv
    return f


def _write(path: Path, text) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if isinstance(text, str) else json.dumps(text, indent=1), encoding="utf-8", newline="\n")


def _git(cwd: Path, *args) -> None:
    subprocess.run(["git", "-c", "user.name=Demo", "-c", "user.email=demo@example.com", *args], cwd=str(cwd),
                   capture_output=True, timeout=30, check=False)


def _architecture(folder: str) -> str:
    return f"\n## Architecture\n\n{ARCHITECTURE[folder]}\n" if folder in ARCHITECTURE else ""


def _enrich(d: Path, folder: str) -> None:
    """Docs, a package.json, an MCP configuration (with a made-up token) and a skill, before the first commit."""
    rich = RICH.get(folder)
    if not rich:
        return
    for rel, text in rich["docs"].items():
        _write(d / "docs" / rel, text)
    _write(d / "package.json", {"name": folder, "version": "0.1.0", "scripts": rich["scripts"]})
    _write(d / ".mcp.json", {"mcpServers": {"issue-tracker": {"command": "npx", "args": ["-y", "demo-tracker-mcp"],
                                                              "env": {"TRACKER_TOKEN": FAKE_MCP_TOKEN}}}})
    for skill in rich["skills"]:
        _write(d / ".claude" / "skills" / skill / "SKILL.md", f"---\nname: {skill}\ndescription: Sample skill.\n---\n# {skill}\n")


def _make_projects(base: Path) -> dict:
    paths = {}
    for folder, hub, domain, blurb in PROJECTS:
        d = base / "projects" / folder
        _write(d / "README.md", f"# {hub}\n\n{blurb}\n{_architecture(folder)}")
        _enrich(d, folder)
        _git(d, "init", "-q", "-b", "main")
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "Initial commit")
        _git(d, "remote", "add", "origin", f"https://github.com/acme/{folder}.git")
        paths[folder] = d
    return paths


def _make_vault(base: Path, paths: dict) -> Path:
    v = base / "vault"
    _write(v / "00-Index.md", "# Index\n\nSample vault for the demo.\n")
    _write(v / "wiki" / "hot.md", "# Recent context\n\n- Storefront: two-factor login is being specified.\n")
    _write(v / "inbox" / "README.md", "# Inbox\n\nWorkers leave draft notes here; the curator promotes them.\n")
    for folder, hub, domain, blurb in PROJECTS:
        _write(v / "Projects" / f"{hub}.md",
               f'---\nlocal: "{paths[folder].as_posix()}"\ndomain: {domain}\n---\n# {hub}\n\n{blurb}\n{_architecture(folder)}')
    return v


def seed(base: Path, now: datetime | None = None) -> Path:
    """Write the whole sample world under `base` and return the data folder (`base/home`) the Chief should use."""
    base = Path(base)
    now = now or datetime.now()
    paths = _make_projects(base)
    vault = _make_vault(base, paths)
    home = base / "home"
    state = home / "state"
    _write(home / "projects.json", {
        "vault": vault.as_posix(), "vault_language": "en", "roots": [(base / "projects").as_posix()], "extras": [], "ignore": [],
        "hide_domains": [], "dashboard_port": DEMO_PORT, "domains": {}, "sensitive_paths": [], "models": ["opus", "sonnet", "haiku"]})
    _write(home / "schema.json", {"schema_version": SCHEMA_VERSION})

    def art(fid, name, text):
        _write(state / "features" / fid / name, text)

    ev = lambda m, e, s, n="": _event(now, m, e, s, n)  # noqa: E731

    # 1. a spec waiting for decisions (open questions)
    f1 = "two-factor-login-totp-demo01"
    art(f1, "spec.md", SPEC_2FA)
    art(f1, "questions.json", QUESTIONS_2FA)
    feat1 = _feature(now, f1, "Two-factor login (TOTP)", "Storefront", "Let customers turn on 2FA with an authenticator app.",
                     "feature", "spec", "awaiting", [ev(52, "worker launched", "spec", "cos-demo001"), ev(41, "finished", "spec")],
                     worker="demo001", created_min=55)
    # 2. a plan that an independent validator has already checked
    f2 = "retry-failed-webhooks-demo02"
    art(f2, "spec.md", SPEC_WEBHOOKS)
    art(f2, "plan.md", PLAN_WEBHOOKS)
    art(f2, "plan-review.md", PLAN_REVIEW_WEBHOOKS)
    feat2 = _feature(now, f2, "Retry failed webhooks", "Payments API", "Retry webhook deliveries with exponential back-off.",
                     "feature", "plan", "awaiting",
                     [ev(1500, "worker launched", "spec", "cos-demo002"), ev(1480, "finished", "spec"), ev(1470, "approved", "spec"),     # yesterday's session
                      ev(1469, "spec archived", "spec", "copied to docs/specs/retry-failed-webhooks.md and a draft in the vault"),
                      ev(268, "worker launched", "plan", "cos-demo003"), ev(240, "finished", "plan"),
                      ev(239, "plan validation", "plan", "cos-demo004"), ev(225, "plan validated", "plan", "with reservations")],
                     rev={"spec": 0}, pv={"checking": False, "fixes": 0, "retries": 0, "rev": 0}, worker="demo004", model="sonnet",
                     created_min=1505)
    # 3. an implementation that passed its independent review (bug fix)
    f3 = "cart-total-is-off-by-one-cent-demo03"
    art(f3, "spec.md", SPEC_CART)
    art(f3, "plan.md", "# Plan\n\n## Tests\nFull suite: `pytest -q`. First step: the test that reproduces the bug.\n")
    art(f3, "report.md", REPORT_CART)
    art(f3, "review.md", REVIEW_CART)
    feat3 = _feature(now, f3, "Cart total is off by one cent", "Storefront", "The checkout total and the receipt differ by one cent.",
                     "fix", "review", "awaiting",
                     [ev(180, "worker launched", "spec", "cos-demo005"), ev(165, "finished", "spec"), ev(160, "approved", "spec"),
                      ev(159, "worker launched", "plan"), ev(150, "finished", "plan"), ev(149, "plan validation", "plan"),
                      ev(140, "plan validated", "plan", "approved"), ev(138, "approved", "plan"), ev(137, "worker launched", "build"),
                      ev(95, "finished", "build"), ev(94, "automatic review", "review"), ev(75, "finished", "review"),
                      ev(74, "committed", "review", "3f9a1c2")],
                     rev={"build": 0}, pv={"checking": False, "fixes": 0, "retries": 0, "rev": 0}, worker="demo006", created_min=185)
    art(f3, "qa/1.md", QA_CART_ANSWER)      # a question the user asked about the conclusions, already answered
    feat3["qa"] = [{"n": 1, "q": "Why does the fix round half-cents up instead of using the receipt's rule?", "at": _iso(now, 50),
                    "worker_id": "demo009", "worker_name": "cart-total-is-off-by-one-q1", "status": "answered"}]
    # 4. an analysis that is being written right now
    f4 = "why-is-the-nightly-sync-slow-demo04"
    feat4 = _feature(now, f4, "Why is the nightly sync slow?", "Home Automation",
                     "The nightly sync of the sensor data takes 40 minutes. Find out why.", "analysis", "build", "working",
                     [ev(7, "worker launched", "build", "cos-demo007")], worker="demo007", created_min=8)
    # 5. an implementation in progress
    f5 = "csv-export-of-trips-demo05"
    art(f5, "spec.md", "# CSV export of trips\n\n## Goal and why\nExport all trips to CSV so they can be opened in a spreadsheet.\n")
    art(f5, "plan.md", "# Plan\n\n## Tests\nFull suite: `pytest -q`.\n")
    feat5 = _feature(now, f5, "CSV export of trips", "Travel Journal", "Export all trips to a CSV file.", "feature", "build", "working",
                     [ev(120, "worker launched", "spec"), ev(110, "finished", "spec"), ev(108, "approved", "spec"),
                      ev(107, "worker launched", "plan"), ev(98, "finished", "plan"), ev(97, "plan validation", "plan"),
                      ev(90, "plan validated", "plan", "approved"), ev(88, "approved", "plan"),
                      ev(22, "worker launched", "build", "cos-demo008")], worker="demo008", model="opus", created_min=125)
    # 6-8. finished requests: one closed in this session, the other two in earlier sessions
    done = []
    for fid, title, proj, kind, stages, base, sess in (
            ("weekly-digest-email-demo07", "Weekly digest email", "Storefront", "feature", ("spec", "plan", "build", "review"), 300, SESSION_NOW),
            ("upgrade-pytest-to-9-demo06", "Upgrade pytest to 9", "Dotfiles", "maintenance", ("plan", "build", "review"), 1650, SESSION_YESTERDAY),
            ("fix-flaky-login-test-demo08", "Fix the flaky login test", "Storefront", "fix", ("spec", "plan", "build", "review"), 2900, SESSION_BEFORE)):
        hist = []
        for i, st in enumerate(stages):
            hist += [ev(base - i * 60, "worker launched", st), ev(base - i * 60 - 10, "finished", st), ev(base - i * 60 - 15, "approved", st)]
        for st, name in (("spec", "spec.md"), ("plan", "plan.md"), ("build", "report.md")):
            if st in stages:
                art(fid, name, f"# {title}\n\nDone.\n")
        art(fid, "review.md", "## Verdict\nAPPROVED — all good.\n\n## Tests\nCONFIRMED\nREVIEWER RESULT: 120 passed, 0 failed\n\n## Findings\nNone.\n")
        f = _feature(now, fid, title, proj, title, kind, "review", "done", hist, worker="", created_min=base + 5)
        f["closed"] = {"at": hist[-1]["at"], "session": sess}
        done.append(f)
    _write(state / "features.json", [feat1, feat2, feat3, feat4, feat5, *done])
    import migrations
    migrations.to_schema_3(state)          # the sample requests carry their pipeline like any request of the current schema

    # workers and what each cost: (id, project, folder, request, phase, started N minutes ago, session, model, task, usage per model)
    # usage rows are (model, messages, input, output, cache read, 1-hour cache write)
    ids = {"f1": f1, "f2": f2, "f3": f3, "f4": f4, "f5": f5, "d1": done[0]["id"], "d2": done[1]["id"], "d3": done[2]["id"], "": ""}
    SON, OPU, HAI = "claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"
    table = [
        ("demo001", "Storefront", "storefront", "f1", "spec", 52, SESSION_NOW, "default", "Feature «Two-factor login (TOTP)»: write the spec", [(SON, 14, 200, 12000, 600000, 60000)]),
        ("demo002", "Payments API", "payments-api", "f2", "spec", 1500, SESSION_YESTERDAY, "sonnet", "Feature «Retry failed webhooks»: write the spec", [(SON, 12, 180, 9000, 500000, 50000)]),
        ("demo003", "Payments API", "payments-api", "f2", "plan", 268, SESSION_NOW, "sonnet", "Feature «Retry failed webhooks»: write the plan", [(SON, 20, 300, 16000, 900000, 80000)]),
        ("demo004", "Payments API", "payments-api", "f2", "plan-validation", 239, SESSION_NOW, "sonnet", "Independent validation of the plan", [(HAI, 16, 400, 5000, 700000, 40000)]),
        ("demo005", "Storefront", "storefront", "f3", "spec", 180, SESSION_NOW, "default", "Fix «Cart total is off by one cent»: diagnosis", [(SON, 18, 250, 11000, 800000, 70000)]),
        ("demo009", "Storefront", "storefront", "f3", "plan", 159, SESSION_NOW, "default", "Fix «Cart total is off by one cent»: plan", [(SON, 14, 220, 8000, 650000, 55000)]),
        ("demo010", "Storefront", "storefront", "f3", "plan-validation", 149, SESSION_NOW, "default", "Independent validation of the plan", [(HAI, 12, 300, 4000, 500000, 30000)]),
        ("demo011", "Storefront", "storefront", "f3", "build", 137, SESSION_NOW, "opus", "Fix «Cart total is off by one cent»: implementation",
         [(OPU, 60, 500, 70000, 6000000, 350000), (SON, 10, 120, 5000, 300000, 20000)]),
        ("demo006", "Storefront", "storefront", "f3", "review", 94, SESSION_NOW, "sonnet", "Independent review of the cart-total fix", [(SON, 30, 400, 20000, 1500000, 90000)]),
        ("demo007", "Home Automation", "home-automation", "f4", "build", 7, SESSION_NOW, "default", "Why is the nightly sync slow?", [(SON, 6, 90, 3000, 150000, 20000), (HAI, 4, 60, 900, 80000, 5000)]),
        ("demo012", "Travel Journal", "travel-journal", "f5", "spec", 120, SESSION_NOW, "opus", "Feature «CSV export of trips»: write the spec", [(OPU, 10, 150, 8000, 400000, 40000)]),
        ("demo013", "Travel Journal", "travel-journal", "f5", "plan", 107, SESSION_NOW, "opus", "Feature «CSV export of trips»: write the plan", [(OPU, 14, 200, 12000, 700000, 60000)]),
        ("demo014", "Travel Journal", "travel-journal", "f5", "plan-validation", 97, SESSION_NOW, "opus", "Independent validation of the plan", [(SON, 10, 180, 4000, 400000, 30000)]),
        ("demo008", "Travel Journal", "travel-journal", "f5", "build", 22, SESSION_NOW, "opus", "Implement the CSV export in its own worktree", [(OPU, 34, 300, 40000, 3200000, 200000)]),
        ("demo015", "Storefront", "storefront", "d1", "spec", 300, SESSION_NOW, "sonnet", "Feature «Weekly digest email»: write the spec", [(SON, 12, 150, 9000, 450000, 50000)]),
        ("demo016", "Storefront", "storefront", "d1", "plan", 240, SESSION_NOW, "sonnet", "Feature «Weekly digest email»: write the plan", [(SON, 15, 200, 11000, 600000, 60000)]),
        ("demo017", "Storefront", "storefront", "d1", "build", 180, SESSION_NOW, "sonnet", "Feature «Weekly digest email»: implementation", [(SON, 40, 350, 30000, 2800000, 150000)]),
        ("demo018", "Storefront", "storefront", "d1", "review", 120, SESSION_NOW, "sonnet", "Independent review of the weekly digest", [(SON, 22, 300, 14000, 1100000, 80000)]),
        ("demo020", "Dotfiles", "dotfiles", "", "", 200, SESSION_NOW, "local/qwen3-coder", "Try a local model on the shell aliases", [("local/qwen3-coder", 25, 3000, 20000, 0, 0)]),
        ("demo101", "Dotfiles", "dotfiles", "d2", "plan", 1650, SESSION_YESTERDAY, "default", "Maintenance «Upgrade pytest to 9»: plan", [(SON, 10, 120, 6000, 300000, 30000)]),
        ("demo102", "Dotfiles", "dotfiles", "d2", "build", 1590, SESSION_YESTERDAY, "default", "Maintenance «Upgrade pytest to 9»: execution", [(SON, 28, 260, 18000, 1600000, 90000)]),
        ("demo103", "Dotfiles", "dotfiles", "d2", "review", 1530, SESSION_YESTERDAY, "default", "Independent review", [(SON, 16, 200, 9000, 800000, 50000)]),
        ("demo104", "Payments API", "payments-api", "", "", 1600, SESSION_YESTERDAY, "opus", "Look at the failing webhook signature test", [(OPU, 22, 260, 26000, 1900000, 120000)]),
        ("demo105", "Storefront", "storefront", "d3", "spec", 2900, SESSION_BEFORE, "default", "Fix «Fix the flaky login test»: diagnosis", [(SON, 16, 220, 10000, 700000, 60000)]),
        ("demo106", "Storefront", "storefront", "d3", "build", 2780, SESSION_BEFORE, "opus", "Fix «Fix the flaky login test»: implementation", [(OPU, 44, 380, 52000, 4200000, 260000)]),
        ("demo107", "Storefront", "storefront", "d3", "review", 2740, SESSION_BEFORE, "sonnet", "Independent review", [(SON, 20, 260, 12000, 1000000, 70000)]),
        ("demo109", "Home Automation", "home-automation", "", "", 2850, SESSION_BEFORE, "default", "Check why the sensors drop off the network", [(SON, 18, 240, 9000, 900000, 65000)]),
    ]
    workers, ledger = [], {}
    for wid, proj, folder, key, phase, mins, sess, model, task, rows in table:
        workers.append({"name": f"{phase or 'task'}-{wid[-3:]}", "id": wid, "project": proj, "cwd": paths[folder].as_posix(), "task": task,
                        "started": _iso(now, mins), "model": model, "session": sess, "request": ids[key], "phase": phase})
        ledger[wid] = {"project": proj, "session": sess, "request": ids[key], "phase": phase, "name": workers[-1]["name"],
                       "models": _usage_rows(rows), "first_at": _iso(now, mins), "last_at": _iso(now, max(mins - 15, 0)), "size": 0, "mtime": 0, "sig": ""}
    _write(state / "workers.json", workers)
    _write(state / "usage.json", {"version": 1, "workers": ledger})

    # work sessions: this one is open (its server is "up" now); the two before it ended, with their totals frozen
    def frozen(sid):
        return report.totals_now(ledger, sid)

    _write(state / "work_sessions.json", {"sessions": [
        {"id": SESSION_BEFORE, "started": _iso(now, 3000), "last_seen": _iso(now, 2700), "ended": _iso(now, 2700), "ended_by": "user",
         "active_s": 12900, "totals": frozen(SESSION_BEFORE)},
        {"id": SESSION_YESTERDAY, "started": _iso(now, 1720), "last_seen": _iso(now, 1440), "ended": _iso(now, 1440), "ended_by": "idle",
         "active_s": 14100, "totals": frozen(SESSION_YESTERDAY)},
        {"id": SESSION_NOW, "started": _iso(now, 330), "last_seen": _iso(now, 0), "ended": None, "ended_by": None, "active_s": 19000, "totals": None}]})
    _write(state / "plan_usage.json", {"updated": _iso(now, 1),
        "five_hour": {"used_percentage": 62.0, "resets_at": int((now + timedelta(minutes=95)).timestamp())},
        "seven_day": {"used_percentage": 38.0, "resets_at": int((now + timedelta(days=3, hours=5)).timestamp())}})
    _write(state / "pending.json", [
        {"id": "p-demo000001", "project": "Home Automation", "title": "Reserve IP addresses on the router", "status": "blocked",
         "summary": "The router password is missing, so the reservations could not be saved.",
         "missing": "Log in to the router and save the reservations for the three sensors.", "session": "a8b92eaf-0000-0000-0000-000000000001",
         "date": now.strftime("%Y-%m-%d"), "created": _iso(now, 90), "dismissed": False},
        {"id": "p-demo000002", "project": "Dotfiles", "title": "Move the shell aliases into their own file", "status": "awaiting",
         "summary": "The aliases are split; the new file is written but not sourced yet.",
         "missing": "Source aliases.zsh from .zshrc and delete the old block.", "session": "c41d77aa-0000-0000-0000-000000000002",
         "date": now.strftime("%Y-%m-%d"), "created": _iso(now, 200), "dismissed": False},
        {"id": "p-demo000003", "project": "Payments API", "title": "Rotate the sandbox API key", "status": "optional",
         "summary": "Not urgent: the key expires in six weeks.", "missing": "Create a new key in the provider dashboard and update the secret store.",
         "session": "f09e12bb-0000-0000-0000-000000000003", "date": now.strftime("%Y-%m-%d"), "created": _iso(now, 400), "dismissed": False},
        {"id": "p-demo000004", "project": "Travel Journal", "title": "Compress the photo thumbnails", "status": "running",
         "summary": "A script is compressing 2,300 thumbnails in the background.", "missing": "Check the result and delete the originals if it looks right.",
         "session": "7be3c0de-0000-0000-0000-000000000004", "date": now.strftime("%Y-%m-%d"), "created": _iso(now, 30), "dismissed": False},
    ])
    return home


# ---------------------------------------------------------------- the simulated machine
PATCHED_CHIEF = ("worker_limit", "sessions_checked", "launch_worker", "stop_worker", "commit_worktree", "open_session", "launch_curator", "clone_project")
PATCHED_SETTINGS = ("github_status", "plan_connection", "connect_plan", "disconnect_plan")


class Runtime:
    """What replaces `claude` and friends in demo mode. Live sessions are just a dict; nothing real is ever executed."""

    def __init__(self, chief, live=None):
        self.chief = chief
        self.live = dict(live or {})        # session id -> claude state
        self.plan_on = True
        self.limited = {}                   # session id -> usage-limit notice (the demo's "hit a limit" switch)

    def worker_limit(self, worker_id):
        return self.limited.get(worker_id)

    def set_limit(self, worker_id, on, auto=False, minutes=90):
        if on:
            at = datetime.now() + timedelta(minutes=minutes)
            self.limited[worker_id] = {"kind": "session", "auto": auto, "resume_at": at.isoformat(timespec="seconds"), "text": "Usage limit reached"}
        else:
            self.limited.pop(worker_id, None)
        return self.limited.get(worker_id)

    def sessions_checked(self):
        return [{"id": i, "state": s, "kind": "background", "name": i} for i, s in self.live.items()]

    def launch_worker(self, project, task, model=None, extra_dirs=(), mode="auto", label=None, effort=None, allow=(), deny=()):
        p = self.chief.find(project, self.chief.load_projects())
        if not task.strip():
            raise ValueError("empty task")
        wid = "demo" + secrets.token_hex(3)
        name = f"{(label or 'cos-' + p['slug'])[:40].strip('-')}-{wid[-3:]}"
        self.live[wid] = "working"            # a demo worker never finishes by itself
        w = {"name": name, "id": wid, "project": p["name"], "cwd": p["path"], "task": task.strip()[:2000],
             "started": datetime.now().isoformat(timespec="seconds"), "model": model or "default",
             "session": self.chief.work_sessions().current_id()}
        if label and label.startswith("extraction-"):
            w["phase"] = "extraction"
        self.chief.record_worker(w)             # the very same record the real launcher writes
        return w

    def stop_worker(self, worker_id):
        self.live.pop(worker_id, None)
        return True

    @staticmethod
    def commit_worktree(path, message):
        return True, "demo000"

    @staticmethod
    def open_session(project, session_id):
        raise RuntimeError("resuming a session is disabled in the demo")

    @staticmethod
    def launch_curator(prompt):
        raise RuntimeError("the vault curation is disabled in the demo")

    @staticmethod
    def clone_project(raw, domain=None):
        raise ValueError("cloning is disabled in the demo")

    def plan_connection(self):                 # the demo never reads or writes the real Claude Code settings
        return "connected" if self.plan_on else "none"

    def connect_plan(self):
        self.plan_on = True
        return {"connection": "connected"}

    def disconnect_plan(self):
        self.plan_on = False
        return {"connection": "none"}

    @staticmethod
    def github_status():
        return {"gh": True, "logged_in": True, "account": "demo-user", "helpers": ["manager"]}


def install(chief, settings) -> Runtime:
    """Swap the real machine for the simulation. Call it before anything binds `chief.launch_worker` & co."""
    live = {w["id"]: "working" for w in json.loads((Path(chief.STATE) / "workers.json").read_text(encoding="utf-8"))
            if w["id"] in ("demo007", "demo008")} if (Path(chief.STATE) / "workers.json").exists() else {}
    rt = Runtime(chief, live)
    for name in PATCHED_CHIEF:
        setattr(chief, name, getattr(rt, name))
    for name in PATCHED_SETTINGS:
        setattr(settings, name, getattr(rt, name))
    return rt
