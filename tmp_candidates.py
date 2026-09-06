"""Try candidate infographic specs for the Long tail card; report gates."""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/danielwallner/dev/mlearn")
from mlearn.infographic import render_spec
from mlearn import visualqa as vqa

TOOLS = Path("/Users/danielwallner/dev/mlearn/tools")

CANDIDATES = {
"relation-flow": """infographic relation-dagre-flow-tb-simple-circle-node
data
  title How the long tail pays off
  nodes
    - label Cheap storage
      id cheap
      desc Store and ship rare items for near zero cost
      icon banknote
    - label Stock every niche
      id stock
      desc Central warehouses and digital catalogs list obscure titles
      icon book
    - label Fans find them
      id find
      desc Search and recommendations surface the rare products
      icon users
    - label Tail rivals hits
      id tail
      desc Many small niche sales add up to a blockbuster total
      icon chart line
  relations
    cheap - lets you stock -> stock
    stock - then -> find
    find - adds up to -> tail
theme
  palette #22d3ee #22c55e #f59e0b #eab308
""",
"funnel": """infographic sequence-funnel-simple
data
  title How the long tail pays off
  sequences
    - label Cost drops to zero
      icon banknote
      desc Warehouses and digital catalogs make rare items viable
    - label Sell every niche
      icon book
      desc List obscure books and films, not just proven hits
    - label Fans find them
      icon users
      desc Search and recommendations connect buyers to niches
    - label Tail rivals head
      icon chart line
      desc Small sales add up to blockbuster-size revenue
theme
  palette #22d3ee #22c55e #f59e0b #eab308
""",
"waterfall": """infographic list-waterfall-badge-card
data
  title How the long tail adds up
  lists
    - label Storage near zero
      value 1
      desc Central warehouses make rare items cheap to stock
      icon banknote
    - label Stock every niche
      value 2
      desc Catalogs list obscure books and films, not just hits
      icon book
    - label Fans find them
      value 3
      desc Search and recommendations surface rare products
      icon users
    - label Tail rivals hits
      value 4
      desc Small niche sales add up to a blockbuster total
      icon chart line
  desc Small steps, one big result
theme
  palette #22d3ee #22c55e #f59e0b #eab308
""",
"hierarchy": """infographic hierarchy-tree-tech-style-badge-card
data
  title What makes the long tail work
  root
    - label Long tail
      icon chart line
      children
        - label Cheap supply
          desc Near-zero storage and delivery costs
          icon banknote
        - label Discovery tools
          desc Search and recommendations find niches
          icon users
        - label Aggregation
          desc Small sales add up into one total
          icon database
theme
  palette #22d3ee #22c55e #f59e0b #eab308
""",
}

for name, spec in CANDIDATES.items():
    print("=" * 20, name)
    svg, err = render_spec(spec, TOOLS)
    if svg is None:
        print("  RENDER FAILED:", err)
        continue
    Path(f"/tmp/{name}.svg").write_text(svg)
    print("  render OK (%d bytes)" % len(svg))
    ok, verr = vqa.qa_banner_svg(svg)
    print("  qa_banner_svg:", ok, verr)