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


def build_parts(parent: dict, split: dict, spec_text: str, taken_ids, now: datetime, validate) -> list:
    """The requests a split becomes, as keyword-argument sets for `Features._new_request` (plus the part's `spec`, "" when the
    kind has no spec). Every part is validated first (`validate(project, title, desc, model, lang, kind)` raises on a bad
    one), so nothing is created when any of them is wrong. Pure: it neither writes nor mutates its arguments."""
    criteria = criteria_section(spec_text) if (spec_text or "").strip() else (parent.get("desc") or "")
    taken, out = set(taken_ids or ()), []
    for task in split.get("tasks") or []:
        fid = part_id(parent["id"], task["id"], taken, now)
        taken.add(fid)
        title = part_title(parent["id"], task)
        desc = part_desc(parent.get("title", ""), task, criteria)
        title, desc, kind = validate(parent["project"], title, desc, parent.get("model"), parent.get("lang", "en"),
                                     parent.get("kind", "feature"))
        out.append({"fid": fid, "project": parent["project"], "title": title, "desc": desc, "model": parent.get("model") or "",
                    "lang": parent.get("lang", "en"), "kind": kind, "path": parent.get("path", ""), "parent": parent["id"],
                    "stage": START_STAGE, "extra": {"part": task["id"]},
                    "spec": part_spec(parent["id"], task, desc) if (spec_text or "").strip() else ""})
    return out


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


# --------------------------------------------------------------------------- B3 — parts view, split state, integration due

LOST_AFTER_S = 30 * 60   # a reserved integration id with nothing behind it for this long is lost


def _by_id(feats) -> dict:
    return {f.get("id"): f for f in (feats or []) if isinstance(f, dict)}


def _launched_at(part: dict) -> str:
    return max((h.get("at", "") for h in part.get("history") or [] if h.get("event") == "worker launched"), default="")


def is_ready(part: dict) -> bool:
    """A part is ready when its pipeline commit succeeded, it is not back at work, and it has not been relaunched since."""
    commit = (part or {}).get("commit") or {}
    if not commit.get("ok") or part.get("status") not in ("awaiting", "done"):
        return False
    return _launched_at(part) <= commit.get("at", "")


def _parts(parent: dict, feats) -> list:
    """The existing part requests of a fanned-out parent, in the order of its split tasks."""
    by_id = _by_id(feats)
    tasks = ((parent or {}).get("split") or {}).get("tasks") or []
    return [by_id[t["part_id"]] for t in tasks if t.get("part_id") in by_id]


def parts_view(parent: dict, feats) -> list:
    """What the card lists: id, title, stage, status and `ready` of each part that still exists."""
    return [{"id": p["id"], "title": p.get("title", ""), "stage": p.get("stage", ""),
             "status": p.get("status", ""), "ready": is_ready(p)} for p in _parts(parent, feats)]


def split_state(parent: dict, feats) -> str:
    """running | partial | ready | integrating | integrated (derived on read, never stored)."""
    split = (parent or {}).get("split") or {}
    integ = _by_id(feats).get(split.get("integration") or None)
    if integ is not None:
        return "integrated" if integ.get("status") == "done" else "integrating"
    parts = _parts(parent, feats)
    if any(p.get("status") in ("cancelled", "failed") for p in parts):
        return "partial"
    return "ready" if parts and all(is_ready(p) for p in parts) else "running"


def _seconds_since(stamp: str, now: str) -> float:
    try:
        return (datetime.fromisoformat(now) - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def integration_due(parent: dict, feats, now: str = "", has_folder: bool = False) -> dict:
    """Is it time to create the integration request? {"due", "ready": [part ids], "skipped": [part ids], "reason"}.

    `now` is an ISO timestamp (only used to date a lost reservation); `has_folder` says whether the
    reserved integration folder exists on disk."""
    def out(due, ready=(), skipped=(), reason=""):
        return {"due": due, "ready": list(ready), "skipped": list(skipped), "reason": reason}

    split = (parent or {}).get("split") or {}
    reserved = split.get("integration") or ""
    if reserved:
        if reserved in _by_id(feats) or has_folder:
            return out(False, reason="integration already created")
        if now and _seconds_since(split.get("integration_reserved_at", ""), now) > LOST_AFTER_S:
            return out(False, reason="integration reservation lost")
        return out(False, reason="integration being created")
    if split.get("integration_error"):
        return out(False, reason="integration failed earlier")
    parts = _parts(parent, feats)
    skipped = [p["id"] for p in parts if p.get("status") == "cancelled"]
    live = [p for p in parts if p.get("status") != "cancelled"]
    if any(p.get("status") == "failed" for p in live):
        return out(False, skipped=skipped, reason="a part failed")
    if any(not is_ready(p) for p in live):
        return out(False, skipped=skipped, reason="parts still under way")
    ready = [p["id"] for p in live]
    if len(ready) < 2:
        return out(False, ready, skipped, "fewer than 2 parts to integrate")
    return out(True, ready, skipped)
