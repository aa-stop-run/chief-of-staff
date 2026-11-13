"""The page knows the «Publish» kind and the worker suggestions (checked on the source; the pure logic is covered elsewhere)."""
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "app" / "index.html").read_text(encoding="utf-8")


def test_the_new_request_dialog_offers_publish_and_explains_that_nothing_is_pushed_yet():
    assert '<option value="ship">Publish (commit / PR)</option>' in HTML
    assert "Nothing is pushed until you approve here." in HTML
    assert 'ship:["build","review"]' in HTML and 'ship:{build:tt("Prepare commit"),review:tt("Publish")}' in HTML


def test_publishing_needs_its_own_button_and_the_last_phase_has_no_review_panel():
    seg = HTML[HTML.index("function actions(f)"):HTML.index("function threadHtml")]
    assert 'tt("Approve and publish")' in seg
    assert seg.index('f.kind==="ship"') < seg.index("return reviewPanel(f,id,go)")


def test_suggestions_open_the_prefilled_dialog_and_are_dismissed_only_after_it_is_confirmed():
    seg = HTML[HTML.index("function propCard"):HTML.index("// ---------- session to-dos")]
    assert "openNewRequest({project:x.project,kind:x.kind,title:x.title,desc:x.desc,proposal:x.id})" in seg
    assert "/api/proposals/" not in seg.split("data-prop-go")[0]       # rendering a card calls nothing
    submit = HTML[HTML.index('$("newForm").addEventListener("submit"'):]
    assert submit.index('api("/api/features"') < submit.index("/dismiss")


def test_suggestion_texts_are_escaped():
    seg = HTML[HTML.index("function propCard"):HTML.index("function renderProposals")]
    assert "esc(x.title)" in seg and "md(x.desc)" in seg and "esc(x.by)" in seg
