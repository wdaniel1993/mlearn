"""B&W (e-ink) infographic variant — derivation, ID namespacing, gates, backfill.

The mono variant is a pure deterministic function of the color SVG: same
geometry and typography, colors remapped for light pages / e-ink. Owner's
spec: the background stays transparent; content must be black/greyish.

Mapping (v2, role-based bands) — the dark theme pairs bright surfaces with
light ink or dark ink; naive inversion collapses those pairs (bright box +
white text would both go dark = invisible text). Instead every color token is
classified by ROLE and mapped into a band that preserves contrast:

- SURFACES (shape fills, background-color) -> light band, 200-250:
  accent boxes/bars become light greys so dark content reads on them.
- INKS (text colors, strokes, icon glyphs) -> dark band, 0-140:
  white text -> black, pale strokes -> dark, icons stay dark.

Role detection: symbol-defs spans and `fill=` on <use>/<text>/<tspan> are
ink; other shape `fill=` are surfaces; `stroke=`, `color:` styles are ink;
gradient refs flatten by usage (fill->surface tone, stroke->ink tone).

ID namespacing (`namespace_ids`): every internal id + `url(#…)` / `href="#…"`
reference is prefixed `ml<sha8><c|b>-`, so any number of SVGs — multiple
cards, or both variants — can share one document without cross-contamination
(a document-wide `url(#id)` resolves to the FIRST definition).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3

from . import validate as validate_mod
from .validate import MAX_INF_CHARS

_NS_ATTR = "data-mlearn-ns"
_Q = r"""["']"""  # either quote style


def _ink(m: int) -> int:
    v = min(m, 255 - m)
    return max(0, min(140, round(v * 1.1)))


def _surface(m: int) -> int:
    return 255 - round(55 * m / 255)


def _hex2rgb(h: str) -> tuple[int, int, int, str | None]:
    h = h.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = h[6:8] if len(h) == 8 else None
    return r, g, b, a


def derive_bw(svg: str) -> str:
    """Color SVG -> mono (e-ink) SVG. Pure byte transform."""
    sym_spans = [(m.start(), m.end())
                 for m in re.finditer(r"<symbol\b.*?</symbol>", svg, re.S)]

    def in_symbol(pos: int) -> bool:
        return any(a <= pos < b for a, b in sym_spans)

    # pass 1: flatten gradient refs with a context-driven band.
    # NOTE: emitted values are stashed behind placeholders — the token pass
    # must not re-map them (double mapping washes surfaces out).
    grad_first: dict[str, str] = {}
    for m in re.finditer(
            rf"<(linearGradient|radialGradient) id=({_Q})([^\"']+)\2[^>]*>(.*?)</\1>", svg, re.S):
        cols = re.findall(rf"stop-color=({_Q})([^\"']+)\1", m.group(4))
        if cols:
            grad_first[m.group(3)] = cols[0][1]

    flat_out: list[str] = []

    def flat(m: re.Match) -> str:
        attr, gid = m.group(1), m.group(3)
        col = grad_first.get(gid)
        if not col or not col.startswith("#"):
            return m.group(0)
        r, g, b, _a = _hex2rgb(col)
        mx = max(r, g, b)
        v = _surface(mx) if attr == "fill" else _ink(mx)
        flat_out.append(f"#{v:02x}{v:02x}{v:02x}")
        return f"{attr}=\"__MLFLAT{len(flat_out) - 1}__\""

    svg = re.sub(rf"(fill|stroke)=({_Q})url\(#([^)]+)\)\2", flat, svg)

    # raise low stop-opacities (kept for parity: matters for surviving
    # mixed gradients; flattened refs no longer depend on them)
    def boost_stop(m: re.Match) -> str:
        op = float(m.group(2))
        if 0.0 < op < 0.55:
            op = min(1.0, round(op * 1.9, 3))
        return f"stop-opacity={m.group(1)}{op}{m.group(1)}"

    svg = re.sub(rf"stop-opacity=({_Q})([0-9.]+)\1", boost_stop, svg)

    # pass 2: per-token role mapping (hex incl. alpha + rgb()/rgba())
    out: list[str] = []
    last = 0
    token_re = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]+\)")
    for m in token_re.finditer(svg):
        out.append(svg[last:m.start()])
        tok = m.group(0)
        p = m.start()
        if tok.startswith("#"):
            r, g, b, a = _hex2rgb(tok)
            alpha = a if a else ""
        else:
            parts = [x.strip() for x in tok[tok.index("(") + 1:tok.index(")")].split(",")]
            try:
                r, g, b = (int(float(parts[i])) for i in range(3))
            except ValueError:
                out.append(tok)  # unsupported color syntax — leave as-is
                last = m.end()
                continue
            alpha = ("," + parts[3]) if len(parts) > 3 else ""
        mx = max(r, g, b)
        if in_symbol(p):
            role = "ink"
        else:
            before = svg[max(0, p - 180):p]
            if "background-color:" in before[-30:]:
                role = "surface"
            elif 'stroke="' in before[-12:] or "stroke='" in before[-12:]:
                role = "ink"
            elif before.rstrip().endswith("color:"):
                role = "ink"
            elif 'fill="' in before[-8:] or "fill='" in before[-8:]:
                # fill on <use>/<text>/<tspan> -> ink; other shapes -> surface
                lt = before.rfind("<")
                tag = before[lt:lt + 8].lower().lstrip("<")
                role = ("ink" if (tag.startswith("use") or tag.startswith("text")
                                  or tag.startswith("tspan")) else "surface")
            else:
                role = "ink"
        v = _ink(mx) if role == "ink" else _surface(mx)
        if tok.startswith("#"):
            out.append(f"#{v:02x}{v:02x}{v:02x}{alpha}")
        elif alpha:
            out.append(f"rgba({v},{v},{v}{alpha})")
        else:
            out.append(f"rgb({v},{v},{v})")
        last = m.end()
    out.append(svg[last:])
    result = "".join(out)

    # resolve pass-1 placeholders (mapped exactly once)
    def unflatten(m: re.Match) -> str:
        return flat_out[int(m.group(1))]

    return re.sub(r"__MLFLAT(\d+)__", unflatten, result)


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


def backfill_bw(conn: sqlite3.Connection, dry_run: bool = False,
                force: bool = False) -> dict:
    """Derive + namespace variants for every stored infographic.

    Idempotent: rows whose color SVG is namespaced and that already have a
    mono variant are skipped — unless force=True (e.g. after a mapping
    change: re-derive everything from the stored color SVGs)."""
    rows = conn.execute(
        "SELECT id, infographic_svg, infographic_svg_bw FROM cards "
        "WHERE infographic_svg IS NOT NULL AND infographic_svg != ''"
    ).fetchall()
    stats = {"scanned": len(rows), "updated": 0, "skipped": 0,
             "bw_failed": 0, "ns_skipped": 0, "warnings": []}
    for row in rows:
        has_ns = _NS_ATTR in row["infographic_svg"]
        if not force and has_ns and row["infographic_svg_bw"]:
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
