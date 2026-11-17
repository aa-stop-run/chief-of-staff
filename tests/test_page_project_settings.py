"""The project page About block exposes category, remote, folder-move, and remove controls."""
import pathlib


def test_about_block_exposes_category_and_remote_save_buttons():
    html = (pathlib.Path(__file__).resolve().parent.parent / "app" / "index.html").read_text(encoding="utf-8")
    assert 'id="abDom"' in html            # category input
    assert 'id="abRemote"' in html          # remote input
    assert 'data-pjact="dom"' in html       # category save
    assert 'data-pjact="remote"' in html    # remote save


def test_about_block_exposes_move_and_remove():
    html = (pathlib.Path(__file__).resolve().parent.parent / "app" / "index.html").read_text(encoding="utf-8")
    assert 'id="abMove"' in html             # folder input
    assert 'data-pjact="move"' in html      # folder move
    assert 'data-pjact="remove"' in html    # remove from list


def test_settings_no_longer_lists_projects():
    """The projects table was removed from the Settings dialog."""
    html = (pathlib.Path(__file__).resolve().parent.parent / "app" / "index.html").read_text(encoding="utf-8")
    # The old Settings dialog used data-sact for project actions; those are gone.
    assert 'data-sact="extra_move"' not in html
    assert 'data-sact="project_remove"' not in html
