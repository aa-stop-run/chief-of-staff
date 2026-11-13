#!/usr/bin/env python3
"""Chief of Staff dashboard: the state of every project and the feature pipeline. 127.0.0.1 only.

Reading: GET /api/state. Writing (approvals, launching workers): POST /api/features[/<id>/<action>],
only with a per-start token (X-Token header), the dashboard's own Origin and a small JSON body.

Usage: python app/server.py [--port 8780] [--home <data folder>] [--demo]

--demo starts on sample data in a throw-away folder with a simulated Claude Code: nothing real is read or launched.
"""
import argparse
import atexit
import json
import os
import secrets
import shutil
import sys
import tempfile
from datetime import datetime
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP.parent / "scripts"))
if "--home" in sys.argv[:-1]:        # must be known before `chief` reads the configuration
    os.environ["CHIEF_HOME"] = sys.argv[sys.argv.index("--home") + 1]
DEMO = "--demo" in sys.argv
if DEMO:                              # the data folder must exist before `chief` reads its configuration
    import demo  # noqa: E402
    _DEMO_DIR = demo.prepare_dir(os.environ.get("CHIEF_DEMO_DIR") or tempfile.mkdtemp(prefix="chief-demo-"))
    atexit.register(shutil.rmtree, _DEMO_DIR, True)
    os.environ["CHIEF_HOME"] = str(demo.seed(_DEMO_DIR))
import chief  # noqa: E402
import home  # noqa: E402
import settings  # noqa: E402
from curation import CurationError, Curator  # noqa: E402
from features import FeatureError, Features  # noqa: E402
from i18n import request_lang, translate  # noqa: E402
from onboarding import Onboarding  # noqa: E402
from pending import Pending, PendingError  # noqa: E402
from proposals import ProposalError, Proposals  # noqa: E402
import planusage  # noqa: E402
import projectpage  # noqa: E402
import projectview  # noqa: E402
import report  # noqa: E402
import usage  # noqa: E402

if DEMO:                              # swap `claude`, the GitHub CLI, terminals and cloning for harmless stand-ins
    demo_runtime = demo.install(chief, settings)

TOKEN = secrets.token_urlsafe(24)
MAX_BODY = 16_384
GRACE_S = 15
PORT = chief.CFG.get("dashboard_port", 8780)

_live = {"map": None}  # id -> state; None = claude could not be queried (assumed active)
_live_lock = threading.Lock()


def _is_active(worker_id: str) -> bool:
    m = _live["map"]
    return True if m is None else m.get(worker_id) in chief.ACTIVE


def _is_busy(worker_id: str) -> bool:
    """Working right now (not just a session left open waiting for input)."""
    m = _live["map"]
    return True if m is None else m.get(worker_id) in chief.BUSY


SESSIONS = chief.work_sessions()                  # work sessions: what "Done" and the history are scoped to
LEDGER = usage.Ledger(chief.STATE / "usage.json")  # what each worker cost, measured from the Claude Code logs
FEATURES = Features(chief.STATE, chief.launch_worker, _is_active, grace_s=GRACE_S, is_busy=_is_busy,
                    on_release=chief.stop_worker, auto_review=True, committer=chief.commit_worktree,
                    publisher=chief.archive_doc, note_lang=chief.SCHEMA["name"],
                    has_vault=chief.vault_enabled(), session=SESSIONS.current_id, on_launch=chief.tag_worker, limit=chief.worker_limit, route=chief.route, tools=chief.tools_for)
TICK_S = 10
PENDING = Pending(chief.STATE)
PROPOSALS = Proposals(chief.STATE)          # requests that workers suggested; only the user turns one into a request


def _curator_worker_state(worker_id: str):
    m = _live["map"]
    return None if m is None else m.get(worker_id, "finished")


CURATOR = Curator(chief.VAULT, chief.STATE / "curation.json", chief.launch_curator, _curator_worker_state,
                  chief.commit_worktree, chief.stop_worker, schema=chief.SCHEMA, limit=chief.worker_limit)


ONBOARD = Onboarding(chief.STATE / "onboarding.json", chief.launch_worker, _is_active,
                     lambda: CURATOR.start(force=True), lambda: CURATOR.status()["running"], chief.stop_worker,
                     schema=chief.SCHEMA)


def _onboard(project: dict) -> str:
    """Launch the new project's extraction into the vault; if it fails the project stays registered anyway."""
    if not chief.vault_enabled():
        return "no vault configured: the project was added without a note"
    try:
        if ONBOARD.start(project, chief.VAULT).get("waiting"):
            return (f"to extract the information into the vault, open claude once in {project['path']} and accept the folder trust: "
                    "the extraction then starts by itself")
        return "extracting the project's information into the vault"
    except Exception as e:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        return f"could not launch the extraction into the vault ({str(e)[:120]})"


_cache = {"at": 0.0, "state": None}
CACHE_S = 4


def invalidate_cache() -> None:
    _cache["at"] = 0.0


def build_state() -> dict:
    with _live_lock:
        if _cache["state"] is not None and time.monotonic() - _cache["at"] < CACHE_S:
            return _cache["state"]
        state = _build_state_uncached()
        _cache.update(at=time.monotonic(), state=state)
        return state


def _limited_workers(sessions: list) -> list:
    """The running workers a usage limit has stopped, each with how long until it resets (None if the notice gives no time)."""
    now = datetime.now()
    out = []
    for s in sessions:
        if chief.session_state(s) not in chief.ACTIVE:
            continue
        n = chief.worker_limit(s["id"])
        if n:
            out.append({"id": s["id"], "kind": n.get("kind", "session"), "auto": bool(n.get("auto")),
                        "resumes_in_s": planusage.resumes_in(n, now)})
    return out


def _in_scope(f: dict, session_id: str) -> bool:
    """Open requests always belong on the dashboard; finished or cancelled ones only in the session that closed them."""
    return report.in_scope(f, session_id)


def _session_summary(session_id: str, in_scope: list) -> dict:
    """The small block the header shows: how long, how much (estimated), how many requests closed."""
    cur = SESSIONS.current() or {}
    t = usage.aggregate(LEDGER.load(), session=session_id)
    return {"id": session_id, "started": cur.get("started", ""), "active_s": SESSIONS.active_seconds(cur) if cur else 0,
            "cost_usd": t["cost_usd"], "unpriced": bool(t["unpriced_models"]), "workers": t["workers"],
            "closed": sum(1 for f in in_scope if f.get("status") in ("done", "cancelled"))}


def _measure_costs() -> None:
    """Read the Claude Code logs of the workers and update the ledger (the demo has nothing real to read)."""
    if not DEMO:
        LEDGER.refresh(chief.load_workers(), chief.CFG.get("prices"))


def _plan_block(ledger: dict) -> dict:
    return report.plan_summary(ledger, chief.plan_view())


def _costs_report() -> dict:
    ledger = LEDGER.load()
    return {**report.build(SESSIONS, ledger, FEATURES.list(), chief.load_workers(), chief.CFG.get("prices"), {**_plan_block(ledger), "setup": chief.plan_setup()})}


GIT_CACHE = projectpage.GitCache()   # git is never run per row per refresh: 60 s per project
_WORKER_KEYS = ("id", "name", "state", "model", "started", "phase", "request")


def find_project(name: str) -> dict | None:
    """The visible project with exactly this name whose folder exists (hidden domains are never served)."""
    if not name:
        return None
    same = [x for x in chief.load_projects() if x["name"] == name]
    p = same[0] if len(same) == 1 else None      # two projects with one name cannot be told apart: served by neither
    return p if p and p.get("path") and Path(p["path"]).is_dir() else None


def _feats_with_cost(ledger: dict) -> list:
    costs = usage.request_costs(ledger)
    return [{**f, "cost": costs.get(f["id"])} for f in FEATURES.list()]


def project_rows() -> list:
    ledger, pending = LEDGER.load(), PENDING.list()
    feats = _feats_with_cost(ledger)
    rows, projects = [], chief.load_projects()
    dup = projectpage.duplicate_names(projects)
    with_folder = [p for p in projects if p.get("path") and Path(p["path"]).is_dir()]
    gits = projectpage.git_for_paths(GIT_CACHE, [p["path"] for p in with_folder], chief.git_info)
    for p in projects:
        git = gits.get(p["path"], {"git": False}) if p in with_folder else {"git": False}
        fixes = projectview.fixes(p["name"], feats, FEATURES.artifacts, pending)
        cost = usage.aggregate(ledger, project=p["name"])["cost_usd"]
        rows.append(projectpage.project_row(p, git, feats, len(fixes), cost, p["name"] in dup))
    return sorted(rows, key=lambda r: (r["attention"], r["name"].lower()))


def project_page(p: dict) -> dict:
    ledger = LEDGER.load()
    note = Path(p["hub"]) if chief.vault_enabled() and p.get("hub") else None
    workers = [{k: w.get(k) for k in _WORKER_KEYS} for w in build_state()["workers"] if w.get("project") == p["name"]]
    todos = [{"id": t.get("id"), "title": projectview.redact(str(t.get("title", ""))), "status": t.get("status"),
              "summary": projectview.redact(str(t.get("summary", "")))}
             for t in PENDING.list() if t.get("project") == p["name"]]
    for w in workers:
        w["project"] = p["name"]
    page = projectpage.project_detail(p, GIT_CACHE.get(p["path"], chief.git_info), note, _feats_with_cost(ledger),
                                      FEATURES.artifacts, [{**t, "project": p["name"]} for t in todos], workers,
                                      usage.request_costs(ledger), usage.aggregate(ledger, project=p["name"]))
    page["workers"] = [{k: w[k] for k in _WORKER_KEYS} for w in page["workers"]]
    page["todos"] = [{k: t[k] for k in ("id", "title", "status", "summary")} for t in page["todos"]]
    return page


def _end_session() -> dict:
    """Freeze the open session's totals into its record and open a fresh one."""
    _measure_costs()
    sid = SESSIONS.current_id()
    out = SESSIONS.end(report.totals_now(LEDGER.load(), sid))
    rep = _costs_report()
    return {"ended": next(s for s in rep["sessions"] if s["id"] == out["ended"]["id"]), "current": rep["current"]}


def _build_state_uncached() -> dict:
    sess = chief.sessions_checked()
    _live["map"] = None if sess is None else {s["id"]: chief.session_state(s) for s in sess}
    state = chief.full_state(sess or [], with_git=False)
    request_costs = usage.request_costs(LEDGER.load())      # a request's cost is all its workers', across phases and work sessions
    feats = [{**f, "worker_state": (_live["map"] or {}).get(f["worker_id"], ""), "cost": request_costs.get(f["id"]),
              "artifacts": FEATURES.artifacts(f["id"]), "questions": FEATURES.questions(f["id"]),
              "qa": FEATURES.qa_of(f)} for f in FEATURES.list()]
    visible = {p["name"] for p in state["projects"]}  # projects in hidden domains appear nowhere
    cur = SESSIONS.current_id()
    in_scope = [f for f in feats[::-1] if f["project"] in visible and _in_scope(f, cur)]
    state["features"] = in_scope
    state["earlier_closed"] = sum(1 for f in feats if f["project"] in visible and not _in_scope(f, cur))
    # the workers' history is this session's: workers still running are always shown, finished ones only if launched in this session
    state["workers"] = [w for w in state["workers"] if w["project"] in visible and (w.get("state") in chief.ACTIVE or w.get("session") == cur)]
    state["pending"] = [x for x in PENDING.list() if x["project"] in visible]
    state["proposals"] = [x for x in PROPOSALS.list() if x["project"] in visible]
    state["claude_ok"] = sess is not None
    state["models"] = chief.models()
    state["vault"] = chief.vault_enabled()
    state["demo"] = DEMO
    state["curation"] = CURATOR.status()
    state["plan"] = _plan_block(LEDGER.load())
    state["limits"] = _limited_workers(sess or [])
    state["session"] = _session_summary(cur, in_scope)
    state["ui"] = str((APP / "index.html").stat().st_mtime_ns)
    return state


def _token_ok(query: dict) -> bool:
    """The `t` parameter of a parsed query string equals the start-up token (constant-time comparison)."""
    return secrets.compare_digest(query.get("t", [""])[0].encode("utf-8"), TOKEN.encode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    _lang = "en"

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def _origin_ok(self) -> bool:
        return self.headers.get("Origin", "") in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._send(403, b"host", "text/plain")
        u = urlparse(self.path)
        if u.path == "/":
            html = (APP / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/logo.svg":
            return self._send(200, (APP / "logo.svg").read_bytes(), "image/svg+xml")
        if u.path == "/api/state":
            if not _token_ok(parse_qs(u.query)):
                return self._send(403, b"token", "text/plain")
            return self._json(200, build_state())
        if u.path == "/api/costs":
            if not _token_ok(parse_qs(u.query)):
                return self._send(403, b"token", "text/plain")
            return self._json(200, _costs_report())
        if u.path == "/api/settings":
            if not _token_ok(parse_qs(u.query)):
                return self._send(403, b"token", "text/plain")
            return self._json(200, settings.snapshot())
        if u.path in ("/api/projects", "/api/project", "/api/project/doc"):
            q = parse_qs(u.query)
            if not _token_ok(q):
                return self._send(403, b"token", "text/plain")
            self._lang = request_lang(self.headers.get("X-Lang"))
            return self._project_route(u.path, q)
        self._send(404, b"not found", "text/plain")

    def _project_route(self, path: str, q: dict) -> None:
        try:
            if path == "/api/projects":
                return self._json(200, {"projects": project_rows()})
            p = find_project(q.get("name", [""])[0])
            if p is None:
                return self._err(404, "document not found" if path == "/api/project/doc" else "project not found")
            if path == "/api/project":
                return self._json(200, project_page(p))
            try:
                root, rel = Path(p["path"]), q.get("path", [""])[0].replace("\\", "/")
                if rel in {d["path"] for d in projectview.list_docs(root)}:     # only what the page lists
                    return self._json(200, projectview.read_doc(root, rel))
            except (ValueError, OSError):
                pass
            return self._err(404, "document not found")
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._err(500, "internal error (see the server console)")

    def _err(self, code: int, msg) -> None:
        """Error for the user, in the language the dashboard asked for (X-Lang header)."""
        self._json(code, {"error": translate(msg, self._lang)})

    def do_POST(self) -> None:
        self._lang = request_lang(self.headers.get("X-Lang"))
        if not (self._host_ok() and self._origin_ok()):
            return self._err(403, "origin not allowed")
        if not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            return self._err(403, "invalid token; reload the page")
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._err(415, "JSON expected")
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= MAX_BODY:
                return self._err(413, "empty or too large body")
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("object expected")
        except (ValueError, UnicodeDecodeError):
            return self._err(400, "invalid JSON")
        try:
            result = self._route(urlparse(self.path).path, body)
            invalidate_cache()
            self._json(200, result)
        except (FeatureError, PendingError, ProposalError) as e:
            self._err(400, str(e))
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._err(500, "internal error (see the server console)")

    def _pending_action(self, pid: str, action: str):
        if action == "dismiss":
            return PENDING.dismiss(pid)
        item = PENDING.get(pid)
        if action == "resume":
            try:
                chief.open_session(item["project"], item["session"])
            except (ValueError, RuntimeError) as e:
                raise PendingError(str(e)) from e
            return {"ok": True}
        if action == "feature":
            label = "Falta" if self._lang == "pt" else "Missing"
            missing = f"\n\n{label}: {item['missing']}" if item["missing"] else ""
            desc = item["summary"] + missing
            f = FEATURES.create(item["project"], item["title"], desc, None, lang=self._lang)
            PENDING.dismiss(pid)
            return f
        raise PendingError("unknown action")

    @staticmethod
    def _settings_action(action: str, body: dict):
        """Settings (vault, folders, categories, git hosts): each action validates and saves to projects.json."""
        name, path = str(body.get("name", "")), body.get("path")
        if action == "domain":
            return settings.set_domain(name, body.get("domain"))
        if action == "vault":
            return settings.set_vault(path)
        if action == "root_add":
            return settings.add_root(path)
        if action == "root_remove":
            return settings.remove_root(path)
        if action == "ignore_remove":
            return settings.set_ignored(name, False)
        if action == "project_remove":
            return settings.remove_project(name)
        if action == "extra_move":
            return settings.move_extra(name, path)
        if action == "hosts":
            return settings.set_clone_hosts(body.get("hosts"))
        if action == "resume_after_limit":
            return settings.set_resume_after_limit(body.get("on"))
        if action == "plan_connect":
            return settings.connect_plan()
        if action == "plan_disconnect":
            return settings.disconnect_plan()
        raise FeatureError("unknown action")

    def _route(self, path: str, body: dict):
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "settings"] and len(parts) == 3:
            try:
                return self._settings_action(parts[2], body)
            except ValueError as e:
                raise FeatureError(str(e)) from e
        if parts == ["api", "projects", "clone"]:
            try:
                p = chief.clone_project(body.get("url"), body.get("domain"))
            except ValueError as e:
                raise FeatureError(str(e)) from e
            return {"ok": True, "name": p["name"], "extraction": translate(_onboard(p), self._lang)}
        if parts == ["api", "projects"]:
            try:
                p = chief.add_project(body.get("path"), body.get("domain"))
            except ValueError as e:
                raise FeatureError(str(e)) from e
            return {"ok": True, "name": p["name"], "extraction": translate(_onboard(p), self._lang)}
        if DEMO and parts == ["api", "demo", "limit"]:        # demo only: pretend a worker hit (or left) a usage limit
            return {"limit": demo_runtime.set_limit(str(body.get("worker", "")), bool(body.get("on", True)), bool(body.get("auto")))}
        if parts == ["api", "session", "end"]:
            return _end_session()
        if parts == ["api", "inbox", "curate"]:
            if not chief.vault_enabled():
                raise PendingError("no vault configured")
            try:
                CURATOR.start(force=True)
            except CurationError as e:
                raise PendingError(str(e)) from e
            return {"ok": True}
        if parts[:2] == ["api", "proposals"] and len(parts) == 4 and parts[3] == "dismiss":
            return PROPOSALS.dismiss(parts[2])            # accepting needs no call: the dashboard opens the new-request dialog and then dismisses
        if parts[:2] == ["api", "pending"] and len(parts) == 4:
            return self._pending_action(parts[2], parts[3])
        if parts[:2] != ["api", "features"]:
            raise FeatureError("unknown route")
        if len(parts) == 2:
            names = {p["name"] for p in chief.load_projects()}
            if body.get("project") not in names:
                raise FeatureError("unknown project")
            return FEATURES.create(body["project"], str(body.get("title", "")), str(body.get("desc", "")),
                                   body.get("model") or None, lang=self._lang, kind=str(body.get("kind") or "feature"))
        if len(parts) == 4:
            fid, action = parts[2], parts[3]
            if action == "approve":
                return FEATURES.approve(fid)
            if action == "revise":
                return FEATURES.revise(fid, str(body.get("comment", "")))
            if action == "fix":
                return FEATURES.request_fixes(fid, str(body.get("comment", "")), selected=bool(body.get("selected")))
            if action == "ask":
                return FEATURES.ask(fid, str(body.get("question", "")))
            if action == "retry":
                return FEATURES.retry(fid)
            if action == "cancel":
                return FEATURES.cancel(fid)
        raise FeatureError("unknown route")


COST_EVERY = 6                     # the costs are measured every 6th tick (a minute): reading the logs is the expensive part
ticks = {"n": 0}


def _ticker() -> None:
    """Advance the features (automatic review, fixes, commit) even with the dashboard closed."""
    while True:
        time.sleep(TICK_S)
        try:
            SESSIONS.heartbeat()
            if chief.CFG.get("resume_after_limit", True):
                FEATURES.auto_resume()
            ticks["n"] += 1
            if ticks["n"] % COST_EVERY == 0:
                _measure_costs()
            build_state()
            if not DEMO and chief.vault_enabled():
                ONBOARD.tick()
                CURATOR.tick()
        except Exception:  # noqa: BLE001 - the ticker must never die
            traceback.print_exc(file=sys.stderr)


def main() -> None:
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=demo.DEMO_PORT if DEMO else PORT)
    ap.add_argument("--demo", action="store_true", help="sample data and a simulated Claude Code (nothing real runs)")
    ap.add_argument("--home", help="folder with the user's data (default: CHIEF_HOME or the per-user data folder)")
    PORT = ap.parse_args().port
    try:
        home.ensure(chief.HOME)     # create the data folder, import old in-tree data, migrate older schemas
    except home.HomeError as e:
        sys.exit(str(e))
    try:
        SESSIONS.ensure()             # a session is always open while the server runs
        FEATURES.publish_approved()   # specs and plans approved before the archive existed (once per document)
    except Exception:  # noqa: BLE001 - archiving is an extra: it never prevents startup
        traceback.print_exc(file=sys.stderr)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Chief of Staff at http://127.0.0.1:{PORT}  (Ctrl+C to stop)\nData folder: {chief.HOME}"
          + ("\nDEMO MODE: sample data, simulated workers; the folder is deleted on exit." if DEMO else ""))
    threading.Thread(target=_ticker, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
