"""Explicit, ordered migrations of the user's data (the files in the Chief's home `state/` folder).

Each step brings data written by the previous schema to the next one. `home.ensure()` backs the data up and runs the steps that
are missing, so the application code only has to understand the current schema. To change a data format: add a step here, bump
CURRENT, and write a test with the old data.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# schema 0 -> 1: the Portuguese keys of the session to-dos, and the Portuguese history events of the features
PENDING_KEYS = {"estado": "status", "resumo": "summary", "falta": "missing", "sessao": "session", "data": "date", "criado": "created"}
EVENTS = {
    "worker lançado": "worker launched", "terminou": "finished", "falhou": "failed", "worker ainda ativo": "worker still active",
    "aprovado": "approved", "alterações pedidas": "changes requested", "correções pedidas": "fixes requested",
    "correções automáticas": "automatic fixes", "revisão automática": "automatic review",
    "revisão automática falhou": "automatic review failed", "comitado": "committed", "commit falhou": "commit failed",
    "nada para comitar": "nothing to commit", "limite de correções automáticas": "automatic fixes limit",
    "revisão repetida": "review repeated", "revisão sem veredicto": "review without verdict",
    "correções automáticas falharam": "automatic fixes failed", "cancelado": "cancelled", "validação do plano": "plan validation",
    "plano validado": "plan validated", "correções do plano": "plan fixes", "limite de correções do plano": "plan fixes limit",
    "validação do plano falhou": "plan validation failed", "análise arquivada": "analysis archived",
    "arquivo da análise falhou": "analysis archiving failed", "spec arquivada": "spec archived", "plano arquivado": "plan archived",
    "arquivo da spec falhou": "spec archiving failed", "arquivo do plano falhou": "plan archiving failed",
    "validação sem veredicto": "validation without verdict", "validação repetida": "validation repeated",
    "correções do plano falharam": "plan fixes failed", "restaurada": "restored",
    "testes do revisor sem confirmação": "reviewer's tests not confirmed",
}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def to_schema_1(state: Path) -> None:
    """Session to-dos get English keys (pendentes.json -> pending.json); features get English events and a `lang`."""
    old = state / "pendentes.json"
    if old.is_file():
        items = _read(old, [])
        migrated = [{PENDING_KEYS.get(k, k): v for k, v in i.items()} for i in items if isinstance(i, dict)]
        if not (state / "pending.json").exists():
            _write(state / "pending.json", migrated)
        old.unlink()
    feats = state / "features.json"
    if feats.is_file():
        data = _read(feats, None)
        if isinstance(data, list):
            for f in data:
                f.setdefault("lang", "pt")           # requests saved before the English default were Portuguese
                for h in f.get("history", []):
                    h["event"] = EVENTS.get(h.get("event"), h.get("event"))
            _write(feats, data)


def _iso_now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def to_schema_2(state: Path) -> None:
    """Work sessions: everything recorded before they existed belongs to one placeholder session (`ws-legacy`), so the
    dashboard's history and "Done" start clean while the old requests and workers stay reachable under past sessions."""
    import worksession

    feats = _read(state / "features.json", [])
    workers = _read(state / "workers.json", [])
    stamps = [f.get("created") for f in feats if isinstance(f, dict)] + [w.get("started") for w in workers if isinstance(w, dict)]
    first = min([x for x in stamps if x], default=_iso_now())
    worksession.WorkSessions(state / "work_sessions.json").add_legacy(first, _iso_now())
    if isinstance(feats, list) and feats:
        for f in feats:
            if f.get("status") in ("done", "cancelled") and not f.get("closed"):
                last = (f.get("history") or [{}])[-1].get("at") or f.get("created") or first
                f["closed"] = {"at": last, "session": worksession.LEGACY_ID}
        _write(state / "features.json", feats)
    if isinstance(workers, list) and workers:
        for w in workers:
            w.setdefault("session", worksession.LEGACY_ID)
        _write(state / "workers.json", workers)


def to_schema_3(state: Path) -> None:
    """Pipelines as data: every request gets the definition of its type as a snapshot (`features/<id>/pipeline.json`), the id and hash of
    that definition, the step it is on and its automatic rounds (from the old `pv` / `auto` counters). Idempotent."""
    import pipelines

    feats = _read(state / "features.json", None)
    if not isinstance(feats, list):
        return
    for f in feats:
        if not isinstance(f, dict) or not f.get("id") or "pipeline" in f:
            continue
        kind = f.get("kind", "feature")
        p = pipelines.load(pipelines.builtin_dir() / f"{kind if kind in pipelines.BUILTIN_IDS else 'feature'}.json", builtin=True)
        folder = state / "features" / f["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "pipeline.json").write_text(json.dumps(pipelines.snapshot(p), ensure_ascii=False, indent=1), encoding="utf-8")
        pv, auto, ids = f.get("pv") or {}, f.get("auto") or {}, {s["id"] for s in p.steps}
        old = {"plancheck:blocked": pv.get("fixes", 0), "plancheck:unknown": pv.get("retries", 0),
               "review:blocked": auto.get("fixes", 0), "review:unknown": auto.get("retries", 0)}
        phase = "plan-validation" if pv.get("checking") else f.get("stage")
        here = next((s["id"] for s in p.steps if s["type"] != "approval" and s.get("phase") == phase), p.steps[0]["id"])
        f.update(pipeline={"id": p.id, "version": p.data.get("version", 1), "hash": pipelines.digest(p)}, step=here,
                 rounds={k: v for k, v in old.items() if v and k.split(":")[0] in ids})
    _write(state / "features.json", feats)


@dataclass(frozen=True)
class Step:
    version: int
    name: str
    run: Callable[[Path], None]


STEPS = (Step(1, "English keys and events", to_schema_1), Step(2, "Work sessions", to_schema_2),
         Step(3, "Pipelines as data", to_schema_3))
CURRENT = STEPS[-1].version


def pending(from_version: int):
    """The steps still to run for data at `from_version`, in order."""
    return [s for s in STEPS if s.version > from_version]
