"""Open requests can be made smaller: the spec, plan and report remember being closed, and a card (or all of them) can be collapsed."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "app" / "index.html"
SRC = HTML.read_text(encoding="utf-8")


def test_the_users_open_or_close_of_an_artifact_is_remembered_across_redraws():
    # the redraw rebuilds the card from `u.open[k]`; without a writer the default («open while it awaits you») came back at every poll
    assert re.search(r'closest\("details\.art > summary"\)', SRC)
    assert "fui(d.dataset.id).open[d.dataset.k]=!d.open" in SRC


def test_a_card_can_be_collapsed_and_the_state_survives_a_redraw():
    assert 'data-act="cardtoggle"' in SRC
    assert 'class="feat s-${esc(f.status)}${shut?" collapsed":""}"' in SRC
    assert 'if(act==="cardtoggle"){const u0=fui(id);u0.collapsed=!u0.collapsed;renderFeatures(lastFs,lastFiltering);return;}' in SRC
    assert ".feat.collapsed>*:not(.ftop){display:none}" in SRC


def test_collapse_all_is_offered_when_there_are_two_or_more_open_cards():
    assert 'id="collAll"' in SRC
    assert "b.hidden=n<2" in SRC


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_script_still_parses():
    scripts = re.findall(r"<script>([\s\S]*?)</script>", SRC)
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "page.js"
        f.write_text("\n".join(scripts), encoding="utf-8")
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
