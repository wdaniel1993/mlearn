"""Visual QA gates for card diagrams (both the AntV banner and every inline
mermaid fence). These run IN the pipeline after rendering and reject cards
whose visuals would confuse a reader — the classes Daniel keeps catching in
screenshots:

- banners that render bare numbers without any word labels
- mermaid state diagrams used as causal chains (the 'planning fallacy' loop
  that read as 'delays cause the next task')
- mermaid fences that don't render to a real diagram at all (parse-valid but
  visually empty)

All checks are fast (pure text analysis + one node subprocess per fence) and
thread-safe (node is spawned per call), so they fit inside the parallel
generation workers.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Banner (AntV infographic SVG) checks
# ---------------------------------------------------------------------------

_NUMERIC_RUN_RE = re.compile(r"^[\d.,%\- ]+$")
_WORD_RUN_RE = re.compile(r"[A-Za-zÄÖÜäöü]{3,}")


def _text_runs(svg: str) -> list[str]:
    runs = re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S)
    runs += re.findall(r"<span[^>]*>(.*?)</span>", svg, re.S)
    return [r.strip() for r in runs if r.strip()]


def qa_banner_svg(svg: str) -> tuple[bool, str]:
    """Structural + self-explanation checks on the rendered banner SVG."""
    if not svg or not svg.startswith("<svg"):
        return False, "banner svg missing or not an svg"
    words = [r for r in _text_runs(svg) if _WORD_RUN_RE.search(r)]
    numerics = [r for r in _text_runs(svg) if _NUMERIC_RUN_RE.match(r)]
    if len(words) < 2:
        detail = "no word labels" if not words else f"only {len(words)} word run(s)"
        return False, (
            f"banner is not self-explanatory: {detail} "
            f"({len(numerics)} bare number text(s)) — every data item needs a "
            "label with letters, numbers alone read as gibberish"
        )
    if len(svg) < 250:
        return False, f"banner suspiciously small ({len(svg)} chars)"
    ids = set(re.findall(r'\bid="([^"]+)"', svg))
    uses = re.findall(r'<use\b[^>]*\b(?:href|xlink:href)="#([^"]+)"', svg)
    dangling = [u for u in uses if u not in ids]
    if dangling:
        return False, (
            f"banner has {len(dangling)} dangling icon refs (no matching "
            f"symbol/def: {', '.join(dangling[:3])}) — these render as BLANK "
            "icon slots; icons must resolve to real defs"
        )
    return True, ""


# ---------------------------------------------------------------------------
# Mermaid fence checks
# ---------------------------------------------------------------------------

_STATE_DIAGRAM_RE = re.compile(r"^\s*stateDiagram\b", re.M)


def qa_mermaid(src: str, tools_dir: str | Path) -> tuple[bool, str]:
    """Semantic + render check for an inline mermaid fence."""
    if _STATE_DIAGRAM_RE.search(src):
        return False, (
            "stateDiagram-v2 is banned for causal chains: it renders as a loop "
            "that implies backwards causality (e.g. 'Real delays --> Next task' "
            "reads as 'delays cause the next task'). Use flowchart LR with "
            "labeled edges and a clear linear direction instead."
        )
    script = Path(tools_dir) / "render_mermaid.mjs"
    if not script.is_file():
        return False, f"mermaid renderer not found: {script}"
    try:
        proc = subprocess.run(
            ["node", str(script)], input=src, capture_output=True,
            timeout=30, check=False, text=True, encoding="utf-8")
    except FileNotFoundError:
        return False, "node not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "mermaid render timed out (30s)"
    if proc.returncode != 0:
        err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown"
        return False, f"mermaid render failed: {err[:160]}"
    svg = proc.stdout.strip()
    if len(svg) < 2000 or not svg.startswith("<svg"):
        return False, "mermaid rendered nothing usable (empty output)"
    # rendered diagram must actually contain structure: connectors + text
    # (v11 renders labels as foreignObject/span HTML — count those too)
    has_connectors = svg.count("<path") >= 1 or svg.count("<polyline") >= 1
    has_text = (svg.count("<text") >= 2
                or svg.count("<foreignObject") >= 2
                or svg.count("<span") >= 2)
    if not (has_connectors and has_text):
        return False, "mermaid diagram visually empty (no connectors/text)"
    return True, ""