"""After a request ends: what is left in git, and cleaning it up.

An implementation lives in its own `git worktree` on the branch `feat/<request id>`. The pipeline commits there and never merges:
the merge is the user's. This module answers what the dashboard needs afterwards, with read-only git commands (was the branch
merged? how big is the change?), and removes the worktree and the branch when the user asks for it.

Removing is conservative by default: git itself refuses a worktree with uncommitted changes and a branch that is not merged.
`force` (the user's explicit choice for work they are throwing away) lifts both refusals. Nothing here pushes or merges.
"""
import re
import subprocess
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BRANCH_RE = re.compile(r"^feat/[a-z0-9][a-z0-9-]{0,80}$")


def branch_of(request_id: str) -> str:
    return f"feat/{request_id}"


def _git(cwd, *args, timeout: int = 30) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None


def _out(r) -> str:
    return r.stdout.strip() if r is not None and r.returncode == 0 else ""


def status(repo, work, branch: str) -> dict:
    """What a request left behind: {branch, exists, merged, into, worktree, dirty}.

    `merged`: every commit of the branch is in the repo's current branch (`into`). A branch nobody committed to is «merged»
    for git too, so the caller only shows it for a request the pipeline did commit. `dirty`: uncommitted changes in the worktree."""
    out = {"branch": branch, "exists": False, "merged": False, "into": "", "worktree": Path(work).is_dir(), "dirty": 0}
    if not BRANCH_RE.match(branch) or not Path(repo).is_dir():
        return out
    out["exists"] = bool(_out(_git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")))
    out["into"] = _out(_git(repo, "branch", "--show-current"))
    if out["exists"] and out["into"] and out["into"] != branch:
        r = _git(repo, "merge-base", "--is-ancestor", branch, "HEAD")
        out["merged"] = r is not None and r.returncode == 0
    if out["worktree"]:
        out["dirty"] = len([ln for ln in _out(_git(work, "--no-optional-locks", "status", "--porcelain")).splitlines() if ln.strip()])
    return out


def merge_commands(repo, branch: str, into: str = "") -> list:
    """The commands the user runs to bring the work into their branch: shown on the card, never run by the Chief."""
    folder = str(repo).replace("\\", "/")
    return [f'git -C "{folder}" switch {into}' if into else f'cd "{folder}"', f'git -C "{folder}" merge --no-ff {branch}']


def _fork_point(work) -> str:
    """The commit a request's branch started from: where it meets what the project's own checkout has. '' if it cannot be told
    (not a linked worktree) or if the branch has nothing of its own there (never committed, or already merged)."""
    listing = _out(_git(work, "worktree", "list", "--porcelain"))
    main_head = next((ln.split(" ", 1)[1] for ln in listing.splitlines() if ln.startswith("HEAD ")), "")   # the first block is the main checkout
    head = _out(_git(work, "rev-parse", "HEAD"))
    base = _out(_git(work, "merge-base", "HEAD", main_head)) if main_head and head and main_head != head else ""
    return base if base and base != head else ""


def diff_summary(work) -> dict | None:
    """How big a request's change is: {files, insertions, deletions, untracked, committed}. None if it cannot be read.

    Everything the branch adds to the commit it started from: all its commits, the uncommitted work on top, and new files git
    does not track yet. `committed` says the worktree is clean. When the starting commit cannot be told, the uncommitted work
    alone, or the last commit when there is none. Only numbers: no file names, no content."""
    if not Path(work).is_dir():
        return None
    porcelain = _git(work, "--no-optional-locks", "status", "--porcelain", "-uall")
    if porcelain is None or porcelain.returncode != 0:
        return None
    lines = [ln for ln in porcelain.stdout.splitlines() if ln.strip()]
    untracked = sum(1 for ln in lines if ln.startswith("??"))
    committed = not lines
    base = _fork_point(work)
    if base:
        stat = _out(_git(work, "diff", "--shortstat", base))
    else:
        stat = _out(_git(work, "show", "--shortstat", "--format=", "HEAD") if committed else _git(work, "diff", "--shortstat", "HEAD"))
    num = lambda word: int(m.group(1)) if (m := re.search(rf"(\d+) {word}", stat)) else 0   # noqa: E731
    return {"files": num("files? changed") + untracked, "insertions": num(r"insertions?\(\+\)"), "deletions": num(r"deletions?\(-\)"),
            "untracked": untracked, "committed": committed}


def changed_files(work) -> list:
    """The names of the files a request's branch changed (the same span as `diff_summary`, new files included). [] if unreadable.
    Names stay on the server: the page only ever gets which of the user's patterns matched."""
    if not Path(work).is_dir():
        return []
    porcelain = _git(work, "--no-optional-locks", "status", "--porcelain", "-uall")
    if porcelain is None or porcelain.returncode != 0:
        return []
    names = {ln[3:].strip().strip('"') for ln in porcelain.stdout.splitlines() if ln.startswith("??")}
    base = _fork_point(work)
    r = _git(work, "diff", "--name-only", base) if base else _git(work, "diff", "--name-only", "HEAD")
    if r is not None and r.returncode == 0:
        names |= {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    if not base and not names:
        r = _git(work, "show", "--name-only", "--format=", "HEAD")
        names |= {ln.strip() for ln in (r.stdout.splitlines() if r is not None and r.returncode == 0 else []) if ln.strip()}
    return sorted(names)


def remove(repo, work, branch: str, force: bool = False) -> tuple:
    """Remove a request's worktree and its branch. Returns (ok, what happened).

    Without `force` git refuses a worktree with uncommitted changes and a branch that is not merged, and that refusal is the
    answer. With `force` both are removed whatever their state. A branch that is checked out elsewhere is never deleted."""
    if not BRANCH_RE.match(branch):
        return False, "not a request branch"
    repo, work = Path(repo), Path(work)
    if not repo.is_dir():
        return False, "the project folder does not exist"
    done = []
    if work.is_dir():
        r = _git(repo, "worktree", "remove", *(["--force"] if force else []), str(work), timeout=120)
        if r is None or r.returncode != 0:
            why = ((r.stderr or r.stdout).strip().splitlines() or ["git worktree remove failed"])[-1] if r is not None else "git could not be run"
            return False, f"the worktree was not removed: {why[:200]}"
        done.append("worktree removed")
    _git(repo, "worktree", "prune")
    if _out(_git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")):
        r = _git(repo, "branch", "-D" if force else "-d", branch)
        if r is None or r.returncode != 0:
            why = ((r.stderr or r.stdout).strip().splitlines() or ["git branch -d failed"])[0] if r is not None else "git could not be run"
            return bool(done), "; ".join(done + [f"the branch {branch} was kept: {why[:200]}"])
        done.append(f"branch {branch} deleted")
    return True, "; ".join(done) or "nothing to remove"
