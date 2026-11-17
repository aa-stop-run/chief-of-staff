"""The project page About block exposes category and remote save buttons."""
import pathlib


def test_about_block_exposes_category_and_remote_save_buttons():
    html = (pathlib.Path(__file__).resolve().parent.parent / "app" / "index.html").read_text(encoding="utf-8")
    assert 'id="abDom"' in html            # category input
    assert 'id="abRemote"' in html          # remote input
    assert 'data-pjact="dom"' in html       # category save
    assert 'data-pjact="remote"' in html    # remote save
