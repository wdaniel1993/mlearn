"""Render hierarchy-tree and arrow-row candidates; verify labels are VISIBLE text."""
import re, sys
from pathlib import Path

sys.path.insert(0, "/Users/danielwallner/dev/mlearn")
from mlearn.infographic import render_spec
from mlearn import visualqa as vqa

TOOLS = Path("/Users/danielwallner/dev/mlearn/tools")

CANDIDATES = {
"tree": """infographic hierarchy-tree-tech-style-badge-card
data
  title What makes the long tail work
  root
    label Long tail
    icon chart line
    desc Niche products, tiny sales each, huge total together
    children
      - label Cheap supply
        desc Near-zero storage and delivery costs
        icon banknote
      - label Discovery tools
        desc Search and recommendations find rare items
        icon users
      - label Aggregation
        desc Millions of small sales add up to one total
        icon database
theme
  palette #22d3ee #22c55e #f59e0b #eab308
""",
"arrowrow": """infographic list-row-horizontal-icon-arrow
data
  title How the long tail pays off
  lists
    - label Storage near zero
      desc Warehouses and digital catalogs make niche items viable
      icon banknote
    - label Stock every niche
      desc List rare books and films, not just proven hits
      icon book
    - label Fans find them
      desc Search and recommendations surface obscure products
      icon users
    - label Tail rivals hits
      desc Small sales add up to blockbuster-size revenue
      icon chart line
theme
  palette #22d3ee #22c55e #f59e0b #eab308
""",
}

def visible_text_runs(svg: str) -> list[str]:
    """Text that actually renders visibly (exclude <title> tooltips)."""
    svg_no_titles = re.sub(r"<title>.*?</title>", "", svg, flags=re.S)
    runs = re.findall(r"<(?:text|span|tspan)[^>]*>(.*?)</(?:text|span|tspan)>", svg_no_titles, re.S)
    return [re.sub(r"<[^>]+>", "", r).strip() for r in runs if re.sub(r"<[^>]+>", "", r).strip()]

for name, spec in CANDIDATES.items():
    print("=" * 25, name)
    svg, err = render_spec(spec, TOOLS)
    if svg is None:
        print("  RENDER FAILED:", err)
        continue
    Path(f"/tmp/{name}.svg").write_text(svg)
    print("  render OK (%d bytes)" % len(svg))
    ok, verr = vqa.qa_banner_svg(svg)
    print("  qa_banner_svg:", ok, verr)
    print("  visible runs:", visible_text_runs(svg))