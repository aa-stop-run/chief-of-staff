"""The page helpers added by the review of 2026-11-14 (the `// <review>` region of index.html), run with node."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "app" / "index.html"
SRC = HTML.read_text(encoding="utf-8")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
T = "const t=(s,v)=>s.replace(/\\{(\\w+)\\}/g,(m,k)=>k in v?v[k]:m);"
STAGES = 'const ST={feature:["spec","plan","build","review"],fix:["spec","plan","build","review"],maintenance:["plan","build","review"],analysis:["build","review"],ship:["build","review"],release:["plan","build","review"]};'


def call(expr: str):
    code = SRC[SRC.index("// <review>"):SRC.index("// </review>")] + f"\n{T}\n{STAGES}\nconsole.log(JSON.stringify({expr}))"
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("key,tag,editable,takes", [("/", "BODY", False, True), ("/", "TEXTAREA", False, False), ("/", "input", False, False),
                                                   ("/", "SELECT", False, False), ("/", "DIV", True, False), ("a", "BODY", False, False),
                                                   ("/", None, False, True)])
def test_the_slash_shortcut_never_takes_a_key_typed_in_a_field(key, tag, editable, takes):
    assert call(f"slashFocuses({json.dumps(key)},{json.dumps(tag)},{json.dumps(editable)})") is takes


def test_the_page_uses_the_helper_for_the_shortcut_and_no_native_confirm_dialogs():
    assert "slashFocuses(e.key,at.tagName,at.isContentEditable)" in SRC
    assert not re.search(r"[^.\w]confirm\(", SRC)


def test_open_cards_are_ordered_by_who_needs_the_user_first():
    fs = [{"id": "w", "status": "working", "history": [{"at": "2026-11-14T10:00:00"}]},
          {"id": "new", "status": "awaiting", "history": [{"at": "2026-11-14T11:50:00"}]},
          {"id": "f", "status": "failed", "history": [{"at": "2026-11-14T09:00:00"}]},
          {"id": "old", "status": "awaiting", "history": [{"at": "2026-11-14T08:00:00"}]},
          {"id": "w2", "status": "working", "history": []}]
    now = "Date.parse('2026-11-14T12:00:00')"
    assert call(f"sortOpen({json.dumps(fs)},{now}).map(f=>f.id)") == ["old", "new", "f", "w", "w2"]
    assert call(f"waitedS({json.dumps(fs[3])},{now})") == 4 * 3600
    assert call(f"waitedS({json.dumps(fs[4])},{now})") == 0


def test_the_tab_title_counts_what_needs_the_user():
    s = {"features": [{"status": "awaiting"}, {"status": "failed"}, {"status": "working"}, {"status": "done"}],
         "alerts": [{"kind": "blocked"}, {"kind": "failed"}]}
    assert call(f"pageTitle(needsYou({json.dumps(s)}))") == "(3) Chief of Staff"
    assert call("pageTitle(needsYou({}))") == "Chief of Staff"


def test_a_card_is_announced_once_per_phase_it_waits_on():
    fs = [{"id": "a", "stage": "spec", "status": "awaiting"}, {"id": "b", "stage": "plan", "status": "awaiting"},
          {"id": "c", "stage": "plan", "status": "working"}]
    assert call(f"newlyWaiting({json.dumps(fs)},new Set(['a|spec'])).map(f=>f.id)") == ["b"]
    assert call(f"newlyWaiting({json.dumps(fs)},new Set(['a|spec','b|spec'])).map(f=>f.id)") == ["b"]     # the same card, a new phase


def test_the_size_of_a_change_reads_as_one_short_line():
    assert call('diffText({files:6,insertions:214,deletions:37},t)') == "6 file(s), +214 −37"


@pytest.mark.parametrize("plan,near", [({"connected": True, "windows": {"five_hour": {"percent": 93, "expired": False}}}, True),
                                       ({"connected": True, "windows": {"five_hour": {"percent": 93, "expired": True}}}, False),
                                       ({"connected": True, "windows": {"five_hour": {"percent": 60, "expired": False}}}, False),
                                       ({"connected": False, "windows": {}}, False), (None, False)])
def test_a_nearly_used_allowance_is_noticed_before_a_phase_starts(plan, near):
    assert call(f"planNearlyUsed({json.dumps(plan)})") is near


@pytest.mark.parametrize("kind,next_kind", [("analysis", "feature"), ("feature", "feature"), ("fix", "fix"), ("maintenance", "maintenance"),
                                            ("ship", "feature"), ("release", "feature")])
def test_a_follow_up_request_names_the_request_it_follows(kind, next_kind):
    out = call(f'followUp({{id:"a-1",title:"Why is it slow?",project:"Alfa",kind:{json.dumps(kind)}}},t)')
    assert out["kind"] == next_kind and out["parent"] == "a-1" and out["project"] == "Alfa" and out["title"] == ""
    assert out["desc"].startswith("Follows the request “Why is it slow?” (a-1).")


@pytest.mark.parametrize("f,ok", [({"kind": "feature", "stage": "spec", "status": "awaiting"}, True),
                                  ({"kind": "analysis", "stage": "build", "status": "failed"}, True),
                                  ({"stage": "spec", "status": "awaiting"}, True),                       # an old request without a kind is a feature
                                  ({"kind": "feature", "stage": "plan", "status": "awaiting"}, False),
                                  ({"kind": "feature", "stage": "spec", "status": "working"}, False),
                                  ({"kind": "ship", "stage": "build", "status": "awaiting"}, False),
                                  ({"kind": "release", "stage": "plan", "status": "awaiting"}, False)])
def test_the_kind_switch_shows_only_where_the_server_accepts_it(f, ok):
    assert call(f"canChangeKind({json.dumps(f)},ST)") is ok


def test_the_card_draws_the_new_blocks():
    for needle in ("${waitChip(f)}", "${parentLine(f)}", "${sizeNote(f)}", "${diffLine(f)}", "${kindSwitch(f)}", "${finishHtml(f)}", "+lastWords(f)",
                   'id="notifBtn"', 'id="scanBtn"', 'id="doctorBtn"', '"?fh="+encodeURIComponent(FH)'):
        assert needle in SRC, needle


def test_split_label_on_the_plan_card():
    plan = lambda sp: json.dumps({"stage": "plan", "plan_split": sp})
    assert call(f"splitLabel({plan({'n': 3, 'errors': 0})},t)") == {"text": "Split: 3 parts", "warn": False}
    assert call(f"splitLabel({plan({'n': 1, 'errors': 0})},t)")["text"] == "Split: 1 part"
    bad = call(f"splitLabel({plan({'n': 0, 'errors': 1})},t)")
    assert bad["warn"] is True and "could not be read" in bad["text"]
    assert call('splitLabel({stage:"build",plan_split:{n:3,errors:0}},t)') is None
    assert call('splitLabel({stage:"plan"},t)') is None


def test_the_card_shows_the_split_note_and_pt_has_the_strings():
    assert "${sizeNote(f)}${splitNote(f)}" in SRC
    assert '"Split: {n} parts":"Divisão: {n} partes"' in SRC


def test_split_block_helpers():
    parts = [{"title": "A", "stage": "build", "status": "working", "ready": False},
             {"title": "B", "stage": "review", "status": "awaiting", "ready": True}]
    rows = call(f"partsRows({json.dumps({'parts': parts})},t)")
    assert [r["text"] for r in rows] == ["A — Implementation · Working", "B — Review · To decide ✓"] and rows[1]["ready"] is True
    assert call("partsRows({},t)") == []
    labels = [call(f'splitStateLabel({{split_state:"{k}"}},t)') for k in ("running", "partial", "ready", "integrating", "integrated")]
    assert all(labels) and len(set(labels)) == 5
    assert call("splitStateLabel({},t)") is None and call('splitStateLabel({split_state:"x"},t)') is None
    summ = {"integration_summary": {"parts": {"a": "APPROVED"}, "conflicts": 0, "tests": "10 passed, 0 failed"}}
    assert call(f"integrationLine({json.dumps(summ)},t)") == "Combined: 0 conflict(s), tests: 10 passed, 0 failed"
    assert call("integrationLine({},t)") is None


def test_no_follow_up_on_a_fanned_out_parent_and_the_page_renders_the_block():
    assert call('canFollowUp({status:"done"})') is True
    assert call('canFollowUp({status:"done",split:{tasks:[]}})') is False
    assert call('canFollowUp({status:"working"})') is False
    assert "const follow=canFollowUp(f)?" in SRC and "${splitNote(f)}${splitBlock(f)}" in SRC
    assert '"Parts combined":"Partes combinadas"' in SRC


@pytest.mark.parametrize("event", ["fanned out", "fan-out launch failed", "integration created", "integration failed",
                                   "integration launch failed", "integration cleanup failed"])
def test_every_split_history_event_has_a_portuguese_string(event):
    assert f'  "{event}":"' in SRC


def test_a_held_part_says_what_it_waits_for_and_is_not_a_decision():
    held = {"status": "awaiting", "held": True, "held_for": ["API", "DB"]}
    assert call(f"heldLine({json.dumps(held)},t)") == {"text": "Waiting for «API», «DB» to be committed and reviewed; it starts by itself after that.", "warn": False}
    assert call('heldLine({held:true,held_error:"boom"},t)') == {"text": "This part cannot start yet: boom", "warn": True}
    assert call('heldLine({held:true,held_for:[]},t)')["text"].startswith("Starting soon")
    assert call('heldLine({status:"awaiting"},t)') is None
    assert call(f"decides({json.dumps(held)})") is False
    assert call('decides({status:"awaiting"})') is True and call('decides({status:"failed",held:true})') is True
    assert call('needsYou({features:[{status:"awaiting",held:true},{status:"awaiting"},{status:"failed"}],alerts:[]})') == 2


def test_open_cards_sort_a_held_part_with_the_work_under_way():
    fs = [{"id": "h", "status": "awaiting", "held": True, "history": []}, {"id": "w", "status": "working", "history": []},
          {"id": "d", "status": "awaiting", "history": []}, {"id": "f", "status": "failed", "history": []}]
    assert [c["id"] for c in call(f"sortOpen({json.dumps(fs)},0)")] == ["d", "f", "h", "w"]


def test_the_card_hides_the_decision_controls_of_a_held_part():
    assert "${f.held?heldNote(f):statusLine(f)}" in SRC and "if(f.held)return heldActions(f);" in SRC
    for event in ("part held", "held part released", "held part cancelled", "held part launch failed"):
        assert f'  "{event}":"' in SRC
    assert '"Waiting":"À espera"' in SRC
