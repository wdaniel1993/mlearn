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
    head = spec.strip().splitlines()[0] if spec.strip() else ""
    if not re.match(r"^infographic\s+[a-zA-Z0-9][a-zA-Z0-9-]*\s*$", head):
        return False, "spec must start with an 'infographic <template>' line"
    if not DATA_RE.search(spec):
        return False, "spec must contain a 'data' section"
    if spec.startswith("<svg") or "<svg " in spec[:80]:
        return False, "looks like raw SVG, not a spec"
    return True, ""


def salvage_spec(text: str) -> str | None:
    """Rescue a spec wrapped in prose/fences (the model's most common
    failure): take the substring from the first 'infographic <template>'
    line up to the closing ``` fence (or end of text), then re-validate.
    Returns the salvaged spec or None."""
    m = re.search(r"^\s*infographic\s+[a-zA-Z0-9][a-zA-Z0-9-]*\s*$", text, re.M)
    if not m:
        return None
    start = m.start()
    end = text.find("```", m.end())
    if end == -1:
        end = len(text)
    cand = text[start:end].strip()
    ok, _ = spec_valid(cand)
    if not ok:
        return None
    return cand


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
    if proc.returncode != 0 and "```" in spec:
        # prose/fence-wrapped specs that slipped past the gates: salvage the
        # spec block and re-render once before giving up
        saved = salvage_spec(spec)
        if saved is not None and saved != spec:
            try:
                proc = subprocess.run(
                    ["node", str(script)], input=saved, capture_output=True,
                    timeout=60, check=False, text=True, encoding="utf-8")
                spec = saved
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
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


def _check_liveliness(spec: str, svg: str) -> str | None:
    """The 'alive' gate: rendered banners must show a palette and >=2
    distinct accent hue families (a single-hue gradient + white text reads
    as a placeholder; templates render icons differently — some as <use>,
    most as custom shapes — so hue diversity is the reliable signal). Also
    enforces count honesty: if the title announces a small number of items
    (e.g. 'TRL 1-9', '7 layers'), the banner must show that many —
    pre-truncating content to fit a template is a silent drop."""
    first = spec.strip().splitlines()[0]
    fam = first.split()[1] if len(first.split()) > 1 else ""
    if not re.search(r"^\s*palette\s+", spec, re.M):
        return ("banner lacks a palette: add 'theme' 'palette' with 2-5 "
                "BRIGHT colors (single-color banners read as placeholders), "
                "e.g. #22d3ee #22c55e #f59e0b #f97316")
    if not fam.startswith("chart-"):
        hues = _accent_hue_families(svg)
        if hues < 2:
            return (f"render shows only {hues} accent hue famil(y/ies) — the "
                    "banner is a single-color text layout. Pick a template "
                    "with real color blocks (quadrant, funnel, waterfall, "
                    "column chart, hierarchy, grid) and keep 3-5 palette "
                    "colors")
    anno = _announced_count(spec)
    if anno:
        shown = _item_count(spec)
        if shown and shown < anno:
            return (f"title announces {anno} items but the banner has only "
                    f"{shown}: NEVER drop items to fit a template. Switch to "
                    "a template that scales (list-column-done-list / "
                    "list-grid-badge-card fit 8+, chart-* fit 8+)")
    return None


def _accent_hue_families(svg: str) -> int:
    """Cluster accent colors (fills + gradient stops) by hue. Requires
    saturation >= 0.30 and value >= 0.55 so white/gray/dark don't count;
    hues within 25 degrees merge into one family."""
    import math
    hexes = set(re.findall(r'(?:fill|stop-color)="#([0-9a-fA-F]{6})"', svg))
    hues = []
    for h in hexes:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        mx, mn = max(r, g, b), min(r, g, b)
        d = mx - mn
        sat = 0 if mx == 0 else d / mx
        if sat < 0.30 or mx < 0.55:
            continue
        if d == 0:
            hu = 0.0
        elif mx == r:
            hu = ((g - b) / d) % 6
        elif mx == g:
            hu = (b - r) / d + 2
        else:
            hu = (r - g) / d + 4
        hues.append(hu * 60)
    if not hues:
        return 0
    hues.sort()
    fams = 1
    for i in range(1, len(hues)):
        if abs(hues[i] - hues[i - 1]) > 25:
            fams += 1
    return fams


def _announced_count(spec: str) -> int | None:
    """Small-number announcements in the title: 'TRL 1-9', '7 layers',
    '3 components'. Returns the announced count or None."""
    m = re.search(r"^  title\s+(.+?)\s*$", spec, re.M)
    if not m:
        return None
    title = m.group(1)
    rng = re.search(r"(?:\b|^)(\d{1,2})\s*[-–—]\s*(\d{1,2})(?:\b|$)", title)
    nouns = re.search(
        r"(\d{1,2})\s*(?:layers|steps|levels|stages|components|items|rules|"
        r"parts|phases|pillars|habits|principles|trls?)\b", title, re.I)
    if rng and int(rng.group(2)) <= 12:
        return int(rng.group(2))
    if nouns:
        return int(nouns.group(1))
    return None


def _item_count(spec: str) -> int | None:
    for blk in ("lists", "sequences", "values", "compares", "items"):
        m = re.search(rf"^\s*{blk}\s*$", spec, re.M)
        if m:
            return len(re.findall(r"^\s+- label\s+", spec[m.end():], re.M))
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