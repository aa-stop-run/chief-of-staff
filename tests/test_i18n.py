"""Languages: the dashboard (index.html) and the server messages are in English, with an optional Portuguese translation."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import i18n  # noqa: E402
from i18n import PAIRS, request_lang, translate  # noqa: E402

HTML = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
PH = re.compile(r"\{(\w+)\}")


def _pt_dict() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    block = re.search(r"const PT = (\{.*?\n\});\nlet LANG", HTML, re.S).group(1)
    out = subprocess.run([node, "-"], input=f"console.log(JSON.stringify({block}))", capture_output=True, text=True, encoding="utf-8")  # stdin: the dictionary outgrew Windows' command-line limit
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_every_translated_string_used_by_the_page_has_a_portuguese_version():
    pt = _pt_dict()
    used = set(re.findall(r'\btt\("((?:[^"\\]|\\.)*)"', HTML))
    used |= {m.replace('\\"', '"') for m in used}
    missing = sorted(k for k in used if k not in pt and not k.startswith("{"))
    assert not missing, f"untranslated: {missing}"


def test_portuguese_keeps_the_same_placeholders():
    for eng, pt in _pt_dict().items():
        assert set(PH.findall(eng)) == set(PH.findall(pt)), (eng, pt)


def test_static_markup_texts_are_translated():
    pt = _pt_dict()
    for needle in ("+ Add project", "+ New request", "Session to-dos", "Launch spec", "Clone", "Cancel", "Add",
                   "Type", "Feature", "Bug fix", "Analysis", "Maintenance"):
        assert needle in pt


def test_new_request_dialog_texts_have_portuguese_versions():
    """The “New request” dialog sentences (one per kind) and the launch buttons go through tt(NF[...]), outside the literal search."""
    pt = _pt_dict()
    block = re.search(r"const NF=\{(.*?)\};", HTML, re.S).group(1)
    strings = re.findall(r'"((?:[^"\\]|\\.)*)"', block)
    missing = [x for x in strings if x not in pt and x not in ("feature", "fix", "analysis", "maintenance")]
    assert not missing, missing


def test_server_messages_translate_exact_and_with_variables():
    assert translate("unknown route", "pt") == "rota desconhecida"
    assert translate("unknown route", "en") == "unknown route"
    assert translate("title required (max. 120 characters)", "pt") == "título obrigatório (máx. 120 caracteres)"
    assert translate("current state «awaiting»; this action needs «failed»", "pt") == "estado atual «awaiting»; esta ação precisa de «failed»"
    assert translate("a message without translation", "pt") == "a message without translation"


def test_every_server_message_has_matching_placeholders():
    for eng, pt in PAIRS:
        assert set(re.findall(r"\{(\d+)\}", eng)) == set(re.findall(r"\{(\d+)\}", pt)), eng


def test_the_language_header_only_switches_to_portuguese_on_request():
    assert request_lang("pt") == "pt" and request_lang("PT-pt") == "pt"
    assert request_lang(None) == "en" and request_lang("en-GB") == "en" and request_lang("fr") == "en"


def test_every_raised_user_message_in_the_code_is_translated():
    """Look for `raise XxxError("...")` with literal text in the modules that talk to the user."""
    patterns = [i18n._compile(eng) for eng, _ in PAIRS]
    missing = []
    for rel in ("app/server.py", "scripts/features.py", "scripts/pending.py", "scripts/chief.py", "scripts/curation.py", "scripts/settings.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for m in re.finditer(r'raise (?:FeatureError|PendingError|ValueError|CurationError)\(f?"((?:[^"\\]|\\.)*)"', text):
            raw = re.sub(r"\{[^}]*\}", "{0}", m.group(1))
            if raw in ("{0}",):
                continue
            if not any(p.fullmatch(raw) or p.fullmatch(m.group(1)) for p in patterns):
                missing.append((rel, raw))
    assert not missing, f"untranslated messages: {missing}"
