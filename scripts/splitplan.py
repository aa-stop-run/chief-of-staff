"""Structural helpers for the split & fan-out feature.

A plan may declare 2-10 independent parts in a fenced JSON block; these
functions parse and check that block without ever raising. The behaviour is
shared by the server (fan-out, integration) and the page (the «Split: N parts»
line); see docs/plans/2026-11-20-split-fanout-task-graph.md.
"""
import json
import re
import subprocess

# Field names in both languages; the first key found wins.
_ID_KEYS = ("id",)
_TITLE_KEYS = ("title", "titulo")
_FILES_KEYS = ("files", "ficheiros")
_DEPENDS_KEYS = ("depends_on", "depende_de")
_TASKS_KEYS = ("tasks", "tarefas")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def norm_path(p: str) -> str:
    """A repo-relative path in canonical form: forward slashes, no leading ./ or
    trailing /, lower-cased. Case- and separator-insensitive so a plan written on
    Windows matches the paths git reports."""
    s = (p or "").replace("\\", "/").strip()
    while s.startswith("./"):
        s = s[2:]
    s = s.lower().rstrip("/")
    return s


def repo_files(project_path: str) -> set:
    """The committed, normalised paths of a git working tree; an empty set when the
    path is not a usable repo. It never raises."""
    if not project_path or not isinstance(project_path, str):
        return set()
    try:
        r = subprocess.run(["git", "-C", project_path, "ls-files"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return set()
    if r.returncode != 0:
        return set()
    return {norm_path(line) for line in r.stdout.splitlines() if line.strip()}


def _norm_file(value):
    """One file entry: "p" -> {"path": p, "new": False}; {"new": p} -> new file."""
    if isinstance(value, str):
        return {"path": value, "new": False}
    if isinstance(value, dict):
        if isinstance(value.get("new"), str):
            return {"path": value["new"], "new": True}
        return {"path": str(value.get("path", "")), "new": bool(value.get("new", False))}
    return {"path": "", "new": False}


def _norm_task(raw):
    """One task: accept both languages' field names; missing keys -> empty."""
    def pick(keys):
        for key in keys:
            if key in raw and raw[key] is not None:
                return raw[key]
        return None

    files_raw = pick(_FILES_KEYS)
    depends_raw = pick(_DEPENDS_KEYS)
    files_raw = files_raw if isinstance(files_raw, list) else []
    depends_raw = depends_raw if isinstance(depends_raw, list) else []
    return {
        "id": str(pick(_ID_KEYS) or ""),
        "title": str(pick(_TITLE_KEYS) or ""),
        "files": [_norm_file(f) for f in files_raw if isinstance(f, (str, dict))],
        "depends_on": [str(d) for d in depends_raw if isinstance(d, str)],
    }


_HEADING_RE = re.compile(r"^#{2,3}\s*(split|divis[ãa]o)\s*$", re.I | re.M)
_NEXT_HEADING_RE = re.compile(r"^#{1,6}\s", re.M)


def parse_split(plan_text: str):
    """Read the «Split»/«Divisão» section's first fenced JSON block.

    None when the plan has no such section; otherwise
    {"tasks": [...], "errors": [...]}. It never raises."""
    if not isinstance(plan_text, str):
        return None
    m = _HEADING_RE.search(plan_text)
    if not m:
        return None
    rest = plan_text[m.end():]
    nxt = _NEXT_HEADING_RE.search(rest)
    section = rest[:nxt.start()] if nxt else rest
    fence = _FENCE_RE.search(section)
    if not fence:
        return {"tasks": [], "errors": ["no json block"]}
    try:
        data = json.loads(fence.group(1).strip())
    except (ValueError, TypeError) as exc:
        return {"tasks": [], "errors": [f"invalid json: {exc}"]}
    raw = None
    if isinstance(data, dict):
        for key in _TASKS_KEYS:
            if key in data:
                raw = data[key]
                break
    if not isinstance(raw, list):
        return {"tasks": [], "errors": ["no tasks list"]}
    tasks, errors = [], []
    for i, t in enumerate(raw):
        if isinstance(t, dict):
            tasks.append(_norm_task(t))
        else:
            errors.append(f"task {i + 1} is not an object")
    return {"tasks": tasks, "errors": errors}


def summary(plan_text: str):
    """{"n": tasks, "errors": count} for the card, or None without a section."""
    sp = parse_split(plan_text)
    if sp is None:
        return None
    return {"n": len(sp["tasks"]), "errors": len(sp["errors"])}


_ID_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,19}$")


def _finding(severity: str, code: str, text: str, tasks=()):
    return {"severity": severity, "code": code, "text": text, "tasks": list(tasks)}


def check_split(split: dict, repo_files: set) -> list:
    """Structural findings for a parsed split block. Pure: it never mutates its
    arguments. Order: parse-error, size, single-part, bad-id, bad-dep, overlap,
    unknown-file. Paths are compared via norm_path (case- and separator-insensitive)."""
    split = split or {}
    parts = split.get("tasks") or []
    errors = split.get("errors") or []
    repo_files = {norm_path(p) for p in (repo_files or set())}
    findings = []

    if errors:
        findings.append(_finding("HIGH", "parse-error",
                                 "The split block could not be read: " + "; ".join(str(e) for e in errors)))
    if len(parts) > 10:
        findings.append(_finding("MEDIUM", "size",
                                 f"The plan declares {len(parts)} parts; keep it to at most 10."))
    if len(parts) < 2:
        findings.append(_finding("MEDIUM", "single-part",
                                 "A split needs at least 2 parts; this plan has fewer."))

    seen_ids = set()
    bad_id_tasks = []
    for p in parts:
        pid = p.get("id", "")
        if not pid or pid in seen_ids or not _ID_OK.match(pid):
            bad_id_tasks.append(pid)
        seen_ids.add(pid)
    if bad_id_tasks:
        findings.append(_finding("HIGH", "bad-id",
                                 "Part ids must be unique, slug-like and at most 20 characters.",
                                 bad_id_tasks))

    known = {p.get("id", "") for p in parts}
    bad_dep_tasks = []
    for p in parts:
        deps = p.get("depends_on") or []
        if any(d not in known for d in deps) or len(deps):
            # D1: depends_on must be empty; a name that is unknown is also flagged.
            bad_dep_tasks.append(p.get("id", ""))
    if bad_dep_tasks:
        findings.append(_finding("HIGH", "bad-dep",
                                 "Parts are independent: depends_on must be empty.",
                                 bad_dep_tasks))

    owner = {}
    for p in parts:
        for f in p.get("files") or []:
            np_ = norm_path(f.get("path", ""))
            if np_:
                owner.setdefault(np_, []).append(p.get("id", ""))
    overlap_tasks = sorted({t for ts in owner.values() if len(ts) > 1 for t in ts})
    if overlap_tasks:
        findings.append(_finding("HIGH", "overlap",
                                 "The same file is listed in more than one part.",
                                 overlap_tasks))

    unknown_tasks = []
    for p in parts:
        for f in p.get("files") or []:
            if not f.get("new"):
                np_ = norm_path(f.get("path", ""))
                if np_ and np_ not in repo_files:
                    unknown_tasks.append(p.get("id", ""))
    if unknown_tasks:
        findings.append(_finding("MEDIUM", "unknown-file",
                                 "A part lists a file that is not in the repo and is not new.",
                                 sorted(set(unknown_tasks))))

    return findings


def is_blocking(findings: list) -> bool:
    """True when any finding is HIGH."""
    return any((f or {}).get("severity") == "HIGH" for f in (findings or []))


# --------------------------------------------------------------------------- S7 — merge_review & finding_line

_V_H = {"en": "## Verdict", "pt": "## Veredicto"}
_F_H = {"en": "## Findings", "pt": "## Achados"}
_BLOCKED = {"en": "BLOCKED — structural problems in the split",
            "pt": "BLOQUEADO — problemas estruturais na divisão"}
_NONE_LINE = {"en": "None.", "pt": "Nenhum."}


def finding_line(finding: dict, lang: str) -> str:
    """One findings-list line for a structural finding."""
    sev = (finding or {}).get("severity", "")
    code = (finding or {}).get("code", "")
    text = (finding or {}).get("text", "")
    return f"- **[{sev}] {code} — {text}:**"


def _heading_re(name: str) -> "re.Pattern":
    return re.compile(rf"^#{{2,3}}\s+{re.escape(name)}\b[^\n]*$", re.I | re.M)


def merge_review(review_text: str, findings: list, lang: str) -> str:
    """Merge structural findings into a review's text (EN/PT). Pure and idempotent.

    A HIGH finding forces the verdict to BLOCKED; MEDIUM-only findings leave the
    verdict as it is. Findings are listed under the findings heading (added when
    missing); a «None.»/«Nenhum.» line is removed. When only MEDIUM findings are
    merged and there is no verdict section, the text is returned unchanged."""
    lang = "pt" if lang == "pt" else "en"
    text = review_text or ""
    findings = [f for f in (findings or []) if isinstance(f, dict)]
    if not findings:
        return text

    any_high = any((f.get("severity") == "HIGH") for f in findings)
    if not any_high and not _heading_re(_V_H[lang][3:]).search(text):
        return text

    # verdict handling
    v_re = _heading_re(_V_H[lang][3:])
    m = v_re.search(text)
    if any_high and m:
        # replace the line after the verdict heading (the verdict sentence)
        text = re.sub(rf"^#{{2,3}}\s+{re.escape(_V_H[lang][3:])}\b[^\n]*\n[^\n]*$",
                      f"{_V_H[lang]}\n{_BLOCKED[lang]}", text, count=1, flags=re.I | re.M)
    elif any_high and not m:
        text = f"{_V_H[lang]}\n{_BLOCKED[lang]}\n\n{text}"

    # findings list handling — only add lines not already present (idempotent)
    new_lines = [finding_line(f, lang) for f in findings if not _is_already_listed(text, f)]
    if new_lines:
        block = "\n".join(new_lines)
        f_re = _heading_re(_F_H[lang][3:])
        fm = f_re.search(text)
        if fm:
            text = re.sub(rf"^#{{2,3}}\s+{re.escape(_F_H[lang][3:])}\b[^\n]*\n(.*?)(?=^#{{1,3}}\s|\Z)",
                          lambda mm: _rebuild_findings(mm.group(0), block, lang),
                          text, count=1, flags=re.I | re.M | re.S)
        else:
            text = f"{text.rstrip()}\n\n{_F_H[lang]}\n{block}\n"

    return text


def _is_already_listed(text: str, finding: dict) -> bool:
    code = (finding or {}).get("code", "")
    sev = (finding or {}).get("severity", "")
    return re.search(rf"\*\*\[\s*{re.escape(sev)}\s*\]\s*{re.escape(code)}\b", text or "") is not None


def _rebuild_findings(section: str, block: str, lang: str) -> str:
    head = section.split("\n", 1)[0]
    body = section[len(head):].strip("\n")
    kept = [ln for ln in body.splitlines() if ln.strip() != _NONE_LINE[lang]]
    kept_text = "\n".join(kept).strip("\n")
    parts = [p for p in (kept_text, block) if p]
    return head + "\n" + "\n".join(parts) + "\n"
