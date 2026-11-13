"""The page's URL routes: Today, the Projects list and one project (pure helpers, run with node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "app" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def call(expr: str):
    src = HTML.read_text(encoding="utf-8")
    code = src[src.index("// <routes>"):src.index("// </routes>")] + f"\nconsole.log(JSON.stringify({expr}))"
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("h,route", [("", {"view": "today", "q": ""}), ("#", {"view": "today", "q": ""}),
                                     ("#q=chief", {"view": "today", "q": "chief"}), ("#q=my+repo", {"view": "today", "q": "my repo"})])
def test_empty_and_query_hashes_are_today(h, route):
    assert call(f"parseRoute({json.dumps(h)})") == route


def test_projects_route():
    assert call("parseRoute('#/projects')") == {"view": "projects"}


def test_project_route_decodes_spaces_and_symbols():
    assert call("parseRoute('#/project/R%26D%20Lab%20%231')") == {"view": "project", "name": "R&D Lab #1"}


@pytest.mark.parametrize("route", [{"view": "today", "q": ""}, {"view": "today", "q": "my repo & co"}, {"view": "projects"},
                                   {"view": "project", "name": "R&D Lab #1"}, {"view": "project", "name": "ação/ü"}])
def test_route_hash_round_trips(route):
    assert call(f"parseRoute(routeHash({json.dumps(route)}))") == route


def test_route_hash_shapes():
    assert call("routeHash({view:'today',q:''})") == ""
    assert call("routeHash({view:'today',q:'a b'})") == "#q=a+b"
    assert call("routeHash({view:'projects'})") == "#/projects"
    assert call("routeHash({view:'project',name:'R&D Lab #1'})") == "#/project/R%26D%20Lab%20%231"


@pytest.mark.parametrize("h", ["#/nope", "#/projects/x", "#/project/", "#/project", "#foo", "#/project/%E0%A4%A", "#/project/%"])
def test_unknown_hash_falls_back_to_today(h):
    assert call(f"parseRoute({json.dumps(h)})") == {"view": "today", "q": ""}


def test_page_has_tabs_and_view_containers_and_pt_labels():
    html = HTML.read_text(encoding="utf-8")
    for needle in ('id="todayView"', 'id="projectsView"', 'id="projectView"', 'aria-current', '"Today":"Hoje"', '"Projects":"Projetos"'):
        assert needle in html


def test_projects_trailing_slash_is_projects():
    assert call("parseRoute('#/projects/')") == {"view": "projects"}


def test_project_name_with_slash_is_kept_whole():
    assert call("parseRoute('#/project/a/b')") == {"view": "project", "name": "a/b"}


ROWS = [{"name": "zeta", "attention": 4, "fixes": 0}, {"name": "Beta", "attention": 0, "fixes": 1},
        {"name": "alpha", "attention": 0, "fixes": 3}, {"name": "gamma", "attention": 1, "fixes": 9},
        {"name": "Delta", "attention": 1, "fixes": 9}]


def test_sort_by_attention_puts_awaiting_first_then_fixes_then_name():
    out = call(f"sortProjects({json.dumps(ROWS)}, 'attention').map(r=>r.name)")
    assert out == ["alpha", "Beta", "Delta", "gamma", "zeta"]


def test_sort_by_name_is_case_insensitive():
    out = call(f"sortProjects({json.dumps(ROWS)}, 'name').map(r=>r.name)")
    assert out == ["alpha", "Beta", "Delta", "gamma", "zeta"]
    assert call(f"sortProjects([{{name:'b'}},{{name:'A'}}], 'name').map(r=>r.name)") == ["A", "b"]


def test_sort_returns_new_array_and_leaves_input_alone():
    expr = f"(()=>{{const a={json.dumps(ROWS)},b=sortProjects(a,'attention');return [b!==a,a.map(r=>r.name)]}})()"
    assert call(expr) == [True, [r["name"] for r in ROWS]]


def test_unknown_sort_key_defaults_to_attention():
    assert call(f"sortProjects({json.dumps(ROWS)}, 'x').map(r=>r.name)")[0] == "alpha"


def test_today_pending_cards_keep_their_status_pill_class():
    html = HTML.read_text(encoding="utf-8")
    assert 'class="pst ${' in html and ".pst.awaiting{" in html
    assert 'class="pstt ${esc(x.status)}"' not in html


def test_projects_view_skips_identical_redraws_and_guards_the_fetch():
    html = HTML.read_text(encoding="utf-8")
    assert "if(html===PROJ.html)return" in html and "if(PROJ.busy)return" in html


# ---- the project page (task 8) ----

REVIEW_FIX = {"source": "review", "request": "cart-demo03", "title": "Cart total", "severity": "HIGH",
              "text": "[HIGH] src/cart.py:88 — rounding is still half-even for refunds:** `refund_total()` keeps the old `round()`."}


def test_fix_request_prefill_has_kind_fix_the_finding_and_the_source_id():
    out = call(f"fixPrefill('Storefront', {json.dumps(REVIEW_FIX)})")
    assert out["project"] == "Storefront" and out["kind"] == "fix"
    assert out["title"].startswith("Fix: src/cart.py:88") and len(out["title"]) <= 70
    assert "**" not in out["title"] and "[HIGH]" not in out["title"]
    assert "refund_total()" in out["desc"] and out["desc"].endswith("\n\nFrom request cart-demo03.")
    assert "**" not in out["desc"]


def test_fix_prefill_clips_the_title_to_the_dialog_limit_and_survives_missing_fields():
    long = {"text": "word " * 200, "request": "r1"}
    out = call(f"fixPrefill('P', {json.dumps(long)})")
    assert len(out["title"]) <= 120 and out["title"].endswith("…")
    assert call("fixPrefill('P', {})") == {"project": "P", "kind": "fix", "title": "", "desc": ""}
    assert call("fixPrefill('P', null)")["kind"] == "fix"
    assert "From request" not in call("fixPrefill('P', {text:'x'})")["desc"]


def test_fix_prefill_can_be_translated():
    t = r"(k,v)=>({'Fix: {t}':'Corrigir: {t}','From request {id}.':'Do pedido {id}.'}[k]||k).replace(/\{(\w+)\}/g,(m,x)=>v[x])"
    out = call(f"fixPrefill('P', {{text:'abc', request:'r9'}}, {t})")
    assert out["title"] == "Corrigir: abc" and out["desc"] == "abc\n\nDo pedido r9."


def test_fix_parts_split_the_severity_head_and_body():
    out = call(f"fixParts({json.dumps(REVIEW_FIX['text'])})")
    assert out["sev"] == "HIGH" and out["head"].startswith("src/cart.py:88") and out["body"].startswith("`refund_total()`")
    assert call("fixParts('just a to-do')") == {"sev": "", "head": "", "body": "just a to-do"}


@pytest.mark.parametrize("src,want", [
    ("", ""), (None, ""),
    ("---\nlocal: x\ndomain: y\n---\n# Storefront\n\nOnline shop: **catalogue**, cart and `checkout`.\n\n## Architecture\n\nThree layers.",
     "Online shop: catalogue, cart and checkout."),
    ("# T\n\n[![ci](https://x/b.svg)](https://x)\n\nA [tool](https://x.io) for things.", "A tool for things."),
    ("# Only a heading\n\n## And another", ""),
    ("```\ncode\n```\n\nReal text\nover two lines.", "Real text over two lines."),
    ("- a list item\n\nAfter the list.", "After the list."),
])
def test_first_paragraph_is_the_first_meaningful_prose(src, want):
    assert call(f"firstParagraph({json.dumps(src)})") == want


def test_first_paragraph_is_clipped():
    out = call(f"firstParagraph({json.dumps('word ' * 100)})")
    assert 150 < len(out) <= 221 and out.endswith("…")


@pytest.mark.parametrize("date,want", [("2026-11-11", "today"), ("2026-11-10", "yesterday"), ("2026-11-01", "10 days ago"), ("", ""), ("junk", "")])
def test_commit_day_is_a_date_distance_never_a_time(date, want):
    assert call(f"daysAgo({json.dumps(date)}, Date.parse('2026-11-11T15:00:00'))") == want


def test_today_project_names_link_to_the_project_page():
    html = HTML.read_text(encoding="utf-8")
    assert html.count("projLink(") >= 4                                # definition, cards, to-dos, the guarded projCell
    body = html[html.index("function projLink"):]
    assert "projectHref(" in body[:200]                              # which builds the href with routeHash
    for fn in ("function featCard", "function pendCard"):
        seg = html[html.index(fn):html.index(fn) + 900]
        assert "projLink(" in seg, fn
    workers = html[html.index("const row=w=>"):html.index("const row=w=>") + 300]
    assert "projLink(" in workers or "projCell(" in workers


def test_a_closed_item_summary_has_no_link_inside_it():
    html = HTML.read_text(encoding="utf-8")
    seg = html[html.index("function closedItem"):html.index("function closedItem") + 600]
    summary = seg[seg.index("<summary>"):seg.index("</summary>")]
    assert "projLink(" not in summary and "<a " not in summary


def test_only_names_in_the_project_list_get_a_project_link():
    assert call("projectHref('Vault curation', ['Storefront','R&D Lab #1'])") == ""
    assert call("projectHref('R&D Lab #1', ['Storefront','R&D Lab #1'])") == "#/project/R%26D%20Lab%20%231"
    assert call("projectHref('', ['Storefront'])") == "" and call("projectHref('x', null)") == ""
    html = HTML.read_text(encoding="utf-8")
    body = html[html.index("function projLink"):html.index("function projLink") + 400]
    assert "projectHref(" in body


def test_fix_prefill_uses_the_server_head_and_body_when_present():
    fix = {"source": "review", "request": "r1", "severity": "HIGH", "head": "src/a.py:3 — Crash",
           "body": "it divides by `n` when n is 0.", "text": "src/a.py:3 — Crash — it divides by `n` when n is 0."}
    out = call(f"fixPrefill('P', {json.dumps(fix)})")
    assert out["title"] == "Fix: src/a.py:3 — Crash"
    assert out["desc"] == "[HIGH] src/a.py:3 — Crash: it divides by `n` when n is 0.\n\nFrom request r1."


def test_fix_prefill_with_nothing_to_say_leaves_the_title_empty():
    assert call("fixPrefill('P', {})")["title"] == ""
    assert call("fixPrefill('P', {head:'', body:'', text:''})")["title"] == ""


def test_the_project_page_renders_server_text_only_through_md_or_esc():
    html = HTML.read_text(encoding="utf-8")
    seg = html[html.index("// ---------- project page ----------"):html.index("// ---------- end project page ----------")]
    assert "md(" in seg and "esc(" in seg
    assert "if(html===PJ.html)return" in seg and "if(PJ.busy)return" in seg
    assert "onclick" not in seg and "innerHTML=d.text" not in seg and "innerHTML=j.text" not in seg
    assert ".pcost .pn{position:relative" in html or ".pcost .pn{color:var(--ink);font-weight:500;position:relative" in html


def test_the_project_page_strings_have_portuguese_entries():
    html = HTML.read_text(encoding="utf-8")
    for k in ('"Fix: {t}":', '"From request {id}.":', '"today":', '"yesterday":', '"Overview and architecture":',
              '"Create fix request":', '"Back to projects":', '"Documents":', '"Tools":'):
        assert k in html, k


# ---- Obsidian wikilinks, embeds and callouts in vault text (fix round 1) ----

NAMES = ["Storefront", "AVA Nexus", "R&D Lab #1"]


@pytest.mark.parametrize("src,want", [
    ("see [[Smart Home]] now", "see Smart Home now"),
    ("[[Projetos/Chief Pessoal/Operação|ver Operação]]", "ver Operação"),
    ("[[Smart Home#Lights]] and [[Smart Home#^b1c2]]", "Smart Home and Smart Home"),
    ("[[Notes#Section|Alias wins]]", "Alias wins"),
    ("[[wiki/folder/Target]]", "Target"),
    ("before ![[Vista do projeto.base]] after", "before  after"),
    ("![[image.png|300]]", ""),
    ("[!info] Entities of this project", "Entities of this project"),
    ("a [[ broken link", "a [[ broken link"),
    ("[[]] stays", "[[]] stays"),
])
def test_wikilinks_become_plain_text(src, want):
    assert call(f"cleanWikilinks({json.dumps(src)}, {json.dumps(NAMES)})") == want


def test_a_wikilink_to_a_project_becomes_a_project_link_case_insensitively():
    out = call(f"cleanWikilinks('Uses [[ava nexus]] and [[Projetos/storefront|the shop]].', {json.dumps(NAMES)})")
    assert '<a class="plink" href="#/project/AVA%20Nexus">ava nexus</a>' in out
    assert 'href="#/project/Storefront">the shop</a>' in out and out.startswith("Uses ")
    amp = call(f"cleanWikilinks('[[R&D Lab|lab]]', ['R&D Lab'])")
    assert amp == '<a class="plink" href="#/project/R%26D%20Lab">lab</a>'
    assert "[[" not in out


def test_wikilink_text_is_escaped_never_raw_html():
    out = call(f"cleanWikilinks('[[<img src=x onerror=alert(1)>]] & <b>', {json.dumps(NAMES)})")
    assert "<img" not in out and "<b>" not in out and "&lt;img src=x onerror=alert(1)&gt;" in out and "&amp;" in out
    assert "<a" not in call("cleanWikilinks('[[Storefront\" onmouseover=\"x]]', ['Storefront'])")


def test_the_header_sentence_has_no_wikilink_syntax():
    src = "---\nx: 1\n---\n# P\n\n![[Vista do projeto.base]]\n\nHome Assistant ([[Smart Home]]), [[Personal Assistant|PA]] and [[AVA Nexus]]."
    assert call(f"firstParagraph({json.dumps(src)})") == "Home Assistant (Smart Home), PA and AVA Nexus."


def test_md_renders_wikilinks_and_drops_callout_markers_and_embeds():
    html = HTML.read_text(encoding="utf-8")
    seg = html[html.index("function mdInline"):html.index("function mdInline") + 800]
    assert "wikiHtml(" in seg
    md = html[html.index("function md(src"):html.index("function md(src") + 2500]
    assert "dropEmbeds(" in md and "[!" in md


# ---- fix round 2: no quadratic regexes, no re-processed project links, stable done list ----

def call_md(expr: str, projects=("a*b", "Proj")):
    """Run `expr` with the routes region plus esc/mdInline/md (S holds a small project list)."""
    src = HTML.read_text(encoding="utf-8")
    esc_line = src[src.index("const esc=s=>"):src.index("\n", src.index("const esc=s=>"))]
    code = (src[src.index("// <routes>"):src.index("// </routes>")] + "\n" + esc_line + "\n"
            + f"var S={{projects:{json.dumps([{'name': n} for n in projects])}}};\n"
            + src[src.index("function mdInline"):src.index("function artifacts")]
            + f"\nconsole.log(JSON.stringify({expr}))")
    r = subprocess.run(["node", "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


EVIL = {"wiki_open": "'[['.repeat(100000)", "brackets": "'['.repeat(100000)", "close_paren": "']('.repeat(100000)",
        "unterminated": "'[x]('+'a'.repeat(200000)", "embeds": "'![['.repeat(50000)",
        "texty": "('[a'+'b'.repeat(50)).repeat(5000)", "wiki_unclosed": "'[[a'.repeat(50000)"}


@pytest.mark.parametrize("fn", ["firstParagraph(x)", "md(x)", "cleanWikilinks(x,['Proj'])", "mdText(x)"])
def test_hostile_markdown_renders_in_well_under_a_second(fn):
    exprs = ",".join(f"(()=>{{const x={v};const t=Date.now();{fn};return Date.now()-t}})()" for v in EVIL.values())
    times = call_md(f"[{exprs}]")
    assert max(times) < 700, dict(zip(EVIL, times))


def test_normal_links_still_render():
    out = call_md("md('see [the docs](https://e.com/x) and ![img](https://e.com/i.png)')")
    assert '<a href="https://e.com/x" target="_blank" rel="noopener noreferrer">the docs</a>' in out
    assert call_md("mdText('a [link](https://e.com) b')") == "a link b"


def test_a_generated_project_link_is_never_reprocessed_by_the_inline_rules():
    out = call_md("mdInline('*hi [[a*b]]*')")
    assert out == '<em>hi <a class="plink" href="#/project/a*b">a*b</a></em>'
    out = call_md("mdInline('[[[Proj]]](https://e.com)')")
    assert out.count("<a ") == 1 and out.count("</a>") == 1 and '<a class="plink" href="#/project/Proj">Proj</a>' in out
    assert call_md("mdInline('~~[[Proj]]~~ **[[Proj]]**')").count('<a class="plink"') == 2


def test_a_project_cell_has_the_link_class_once():
    html = HTML.read_text(encoding="utf-8")
    body = html[html.index("function projLink"):html.index("function projLink") + 500]
    assert 'new Set([cls||"fp","plink"])' in body


def test_the_done_list_is_compared_and_stored_as_the_same_string():
    html = HTML.read_text(encoding="utf-8")
    seg = html[html.index("function renderFeatures"):html.index("let doneOpen")]
    assert "if(dn===doneHtml)return" in seg and "doneHtml=dn;" in seg


# ---- fix round 3: placeholders never reach a link URL, raw NULs, nested quotes ----

def test_a_wikilink_inside_a_link_url_does_not_break_the_markup():
    import re as _re
    for src in ("[x](https://e.com/[[Proj]])", "| a |\n|---|\n| [x](https://e.com/[[Proj]]) |"):
        out = call_md(f"md({json.dumps(src)})")
        hrefs = _re.findall(r'href="([^"]*)"', out)
        assert hrefs and all("<" not in h and ">" not in h for h in hrefs), out
        assert out.count("<a ") == out.count("</a>")


def test_raw_nul_characters_cannot_forge_code_spans():
    out = call_md("mdInline('`a` \u00000\u0000 tail')")
    assert out.count("<code>") == 1 and "undefined" not in out and "\u0000" not in out
    out = call_md("mdInline('\u00005\u0000 x')")
    assert "undefined" not in out and "<code>" not in out


def test_long_link_urls_up_to_2000_chars_still_render():
    out = call_md("md('[x](https://e.com/'+'a'.repeat(1500)+')')")
    assert '<a href="https://e.com/' in out


@pytest.mark.parametrize("src", ["'> '.repeat(20000)+'x'", "'> [!note] '.repeat(50000)", "('>'.repeat(30000)+'\\n').repeat(3)"])
def test_deeply_nested_blockquotes_do_not_overflow(src):
    ms = call_md(f"(()=>{{const t=Date.now();const h=md({src});return [Date.now()-t,h.length>0]}})()")
    assert ms[1] and ms[0] < 1500, ms


def test_fix_keys_are_stable_url_safe_and_tell_findings_apart():
    a = {"source": "review", "request": "r 1/x", "head": "src/a.py:1 — bad & worse"}
    b = {**a, "head": "src/b.py:2 — other"}
    ka, kb = call(f"fixKey({json.dumps(a)})"), call(f"fixKey({json.dumps(b)})")
    assert ka != kb and ka == call(f"fixKey({json.dumps(dict(reversed(list(a.items()))))})")
    assert all(ch.isalnum() or ch in "-_.!~*'()%" for ch in ka) and "/" not in ka and " " not in ka


def test_fix_keys_tell_same_head_findings_apart_and_duplicates_by_occurrence():
    base = {"source": "review", "request": "r1", "severity": "HIGH", "head": "Missing test"}
    a, b = {**base, "body": "cart"}, {**base, "body": "refund", "severity": "CRITICAL"}
    keys = call(f"fixKeys({json.dumps([a, b, a, a])})")
    assert len(set(keys)) == 4 and keys[0] != keys[1]
    assert keys == call(f"fixKeys({json.dumps([a, b, a, a])})")
    assert call(f"fixKeys({json.dumps([a])})")[0] == keys[0]          # the first occurrence keeps the plain key
    assert call(f"fixKeys({json.dumps([b, a])})")[1] == keys[0]


def test_a_stale_fix_key_is_not_found_and_the_click_shows_a_toast():
    src = HTML.read_text(encoding="utf-8")
    i = src.index("const fb=e.target.closest")
    assert "fixKeys(fl)" in src[i:i + 600] and "no longer listed" in src[i:i + 600]
