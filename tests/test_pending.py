import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from pending import Pending, PendingError  # noqa: E402

ITEM = dict(project="Smart Home", title="IP reservations on the router", status="blocked",
            summary="The router password is missing.", missing="Log in and save the reservations.",
            session="a8b92eaf-0000", date="2026-10-12")


def test_add_and_list(tmp_path):
    p = Pending(tmp_path)
    c = p.add(**ITEM)
    assert c["id"] and c["status"] == "blocked"
    assert [x["id"] for x in p.list()] == [c["id"]]


def test_persisted_between_instances(tmp_path):
    Pending(tmp_path).add(**ITEM)
    assert len(Pending(tmp_path).list()) == 1


def test_dismiss_hides_from_list(tmp_path):
    p = Pending(tmp_path)
    c = p.add(**ITEM)
    p.dismiss(c["id"])
    assert p.list() == []


def test_add_same_session_and_title_is_idempotent(tmp_path):
    p = Pending(tmp_path)
    a, b = p.add(**ITEM), p.add(**ITEM)
    assert a["id"] == b["id"] and len(p.list()) == 1


@pytest.mark.parametrize("field,value", [("title", ""), ("title", "x" * 141), ("status", "done"),
                                          ("project", ""), ("summary", "x" * 1201)])
def test_rejects_invalid(tmp_path, field, value):
    with pytest.raises(PendingError):
        Pending(tmp_path).add(**{**ITEM, field: value})


@pytest.mark.parametrize("bad", ["../x", "", "a/b", "does-not-exist"])
def test_dismiss_rejects_bad_or_unknown_id(tmp_path, bad):
    with pytest.raises(PendingError):
        Pending(tmp_path).dismiss(bad)


def test_get_returns_item_or_raises(tmp_path):
    p = Pending(tmp_path)
    c = p.add(**ITEM)
    assert p.get(c["id"])["title"] == ITEM["title"]
    with pytest.raises(PendingError):
        p.get("does-not-exist")
    p.dismiss(c["id"])
    with pytest.raises(PendingError):
        p.get(c["id"])

