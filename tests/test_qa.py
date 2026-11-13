"""Questions to the worker: a read-only follow-up worker answers about a request's conclusions, in any kind and phase,
without changing the request's stage or status."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FeatureError, Features  # noqa: E402
from test_auto_review import Fake, finish_stage  # noqa: E402


@pytest.fixture
def env(tmp_path):
    fake = Fake()
    tags = []
    fs = Features(tmp_path, fake.launch, fake.is_active, auto_review=True,
                  on_launch=lambda wid, request=None, phase=None: tags.append((wid, request, phase)))
    return fs, fake, tmp_path, tags


def answer(fake, root, fid, n, text="The answer.", stop=True):
    d = root / "features" / fid / "qa"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{n}.md").write_text(text, encoding="utf-8")
    if stop:
        fake.active.discard(f"w{len(fake.calls)}")


def analysis_ready(fs, fake, root):
    f = fs.create("Alfa", "Why is it slow?", "Investigate", None, kind="analysis")
    finish_stage(fs, fake, root, f["id"], "report.md", "# Analysis\nThe cache is cold.")
    finish_stage(fs, fake, root, f["id"], "review.md", "## Verdict\nAPPROVED — ok.\n\n## Findings\nNone.\n")
    return f["id"]


def test_asking_launches_a_read_only_worker_that_knows_the_request_and_the_question(env):
    fs, fake, root, tags = env
    fid = analysis_ready(fs, fake, root)
    before = len(fake.calls)
    q = fs.ask(fid, "Why did you conclude the cache is cold?")
    assert len(fake.calls) == before + 1
    task = fake.calls[-1]["task"]
    assert "Why did you conclude the cache is cold?" in task
    assert "report.md" in task and "review.md" in task and "READ-ONLY" in task
    assert str(root / "features" / fid / "qa" / "1.md") in task
    assert q["n"] == 1 and q["status"] == "working"
    assert (fake.calls[-1]["id"], fid, "qa") in tags                      # the cost is attributed to the request


def test_asking_never_changes_the_requests_stage_or_status(env):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    f0 = next(x for x in fs.list() if x["id"] == fid)
    fs.ask(fid, "What is the evidence for P1?")
    f1 = next(x for x in fs.list() if x["id"] == fid)
    assert (f1["stage"], f1["status"], f1["worker_id"]) == (f0["stage"], f0["status"], f0["worker_id"])
    assert not any(h["event"] == "worker launched" and h["stage"] == "qa" for h in f1["history"])


def test_the_answer_is_collected_when_the_worker_stops(env):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    fs.ask(fid, "Why?")
    assert fs.qa(fid)[0]["status"] == "working" and fs.qa(fid)[0]["answer"] == ""
    answer(fake, root, fid, 1, "Because of X (file.py:10).")
    fs.list()
    got = fs.qa(fid)
    assert got[0]["status"] == "answered" and got[0]["answer"] == "Because of X (file.py:10)."


def test_a_worker_that_dies_without_answering_is_marked_failed(env):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    fs.ask(fid, "Why?")
    fake.active.discard(f"w{len(fake.calls)}")
    fs.list()
    assert fs.qa(fid)[0]["status"] == "failed"


def test_one_question_at_a_time_then_the_next_gets_the_next_number_and_sees_the_history(env):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    fs.ask(fid, "First?")
    with pytest.raises(FeatureError, match="still being answered"):
        fs.ask(fid, "Second?")
    answer(fake, root, fid, 1, "First answer.")
    fs.list()
    q2 = fs.ask(fid, "Second?")
    assert q2["n"] == 2
    task = fake.calls[-1]["task"]
    assert "First?" in task and "First answer." in task and "2.md" in task    # the earlier exchange is context


def test_a_failed_question_can_be_asked_again(env):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    fs.ask(fid, "Why?")
    fake.active.discard(f"w{len(fake.calls)}")
    fs.list()
    assert fs.ask(fid, "Why?")["n"] == 2


@pytest.mark.parametrize("bad", ["", "   ", "x" * 2001])
def test_empty_or_oversized_questions_are_refused(env, bad):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    with pytest.raises(FeatureError):
        fs.ask(fid, bad)


def test_unknown_or_invalid_ids_are_refused(env):
    fs, *_ = env
    with pytest.raises(FeatureError):
        fs.ask("nope", "Why?")
    with pytest.raises(FeatureError):
        fs.ask("../x", "Why?")


@pytest.mark.parametrize("kind", ["feature", "fix", "maintenance", "analysis"])
def test_every_kind_can_be_asked_right_after_it_is_created(env, kind):
    fs, fake, root, _ = env
    f = fs.create("Alfa", "Something", "Do it", None, kind=kind)
    assert fs.ask(f["id"], "What are you going to do?")["status"] == "working"


def test_questions_work_on_cancelled_and_done_requests(env):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    fs.approve(fid)
    assert next(x for x in fs.list() if x["id"] == fid)["status"] == "done"
    assert fs.ask(fid, "Still there?")["n"] == 1


def test_a_failed_launch_leaves_no_trace(env):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    fs._launch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no claude"))
    with pytest.raises(FeatureError, match="could not launch"):
        fs.ask(fid, "Why?")
    assert fs.qa(fid) == []


def test_portuguese_requests_get_a_portuguese_prompt(env):
    fs, fake, root, _ = env
    f = fs.create("Alfa", "Porque é lento", "x", None, lang="pt", kind="analysis")
    fs.ask(f["id"], "Porquê?")
    assert "SÓ LEITURA" in fake.calls[-1]["task"]


def test_the_answer_is_capped(env):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    fs.ask(fid, "Why?")
    answer(fake, root, fid, 1, "a" * 100_000)
    fs.list()
    assert len(fs.qa(fid)[0]["answer"]) < 41_000


def test_a_worker_that_waits_for_input_without_answering_fails_the_question_and_is_released(tmp_path):
    fake, released = Fake(), []
    fs = Features(tmp_path, fake.launch, fake.is_active, is_busy=lambda wid: False, on_release=released.append)
    f = fs.create("Alfa", "Why is it slow?", "x", None, kind="analysis")
    q = fs.ask(f["id"], "Why?")                       # the session stays 'active' (idle/waiting) but is not working and wrote nothing
    fs.list()
    assert fs.qa(f["id"])[0]["status"] == "failed" and q["worker_id"] in released
    assert fs.ask(f["id"], "Again?")["n"] == 2        # no longer stuck


def test_cancelling_a_request_stops_a_question_in_flight(tmp_path):
    fake, released = Fake(), []
    fs = Features(tmp_path, fake.launch, fake.is_active, on_release=released.append)
    f = fs.create("Alfa", "Why is it slow?", "x", None, kind="analysis")
    q = fs.ask(f["id"], "Why?")
    fs.cancel(f["id"])
    assert q["worker_id"] in released and fs.qa(f["id"])[0]["status"] == "failed"


def test_the_prompt_forbids_other_files_and_the_vault_note(env):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    fs.ask(fid, "Why?")
    t = " ".join(fake.calls[-1]["task"].split())
    assert "never write to any other file" in t and "ignore rules 4 and 6" in t
    f = fs.create("Alfa", "Porquê", "x", None, lang="pt", kind="analysis")
    fs.ask(f["id"], "Porquê?")
    t = " ".join(fake.calls[-1]["task"].split())
    assert "não escrevas em mais nenhum ficheiro" in t and "ignora as regras 4 e 6" in t


def test_qa_of_reads_a_request_already_in_hand(env):
    fs, fake, root, _ = env
    fid = analysis_ready(fs, fake, root)
    fs.ask(fid, "Why?")
    answer(fake, root, fid, 1, "Because.")
    f = next(x for x in fs.list() if x["id"] == fid)
    assert fs.qa_of(f)[0]["answer"] == "Because."
