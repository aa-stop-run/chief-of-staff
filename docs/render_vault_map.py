#!/usr/bin/env python3
"""Draws docs/img/vault-map.svg: what the generated vault map looks like, from a small SAMPLE vault built in a temporary folder.

    python docs/render_vault_map.py

It uses the same code that writes the real `Mapa automático.canvas` (`scripts/vaultmap.py`), so the picture is the real layout; the labels are the
ones a worker would write from the sentences below. Nothing of anyone's own vault is read."""
import html
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import vaultmap  # noqa: E402

NOTES = {
    "Areas/Home Server.md": ("home", "A small server at home that hosts [[Home Automation]] and [[Photo Archive]]."),
    "Projects/Home Automation.md": ("home", "Lights and heating. Runs on the [[Home Server]]."),
    "Projects/Photo Archive.md": ("home", "Family photos, backed up nightly."),
    "Projects/Storefront.md": ("shop", "The web shop. Its checkout calls the [[Payments API]]."),
    "Projects/Payments API.md": ("shop", "A small service that the [[Storefront]] uses to take payments. Successor of [[Legacy Checkout]]."),
    "Projects/Legacy Checkout.md": ("shop", "The old checkout, kept until the migration ends."),
    "Projects/Dotfiles.md": ("tools", "Shell and editor settings."),
    "Projects/Dotfiles/Setup.md": ("tools", "How to install the settings on a new machine."),
    "Projects/Storefront/State.md": ("shop", "Release 4 is out."),
    "Projects/Storefront/Architecture.md": ("shop", "Django and a queue."),
}
LABELS = {"Home Automation|Home Server": ("runs on", "a→b"), "Home Server|Photo Archive": ("hosts", "a→b"),
          "Legacy Checkout|Payments API": ("replaced by", "a→b"), "Payments API|Storefront": ("is used by", "a→b")}
GROUP = {"1": "#7c6cf0", "2": "#3aa17e", "3": "#d1883b", "4": "#c4527a", "5": "#4f9bd9", "6": "#9aa0a6"}


def build(base: Path):
    for rel, (dom, text) in NOTES.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\ntype: project\ndomain: {dom}\n---\n{text}\n", encoding="utf-8")
    mp = vaultmap.collect(base, ("Projects", "Areas"))
    labels = {k: {"label": v[0], "direction": v[1]} for k, v in LABELS.items()}
    return mp, vaultmap.canvas(mp, labels, "en")


def svg(data) -> str:
    nodes = [n for n in data["nodes"] if n["type"] != "text"]
    x0, y0 = min(n["x"] for n in nodes) - 20, min(n["y"] for n in nodes) - 20
    w, h = max(n["x"] + n["width"] for n in nodes) - x0 + 20, max(n["y"] + n["height"] for n in nodes) - y0 + 20
    by = {n["id"]: n for n in nodes}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="system-ui,Segoe UI,Arial,sans-serif">',
           '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#9aa0a6"/></marker></defs>',
           f'<rect width="{w}" height="{h}" fill="#14161a"/>']
    for n in nodes:
        if n["type"] == "group":
            c = GROUP[n["color"]]
            out.append(f'<rect x="{n["x"] - x0}" y="{n["y"] - y0}" width="{n["width"]}" height="{n["height"]}" rx="14" fill="{c}" fill-opacity=".10" stroke="{c}" stroke-opacity=".6"/>'
                       f'<text x="{n["x"] - x0 + 16}" y="{n["y"] - y0 + 24}" fill="{c}" font-size="15" font-weight="600">{html.escape(n["label"])}</text>')

    def mid(n, side):
        x, y, nw, nh = n["x"] - x0, n["y"] - y0, n["width"], n["height"]
        return {"left": (x, y + nh / 2), "right": (x + nw, y + nh / 2), "top": (x + nw / 2, y), "bottom": (x + nw / 2, y + nh)}[side]
    for e in data["edges"]:
        a, b = by[e["fromNode"]], by[e["toNode"]]
        (x1, y1), (x2, y2) = mid(a, e["fromSide"]), mid(b, e["toSide"])
        hub = e["id"].startswith("e-")
        end = ' marker-end="url(#a)"' if hub and e.get("toEnd") != "none" else ""
        start = ' marker-start="url(#a)"' if hub and e.get("fromEnd") == "arrow" else ""
        out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#9aa0a6" stroke-opacity="{1 if hub else .35}" stroke-width="{1.6 if hub else 1}"{end}{start}/>')
        if hub and e.get("label"):
            tx, ty = (x1 + x2) / 2, (y1 + y2) / 2
            out.append(f'<rect x="{tx - 4 * len(e["label"]) - 6}" y="{ty - 11}" width="{8 * len(e["label"]) + 12}" height="20" rx="5" fill="#14161a" stroke="#3a3f47"/>'
                       f'<text x="{tx}" y="{ty + 4}" fill="#d7dae0" font-size="12" text-anchor="middle">{html.escape(e["label"])}</text>')
    for n in nodes:
        if n["type"] == "file":
            hub = n["id"].startswith("h-")
            name = Path(n["file"]).stem
            out.append(f'<rect x="{n["x"] - x0}" y="{n["y"] - y0}" width="{n["width"]}" height="{n["height"]}" rx="8" fill="{"#23272e" if hub else "#1b1e24"}" stroke="#3a3f47"/>'
                       f'<text x="{n["x"] - x0 + 14}" y="{n["y"] - y0 + n["height"] / 2 + 5}" fill="{"#f0f1f4" if hub else "#aeb3bb"}" font-size="{15 if hub else 13}">{html.escape(name)}</text>')
    out.append("</svg>")
    return "\n".join(out)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        mp, data = build(Path(tmp))
        bad = vaultmap.check_canvas(data)
        if bad:
            sys.exit("the sample canvas is not valid: " + "; ".join(bad))
        target = ROOT / "docs" / "img" / "vault-map.svg"
        target.write_text(svg(data), encoding="utf-8")
        print("wrote", target, f"({len(mp['hubs'])} hubs, {len(mp['pairs'])} relations)")
