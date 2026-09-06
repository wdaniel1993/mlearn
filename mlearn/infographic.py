"""AntV-infographic lane: render a declarative infographic spec to SVG.

The generation model may emit `infographic_spec` (AntV DSL, see
tools/render_infographic.mjs + the AntV docs) instead of hand-writing raw
SVG. Layout faults (white space, overflow) become the engine's problem;
we only gate the rendered result like any other infographic.
"""

import re
import subprocess
from pathlib import Path

SPEC_RE = re.compile(r"^\s*infographic\s+[a-zA-Z0-9][a-zA-Z0-9-]*\s*$", re.M)
DATA_RE = re.compile(r"^\s*data\s*$", re.M)
MIN_SPEC_CHARS = 40
MAX_SPEC_CHARS = 4000


def spec_valid(spec: str) -> tuple[bool, str]:
    """Cheap syntax gate before spending a node render (the engine reports
    template/data errors itself; this only rejects non-specs)."""
    if not spec or not spec.strip():
        return False, "empty spec"
    if len(spec) > MAX_SPEC_CHARS:
        return False, f"spec too long: {len(spec)} chars > {MAX_SPEC_CHARS}"
    if not SPEC_RE.search(spec):
        return False, "spec must start with an 'infographic <template>' line"
    if not DATA_RE.search(spec):
        return False, "spec must contain a 'data' section"
    if spec.startswith("<svg") or "<svg " in spec[:80]:
        return False, "looks like raw SVG, not a spec"
    return True, ""


def render_spec(spec: str, tools_dir: str | Path) -> tuple[str | None, str]:
    """Render an infographic spec to SVG via node (tools/render_infographic.mjs).

    Returns (svg, "") on success, (None, reason) on failure."""
    ok, err = spec_valid(spec)
    if not ok:
        return None, err
    script = Path(tools_dir) / "render_infographic.mjs"
    if not script.is_file():
        return None, f"infographic renderer not found: {script} (run: cd tools && npm i @antv/infographic)"
    try:
        proc = subprocess.run(
            ["node", str(script)], input=spec, capture_output=True,
            timeout=60, check=False, text=True, encoding="utf-8",
        )
    except FileNotFoundError:
        return None, "node not found on PATH"
    except subprocess.TimeoutExpired:
        return None, "infographic render timed out"
    if proc.returncode != 0:
        return None, f"render failed: {proc.stderr.strip()[:200]}"
    svg = proc.stdout.strip()
    if not svg.startswith("<svg"):
        return None, "renderer produced no svg"
    trunc = _check_truncation(spec, svg)
    if trunc:
        return None, trunc
    return svg, ""


def _check_truncation(spec: str, svg: str) -> str | None:
    """Detect silent item drops: some templates cap their item count
    geometrically (e.g. list-pyramid-* renders at most 6) and slice the rest.
    Every label declared under `lists` must appear in the rendered SVG —
    otherwise the renderer truncated and the retry loop must switch templates."""
    m = re.search(r"^\s*lists\s*$", spec, re.M)
    if not m:
        return None
    labels = re.findall(r"^\s+- label\s+(.+?)\s*$", spec[m.end():], re.M)
    if len(labels) < 3:
        return None
    missing = [lbl for lbl in labels if lbl.strip() not in svg]
    if missing:
        return (f"renderer dropped items: {len(labels) - len(missing)} of "
                f"{len(labels)} rendered (missing: {', '.join(missing[:3])}) — "
                f"this template caps its item count; use list-column-done-list "
                f"or list-grid-badge-card for {len(labels)} items")
    return None