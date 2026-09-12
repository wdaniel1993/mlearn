"""B&W (e-ink) infographic variant — derivation, ID namespacing, gates, backfill.

The mono variant is a pure deterministic function of the color SVG: same
geometry and typography, colors remapped to inverted max-channel grayscale
(vivid accents -> dark ink, readable on light pages). Owner's spec: the
background stays transparent; content must be black/greyish for e-ink.

Two transforms beyond the color map:
- low gradient-stop opacities are raised (faded dark-theme inks would read
  too faint on light pages);
- monochrome gradients (all stops one ink) are flattened to solid refs —
  crisper on e-ink, removes wash-out fade tails.

ID namespacing (`namespace_ids`): every internal id + `url(#…)` / `href="#…"`
reference is prefixed per card+variant (`ml<sha8><c|b>-`), so any number of
SVGs — multiple cards or both variants — can share one document without
cross-contamination (a document-wide `url(#id)` resolves to the FIRST
definition; duplicate ids across cards silently repaint one card with
another card's palette).

All attribute regexes accept single- AND double-quoted attributes (AntV
emits double quotes; hand-written fallback SVGs often use single).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3

from . import validate as validate_mod
from .validate import MAX_INF_CHARS

_GRAY_BOOST = 1.30
_NS_ATTR = "data-mlearn-ns"
_Q = r"""["']"""  # either quote style


def _map_gray(r: int, g: int, b: int) -> int:
    v0 = max(r, g, b)  # max-channel: vivid accents -> dark ink
    v = 255 - v0
    v = 128 + (v - 128) * _GRAY_BOOST
    return max(0, min(255, round(v)))


def _hex2rgb(h: str) -> tuple[int, int, int, str | None]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = h[6:8] if len(h) == 8 else None
    return r, g, b, a


def derive_bw(svg: str) -> str:
    """Color SVG -> mono (e-ink) SVG. Pure byte transform."""
    def rep_hex(m: re.Match) -> str:
        r, g, b, a = _hex2rgb(m.group(0))
        v = _map_gray(r, g, b)
        return f"#{v:02x}{v:02x}{v:02x}" + (a.lower() if a else "")

    svg = re.sub(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b", rep_hex, svg)
    svg = re.sub(r"#[0-9a-fA-F]{3}\b", rep_hex, svg)

    def rep_rgb(m: re.Match) -> str:
        parts = [p.strip() for p in m.group(1).split(",")]
        r, g, b = (int(float(parts[i])) for i in range(3))
        v = _map_gray(r, g, b)
        if len(parts) > 3:
            return f"rgba({v},{v},{v},{parts[3]})"
        return f"rgb({v},{v},{v})"

    svg = re.sub(r"rgb\(([^)]+)\)", rep_rgb, svg)
    svg = re.sub(r"rgba\(([^)]+)\)", rep_rgb, svg)

    def boost_stop(m: re.Match) -> str:
        op = float(m.group(2))
        if 0.0 < op < 0.55:
            op = min(1.0, round(op * 1.9, 3))
        return f"stop-opacity={m.group(1)}{op}{m.group(1)}"

    svg = re.sub(rf"stop-opacity=({_Q})([0-9.]+)\1", boost_stop, svg)

    for tag in ("linearGradient", "radialGradient"):
        for _q, gid, block in re.findall(
                rf"<{tag} id=({_Q})([^\"']+)\1[^>]*>(.*?)</{tag}>", svg, re.S):
            cols = re.findall(rf"stop-color=({_Q})([^\"']+)\1", block)
            cols = [c for _q, c in cols]
            if cols and len(set(c.lower() for c in cols)) == 1:
                svg = svg.replace(f"url(#{gid})", cols[0])
    return svg


def namespace_ids(svg: str, suffix: str, base: str | None = None) -> tuple[str, str | None]:
    """Prefix all internal ids/refs so multiple SVGs can share a document.

    Idempotent: the root carries `data-mlearn-ns` once rewritten. Returns
    (svg, prefix) — prefix is None when already namespaced or when a rewrite
    would break references (input returned unchanged in that case)."""
    if _NS_ATTR in svg:
        return svg, None
    base = base or hashlib.sha1(svg.encode("utf-8")).hexdigest()[:8]
    prefix = f"ml{base}{suffix}-"
    out = svg
    ids = {m[1] for m in re.findall(rf"id=({_Q})([^\"']+)\1", out)}
    for i in sorted(ids, key=len, reverse=True):
        esc = re.escape(i)
        out = re.sub(rf"id=({_Q}){esc}\1",
                     lambda m: f"id={m.group(1)}{prefix}{i}{m.group(1)}", out)
        out = re.sub(rf"url\(#{esc}\)", f"url(#{prefix}{i})", out)
        out = re.sub(rf"href=({_Q})#{esc}\1",
                     lambda m: f"href={m.group(1)}#{prefix}{i}{m.group(1)}", out)
    defined = {m[1] for m in re.findall(rf"id=({_Q})([^\"']+)\1", out)}
    refs = (set(re.findall(r"url\(#([^)]+)\)", out))
            | {m[1] for m in re.findall(rf"href=({_Q})#([^\"']+)\1", out)})
    if not refs <= defined:  # would dangle -> keep the original untouched
        return svg, None
    if "<svg " in out:
        out = out.replace("<svg ", f'<svg {_NS_ATTR}="{prefix}" ', 1)
    else:
        out = out.replace("<svg", f'<svg {_NS_ATTR}="{prefix}"', 1)
    return out, prefix


def qa_mono_svg(svg: str) -> tuple[bool, str]:
    """Gate for the derived variant: self-contained SVG (reuses the standard
    infographic checks, non-strict layout) AND fully desaturated."""
    ok, err = validate_mod.infographic_valid(svg, strict_layout=False)
    if not ok:
        return False, err
    if len(svg) > MAX_INF_CHARS:
        return False, f"too large: {len(svg)} chars > {MAX_INF_CHARS}"
    for m in re.finditer(r"#([0-9a-fA-F]{3,8})\b", svg):
        h = m.group(1)
        if len(h) in (3, 4):
            if not (h[0].lower() == h[1].lower() == h[2].lower()):
                return False, f"saturated token left: #{h}"
        elif len(h) in (6, 8):
            if not (h[0:2].lower() == h[2:4].lower() == h[4:6].lower()):
                return False, f"saturated token left: #{h}"
    for m in re.finditer(r"rgba?\(([^)]+)\)", svg):
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(set(parts[:3])) > 1:
            return False, f"saturated token left: rgb({m.group(1)})"
    if re.search(r"\bhsla?\(", svg):
        return False, "hsl colors not supported by the mono derive"
    return True, ""


def prepare_variants(svg: str | None) -> tuple[str | None, str | None, str | None]:
    """(color_namespaced, bw_namespaced, warn) for storage.

    Mono is best-effort: on any failure it is dropped with a warning and the
    card ships color-only (never block generation over the optional variant).
    """
    if not svg or not svg.strip():
        return None, None, None
    color, _ = namespace_ids(svg, "c")
    bw, _ = namespace_ids(derive_bw(svg), "b")
    ok, err = qa_mono_svg(bw)
    if not ok:
        return color, None, f"mono gate: {err}"
    return color, bw, None


def backfill_bw(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Derive + namespace variants for every stored infographic. Idempotent:
    rows whose color SVG is namespaced and that already have a mono variant
    are skipped."""
    rows = conn.execute(
        "SELECT id, infographic_svg, infographic_svg_bw FROM cards "
        "WHERE infographic_svg IS NOT NULL AND infographic_svg != ''"
    ).fetchall()
    stats = {"scanned": len(rows), "updated": 0, "skipped": 0,
             "bw_failed": 0, "ns_skipped": 0, "warnings": []}
    for row in rows:
        has_ns = _NS_ATTR in row["infographic_svg"]
        if has_ns and row["infographic_svg_bw"]:
            stats["skipped"] += 1
            continue
        color, bw, warn = prepare_variants(row["infographic_svg"])
        if not has_ns and _NS_ATTR not in (color or ""):
            stats["ns_skipped"] += 1  # rewrite would dangle; keep original
        if warn:
            stats["bw_failed"] += 1
            stats["warnings"].append(f"card {row['id']}: {warn}")
        if dry_run:
            stats["updated"] += 1
            continue
        conn.execute(
            "UPDATE cards SET infographic_svg = ?, infographic_svg_bw = ? WHERE id = ?",
            (color if color != row["infographic_svg"] else row["infographic_svg"],
             bw, row["id"]),
        )
        stats["updated"] += 1
    if not dry_run:
        conn.commit()
    return stats
