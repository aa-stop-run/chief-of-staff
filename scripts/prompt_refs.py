"""Built-in prompts, by key `builtin:<kind>.<phase>`: the pipelines' JSON files point here, the text stays in the prompts*.py
modules. `render` produces exactly what `Features._task` / the plan validation launch produced before pipelines were data."""
from pathlib import Path

import fanout
import prompts
import prompts_kinds
import prompts_pt

ARTIFACT = {"spec": "spec.md", "plan": "plan.md", "build": "report.md", "review": "review.md"}
QUESTIONS_FILE = "questions.json"
PLAN_REVIEW = "plan-review.md"
PHASES = ("spec", "plan", "build", "review", "plancheck")
MAX_PLAN_TASKS = 12   # above this many ordered steps the planner plans a first part and the validator proposes a split


def _module(lang: str):
    return prompts if lang == "en" else prompts_pt


def refs() -> tuple:
    """Every key that exists: each kind's phases, and `plancheck` for the kinds that validate a plan."""
    out = []
    for kind, stages in prompts_kinds.KIND_STAGES.items():
        out += [f"builtin:{kind}.{s}" for s in stages]
        if "plan" in stages and kind not in ("release",):
            out.append(f"builtin:{kind}.plancheck")
    return tuple(out)


REFS = refs()


def spec_ref(kind: str, lang: str, d: Path):
    """The spec's path; kinds without a spec say the requirement is the user's request."""
    return d / ARTIFACT["spec"] if kind in ("feature", "fix") else prompts_kinds.NO_SPEC[lang]


def render(ref: str, lang: str, ctx: dict) -> str:
    """ctx: title, fid, project, desc, dir (Path of the request's artifacts), rev (artifact version counter of the phase),
    feedback, note_lang, has_vault."""
    if ref not in REFS:
        raise KeyError(f"unknown prompt reference: {ref}")
    kind, phase = ref[len("builtin:"):].split(".")
    P, d = _module(lang), Path(ctx["dir"])
    if phase == "plancheck":
        base = P.language_rule(ctx["note_lang"]) + P.PLANCHECK
        extra = prompts_kinds.EXTRA.get((lang, kind, "plancheck"))
        if extra:
            base = base.replace(P.OUT_MARKER, extra + "\n" + P.OUT_MARKER, 1)
        size = P.PLANCHECK_SIZE.replace("{max}", str(MAX_PLAN_TASKS))
        if kind in fanout.FAN_OUT_KINDS and not ctx.get("part"):       # SPLIT_CHECK has no braces: it may go before .format
            size += "\n" + P.SPLIT_CHECK
        base = base.replace(P.OUT_MARKER, size + "\n" + P.OUT_MARKER, 1)
        return base.format(title=ctx["title"], fid=ctx["fid"], project=ctx["project"], desc=ctx["desc"],
                           spec=spec_ref(kind, lang, d), plan=d / ARTIFACT["plan"], out=d / PLAN_REVIEW) + P.BRIEF
    text = P.language_rule(ctx["note_lang"]) + P.COMMON.format(
        title=ctx["title"], fid=ctx["fid"], project=ctx["project"], desc=ctx["desc"],
        path=d / ARTIFACT[phase], qpath=d / QUESTIONS_FILE) + "\n\n"
    template = prompts_kinds.OVERRIDE.get((lang, kind, phase)) or (
        P.BUILD_RESUME if phase == "build" and (d / "work").exists() else P.STAGE_TEXT[phase])
    extra = prompts_kinds.EXTRA.get((lang, kind, phase))
    if extra:
        template += "\n" + extra
    text += template.format(fid=ctx["fid"], spec=spec_ref(kind, lang, d), plan=d / ARTIFACT["plan"],
                            report=d / ARTIFACT["build"], work=d / "work")
    if phase == "plan" and kind != "release":          # a release plan is a checklist, not a build to be sized
        text += P.PLAN_SIZE.replace("{max}", str(MAX_PLAN_TASKS))
        if kind in fanout.FAN_OUT_KINDS and not ctx.get("part"):       # JSON braces: appended after .format, like PLAN_SIZE
            text += P.SPLIT_FORMAT
    if ctx.get("base") and phase == "plan":          # a part that builds on earlier parts: where their code is
        text += P.BASE_PLAN.format(base=ctx["base"])
    if ctx.get("base") and phase == "build" and not (d / "work").exists():
        text += P.BASE_BUILD.format(base=ctx["base"], fid=ctx["fid"], work=d / "work")
    text += P.BRIEF
    if not ctx.get("has_vault", True):
        text += P.NO_VAULT_NOTE
    if ctx.get("feedback"):
        prev = d / f"{Path(ARTIFACT[phase]).stem}.v{ctx.get('rev', 0)}.md"
        text += P.CHANGES_BLOCK.format(feedback=ctx["feedback"], prev=prev, path=d / ARTIFACT[phase])
    return text
