"""The «Vault health» card (checked on the source) and its routes (the demo has no vault: it refuses them and shows no card)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from test_demo_server import DemoServer  # noqa: E402

HTML = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
SEG = HTML[HTML.index("function vhCard"):HTML.index("function propCard")]


def test_the_card_is_hidden_unless_there_is_something_to_say():
    assert 'id="vhWrap" hidden' in HTML and "renderVaultHealth(S.vault_health)" in HTML
    assert "w.hidden=!show" in SEG and "h.enabled!==false" in SEG


def test_a_finding_shows_evidence_and_escapes_everything_it_prints():
    for field in ("f.id", "f.note", "f.quote", "f.old", "f.new", "f.detail", "f.severity"):
        assert f"esc({field})" in SEG
    assert "esc(e)" in SEG                                           # each evidence line


def test_apply_is_offered_only_with_a_replacement_and_needs_a_second_click():
    assert 'f.replace?`<button class="btn sm pri" type="button" data-vh="apply"' in SEG
    assert 'b.dataset.vh==="apply"&&!armed(' in SEG
    assert "/api/vault/findings/" in SEG and "/api/vault/check" in SEG


def test_the_texts_are_translated():
    for key in ("Vault health", "Check the vault now", "Apply", "The vault has not been checked yet", "Note updated."):
        assert f'"{key}":"' in HTML


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    s = DemoServer(tmp_path_factory.mktemp("vault-health"))
    yield s
    s.stop()


def test_the_demo_has_no_vault_health_and_refuses_its_routes(srv):
    assert srv.get("/api/state")[1]["vault_health"] is None
    for path in ("/api/vault/check", "/api/vault/findings/x/apply"):
        status, out = srv.post(path, {})
        assert status == 400 and "error" in out


def test_a_finding_offers_apply_open_and_one_ignore_and_hides_minor_structure_ones_by_default():
    assert 'data-vh="accept"' not in SEG and 'data-vh="dismiss"' in SEG and "obsidian://open?vault=" in SEG
    assert 'f.layer!=="audit"&&f.severity==="low"' in SEG and "VH.minor" in SEG


def test_the_header_has_a_curate_now_button_that_shows_only_with_drafts_and_no_running_curation():
    assert 'id="curBtn" type="button" hidden' in HTML
    assert "cb.hidden=!(S.vault&&cu.drafts>0&&!cu.running)" in HTML and 'api("/api/inbox/curate",{})' in HTML
    assert '"Curate now":"' in HTML


def test_the_card_shows_the_map_line_and_an_update_button_and_the_demo_has_no_map(srv):
    assert 'id="vmBtn"' in HTML and 'api("/api/vault/map",{})' in HTML and "S.vault_map" in HTML
    assert '"Update the map":"' in HTML and '"Map: {h} hubs, {p} relations, {l} labelled":"' in HTML
    assert srv.get("/api/state")[1]["vault_map"] is None
    assert srv.post("/api/vault/map", {})[0] == 400


def test_a_finding_without_a_replacement_can_be_sent_to_the_curation():
    assert 'data-vh="fix"' in SEG and '!f.replace&&f.layer!=="health"&&!String(f.kind).startsWith("lint-")' in SEG
    assert '"Ask the curation to fix it":"' in HTML
