"""REAL Claude Code and the vault curator (opt-in: `set CHIEF_REAL_TESTS=1`, then `python -m pytest tests/real -q -s -k curation`).

The curator is started INSIDE the vault, exactly as `chief.curator_command` builds it. The probe repository (`D:/_chief_probe/proj`, already
trusted by Claude Code) plays the vault: a draft about a project is put in its inbox and the test checks that the curator promotes it THERE
(the note changes, the draft goes, the log gets an entry) and does not work in a worktree or a copy, which is what happened when it was
started elsewhere with `--add-dir`. Everything the test adds is removed at the end."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import chief  # noqa: E402
from curation import Curator  # noqa: E402

VAULT = Path(os.environ.get("CHIEF_REAL_BASE", r"D:\_chief_probe")) / "proj"
pytestmark = [pytest.mark.real, pytest.mark.with_concept,
              pytest.mark.skipif(os.environ.get("CHIEF_REAL_TESTS") != "1", reason="real Claude Code: set CHIEF_REAL_TESTS=1")]
SCHEMA = {"readme": "README.md", "review_dir": "_review", "hub_dirs": ("Projects", "Areas"), "name": "English"}


def git(*a):
    return subprocess.run(["git", "-C", str(VAULT), *a], capture_output=True, text=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def vault():
    if not VAULT.is_dir():
        pytest.skip("the probe repository is missing")
    head = git("rev-parse", "HEAD")
    (VAULT / "inbox").mkdir(exist_ok=True)
    (VAULT / "wiki").mkdir(exist_ok=True)
    (VAULT / "Projects").mkdir(exist_ok=True)
    (VAULT / "CLAUDE.md").write_text("# Vault rules\n- Notes live in Projects/ (hubs) and wiki/.\n- Keep wiki/index.md, wiki/hot.md and wiki/log.md up to date (newest entry first).\n"
                                     "- Never write secrets.\n", encoding="utf-8")
    (VAULT / "Projects" / "Calc.md").write_text("---\ntype: project\nstatus: active\n---\n# Calc\nA tiny calculator.\n\n## State\n- v0\n", encoding="utf-8")
    for n, t in (("index", "# Index\n- [[Calc]]\n"), ("hot", "# Recent context\n"), ("log", "# Wiki Log\n\nNewest first.\n")):
        (VAULT / "wiki" / f"{n}.md").write_text(t, encoding="utf-8")
    (VAULT / "inbox" / "README.md").write_text("Drafts: `YYYY-MM-DD <project> - <subject>.md` with the frontmatter project/type/status: draft/date.\n", encoding="utf-8")
    (VAULT / "inbox" / "2026-11-24 Calc - division by zero.md").write_text(
        "---\nproject: \"[[Calc]]\"\ntype: decision\nstatus: draft\ndate: 2026-11-24\n---\nDecision: Calc.divide raises ValueError('division by zero') instead of "
        "returning None. Reason: callers silently ignored None. Done in v1.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(VAULT), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(VAULT), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "test vault"], check=True, capture_output=True)
    yield VAULT
    subprocess.run(["git", "-C", str(VAULT), "reset", "-q", "--hard", head], capture_output=True)
    subprocess.run(["git", "-C", str(VAULT), "clean", "-fdq"], capture_output=True)
    subprocess.run(["git", "-C", str(VAULT), "worktree", "prune"], capture_output=True)


def test_the_curator_started_in_the_vault_edits_the_vault_itself(vault, tmp_path):
    ids = []

    def launch(prompt):
        cmd, cwd = chief.curator_command(vault, prompt)
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=120)
        import re
        m = re.search(r"backgrounded\s*·\s*([0-9a-f]{6,})", r.stdout + r.stderr)
        assert m, r.stdout + r.stderr
        ids.append(m[1])
        return {"id": m[1], "name": "cos-curation"}

    def state(wid):
        return "finished" if not [p for p in (vault / "inbox").glob("2026-*.md")] else "working"

    c = Curator(vault, tmp_path / "curation.json", launch, state, chief.commit_worktree,
                lambda wid: subprocess.run(["claude", "stop", wid], capture_output=True, text=True, timeout=60), schema=SCHEMA)
    c.start(force=True)
    deadline = time.time() + 900
    while c.status()["running"] and time.time() < deadline:
        time.sleep(15)
        c.tick()
    st = c.status()
    print(st)
    assert not st["running"], "the curator did not finish in time"
    assert st["last"]["ok"] and st["last"]["handled"] >= 1, st["last"]
    assert "worktree" not in git("worktree", "list").lower().replace(str(vault).lower().replace("\\", "/"), "") or len(git("worktree", "list").splitlines()) == 1
    assert not (vault / ".claude" / "worktrees").exists(), "the curator worked in a worktree again"
    notes = "\n".join(p.read_text(encoding="utf-8") for p in list((vault / "Projects").rglob("*.md")) + list((vault / "wiki").rglob("*.md")))
    assert "division by zero" in notes.lower() or "valueerror" in notes.lower(), "the decision was not promoted into the vault"
    assert not list((vault / "inbox").glob("2026-*.md")) or (vault / "inbox" / "_review").exists()
    assert "2026-11-24" in (vault / "wiki" / "log.md").read_text(encoding="utf-8")
