"""Warnings about a change under review: too big, or touching paths the user marked as sensitive.

Pure: it gets numbers and file names and answers with a list. It only informs; the review and the user's decision are
untouched. The thresholds and the globs are the user's (`review_limits` and `protected_paths` in projects.json)."""
import re

DEFAULT_LIMITS = {"max_diff_lines": 800, "max_diff_files": 25}      # 0 turns a limit off
MAX_GLOBS = 50


def limits(cfg) -> dict:
    """The effective size limits: the defaults, then the user's valid non-negative integers."""
    out = dict(DEFAULT_LIMITS)
    for k, v in (cfg or {}).items() if isinstance(cfg, dict) else ():
        if k in out and isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            out[k] = v
    return out


def globs(raw) -> list:
    """The valid protected-path globs (strings, at most MAX_GLOBS)."""
    return [g.strip() for g in raw if isinstance(g, str) and g.strip()][:MAX_GLOBS] if isinstance(raw, list) else []


def _regex(glob: str):
    """`*` stays inside a folder, `**` crosses folders, `?` is one character. A glob without a slash matches by file name anywhere."""
    g = glob.replace("\\", "/").lstrip("./")
    if "/" not in g:
        g = "**/" + g
    out, i = "", 0
    while i < len(g):
        if g.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif g.startswith("**", i):
            out, i = out + ".*", i + 2
        elif g[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif g[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(g[i]), i + 1
    return re.compile("^" + out + "$")


def matches(path: str, glob: str) -> bool:
    return bool(_regex(glob).match(path.replace("\\", "/").lstrip("./")))


def warnings(diff: dict | None, paths, lim: dict, protected) -> list:
    """[{code: size|files|protected, ...}]. `diff`: finish.diff_summary's numbers; `paths`: the changed file names (they stay
    here: only the patterns that matched, and how many files, leave)."""
    out = []
    if diff:
        lines = int(diff.get("insertions", 0)) + int(diff.get("deletions", 0))
        if lim.get("max_diff_lines") and lines > lim["max_diff_lines"]:
            out.append({"code": "size", "lines": lines, "max": lim["max_diff_lines"]})
        if lim.get("max_diff_files") and int(diff.get("files", 0)) > lim["max_diff_files"]:
            out.append({"code": "files", "files": int(diff["files"]), "max": lim["max_diff_files"]})
    hit = {}
    for g in protected or ():
        n = sum(1 for p in paths or () if matches(p, g))
        if n:
            hit[g] = n
    if hit:
        out.append({"code": "protected", "patterns": [{"glob": g, "files": n} for g, n in hit.items()]})
    return out
