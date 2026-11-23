"""The split & fan-out structural helpers (scripts/splitplan.py), tested first."""
import json
import re
import subprocess
from pathlib import Path

import pytest

sys_path = str(Path(__file__).resolve().parent.parent / "scripts")
import sys  # noqa: E402
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)
import splitplan  # noqa: E402


def _plan(body: str, heading: str = "## Split") -> str:
    return f"# Plan\n\nProse.\n\n{heading}\n\n```json\n{body}\n```\n\n## Tests\nrun it\n"


# --------------------------------------------------------------------------- S1 — parse_split

def test_parse_split_none_without_the_section():
    assert splitplan.parse_split("") is None
    assert splitplan.parse_split("# Plan\n\nno split here\n") is None


def test_parse_split_en_section_two_tasks():
    body = json.dumps({"tasks": [
        {"id": "api", "title": "Export endpoint",
         "files": ["src/export.py", {"new": "tests/test_export.py"}], "depends_on": []},
        {"id": "beta", "title": "Beta"},
    ]})
    got = splitplan.parse_split(_plan(body))
    assert got["errors"] == []
    t = got["tasks"]
    assert len(t) == 2
    assert t[0] == {"id": "api", "title": "Export endpoint",
                    "files": [{"path": "src/export.py", "new": False},
                              {"path": "tests/test_export.py", "new": True}],
                    "depends_on": []}
    assert t[1] == {"id": "beta", "title": "Beta", "files": [], "depends_on": []}


def test_parse_split_pt_section_and_field_names():
    body = json.dumps({"tarefas": [
        {"id": "a", "titulo": "A", "ficheiros": ["x.py"], "depende_de": []},
        {"id": "b", "titulo": "B", "ficheiros": [{"new": "y.py"}], "depende_de": ["a"]},
        {"id": "c", "titulo": "C"},
    ]})
    got = splitplan.parse_split(_plan(body, "## Divisão"))
    assert got["errors"] == [] and len(got["tasks"]) == 3
    assert got["tasks"][0]["files"] == [{"path": "x.py", "new": False}]
    assert got["tasks"][1]["files"] == [{"path": "y.py", "new": True}]
    assert got["tasks"][1]["depends_on"] == ["a"]


def test_parse_split_section_without_json_block():
    assert splitplan.parse_split("## Split\n\njust words\n") == {"tasks": [], "errors": ["no json block"]}


def test_parse_split_invalid_json():
    got = splitplan.parse_split(_plan("{ not json"))
    assert got["tasks"] == []
    assert got["errors"][0].startswith("invalid json: ")


def test_parse_split_skips_non_object_tasks_and_notes_them():
    got = splitplan.parse_split(_plan(json.dumps({"tasks": ["x", {"id": "a"}]})))
    assert [t["id"] for t in got["tasks"]] == ["a"]
    assert len(got["errors"]) == 1


def test_parse_split_only_the_first_block_before_the_next_heading():
    text = ("## Split\n\n```json\n" + json.dumps({"tasks": [{"id": "a"}]}) + "\n```\n\n"
            "```json\n" + json.dumps({"tasks": [{"id": "z"}]}) + "\n```\n")
    assert [t["id"] for t in splitplan.parse_split(text)["tasks"]] == ["a"]
    other = "## Split\n\nnothing\n\n## Other\n\n```json\n" + json.dumps({"tasks": [{"id": "z"}]}) + "\n```\n"
    assert splitplan.parse_split(other)["errors"] == ["no json block"]


def test_parse_split_never_raises_on_odd_input():
    assert splitplan.parse_split(None) is None
    assert splitplan.parse_split(_plan(json.dumps({"tasks": "nope"})))["errors"]
    assert splitplan.parse_split(_plan("[1, 2]"))["tasks"] == []


def test_summary():
    assert splitplan.summary("no section") is None
    assert splitplan.summary(_plan(json.dumps({"tasks": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}))) == {"n": 3, "errors": 0}
    assert splitplan.summary(_plan("{ bad")) == {"n": 0, "errors": 1}


# --------------------------------------------------------------------------- S3 — norm_path & repo_files

def test_norm_path_normalises_windows_and_dot_slash():
    assert splitplan.norm_path("Src\\A.py") == "src/a.py"
    assert splitplan.norm_path("./src/a.py") == "src/a.py"
    assert splitplan.norm_path("src/") == "src"
    assert splitplan.norm_path("SRC/B.C") == "src/b.c"


def test_norm_path_is_case_and_separator_insensitive():
    a = splitplan.norm_path("Src\\A.py")
    b = splitplan.norm_path("./src/a.py")
    c = splitplan.norm_path("src/A.PY")
    assert a == b == c


@pytest.fixture
def git_repo(tmp_path):
    def run(*args, cwd=None):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run("init", cwd=repo)
    (repo / "a.py").write_text("x")
    sub = repo / "src"; sub.mkdir()
    (sub / "b.py").write_text("y")
    run("add", ".", cwd=repo)
    run("commit", "-m", "init", cwd=repo)
    return repo


def test_repo_files_returns_committed_paths_normalised(git_repo):
    got = splitplan.repo_files(str(git_repo))
    assert got == {"a.py", "src/b.py"}


def test_repo_files_never_raises_on_non_repos_or_missing():
    import tempfile
    plain = Path(tempfile.mkdtemp())
    (plain / "x.py").write_text("x")
    assert splitplan.repo_files(str(plain)) == set()
    assert splitplan.repo_files(str(Path(tempfile.gettempdir()) / "no-such-folder-xyz")) == set()
    assert splitplan.repo_files("") == set()


# --------------------------------------------------------------------------- S2 — check_split

def _split(tasks):
    return {"tasks": tasks, "errors": []}


def test_check_split_clean_two_parts_returns_no_findings():
    sp = _split([
        {"id": "api", "title": "A", "files": [{"path": "src/a.py", "new": False}], "depends_on": []},
        {"id": "web", "title": "B", "files": [{"path": "src/b.py", "new": True}], "depends_on": []},
    ])
    assert splitplan.check_split(sp, {"src/a.py"}) == []


def test_check_split_parse_error_is_high():
    sp = {"tasks": [], "errors": ["invalid json: x"]}
    f = splitplan.check_split(sp, set())
    assert any(x["code"] == "parse-error" and x["severity"] == "HIGH" for x in f)


def test_check_split_single_part_is_medium():
    sp = _split([{"id": "a", "title": "A", "files": [], "depends_on": []}])
    f = splitplan.check_split(sp, set())
    assert any(x["code"] == "single-part" and x["severity"] == "MEDIUM" for x in f)


def test_check_split_more_than_ten_is_medium_size():
    sp = _split([{"id": f"t{i}", "title": "", "files": [], "depends_on": []} for i in range(11)])
    f = splitplan.check_split(sp, set())
    assert any(x["code"] == "size" and x["severity"] == "MEDIUM" for x in f)


def test_check_split_bad_ids_are_high():
    sp = _split([
        {"id": "", "title": "", "files": [], "depends_on": []},
        {"id": "dup", "title": "", "files": [], "depends_on": []},
        {"id": "dup", "title": "", "files": [], "depends_on": []},
        {"id": "Bad_ID!", "title": "", "files": [], "depends_on": []},
    ])
    f = splitplan.check_split(sp, set())
    assert any(x["code"] == "bad-id" and x["severity"] == "HIGH" for x in f)


def test_check_split_a_part_may_depend_on_an_earlier_part():
    sp = _split([
        {"id": "a", "title": "", "files": [], "depends_on": []},
        {"id": "b", "title": "", "files": [], "depends_on": ["a"]},
        {"id": "c", "title": "", "files": [], "depends_on": ["a", "b"]},
    ])
    assert [x for x in splitplan.check_split(sp, set()) if x["code"] == "bad-dep"] == []


@pytest.mark.parametrize("deps,bad", [
    ({"a": ["b"], "b": []}, ["a"]),                    # depends on a LATER part (that is how a cycle would start)
    ({"a": [], "b": ["ghost"]}, ["b"]),                # unknown id
    ({"a": ["a"], "b": []}, ["a"]),                    # itself
    ({"a": [], "b": ["a", "a"]}, ["b"]),               # the same dependency twice
    ({"a": ["b"], "b": ["a"]}, ["a"]),                 # a cycle: only its edge pointing forward is wrong
])
def test_check_split_bad_dependencies_are_high(deps, bad):
    sp = _split([{"id": k, "title": "", "files": [], "depends_on": v} for k, v in deps.items()])
    f = [x for x in splitplan.check_split(sp, set()) if x["code"] == "bad-dep"]
    assert len(f) == 1 and f[0]["severity"] == "HIGH" and sorted(f[0]["tasks"]) == bad


def test_check_split_overlap_is_high_and_case_insensitive():
    sp = _split([
        {"id": "a", "title": "", "files": [{"path": "Src/A.py", "new": False}], "depends_on": []},
        {"id": "b", "title": "", "files": [{"path": "./src/a.py", "new": True}], "depends_on": []},
    ])
    f = splitplan.check_split(sp, set())
    assert any(x["code"] == "overlap" and x["severity"] == "HIGH" for x in f)


def test_check_split_unknown_file_is_medium_but_new_files_never():
    sp = _split([
        {"id": "a", "title": "", "files": [{"path": "missing.py", "new": False}], "depends_on": []},
        {"id": "b", "title": "", "files": [{"path": "brand_new.py", "new": True}], "depends_on": []},
    ])
    f = splitplan.check_split(sp, set())
    codes = [x["code"] for x in f]
    assert "unknown-file" in codes
    # the new file must not be flagged unknown
    uf = [x for x in f if x["code"] == "unknown-file"][0]
    assert "brand_new.py" not in uf["text"].lower() or all(
        t != "b" for t in uf.get("tasks", []))


def test_check_split_does_not_mutate_arguments():
    import copy
    sp = _split([
        {"id": "a", "title": "", "files": [{"path": "x.py", "new": False}], "depends_on": []},
        {"id": "b", "title": "", "files": [{"path": "x.py", "new": True}], "depends_on": []},
    ])
    repo = {"x.py"}
    sp_copy, repo_copy = copy.deepcopy(sp), copy.deepcopy(repo)
    splitplan.check_split(sp, repo)
    assert sp == sp_copy and repo == repo_copy


def test_is_blocking_true_only_for_high():
    high = [{"severity": "HIGH", "code": "overlap", "text": "", "tasks": []}]
    med = [{"severity": "MEDIUM", "code": "size", "text": "", "tasks": []}]
    assert splitplan.is_blocking(high) is True
    assert splitplan.is_blocking(med) is False
    assert splitplan.is_blocking([]) is False


def test_check_split_findings_carry_tasks_and_text():
    sp = _split([
        {"id": "a", "title": "", "files": [{"path": "x.py", "new": False}], "depends_on": []},
        {"id": "b", "title": "", "files": [{"path": "x.py", "new": True}], "depends_on": []},
    ])
    f = splitplan.check_split(sp, set())
    ov = [x for x in f if x["code"] == "overlap"][0]
    assert set(ov["tasks"]) == {"a", "b"}
    assert isinstance(ov["text"], str) and ov["text"]


# --------------------------------------------------------------------------- S4 — split_line

# --------------------------------------------------------------------------- S7 — merge_review & finding_line

def _review_en():
    return "## Verdict\nAPPROVED. Looks fine.\n\n## Findings\nNone.\n"


def test_finding_line_format_en():
    f = {"severity": "HIGH", "code": "overlap", "text": "same file in two parts", "tasks": ["a", "b"]}
    line = splitplan.finding_line(f, "en")
    assert "**[HIGH] overlap" in line and "same file in two parts" in line


def test_finding_line_format_pt():
    f = {"severity": "MEDIUM", "code": "size", "text": "muitas partes", "tasks": []}
    line = splitplan.finding_line(f, "pt")
    assert "**[MEDIUM] size" in line and "muitas partes" in line


def test_merge_review_empty_findings_leaves_text_unchanged():
    text = _review_en()
    assert splitplan.merge_review(text, [], "en") == text


def test_merge_review_high_blocks_an_approved_review():
    f = {"severity": "HIGH", "code": "overlap", "text": "same file in two parts", "tasks": ["a", "b"]}
    out = splitplan.merge_review(_review_en(), [f], "en")
    assert "BLOCKED" in out.upper()
    assert "**[HIGH] overlap" in out
    # the «None.» line is removed
    assert "None." not in out


def test_merge_review_pt_high_blocks():
    f = {"severity": "HIGH", "code": "overlap", "text": "mesmo ficheiro em duas partes", "tasks": []}
    text = "## Veredicto\nAPROVADO. Tudo bem.\n\n## Achados\nNenhum.\n"
    out = splitplan.merge_review(text, [f], "pt")
    assert "BLOQUEADO" in out.upper()


def test_merge_review_medium_only_keeps_verdict():
    f = {"severity": "MEDIUM", "code": "size", "text": "muitas partes", "tasks": []}
    out = splitplan.merge_review(_review_en(), [f], "en")
    # verdict stays APPROVED (medium does not block)
    assert "APPROVED" in out.upper() and "BLOCKED" not in out.upper()


def test_merge_review_appends_findings_section_when_missing():
    text = "## Verdict\nAPPROVED. Fine.\n"
    f = {"severity": "HIGH", "code": "overlap", "text": "x", "tasks": []}
    out = splitplan.merge_review(text, [f], "en")
    assert "## Findings" in out


def test_merge_review_appends_verdict_when_missing_and_high():
    text = "## Checks\nRan ls.\n"
    f = {"severity": "HIGH", "code": "overlap", "text": "x", "tasks": []}
    out = splitplan.merge_review(text, [f], "en")
    assert out.upper().startswith("## VERDICT") or "## Verdict" in out


def test_merge_review_medium_only_no_verdict_returns_unchanged():
    text = "## Checks\nRan ls.\n"
    f = {"severity": "MEDIUM", "code": "size", "text": "x", "tasks": []}
    assert splitplan.merge_review(text, [f], "en") == text


def test_merge_review_is_idempotent():
    f = {"severity": "HIGH", "code": "overlap", "text": "same file in two parts", "tasks": ["a", "b"]}
    once = splitplan.merge_review(_review_en(), [f], "en")
    twice = splitplan.merge_review(once, [f], "en")
    assert twice.count("**[HIGH] overlap") == 1


def test_merge_review_result_parses_as_blocked_with_high():
    import features
    f = {"severity": "HIGH", "code": "overlap", "text": "same file in two parts", "tasks": ["a", "b"]}
    out = splitplan.merge_review(_review_en(), [f], "en")
    verdict, counts = features.parse_review(out)
    assert verdict == "blocked" and counts["HIGH"] >= 1


def test_merge_review_does_not_mutate_arguments():
    import copy
    text = _review_en()
    f = {"severity": "HIGH", "code": "overlap", "text": "x", "tasks": ["a"]}
    text_copy, f_copy = copy.deepcopy(text), copy.deepcopy(f)
    splitplan.merge_review(text, [f], "en")
    assert text == text_copy and f == f_copy


# --------------------------------------------------------------------------- S6 — prompt constants

@pytest.mark.parametrize("lang,heading,keys", [("en", "## Split", ("tasks", "files", "depends_on")),
                                               ("pt", "## Divisão", ("tarefas", "ficheiros", "depende_de"))])
def test_prompts_and_parser_agree(lang, heading, keys):
    import prompts
    import prompts_pt
    mod = prompts if lang == "en" else prompts_pt
    text = mod.SPLIT_FORMAT
    assert heading in text
    for k in keys:
        assert k in text
    body = re.search(r"```json\n(.*?)\n```", text, re.S).group(1)
    sp = splitplan.parse_split(f"{heading}\n\n```json\n{body}\n```\n")
    assert sp["errors"] == [] and len(sp["tasks"]) == 2
    files = {f["path"] for t in sp["tasks"] for f in t["files"] if not f["new"]}
    assert splitplan.check_split(sp, files) == []
    assert "{" not in mod.SPLIT_CHECK and "}" not in mod.SPLIT_CHECK
    assert "split-coupling" in mod.SPLIT_CHECK


def test_prompts_state_the_rules():
    import prompts
    import prompts_pt
    en = prompts.SPLIT_FORMAT
    assert "EMPTY" in en and "EARLIER" in en and "2 to 10" in en and "two parts" in en and "release" in en and "never both" in en
    pt = prompts_pt.SPLIT_FORMAT
    assert "VAZIO" in pt and "ANTERIORES" in pt and "2 a 10" in pt and "duas partes" in pt and "release" in pt and "nunca as duas" in pt
