import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from sessions import encode_path, scrub, summarize  # noqa: E402


def write(tmp_path, rows):
    f = tmp_path / "abcdef12-0000-0000-0000-000000000000.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return f


def user(t, **kw):
    return {"type": "user", "message": {"content": t}, **kw}


def asst(*blocks):
    return {"type": "assistant", "message": {"content": list(blocks)}}


def text(t):
    return {"type": "text", "text": t}


def test_summarize_picks_first_last_and_ignores_noise(tmp_path):
    f = write(tmp_path, [
        user("first request"),
        user("<system-reminder>noise</system-reminder>"),
        user("meta", isMeta=True),
        asst(text("answer 1")),
        user("last request"),
        asst(text("final answer")),
    ])
    s = summarize(f)
    assert s["first"] == "first request" and s["last_user"] == "last request"
    assert s["last_assistant"] == "final answer"
    assert s["id"] == "abcdef12-0000-0000-0000-000000000000"


def test_summarize_reports_unfinished_todos(tmp_path):
    todos = [{"content": "a", "status": "completed"}, {"content": "b", "status": "in_progress"},
             {"content": "c", "status": "pending"}]
    f = write(tmp_path, [user("x"), asst({"type": "tool_use", "name": "TodoWrite", "input": {"todos": todos}})])
    assert [t["content"] for t in summarize(f)["open_todos"]] == ["b", "c"]


def test_summarize_tolerates_bad_lines_and_empty(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text("not json\n\n" + json.dumps(user("ok")), encoding="utf-8")
    assert summarize(f)["first"] == "ok"
    e = tmp_path / "e.jsonl"
    e.write_text("", encoding="utf-8")
    assert summarize(e)["last_user"] == ""


def test_scrub_removes_secrets_and_private_addresses():
    t = scrub("key=sk-ABCDEFGHIJKLMNOP1234 at http://192.168.1.197:8096 and 100.84.205.24 mail a@b.pt password: hunter2")
    for leak in ("sk-ABC", "192.168.1.197", "100.84.205.24", "a@b.pt", "hunter2"):
        assert leak not in t


def test_scrub_truncates():
    assert len(scrub("x" * 1000, 50)) <= 51


def test_encode_path_matches_claude_folder_names():
    assert encode_path(r"D:\Projects_AI\Home server") == "d--projects-ai-home-server"
