"""The «Ask the worker» block on every request card (app/index.html), run with node on stubbed helpers."""
import json
import subprocess
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "app" / "index.html"


def render(f, ui=None):
    src = HTML.read_text(encoding="utf-8")
    esc_line = src[src.index("const esc=s=>"):src.index("\n", src.index("const esc=s=>"))]
    fn = src[src.index("function qaHtml"):src.index("function featCard")]
    code = (esc_line + "\nconst tt=s=>s;const ago=()=>'5 min ago';const md=s=>'<p>'+esc(s)+'</p>';\n"
            f"const FUI={{}};const fui=id=>FUI[id]||(FUI[id]={json.dumps(ui or {})});\n" + fn +
            f"\nconsole.log(JSON.stringify(qaHtml({json.dumps(f)})))")
    r = subprocess.run(["node", "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def card(qa=None):
    return {"id": "req-1", "qa": qa or []}


def ask_button(html):
    return html[html.index('data-act="ask"'):html.index("</button>", html.index('data-act="ask"'))]


def test_an_empty_block_is_closed_with_a_disabled_button():
    h = render(card())
    assert 'data-ask="req-1"' in h and 'data-act="ask"' in h and "<details" in h and " open" not in h.split(">")[0]
    assert "disabled" in ask_button(h) and 'class="thread"' not in h


def test_the_button_enables_with_a_draft_and_the_block_stays_open_when_the_user_opened_it():
    h = render(card(), {"ask": "Why?", "askOpen": True})
    assert "disabled" not in ask_button(h) and h.split(">")[0].endswith("open")
    assert ">Why?</textarea>" in h


def test_answers_questions_and_failures_are_all_shown():
    h = render(card([
        {"n": 1, "q": "First?", "at": "2026-11-12T08:00:00", "status": "answered", "answer": "The first answer."},
        {"n": 2, "q": "Second?", "at": "2026-11-12T08:05:00", "status": "failed"},
        {"n": 3, "q": "Third?", "at": "2026-11-12T08:10:00", "status": "working"}]))
    assert "First?" in h and "The first answer." in h
    assert "The worker stopped without answering. Ask again." in h
    assert "Reading the work and preparing the answer…" in h
    assert "<em>3</em>" in h


def test_while_a_question_is_being_answered_nothing_more_can_be_sent_and_the_users_choice_to_close_it_is_kept():
    h = render(card([{"n": 1, "q": "Q?", "at": "x", "status": "working"}]), {"ask": "Another?"})
    assert not h.split(">")[0].endswith("open") and 'data-busy="1"' in h and "disabled" in ask_button(h)
    assert 'role="log" aria-live="polite"' in h


def test_markup_in_a_question_or_a_draft_is_escaped():
    h = render(card([{"n": 1, "q": "<img src=x onerror=alert(1)>", "at": "x", "status": "failed"}]), {"ask": "<b>draft</b>"})
    assert "<img" not in h and "&lt;img" in h
    assert "<b>draft</b>" not in h and "&lt;b&gt;draft" in h


def test_the_block_is_on_every_card_and_the_redraw_never_eats_what_is_being_typed():
    src = HTML.read_text(encoding="utf-8")
    assert "${actions(f)}${qaHtml(f)}" in src
    seg = src[src.index("function renderFeatures"):src.index("let doneOpen")]
    assert seg.count("[data-ask]") == 2                                  # the typing guard and the done list
    assert 'body.question=(u.ask||"").trim()' in src and "featClick" in src


def test_while_the_user_types_the_answers_still_arrive_without_touching_the_box():
    src = HTML.read_text(encoding="utf-8")
    seg = src[src.index("function renderFeatures"):src.index("let doneOpen")]
    assert seg.count("patchQa(") == 1 and "syncAround(" in seg           # the done list here; the open list goes through syncAround (test_page_sync.py)
    fn = src[src.index("function patchQa"):src.index("function featCard")]
    assert "textarea" not in fn and ".thread" in fn and "disabled" in fn
