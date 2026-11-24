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
