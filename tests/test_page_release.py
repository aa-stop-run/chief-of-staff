"""The page knows the «Production release» kind: the promotion is only enabled by a POSITIVE independent regression."""
import json
import subprocess
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "app" / "index.html").read_text(encoding="utf-8")


def test_the_release_kind_is_offered_and_its_panel_only_enables_the_promotion_on_a_positive_verdict():
    assert '<option value="release">Production release</option>' in HTML and 'release:["plan","build","review"]' in HTML
    seg = HTML[HTML.index("function releasePanel"):HTML.index("function shipAuth")]
    assert '${v==="positive"?"":" disabled"}' in seg and 'data-act="rerun"' in seg and "Approving authorises exactly this" in seg
    assert 'if(f.stage==="build"&&f.kind==="release")return releasePanel' in HTML
    assert 'act==="rerun"?"revise":act' in HTML


def test_the_release_verdict_reader_matches_the_servers_rule():
    fn = HTML[HTML.index("function relVerdict(src)"):HTML.index("function relVerdictLabel")]
    code = ("const extractSection=(src,re)=>{const L=src.split('\\n');const i=L.findIndex(l=>re.test(l));if(i<0)return '';"
            "let o=[];for(let j=i+1;j<L.length&&!/^#{2,3}\\s/.test(L[j]);j++)o.push(L[j]);return o.join('\\n');};\n" + fn +
            "\nconsole.log(JSON.stringify(['## Regression verdict\\nPOSITIVE\\nx','## Regression verdict\\nNEGATIVO',"
            "'## Regression verdict\\nPOSITIVE or NEGATIVE','nothing'].map(relVerdict)))")
    r = subprocess.run(["node", "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["positive", "negative", "unknown", "unknown"]
