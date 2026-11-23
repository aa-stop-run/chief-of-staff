"""The fan-out helpers (scripts/fanout.py), tested first."""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys_path = str(Path(__file__).resolve().parent.parent / "scripts")
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)
import fanout  # noqa: E402
import features  # noqa: E402

NOW = datetime(2026, 11, 23, 10, 5, 7)


# --------------------------------------------------------------------------- B1 — part builders

def test_part_id_format_and_id_re():
    pid = fanout.part_id("Export-Trips-260101000000", "API", set(), NOW)
    assert pid == "export-trips-260101000000-api-261123100507"
    assert features.ID_RE.match(pid) and len(pid) <= 81


def test_part_id_same_second_never_collides():
    first = fanout.part_id("p", "a", set(), NOW)
    second = fanout.part_id("p", "a", {first}, NOW)
    third = fanout.part_id("p", "a", {first, second}, NOW)
    assert len({first, second, third}) == 3 and second.endswith("-2") and third.endswith("-3")
    assert fanout.part_id("p", "a", {first}, NOW) != fanout.part_id("p", "b", {first}, NOW)


def test_part_id_long_parent_is_still_valid():
    pid = fanout.part_id("a" * 52, "x" * 20, set(), NOW)
    assert features.ID_RE.match(pid) and len(pid) <= 81


def test_part_title_prefix_and_cut():
    t = fanout.part_title("export-trips-260101000000-abcdef", {"id": "api", "title": "Export endpoint"})
    assert t == "[export-trips-26010100000/api] Export endpoint"
    long = fanout.part_title("p", {"id": "a", "title": "x" * 300})
    assert len(long) == 120 and long.endswith("…")
    assert fanout.part_title("a" * 23 + "-b", {"id": "x", "title": "t"}) == "[" + "a" * 23 + "/x] t"


def test_criteria_section_en_pt_and_missing():
    spec = "# S\n\n## Goal\nx\n\n## Acceptance criteria\n- one\n- two\n\n### Sub\nkept\n\n## Other\ny\n"
    sec = fanout.criteria_section(spec)
    assert sec.startswith("## Acceptance criteria") and "- two" in sec and "kept" in sec and "Other" not in sec
    assert "- um" in fanout.criteria_section("### Critérios de aceitação\n- um\n")
    assert fanout.criteria_section("no such section") == "" and fanout.criteria_section("") == ""


def test_part_desc_content_and_limits():
    task = {"id": "api", "title": "Export endpoint",
            "files": [{"path": "src/a.py", "new": False}, {"path": "tests/t.py", "new": True}]}
    d = fanout.part_desc("Export trips", task, "## Acceptance criteria\n- works")
    assert "Export endpoint" in d and "- src/a.py" in d and "- tests/t.py (new)" in d
    assert "Touch only these files; ask for a follow-up request if you need others." in d
    assert "keep only the criteria that concern your files" in d and "- works" in d
    big = fanout.part_desc("T" * 100, task, "Q" * 5000)
    assert len(big) <= 4000 and big.count("Q") <= 1500
    assert "keep only" not in fanout.part_desc("T", task, "")


def test_part_desc_for_a_maintenance_parent_uses_its_own_desc():
    task = {"id": "a", "title": "Rename", "files": [{"path": "x.py", "new": False}]}
    assert "keep the behaviour" in fanout.part_desc("Tidy", task, "keep the behaviour")


def test_part_spec():
    s = fanout.part_spec("parent-1", {"id": "a", "title": "Title"}, "the desc")
    assert s.startswith("# Title") and "the desc" in s and "Part of parent-1" in s


def test_kind_allows():
    assert fanout.START_STAGE == "plan"
    assert fanout.kind_allows({}) and fanout.kind_allows({"kind": "fix"}) and fanout.kind_allows({"kind": "maintenance"})
    for k in ("ship", "release", "analysis"):
        assert not fanout.kind_allows({"kind": k})
    assert not fanout.kind_allows({"kind": "feature", "part": "api"})


# --------------------------------------------------------------------------- B2 — decide

def _plan(tasks, heading="## Split"):
    return f"# Plan\n\n{heading}\n\n```json\n{json.dumps({'tasks': tasks})}\n```\n"


A = {"id": "a", "title": "A", "files": ["a.py"]}
B = {"id": "b", "title": "B", "files": ["b.py"]}
FILES = {"a.py", "b.py"}


def _d(plan, f=None, q=(), files=FILES):
    return fanout.decide(f if f is not None else {"kind": "feature"}, plan, list(q) if q is not None else None, files)


def test_decide_no_split_is_linear():
    assert _d("# Plan\nnothing") == {"action": "linear", "reason": "", "split": None}


def test_decide_other_kinds_and_parts_stay_linear():
    for k in ("ship", "release", "analysis"):
        d = _d(_plan([A, B]), {"kind": k})
        assert d["action"] == "linear" and d["reason"] == "kind"
    d = _d(_plan([A, B]), {"kind": "feature", "part": "a"})
    assert d["action"] == "linear" and d["reason"] == "part"


def test_decide_open_or_unreadable_questions_refuse():
    for q in (None, [{"id": "q1"}]):
        d = fanout.decide({"kind": "feature"}, _plan([A, B]), q, FILES)
        assert d["action"] == "refuse" and "open questions" in d["reason"]


def test_decide_blocking_findings_refuse_and_name_the_codes():
    overlap = _d(_plan([A, {"id": "b", "title": "B", "files": ["a.py"]}]))
    assert overlap["action"] == "refuse" and "overlap" in overlap["reason"] and "ask for changes" in overlap["reason"]
    many = [{"id": f"t{i}", "title": "T", "files": [f"f{i}.py"]} for i in range(11)]
    big = _d(_plan(many), files={f"f{i}.py" for i in range(11)})
    assert big["action"] == "refuse" and "size" in big["reason"]
    assert _d("## Split\n\nno block\n")["action"] == "refuse"


def test_decide_single_part_is_linear():
    d = _d(_plan([A]))
    assert d["action"] == "linear" and d["reason"] == "single part"


def test_decide_clean_split_fans_out():
    d = _d(_plan([A, B]))
    assert d["action"] == "fan_out" and d["reason"] == "" and len(d["split"]["tasks"]) == 2
    assert _d(_plan([A, B]), {"kind": "maintenance"})["action"] == "fan_out"


def test_autopilot_blocks():
    assert fanout.autopilot_blocks({"kind": "feature"}, _plan([A, B])) is True
    assert fanout.autopilot_blocks({"kind": "feature"}, "## Split\n\nbroken\n") is True
    assert fanout.autopilot_blocks({"kind": "feature"}, _plan([A])) is False
    assert fanout.autopilot_blocks({"kind": "feature"}, "no split") is False
    assert fanout.autopilot_blocks({"kind": "feature", "part": "a"}, _plan([A, B])) is False
    assert fanout.autopilot_blocks({"kind": "release"}, _plan([A, B])) is False
