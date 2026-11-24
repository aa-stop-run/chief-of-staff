"""REAL Claude Code and the trust of folders (opt-in: `set CHIEF_REAL_TESTS=1`, then `python -m pytest tests/real -q -s -k trust`).

What it proves with the installed `claude`, without changing Claude Code's trusted folders:
- `claude --bg --add-dir <folder it does not trust>` is refused, and `trust.looks_untrusted` / `trust.path_from` read that refusal;
- a folder BELOW a trusted one is accepted by `claude`, and `trust.is_trusted` says the same (the two agree in both directions);
- a real dashboard server whose vault Claude Code does not trust raises the alert, puts the curation in the blocked state (no failures
  counted, no worker started) and refuses to «trust» a folder nobody was refused for.
The step that WRITES a trusted folder (`trust.grant`) is tested against a stand-in config file in tests/test_trust.py: granting a real
folder changes your Claude Code settings, so it is left to the dashboard's «Trust this folder» click."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import trust  # noqa: E402

BASE = Path(os.environ.get("CHIEF_REAL_BASE", r"D:\_chief_probe"))
PORT = int(os.environ.get("CHIEF_REAL_TRUST_PORT", "8794"))
pytestmark = [pytest.mark.real, pytest.mark.with_concept,
              pytest.mark.skipif(os.environ.get("CHIEF_REAL_TESTS") != "1", reason="real Claude Code: set CHIEF_REAL_TESTS=1")]
PROMPT = "Reply with the single word OK and stop. Do not use any tool."


def _rm(path: Path):
    """rmtree that also removes git's read-only files (Windows refuses them otherwise)."""
    def onerror(func, p, _exc):
        os.chmod(p, 0o700)
        func(p)
    if Path(path).exists():
        shutil.rmtree(path, onerror=onerror)


def _bg(add_dir: Path):
    """The same launch the curator does, from the Chief's own (trusted) folder, with a prompt that costs almost nothing."""
    r = subprocess.run(["claude", "--bg", "-n", "trust-probe", "--add-dir", str(add_dir), "--permission-mode", "auto", "--", PROMPT],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=120)
    return r.stdout + r.stderr


def _stop(output: str):
    m = re.search(r"backgrounded\s*·\s*([0-9a-f]{6,})", output)
    if m:
        subprocess.run(["claude", "stop", m[1]], capture_output=True, text=True, timeout=60)


def _git_folder(path: Path) -> Path:
    _rm(path)
    path.mkdir(parents=True)
    subprocess.run(["git", "-C", str(path), "init", "-q", "-b", "main"], check=True)
    (path / "note.md").write_text("# note\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"], check=True)
    return path


def _plain(path: Path) -> Path:
    _rm(path)
    path.mkdir(parents=True)
    return path


CASES = [          # (label, how to build the folder, does claude accept it)
    ("a plain folder outside any repository", lambda b: _plain(b / "plain"), True),
    ("an untrusted git repository", lambda b: _git_folder(b / "repo"), False),
    ("a plain subfolder of an untrusted repository", lambda b: (_plain((_git_folder(b / "repo2")) / "sub")), False),
    ("a plain subfolder of the trusted Chief repository", lambda b: _plain(ROOT / "probe-plain"), True),
    ("a repository nested inside the trusted Chief repository", lambda b: _git_folder(ROOT / "probe-nested"), False),
]


@pytest.mark.parametrize("label,build,accepted", CASES, ids=[c[0] for c in CASES])
def test_claude_and_our_reading_of_its_trusted_folders_agree(label, build, accepted):
    """The installed `claude --bg --add-dir <folder>` and `trust.is_trusted` must say the same, and a refusal must name the folder
    that would have to be trusted (the repository) as `trust.target` says."""
    assert trust.is_trusted(ROOT), "run `claude` once in the Chief folder and accept its trust prompt before this test"
    folder = build(BASE / "trust-cases")
    try:
        out = _bg(folder)
        print(f"{label}: claude said: {out.strip()[:170]}")
        assert ("backgrounded" in out and not trust.looks_untrusted(out)) is accepted, out
        assert trust.is_trusted(folder) is accepted
        if not accepted:
            assert trust.looks_untrusted(out)
            assert Path(trust.path_from(out)).resolve() == Path(trust.target(folder)), (trust.path_from(out), trust.target(folder))
        else:
            _stop(out)
    finally:
        _rm(BASE / "trust-cases")
        _rm(ROOT / "probe-plain")
        _rm(ROOT / "probe-nested")


class Server:
    def __init__(self, home: Path):
        self.base = f"http://127.0.0.1:{PORT}"
        self.log = open(home / "server.log", "w")
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "app" / "server.py"), "--home", str(home), "--port", str(PORT)],
                                     cwd=str(ROOT), stdout=self.log, stderr=subprocess.STDOUT)
        html = ""
        for _ in range(40):
            try:
                html = urllib.request.urlopen(self.base + "/", timeout=3).read().decode("utf-8")
                break
            except Exception:
                time.sleep(1)
        m = re.search(r'const T="([^"]+)"', html)
        assert m, "the dashboard did not start"
        self.token = m.group(1)

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Token": self.token, "Origin": self.base, "Content-Type": "application/json"},
                                     method="POST" if body is not None else "GET")
        try:
            return 200, json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except Exception:
            self.proc.kill()
        self.log.close()


def test_a_dashboard_whose_vault_is_not_trusted_raises_the_alert_and_blocks_the_curation():
    vault = _git_folder(BASE / "untrusted-vault")
    inbox = vault / "inbox"
    inbox.mkdir()
    draft = inbox / "2026-11-24 Proj - note.md"
    draft.write_text("---\nprojeto: x\n---\nbody\n", encoding="utf-8")
    old = time.time() - 7200
    os.utime(draft, (old, old))
    home = BASE / "home-trust-test"
    _rm(home)
    home.mkdir(parents=True)
    (home / "projects.json").write_text(json.dumps({"roots": [], "extras": [], "vault": str(vault), "ignore": []}), encoding="utf-8")
    srv = Server(home)
    try:
        assert not trust.is_trusted(vault)
        status, out = srv.call("/api/inbox/curate", {})                 # «Curate now»: the real launch, refused by the real claude
        print("\ncurate now ->", status, str(out)[:260])
        assert status == 400 and "trust" in out.get("error", "").lower(), out
        st = srv.call("/api/state")[1]
        alerts = st["trust"]
        assert alerts and alerts[0]["kind"] == "vault" and Path(alerts[0]["path"]).resolve() == vault.resolve(), alerts
        cur = st["curation"]
        assert cur["blocked"] and cur["blocked"]["kind"] == "trust" and cur["attempts"] == 0 and not cur["running"], cur
        assert "trust" in cur["info"].lower() and cur["last"]["ok"] is False
        # nothing was launched, and nobody may «trust» a folder it was not refused for
        assert not any(w.get("project") == "Vault curation" and w.get("state") in ("working", "running") for w in st["workers"])
        status, out = srv.call("/api/trust/grant", {"path": str(BASE)})
        assert status == 400 and "not waiting" in out["error"], (status, out)
        assert not trust.is_trusted(vault)                                              # nothing was granted: the refused vault is still refused
    finally:
        srv.stop()
        _rm(home)
        _rm(vault)
