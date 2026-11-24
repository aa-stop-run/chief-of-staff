"""REAL Claude Code and the vault sanitizer (opt-in: `set CHIEF_REAL_TESTS=1`, then `python -m pytest tests/real -q -s -k sanitizer`).

A throw-away vault (not a git repository: the worker only ever sees copies in a stage folder) and two small repositories are seeded:
- Alfa: its hub says «sem remoto» while the repository has a remote, and two notes give different ports for the same service;
- Beta: consistent notes, which must produce NO finding (false positives matter as much as misses).
A real audit worker runs on the staged copies; the test checks the findings, that the vault was not touched by it, and Apply."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import sanitize as sz  # noqa: E402
import vaultcheck as vc  # noqa: E402

BASE = Path(os.environ.get("CHIEF_REAL_BASE", r"D:\_chief_probe")) / "sanitizer"
pytestmark = [pytest.mark.real, pytest.mark.with_concept,
              pytest.mark.skipif(os.environ.get("CHIEF_REAL_TESTS") != "1", reason="real Claude Code: set CHIEF_REAL_TESTS=1")]
SCHEMA = {"hub_dirs": ("Projetos", "Areas"), "fm": {"kind": "tipo", "status": "estado", "project": "projeto"}, "name": "Portuguese (European)"}
HUB = "---\ntipo: projeto\nestado: ativo\nstack: [Python]\nlocal: {local}\nrepo: {repo}\n---\n# {name}\n{body}\n"


def _rm(path: Path):
    def onerror(func, p, _exc):
        os.chmod(p, 0o700)
        func(p)
    if path.exists():
        shutil.rmtree(path, onerror=onerror)


def _git(repo: Path, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)


def _repo(path: Path, readme: str, remote: str = ""):
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "README.md").write_text(readme, encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "first")
    if remote:
        _git(path, "remote", "add", "origin", remote)


@pytest.fixture
def world():
    _rm(BASE)
    vault, alfa, beta = BASE / "Vault", BASE / "alfa", BASE / "beta"
    _repo(alfa, "# Alfa\n\n## Run\n\n```\npython serve.py --port 8780\n```\n", "https://github.com/example/alfa.git")
    _repo(beta, "# Beta\n\nA tiny tool. It has no remote yet.\n")
    for d in ("Projetos/Alfa", "Projetos/Beta", "wiki"):
        (vault / d).mkdir(parents=True)
    (vault / "Projetos" / "Alfa.md").write_text(HUB.format(local=alfa, repo="(sem remoto; git local)", name="Alfa", body="O repo não tem remoto."), encoding="utf-8")
    (vault / "Projetos" / "Alfa" / "Estado.md").write_text("# Estado\nO servidor do Alfa corre na porta 8780.\n", encoding="utf-8")
    (vault / "Projetos" / "Alfa" / "Operacao.md").write_text("# Operação\nPara aceder ao Alfa abre http://127.0.0.1:9000 no browser.\n", encoding="utf-8")
    (vault / "Projetos" / "Beta.md").write_text(HUB.format(local=beta, repo="(sem remoto)", name="Beta", body="O Beta é uma ferramenta pequena e ainda sem remoto."), encoding="utf-8")
    (vault / "wiki" / "log.md").write_text("# Log\n\n## 2026-11-13 — x\n- [[a]]\n", encoding="utf-8")
    yield {"vault": vault, "alfa": alfa, "beta": beta}
    _rm(BASE)


def test_a_real_audit_finds_the_seeded_problems_and_stays_silent_on_the_clean_project(world):
    ids, commits = [], []

    def launch(prompt, stage):
        r = subprocess.run(["claude", "--bg", "-n", "cos-sanitize-test", "--permission-mode", "auto", "--", prompt], cwd=stage,
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        m = re.search(r"backgrounded\s*·\s*([0-9a-f]{6,})", r.stdout + r.stderr)
        assert m, r.stdout + r.stderr
        ids.append(m[1])
        return {"id": m[1], "name": "cos-sanitize-test"}

    def state(wid):
        stage = next(Path(BASE / "stage").iterdir(), None)
        return "finished" if stage and (stage / "findings.json").exists() else "working"

    s = sz.Sanitizer(world["vault"], BASE / "sanitize.json", BASE / "stage", launch, state, lambda p, m: (commits.append(m) or (True, "c0")),
                     lambda wid: subprocess.run(["claude", "stop", wid], capture_output=True, text=True, timeout=60), SCHEMA,
                     now=time.time, note_lang="Portuguese (European)")
    before = {p: p.read_text(encoding="utf-8") for p in world["vault"].rglob("*.md")}
    assert s.start(force=True)["launched"]
    deadline = time.time() + 900
    while s.status()["running"] and time.time() < deadline:
        time.sleep(15)
        s.tick()
    st = s.status()
    print(json.dumps(st, ensure_ascii=False, indent=1))
    assert not st["running"] and st["last"]["ok"], st["last"]
    assert before == {p: p.read_text(encoding="utf-8") for p in world["vault"].rglob("*.md")}      # the audit changed nothing in the vault
    notes = {f["note"] for f in st["findings"]}
    assert "Projetos/Alfa.md" in notes, "the false «sem remoto» was not found"
    assert {"Projetos/Alfa/Estado.md", "Projetos/Alfa/Operacao.md"} & notes, "the two different ports were not found"
    assert not {n for n in notes if "Beta" in n}, "a consistent project produced a finding"
    remote = next(f for f in st["findings"] if f["note"] == "Projetos/Alfa.md")
    assert remote["evidence"] and remote["quote"]
    if remote["replace"]:                                                                          # Apply: the exact replacement, two commits
        s.apply(remote["id"])
        assert len(commits) == 2 and vc.read_note(world["vault"], "Projetos/Alfa.md") != before[world["vault"] / "Projetos" / "Alfa.md"]
