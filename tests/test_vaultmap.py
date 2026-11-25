"""scripts/vaultmap.py: hubs and linked pairs from the notes, the validated labels, the layout and the canvas."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import vaultmap as vm  # noqa: E402
from vaultcheck import FindingsError  # noqa: E402

HUB_DIRS = ("Projetos", "Areas")


def note(path, text, front=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntipo: projeto\n{front}---\n{text}\n", encoding="utf-8")


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "Vault"
    note(v / "Areas" / "Home Server.md", "Servidor caseiro que aloja o [[Personal Assistant]] e a [[Operacao Escola]].", "dominio: casa\n")
    note(v / "Projetos" / "Personal Assistant.md", "Corre no [[Home Server]]. Tambem [[Mundo Inexistente]].", "dominio: pessoal\n")
    note(v / "Projetos" / "Personal Assistant" / "Arquitetura.md", "Ver [[Operacao Escola]] para o ciclo escolar.", "dominio: pessoal\n")
    note(v / "Projetos" / "Operacao Escola.md", "Escola. Sem links para fora.", "dominio: pessoal\n")
    note(v / "Projetos" / "Chief Pessoal.md", "Ferramenta. Link para si [[Chief Pessoal]].", "dominio: ferramentas\n")
    for i in range(5):
        note(v / "Projetos" / "Chief Pessoal" / f"Nota {i}.md", "x", "dominio: ferramentas\n")
    return v


def test_hubs_have_kind_domain_and_at_most_four_key_notes(vault):
    hubs = vm.hubs_of(vault, HUB_DIRS)
    assert set(hubs) == {"Home Server", "Personal Assistant", "Operacao Escola", "Chief Pessoal"}
    assert hubs["Home Server"]["kind"] == "area" and hubs["Personal Assistant"]["kind"] == "project" and hubs["Personal Assistant"]["domain"] == "pessoal"
    assert len(hubs["Chief Pessoal"]["detail"]) == vm.MAX_DETAIL and hubs["Personal Assistant"]["detail"] == ["Projetos/Personal Assistant/Arquitetura.md"]


def test_a_pair_exists_only_because_a_note_links_the_other_hub(vault):
    pairs = {(p["a"], p["b"]): p for p in vm.collect(vault, HUB_DIRS)["pairs"]}
    assert set(pairs) == {("Home Server", "Personal Assistant"), ("Home Server", "Operacao Escola"), ("Operacao Escola", "Personal Assistant")}
    hs_pa = pairs[("Home Server", "Personal Assistant")]
    assert hs_pa["ab"] == 1 and hs_pa["ba"] == 1 and hs_pa["count"] == 2 and len(hs_pa["sentences"]) == 2      # both notes speak of it
    assert not any({"Mundo Inexistente", "Chief Pessoal"} & {p["a"], p["b"]} for p in pairs.values())   # unknown targets and self-links
    assert pairs[("Operacao Escola", "Personal Assistant")]["ba"] == 1                                         # a link in a detail note counts for its hub


def test_the_sentences_are_redacted_and_the_result_is_stable(vault):
    note(vault / "Projetos" / "Operacao Escola.md", "Fala com [[Home Server]] em 192.168.1.20 e a@b.pt.")
    first = vm.collect(vault, HUB_DIRS)
    assert "192.168" not in json.dumps(first) and "a@b.pt" not in json.dumps(first)
    assert first == vm.collect(vault, HUB_DIRS)


def test_a_pair_hash_follows_its_sentences(vault):
    a = {p["a"] + p["b"]: p["hash"] for p in vm.collect(vault, HUB_DIRS)["pairs"]}
    note(vault / "Areas" / "Home Server.md", "Servidor caseiro que NAO aloja o [[Personal Assistant]] e a [[Operacao Escola]].", "dominio: casa\n")
    b = {p["a"] + p["b"]: p["hash"] for p in vm.collect(vault, HUB_DIRS)["pairs"]}
    assert a["Home ServerPersonal Assistant"] != b["Home ServerPersonal Assistant"] and a["Operacao EscolaPersonal Assistant"] == b["Operacao EscolaPersonal Assistant"]


def good(**kw):
    item = {"a": "Home Server", "b": "Personal Assistant", "label": "aloja", "direction": "a→b", "quote": "aloja o [[Personal Assistant]]"}
    item.update(kw)
    return {"labels": [item]}


def test_valid_labels_are_normalised_to_the_sorted_pair(vault):
    pairs = vm.collect(vault, HUB_DIRS)["pairs"]
    assert vm.validate_labels(good(), pairs) == [{"key": "Home Server|Personal Assistant", "label": "aloja", "direction": "a→b"}]
    swapped = good(a="Personal Assistant", b="Home Server", label="corre em", direction="a→b", quote="Corre no [[Home Server]]")
    assert vm.validate_labels(swapped, pairs)[0]["direction"] == "b→a"                                         # a→b of the swapped item is b→a of the sorted pair


@pytest.mark.parametrize("mutate,why", [
    (dict(b="Chief Pessoal"), "not a linked pair"), (dict(label=""), "characters"), (dict(label="x" * 41), "characters"),
    (dict(direction="up"), "direction"), (dict(quote="invented sentence"), "quote"), (dict(label="ver 10.0.0.5"), "secret"),
    (dict(label="mail a@b.pt"), "secret")])
def test_a_bad_label_refuses_the_whole_file(vault, mutate, why):
    with pytest.raises(FindingsError, match=why):
        vm.validate_labels(good(**mutate), vm.collect(vault, HUB_DIRS)["pairs"])


def test_the_canvas_is_valid_has_layers_and_count_labels_by_default(vault):
    mp = vm.collect(vault, HUB_DIRS)
    data = vm.canvas(mp)
    assert vm.check_canvas(data) == []
    groups = [n["label"] for n in data["nodes"] if n["type"] == "group"]
    assert groups[0] == "Áreas" and groups[1:] == ["casa" if False else "ferramentas", "pessoal"]
    hub_edges = [e for e in data["edges"] if e["id"].startswith("e-")]
    assert len(hub_edges) == 3 and all(e["label"].startswith("citado em") for e in hub_edges)
    n_files = [n for n in data["nodes"] if n["type"] == "file"]
    assert len([n for n in n_files if n["id"].startswith("h-")]) == 4 and len(n_files) == 4 + 4 + 1             # 4 hubs, 4+1 key notes
    ys = {n["id"]: n["y"] for n in data["nodes"]}
    assert ys["h-home-server"] < ys["h-chief-pessoal"] < ys["h-personal-assistant"]                           # areas above, then domains in order


def test_labels_replace_the_counts_and_set_the_arrow(vault):
    mp = vm.collect(vault, HUB_DIRS)
    data = vm.canvas(mp, {"Home Server|Personal Assistant": {"label": "aloja", "direction": "a→b"}, "Operacao Escola|Personal Assistant": {"label": "usa", "direction": "b→a"}})
    by = {e["id"]: e for e in data["edges"]}
    assert by["e-home-server--personal-assistant"]["label"] == "aloja" and "fromEnd" not in by["e-home-server--personal-assistant"]
    assert by["e-operacao-escola--personal-assistant"]["fromEnd"] == "arrow" and by["e-operacao-escola--personal-assistant"]["toEnd"] == "none"


def test_the_same_input_gives_a_byte_identical_canvas(vault):
    mp = vm.collect(vault, HUB_DIRS)
    assert vm.canvas_text(mp) == vm.canvas_text(vm.collect(vault, HUB_DIRS))


def test_only_the_generated_file_is_written_and_unchanged_content_is_not_rewritten(vault):
    text = vm.canvas_text(vm.collect(vault, HUB_DIRS))
    (vault / "Mapa.canvas").write_text("hand drawn", encoding="utf-8")
    assert vm.write_canvas(vault, vm.AUTO_NAME, text) is True and vm.write_canvas(vault, vm.AUTO_NAME, text) is False
    assert (vault / "Mapa.canvas").read_text(encoding="utf-8") == "hand drawn"
    for bad in ("Mapa.canvas", "../x.canvas", "wiki/Mapa automático.canvas"):
        with pytest.raises(ValueError):
            vm.write_canvas(vault, bad, text)


def test_the_fixed_texts_follow_the_vault_language(vault):
    mp = vm.collect(vault, HUB_DIRS)
    pt, en = vm.canvas(mp), vm.canvas(mp, lang="en")
    assert [n["label"] for n in pt["nodes"] if n["type"] == "group"][0] == "Áreas" and [n["label"] for n in en["nodes"] if n["type"] == "group"][0] == "Areas"
    assert all(e["label"].startswith("cited in") for e in en["edges"] if e["id"].startswith("e-"))
    assert vm.lang_of("Portuguese (European)") == "pt" and vm.lang_of("English") == "en"
