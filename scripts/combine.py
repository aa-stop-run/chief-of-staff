"""Merge the parts' branches into a throw-away worktree, so one integration request can test them together.

The server does every git write here; the integration worker only reads and runs the suite. The project's own
checkout, its branches and its git config are never touched: the work happens in a detached worktree at `dest`.
"""
import shutil
import subprocess
from pathlib import Path

TIMEOUT_S = 120
_IDENTITY = ["-c", "user.name=Chief", "-c", "user.email=chief@localhost"]


class CombineError(Exception):
    pass


def _git(cwd: str, *args: str, timeout: int = TIMEOUT_S) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CombineError(f"git {args[0] if args else ''} failed: {exc}") from exc


def _must(cwd: str, *args: str) -> str:
    r = _git(cwd, *args)
    if r.returncode != 0:
        raise CombineError((r.stderr or r.stdout or f"git {args[0]} failed").strip())
    return r.stdout.strip()


def remove(repo: str, dest: str) -> None:
    """Delete the integration worktree and its registration. Safe to call twice; never raises."""
    try:
        _git(repo, "worktree", "remove", "--force", str(dest))
    except CombineError:
        pass
    shutil.rmtree(dest, ignore_errors=True)
    try:
        _git(repo, "worktree", "prune")
    except CombineError:
        pass


def run(repo: str, branches: list, dest: str) -> dict:
    """Create `dest` as a detached worktree of `repo`'s current HEAD and merge each branch into it, in order.

    Every successful merge is committed, so the next one can run. A branch that conflicts is aborted and listed
    with its conflicting files; a branch that does not exist is listed as missing; the rest are still merged.
    Returns {"base", "head", "merged", "conflicts", "missing"}."""
    if Path(dest).exists():
        remove(repo, dest)
    base = _must(repo, "rev-parse", "HEAD")
    _must(repo, "worktree", "add", "--detach", str(dest), "HEAD")
    merged, conflicts, missing = [], {}, []
    try:
        for branch in branches:
            if _git(repo, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}").returncode != 0:
                missing.append(branch)
                continue
            r = _git(dest, *_IDENTITY, "merge", "--no-ff", "-m", f"integration: {branch}", branch)
            if r.returncode == 0:
                merged.append(branch)
                continue
            files = _git(dest, "diff", "--name-only", "--diff-filter=U").stdout.split()
            conflicts[branch] = files
            _git(dest, "merge", "--abort")
        head = _must(dest, "rev-parse", "HEAD")
    except CombineError:
        remove(repo, dest)
        raise
    return {"base": base, "head": head, "merged": merged, "conflicts": conflicts, "missing": missing}


def branch_exists(repo: str, branch: str) -> bool:
    """Does `branch` exist in `repo`? False for a folder that is not a repo."""
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}").returncode == 0
    except CombineError:
        return False


def make_base(repo: str, branches: list, name: str, dest: str) -> dict:
    """Create the branch `name` holding the merge of `branches` (what a part that follows several parts builds on).

    The merge is done in a throw-away worktree at `dest`, which is always removed. A conflict or a missing branch raises
    `CombineError` and leaves no branch behind. The project's checkout and the merged branches are never touched."""
    try:
        res = run(repo, branches, dest)
        if res["conflicts"] or res["missing"]:
            problems = [f"{b} conflicts in {', '.join(f) or 'some files'}" for b, f in res["conflicts"].items()]
            problems += [f"{b} does not exist" for b in res["missing"]]
            raise CombineError("the parts this one follows cannot be combined: " + "; ".join(problems))
        _must(repo, "branch", name, res["head"])
        return res
    finally:
        remove(repo, dest)


def delete_branch(repo: str, branch: str) -> None:
    """Delete a branch if it exists (used for the bases made by `make_base`). Never raises."""
    try:
        if branch_exists(repo, branch):
            _git(repo, "branch", "-D", branch)
    except CombineError:
        pass
