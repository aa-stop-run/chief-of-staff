"""The project pages' GET routes, over HTTP against the demo server (same helper as test_demo_server)."""
import json
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from test_demo_server import DemoServer  # noqa: E402

FAKE_TOKEN = "sk-demo-0123456789abcdef"
ODD = "R&D Lab #1"


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    s = DemoServer(tmp_path_factory.mktemp("proj-routes"))
    yield s
    s.stop()


def project(srv, name, **kw):
    return srv.get(f"/api/project?name={quote(name, safe='')}", **kw)


def doc(srv, name, path, **kw):
    return srv.get(f"/api/project/doc?name={quote(name, safe='')}&path={quote(path, safe='')}", **kw)


def test_the_project_routes_need_the_token(srv):
    for path in ("/api/projects", "/api/project?name=Storefront", "/api/project/doc?name=Storefront&path=README.md"):
        assert srv.get(path, token=False)[0] == 403
        assert srv.request("GET", path + ("&" if "?" in path else "?") + "t=wrong", token=False)[0] == 403


def test_the_list_has_one_row_per_project_with_the_attention_order(srv):
    status, out = srv.get("/api/projects")
    rows = out["projects"]
    assert status == 200 and len(rows) == 6
    assert [r["attention"] for r in rows] == sorted(r["attention"] for r in rows)
    by = {r["name"]: r for r in rows}
    assert by["Storefront"]["attention"] == 0 and by["Home Automation"]["attention"] == 2 and by["Dotfiles"]["attention"] == 4
    assert by["Storefront"]["branch"] == "main" and by["Storefront"]["fixes"] >= 1 and by["Storefront"]["cost_usd"] > 0
    assert by["Home Automation"]["fixes"] == 1                                  # the blocked to-do


def test_every_list_row_carries_the_fields_the_view_reads(srv):
    fields = {"name", "domain", "path", "branch", "dirty", "last_date", "open", "attention", "fixes", "cost_usd", "git_age_s"}
    _, out = srv.get("/api/projects")
    assert out["projects"] and all(fields <= set(r) for r in out["projects"])


def test_a_project_name_with_spaces_and_symbols_round_trips(srv):
    status, out = project(srv, ODD)
    assert status == 200 and out["header"]["name"] == ODD
    assert ODD in {r["name"] for r in srv.get("/api/projects")[1]["projects"]}


def test_the_detail_shows_docs_tools_and_fixes_for_the_demo_project(srv):
    status, out = project(srv, "Storefront")
    assert status == 200
    assert out["header"]["git"] is True and out["header"]["branch"] == "main"
    paths = {d["path"] for d in out["docs"]}
    assert {"README.md", "docs/specs/two-factor-login.md", "docs/plans/checkout-rework.md", "docs/analyses/cart-abandonment.md"} <= paths
    assert any(f["severity"] == "HIGH" and f["source"] == "review" for f in out["fixes"])
    assert "architecture" not in out["overview"] and out["overview"]["source"] == "vault"
    assert out["tools"]["claude"]["mcp"] and out["tools"]["claude"]["skills"]
    assert {c["name"] for c in out["tools"]["commands"]} >= {"test", "build", "lint"}
    assert out["requests"]["open"] and out["requests"]["closed"]
    assert out["costs"]["total_usd"] > 0 and out["costs"]["by_request"]


def test_traversal_and_forbidden_documents_are_404(srv):
    assert doc(srv, "Storefront", "README.md")[0] == 200
    for bad in ("../x", "..%2F..%2Fsecrets", "../../secrets", ".env", "C:/Windows/win.ini", "/etc/passwd", "docs", "missing.md", ""):
        status, out = doc(srv, "Storefront", bad)
        assert (status, out) == (404, {"error": "document not found"}), bad
    assert srv.get("/api/project/doc?name=Storefront&path=..%2F..%2Fsecrets")[0] == 404
    assert doc(srv, "Nope", "README.md")[0] == 404


def test_the_demo_mcp_token_never_leaves_the_server(srv):
    texts = [json.dumps(project(srv, n)[1]) for n in ("Storefront", "Payments API", ODD)]
    texts.append(json.dumps(srv.get("/api/projects")[1]))
    texts += [json.dumps(doc(srv, "Storefront", p)[1]) for p in ("README.md", ".mcp.json")]   # the second is a 404
    assert all(FAKE_TOKEN not in t for t in texts)
    assert (srv.dir / "projects" / "storefront" / ".mcp.json").read_text(encoding="utf-8").count(FAKE_TOKEN) == 1   # it is really on disk


def test_unknown_project_is_404_in_both_languages(srv):
    assert project(srv, "Nope") == (404, {"error": "project not found"})
    assert project(srv, "Nope", headers={"X-Lang": "pt"}) == (404, {"error": "projeto não encontrado"})
    assert project(srv, "storefront")[0] == 404                                  # exact name only
    assert doc(srv, "Nope", "README.md", headers={"X-Lang": "pt"}) == (404, {"error": "documento não encontrado"})


def test_a_project_without_a_folder_is_404(srv):
    import shutil
    victim = srv.dir / "projects" / "dotfiles"
    gone = srv.dir / "dotfiles-moved"
    victim.rename(gone)
    try:
        names = {r["name"] for r in srv.get("/api/projects")[1]["projects"]}
        assert "Dotfiles" not in names or project(srv, "Dotfiles")[0] == 404
        assert project(srv, "Dotfiles")[0] == 404
        assert doc(srv, "Dotfiles", "README.md")[0] == 404
    finally:
        gone.rename(victim)
        shutil.rmtree(gone, ignore_errors=True)


def test_workers_and_todos_never_carry_task_text(srv):
    out = project(srv, "Storefront")[1]
    body = json.dumps(out)
    assert out["workers"] and all(set(w) <= {"id", "name", "state", "model", "started", "phase", "request"} for w in out["workers"])
    assert "write the spec" not in body and "cwd" not in body
    home = project(srv, "Home Automation")[1]
    assert home["todos"] and all(set(t) <= {"id", "title", "status", "summary"} for t in home["todos"])
    assert "missing" not in home["todos"][0] and "session" not in home["todos"][0]


def test_a_blocked_todo_is_an_identified_fix_with_the_todo_source(srv):
    fixes = project(srv, "Home Automation")[1]["fixes"]
    assert [(f["source"], f["severity"]) for f in fixes] == [("todo", "HIGH")]
    assert fixes[0]["request"].startswith("p-demo")                              # a to-do id, not a request id


@pytest.mark.parametrize("path", [".mcp.json", ".claude/settings.json", ".git/config", "package.json"])
def test_only_listed_documents_are_served(srv, path):
    assert doc(srv, "Storefront", path) == (404, {"error": "document not found"})
    listed = [d["path"] for d in project(srv, "Storefront")[1]["docs"]]
    assert path not in listed
    assert doc(srv, "Storefront", "docs/specs/two-factor-login.md")[0] == 200


def test_backslash_unc_and_single_encoded_paths_give_the_same_404(srv):
    uniform = (404, {"error": "document not found"})
    for raw in ("..%5Cx", "%5C%5Chost%5Cshare", "%2e%2e/x", "%2e%2e%2fx", "docs%5C..%5C..%5Cx", "..%5C..%5Cwindows%5Cwin.ini"):
        assert srv.get(f"/api/project/doc?name=Storefront&path={raw}") == uniform, raw
    for plain in (r"..\x", r"\\host\share", r"docs\specs\two-factor-login.md\.."):
        assert doc(srv, "Storefront", plain) == uniform, plain


def test_a_hidden_domain_project_is_not_served(tmp_path):
    import demo
    from test_demo_server import DemoServer as Srv
    home = demo.seed(tmp_path / "demo-world")
    cfg = json.loads((home / "projects.json").read_text(encoding="utf-8"))
    cfg["hide_domains"] = ["tools"]                  # Dotfiles and R&D Lab #1
    (home / "projects.json").write_text(json.dumps(cfg), encoding="utf-8")
    s = Srv.__new__(Srv)
    s.__init__(tmp_path, mode_args=("--home", str(home)))
    try:
        names = {r["name"] for r in s.get("/api/projects")[1]["projects"]}
        assert names and "Dotfiles" not in names and ODD not in names and "Storefront" in names
        assert project(s, "Dotfiles")[0] == 404 and project(s, ODD)[0] == 404
        assert doc(s, "Dotfiles", "README.md")[0] == 404
    finally:
        s.stop()


def test_the_detail_carries_every_field_the_project_page_reads(srv):
    out = project(srv, "Storefront")[1]
    assert {"header", "requests", "fixes", "overview", "docs", "tools", "costs", "workers", "todos"} <= set(out)
    assert {"name", "domain", "path", "git", "git_age_s", "branch", "dirty", "last_date", "last_msg", "ahead"} <= set(out["header"])
    assert len(out["header"]["last_date"]) == 10                                  # a date, never a time of day
    row = out["requests"]["open"][0]
    assert {"id", "title", "kind", "stage", "status", "cost_usd"} <= set(row)
    assert all({"source", "request", "title", "severity", "text"} <= set(f) for f in out["fixes"])
    assert {"source", "text"} <= set(out["overview"]) and "architecture" not in out["overview"]
    assert all({"path", "kind", "title", "age_s"} <= set(d) for d in out["docs"])
    assert {"skills", "commands", "agents", "mcp", "hooks"} <= set(out["tools"]["claude"])
    assert all({"name", "source"} <= set(c) for c in out["tools"]["commands"])
    assert {"total_usd", "by_request", "by_model"} <= set(out["costs"])
    assert all({"id", "name", "state", "model", "started", "phase", "request"} <= set(w) for w in out["workers"])
    status, d = doc(srv, "Storefront", out["docs"][0]["path"])
    assert status == 200 and {"path", "text", "truncated"} <= set(d)


def test_a_project_without_docs_tools_or_requests_has_empty_blocks_not_errors(srv):
    status, out = project(srv, ODD)
    assert status == 200
    assert out["fixes"] == [] and out["requests"] == {"open": [], "closed": []} and out["todos"] == []
    assert out["tools"]["commands"] == [] and not any(out["tools"]["claude"].values())
    assert out["costs"]["total_usd"] == 0 and out["costs"]["by_request"] == []


def test_two_projects_with_one_name_are_ambiguous_and_have_no_page(tmp_path):
    s = DemoServer(tmp_path)
    try:
        world = s.dir
        for n in ("twin-a", "twin-b"):
            (world / "projects" / n).mkdir()
            (world / "projects" / n / "README.md").write_text("# x\n", encoding="utf-8")
        (world / "vault" / "Projects" / "Twin.md").write_text(
            f'---\nlocal: "{(world / "projects").as_posix()}"\ndomain: tools\n---\n# Twin\n', encoding="utf-8")
        status, out = s.get("/api/projects")
        twins = [r for r in out["projects"] if r["name"] == "Twin"]
        assert status == 200 and len(twins) == 2 and all(r["ambiguous"] for r in twins)
        assert not any(r["ambiguous"] for r in out["projects"] if r["name"] != "Twin")
        assert project(s, "Twin") == (404, {"error": "project not found"})
        assert project(s, "Storefront")[0] == 200
    finally:
        s.stop()
