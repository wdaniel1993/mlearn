"""AntV-infographic lane: render a declarative infographic spec to SVG.

The generation model may emit `infographic_spec` (AntV DSL, see
tools/render_infographic.mjs + the AntV docs) instead of hand-writing raw
SVG. Layout faults (white space, overflow) become the engine's problem;
we only gate the rendered result like any other infographic.
"""

import re
import subprocess
import time
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
        log_failure(spec, "renderer produced no svg")
        return None, "renderer produced no svg"
    trunc = _check_truncation(spec, svg)
    if trunc:
        return None, trunc
    dark = _check_palette(spec)
    if dark:
        return None, dark
    alive = _check_liveliness(spec, svg)
    if alive:
        return None, alive
    return svg, ""


_ACCENT_GRAY = {"#ffffff", "#000000", "#1f1f1f", "#0f1216", "#101216"}


def _check_liveliness(spec: str, svg: str) -> str | None:
    """The 'alive' gate: banners must carry icons and a palette, and the
    RENDER must show at least two accent colors beyond white/gray. A spec
    that passes render but shows only white text boxes reads as a
    placeholder — fail it and the retry loop feeds the fix back."""
    first = spec.strip().splitlines()[0]
    fam = first.split()[1] if len(first.split()) > 1 else ""
    needs_icons = fam.startswith(("list-", "sequence-", "compare-", "hierarchy-"))
    if needs_icons and len(re.findall(r"^\s+icon\s+\S", spec, re.M)) < 2:
        return ("banner lacks icons: semantic keyword items (lists/sequences/"
                "compares/hierarchy) must carry 'icon <keywords>' on at least"
                " 2 items — e.g. icon rocket launch, icon shield check")
    if not re.search(r"^\s*palette\s+", spec, re.M):
        return ("banner lacks a palette: add 'theme' 'palette' with 2-5 "
                "BRIGHT colors (single-color banners read as placeholders), "
                "e.g. #22d3ee #22c55e #f59e0b #f97316")
    fills = set(re.findall(r'(?:fill|stop-color)="#([0-9a-fA-F]{6})"', svg))
    accents = {f for f in fills
               if f.lower() not in _ACCENT_GRAY
               and not (f[1:3] == f[3:5] == f[5:7])}
    if len(accents) < 2:
        return (f"render shows only {len(accents)} accent color(s) — the "
                "template renders the palette too thinly; pick a template "
                "with real color blocks (chart, quadrant, funnel, waterfall)"
                " or restyle with stylize linear-gradient")
    return None


def log_failure(spec: str, err: str) -> None:
    """Persist a failing spec so renderer crashes are reproducible instead
    of vanishing into the retry loop."""
    try:
        path = Path(__file__).resolve().parents[1] / "data" / "logs" / "render_failures.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} | {err[:300]}\n{spec}\n")
    except OSError:
        pass


def _check_palette(spec: str) -> str | None:
    """Palette colors sit on the engine's DARK background (#1F1F1F): a dark
    palette = unreadable banner. Every palette hex must be bright enough
    (WCAG relative luminance >= 0.25). Lightness is a property of the theme,
    not personal taste — enforce it."""
    m = re.search(r"^\s*palette\s+(.+?)\s*$", spec, re.M)
    if not m:
        return None
    bad = []
    for tok in m.group(1).split():
        hexs = re.findall(r"#([0-9a-fA-F]{6})", tok)
        if not hexs:
            continue
        h = hexs[0]
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
        if lum < 0.25:
            bad.append(f"#{h} (lum {lum:.2f})")
    if bad:
        return ("palette color(s) too dark for the fixed dark theme: "
                + ", ".join(bad[:3])
                + " — use BRIGHT colors (luminance >= 0.25), e.g. #22d3ee "
                "#22c55e #f59e0b #f97316 #eab308 #a78bfa")
    return None


def _check_truncation(spec: str, svg: str) -> str | None:
    """Detect silent item drops: some templates cap their item count
    geometrically and slice the rest. Depth-aware rules (verified against
    engine 0.x):
    - every CHILD label (a line indented under `children`) must render —
      losing content is always a failure;
    - at most ONE root-level label may be absent (compare-binary-* silently
      omits the second root's label by design while rendering both sides);
    - lists/sequences/values: every label must render (templates like
      list-pyramid-* cap at 6 and must switch to list-column/grid)."""
    def labels_under(start: int) -> list[tuple[int, str]]:
        return [(len(m.group(1)) // 2, m.group(2))
                for m in re.finditer(r"^(\s+)- label\s+(.+?)\s*$", spec[start:], re.M)]

    blocks = [("lists",), ("sequences",), ("values",), ("compares",),
              ("items",), ("root",), ("nodes",)]
    for b in blocks:
        m = re.search(rf"^\s*{b[0]}\s*$", spec, re.M)
        if not m:
            continue
        items = labels_under(m.end())
        if len(items) < 3:
            continue
        min_d = min(d for d, _ in items)
        root_items = [lbl for d, lbl in items if d == min_d]
        child_items = [lbl for d, lbl in items if d > min_d]
        missing_root = [lbl for lbl in root_items if lbl not in svg]
        missing_child = [lbl for lbl in child_items if lbl not in svg]
        if missing_child:
            return (f"renderer dropped content items: missing "
                    f"{', '.join(missing_child[:3])} — template loses nested"
                    f" data; use a template that fits the depth")
        if not missing_root:
            continue
        if b[0] == "compares" and len(missing_root) == 1:
            # compare-binary-* omits the second root's label by design
            # (verified: both sides' children still render)
            continue
        return (f"renderer dropped items: {len(root_items) - len(missing_root)}"
                f" of {len(root_items)} rendered (missing: "
                f"{', '.join(missing_root[:3])}) — this template caps its"
                f" item count; use list-column-done-list or"
                f" list-grid-badge-card for {len(root_items)} items")
    return None