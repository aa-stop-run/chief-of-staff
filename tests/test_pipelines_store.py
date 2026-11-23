"""scripts/pipelines_store.py: the files of the user's own pipelines (`<home>/pipelines/`), over the engine's validator."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pipelines  # noqa: E402
import pipelines_store as store  # noqa: E402


def builtin(pid="feature"):
    return json.loads((pipelines.builtin_dir() / f"{pid}.json").read_text(encoding="utf-8"))


def custom(pid="my-flow", base="maintenance"):
    d = builtin(base)
    d["id"] = pid
    d["name"] = "My flow"
    return d


def codes(exc):
    return exc.value.code


def test_list_has_the_builtins_first_marked_and_user_ones_first_in_the_list(tmp_path):
    store.create(tmp_path, custom())
    rows = store.list_entries(tmp_path)
    assert rows[0] == {"id": "my-flow", "origin": "user", "valid": True, "reason": ""}
    assert [r["id"] for r in rows if r["origin"] == "builtin"] == sorted(pipelines.BUILTIN_IDS)
    assert all(r["valid"] for r in rows)


def test_a_missing_folder_lists_only_the_builtins(tmp_path):
    assert {r["origin"] for r in store.list_entries(tmp_path / "nope")} == {"builtin"}


def test_list_marks_an_invalid_file_with_its_reason(tmp_path):
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "pipelines" / "broken.json").write_text("{ not json", encoding="utf-8")
    bad = next(r for r in store.list_entries(tmp_path) if r["id"] == "broken")
    assert bad["valid"] is False and "not valid JSON" in bad["reason"] and bad["origin"] == "user"


def test_create_valid_writes_the_file(tmp_path):
    entry = store.create(tmp_path, custom())
    f = tmp_path / "pipelines" / "my-flow.json"
    assert f.is_file() and json.loads(f.read_text(encoding="utf-8"))["id"] == "my-flow"
    assert entry["valid"] is True and entry["origin"] == "user" and entry["digest"]


def test_create_invalid_is_rejected_and_writes_nothing(tmp_path):
    bad = custom()
    bad["steps"][0]["mode"] = "teleport"
    with pytest.raises(store.StoreError) as e:
        store.create(tmp_path, bad)
    assert codes(e) == "invalid" and "mode" in str(e.value)
    assert not (tmp_path / "pipelines" / "my-flow.json").exists()


@pytest.mark.parametrize("pid,why", [("feature", "built-in"), ("ab", "3"), ("Bad_Id", "id"), ("x" * 41, "id"), ("", "id")])
def test_create_id_rules(tmp_path, pid, why):
    d = custom()
    d["id"] = pid
    with pytest.raises(store.StoreError) as e:
        store.create(tmp_path, d)
    assert codes(e) in ("invalid", "builtin")


def test_create_refuses_an_id_that_is_taken(tmp_path):
    store.create(tmp_path, custom())
    with pytest.raises(store.StoreError) as e:
        store.create(tmp_path, custom())
    assert codes(e) == "exists"


def test_fork_a_builtin_records_where_it_came_from(tmp_path):
    d = builtin("fix")
    d["id"] = "my-fix"
    entry = store.create(tmp_path, d, forked_from="fix")
    assert json.loads((tmp_path / "pipelines" / "my-fix.json").read_text(encoding="utf-8"))["based_on"] == "fix" and entry["valid"]


def test_get_a_builtin_and_a_user_pipeline(tmp_path):
    store.create(tmp_path, custom())
    b, u = store.get(tmp_path, "feature"), store.get(tmp_path, "my-flow")
    assert b["origin"] == "builtin" and b["data"]["id"] == "feature" and b["valid"] and len(b["digest"]) >= 8
    assert u["origin"] == "user" and u["data"]["name"] == "My flow"
    with pytest.raises(store.StoreError) as e:
        store.get(tmp_path, "ghost")
    assert codes(e) == "notfound"


def test_get_an_invalid_user_file_still_returns_its_data_for_the_editor(tmp_path):
    (tmp_path / "pipelines").mkdir()
    d = custom("half")
    d["steps"][0]["mode"] = "teleport"
    (tmp_path / "pipelines" / "half.json").write_text(json.dumps(d), encoding="utf-8")
    got = store.get(tmp_path, "half")
    assert got["valid"] is False and "mode" in got["reason"] and got["data"]["id"] == "half"


def test_update_a_user_pipeline_ok_and_a_builtin_refused(tmp_path):
    store.create(tmp_path, custom())
    d = custom()
    d["name"] = "Renamed"
    assert store.update(tmp_path, "my-flow", d)["valid"] is True
    assert store.get(tmp_path, "my-flow")["data"]["name"] == "Renamed"
    with pytest.raises(store.StoreError) as e:
        store.update(tmp_path, "feature", builtin("feature"))
    assert codes(e) == "builtin"
    with pytest.raises(store.StoreError) as e:
        store.update(tmp_path, "my-flow", custom("other-id"))        # the id is the file name: it cannot change on an update
    assert codes(e) == "invalid"
    with pytest.raises(store.StoreError) as e:
        store.update(tmp_path, "ghost", custom("ghost"))
    assert codes(e) == "notfound"


def test_update_with_an_invalid_pipeline_keeps_the_old_file(tmp_path):
    store.create(tmp_path, custom())
    bad = custom()
    bad["steps"] = []
    with pytest.raises(store.StoreError) as e:
        store.update(tmp_path, "my-flow", bad)
    assert codes(e) == "invalid" and store.get(tmp_path, "my-flow")["data"]["steps"]


def test_delete_a_user_pipeline_and_refuse_a_builtin(tmp_path):
    store.create(tmp_path, custom())
    store.delete(tmp_path, "my-flow", [])
    assert not (tmp_path / "pipelines" / "my-flow.json").exists()
    with pytest.raises(store.StoreError) as e:
        store.delete(tmp_path, "feature", [])
    assert codes(e) == "builtin"
    with pytest.raises(store.StoreError) as e:
        store.delete(tmp_path, "my-flow", [])
    assert codes(e) == "notfound"


def test_delete_is_refused_while_any_request_uses_it_and_says_how_many(tmp_path):
    store.create(tmp_path, custom())
    used = [{"id": "r1", "pipeline": {"id": "my-flow"}}, {"id": "r2", "pipeline": {"id": "my-flow"}, "status": "done"}, {"id": "r3", "pipeline": {"id": "fix"}}]
    with pytest.raises(store.StoreError) as e:
        store.delete(tmp_path, "my-flow", used)
    assert codes(e) == "inuse" and e.value.count == 2 and "2" in str(e.value)
    assert (tmp_path / "pipelines" / "my-flow.json").exists()


def test_usage_counts_requests_by_pipeline(tmp_path):
    reqs = [{"pipeline": {"id": "a"}}, {"pipeline": {"id": "a"}}, {"pipeline": {"id": "b"}}, {}]
    assert store.usage(reqs) == {"a": 2, "b": 1}


def test_a_symlink_in_the_folder_is_not_followed(tmp_path):
    (tmp_path / "pipelines").mkdir()
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(custom("linked")), encoding="utf-8")
    link = tmp_path / "pipelines" / "linked.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks not allowed here")
    assert next(r for r in store.list_entries(tmp_path) if r["id"] == "linked")["valid"] is False
