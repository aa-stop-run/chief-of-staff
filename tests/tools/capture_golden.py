"""Golden traces of the request pipelines (plan T0): drive `Features` through a scripted scenario with a recording launcher and
return a normalised, JSON-serialisable trace. The traces in `tests/golden/` are captured from the code BEFORE the engine is wired in
(`python tests/tools/capture_golden.py`) and are the safety net that proves the engine behaves the same (criterion A0.1).

A scenario is a list of actions; every action is applied and the launches it caused plus the request's state are recorded:
("finish", artifact, text) the running worker writes `artifact` and stops; ("approve",); ("revise", comment); ("fixes", comment);
("retry",); ("cancel",); ("mkwork",) creates the worktree folder; ("commit_fails",) makes the next commit fail."""
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts"))
sys.path.insert(0, str(HERE.parent))
import chief  # noqa: E402
from features import FeatureError, Features  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

GOLDEN = HERE.parent / "golden"
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?)?")


class Recorder:
    """Launcher that keeps EVERY argument of each launch (the test fakes drop effort / allow / deny)."""

    def __init__(self):
        self.calls, self.active = [], set()

    def launch(self, project, task, model, extra_dirs, label=None, **extra):
        wid = f"w{len(self.calls) + 1}"
        self.calls.append({"project": project, "task": task, "model": model, "dirs": [str(d) for d in extra_dirs or []],
                           "label": label, **{k: extra[k] for k in sorted(extra)}})
        self.active.add(wid)
        return {"id": wid, "name": f"cos-{wid}"}

    def is_active(self, wid):
        return wid in self.active

    def stop_all(self):
        self.active.clear()


def _route(phase, request_model=""):
    return (request_model or f"m-{phase}"), "high"


def _tools(kind, stage):
    e = chief.DEFAULT_PHASE_TOOLS.get(f"{kind}:{stage}", {})
    return tuple(e.get("allow", ())), tuple(e.get("deny", ()))


def normalize(obj, subs=()):
    """Stable form: ids -> placeholders, dates -> <date>, temporary paths -> <home>, backslashes -> slashes, keys sorted."""
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    text = text.replace("\\\\", "/")
    for old, new in subs:
        text = text.replace(old.replace("\\", "/"), new)
    text = _DATE.sub("<date>", text)
    return json.loads(text)


def _state(f):
    pv = f.get("pv") or {}
    return {"stage": f.get("stage"), "status": f.get("status"), "rev": f.get("rev"), "auto": f.get("auto"),
            "pv": {k: pv.get(k) for k in sorted(pv) if k in ("checking", "verdict", "round", "fixes", "retries")},
            "kind": f.get("kind"), "closed": bool(f.get("closed"))}


def capture(kind: str, scenario: str) -> dict:
    steps = SCENARIOS[kind][scenario]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rec, commits, archived, ctl = Recorder(), [], [], {"fail": False}

        def committer(path, message):
            commits.append({"path": str(path), "message": message})
            return (False, "boom") if ctl["fail"] else (True, "abc1234")

        def publisher(*args):
            archived.append([str(a) for a in args])
            return "published"

        fs = Features(root, rec.launch, rec.is_active, auto_review=True, committer=committer, publisher=publisher,
                      route=_route, tools=_tools)
        f = fs.create("Alfa", "Login with 2FA", "I want 2FA", None, lang=steps.get("lang", "en"), kind=kind)
        fid, trace, seen = f["id"], [], 0
        def snap(action, error=""):
            nonlocal seen
            cur = next(x for x in fs.list() if x["id"] == fid)
            trace.append({"action": action, "error": error, "launches": rec.calls[seen:], "state": _state(cur)})
            seen = len(rec.calls)
        snap(["create"])
        for act in steps["actions"]:
            op, error = act[0], ""

            if op == "finish":
                rec.stop_all()
                d = root / "features" / fid
                d.mkdir(parents=True, exist_ok=True)
                (d / act[1]).write_text(act[2], encoding="utf-8")
            elif op == "mkwork":
                (root / "features" / fid / "work").mkdir(parents=True, exist_ok=True)
            elif op == "commit_fails":
                ctl["fail"] = True
            else:
                try:
                    if op in ("approve", "cancel", "retry"):
                        getattr(fs, op)(fid)
                    else:
                        (fs.revise if op == "revise" else fs.request_fixes)(fid, act[1])
                except FeatureError as e:      # a refusal (gate, wrong state) is part of the behaviour to keep
                    error = str(e)
            fs.list()   # the same refresh the server ticker does
            snap(list(act[:2]) if op != "finish" else ["finish", act[1]], error)
        final = next(x for x in fs.list() if x["id"] == fid)
        out = {"trace": trace, "history": [{"event": h.get("event"), "stage": h.get("stage"), "note": h.get("note", "")}
                                           for h in final.get("history", [])], "commits": commits, "archived": archived}
        return normalize(out, [(str(root), "<home>"), (fid, "<fid>")])


def golden_path(kind, scenario) -> Path:
    return GOLDEN / f"{kind}-{scenario}.json"


def main():
    GOLDEN.mkdir(exist_ok=True)
    for kind, scs in SCENARIOS.items():
        for name in scs:
            golden_path(kind, name).write_text(json.dumps(capture(kind, name), indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                               encoding="utf-8", newline="\n")
            print("wrote", golden_path(kind, name).name)


if __name__ == "__main__":
    main()
