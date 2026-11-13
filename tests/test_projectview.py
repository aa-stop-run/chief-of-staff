"""Sandboxed document access for the project pages."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import projectview  # noqa: E402


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    (root / "docs" / "specs").mkdir(parents=True)
    (root / "docs" / "specs" / "a.md").write_text("# Spec A\n\nbody\n", encoding="utf-8")
    (root / "README.md").write_text("# Readme title\n", encoding="utf-8")
    (root / ".env").write_text("KEY=1", encoding="utf-8")
    (root / "secrets_codes.md").write_text("x", encoding="utf-8")
    (root / "SECRETS.txt").write_text("x", encoding="utf-8")
    return root


def test_dotdot_and_absolute_paths_are_refused(proj, tmp_path):
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    for bad in ("../outside.md", "docs/../../outside.md", str(tmp_path / "outside.md"), "docs", ""):
        with pytest.raises(ValueError):
            projectview.safe_path(proj, bad)


def test_forbidden_names_are_not_listed_or_readable(proj):
    for name in (".env", "secrets_codes.md", "SECRETS.txt", "CLAUDE.local.md", ".claude/settings.local.json"):
        with pytest.raises(ValueError):
            projectview.safe_path(proj, name)
        with pytest.raises(ValueError):
            projectview.read_doc(proj, name)
    (proj / "CLAUDE.local.md").write_text("x", encoding="utf-8")
    listed = {d["path"] for d in projectview.list_docs(proj)}
    assert listed.isdisjoint({".env", "secrets_codes.md", "SECRETS.txt", "CLAUDE.local.md"})


def _make_link(link: Path, target: Path) -> bool:
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True) if os.name == "nt" else None
    if r is not None and r.returncode == 0:
        return True
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


def test_a_link_pointing_outside_the_project_is_refused(proj, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("secret stuff", encoding="utf-8")
    if not _make_link(proj / "docs" / "plans", outside):
        pytest.skip("neither a junction nor a symlink could be created on this platform")
    with pytest.raises(ValueError):
        projectview.safe_path(proj, "docs/plans/x.md")
    assert all("plans" not in d["path"] for d in projectview.list_docs(proj))


def test_documents_are_listed_by_kind_with_their_age(proj):
    docs = {d["path"]: d for d in projectview.list_docs(proj)}
    assert docs["docs/specs/a.md"]["kind"] == "spec"
    assert docs["docs/specs/a.md"]["title"] == "Spec A"
    assert docs["README.md"]["kind"] == "readme"
    assert isinstance(docs["README.md"]["age_s"], (int, float)) and docs["README.md"]["age_s"] >= 0
    assert set(docs) == {"docs/specs/a.md", "README.md"}


def test_text_is_redacted(proj):
    (proj / "README.md").write_text(
        "password: hunter2\nhost 192.168.1.5\nmail me@example.com\nok line\n", encoding="utf-8")
    text = projectview.read_doc(proj, "README.md")["text"]
    assert "hunter2" not in text and "192.168.1.5" not in text and "me@example.com" not in text
    assert "ok line" in text
    assert projectview.redact("a@b.co") != "a@b.co"


def test_a_big_file_is_truncated_and_bad_bytes_do_not_fail(proj):
    (proj / "README.md").write_bytes(b"\xff\xfe bad " + b"a" * (projectview.MAX_DOC + 5000))
    out = projectview.read_doc(proj, "README.md")
    assert out["truncated"] is True
    assert len(out["text"]) <= projectview.MAX_DOC + 200
    small = projectview.read_doc(proj, "docs/specs/a.md")
    assert small["truncated"] is False and small["path"] == "docs/specs/a.md"


@pytest.mark.parametrize("name", [
    "CLAUDE.local.md.", "CLAUDE.local.md ", "CLAUDE.local.md::$DATA", "settings.local.json.",
    "key.pem ", "server.key::$DATA", "CLAUDE~1.MD",
])
def test_windows_name_aliases_of_forbidden_files_are_refused(proj, name):
    (proj / "CLAUDE.local.md").write_text("private", encoding="utf-8")
    (proj / "settings.local.json").write_text("{}", encoding="utf-8")
    (proj / "key.pem").write_text("k", encoding="utf-8")
    (proj / "server.key").write_text("k", encoding="utf-8")
    with pytest.raises(ValueError):
        projectview.read_doc(proj, name)


def test_a_junction_into_a_forbidden_folder_is_refused(proj):
    hidden = proj / "secrets_dir"
    hidden.mkdir()
    (hidden / "a.md").write_text("x", encoding="utf-8")
    if not _make_link(proj / "docs" / "alias", hidden):
        pytest.skip("neither a junction nor a symlink could be created on this platform")
    with pytest.raises(ValueError):
        projectview.safe_path(proj, "docs/alias/a.md")


@pytest.mark.parametrize("pad", [1995, 1996, 1999, 2000])
def test_redaction_works_across_the_old_piece_boundary(pad):
    for sample, leak in (("password: hunter2", "hunter2"), ("host 192.168.1.5 x", "192.168.1.5"),
                         ("me@example.com", "me@example.com")):
        out = projectview.redact("x" * pad + " " + sample)
        assert leak not in out


def test_redaction_of_a_huge_single_line_is_fast():
    import time
    text = "a" * projectview.MAX_DOC
    start = time.time()
    projectview.redact(text)
    assert time.time() - start < 1.0


def test_a_secret_cut_by_the_size_limit_is_not_exposed(proj):
    body = "a" * (projectview.MAX_DOC - 12) + " password: hunter2 tail"
    (proj / "README.md").write_text(body, encoding="utf-8")
    assert "hunter" not in projectview.read_doc(proj, "README.md")["text"]


# ---- Task 2: overview, architecture, tools ----


def test_overview_prefers_the_vault_note_then_the_readme_then_nothing(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    note = tmp_path / "note.md"
    note.write_text("# Note\nfrom vault\n", encoding="utf-8")
    assert projectview.overview(root, note)["source"] == "vault"
    assert "from vault" in projectview.overview(root, note)["text"]
    (root / "README.md").write_text("# R\nfrom readme\n", encoding="utf-8")
    out = projectview.overview(root, None)
    assert out["source"] == "readme" and "from readme" in out["text"]
    assert projectview.overview(root, tmp_path / "missing.md")["source"] == "readme"
    (root / "README.md").unlink()
    assert projectview.overview(root, None) == {"source": None, "text": "", "architecture": ""}


def test_architecture_section_is_extracted(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "README.md").write_text(
        "# T\nintro\n## Arquitetura\nlayer one password: hunter2\n### sub\nmore\n## Usage\nrun it\n",
        encoding="utf-8")
    arch = projectview.overview(root, None)["architecture"]
    assert "layer one" in arch and "more" in arch
    assert "run it" not in arch and "hunter2" not in arch


def test_mcp_server_names_are_listed_but_tokens_never_appear(tmp_path):
    root = tmp_path / "p"
    (root / ".claude").mkdir(parents=True)
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"github": {
        "command": "npx", "args": ["--token", "sk-abc1234567890"],
        "env": {"TOKEN": "sk-abc1234567890"}}}}), encoding="utf-8")
    (root / ".claude" / "settings.json").write_text(json.dumps({
        "mcpServers": {"other": {"env": {"TOKEN": "sk-abc1234567890"}}},
        "hooks": {"PreToolUse": [{"hooks": [{"command": "echo sk-abc1234567890"}]}]}}), encoding="utf-8")
    out = projectview.tools(root)
    dumped = json.dumps(out)
    assert "sk-abc" not in dumped and "TOKEN" not in dumped
    assert sorted(out["claude"]["mcp"]) == ["github", "other"]
    assert out["claude"]["hooks"] == ["PreToolUse"]


def test_settings_local_json_is_never_read(tmp_path):
    root = tmp_path / "p"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.local.json").write_text(json.dumps({
        "hooks": {"SecretEvent": []}, "mcpServers": {"hidden": {}}}), encoding="utf-8")
    out = projectview.tools(root)
    assert out["claude"]["hooks"] == [] and out["claude"]["mcp"] == []


def test_commands_are_detected_from_package_json_and_makefile(tmp_path):
    root = tmp_path / "p"
    (root / ".claude" / "skills" / "my-skill").mkdir(parents=True)
    (root / ".claude" / "commands").mkdir()
    (root / ".claude" / "commands" / "deploy.md").write_text("x", encoding="utf-8")
    (root / ".claude" / "agents").mkdir()
    (root / ".claude" / "agents" / "rev.md").write_text("x", encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"scripts": {"build": "SECRET=1 tsc", "test": "jest"}}),
                                       encoding="utf-8")
    (root / "Makefile").write_text("lint:\n\truff .\n.PHONY: lint\nall: lint\n", encoding="utf-8")
    (root / "README.md").write_text("# R\n## Tests\n```\npytest -q\n```\n", encoding="utf-8")
    out = projectview.tools(root)
    names = {c["name"] for c in out["commands"]}
    assert {"build", "test", "lint", "all"} <= names
    assert "SECRET" not in json.dumps(out) and "jest" not in json.dumps(out)
    assert out["claude"]["skills"] == ["my-skill"]
    assert out["claude"]["commands"] == ["deploy"] and out["claude"]["agents"] == ["rev"]
    assert len(out["commands"]) <= 20


def test_a_project_with_nothing_gives_empty_blocks(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "package.json").write_text("{not json", encoding="utf-8")
    (root / "pyproject.toml").write_text("= broken [", encoding="utf-8")
    (root / ".mcp.json").write_text("nope", encoding="utf-8")
    assert projectview.tools(root) == {"commands": [], "claude": {
        "skills": [], "commands": [], "agents": [], "mcp": [], "hooks": []}}


def test_secret_like_names_are_dropped_from_commands_and_claude_lists(tmp_path):
    root = tmp_path / "p"
    (root / ".claude" / "skills" / ("hunter2secretvalue99-" + "x" * 60)).mkdir(parents=True)
    (root / ".claude" / "skills" / "ok-skill").mkdir()
    (root / ".claude" / "agents").mkdir()
    (root / ".claude" / "agents" / "bad name!.md").write_text("x", encoding="utf-8")
    (root / "package.json").write_text(json.dumps({"scripts": {
        "build": "x", "sk-abcdefghijklmnop=1": "x", "a" * 80: "x"}}), encoding="utf-8")
    (root / "Makefile").write_text("make-ok:\n\tx\n", encoding="utf-8")
    (root / "README.md").write_text(
        "# R\n## Commands\n```\nmake hunter2secretvalue99\nnpm run test\nnpx tsc\n"
        "export TOKEN=sk-abcdefghijklmnop && pytest\ncurl -H 'Authorization: Bearer abc' x\npytest -q\n```\n",
        encoding="utf-8")
    out = projectview.tools(root)
    dumped = json.dumps(out)
    for leak in ("hunter2", "sk-abc", "TOKEN", "Bearer", "bad name", "a" * 60):
        assert leak not in dumped
    names = {c["name"] for c in out["commands"]}
    assert {"pytest", "build", "make-ok", "make", "npm run", "npx tsc"} <= names or \
        {"pytest", "build", "make-ok", "make"} <= names
    assert out["claude"]["skills"] == ["ok-skill"] and out["claude"]["agents"] == []


def test_vault_note_is_bounded_and_redacted_and_forbidden_names_refused(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    note = tmp_path / "note.md"
    note.write_text("password: hunter2\n" + "a" * (projectview.MAX_DOC * 5), encoding="utf-8")
    out = projectview.overview(root, note)
    assert "hunter2" not in out["text"] and len(out["text"]) <= projectview.MAX_DOC
    bad = tmp_path / "secrets.md"
    bad.write_text("# x\nhidden", encoding="utf-8")
    assert projectview.overview(root, bad)["source"] is None
    assert projectview.overview(root, tmp_path)["source"] is None


def test_claude_folders_that_leave_the_project_are_not_listed(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    outside = tmp_path / "outside"
    (outside / "skills" / "leaked").mkdir(parents=True)
    if not _make_link(root / ".claude", outside):
        pytest.skip("no junction or symlink available")
    assert projectview.tools(root)["claude"]["skills"] == []
    root2 = tmp_path / "p2"
    (root2 / ".claude").mkdir(parents=True)
    out2 = tmp_path / "out2"
    (out2 / "leaked2").mkdir(parents=True)
    if not _make_link(root2 / ".claude" / "skills", out2):
        pytest.skip("no junction or symlink available")
    assert projectview.tools(root2)["claude"]["skills"] == []


def test_deeply_nested_json_does_not_raise(tmp_path):
    root = tmp_path / "p"
    (root / ".claude").mkdir(parents=True)
    deep = "[" * 200000
    (root / "package.json").write_text(deep, encoding="utf-8")
    (root / ".mcp.json").write_text(deep, encoding="utf-8")
    (root / ".claude" / "settings.json").write_text(deep, encoding="utf-8")
    (root / "pyproject.toml").write_text("a=" + deep, encoding="utf-8")
    assert projectview.tools(root)["commands"] == []


def test_non_dict_mcp_servers_and_hooks_give_empty_lists(tmp_path):
    root = tmp_path / "p"
    (root / ".claude").mkdir(parents=True)
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": ["a"]}), encoding="utf-8")
    (root / ".claude" / "settings.json").write_text(json.dumps({"hooks": "x", "mcpServers": 3}), encoding="utf-8")
    claude = projectview.tools(root)["claude"]
    assert claude["mcp"] == [] and claude["hooks"] == []


def test_sections_ignore_fenced_comments_and_end_at_higher_headings(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "README.md").write_text(
        "# T\n```\n# Design not a heading\nfake\n```\n## Design\nreal\n```\n# comment\n```\nstill\n# Top\nafter\n",
        encoding="utf-8")
    arch = projectview.overview(root, None)["architecture"]
    assert "real" in arch and "comment" in arch and "still" in arch
    assert "fake" not in arch and "after" not in arch


def test_command_headings_need_whole_words(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "README.md").write_text("# R\n## Latest news\n```\nrm\n```\n## Contest\n```\nsudo\n```\n",
                                    encoding="utf-8")
    assert projectview.tools(root)["commands"] == []


# ---- identified fixes and request summaries (task 3) ----

REVIEW = """## Verdict
BLOCKED

## Findings
- **[CRITICAL] app.py:10 — SQL injection:** query built with string concat. token=sk-abcdefghijklmnopqrstuvwxyz123456
- **[HIGH] app.py:20 — Bug:** off by one.
- **[MEDIUM] app.py:30 — Style:** long function.
- **[LOW] app.py:40 — Nit:** naming.
"""


def _feat(fid, status="awaiting", stage="review", project="p", **kw):
    return {"id": fid, "title": f"T {fid}", "project": project, "status": status, "stage": stage,
            "kind": "feature", "created": 1, "cost": None, **kw}


def test_only_critical_and_high_findings_count():
    out = projectview.findings(REVIEW)
    assert [f["severity"] for f in out] == ["CRITICAL", "HIGH"]
    assert "SQL injection" in out[0]["text"] and "sk-abcdefghijklmnopqrstuvwxyz123456" not in out[0]["text"]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in out[0]["body"]


def test_a_finding_is_split_into_head_and_body_with_a_clean_text():
    f = projectview.findings(REVIEW)[1]
    assert (f["head"], f["body"]) == ("app.py:20 — Bug", "off by one.")
    assert f["text"] == "app.py:20 — Bug — off by one."
    assert "**" not in f["text"] and "[HIGH]" not in f["text"]


def test_bold_inside_the_head_stays_in_the_head():
    f = projectview.findings("- **[HIGH] the **cart** total is wrong:** rounding.")[0]
    assert f["head"] == "the cart total is wrong" and f["body"] == "rounding."


def test_a_finding_without_a_body_or_closing_marker():
    assert projectview.findings("- **[HIGH] app.py:3 — Crash:**")[0] == {
        "severity": "HIGH", "head": "app.py:3 — Crash", "body": "", "text": "app.py:3 — Crash"}
    f = projectview.findings("- **[CRITICAL] no closing marker here")[0]
    assert f["head"] == "no closing marker here" and f["body"] == "" and "**" not in f["text"]
    assert projectview.findings("- **[HIGH] x**")[0]["head"] == "x"


def test_only_the_body_is_clipped_never_the_head():
    head = "h" * 250
    f = projectview.findings(f"- **[HIGH] {head}:** " + "b" * 900)[0]
    assert f["head"] == head and len(f["body"]) <= 300 and f["body"].endswith("…")
    assert f["text"].startswith(head)


def test_a_portuguese_finding_is_parsed_the_same_way():
    f = projectview.findings("- **[HIGH] app.py:5 — Ação sem validação:** o formulário não valida o e-mail.")[0]
    assert f["head"] == "app.py:5 — Ação sem validação" and f["body"] == "o formulário não valida o e-mail."


def test_closed_and_cancelled_requests_contribute_no_fixes():
    feats = [_feat("a"), _feat("b", status="done"), _feat("c", status="cancelled")]
    out = projectview.fixes("p", feats, lambda fid: {"review": REVIEW}, [])
    assert {f["request"] for f in out} == {"a"} and len(out) == 2
    assert all({"head", "body", "text", "severity"} <= set(f) for f in out)
    assert all(f["source"] == "review" for f in out)


def test_a_blocked_todo_of_the_project_is_listed_and_other_projects_are_not():
    pending = [{"id": "1", "project": "p", "status": "blocked", "title": "Fix it"},
               {"id": "2", "project": "q", "status": "blocked", "title": "Other"},
               {"id": "3", "project": "p", "status": "awaiting", "title": "Not blocked"}]
    out = projectview.fixes("p", [], lambda fid: {}, pending)
    assert [(f["source"], f["request"], f["title"]) for f in out] == [("todo", "1", "Fix it")]


def test_plan_review_findings_come_from_the_planreview_artifact():
    feats = [_feat("a", stage="plan")]
    out = projectview.fixes("p", feats, lambda fid: {"planreview": REVIEW, "review": "- **[HIGH] x**"}, [])
    assert len(out) == 2 and all(f["source"] == "plan-review" for f in out)


def test_requests_are_split_into_open_and_closed_with_their_cost():
    feats = [_feat("a", cost={"cost_usd": 1.5, "workers": 2}), _feat("b", status="done"),
             _feat("c", project="q")]
    out = projectview.requests_view("p", feats)
    assert [r["id"] for r in out["open"]] == ["a"] and out["open"][0]["cost_usd"] == 1.5
    assert [r["id"] for r in out["closed"]] == ["b"] and out["closed"][0]["cost_usd"] is None


# ---- Task 5 fix round 1: JSON / YAML / INI / header secrets ----

@pytest.mark.parametrize("text,leak", [
    ('{"API_KEY": "abcdef123456", "STRIPE_SECRET_KEY": "rk_live_abc123", "Authorization": "Bearer xyzTOKEN123"}',
     ("abcdef123456", "rk_live_abc123", "xyzTOKEN123")),
    ("{'db_password': 'hunter22'}", ("hunter22",)),
    ('api_key = "zzz999yyy"', ("zzz999yyy",)),
    ("client_secret: s3cr3tvalue", ("s3cr3tvalue",)),
    ("Authorization: Bearer abc.def.ghi", ("abc.def.ghi",)),
    ("Authorization: Basic dXNlcjpwYXNz", ("dXNlcjpwYXNz",)),
    ('"pwd":   "p4ss"', ("p4ss",)),
    ("[core]\ncredential = abc123xyz", ("abc123xyz",)),
])
def test_quoted_and_spaced_secret_values_are_redacted(text, leak):
    out = projectview.redact(text)
    assert all(x not in out for x in leak), out


def test_benign_json_is_untouched():
    for text in ('{"name": "demo", "version": "1.0"}', '{"author": "Jane", "scripts": {"test": "pytest"}}'):
        assert projectview.redact(text) == text


def test_redaction_of_pathological_secret_words_is_fast():
    import time
    for text in ("key" * 70000, "token:" * 35000, '"key": "' + "a" * 200000, "auth " * 40000):
        start = time.time()
        projectview.redact(text)
        assert time.time() - start < 1.0


# ---- fix round 2: a secret split between the head and the body is still redacted ----

@pytest.mark.parametrize("line,secret", [
    ("- **[HIGH] password:** hunter2", "hunter2"),
    ("- **[CRITICAL] API_KEY:** abcdef123456", "abcdef123456"),
    ("- **[HIGH] token:** sk-abcdefghijklmnop", "sk-abcdefghijklmnop"),
    ("- **[HIGH] config.py:3 — Leak:** the file sets password=hunter22 in clear", "hunter22"),
])
def test_a_secret_across_head_and_body_is_redacted(line, secret):
    f = projectview.findings(line)[0]
    assert all(secret not in f[k] for k in ("head", "body", "text")), f
    assert "[secret]" in f["body"] and f["head"] and "**" not in f["text"]


def test_the_redacted_finding_keeps_its_head_and_body():
    f = projectview.findings("- **[HIGH] password:** hunter2 and more words")[0]
    assert f["head"] == "password" and f["body"].startswith("[secret]") and f["body"].endswith("and more words")


def test_a_huge_head_is_capped():
    f = projectview.findings("- **[HIGH] " + "h" * 5000 + ":** body")[0]
    assert len(f["head"]) <= 300 and f["head"].endswith("…") and f["body"] == "body"


# ---- fix round 3: Bearer/Basic across the split, PT/short key names, URL credentials ----

@pytest.mark.parametrize("line,secret", [
    ("- **[HIGH] Auth:** Bearer abc123", "abc123"),
    ("- **[HIGH] Authorization:** Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ("- **[HIGH] Auth:** bearer   eyJabc.def", "eyJabc.def"),
    ("- **[HIGH] Secret key:** Bearer abc", "abc"),
])
def test_a_bearer_or_basic_credential_across_the_split_is_masked_whole(line, secret):
    f = projectview.findings(line)[0]
    assert secret not in f["body"] and secret not in f["text"], f
    assert f["body"].startswith("[secret]")


@pytest.mark.parametrize("line,head,body", [
    ("- **[HIGH] the api_key is exposed:** in config", "the api_key is exposed", "in config"),
    ("- **[CRITICAL] SQL injection in login:** query built by concatenation", "SQL injection in login",
     "query built by concatenation"),
])
def test_harmless_findings_stay_readable(line, head, body):
    f = projectview.findings(line)[0]
    assert (f["head"], f["body"]) == (head, body)


@pytest.mark.parametrize("text,leak", [
    ("senha: hunter22", "hunter22"),
    ("SENHA = 'abc 123'", "abc 123"),
    ("pass: hunter22", "hunter22"),
    ("db pass=s3cr3t", "s3cr3t"),
    ("https://alice:s3cr3tpw@example.com/repo.git", "s3cr3tpw"),
    ("postgres://app:pa55word@db.internal:5432/x", "pa55word"),
])
def test_pt_and_short_key_names_and_url_credentials_are_redacted(text, leak):
    assert leak not in projectview.redact(text)


def test_url_credentials_keep_the_scheme_and_host():
    out = projectview.redact("clone https://alice:s3cr3tpw@example.com/repo.git now")
    assert out.startswith("clone https://alice:") and "@example.com/repo.git now" in out


@pytest.mark.parametrize("text", ["the tests passed: 12 of 12", "compass: north", "passage: long",
                                  "see https://example.com/a:b/c and http://host:8080/x",
                                  "mailto-like user@ host"])
def test_ordinary_words_and_urls_are_untouched(text):
    assert projectview.redact(text) == text


def test_the_new_rules_stay_linear_on_hostile_single_lines():
    import time as _t
    for text in ("pass:" * 40000, "senha " * 35000, "a://b:" * 35000, "https://" + "u:" * 100000, "x://" + "a" * 200000 + ":"):
        t0 = _t.perf_counter()
        projectview.redact(text)
        assert _t.perf_counter() - t0 < 1.0, text[:20]


# ---- final fix wave ----

def test_a_working_request_gives_no_fixes_because_its_review_is_stale():
    feats = [_feat("w", status="working"), _feat("a", status="awaiting"), _feat("f", status="failed"),
             _feat("b", status="blocked")]
    out = projectview.fixes("p", feats, lambda fid: {"review": REVIEW}, [])
    assert {f["request"] for f in out} == {"a", "f", "b"}


def test_a_token_like_script_key_and_fenced_line_never_appear_as_tool_names(tmp_path):
    tok = "ghp_" + "A1b2C3d4E5" * 3 + "Zz"          # 36 chars after the prefix, 40 in all
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "x", tok: "x"}}), encoding="utf-8")
    (tmp_path / "README.md").write_text(f"# R\n## Commands\n```\nnpm run test\nmake {tok}\n{tok}\n```\n", encoding="utf-8")
    out = projectview.tools(tmp_path)
    assert tok not in json.dumps(out) and "ghp_" not in json.dumps(out)
    assert "build" in {c["name"] for c in out["commands"]}
