"""Typing in one card must never freeze the others: the redraw of the open cards works card by card (checked on the source; the behaviour
was also exercised in a real browser: cursor in a card's «Ask the worker» box while another card, and then the same card, changed)."""
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "app" / "index.html").read_text(encoding="utf-8")


def seg(a, b):
    return HTML[HTML.index(a):HTML.index(b)]


def test_every_card_is_addressable_and_the_redraw_no_longer_stops_at_the_first_text_box():
    assert 'class="feat s-${esc(f.status)}${shut?" collapsed":""}" data-fid="${esc(f.id)}"' in HTML
    r = seg("function renderFeatures", "let doneOpen")
    assert "syncAround($(\"feats\"),cards,typing)" in r
    assert 'if(!typing){if(html!==featHtml)' in r                       # nothing focused: the full redraw, as before


def test_the_card_being_typed_in_keeps_its_box_but_the_others_are_replaced():
    s = seg("function syncAround", "let cardHtml")
    assert "old.replaceWith(tpl.content.firstElementChild)" in s and "cardHtml[f.id]===h" in s
    assert 'if(!typing.matches("[data-ask]"))return;' in s              # an answer or a note being typed is never replaced
    assert "slot.replaceWith(live)" in s and "typing.focus()" in s and "setSelectionRange" in s
    assert "wait for the next redraw" in s and "join(\"|\")" in s       # a changed set of cards waits for the next full redraw
