"""Approved specs and plans: a copy in the project's docs/specs and docs/plans and a draft in the vault inbox."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chief  # noqa: E402
from features import Features  # noqa: E402
from test_auto_review import Fake, finish_stage  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    root, vault, proj_root = tmp_path / "Chief", tmp_path / "Vault", tmp_path / "Projects"
    (vault / "inbox").mkdir(parents=True)
    (vault / "Projects").mkdir()
    root.mkdir()
    (proj_root / "Alfa").mkdir(parents=True)
    cfg = {"vault": str(vault), "roots": [str(proj_root)], "extras": [], "ignore": [], "hide_domains": [], "dashboard_port": 8780}
    (root / "projects.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(chief, "ROOT", root)
    monkeypatch.setattr(chief, "HOME", root)               # the data folder (configuration is written here)
    monkeypatch.setattr(chief, "VAULT", vault)
    monkeypatch.setattr(chief, "CFG", cfg)
    return vault, proj_root / "Alfa"


def test_spec_goes_to_docs_specs_and_to_the_vault_inbox(env):
    vault, proj = env
    info = chief.archive_doc("spec", "Alfa", "Login with 2FA", "login-261107", "2026-11-07T10:00:00", "# Spec\nText")
    f = proj / "docs" / "specs" / "2026-11-07-login-with-2fa.md"
    assert f.is_file() and "copied to docs/specs/2026-11-07-login-with-2fa.md" in info
    t = f.read_text(encoding="utf-8")
    assert t.startswith("> Spec approved in the Chief of Staff") and "# Spec\nText" in t and "\r" not in t
    drafts = list((vault / "inbox").glob("* Alfa - spec *.md"))
    assert len(drafts) == 1
    d = drafts[0].read_text(encoding="utf-8")
    assert d.startswith('---\nproject: "[[Alfa]]"') and "status: draft" in d and "docs/specs/2026-11-07-login-with-2fa.md" in d


def test_a_portuguese_vault_gets_a_draft_in_portuguese(env, monkeypatch):
    vault, _ = env
    monkeypatch.setattr(chief, "SCHEMA", chief.VAULT_SCHEMA["pt"])
    monkeypatch.setattr(chief, "DRAFT_TEXT", chief.DRAFT_TEXTS["pt"])
    chief.archive_doc("spec", "Alfa", "Login com 2FA", "login-261107", "2026-11-07T10:00:00", "# Spec", lang="pt")
    d = next((vault / "inbox").glob("* Alfa - spec *.md")).read_text(encoding="utf-8")
    assert d.startswith('---\nprojeto: "[[Alfa]]"') and "estado: rascunho" in d and "Spec aprovada" in d


def test_plan_goes_to_docs_plans_with_the_same_base_name(env):
    _, proj = env
    chief.archive_doc("plan", "Alfa", "Login with 2FA", "x", "2026-11-07T10:00:00", "# Plan")
    assert (proj / "docs" / "plans" / "2026-11-07-login-with-2fa.md").is_file()


def test_the_note_in_the_repo_follows_the_features_language(env):
    _, proj = env
    chief.archive_doc("plan", "Alfa", "Login with 2FA", "x", "2026-11-07T10:00:00", "# Plan", lang="en")
    assert (proj / "docs" / "plans" / "2026-11-07-login-with-2fa.md").read_text(encoding="utf-8").startswith("> Plan approved")
    chief.archive_doc("plan", "Alfa", "Login com 2FA", "x", "2026-11-07T10:00:00", "# Plano", lang="pt")
    assert (proj / "docs" / "plans" / "2026-11-07-login-com-2fa.md").read_text(encoding="utf-8").startswith("> Plano aprovado")


def test_never_overwrites_an_existing_document(env):
    _, proj = env
    chief.archive_doc("spec", "Alfa", "Login", "x", "2026-11-07T10:00:00", "first")
    f = proj / "docs" / "specs" / "2026-11-07-login.md"
    f.write_text("edited by hand", encoding="utf-8")
    info = chief.archive_doc("spec", "Alfa", "Login", "x", "2026-11-07T10:00:00", "second")
    assert "not overwritten" in info and f.read_text(encoding="utf-8") == "edited by hand"


@pytest.mark.parametrize("kind,project,text", [("other", "Alfa", "x"), ("spec", "Alfa", "  "), ("spec", "DoesNotExist", "x")])
def test_refuses_bad_requests(env, kind, project, text):
    with pytest.raises(ValueError):
        chief.archive_doc(kind, project, "t", "x", "2026-11-07T10:00:00", text)


# --- connection to the pipeline ------------------------------------------------------------------------------------

class Recorder:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, kind, project, title, fid, created, text, lang):
        if self.fail:
            raise ValueError("folder is locked")
        self.calls.append((kind, project, text.strip(), lang))
        return f"{kind} ok"


def make(tmp_path, rec):
    fake = Fake()
    return Features(tmp_path, fake.launch, fake.is_active, publisher=rec), fake


def test_approving_a_spec_and_a_plan_archives_each_once(tmp_path):
    rec = Recorder()
    fs, fake = make(tmp_path, rec)
    f = fs.create("Alfa", "Login with 2FA", "I want 2FA", None)
    finish_stage(fs, fake, tmp_path, f["id"], "spec.md", "# spec v1")
    fs.approve(f["id"])
    finish_stage(fs, fake, tmp_path, f["id"], "plan.md", "# plan v1")
    f = fs.approve(f["id"])
    assert [c[0] for c in rec.calls] == ["spec", "plan"] and rec.calls[0][2] == "# spec v1" and rec.calls[1][2] == "# plan v1"
    events = [h["event"] for h in f["history"]]
    assert "spec archived" in events and "plan archived" in events


def test_a_failed_archive_is_logged_and_never_blocks_the_approval(tmp_path):
    fs, fake = make(tmp_path, Recorder(fail=True))
    f = fs.create("Alfa", "Login", "x", None)
    finish_stage(fs, fake, tmp_path, f["id"], "spec.md")
    f = fs.approve(f["id"])
    assert f["stage"] == "plan" and f["status"] == "working"
    assert any(h["event"] == "spec archiving failed" and "folder is locked" in h["note"] for h in f["history"])


def test_publish_approved_catches_up_old_features_once(tmp_path):
    fake = Fake()
    plain = Features(tmp_path, fake.launch, fake.is_active)          # as before the archive existed
    f = plain.create("Alfa", "Login", "x", None)
    finish_stage(plain, fake, tmp_path, f["id"], "spec.md", "# spec")
    plain.approve(f["id"])
    rec = Recorder()
    fs = Features(tmp_path, fake.launch, fake.is_active, publisher=rec)
    assert fs.publish_approved() == 1 and [c[0] for c in rec.calls] == ["spec"]
    assert fs.publish_approved() == 0 and len(rec.calls) == 1             # idempotent


def test_publish_approved_does_not_retry_a_failure_forever(tmp_path):
    fake = Fake()
    plain = Features(tmp_path, fake.launch, fake.is_active)
    f = plain.create("Alfa", "Login", "x", None)
    finish_stage(plain, fake, tmp_path, f["id"], "spec.md")
    plain.approve(f["id"])
    fs = Features(tmp_path, fake.launch, fake.is_active, publisher=Recorder(fail=True))
    assert fs.publish_approved() == 1 and fs.publish_approved() == 0

