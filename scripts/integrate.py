"""Integrate a finished request: merge its branch into the project's branch, push when there is a remote, remove the worktree.

Two steps, both run by the server (never by a worker), so the result does not depend on a model's permission rules:

- `plan` only reads. It says what would run (the commits, the target branch, the remote without credentials, the exact commands)
  and why it cannot run, and returns `expect`: the state of the project the plan describes.
- `run` is called only after the user's confirmation. It plans again and refuses if the project is no longer in the state the
  user was shown, so the click authorises exactly the commands that were on screen.

Never forces: no `--force`, no deleting a remote branch, no history rewriting. A merge with a conflict is aborted and the project
is left as it was. A rejected push keeps the local merge and a new plan offers the push alone. The remote's credentials are
git's own; nothing here reads or stores them.
"""
import re
import subprocess
from pathlib import Path

import finish

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TIMEOUT_S = 120
CRED_RE = re.compile(r"(?<=//)[^/@\s]+@")           # user:password@ inside a URL
MAX_COMMITS = 30


def _git(cwd, *args, timeout: int = TIMEOUT_S):
    """(returncode, stdout, stderr). A git that cannot be run is (-1, '', reason)."""
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=NO_WINDOW, env=_env())
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _env():
    import os
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}          # a missing login fails; it never waits for a prompt nobody sees


def _last_line(text: str, default: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return (lines[-1] if lines else default)[:300]


def _remote_target(repo, into: str) -> dict:
    """Where `into` is pushed: its upstream, else `origin` with the same name. {remote, target, ahead}; remote '' if none."""
    rc, remote, _ = _git(repo, "config", f"branch.{into}.remote")
    rc2, merge_ref, _ = _git(repo, "config", f"branch.{into}.merge")
    if rc == 0 and remote and remote != "." and rc2 == 0 and merge_ref.startswith("refs/heads/"):
        _, ahead, _ = _git(repo, "rev-list", "--count", f"{into}@{{upstream}}..{into}")
        return {"remote": remote, "target": merge_ref[len("refs/heads/"):], "ahead": int(ahead) if ahead.isdigit() else 0}
    _, remotes, _ = _git(repo, "remote")
    if "origin" in remotes.split():
        return {"remote": "origin", "target": into, "ahead": 0}
    return {"remote": "", "target": "", "ahead": 0}


def _problems(repo, work, branch: str, into: str) -> list:
    out = []
    if not into:
        out.append("the project's checkout is not on a branch (detached HEAD): switch to the branch to integrate into")
    elif into == branch:
        out.append("the project's checkout is on the request's own branch")
    if _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD")[0] == 0:
        out.append("a merge is already in progress in the project: finish or abort it first")
    if _git(repo, "status", "--porcelain", "--untracked-files=no")[1]:
        out.append("the project's checkout has uncommitted changes (they are yours: commit or stash them first)")
    if Path(work).is_dir() and _git(work, "status", "--porcelain")[1]:
        out.append("the request's worktree has uncommitted changes that integrating would lose")
    return out


def plan(repo, work, branch: str, secret_paths=None) -> dict:
    """What integrating would do. {ok, problems, into, mode, commits, remote, remote_url, commands, expect}. Changes nothing."""
    out = {"ok": False, "problems": [], "into": "", "mode": "", "commits": [], "remote": "", "remote_url": "", "commands": [], "expect": {}}
    if not finish.BRANCH_RE.match(branch) or not Path(repo).is_dir():
        out["problems"] = ["not a request branch, or the project folder does not exist"]
        return out
    if _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")[0] != 0:
        out["problems"] = [f"the branch {branch} does not exist"]
        return out
    into = _git(repo, "branch", "--show-current")[1]
    problems = _problems(repo, work, branch, into)
    target = _remote_target(repo, into) if into else {"remote": "", "target": "", "ahead": 0}
    commits = []
    if into and into != branch:
        _, log, _ = _git(repo, "log", f"--max-count={MAX_COMMITS}", "--format=%h%x09%s", f"{into}..{branch}")
        commits = [{"sha": s, "subject": subj} for s, _, subj in (ln.partition("\t") for ln in log.splitlines() if ln.strip())]
    mode = "merge" if commits else "push" if target["remote"] and target["ahead"] > 0 else ""
    if not mode and not problems:
        problems.append(f"nothing to integrate: {branch} has no commits that {into} lacks, and there is nothing to push")
    if secret_paths and commits:
        _, names, _ = _git(repo, "diff", "--name-only", "-z", f"{into}...{branch}")
        bad = secret_paths([n for n in names.split("\0") if n])
        if bad:
            problems.append("these files look like secrets and are not integrated: " + ", ".join(bad[:5]))
    folder = str(repo).replace("\\", "/")
    commands = []
    if mode == "merge":
        commands.append(f'git -C "{folder}" merge --no-ff {branch}')
    if mode and target["remote"]:
        commands.append(f'git -C "{folder}" push {target["remote"]} {into}:{target["target"]}')
    if mode:
        commands += [f'git -C "{folder}" worktree remove <the request\'s worktree>', f'git -C "{folder}" branch -d {branch}']
    url = _git(repo, "remote", "get-url", target["remote"])[1] if target["remote"] else ""
    out.update(ok=not problems, problems=problems, into=into, mode=mode, commits=commits, remote=target["remote"],
               remote_url=CRED_RE.sub("", url), commands=commands,
               expect={"into": into, "mode": mode, "head": _git(repo, "rev-parse", "HEAD")[1],
                       "branch_head": _git(repo, "rev-parse", branch)[1]})
    return out


def _fail(error: str, merged: bool = False, steps=()) -> dict:
    return {"ok": False, "merged": merged, "pushed": False, "steps": list(steps), "error": error}


def _merge(repo, branch: str):
    """Merge the branch; on a conflict or any failure abort so the project is as it was. (ok, error)."""
    rc, _, err = _git(repo, "merge", "--no-ff", "-m", f"Merge {branch}", branch)
    if rc == 0:
        return True, ""
    conflicts = _git(repo, "diff", "--name-only", "--diff-filter=U")[1].splitlines()
    _git(repo, "merge", "--abort")
    if conflicts:
        return False, "the merge has conflicts and was aborted; the project is as it was. Conflicting files: " + ", ".join(conflicts[:8])
    return False, "the merge failed and was aborted: " + _last_line(err, "git merge failed")


def run(repo, work, branch: str, expect: dict, secret_paths=None) -> dict:
    """Do what `plan` showed, after the user's confirmation. {ok, merged, pushed, steps, error}."""
    p = plan(repo, work, branch, secret_paths)
    if not isinstance(expect, dict) or p["expect"] != expect:
        return _fail("the project changed since the plan was shown: look at the new plan before confirming")
    if not p["ok"]:
        return _fail("; ".join(p["problems"]))
    steps, merged, into = [], False, p["into"]
    if p["mode"] == "merge":
        ok, err = _merge(repo, branch)
        if not ok:
            return _fail(err)
        merged = True
        steps.append(f"merged {branch} into {into}")
    pushed = False
    if p["remote"]:
        target = _remote_target(repo, into)["target"]
        rc, _, err = _git(repo, "push", p["remote"], f"{into}:{target}")
        if rc != 0:
            return _fail(f"the push to {p['remote']} failed (nothing was forced): " + _last_line(err, "git push failed")
                         + (". The merge is done locally; integrate again to retry the push." if merged else ""), merged, steps)
        pushed = True
        steps.append(f"pushed {into} to {p['remote']}")
    ok, info = finish.remove(repo, work, branch)
    steps.append(info if ok else f"the branch is integrated but the clean-up did not finish: {info}")
    return {"ok": True, "merged": merged, "pushed": pushed, "steps": steps, "error": ""}
