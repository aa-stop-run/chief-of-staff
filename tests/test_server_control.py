"""Stopping and restarting the dashboard server from its own page, and starting it without a window (`--background`)."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from test_demo_server import ROOT, DemoServer, free_port  # noqa: E402

pytestmark = pytest.mark.slow


def alive(port: int) -> bool:
    import http.client
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        c.request("GET", "/")
        c.getresponse().read()
        return True
    except OSError:
        return False


def wait(cond, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.4)
    return False


def token_of(port: int) -> str:
    import urllib.request
    html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")
    return re.search(r'const T="([^"]+)"', html).group(1)


def post(port: int, path: str, token: str):
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"{}", method="POST",
                                 headers={"X-Token": token, "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=10)


def test_stop_ends_the_server_after_answering_and_needs_the_token(tmp_path):
    srv = DemoServer(tmp_path)
    try:
        assert srv.post("/api/server/stop", {}, token=False)[0] == 403 and alive(srv.port)       # nothing without the token
        status, out = srv.post("/api/server/stop", {})
        assert status == 200 and out == {"ok": True, "restarting": False}                         # it answered first
        assert srv.proc.wait(15) is not None
        assert wait(lambda: not alive(srv.port), 10)
    finally:
        srv.proc.kill()


def test_the_demo_refuses_a_restart_because_it_deletes_its_folder_when_it_stops(tmp_path):
    srv = DemoServer(tmp_path)
    try:
        status, out = srv.post("/api/server/restart", {})
        assert status == 400 and "demo" in out["error"] and alive(srv.port)
    finally:
        srv.post("/api/server/stop", {})
        srv.proc.wait(15)


def test_restart_brings_a_normal_server_back_without_a_window(tmp_path):
    home = tmp_path / "home"
    srv = DemoServer(tmp_path, mode_args=("--home", str(home)))                                   # a real server on a throw-away data folder
    port = srv.port
    try:
        old = srv.token
        status, out = srv.post("/api/server/restart", {})
        assert status == 200 and out["restarting"] is True
        assert srv.proc.wait(15) is not None                                                      # the first one is gone
        assert wait(lambda: alive(port), 40), "the server did not come back"
        new = token_of(port)
        assert new and new != old                                                                 # a new start has a new token
        assert (home / "server.log").exists()                                                     # what it prints goes to a file
    finally:
        try:
            post(port, "/api/server/stop", token_of(port))
        except Exception:
            pass
        srv.proc.kill()
        wait(lambda: not alive(port), 10)


def test_background_starts_a_server_and_returns_at_once(tmp_path):
    port = free_port()
    env = {**os.environ, "CHIEF_DEMO_DIR": str(tmp_path / "demo-world")}
    t0 = time.time()
    r = subprocess.run([sys.executable, str(ROOT / "app" / "server.py"), "--demo", "--port", str(port), "--background"], env=env,
                       capture_output=True, text=True, timeout=60)
    try:
        assert r.returncode == 0 and "started in the background" in r.stdout, (r.stdout, r.stderr)
        assert time.time() - t0 < 30
        assert wait(lambda: alive(port), 20)                                                      # the server lives on after the launcher left
    finally:
        try:
            post(port, "/api/server/stop", token_of(port))
        except Exception:
            pass
        wait(lambda: not alive(port), 15)


def test_the_page_has_the_server_buttons_and_the_new_text_in_portuguese():
    src = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    assert 'data-srv="restart"' in src and 'data-srv="stop"' in src and "/api/server/" in src
    for needle in ('"Restart the Chief":"Reiniciar o Chief"', '"Stop the Chief":"Parar o Chief"', '"The Chief has stopped":"O Chief parou"'):
        assert needle in src


def test_wait_for_exit_waits_for_a_real_process_and_never_signals_it():
    import waitpid
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    t0 = time.time()
    assert waitpid.wait_for_exit(p.pid, 20) is True
    assert 0.5 < time.time() - t0 < 15 and p.poll() == 0                                           # it ended by itself, not killed
    assert waitpid.wait_for_exit(p.pid, 1) is True                                                 # already gone
    q = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert waitpid.wait_for_exit(q.pid, 0.5) is False and q.poll() is None                     # still running: not touched
    finally:
        q.kill()


# ------------------------------------------------------------------ no console windows

def test_the_server_adds_no_window_unless_the_caller_chose_its_own_flags(monkeypatch):
    import nowindow
    monkeypatch.setattr(nowindow.os, "name", "nt")
    assert nowindow.with_no_window({"cwd": "x"}) == {"cwd": "x", "creationflags": nowindow.CREATE_NO_WINDOW}
    assert nowindow.with_no_window({"creationflags": 0}) == {"creationflags": nowindow.CREATE_NO_WINDOW}
    kept = {"creationflags": 0x10}                                                                  # a deliberate window (Resume) or a detached start
    assert nowindow.with_no_window(kept) is kept
    monkeypatch.setattr(nowindow.os, "name", "posix")
    plain = {"cwd": "x"}
    assert nowindow.with_no_window(plain) is plain


def test_install_is_done_once_and_the_commands_still_work():
    import nowindow
    first = nowindow.install()
    assert nowindow.install() is False                                                              # never patched twice
    out = subprocess.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "hi"
    if os.name == "nt":
        assert first in (True, False) and getattr(subprocess.Popen, "_chief_nowindow", False)


def test_the_server_installs_it_and_the_deliberate_terminal_keeps_its_own_flags():
    src = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
    assert "nowindow.install()" in src
    chief_src = (ROOT / "scripts" / "chief.py").read_text(encoding="utf-8")
    assert "CREATE_NEW_CONSOLE" in chief_src                                                       # «Resume» still opens a window on purpose
