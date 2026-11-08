"""Shared test setup.

The user's data folder is pinned to a throw-away directory *before* `chief` is imported, so no test can read or write the real
data, and the vault conventions are pinned to English so the tests never depend on the local configuration.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

TEST_HOME = Path(tempfile.mkdtemp(prefix="chief-test-home-"))
os.environ["CHIEF_HOME"] = str(TEST_HOME)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chief  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_chief(monkeypatch, tmp_path):
    """Every test gets its own empty data folder and English vault conventions."""
    monkeypatch.setattr(chief, "HOME", tmp_path / "chief-home")
    monkeypatch.setattr(chief, "STATE", tmp_path / "chief-home" / "state")
    monkeypatch.setattr(chief, "VAULT_LANG", "en")
    monkeypatch.setattr(chief, "SCHEMA", chief.VAULT_SCHEMA["en"])
    monkeypatch.setattr(chief, "DRAFT_TEXT", chief.DRAFT_TEXTS["en"])


def test_the_suite_never_points_at_the_real_data_folder():
    assert Path(os.environ["CHIEF_HOME"]).resolve() == TEST_HOME.resolve()
    assert str(TEST_HOME.resolve()).startswith(str(Path(tempfile.gettempdir()).resolve()))
