"""REAL Claude Code and the vault map (opt-in: `set CHIEF_REAL_TESTS=1`, then `python -m pytest tests/real -q -s -k vaultmap`).

A throw-away vault (not a git repository: the worker only ever sees `pairs.json`) has three hubs. Two relations are STATED in the notes
(«o Alfa aloja o Beta», «o Gama é o sucessor do Alfa») and one is only a bare link («Ver também [[Delta]]»). A real worker labels the pairs;
the test checks the two stated ones get a fitting label and direction, the bare one keeps its count, and that no label was invented."""
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
import chief  # noqa: E402
import mapsync as ms  # noqa: E402
import vaultmap as vm  # noqa: E402

PROBE = Path(os.environ.get("CHIEF_REAL_BASE", r"D:\_chief_probe"))
pytestmark = [pytest.mark.real, pytest.mark.with_concept,
              pytest.mark.skipif(os.environ.get("CHIEF_REAL_TESTS") != "1", reason="real Claude Code: set CHIEF_REAL_TESTS=1")]


def note(path, text, dom):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntipo: projeto\ndominio: {dom}\n---\n{text}\n", encoding="utf-8")


def test_a_real_worker_labels_the_stated_relations_and_leaves_the_bare_link_with_its_count(tmp_path):
    vault = tmp_path / "Vault"
    note(vault / "Projetos" / "Alfa.md", "O Alfa é o servidor caseiro que aloja o [[Beta]].", "casa")
    note(vault / "Projetos" / "Beta.md", "Aplicação de finanças pessoais. Ver também [[Delta]].", "casa")
    note(vault / "Projetos" / "Gama.md", "O Gama é o sucessor conceptual do [[Alfa]], sem código partilhado.", "casa")
    note(vault / "Projetos" / "Delta.md", "Uma coisa qualquer.", "casa")
    stage_root = PROBE / "proj" / ".mapstage"
    shutil.rmtree(stage_root, ignore_errors=True)
    commits = []

    def launch(prompt, stage):
        r = subprocess.run(["claude", "--bg", "-n", "cos-map-test", "--permission-mode", "auto", "--settings", chief.BG_NO_ISOLATION, "--", prompt], cwd=stage,
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        m = re.search(r"backgrounded\s*·\s*([0-9a-f]{6,})", r.stdout + r.stderr)
        assert m, r.stdout + r.stderr
        return {"id": m[1], "name": "cos-map-test"}

    def state(wid):
        runs = [p for p in stage_root.iterdir()] if stage_root.exists() else []
        return "finished" if runs and (runs[0] / "labels.json").exists() else "working"

    m = ms.Mapper(vault, tmp_path / "vaultmap.json", stage_root, launch, state, lambda p, msg: (commits.append(msg) or (True, "c")),
                  lambda wid: subprocess.run(["claude", "stop", wid], capture_output=True, text=True, timeout=60), ("Projetos", "Areas"), now=time.time)
    try:
        assert m.start(force=True)["launched"]
        deadline = time.time() + 900
        while m.status()["running"] and time.time() < deadline:
            time.sleep(15)
            m.tick()
        print(m.status())
        assert not m.status()["running"] and m.status()["last"]["ok"], m.status()["last"]
        data = vm.canvas  # noqa: F841 - the canvas is read back from the file the Mapper wrote
        import json
        canvas = json.loads((vault / vm.AUTO_NAME).read_text(encoding="utf-8"))
        edges = {e["id"]: e for e in canvas["edges"] if e["id"].startswith("e-")}
        print({k: (e["label"], e.get("fromEnd"), e.get("toEnd")) for k, e in edges.items()})
        assert vm.check_canvas(canvas) == []
        assert len(edges) == 3
        alfa_beta = edges["e-alfa--beta"]
        assert not alfa_beta["label"].startswith("citado") and "alberg" in alfa_beta["label"].lower() or "aloj" in alfa_beta["label"].lower() or "host" in alfa_beta["label"].lower()
        assert alfa_beta.get("fromEnd") is None and alfa_beta.get("toEnd") != "none"                 # Alfa -> Beta
        alfa_gama = edges["e-alfa--gama"]
        assert not alfa_gama["label"].startswith("citado") and "suce" in alfa_gama["label"].lower()
        assert alfa_gama.get("fromEnd") == "arrow" and alfa_gama.get("toEnd") == "none"            # Gama -> Alfa (b -> a of the sorted pair)
        assert edges["e-beta--delta"]["label"].startswith("citado em"), "a bare link got an invented label"
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
