"""Demo mode: believable sample data, and a simulated machine, so the dashboard can be explored without touching anything real."""
import json
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import demo  # noqa: E402
import migrations  # noqa: E402
import usage  # noqa: E402
from features import Features, parse_review, parse_tests  # noqa: E402
from pending import Pending  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp("demo-world")
    return base, demo.seed(base)


def test_the_sample_data_is_written_in_the_current_schema():
    assert demo.SCHEMA_VERSION == migrations.CURRENT


def test_seed_builds_projects_a_vault_and_a_configuration(world):
    base, home = world
    cfg = json.loads((home / "projects.json").read_text(encoding="utf-8"))
    assert cfg["vault_language"] == "en" and Path(cfg["vault"]).is_dir() and cfg["roots"] == [(base / "projects").as_posix()]
    for folder, hub, domain, _ in demo.PROJECTS:
        assert (base / "projects" / folder / "README.md").is_file()
        assert f"domain: {domain}" in (Path(cfg["vault"]) / "Projects" / f"{hub}.md").read_text(encoding="utf-8")
    assert json.loads((home / "schema.json").read_text(encoding="utf-8")) == {"schema_version": migrations.CURRENT}


def test_the_sample_projects_are_git_repos_with_a_harmless_remote(world):
    base, _ = world
    out = subprocess.run(["git", "-C", str(base / "projects" / "storefront"), "remote", "get-url", "origin"], capture_output=True, text=True)
    assert out.stdout.strip() == "https://github.com/acme/storefront.git"


def test_the_requests_cover_every_stage_of_the_pipeline(world):
    _, home = world
    rt = demo.Runtime(None, {"demo007": "working", "demo008": "working"})
    fs = Features(home / "state", lambda *a, **k: {"id": "x"}, lambda wid: wid in rt.live)
    by_title = {f["title"]: f for f in fs.list()}
    assert {t: (f["kind"], f["stage"], f["status"]) for t, f in by_title.items()} == {
        "Two-factor login (TOTP)": ("feature", "spec", "awaiting"),
        "Retry failed webhooks": ("feature", "plan", "awaiting"),
        "Cart total is off by one cent": ("fix", "review", "awaiting"),
        "Why is the nightly sync slow?": ("analysis", "build", "working"),
        "CSV export of trips": ("feature", "build", "working"),
        "Upgrade pytest to 9": ("maintenance", "review", "done"),
        "Weekly digest email": ("feature", "review", "done"),
        "Fix the flaky login test": ("fix", "review", "done"),
    }


def test_the_sample_artifacts_use_the_formats_the_pipeline_reads(world):
    _, home = world
    rt = demo.Runtime(None, {})
    fs = Features(home / "state", lambda *a, **k: {"id": "x"}, lambda wid: wid in rt.live)
    f = {x["title"]: x for x in fs.list()}
    art = fs.artifacts(f["Cart total is off by one cent"]["id"])
    assert parse_review(art["review"])[0] == "reserves" and parse_tests(art["review"])[0] == "confirmed"
    assert parse_tests(art["build"]) == ("ok", {"base": (212, 0), "final": (215, 0)})
    assert parse_review(fs.artifacts(f["Retry failed webhooks"]["id"])["planreview"])[0] == "reserves"
    assert [q["id"] for q in fs.questions(f["Two-factor login (TOTP)"]["id"])] == ["P1", "P2"]


def test_the_sample_todos_load(world):
    _, home = world
    items = Pending(home / "state").list()
    assert sorted(i["status"] for i in items) == ["awaiting", "blocked", "optional", "running"]


def test_nothing_in_the_sample_data_comes_from_the_real_machine(world):
    base, home = world
    text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in home.rglob("*") if p.is_file())
    text = text.replace(base.as_posix(), "<base>").replace(str(base), "<base>")      # the folder the test built it in is expected
    assert str(Path.home()) not in text and Path.home().name.lower() not in text.lower()   # nothing of whoever runs the tests
    assert not re.search(r"[\w.+-]+@(?!example\.com)[\w-]+\.[\w.-]+", text.replace("git@github.com", ""))     # no e-mail address but the sample one


# ---------------------------------------------------------------- the simulated machine
def test_the_simulation_never_runs_claude_and_refuses_what_needs_the_real_world(monkeypatch, tmp_path):
    def forbidden(*a, **k):
        raise AssertionError("the demo must not run any program")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    projects = [{"name": "Storefront", "slug": "storefront", "path": "x"}]
    recorded = []
    chief = SimpleNamespace(STATE=tmp_path, find=lambda name, projs: projs[0], load_projects=lambda: projects, load_workers=lambda: [],
                            work_sessions=lambda: SimpleNamespace(current_id=lambda: "ws-1"), record_worker=recorded.append)
    rt = demo.Runtime(chief)
    w = rt.launch_worker("Storefront", "do it", model="opus", label="my-task")
    assert w["id"].startswith("demo") and rt.live[w["id"]] == "working"
    assert [s["id"] for s in rt.sessions_checked()] == [w["id"]]
    assert recorded == [w] and w["model"] == "opus" and w["session"] == "ws-1" and "phase" not in w
    assert rt.stop_worker(w["id"]) is True and rt.sessions_checked() == []
    assert rt.commit_worktree(tmp_path, "m")[0] is True
    assert rt.launch_worker("Storefront", "study it", label="extraction-app")["phase"] == "extraction"
    for call in (lambda: rt.open_session("Storefront", "abc12345"), lambda: rt.launch_curator("x")):
        with pytest.raises(RuntimeError, match="demo"):
            call()
    with pytest.raises(ValueError, match="demo"):
        rt.clone_project("https://github.com/a/b")
    assert rt.github_status()["account"] == "demo-user"


def test_install_replaces_exactly_the_functions_that_touch_the_machine(monkeypatch, world):
    _, home = world
    chief = SimpleNamespace(STATE=home / "state", **{n: None for n in demo.PATCHED_CHIEF})
    settings = SimpleNamespace(github_status=None)
    rt = demo.install(chief, settings)
    assert all(callable(getattr(chief, n)) for n in demo.PATCHED_CHIEF) and settings.github_status() == rt.github_status()
    assert {s["id"] for s in chief.sessions_checked()} == {"demo007", "demo008"}


# ---------------------------------------------------------------- the real server in demo mode
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url, **headers):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as r:
        return r.read().decode("utf-8")


def test_the_server_starts_in_demo_mode_and_serves_only_sample_data():
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(ROOT / "app" / "server.py"), "--demo", "--port", str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        html = None
        for _ in range(60):
            try:
                html = get(f"http://127.0.0.1:{port}/")
                break
            except OSError:
                time.sleep(0.25)
        assert html, proc.poll()
        token = re.search(r'const T="([^"]+)"', html).group(1)
        state = json.loads(get(f"http://127.0.0.1:{port}/api/state?t={token}"))
        assert state["demo"] is True and state["claude_ok"] is True and state["models"] == ["opus", "sonnet", "haiku"]
        assert {p["name"] for p in state["projects"]} == {h for _, h, _, _ in demo.PROJECTS}
        assert len(state["features"]) == 6 and state["earlier_closed"] == 2 and len(state["pending"]) == 4     # the two older done ones are in past sessions
        assert {w["id"]: w["state"] for w in state["workers"]}["demo007"] == "working"
        settings = json.loads(get(f"http://127.0.0.1:{port}/api/settings?t={token}"))
        assert settings["github"]["account"] == "demo-user"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_a_demo_folder_is_only_reused_if_it_is_empty_or_was_made_by_a_demo(tmp_path):
    ok = demo.prepare_dir(tmp_path / "new")
    assert (ok / demo.MARKER).is_file()
    (ok / "old-sample.txt").write_text("x", encoding="utf-8")
    assert demo.prepare_dir(ok) == ok and not (ok / "old-sample.txt").exists()             # a previous demo folder is cleaned and reused
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "important.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(SystemExit, match="not a Chief demo folder"):
        demo.prepare_dir(mine)
    assert (mine / "important.txt").read_text(encoding="utf-8") == "keep me"


def test_the_sample_sessions_and_costs_are_consistent(world):
    _, home = world
    sessions = json.loads((home / "state" / "work_sessions.json").read_text(encoding="utf-8"))["sessions"]
    assert [s["id"] for s in sessions] == [demo.SESSION_BEFORE, demo.SESSION_YESTERDAY, demo.SESSION_NOW]
    assert [bool(s["ended"]) for s in sessions] == [True, True, False]
    ledger = json.loads((home / "state" / "usage.json").read_text(encoding="utf-8"))["workers"]
    assert sessions[0]["totals"]["cost_usd"] == usage.aggregate(ledger, session=demo.SESSION_BEFORE)["cost_usd"] > 0
    workers = json.loads((home / "state" / "workers.json").read_text(encoding="utf-8"))
    assert {w["id"] for w in workers} == set(ledger) and all(w["session"] in {s["id"] for s in sessions} for w in workers)
