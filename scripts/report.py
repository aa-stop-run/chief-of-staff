"""The report of work sessions and AI costs: what the dashboard's cost view and `chief.py costs` show.

Pure functions over what the other modules store (the sessions, the usage ledger, the requests and the workers), so they are easy
to test and the server and the command line cannot disagree.
"""
from datetime import datetime, timedelta, timezone

import planusage
import usage


def _requests_closed_in(features: list, session_id: str) -> list:
    out = []
    for f in features:
        closed = f.get("closed") or {}
        if closed.get("session") == session_id:
            out.append({"id": f["id"], "title": f.get("title", ""), "project": f.get("project", ""), "kind": f.get("kind", "feature"),
                        "status": f.get("status", ""), "at": closed.get("at", "")})
    return sorted(out, key=lambda r: r["at"], reverse=True)


def in_scope(feature: dict, session_id: str) -> bool:
    """Does a request belong on the dashboard in this work session? Open requests always do, whenever they were started: a card
    stays open across sessions until it is finished or cancelled. A finished or cancelled one shows only in the session that closed it."""
    if feature.get("status") in ("done", "cancelled"):
        return (feature.get("closed") or {}).get("session") == session_id
    return True


def cards_worked_on(ledger: dict, session_id: str, features: list) -> list:
    """The cards this session's workers worked on (finished or not), with what each cost in this session and what it has cost in all."""
    here = usage.request_costs({k: v for k, v in ledger.items() if v.get("session") == session_id})
    overall = usage.request_costs(ledger)
    rows = []
    for f in features:
        if f["id"] not in here:
            continue
        rows.append({"id": f["id"], "title": f.get("title", ""), "project": f.get("project", ""), "status": f.get("status", ""),
                     "closed_here": (f.get("closed") or {}).get("session") == session_id, "workers": here[f["id"]]["workers"],
                     "cost_session": here[f["id"]]["cost_usd"], "cost_total": overall[f["id"]]["cost_usd"],
                     "unpriced": overall[f["id"]]["unpriced"]})
    return sorted(rows, key=lambda r: (-r["cost_session"], r["title"]))


def session_view(ws, session: dict, ledger: dict, features: list, workers: list) -> dict:
    """One work session with its time, its cost (frozen once it has ended), the requests it closed and the workers it launched."""
    sid = session["id"]
    totals = session.get("totals") if session.get("ended") and session.get("totals") else usage.aggregate(ledger, session=sid)
    return {"id": sid, "started": session.get("started", ""), "ended": session.get("ended"), "ended_by": session.get("ended_by"),
            "legacy": bool(session.get("legacy")), "active_s": ws.active_seconds(session), "cost_usd": totals["cost_usd"],
            "tokens": totals["tokens"], "unpriced_models": totals.get("unpriced_models", []), "by_model": totals["by_model"],
            "by_project": totals["by_project"], "requests": _requests_closed_in(features, sid), "cards": cards_worked_on(ledger, sid, features),
            "workers": sum(1 for w in workers if w.get("session") == sid)}


def requests_view(ledger: dict, features: list) -> list:
    """What every request cost in all (whatever the work sessions it ran in), most expensive first."""
    costs = usage.request_costs(ledger)
    rows = [{"id": f["id"], "title": f.get("title", ""), "project": f.get("project", ""), "kind": f.get("kind", "feature"),
             "status": f.get("status", ""), **costs[f["id"]]} for f in features if f["id"] in costs]
    return sorted(rows, key=lambda r: (-r["cost_usd"], r["title"]))


def plan_summary(ledger: dict, plan_view: dict, now: datetime | None = None) -> dict:
    """The plan allowance as the dashboard shows it: the 5-hour and 7-day percentages (from the status line, if connected) next to
    what the Chief's workers are estimated to have cost over the same spans. The percentages cover everything you do with Claude;
    the dollar figures only the workers the Chief launched."""
    now = now or datetime.now(timezone.utc)
    return {**plan_view, "spend": {"five_hour_usd": usage.spend_in_window(ledger, now - timedelta(hours=5), now),
                                   "seven_day_usd": usage.spend_in_window(ledger, now - timedelta(days=7), now)}}


def totals_now(ledger: dict, session_id: str) -> dict:
    """The numbers frozen into a session's record when it ends."""
    t = usage.aggregate(ledger, session=session_id)
    return {k: t[k] for k in ("cost_usd", "tokens", "workers", "unpriced_tokens", "unpriced_models", "by_model", "by_project", "by_request")}


def projects_view(ledger: dict, sessions: list) -> list:
    """All-time cost per project, with the models used and the number of sessions it appears in, most expensive first."""
    names = sorted({e.get("project") or "" for e in ledger.values()})
    rows = []
    for name in names:
        t = usage.aggregate(ledger, project=name)
        used = {e.get("session") for e in ledger.values() if (e.get("project") or "") == name and e.get("session")}
        rows.append({"project": name, "cost_usd": t["cost_usd"], "tokens": t["tokens"], "workers": t["workers"], "sessions": len(used),
                     "by_model": t["by_model"], "unpriced_models": t["unpriced_models"]})
    return sorted(rows, key=lambda r: (-r["cost_usd"], r["project"]))


def phases_view(ledger: dict) -> list:
    """All-time cost per phase of a request (spec, plan, plan-validation, build, review, qa...) with the models each phase ran on,
    so the model routing can be judged on real spend. Workers that belong to no request are filed under «other»."""
    out = {}
    for entry in ledger.values():
        r = out.setdefault(entry.get("phase") or "other", {"phase": entry.get("phase") or "other", "cost_usd": 0.0, "workers": 0, "by_model": {}})
        r["workers"] += 1
        for model, row in (entry.get("models") or {}).items():
            cost = row.get("cost_usd", 0.0)
            r["cost_usd"] = round(r["cost_usd"] + cost, 6)
            r["by_model"][model] = round(r["by_model"].get(model, 0.0) + cost, 6)
    return sorted(out.values(), key=lambda r: (-r["cost_usd"], r["phase"]))


def build(ws, ledger: dict, features: list, workers: list, overrides: dict | None = None, plan: dict | None = None) -> dict:
    """Everything the cost view needs: the open session, past sessions, all-time per project and per model, and how prices were set."""
    sessions = ws.list()
    cur = ws.current()
    current = session_view(ws, cur, ledger, features, workers) if cur else None
    past = [session_view(ws, s, ledger, features, workers) for s in sessions if not cur or s["id"] != cur["id"]]
    everything = usage.aggregate(ledger)
    return {"plan": plan, "prices": {"as_of": usage.PRICES_AS_OF, "source": usage.PRICES_SOURCE, "custom": sorted((overrides or {}).keys())},
            "current": current, "sessions": past, "projects": projects_view(ledger, sessions), "requests": requests_view(ledger, features), "phases": phases_view(ledger),
            "models": sorted(({"model": m, **v} for m, v in everything["by_model"].items()), key=lambda r: -r["cost_usd"]),
            "total": {"cost_usd": everything["cost_usd"], "tokens": everything["tokens"], "unpriced_models": everything["unpriced_models"]}}


# ---------------------------------------------------------------- text, for the command line
def money(x: float) -> str:
    return f"${x:,.2f}" if x >= 0.01 or x == 0 else f"${x:,.4f}"


def duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}h {m:02d}m" if h else (f"{m}m {sec:02d}s" if m else f"{sec}s")


def tokens_short(n: int) -> str:
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else (f"{n / 1000:.0f}k" if n >= 1000 else str(n))


def render_text(rep: dict) -> str:
    lines = [f"Costs are estimates at list price (prices as of {rep['prices']['as_of']}); your bill may differ.", ""]
    cur = rep["current"]
    if cur:
        lines += [f"This session: {duration(cur['active_s'])} active · {money(cur['cost_usd'])} · {cur['workers']} workers · "
                  f"{len(cur['requests'])} requests closed"]
        for model, v in sorted(cur["by_model"].items(), key=lambda kv: -kv[1]["cost_usd"]):
            lines.append(f"    {model:34} {money(v['cost_usd']):>10}   in {tokens_short(v['input'])} · out {tokens_short(v['output'])}"
                         f" · cache {tokens_short(v['cache_read'])}")
    lines += ["", "By project (all sessions):"]
    for p in rep["projects"]:
        models = ", ".join(sorted(p["by_model"])) or "-"
        lines.append(f"    {p['project'] or '(none)':30} {money(p['cost_usd']):>10}   {p['sessions']} sessions · {models}")
    if rep["total"]["unpriced_models"]:
        lines += ["", "No price for: " + ", ".join(rep["total"]["unpriced_models"]) + " (counted in tokens only; add `prices` to projects.json)"]
    if rep.get("phases"):
        lines += ["", "By phase (all sessions):"]
        for r in rep["phases"]:
            models = ", ".join(f"{m} {money(c)}" for m, c in sorted(r["by_model"].items(), key=lambda kv: -kv[1])) or "-"
            lines.append(f"    {r['phase']:18} {money(r['cost_usd']):>10}   {r['workers']} workers · {models}")
    if rep.get("requests"):
        lines += ["", "By request (all sessions):"]
        for r in rep["requests"][:15]:
            phases = ", ".join(f"{p} {money(v['cost_usd'])}" for p, v in r["by_phase"].items())
            lines.append(f"    {r['title'][:38]:38} {money(r['cost_usd']):>10}   {r['workers']} workers · {r['sessions']} sessions · {phases}")
    lines += ["", f"Past sessions: {len(rep['sessions'])}"]
    for s in rep["sessions"][:10]:
        lines.append(f"    {s['started'][:10]}  {duration(s['active_s']):>8}  {money(s['cost_usd']):>10}  {len(s['requests'])} requests closed"
                     + ("  (before sessions were recorded)" if s["legacy"] else ""))
    return "\n".join(lines)
