#!/usr/bin/env python3
"""Chief of Staff dashboard: the state of every project and the feature pipeline. 127.0.0.1 only.

Reading: GET /api/state. Writing (approvals, launching workers): POST /api/features[/<id>/<action>],
only with a per-start token (X-Token header), the dashboard's own Origin and a small JSON body.

Usage: python app/server.py [--port 8780] [--home <data folder>]
"""
import argparse
import json
import os
import secrets
import sys
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
import chief  # noqa: E402
import home  # noqa: E402
import settings  # noqa: E402
from curation import CurationError, Curator  # noqa: E402
from features import FeatureError, Features  # noqa: E402
from i18n import request_lang, translate  # noqa: E402
from onboarding import Onboarding  # noqa: E402
from pending import Pending, PendingError  # noqa: E402

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


FEATURES = Features(chief.STATE, chief.launch_worker, _is_active, grace_s=GRACE_S, is_busy=_is_busy,
                    on_release=chief.stop_worker, auto_review=True, committer=chief.commit_worktree,
                    publisher=chief.archive_doc, note_lang=chief.SCHEMA["name"])
TICK_S = 10
PENDING = Pending(chief.STATE)


def _curator_worker_state(worker_id: str):
    m = _live["map"]
    return None if m is None else m.get(worker_id, "finished")


CURATOR = Curator(chief.VAULT, chief.STATE / "curation.json", chief.launch_curator, _curator_worker_state,
                  chief.commit_worktree, chief.stop_worker, schema=chief.SCHEMA)


ONBOARD = Onboarding(chief.STATE / "onboarding.json", chief.launch_worker, _is_active,
                     lambda: CURATOR.start(force=True), lambda: CURATOR.status()["running"], chief.stop_worker,
                     schema=chief.SCHEMA)


def _onboard(project: dict) -> str:
    """Launch the new project's extraction into the vault; if it fails the project stays registered anyway."""
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


def _build_state_uncached() -> dict:
    sess = chief.sessions_checked()
    _live["map"] = None if sess is None else {s["id"]: s["state"] for s in sess}
    state = chief.full_state(sess or [], with_git=False)
    feats = [{**f, "worker_state": (_live["map"] or {}).get(f["worker_id"], ""),
              "artifacts": FEATURES.artifacts(f["id"]), "questions": FEATURES.questions(f["id"])} for f in FEATURES.list()]
    visible = {p["name"] for p in state["projects"]}  # projects in hidden domains appear nowhere
    state["features"] = [f for f in feats[::-1] if f["project"] in visible]
    state["workers"] = [w for w in state["workers"] if w["project"] in visible]
    state["pending"] = [x for x in PENDING.list() if x["project"] in visible]
    state["claude_ok"] = sess is not None
    state["curation"] = CURATOR.status()
    state["ui"] = str((APP / "index.html").stat().st_mtime_ns)
    return state


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
            if parse_qs(u.query).get("t", [""])[0] != TOKEN:
                return self._send(403, b"token", "text/plain")
            return self._json(200, build_state())
        if u.path == "/api/settings":
            if parse_qs(u.query).get("t", [""])[0] != TOKEN:
                return self._send(403, b"token", "text/plain")
            return self._json(200, settings.snapshot())
        self._send(404, b"not found", "text/plain")

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
        except (FeatureError, PendingError) as e:
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
        if parts == ["api", "inbox", "curate"]:
            try:
                CURATOR.start(force=True)
            except CurationError as e:
                raise PendingError(str(e)) from e
            return {"ok": True}
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
            if action == "retry":
                return FEATURES.retry(fid)
            if action == "cancel":
                return FEATURES.cancel(fid)
        raise FeatureError("unknown route")


def _ticker() -> None:
    """Advance the features (automatic review, fixes, commit) even with the dashboard closed."""
    while True:
        time.sleep(TICK_S)
        try:
            build_state()
            ONBOARD.tick()
            CURATOR.tick()
        except Exception:  # noqa: BLE001 - the ticker must never die
            traceback.print_exc(file=sys.stderr)


def main() -> None:
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--home", help="folder with the user's data (default: CHIEF_HOME or the per-user data folder)")
    PORT = ap.parse_args().port
    try:
        home.ensure(chief.HOME)     # create the data folder, import old in-tree data, migrate older schemas
    except home.HomeError as e:
        sys.exit(str(e))
    try:
        FEATURES.publish_approved()   # specs and plans approved before the archive existed (once per document)
    except Exception:  # noqa: BLE001 - archiving is an extra: it never prevents startup
        traceback.print_exc(file=sys.stderr)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Chief of Staff at http://127.0.0.1:{PORT}  (Ctrl+C to stop)\nData folder: {chief.HOME}")
    threading.Thread(target=_ticker, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
