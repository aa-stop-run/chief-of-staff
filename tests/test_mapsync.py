"""scripts/mapsync.py: the generated map, with a fake labelling worker."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import mapsync as ms  # noqa: E402
import vaultmap as vm  # noqa: E402

HUB_DIRS = ("Projetos", "Areas")


def note(path, text, front=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntipo: projeto\n{front}---\n{text}\n", encoding="utf-8")


class Env:
    def __init__(self, tmp):
        self.t = 1_800_000_000.0
        self.vault = tmp / "Vault"
        note(self.vault / "Areas" / "Home Server.md", "Servidor caseiro que aloja o [[Personal Assistant]].", "dominio: casa\n")
        note(self.vault / "Projetos" / "Personal Assistant.md", "Assistente. Corre no [[Home Server]].", "dominio: pessoal\n")
        note(self.vault / "Projetos" / "Operacao Escola.md", "Escola, em [[Personal Assistant]].", "dominio: pessoal\n")
        (self.vault / "Mapa.canvas").write_text("hand drawn", encoding="utf-8")
        self.launched, self.states, self.commits, self.busy, self.cfg = [], {}, [], "", {}
        self.m = ms.Mapper(self.vault, tmp / "vaultmap.json", tmp / "stage", self.launch, lambda i: self.states.get(i, "working"),
                           lambda p, msg: (self.commits.append(msg) or (True, "c1")), lambda i: None, HUB_DIRS,
                           busy=lambda: self.busy, config=lambda: self.cfg, now=lambda: self.t)

    def launch(self, prompt, stage):
        w = {"id": f"w{len(self.launched)}"}
        self.launched.append({"prompt": prompt, "stage": Path(stage), **w})
        return w

    def finish(self, labels=None, raw=None):
        run = self.launched[-1]
        (run["stage"] / "labels.json").write_text(raw if raw is not None else json.dumps({"labels": labels or []}), encoding="utf-8")
        self.states[run["id"]] = "finished"
        self.t += 120
        self.m.tick()

    def canvas(self):
        return json.loads((self.vault / vm.AUTO_NAME).read_text(encoding="utf-8"))


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def label(**kw):
    item = {"a": "Home Server", "b": "Personal Assistant", "label": "aloja", "direction": "a→b", "quote": "aloja o [[Personal Assistant]]"}
    item.update(kw)
    return item


def test_the_canvas_is_written_at_once_with_counts_and_a_worker_gets_only_the_sentences(env):
    out = env.m.start(force=True)
    assert out == {"written": True, "launched": True, "pairs": 2}
    assert env.commits == ["checkpoint before the vault map", "vault map: links of the notes"]
    assert all(e["label"].startswith("citado em") for e in env.canvas()["edges"] if e["id"].startswith("e-"))
    stage = env.launched[0]["stage"]
    pairs = json.loads((stage / "pairs.json").read_text(encoding="utf-8"))["pairs"]
    assert {(p["a"], p["b"]) for p in pairs} == {("Home Server", "Personal Assistant"), ("Operacao Escola", "Personal Assistant")}
    assert str(env.vault).replace("\\", "/") not in env.launched[0]["prompt"] and not list(stage.glob("*.md"))
    assert (env.vault / "Mapa.canvas").read_text(encoding="utf-8") == "hand drawn"


def test_validated_labels_replace_the_counts_and_are_committed(env):
    env.m.start(force=True)
    env.finish([label()])
    labels = {e["id"]: e["label"] for e in env.canvas()["edges"]}
    assert labels["e-home-server--personal-assistant"] == "aloja" and labels["e-operacao-escola--personal-assistant"].startswith("citado em")
    assert env.commits[-1].startswith("vault map: 1 label(s)") and env.m.status()["labelled"] == 1 and not env.m.status()["running"]


def test_an_unchanged_pair_is_not_sent_again_and_a_changed_sentence_is(env):
    env.m.start(force=True)
    env.finish([label()])
    n = len(env.commits)
    env.t += 4000
    assert env.m.start(force=True)["launched"] is True                              # the other pair never got a label: still asked
    env.finish([])
    assert len(env.commits) == n                                                    # nothing changed in the file: no commit
    note(env.vault / "Areas" / "Home Server.md", "Servidor caseiro que NAO aloja o [[Personal Assistant]].", "dominio: casa\n")
    env.t += 4000
    env.m.start(force=True)
    sent = json.loads((env.launched[-1]["stage"] / "pairs.json").read_text(encoding="utf-8"))["pairs"]
    assert ("Home Server", "Personal Assistant") in {(p["a"], p["b"]) for p in sent}
    assert next(e for e in env.canvas()["edges"] if e["id"] == "e-home-server--personal-assistant")["label"].startswith("citado em")   # the old label no longer applies


def test_a_refused_labels_file_is_a_failure_and_the_counts_stay(env):
    env.m.start(force=True)
    env.finish([label(quote="a sentence nobody wrote")])
    st = env.m.status()
    assert st["last"]["ok"] is False and "refused" in st["last"]["info"] and st["attempts"] == 1 and st["labelled"] == 0
    env.m.start(force=True)
    env.finish(raw="not json")
    assert "no readable" in env.m.status()["last"]["info"]


def test_three_failures_stop_the_automation_but_the_button_resets_it(env):
    for _ in range(3):
        env.m.start(force=True)
        env.finish(raw="{")
        env.t += 4000
        env.m._save({**env.m._load(), "attempts": env.m._load()["attempts"]})
    s = env.m._load()
    s["attempts"], s["poked"] = 3, True
    env.m._save(s)
    n = len(env.launched)
    env.m.tick()
    assert len(env.launched) == n
    assert env.m.start(force=True)["launched"] is True


def test_it_waits_while_the_curation_or_the_sanitizer_works(env):
    env.busy = "the vault curation is running"
    with pytest.raises(ms.MapError, match="curation"):
        env.m.start(force=True)
    env.m.tick()
    assert env.launched == [] and not (env.vault / vm.AUTO_NAME).exists()


def test_with_labels_off_only_the_canvas_with_counts_is_written(env):
    env.cfg = {"labels": False}
    out = env.m.start(force=True)
    assert out["written"] is True and out["launched"] is False and env.launched == []


def test_off_means_off_and_a_poke_makes_the_next_tick_look(env):
    env.cfg = {"enabled": False}
    env.m.tick()
    assert not (env.vault / vm.AUTO_NAME).exists()
    env.cfg = {}
    env.m.tick()                                                                   # a fresh state looks at once
    assert (env.vault / vm.AUTO_NAME).exists()
    env.finish([label()])
    env.t += 100
    env.m.tick()
    assert len(env.launched) == 1                                                  # not again until an hour passes or a poke
    note(env.vault / "Projetos" / "Operacao Escola.md", "Escola, agora tambem com [[Home Server]].", "dominio: pessoal\n")
    env.m.poke()
    env.m.tick()
    assert any(e["id"] == "e-home-server--operacao-escola" for e in env.canvas()["edges"])


def test_a_usage_limit_pauses_without_counting_a_failure(env):
    env.m._limit = lambda wid: {"resume_at": "2030-01-01T00:00:00"}
    env.m.start(force=True)
    env.t += 120
    env.m.tick()
    st = env.m.status()
    assert not st["running"] and st["attempts"] == 0 and "usage limit" in st["last"]["info"]
