"""REAL workers: the concept phase end to end with the user's own Claude Code (opt-in: it spends tokens and takes minutes).

    set CHIEF_REAL_TESTS=1            (PowerShell: $env:CHIEF_REAL_TESTS = "1")
    python -m pytest tests/real -q -s

It starts a throw-away dashboard server (own home, own port) on a tiny git project, creates a REAL feature request and checks what the
simulated tests cannot: that a real worker writes a concept that passes the gate, that the real independent validator writes a verdict
the server can read, that the spec worker really reads the approved concept, and that nothing touched the project's files.

The project folder must be one Claude Code already trusts (`claude --bg` refuses untrusted folders: «Workspace not trusted»). Set
CHIEF_REAL_PROJECT to your own, or accept the default D:/_chief_probe/proj after running `claude` once in it."""
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
PROJECT = Path(os.environ.get("CHIEF_REAL_PROJECT", r"D:\_chief_probe\proj"))
PORT = int(os.environ.get("CHIEF_REAL_PORT", "8793"))
WAIT_S = int(os.environ.get("CHIEF_REAL_WAIT_S", "1200"))
pytestmark = [pytest.mark.real, pytest.mark.with_concept,
              pytest.mark.skipif(os.environ.get("CHIEF_REAL_TESTS") != "1", reason="real workers: set CHIEF_REAL_TESTS=1")]

DESC = ("Add a `percent(a, b)` helper to this mini calculator that returns what percentage `a` is of `b`, with its unit test. "
        "It must reject b == 0 with a clear error. Python standard library only.")


def _git(*a):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(PROJECT), *a], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _ensure_project():
    PROJECT.mkdir(parents=True, exist_ok=True)
    if subprocess.run(["git", "-C", str(PROJECT), "rev-parse", "HEAD"], capture_output=True).returncode != 0:
        subprocess.run(["git", "-C", str(PROJECT), "init", "-b", "main"], capture_output=True)
        (PROJECT / "README.md").write_text("# Mini calculator\nStdlib only. Tests: `python -m unittest discover -s . -p \"test_*.py\"`.\n")
        (PROJECT / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (PROJECT / "test_calc.py").write_text("import unittest, calc\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n"
                                              "        self.assertEqual(calc.add(1, 2), 3)\n\n\nif __name__ == '__main__':\n    unittest.main()\n")
        _git("add", ".")
        _git("commit", "-m", "init")


def _changes(allow_archive: bool = False) -> str:
    """The project's uncommitted changes, ignoring Python's bytecode cache (a validator that runs `python -c` creates it; it is not a
    change). `allow_archive`: also ignore the approved documents the Chief archives under docs/ (concepts, specs, plans)."""
    skip = ("__pycache__",) + (("docs/concepts/", "docs/specs/", "docs/plans/") if allow_archive else ())
    return chr(10).join(ln for ln in _git("status", "--porcelain", "-uall").splitlines() if not any(x in ln for x in skip))


class Dashboard:
    """A throw-away dashboard server on the test project, and a small client for its API."""

    def __init__(self, home: Path):
        self.home, self.base = home, f"http://127.0.0.1:{PORT}"
        home.mkdir(parents=True, exist_ok=True)
        (home / "projects.json").write_text(json.dumps({"roots": [], "extras": [str(PROJECT)], "vault": "", "ignore": []}), encoding="utf-8")
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
        assert m, "the dashboard did not start (see server.log in the test home)"
        self.token = m.group(1)

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Token": self.token, "Origin": self.base, "Content-Type": "application/json"},
                                     method="POST" if body is not None else "GET")
        try:
            return json.load(urllib.request.urlopen(req, timeout=60))
        except urllib.error.HTTPError as e:
            return {"_error": e.code, "_body": e.read().decode()[:400]}

    def card(self, fid):
        return next((f for f in self.call("/api/state")["features"] if f["id"] == fid), None)

    def wait(self, fid, ok, what, timeout=WAIT_S):
        end, last = time.time() + timeout, None
        while time.time() < end:
            last = self.card(fid)
            if last and last["status"] == "failed":
                pytest.fail(f"the request failed while waiting for {what}: {last.get('last_message', '')[:300]}")
            if last and ok(last):
                return last
            time.sleep(10)
        pytest.fail(f"timed out after {timeout}s waiting for {what}; last state: {last and (last['stage'], last['status'])}")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except Exception:
            self.proc.kill()
        self.log.close()


@pytest.fixture(scope="module")
def dash():
    _ensure_project()
    before = _changes()
    home = PROJECT.parent / "home-concept-test"
    shutil.rmtree(home, ignore_errors=True)
    d = Dashboard(home)
    d.project_status_before = before
    yield d
    d.stop()
    shutil.rmtree(home, ignore_errors=True)


def test_a_real_concept_is_written_validated_approved_and_read_by_the_spec(dash):
    made = dash.call("/api/features", {"project": PROJECT.name, "title": "Percent helper", "desc": DESC, "kind": "feature"})
    assert "id" in made, made
    fid = made["id"]
    assert made["stage"] == "concept" and made["step"] == "concept"
    try:
        # the real concept worker writes concept.md; the real independent validator then writes concept-review.md
        c = dash.wait(fid, lambda x: x["stage"] == "concept" and x["status"] == "awaiting" and not (x.get("cv") or {}).get("checking")
                      and (x.get("artifacts") or {}).get("conceptreview"), "the concept and its validation")
        concept = c["artifacts"]["concept"]
        review = c["artifacts"]["conceptreview"]
        print("\n---- CONCEPT (first 1500 chars) ----\n" + concept[:1500])
        print("---- VALIDATION (first 900 chars) ----\n" + review[:900])
        heads = {h.strip().lower() for h in re.findall(r"^#{2,3}\s*(.+?)\s*$", concept, re.M)}
        assert any(h.startswith(("approaches considered", "abordagens consideradas")) for h in heads), heads
        assert any(h.startswith(("recommendation and decisions", "recomendação e decisões")) for h in heads), heads
        assert re.search(r"\bD1\b", concept), "the concept has no numbered decision"
        # the server can read the validator's verdict (an unreadable one would have been repeated, then left to the user)
        assert re.search(r"^#{2,3}\s*(verdict|veredicto)", review, re.I | re.M), review[:200]
        assert not any(h["event"] == "concept validation without verdict" for h in c["history"]), "the validator gave no readable verdict"
        # a worker that is only to analyse must not have changed the project
        assert _changes() == dash.project_status_before, "the concept phase changed files in the project"
        # your approval is a real gate, and it is the click that starts the spec
        res = dash.call(f"/api/features/{fid}/approve", {})
        assert res.get("stage") == "spec", res
        # the real spec worker reads the approved concept and builds on its decisions
        s = dash.wait(fid, lambda x: x["stage"] == "spec" and x["status"] == "awaiting", "the spec")
        spec = s["artifacts"]["spec"]
        print("---- SPEC (first 1200 chars) ----\n" + spec[:1200])
        assert re.search(r"concept|conceito|\bD\d\b", spec, re.I), "the spec does not refer to the concept or its decisions"
        trail = (dash.home / "state" / "features" / fid / "decisions.md").read_text(encoding="utf-8")
        assert "concept validated" in trail or "concept fixes" in trail
        # the findings the validator left open (you approved without sending them back) must have reached the spec
        sys.path.insert(0, str(ROOT / "scripts"))
        import prompt_refs
        open_ = prompt_refs.open_findings(review)
        if open_:
            words = {w.lower() for ln in open_ for w in re.findall(r"[A-Za-z]{6,}", ln.split(":**")[0])}
            assert any(w in spec.lower() for w in words), f"none of the open findings' words {sorted(words)} appear in the spec"
        # approving archived the concept in the project's docs/ (uncommitted, like the spec's archive); nothing else changed
        assert list((PROJECT / "docs" / "concepts").glob("*percent-helper*.md")), "the approved concept was not archived"
        assert _changes(allow_archive=True) == dash.project_status_before
    finally:
        dash.call(f"/api/features/{fid}/cancel", {})


def test_asking_for_changes_runs_the_real_concept_again_with_your_comment(dash):
    made = dash.call("/api/features", {"project": PROJECT.name, "title": "Percent helper again", "desc": DESC, "kind": "feature"})
    fid = made["id"]
    try:
        dash.wait(fid, lambda x: x["stage"] == "concept" and x["status"] == "awaiting" and not (x.get("cv") or {}).get("checking")
                  and (x.get("artifacts") or {}).get("conceptreview"), "the first concept and its validation")
        folder = dash.home / "state" / "features" / fid
        before = (folder / "concept.md").read_text(encoding="utf-8")
        res = dash.call(f"/api/features/{fid}/revise", {"comment": "Add an approach that returns None instead of raising, and say why it is rejected."})
        assert res.get("stage") == "concept" and res.get("status") == "working", res
        c = dash.wait(fid, lambda x: x["stage"] == "concept" and x["status"] == "awaiting" and not (x.get("cv") or {}).get("checking")
                      and (x.get("artifacts") or {}).get("conceptreview"), "the concept written again and validated again")
        after = c["artifacts"]["concept"]
        assert (folder / "concept.v1.md").is_file(), "the earlier version was not kept"
        assert after != before
        assert re.search(r"\bNone\b", after), "the new concept does not take the comment into account"
        assert _changes(allow_archive=True) == dash.project_status_before
    finally:
        dash.call(f"/api/features/{fid}/cancel", {})


def test_a_real_maintenance_request_goes_from_the_concept_to_the_plan_that_reads_it(dash):
    desc = "Maintenance: rename the test class `T` in test_calc.py to `CalcTests` and add a docstring to `add`. Behaviour must not change."
    made = dash.call("/api/features", {"project": PROJECT.name, "title": "Tidy the calc tests", "desc": desc, "kind": "maintenance"})
    fid = made["id"]
    assert made["stage"] == "concept"
    try:
        c = dash.wait(fid, lambda x: x["stage"] == "concept" and x["status"] == "awaiting" and not (x.get("cv") or {}).get("checking")
                      and (x.get("artifacts") or {}).get("conceptreview"), "the concept and its validation")
        assert re.search(r"behaviou?r", c["artifacts"]["concept"], re.I), "a maintenance concept should say what behaviour is preserved"
        assert dash.call(f"/api/features/{fid}/approve", {}).get("stage") == "plan"          # no spec in maintenance
        p = dash.wait(fid, lambda x: x["stage"] == "plan" and x["status"] == "awaiting" and not (x.get("pv") or {}).get("checking")
                      and (x.get("artifacts") or {}).get("planreview"), "the plan and its validation")
        print("\n---- PLAN (first 900 chars) ----\n" + p["artifacts"]["plan"][:900])
        assert re.search(r"concept|conceito|\bD\d\b", p["artifacts"]["plan"], re.I), "the plan does not refer to the concept"
        assert _changes(allow_archive=True) == dash.project_status_before
    finally:
        dash.call(f"/api/features/{fid}/cancel", {})
