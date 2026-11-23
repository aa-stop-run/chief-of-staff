"""V1: a split plan, end to end — approve, two parts through their pipeline, the integration request and its report."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fanout  # noqa: E402
import prompts_integration  # noqa: E402
from features import Features  # noqa: E402
from test_auto_review import REVIEW_OK, Fake  # noqa: E402
from test_integration_prompt import EN_REPORT  # noqa: E402

SPEC = "# Spec\n\n## Acceptance criteria\n- both exports work\n"
PLAN = ("# Plan\nTASKS: 2\n\n## Split\n\n```json\n" + json.dumps({"tasks": [
    {"id": "api", "title": "Export endpoint", "files": [{"new": "api.txt"}]},
    {"id": "ui", "title": "Export button", "files": [{"new": "ui.txt"}]}]}) + "\n```\n")


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def world(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / "base.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    committed = []

    def committer(work, message):
        """What chief.commit_worktree does for a part, without a real worktree: the part's branch gets one commit."""
        pid = Path(work).parent.name
        part = next(x for x in fs._load() if x["id"] == pid)
        git(repo, "switch", "-c", f"feat/{pid}", "main")
        (repo / f"{part['part']}.txt").write_text(f"work of {part['part']}\n")
        git(repo, "add", ".")
        git(repo, "commit", "-m", message)
        git(repo, "switch", "main")
        committed.append(pid)
        return True, "abc1234"

    fake = Fake()
    fs = Features(tmp_path / "state", fake.launch, fake.is_active, auto_review=True, committer=committer)
    return fs, fake, repo, tmp_path / "state", committed


def stored(fs, fid):
    return next(x for x in fs.list() if x["id"] == fid)


def finish_stage(fs, fake, root, fid, artifact, text):
    """The request's own worker writes `artifact` and stops (several workers run at once here, so not «the last one launched»)."""
    fake.active.discard(stored(fs, fid)["worker_id"])
    d = root / "features" / fid
    d.mkdir(parents=True, exist_ok=True)
    (d / artifact).write_text(text, encoding="utf-8")
    return stored(fs, fid)


def drive_part_to_commit(fs, fake, root, pid):
    finish_stage(fs, fake, root, pid, "plan.md", "# Plan\nTASKS: 2\nsteps\n")
    finish_stage(fs, fake, root, pid, "plan-review.md", REVIEW_OK)
    fs.approve(pid)
    (root / "features" / pid / "work").mkdir(parents=True)
    finish_stage(fs, fake, root, pid, "report.md", "# Report\n")
    return finish_stage(fs, fake, root, pid, "review.md", REVIEW_OK)


def test_a_split_plan_goes_from_approval_to_an_integration_report(world):
    fs, fake, repo, root, committed = world
    main_before = git(repo, "rev-parse", "main")
    f = fs.create("Alfa", "Export trips", "Let me export trips", None, path=str(repo))
    finish_stage(fs, fake, root, f["id"], "spec.md", SPEC)
    fs.approve(f["id"])
    (root / "features" / f["id"] / "questions.json").write_text("[]", encoding="utf-8")
    finish_stage(fs, fake, root, f["id"], "plan.md", PLAN)
    assert finish_stage(fs, fake, root, f["id"], "plan-review.md", REVIEW_OK)["status"] == "awaiting"

    fs.approve(f["id"])                                                    # the user's click fans it out
    parent = stored(fs, f["id"])
    part_ids = [t["part_id"] for t in parent["split"]["tasks"]]
    assert parent["status"] == "done" and len(part_ids) == 2 and fanout.split_state(parent, fs.list()) == "running"

    fs.maybe_integrate()                                                   # nothing is ready yet
    assert not [x for x in fs.list() if x.get("integration")]

    for pid in part_ids:
        assert drive_part_to_commit(fs, fake, root, pid)["commit"]["ok"] is True
        if pid != part_ids[-1]:
            fs.maybe_integrate()                                           # still one part short
            assert not [x for x in fs.list() if x.get("integration")]
    assert committed == part_ids and fanout.split_state(stored(fs, f["id"]), fs.list()) == "ready"

    heads = {p: git(repo, "rev-parse", f"feat/{p}") for p in part_ids}
    fs.maybe_integrate()
    fs.maybe_integrate()                                                   # the second tick does nothing
    (integ,) = [x for x in fs.list() if x.get("integration")]
    d = root / "features" / integ["id"]
    info = json.loads((d / "combine.json").read_text(encoding="utf-8"))
    assert info["merged"] == [f"feat/{p}" for p in part_ids] and info["conflicts"] == {} and info["missing"] == []
    assert (d / "integration" / "api.txt").exists() and (d / "integration" / "ui.txt").exists()
    assert integ["kind"] == "analysis" and prompts_integration.REPORT_SECTIONS["en"][0] in fake.calls[-1]["task"]

    report = EN_REPORT.replace("export-trips-1-api-1", part_ids[0]).replace("export-trips-1-ui-1", part_ids[1])
    finish_stage(fs, fake, root, integ["id"], "report.md", report)
    import server
    card = stored(fs, integ["id"])
    out = server._card_extras({**card, "artifacts": fs.artifacts(integ["id"])}, {}, fs.list())
    assert out["integration_summary"]["parts"] == {part_ids[0]: "APPROVED", part_ids[1]: "WITH RESERVATIONS"}
    assert out["integration_summary"]["tests"] == "10 passed, 0 failed"
    parent_card = server._card_extras({**stored(fs, f["id"]), "artifacts": {}}, {}, fs.list())
    assert parent_card["split_state"] == "integrating" and len(parent_card["parts"]) == 2

    # nothing was committed outside the parts' branches; the project's own checkout and the branches are untouched
    assert git(repo, "rev-parse", "main") == main_before and git(repo, "status", "--porcelain") == ""
    assert {p: git(repo, "rev-parse", f"feat/{p}") for p in part_ids} == heads
