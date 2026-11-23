"""Pipelines as data: load, validate, snapshot and compare a pipeline definition (a JSON file or dict).

A pipeline is a graph of steps (worker | check | approval). The validator is the safety net: it only accepts a closed set of keys,
named gates from `gates.GATES`, bounded loops, and an approval by the user before any step that publishes or touches
production. Nothing here executes anything, and nothing runs at import time (no I/O)."""
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import gates

FORMAT = 1
MAX_BYTES, MAX_STEPS, MAX_PROMPT, MAX_TOOL_RULES, MAX_ROUNDS, MAX_NOTES = 64 * 1024, 30, 20_000, 24, 10, 500
PIPELINE_ID = re.compile(r"^[a-z0-9-]{1,40}$")
STEP_ID = re.compile(r"^[a-z0-9-]{1,30}$")
PHASE = re.compile(r"^[a-z-]{1,24}$")
TOOL_RULE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\([^\n,]{1,200}\))?$")   # same shape as chief._TOOL_OK
MODES = ("read_only", "worktree", "local_commit", "publish", "production")
RISKY_MODES = ("publish", "production")
VERDICTS = ("approved", "reserves", "blocked", "unknown")
END = "end"
# The {fields} a prompt may use (what the prompts are formatted with today).
MARKERS = frozenset({"title", "fid", "project", "desc", "path", "qpath", "spec", "plan", "report", "work", "out",
                     "feedback", "prev", "review", "comment", "concept"})
_COMMON = {"id", "type", "label", "next", "notes"}
_WORKER = _COMMON | {"phase", "artifact", "mode", "prompt", "skills", "questions", "model", "effort", "permissions",
                     "archive", "report_gate", "inputs", "keep_questions_on_next"}
ACCEPTED_KEYS = {
    "pipeline": frozenset({"format", "id", "version", "name", "description", "based_on", "kind", "limits", "inputs", "steps"}),
    "limits": frozenset({"max_total_rounds"}),
    "worker": frozenset(_WORKER),
    "check": frozenset(_WORKER | {"checks", "verdict", "gates", "on"}),
    "approval": frozenset(_COMMON | {"of", "on_changes", "on_fixes", "confirm", "requires_user"}),
    "prompt": frozenset({"ref", "pt", "en", "extra"}),
    "permissions": frozenset({"profile", "allow", "deny"}),
    "transition": frozenset({"goto", "max_rounds", "then", "retry", "do", "next"}),
    "gate": frozenset({"use", "args"}),
}
INPUT_NAMES = ("request", "spec", "plan", "report")
_SECRET = re.compile(r"(?i)(?:sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|(?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S{6,}|"
                     r"-----BEGIN [A-Z ]*PRIVATE KEY-----)")
BUILTIN_IDS = ("feature", "fix", "maintenance", "analysis", "ship", "release")
# What the built-in pipelines allow the user to do to a request, kept here so `features.py` never branches on the type:
NO_AUTOPILOT = ("ship", "release")                      # «run until the review» never applies to a publication or a release
CONVERTIBLE = ("feature", "fix", "maintenance", "analysis")   # the types a request may be switched between


class PipelineError(Exception):
    def __init__(self, field: str, msg: str):
        super().__init__(f"{field}: {msg}")
        self.field, self.msg = field, msg


@dataclass(frozen=True)
class Pipeline:
    data: dict            # the validated definition (treat as read-only)
    builtin: bool = False

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def steps(self) -> list:
        return self.data["steps"]

    def step(self, sid: str) -> dict:
        return next(s for s in self.steps if s["id"] == sid)


@dataclass(frozen=True)
class Change:
    path: str
    old: object
    new: object


def _keys(obj, kind: str, where: str) -> None:
    if not isinstance(obj, dict):
        raise PipelineError(where, "must be an object")
    extra = sorted(set(obj) - ACCEPTED_KEYS[kind])
    if extra:
        raise PipelineError(where, f"unknown key(s): {', '.join(extra)}")


def _no_secrets(value, where: str) -> None:
    if isinstance(value, str):
        if _SECRET.search(value):
            raise PipelineError(where, "text looks like a secret or token")
    elif isinstance(value, dict):
        for k, v in value.items():
            _no_secrets(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _no_secrets(v, f"{where}[{i}]")


def _check_prompt(p, where: str) -> None:
    _keys(p, "prompt", where)
    ref = p.get("ref")
    if ref is not None and not (isinstance(ref, str) and re.match(r"^builtin:[a-z]+\.[a-z-]+$", ref)):
        raise PipelineError(where + ".ref", "must look like builtin:<type>.<phase>")
    if not ref and not (p.get("pt") or p.get("en")):
        raise PipelineError(where, "needs a ref or a text (pt/en)")
    for lang in ("pt", "en", "extra"):
        t = p.get(lang)
        if t is None:
            continue
        if not isinstance(t, str) or len(t) > MAX_PROMPT:
            raise PipelineError(f"{where}.{lang}", f"text of at most {MAX_PROMPT} characters")
        bad = sorted(set(re.findall(r"\{([A-Za-z_]\w*)\}", t)) - MARKERS)
        if bad:
            raise PipelineError(f"{where}.{lang}", f"unknown marker(s): {', '.join('{' + b + '}' for b in bad)}")


def _check_permissions(perm, mode: str, where: str) -> None:
    _keys(perm, "permissions", where)
    for side in ("allow", "deny"):
        rules = perm.get(side, [])
        if not isinstance(rules, list) or len(rules) > MAX_TOOL_RULES or not all(isinstance(r, str) and TOOL_RULE.match(r) for r in rules):
            raise PipelineError(f"{where}.{side}", f"at most {MAX_TOOL_RULES} well-formed rules")
    if mode not in RISKY_MODES and any(re.search(r"git\s+push|gh\s+pr\b", r) for r in perm.get("allow", [])):
        raise PipelineError(where + ".allow", f"push / pull request only in {' or '.join(RISKY_MODES)} mode, not «{mode}»")


def _check_gates(items, where: str) -> None:
    if not isinstance(items, list):
        raise PipelineError(where, "must be a list")
    for i, g in enumerate(items):
        _keys(g, "gate", f"{where}[{i}]")
        if g.get("use") not in gates.GATES:
            raise PipelineError(f"{where}[{i}].use", f"unknown gate «{g.get('use')}»")
        if not isinstance(g.get("args", {}), dict):
            raise PipelineError(f"{where}[{i}].args", "must be an object")


def _targets(step: dict) -> list:
    """(label, target id or END, bounded) edges leaving a step."""
    out = []
    nxt = step.get("next")
    if isinstance(nxt, str):
        out.append(("next", nxt, False))
    for key in ("on", "on_changes", "on_fixes"):
        for verdict, t in (step.get(key) or {}).items():
            if isinstance(t, str):
                out.append((f"{key}.{verdict}", t, False))
            elif isinstance(t, dict):
                if t.get("goto"):
                    out.append((f"{key}.{verdict}", t["goto"], "max_rounds" in t))
                for extra in ("then", "next"):
                    if t.get(extra):
                        out.append((f"{key}.{verdict}.{extra}", t[extra], False))
    return out


def _check_step(s, where: str) -> None:
    if not isinstance(s, dict):
        raise PipelineError(where, "must be an object")
    kind = s.get("type")
    if kind not in ("worker", "check", "approval"):
        raise PipelineError(where + ".type", "must be worker, check or approval")
    _keys(s, kind, where)
    if not STEP_ID.match(str(s.get("id", ""))):
        raise PipelineError(where + ".id", "must match [a-z0-9-]{1,30}")
    notes = s.get("notes")
    if notes is not None and not (isinstance(notes, str) and len(notes) <= MAX_NOTES):
        raise PipelineError(where + ".notes", f"free text of at most {MAX_NOTES} characters")
    if kind == "approval":
        if s.get("requires_user", True) is not True:
            raise PipelineError(where + ".requires_user", "an approval always needs the user")
        return
    if "phase" in s and not PHASE.match(str(s["phase"])):
        raise PipelineError(where + ".phase", "must match [a-z-]{1,24}")
    mode = s.get("mode", "read_only")
    if mode not in MODES:
        raise PipelineError(where + ".mode", f"must be one of {', '.join(MODES)}")
    if "prompt" not in s:
        raise PipelineError(where + ".prompt", "a worker step needs a prompt")
    _check_prompt(s["prompt"], where + ".prompt")
    if "permissions" in s:
        _check_permissions(s["permissions"], mode, where + ".permissions")
    for key in ("inputs",):
        if key in s and (not isinstance(s[key], list) or not set(s[key]) <= set(INPUT_NAMES)):
            raise PipelineError(where + "." + key, f"list of {', '.join(INPUT_NAMES)}")
    if "keep_questions_on_next" in s and not isinstance(s["keep_questions_on_next"], bool):
        raise PipelineError(where + ".keep_questions_on_next", "must be true or false")
    if "report_gate" in s:
        _check_gates([s["report_gate"]] if isinstance(s["report_gate"], dict) else s["report_gate"], where + ".report_gate")
    if kind == "check":
        _check_gates(s.get("gates", []), where + ".gates")
        for verdict, t in (s.get("on") or {}).items():
            if verdict not in VERDICTS:
                raise PipelineError(f"{where}.on.{verdict}", f"verdict must be one of {', '.join(VERDICTS)}")
            if isinstance(t, dict):
                _keys(t, "transition", f"{where}.on.{verdict}")


def _check_graph(steps: list) -> None:
    ids = [s["id"] for s in steps]
    if len(set(ids)) != len(ids):
        raise PipelineError("steps", "duplicate step id")
    index = {sid: i for i, sid in enumerate(ids)}
    edges = {}
    for i, s in enumerate(steps):
        outs = []
        for label, t, bounded in _targets(s):
            if t != END and t not in index:
                raise PipelineError(f"steps.{s['id']}.{label}", f"unknown step «{t}»")
            if t != END and index[t] <= i and not bounded:
                raise PipelineError(f"steps.{s['id']}.{label}", "going back to an earlier step needs max_rounds")
            outs.append(t)
        if "next" not in s:                      # a step without `next` falls through to the following one (the last ends)
            outs.append(steps[i + 1]["id"] if i + 1 < len(steps) else END)
        edges[s["id"]] = outs
    # every step must be able to reach an end (the last step ends the pipeline)
    last = ids[-1]
    reach, changed = {last}, True
    while changed:
        changed = False
        for sid, outs in edges.items():
            if sid not in reach and (END in outs or any(o in reach for o in outs)):
                reach.add(sid)
                changed = True
    stuck = [sid for sid in ids if sid not in reach]
    if stuck:
        raise PipelineError("steps", f"no way out of the loop at: {', '.join(stuck)}")
    _check_publish_has_approval(steps, edges)


def _check_publish_has_approval(steps: list, edges: dict) -> None:
    """Every way into a publish/production step comes straight from an approval by the user (and it is never the first step)."""
    by_id = {s["id"]: s for s in steps}
    for s in steps:
        if s["type"] == "approval" or s.get("mode") not in RISKY_MODES:
            continue
        msg = f"a «{s['mode']}» step needs an approval by the user right before it on every path"
        if s is steps[0]:
            raise PipelineError(f"steps.{s['id']}", msg)
        for sid, outs in edges.items():
            if s["id"] in outs and by_id[sid]["type"] != "approval":
                raise PipelineError(f"steps.{s['id']}", msg + f" (reached from «{sid}»)")


def validate(data: dict, *, filename_id: str | None = None, builtin: bool = False) -> dict:
    if not isinstance(data, dict):
        raise PipelineError("pipeline", "must be an object")
    if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise PipelineError("pipeline", f"larger than {MAX_BYTES // 1024} KB")
    _keys(data, "pipeline", "pipeline")
    fmt = data.get("format")
    if fmt != FORMAT:
        raise PipelineError("format", f"only format {FORMAT} is supported" + (" (newer: update the Chief)" if isinstance(fmt, int) and fmt > FORMAT else ""))
    pid = str(data.get("id", ""))
    if not PIPELINE_ID.match(pid):
        raise PipelineError("id", "must match [a-z0-9-]{1,40}")
    if filename_id is not None and pid != filename_id:
        raise PipelineError("id", f"«{pid}» differs from the file name «{filename_id}»")
    if not builtin and pid in BUILTIN_IDS:
        raise PipelineError("id", f"«{pid}» belongs to a built-in pipeline")
    if "kind" in data and data["kind"] not in CONVERTIBLE:
        raise PipelineError("kind", "one of " + ", ".join(CONVERTIBLE))
    if "limits" in data:
        _keys(data["limits"], "limits", "limits")
        n = data["limits"].get("max_total_rounds")
        if n is not None and not (isinstance(n, int) and 1 <= n <= 100):
            raise PipelineError("limits.max_total_rounds", "integer 1..100")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PipelineError("steps", "at least one step")
    if len(steps) > MAX_STEPS:
        raise PipelineError("steps", f"at most {MAX_STEPS} steps")
    _no_secrets(data, "pipeline")
    for i, s in enumerate(steps):
        _check_step(s, f"steps[{i}]")
    _check_graph(steps)
    for s in steps:
        for verdict, t in (s.get("on") or {}).items():
            if isinstance(t, dict) and "max_rounds" in t and not (isinstance(t["max_rounds"], int) and 1 <= t["max_rounds"] <= MAX_ROUNDS):
                raise PipelineError(f"steps.{s['id']}.on.{verdict}.max_rounds", f"integer 1..{MAX_ROUNDS}")
    return data


_FREE = frozenset({"id", "label", "notes", "next", "model", "effort"})      # what a user may change in a step taken from a built-in
_INPUT_PHASE = {"spec": "spec", "plan": "plan", "report": "build"}          # the phase whose artifact an input names
_NEEDS = {"concept-validation": "concept", "plan-validation": "plan", "review": "build"}
_ROUNDS = frozenset({"max_rounds", "retry"})      # how many automatic rounds a check may use is the user's to set


def _keyed(steps: list) -> dict:
    """step id -> a name that does not depend on the id: its phase, or `approval:<phase of what it approves>`."""
    out = {s["id"]: s.get("phase", s["id"]) for s in steps if s["type"] != "approval"}
    out.update({s["id"]: "approval:" + out.get(s.get("of"), "?") for s in steps if s["type"] == "approval"})
    return out


def _canon(step: dict, names: dict) -> dict:
    """The step without what the user may change, its ways to other steps written by name instead of id."""
    c = {k: v for k, v in step.items() if k not in _FREE}
    if "next" in step:
        c["next"] = names.get(step["next"], step["next"])
    if step.get("on"):
        c["on"] = {v: ({**{k: x for k, x in t.items() if k not in _ROUNDS}, **{k: names.get(t[k], t[k]) for k in ("goto", "next") if k in t}}
                       if isinstance(t, dict) else names.get(t, t)) for v, t in step["on"].items()}
    return c


def runnable(data: dict) -> list:
    """Why a (valid) user pipeline cannot be chosen for a request: a list of plain sentences, empty when it can.

    A request runs on the built-in phases only (each one writes its own file and is waited for by it), so a usable pipeline is an
    ordered choice of the phases of ONE request type, each at most once, every step being the built-in step of its phase; its id,
    label, model, effort and rounds are free. It must be able to read what its prompts read, a build needs the review after it, and it
    ends with the user's acceptance of the review."""
    kind = data.get("kind")
    if kind not in CONVERTIBLE:
        return ["kind: choose the type of request it is (" + ", ".join(CONVERTIBLE) + ")"]
    base = load(builtin_dir() / f"{kind}.json", builtin=True)
    base_by_phase = {s["phase"]: s for s in base.steps if s["type"] != "approval"}
    base_names, names = _keyed(base.steps), _keyed(data["steps"])
    problems, seen = [], []
    for s in data["steps"]:
        if s["type"] == "approval":
            continue
        ph, sid = s.get("phase"), s["id"]
        ref = base_by_phase.get(ph)
        if ref is None:
            problems.append(f"step «{sid}»: «{ph}» is not a phase of a {kind} request")
            continue
        if ph in seen:
            problems.append(f"step «{sid}»: the phase «{ph}» is used twice")
            continue
        if ph == "concept" and ("spec" in seen or "plan" in seen):
            problems.append(f"step «{sid}»: the concept must come before the spec and the plan")
        if _canon(s, names) != _canon(ref, base_names):
            problems.append(f"step «{sid}»: it must be the built-in «{ph}» step (only its id, label, model, effort and rounds may change)")
        needs = [_INPUT_PHASE[i] for i in ref.get("inputs", []) if i in _INPUT_PHASE] + ([_NEEDS[ph]] if ph in _NEEDS else [])
        for need in dict.fromkeys(needs):
            if need not in seen:
                problems.append(f"step «{sid}»: the «{need}» step must come before it")
        seen.append(ph)
    if "build" in seen and "review" not in seen:
        problems.append("a build needs the independent review after it")
    last = data["steps"][-1]
    rev = next((s["id"] for s in data["steps"] if s.get("phase") == "review"), None)
    if not (last["type"] == "approval" and last.get("requires_user", True) and rev and last.get("of") == rev):
        problems.append("it must end with your acceptance of the review")
    return problems


def load(source, *, builtin: bool = False) -> Pipeline:
    """A Pipeline from a path or a dict. Raises PipelineError with the offending field."""
    filename_id = None
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_symlink():   # a file in the user's folder may not point somewhere else
            raise PipelineError("file", "a symbolic link is not accepted")
        try:
            raw = path.read_bytes()
        except OSError as e:
            raise PipelineError("file", str(e)) from e
        if len(raw) > MAX_BYTES:
            raise PipelineError("file", f"larger than {MAX_BYTES // 1024} KB")
        try:
            source = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise PipelineError("file", f"not valid JSON: {e}") from e
        filename_id = path.stem
    return Pipeline(validate(source, filename_id=filename_id, builtin=builtin), builtin)


def snapshot(p: Pipeline) -> dict:
    """A deep copy of the definition: what a request keeps, immune to later edits of the file."""
    return json.loads(json.dumps(p.data))


def digest(p: Pipeline) -> str:
    return hashlib.sha256(json.dumps(p.data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def diff(a: Pipeline, b: Pipeline) -> list:
    """Flat list of the differences between two definitions."""
    out = []

    def walk(x, y, path):
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                walk(x.get(k), y.get(k), f"{path}.{k}" if path else k)
        elif isinstance(x, list) and isinstance(y, list) and all(isinstance(i, dict) and "id" in i for i in x + y):
            xi, yi = {i["id"]: i for i in x}, {i["id"]: i for i in y}
            for k in list(xi) + [k for k in yi if k not in xi]:
                walk(xi.get(k), yi.get(k), f"{path}[{k}]")
        elif x != y:
            out.append(Change(path, x, y))

    walk(a.data, b.data, "")
    return out


@dataclass(frozen=True)
class Entry:
    id: str
    origin: str          # builtin | user
    valid: bool
    reason: str = ""


def builtin_dir() -> Path:
    return Path(__file__).resolve().parent / "pipelines_builtin"


def list_all(home) -> list:
    """Built-in and user pipelines, each marked valid or not (with the reason). Reads files; never writes."""
    out = []
    for origin, folder in (("builtin", builtin_dir()), ("user", Path(home) / "pipelines")):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                load(path, builtin=origin == "builtin")
                out.append(Entry(path.stem, origin, True))
            except PipelineError as e:
                out.append(Entry(path.stem, origin, False, str(e)))
    return out
