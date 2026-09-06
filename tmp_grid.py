"""Test list-grid-badge-card for the Long tail card; check for numeric runs."""
import re, sys
from pathlib import Path

sys.path.insert(0, "/Users/danielwallner/dev/mlearn")
from mlearn.infographic import render_spec
from mlearn import visualqa as vqa

TOOLS = Path("/Users/danielwallner/dev/mlearn/tools")

GRID = """infographic list-grid-badge-card
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
"""

svg, err = render_spec(GRID, TOOLS)
if svg is None:
    print("RENDER FAILED:", err)
    sys.exit(0)
Path("/tmp/grid.svg").write_text(svg)
print("render OK (%d bytes)" % len(svg))
ok, verr = vqa.qa_banner_svg(svg)
print("qa_banner_svg:", ok, verr)
runs = re.findall(r"<(?:text|span|tspan)[^>]*>(.*?)</(?:text|span|tspan)>", re.sub(r"<title>.*?</title>", "", svg, flags=re.S), re.S)
runs = [re.sub(r"<[^>]+>", "", r).strip() for r in runs]
print("runs:", [r for r in runs if r])
num_runs = [r for r in runs if re.search(r"\d", r)]
print("numeric runs:", num_runs)