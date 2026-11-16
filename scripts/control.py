"""Talking to the running dashboard server from a terminal.

The pipeline lives in the server: it holds the lock on the requests, knows which workers are alive and launches the next phase.
So `chief.py requests | approve | revise | ask | cancel` do not touch the state files: they call the server's own API, the same
routes the page uses, and every gate applies exactly as it does to a click.

To find the server, it writes <state>/server.json when it starts ({port, token, pid}) and removes it when it stops. The token is
the per-start one the page already carries (any local program can read it from the page), so the file gives away nothing new;
it lives in the user's own data folder. A file left by a server that died is recognised because nothing answers on its port.
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import atomicio

FILE = "server.json"
TIMEOUT_S = 150          # an approval launches a worker, which can take a while


class ControlError(Exception):
    pass


def announce(state_dir, port: int, token: str) -> Path:
    """Called by the server when it starts listening."""
    path = Path(state_dir) / FILE
    atomicio.write_text(path, json.dumps({"port": int(port), "token": token, "pid": os.getpid()}))
    return path


def withdraw(state_dir) -> None:
    """Called by the server when it stops: only its own announcement is removed."""
    path = Path(state_dir) / FILE
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def read(state_dir) -> dict | None:
    try:
        data = json.loads((Path(state_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(data, dict) and isinstance(data.get("port"), int) and isinstance(data.get("token"), str) and data["token"]
    return data if ok else None


def call(state_dir, method: str, path: str, body: dict | None = None, opener=urllib.request.urlopen):
    """One request to the running server. Raises ControlError with a sentence for the user (server not running, or its answer)."""
    info = read(state_dir)
    if info is None:
        raise ControlError("the dashboard server is not running: start it with `python app/server.py`")
    base = f"http://127.0.0.1:{info['port']}"
    headers = {"X-Token": info["token"]}
    data = None
    if method == "POST":
        data = json.dumps(body or {"cli": True}).encode("utf-8")
        headers.update({"Content-Type": "application/json", "Origin": base})
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with opener(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", "")
        except (ValueError, AttributeError):
            msg = ""
        if e.code == 403 and not msg:
            msg = "the server did not accept the token (it was restarted): try again"
        raise ControlError(msg or f"the server answered {e.code}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ControlError(f"the dashboard server is not answering on port {info['port']} (start it with `python app/server.py`): {e}") from e


def find_request(requests: list, ref: str) -> dict:
    """The request a user means by a full id, the start of one, or a unique part of a title. Raises ControlError."""
    ref = (ref or "").strip().lower()
    if not ref:
        raise ControlError("say which request (its id or part of its title)")
    exact = [f for f in requests if f["id"] == ref]
    hits = exact or [f for f in requests if f["id"].startswith(ref)] or [f for f in requests if ref in f["title"].lower()]
    if len(hits) != 1:
        raise ControlError(f"request '{ref}' " + ("is ambiguous: " + ", ".join(f["id"] for f in hits[:6]) if hits else "not found"))
    return hits[0]
