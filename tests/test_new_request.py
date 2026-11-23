"""Features._new_request: build a request inside a loaded list, without loading or saving it (W1 / R1)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FeatureError, Features  # noqa: E402
from test_features import Fake  # noqa: E402


@pytest.fixture
def env(tmp_path):
    fake = Fake()
    return Features(tmp_path, fake.launch, fake.is_active), fake, tmp_path


def saved(root):
    p = Path(root) / "features.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def test_new_request_appends_to_the_given_list_and_never_writes(env):
    fs_, fake, root = env
    fs = []
    f = fs_._new_request(fs, "Alfa", "Title", "Desc", None, lang="en", kind="feature", path="", parent="")
    assert fs == [f] and f["status"] == "working" and f["stage"] == "spec" and len(fake.calls) == 1
    assert saved(root) is None                       # the caller saves


def test_start_false_launches_nothing_and_waits(env):
    fs_, fake, root = env
    fs = []
    f = fs_._new_request(fs, "Alfa", "Title", "Desc", None, kind="feature", start=False)
    assert fake.calls == [] and f["status"] == "awaiting" and fs == [f]
    assert (root / "features" / f["id"]).is_dir() and f["pipeline"]["id"] == "feature"


def test_stage_plan_sets_the_stage_and_its_step(env):
    fs_, _, _ = env
    f = fs_._new_request([], "Alfa", "Title", "Desc", None, kind="feature", stage="plan", start=False)
    assert f["stage"] == "plan" and f["rev"]["plan"] == 0
    assert fs_._stage_step(f)["id"] == f["step"]


def test_extra_keys_are_merged_before_the_request_starts(env):
    fs_, fake, _ = env
    seen = {}
    real = fs_._start

    def spy(fs, f, feedback):
        seen.update(part=f.get("part"), parent=f.get("parent"))
        return real(fs, f, feedback)
    fs_._start = spy
    fs_._new_request([], "Alfa", "Title", "Desc", None, extra={"part": "api", "parent": "p-1"})
    assert seen == {"part": "api", "parent": "p-1"}


def test_a_given_fid_is_used_as_is(env):
    fs_, _, root = env
    f = fs_._new_request([], "Alfa", "Title", "Desc", None, fid="my-id-1", start=False)
    assert f["id"] == "my-id-1" and (root / "features" / "my-id-1").is_dir()


def test_autopilot_is_never_set_on_a_part(env):
    fs_, _, _ = env
    assert fs_._new_request([], "Alfa", "T", "D", None, autopilot=True, start=False)["autopilot"] is True
    assert "autopilot" not in fs_._new_request([], "Alfa", "T", "D", None, autopilot=True, extra={"part": "a"}, start=False)
    assert "autopilot" not in fs_._new_request([], "Alfa", "T", "D", None, kind="ship", autopilot=True, start=False)


def test_validation_helper_rejects_bad_fields_before_anything_is_written(env):
    fs_, fake, root = env
    for args in (("", "T", "D", None, "en", "feature"), ("A", "", "D", None, "en", "feature"), ("A", "T", "", None, "en", "feature"),
                 ("A", "T", "D", "bad model!", "en", "feature"), ("A", "T", "D", None, "xx", "feature"), ("A", "T", "D", None, "en", "nope"),
                 ("A", "x" * 121, "D", None, "en", "feature"), ("A", "T", "d" * 4001, None, "en", "feature")):
        with pytest.raises(FeatureError):
            fs_._validate_request_fields(*args)
    assert fs_._validate_request_fields("A", " T ", " D ", None, "", "") == ("T", "D", "feature")
    assert fake.calls == [] and saved(root) is None


def test_create_is_still_a_load_validate_new_request_save(env):
    fs_, fake, root = env
    f = fs_.create("Alfa", "Title", "Desc", None, autopilot=True, path="/p")
    assert saved(root) == [f] and f["autopilot"] is True and f["path"] == "/p" and len(fake.calls) == 1
    with pytest.raises(FeatureError):
        fs_.create("Alfa", "Title", "Desc", None, parent="nope-1")
    assert len(saved(root)) == 1
