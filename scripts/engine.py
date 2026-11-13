"""Pipeline engine: pure decisions over a request's state. It never launches, writes or runs anything: it answers «what happens
next» for a pipeline definition, and whoever calls it applies the answer. State kept on the request: f["step"] (current step
id) and f["rounds"] (counters per step/verdict)."""
from dataclasses import dataclass

import gates
import pipelines

DEFAULT_MAX_TOTAL_ROUNDS = 12
# Always added to a step's own permissions, whatever the pipeline says (never removable).
FIXED_DENY = ("Bash(git push --force:*)", "Bash(git push -f:*)", "Bash(git push --mirror:*)", "Bash(gh pr merge:*)",
              "Bash(gh auth:*)")
BLOCKING_GATES = {"diverged", "notrun", "regress"}       # the user decides: neither a retry nor a fix round helps


@dataclass(frozen=True)
class Decision:
    kind: str                 # launch | wait_user | await | done
    step: str = ""            # the step to launch / wait on
    feedback: str = ""        # a feedback key for the launch ("review", "comment", ...), "" for none
    log: str = ""             # what to write in the request's history
    action: str = ""          # a closed action to perform first (commit_worktree)


def effective_permissions(step: dict) -> tuple:
    """(allow, deny) for a step: its own rules, plus the fixed denials."""
    perm = step.get("permissions") or {}
    deny = list(perm.get("deny", []))
    deny += [d for d in FIXED_DENY if d not in deny]
    return list(perm.get("allow", [])), deny


class Engine:
    def __init__(self, pipeline: "pipelines.Pipeline"):
        self.p = pipeline
        self._ids = [s["id"] for s in pipeline.steps]

    def first_step(self) -> dict:
        return self.p.steps[0]

    def step(self, sid: str) -> dict:
        return self.p.step(sid)

    def _following(self, step: dict, explicit=None):
        target = explicit or step.get("next")
        if target == pipelines.END:
            return None
        if target:
            return self.step(target)
        i = self._ids.index(step["id"]) + 1
        return self.p.steps[i] if i < len(self.p.steps) else None

    def _go(self, nxt, log: str = "", action: str = "") -> Decision:
        if nxt is None:
            return Decision("done", log=log or "pipeline finished", action=action)
        if nxt["type"] == "approval":
            return Decision("wait_user", nxt["id"], log=log, action=action)
        return Decision("launch", nxt["id"], log=log, action=action)

    @staticmethod
    def _rounds(f: dict) -> dict:
        return f.setdefault("rounds", {})

    def _total_exhausted(self, f: dict) -> bool:
        cap = (self.p.data.get("limits") or {}).get("max_total_rounds", DEFAULT_MAX_TOTAL_ROUNDS)
        return sum(self._rounds(f).values()) >= cap

    def on_worker_finished(self, f: dict, step: dict) -> Decision:
        """A plain worker step ended: continue to the following step. Approvals are never advanced here, and a check goes
        through `on_check`."""
        if step["type"] == "approval":
            raise ValueError("an approval step is only advanced by the user")
        if step["type"] == "check":
            raise ValueError("a check step is resolved with on_check")
        return self._go(self._following(step))

    def on_check(self, f: dict, step: dict, verdict: str, gate_codes=()) -> Decision:
        """A check step ended with a reviewer's verdict and the results of its gates."""
        if step["type"] != "check":
            raise ValueError("not a check step")
        codes = set(gate_codes) - {"ok"}
        if codes & BLOCKING_GATES:
            return Decision("await", step["id"], log="gates not confirmed: " + ", ".join(sorted(codes & BLOCKING_GATES)))
        if verdict in ("approved", "reserves") and codes & {"missing", "unknown"}:
            verdict = "unknown"
        if verdict in ("approved", "reserves") and "blocked" in codes:
            verdict = "blocked"
        t = (step.get("on") or {}).get(verdict)
        r = self._rounds(f)
        if t is None:
            if verdict in ("approved", "reserves"):
                return self._go(self._following(step))
            t = {"retry": 2} if verdict == "unknown" else {}
        if isinstance(t, str):
            t = {"goto": t, "max_rounds": 1} if self._ids.index(t) <= self._ids.index(step["id"]) else {"next": t}
        action = t.get("do", "")
        key = f"{step['id']}:{verdict}"
        if "goto" in t:
            if r.get(key, 0) >= t["max_rounds"] or self._total_exhausted(f):
                return Decision("await", step["id"], log=f"{r.get(key, 0)} rounds without approval: it is up to you")
            r[key] = r.get(key, 0) + 1
            return Decision("launch", t["goto"], feedback=step["id"], log=f"round {r[key]} of {t['max_rounds']}", action=action)
        if "retry" in t:
            if r.get(key, 0) >= t["retry"] or self._total_exhausted(f):
                return Decision("await", step["id"], log=f"no readable verdict after {r.get(key, 0)} retries: read it and decide")
            r[key] = r.get(key, 0) + 1
            return Decision("launch", step["id"], log=f"repeated ({r[key]} of {t['retry']})")
        return self._go(self._following(step, t.get("next")), action=action)

    def advance(self, step: dict) -> Decision:
        """The step after `step` in the flow, whatever its type (what «move on» means when no approval comes in between)."""
        return self._go(self._following(step))

    def on_approve(self, f: dict, step: dict) -> Decision:
        if step["type"] != "approval":
            raise ValueError("not an approval step")
        return self._go(self._following(step))

    def on_request_changes(self, f: dict, step: dict, comment: str) -> Decision:
        """The user asks for changes at an approval: the step it approves (`of`) runs again with the comment."""
        if step["type"] != "approval":
            raise ValueError("not an approval step")
        target = (step.get("on_changes") or {}).get("goto") or step.get("of")
        if not target:
            raise ValueError("the approval does not say which step to repeat")
        return Decision("launch", target, feedback="comment", log=comment)


def gate_codes(step: dict, ctx: "gates.GateCtx") -> list:
    """Run a check step's catalogue gates (names and arguments come from the definition; no code from the file runs)."""
    return [gates.run(g["use"], g.get("args", {}), ctx).code for g in step.get("gates", [])]

