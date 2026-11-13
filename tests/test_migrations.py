"""Schema migrations: data written by older versions is brought to the current schema, explicitly and idempotently."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import migrations  # noqa: E402

LEGACY_PENDING = [{"id": "p-1", "project": "Alfa", "title": "Old card", "estado": "awaiting", "resumo": "Summary", "falta": "Missing",
                   "sessao": "abc", "data": "2026-10-01", "criado": "2026-10-01T10:00:00", "dismissed": False}]
LEGACY_FEATURES = [{"id": "f1", "title": "x", "history": [
    {"at": "t", "event": "aprovado", "stage": "spec", "note": ""},
    {"at": "t", "event": "revisão automática", "stage": "review", "note": ""},
    {"at": "t", "event": "an event nobody knows", "stage": "review", "note": ""}]}]


def write(state: Path, name: str, data) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / name).write_text(json.dumps(data), encoding="utf-8")


def read(state: Path, name: str):
    return json.loads((state / name).read_text(encoding="utf-8"))


def test_the_steps_are_ordered_and_current_is_the_last(tmp_path):
    versions = [s.version for s in migrations.STEPS]
    assert versions == sorted(versions) == list(range(1, len(versions) + 1)) and migrations.CURRENT == versions[-1]
    assert migrations.pending(0) == list(migrations.STEPS) and migrations.pending(migrations.CURRENT) == []


def test_pending_items_get_english_keys_and_the_old_file_goes_away(tmp_path):
    write(tmp_path, "pendentes.json", LEGACY_PENDING)
    migrations.to_schema_1(tmp_path)
    item = read(tmp_path, "pending.json")[0]
    assert (item["status"], item["summary"], item["missing"], item["session"], item["date"], item["created"]) == (
        "awaiting", "Summary", "Missing", "abc", "2026-10-01", "2026-10-01T10:00:00")
    assert "estado" not in item and not (tmp_path / "pendentes.json").exists()


def test_an_existing_pending_json_is_never_overwritten(tmp_path):
    write(tmp_path, "pendentes.json", LEGACY_PENDING)
    write(tmp_path, "pending.json", [{"id": "new"}])
    migrations.to_schema_1(tmp_path)
    assert read(tmp_path, "pending.json") == [{"id": "new"}]


def test_features_get_english_events_and_a_portuguese_lang_when_they_had_none(tmp_path):
    write(tmp_path, "features.json", LEGACY_FEATURES)
    migrations.to_schema_1(tmp_path)
    f = read(tmp_path, "features.json")[0]
    assert f["lang"] == "pt"
    assert [h["event"] for h in f["history"]] == ["approved", "automatic review", "an event nobody knows"]


def test_a_feature_that_already_has_a_lang_keeps_it(tmp_path):
    write(tmp_path, "features.json", [{"id": "f", "lang": "en", "history": []}])
    migrations.to_schema_1(tmp_path)
    assert read(tmp_path, "features.json")[0]["lang"] == "en"


def test_running_a_step_twice_changes_nothing(tmp_path):
    write(tmp_path, "pendentes.json", LEGACY_PENDING)
    write(tmp_path, "features.json", LEGACY_FEATURES)
    migrations.to_schema_1(tmp_path)
    once = (read(tmp_path, "pending.json"), read(tmp_path, "features.json"))
    migrations.to_schema_1(tmp_path)
    assert (read(tmp_path, "pending.json"), read(tmp_path, "features.json")) == once


def test_an_empty_or_missing_state_is_fine(tmp_path):
    migrations.to_schema_1(tmp_path)                      # nothing there
    (tmp_path / "features.json").write_text("not json", encoding="utf-8")
    migrations.to_schema_1(tmp_path)                      # corrupt file: left alone, no crash
    assert (tmp_path / "features.json").read_text(encoding="utf-8") == "not json"


# ---------------------------------------------------------------- schema 2: work sessions
def test_old_requests_and_workers_move_into_a_placeholder_session(tmp_path):
    write(tmp_path, "features.json", [
        {"id": "a", "status": "done", "created": "2026-11-01T09:00:00", "history": [{"at": "2026-11-02T10:00:00", "event": "approved", "stage": "review"}]},
        {"id": "b", "status": "cancelled", "created": "2026-11-03T09:00:00", "history": []},
        {"id": "c", "status": "awaiting", "created": "2026-11-04T09:00:00", "history": []}])
    write(tmp_path, "workers.json", [{"id": "w1", "started": "2026-10-30T08:00:00"}, {"id": "w2", "started": "2026-11-05T08:00:00", "session": "keep"}])
    migrations.to_schema_2(tmp_path)
    feats = {f["id"]: f for f in read(tmp_path, "features.json")}
    assert feats["a"]["closed"] == {"at": "2026-11-02T10:00:00", "session": "ws-legacy"}
    assert feats["b"]["closed"] == {"at": "2026-11-03T09:00:00", "session": "ws-legacy"}
    assert "closed" not in feats["c"]                                         # still open: it carries over into the next session
    assert [w["session"] for w in read(tmp_path, "workers.json")] == ["ws-legacy", "keep"]
    sessions = read(tmp_path, "work_sessions.json")["sessions"]
    assert [s["id"] for s in sessions] == ["ws-legacy"] and sessions[0]["started"] == "2026-10-30T08:00:00" and sessions[0]["ended"]


def test_the_placeholder_session_is_never_the_current_one(tmp_path):
    import worksession
    write(tmp_path, "features.json", [])
    migrations.to_schema_2(tmp_path)
    assert worksession.WorkSessions(tmp_path / "work_sessions.json").current() is None


def test_schema_2_is_idempotent_and_copes_with_an_empty_state(tmp_path):
    migrations.to_schema_2(tmp_path)
    migrations.to_schema_2(tmp_path)
    assert [s["id"] for s in read(tmp_path, "work_sessions.json")["sessions"]] == ["ws-legacy"]
    write(tmp_path, "features.json", [{"id": "a", "status": "done", "created": "2026-11-01T09:00:00", "history": []}])
    migrations.to_schema_2(tmp_path)
    first = read(tmp_path, "features.json")
    migrations.to_schema_2(tmp_path)
    assert read(tmp_path, "features.json") == first
