"""Regenerates docs/hero.svg and docs/demo.svg. Run: python docs/make_assets.py"""
from __future__ import annotations

import html
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT.parent / "src/dungscraper/__init__.py").read_text()).group(1)

BONE = (
    '<g transform="translate({x},{y}) rotate(-35) scale({s})" fill="url(#g)">'
    '<rect x="-70" y="-9" width="140" height="18" rx="9"/>'
    '<circle cx="-72" cy="-13" r="15"/><circle cx="-72" cy="13" r="15"/>'
    '<circle cx="72" cy="-13" r="15"/><circle cx="72" cy="13" r="15"/></g>'
)


def hero() -> str:
    nodes = [(930, 90), (1010, 170), (1090, 80), (1110, 200), (990, 280), (880, 230), (1060, 320), (800, 120)]
    edges = [(0, 1), (1, 2), (1, 3), (1, 4), (4, 5), (5, 0), (4, 6), (0, 7), (7, 5), (3, 6)]
    lines = "".join(
        f'<line x1="{nodes[a][0]}" y1="{nodes[a][1]}" x2="{nodes[b][0]}" y2="{nodes[b][1]}" stroke="#2dd4bf" stroke-opacity=".35" stroke-width="2"/>'
        for a, b in edges
    )
    dots = "".join(
        f'<circle cx="{x}" cy="{y}" r="{7 if i % 3 == 0 else 5}" fill="#2dd4bf"><animate attributeName="opacity" values="1;.35;1" dur="{2 + i % 3}s" repeatCount="indefinite"/></circle>'
        for i, (x, y) in enumerate(nodes)
    )
    chips = [("v" + VERSION, "#2dd4bf"), ("MCP ready", "#818cf8"), ("MIT", "#f59e0b"), ("no API key", "#f472b6")]
    chip_svg, x = "", 70
    for text, color in chips:
        w = 14 * len(text) + 34
        chip_svg += (
            f'<rect x="{x}" y="330" width="{w}" height="38" rx="19" fill="{color}" fill-opacity=".14" stroke="{color}"/>'
            f'<text x="{x + w / 2}" y="355" text-anchor="middle" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="18" font-weight="600" fill="{color}">{html.escape(text)}</text>'
        )
        x += w + 14
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="420" viewBox="0 0 1200 420" role="img" aria-label="DungScraper {VERSION}">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0b1020"/><stop offset="1" stop-color="#151b3a"/></linearGradient>
<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2dd4bf"/><stop offset="1" stop-color="#818cf8"/></linearGradient>
</defs>
<rect width="1200" height="420" rx="24" fill="url(#bg)"/>
{lines}{dots}
{BONE.format(x=140, y=80, s=0.7)}
<text x="70" y="215" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="78" font-weight="800" fill="#f8fafc">Dung<tspan fill="url(#g)">Scraper</tspan></text>
<text x="72" y="262" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="27" fill="#cbd5e1">The web, readable by your AI agents.</text>
<text x="72" y="300" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="20" fill="#94a3b8">Local. Free. One command. Pages in, clean Markdown and JSON out.</text>
{chip_svg}
</svg>
'''


DEMO = [
    ("cmd", "pip install git+https://github.com/dakisxxx-oss/DungScraper"),
    ("out", "Successfully installed dungscraper-" + VERSION),
    ("gap", ""),
    ("cmd", 'dungscraper research "How does BM25 ranking work?" --max-pages 5'),
    ("hd", "# Research: How does BM25 ranking work?"),
    ("out", "10 passages from 5 pages, most relevant first. Numbers in brackets are sources."),
    ("out", "1. **How Does BM25 Work?**: BM25 works by calculating a relevance score for each document… [2]"),
    ("out", "[2] How Does BM25 Ranking Algorithm Work? - https://blog.dailydoseofds.com/p/how-does-bm25…"),
    ("dim", "[5 pages, 3371 ms, stopped by page_budget]"),
    ("gap", ""),
    ("cmd", 'dungscraper crawl https://www.python.org --local --strategy best_first --keywords download,windows'),
    ("out", '{ "strategy": "best_first", "pages": [ { "crawl_score": 0.4267, "url": ".../python-3152/" }, …'),
    ("gap", ""),
    ("dim", "# what your agent sees in Claude, Cursor or any MCP client:"),
    ("ban", f"🦴 DungScraper v{VERSION} · read_focused · api · 574 ms"),
]


def demo() -> str:
    line_h, top = 30, 78
    height = top + line_h * len(DEMO) + 30
    colors = {"cmd": "#e2e8f0", "out": "#94a3b8", "dim": "#64748b", "hd": "#2dd4bf", "ban": "#fbbf24"}
    rows, delay = "", 0.4
    for i, (kind, text) in enumerate(DEMO):
        if kind == "gap":
            continue
        y = top + i * line_h
        prompt = '<tspan fill="#2dd4bf">$ </tspan>' if kind == "cmd" else ""
        weight = ' font-weight="700"' if kind in {"ban", "hd"} else ""
        rows += (
            f'<text x="34" y="{y}" class="l" style="animation-delay:{delay:.2f}s" fill="{colors[kind]}"{weight}>{prompt}{html.escape(text)}</text>'
        )
        delay += 0.55 if kind == "cmd" else 0.28
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-label="DungScraper terminal demo">
<style>
.l{{font:17px Consolas,'DejaVu Sans Mono',Menlo,monospace;opacity:0;animation:show .01s forwards}}
@keyframes show{{to{{opacity:1}}}}
</style>
<rect width="1200" height="{height}" rx="16" fill="#0d1117" stroke="#30363d"/>
<rect width="1200" height="44" rx="16" fill="#161b22"/><rect y="28" width="1200" height="16" fill="#161b22"/>
<circle cx="28" cy="22" r="7" fill="#ff5f56"/><circle cx="52" cy="22" r="7" fill="#ffbd2e"/><circle cx="76" cy="22" r="7" fill="#27c93f"/>
<text x="600" y="27" text-anchor="middle" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="14" fill="#8b949e">dungscraper {VERSION}</text>
{rows}
</svg>
'''


if __name__ == "__main__":
    (ROOT / "hero.svg").write_text(hero(), encoding="utf-8")
    (ROOT / "demo.svg").write_text(demo(), encoding="utf-8")
    print("wrote hero.svg and demo.svg for", VERSION)
