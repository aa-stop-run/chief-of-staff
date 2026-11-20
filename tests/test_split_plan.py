"""The split & fan-out structural helpers (scripts/splitplan.py), tested first."""
import copy
import json
import re
from pathlib import Path

import pytest

sys_path = str(Path(__file__).resolve().parent.parent / "scripts")
import sys  # noqa: E402
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)
import splitplan  # noqa: E402


def _fenced(body: str) -> str:
    return f"```json\n{body}\n```"


# --------------------------------------------------------------------------- S1 — parse_split

def test_parse_split_reads_a_fenced_json_block():
    plan = "Some prose.\n\n" + _fenced(json.dumps({
        "parts": [
            {"id": "alpha", "title": "Alpha part", "files": ["a.py"], "depends_on": []},
            {"id": "beta", "title": "Beta"},
        ]
    })) + "\n\nMore prose."
    got = splitplan.parse_split(plan)
    assert got is not None
    parts = got["parts"]
    assert len(parts) == 2
    assert parts[0] == {"id": "alpha", "title": "Alpha part",
                        "files": [{"path": "a.py", "new": False}], "depends_on": []}
    # missing keys become empty values, not errors
    assert parts[1] == {"id": "beta", "title": "Beta", "files": [], "depends_on": []}


def test_parse_split_accepts_pt_field_names():
    plan = _fenced(json.dumps({
        "partes": [
            {"id": "um", "titulo": "Um", "ficheiros": ["x.py"], "depende_de": []},
        ]
    }))
    got = splitplan.parse_split(plan)
    assert got is not None
    p = got["parts"][0]
    assert p["id"] == "um" and p["title"] == "Um"
    assert p["files"] == [{"path": "x.py", "new": False}]
    assert p["depends_on"] == []


def test_parse_split_accepts_mixed_field_names_and_new_flag():
    plan = _fenced(json.dumps({
        "parts": [
            {"id": "a", "title": "A", "files": [{"path": "n.py", "new": True}]},
        ]
    }))
    got = splitplan.parse_split(plan)
    assert got["parts"][0]["files"] == [{"path": "n.py", "new": True}]


def test_parse_split_returns_none_when_there_is_no_block():
    assert splitplan.parse_split("no fence here") is None


def test_parse_split_never_raises_on_bad_json_or_shape():
    assert splitplan.parse_split(_fenced("{ not json")) is None
    assert splitplan.parse_split(_fenced(json.dumps({"parts": "not a list"}))) is None
    assert splitplan.parse_split(_fenced(json.dumps({"parts": [{"id": 1}]}))) is not None


def test_parse_split_normalises_missing_keys_to_empty():
    plan = _fenced(json.dumps({"parts": [{}]}))
    got = splitplan.parse_split(plan)
    assert got["parts"][0] == {"id": "", "title": "", "files": [], "depends_on": []}
