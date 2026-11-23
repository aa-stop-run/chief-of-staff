"""Fan-out: the pure helpers that turn the parts of an approved plan into sub-requests.

B1 here: ids, titles, descriptions and spec files of the parts. B2 (the approve-time
decision) and B3 (parts view and split state) are added next. Nothing in this module
touches the disk or the request list; the server's thin wrappers do. See
docs/plans/2026-11-20-split-fanout-task-graph.md.
"""
import re
from datetime import datetime

import splitplan

START_STAGE = "plan"
FAN_OUT_KINDS = ("feature", "fix", "maintenance")

MAX_TITLE = 120          # features.MAX_TITLE
MAX_DESC = 4000          # features.MAX_DESC
MAX_CRITERIA = 1500

_CRITERIA_RE = re.compile(r"^(#{1,6})\s*(acceptance criteria|crit[ée]rios de aceita[çc][ãa]o)\s*$", re.I | re.M)
_HEADING_RE = re.compile(r"^(#{1,6})\s", re.M)


def kind_allows(f: dict) -> bool:
    """May this request fan out? Only feature, fix and maintenance, and never a part itself."""
    f = f or {}
    return f.get("kind", "feature") in FAN_OUT_KINDS and not f.get("part")


def part_id(parent_id: str, task_id: str, taken, now: datetime) -> str:
    """`<parent[:28]>-<task id[:20]>-<yymmddHHMMSS>`; `-2`, `-3`… when `taken` already has it."""
    base = f"{parent_id[:28].rstrip('-')}-{task_id[:20]}-{now.strftime('%y%m%d%H%M%S')}".lower()
    taken = set(taken or ())
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def part_title(parent_id: str, task: dict, max_len: int = MAX_TITLE) -> str:
    """`[<parent[:24]>/<task id>] <title>`, cut with an ellipsis at `max_len`."""
    text = f"[{parent_id[:24].rstrip('-')}/{task.get('id', '')}] {task.get('title', '')}".strip()
    return text if len(text) <= max_len else text[:max_len - 1].rstrip() + "…"


def criteria_section(spec_text: str) -> str:
    """The «Acceptance criteria»/«Critérios de aceitação» section of a spec (heading included), or ""."""
    text = spec_text or ""
    m = _CRITERIA_RE.search(text)
    if not m:
        return ""
    level = len(m.group(1))
    end = len(text)
    for h in _HEADING_RE.finditer(text, m.end()):
        if len(h.group(1)) <= level:
            end = h.start()
            break
    return text[m.start():end].strip()


def part_desc(parent_title: str, task: dict, criteria: str) -> str:
    """The description of a part's request: goal, owned files, the scope rule and the parent's criteria."""
    files = task.get("files") or []
    lines = [f"Goal: {task.get('title', '')} (a part of «{parent_title}»).", "", "Files you own:"]
    lines += [f"- {f.get('path', '')}{' (new)' if f.get('new') else ''}" for f in files] or ["- (none listed)"]
    lines += ["", "Touch only these files; ask for a follow-up request if you need others."]
    criteria = (criteria or "").strip()
    if criteria:
        if len(criteria) > MAX_CRITERIA:
            criteria = criteria[:MAX_CRITERIA - 1].rstrip() + "…"
        lines += ["", "Parent's acceptance criteria (keep only the criteria that concern your files):", "", criteria]
    out = "\n".join(lines)
    return out if len(out) <= MAX_DESC else out[:MAX_DESC - 1].rstrip() + "…"


def part_spec(parent_id: str, task: dict, desc: str) -> str:
    """Markdown for the part's `spec.md`: title, description and where it comes from."""
    return f"# {task.get('title', '')}\n\n{desc}\n\nPart of {parent_id}.\n"


def decide(f: dict, plan_text: str, questions, repo_files) -> dict:
    """What approving this plan means: {"action": "linear"|"refuse"|"fan_out", "reason": str, "split": dict|None}.

    Pure; the caller supplies the open questions (None = unreadable file) and the repo's files."""
    sp = splitplan.parse_split(plan_text)
    if sp is None:
        return {"action": "linear", "reason": "", "split": None}
    f = f or {}
    if f.get("kind", "feature") not in FAN_OUT_KINDS:
        return {"action": "linear", "reason": "kind", "split": sp}
    if f.get("part"):
        return {"action": "linear", "reason": "part", "split": sp}
    findings = splitplan.check_split(sp, repo_files)
    blocking = [x for x in findings if x["severity"] == "HIGH" or x["code"] == "size"]
    if blocking:
        codes = ", ".join(dict.fromkeys(x["code"] for x in blocking))
        return {"action": "refuse", "split": sp,
                "reason": f"The plan's split has problems ({codes}): ask for changes."}
    if any(x["code"] == "single-part" for x in findings):
        return {"action": "linear", "reason": "single part", "split": sp}
    if questions is None or questions:
        return {"action": "refuse", "split": sp,
                "reason": "The plan still has open questions (or the questions file is missing or invalid): "
                          "answer them or ask for changes before it fans out."}
    return {"action": "fan_out", "reason": "", "split": sp}


def autopilot_blocks(f: dict, plan_text: str) -> bool:
    """True when autopilot must not approve this plan: it has a split (2+ parts, or one that cannot be read)."""
    if not kind_allows(f):
        return False
    sp = splitplan.parse_split(plan_text)
    return sp is not None and (len(sp["tasks"]) >= 2 or bool(sp["errors"]))
