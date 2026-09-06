"""Hard validation gates (spec 4.2 validate). All must pass before a card
is stored. Failure -> regenerate, max 3 attempts, then drop the item."""
from __future__ import annotations

import os
import re
import statistics
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

# Operator standard (2026-09): short, scannable cards
MIN_BODY_WORDS = 200
MAX_BODY_WORDS = 500
MAX_ANCHOR_WORDS = 25
MIN_PROMPTS = 2
MAX_DIAGRAM_LINES = 10  # simple, very understandable diagrams only (no walls)
MAX_INF_CHARS = 15_000  # infographic SVG size cap
MAX_INF_TEXTS = 40      # infographic: text element cap
MAX_INF_TEXT_CHARS = 200  # per text element (span-wrapped lines inside foreignObject)

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?%?")
_MERMAID_FENCE_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.S)
# Abbreviation gate: ALL-CAPS tokens (>=3 letters) or digit+letters (5G, 4K)
# must be defined at first use. Two-letter tokens (AI, US, TV) are skipped —
# they are ambient language; the user's bar is acronyms like QLC/SLC.
_ABBR_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{3,6}|\d[A-Z]{1,3})\b")
# Logic words that legitimately appear all-caps inside definitions
_ABBR_WHITELIST = {"AND"}
_visual_qa = None


def _visual_qa_enabled() -> bool:
    global _visual_qa
    if _visual_qa is None:
        _visual_qa = os.environ.get("MLEARN_VISUAL_QA", "1").lower() not in ("0", "false", "no", "off")
    return _visual_qa


def _load_visualqa():
    from . import visualqa as vqa
    return vqa


def unexplained_abbrs(*texts: str) -> list[str]:
    """Collect ALL-CAPS/digit-letter tokens that are never defined by a
    parenthetical ('QLC (Quad-Level Cell)' or 'Quad-Level Cell (QLC)')."""
    raw = "\n".join(texts)
    prose = "\n".join(
        ln for ln in raw.splitlines()
        if not ln.strip().startswith("#")            # drop markdown headers
    )
    prose = re.sub(r"```.*?```", " ", prose, flags=re.S)   # drop fences
    prose = re.sub(r"`[^`]*`", " ", prose)                 # drop inline code
    prose = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", prose)  # link text only
    abbrs = sorted({m.group(1) for m in _ABBR_RE.finditer(prose)
                if m.group(1) not in _ABBR_WHITELIST})
    bad = []
    for a in abbrs:
        # 'QLC (Quad-Level Cell)' or 'Quad-Level Cell (QLC)'; allow a version
        # suffix so 'COVID-19 (coronavirus disease 2019)' satisfies 'COVID'
        if re.search(rf"\b{re.escape(a)}(?:-\d+)?\s*\(", prose):
            continue
        if re.search(rf"\(\s*{re.escape(a)}(?:-\d+)?\s*\)", prose):
            continue
        bad.append(a)
    return bad
_SVG_TAG_RE = re.compile(r"<script|</script|<iframe", re.I)
# event handlers and javascript: URLs are banned even inside engine-rendered
# foreignObject HTML (AntV renders text as HTML spans — legit, but scrubbed)
_SVG_ATTR_RE = re.compile(r"\son\w+\s*=|javascript\s*:", re.I)
_EXT_REF_RE = re.compile(r"\bxlink:href\s*=|href\s*=\s*[\"']https?:", re.I)


def count_words(text: str) -> int:
    return len(text.split())


def anchor_in_body(anchor: str, body: str) -> bool:
    """C3: verbatim span check."""
    return bool(anchor) and anchor in body


def anchor_word_count(anchor: str) -> int:
    return len(anchor.split())


def mermaid_valid(src: str, tools_dir: str | Path) -> tuple[bool, str]:
    """C6: the diagram must parse before a card is stored.

    Uses `mermaid.parse()` via node (tools/parse.mjs); mmdc is heavier and
    drags in Chromium. Returns (ok, error)."""
    parse_mjs = Path(tools_dir) / "parse.mjs"
    if not parse_mjs.is_file():
        return False, f"mermaid parser not found: {parse_mjs} (run: cd tools && npm i mermaid)"
    try:
        r = subprocess.run(
            ["node", str(parse_mjs)], input=src, capture_output=True,
            text=True, timeout=60,
        )
    except FileNotFoundError:
        return False, "node not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "mermaid parse timed out"
    if r.returncode == 0:
        return True, ""
    err = (r.stderr or r.stdout or "parse error").strip().splitlines()
    return False, err[-1][:300] if err else "parse error"


def _numbers_in(text: str) -> list[str]:
    """Distinct numeric tokens (incl. percentages) present in text."""
    seen = []
    for tok in _NUM_RE.findall(text):
        norm = tok.replace(",", ".").rstrip("%")
        if norm not in seen:
            seen.append(norm)
    return seen


def _num(val) -> float:
    """SVG numeric attr: missing -> 0 (per SVG spec); unparseable -> NaN (gates fail clean)."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return float("nan")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _svg_text_items(root) -> list[tuple[str, float]]:
    """(text, y) for <text> elements AND AntV <foreignObject> text spans.

    foreignObject carries the rendered HTML text when the AntV engine renders
    a spec; y accumulates translate() transforms from ancestor groups."""
    parents = {}
    for el in root.iter():
        for child in el:
            parents[child] = el
    items = []
    for el in root.iter():
        if _localname(el.tag) not in ("text", "foreignobject"):
            continue
        txt = "".join(el.itertext()).strip()
        if not txt:
            continue
        y = _num(el.get("y"))
        node = el
        while node is not None:
            t = node.get("transform")
            if t:
                m = re.search(r"translate\(\s*([-\d.eE]+)[,\s]+([-\d.eE]+)", t)
                if m:
                    y += float(m.group(2))
            node = parents.get(node)
        items.append((txt, y))
    return items


def infographic_valid(svg: str, strict_layout: bool = True) -> tuple[bool, str]:
    """Visual-lane gate: infographic_svg must be a small, self-contained SVG.

    Engine-agnostic: covers raw model-written SVG and AntV-rendered specs
    (foreignObject text spans, banner aspect). strict_layout applies the
    fill-height rule (lowest text baseline reaches the bottom quarter) — the
    raw lane needs it (models leave blank bands); AntV-rendered banners are
    tight by construction."""
    if not svg or not svg.strip():
        return False, "empty"
    if len(svg) > MAX_INF_CHARS:
        return False, f"too large: {len(svg)} chars > {MAX_INF_CHARS}"
    if _SVG_TAG_RE.search(svg):
        return False, "script / iframe banned"
    if _SVG_ATTR_RE.search(svg):
        return False, "event handlers / javascript: banned"
    if _EXT_REF_RE.search(svg):
        return False, "external hrefs banned (must be self-contained)"
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        return False, f"not parseable: {e}"
    if _localname(root.tag) != "svg":
        return False, "root must be <svg>"
    vb = root.get("viewBox")
    if not vb or len(vb.split()) != 4:
        return False, "viewBox with 4 numbers required"
    try:
        vb_w, vb_h = (float(v) for v in vb.split()[-2:])  # viewBox = minx miny WIDTH HEIGHT
    except ValueError:
        return False, "viewBox must be numeric"
    items = _svg_text_items(root)
    if not items:
        return False, "no text — an infographic must carry text"
    for txt, _y in items:
        if len(txt) > MAX_INF_TEXT_CHARS:
            return False, f"text too long ({len(txt)} chars > {MAX_INF_TEXT_CHARS})"
    if len(items) > MAX_INF_TEXTS:
        return False, f"{len(items)} text elements > {MAX_INF_TEXTS}"
    max_y = max(y for _t, y in items)
    if strict_layout and max_y < 0.75 * vb_h:
        return False, (f"text stops at y={max_y:.0f} (< 75% of {vb_h:.0f} canvas height)"
                       f" — content must fill the canvas")
    return True, ""


def infographic_text(svg: str) -> str:
    """All text content of the infographic (used for the figures gate)."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return ""
    return "\n".join(txt for txt, _y in _svg_text_items(root))


def body_mermaid_fences(body_md: str) -> list[str]:
    """Inline mermaid fences inside body_md (visual budget, optional)."""
    return [m.group(1).strip() for m in _MERMAID_FENCE_RE.finditer(body_md or "")]


def figures_pass(diagram_type: str, diagram_src: str, figures_json: str | None,
                 source_body: str) -> tuple[bool, str]:
    """C4: quantitative diagrams require extracted figures; every figure has
    a verbatim source substring.
    figures_json entries: {"value": <number>, "source": "<verbatim span>"}
    """
    if diagram_type != "data":
        return True, ""
    if not figures_json:
        return False, "diagram_type='data' but figures_json is empty"
    try:
        import json
        figures = json.loads(figures_json)
    except Exception:
        return False, "figures_json is not valid JSON"
    if not figures:
        return False, "diagram_type='data' but no figures"
    fig_vals = {str(f.get("value", "")).replace(",", ".") for f in figures}
    missing = [num for num in _numbers_in(diagram_src) if num not in fig_vals]
    # percentages: '12%' -> value 12
    for tok in _NUM_RE.findall(diagram_src):
        if tok.endswith("%") and tok[:-1] not in fig_vals:
            missing.append(tok[:-1])
    if missing:
        return False, f"diagram numbers missing from figures_json: {sorted(set(missing))}"
    for f in figures:
        quote = str(f.get("source", ""))
        if not quote or quote not in source_body:
            return False, f"figure {f.get('value')!r} lacks a verbatim source substring"
    return True, ""


def validate_card(card: dict, source_body: str, tools_dir: str | Path,
                  infographic_strict: bool = True,
                  banner_check: bool = True,
                  mermaid_check: bool = True) -> tuple[bool, list[str]]:
    """All hard gates. Return (ok, [error strings]).

    infographic_strict=False when the infographic was rendered by the AntV
    engine (banner aspect): the fill-height layout gate does not apply.
    banner_check/mermaid_check skip the visual-QA passes (used by in-place
    improves on unchanged fields — re-gating an unchanged old banner or
    inline fence would block unrelated content/banner fixes)."""
    errors: list[str] = []

    if not anchor_in_body(card.get("anchor_quote", ""), source_body):
        errors.append("anchor_quote not found verbatim in source body (C3)")
    if anchor_word_count(card.get("anchor_quote", "")) > MAX_ANCHOR_WORDS:
        errors.append(f"anchor_quote > {MAX_ANCHOR_WORDS} words (got "
                      f"{anchor_word_count(card['anchor_quote'])})")

    diagram_src = str(card.get("diagram_src", "") or "")
    infographic = str(card.get("infographic_svg") or "")
    fences = body_mermaid_fences(card.get("body_md", ""))

    # Main image = ONE infographic (spec-rendered banner or raw-SVG fallback).
    # Mermaid exists ONLY as inline fences embedded in the body (zero to many).
    hero_lines = [ln for ln in diagram_src.splitlines()
                  if ln.strip() and not ln.strip().startswith("%%")]
    if hero_lines:
        errors.append("no hero mermaid (C6): the main image is ONE infographic; "
                      "mermaid only as inline ```mermaid fences in body_md — "
                      "set diagram_src to empty string")
        figures_visual = ""
    else:
        ok, err = infographic_valid(infographic, infographic_strict)
        if not ok:
            errors.append(f"infographic invalid: {err} (C6)")
        elif _visual_qa_enabled() and banner_check:
            ok, err = _load_visualqa().qa_banner_svg(infographic)
            if not ok:
                errors.append(f"infographic visual QA: {err} (C6)")
        figures_visual = infographic_text(infographic) if ok else ""
    if not infographic.strip():
        errors.append("no main image (C6): the card needs an infographic "
                      "(infographic_spec rendered banner, or the raw "
                      "infographic_svg fallback)")

    for i, fence in enumerate(fences):
        fl = [ln for ln in fence.splitlines()
              if ln.strip() and not ln.strip().startswith("%%")]
        if len(fl) > MAX_DIAGRAM_LINES:
            errors.append(f"inline mermaid fence {i + 1} too busy: {len(fl)} lines "
                          f"> {MAX_DIAGRAM_LINES}")
        ok, err = mermaid_valid(fence, tools_dir)
        if not ok:
            errors.append(f"inline mermaid fence {i + 1} parse failed: {err} (C6)")
            continue
        if _visual_qa_enabled() and mermaid_check:
            ok, err = _load_visualqa().qa_mermaid(fence, tools_dir)
            if not ok:
                errors.append(f"inline mermaid fence {i + 1} visual QA: {err}")

    ok, err = figures_pass(
        card.get("diagram_type", ""), figures_visual,
        card.get("figures_json"), source_body,
    )
    if not ok:
        errors.append(f"figures gate failed: {err} (C4)")

    words = count_words(card.get("body_md", ""))
    if not (MIN_BODY_WORDS <= words <= MAX_BODY_WORDS):
        errors.append(f"body word count {words} outside [{MIN_BODY_WORDS}, {MAX_BODY_WORDS}]")

    bad_abbrs = unexplained_abbrs(
        card.get("title", ""), card.get("hook", ""),
        card.get("body_md", ""), card.get("anchor_quote", ""),
    )
    if bad_abbrs:
        errors.append(
            "abbreviations not explained (C6): "
            + ", ".join(bad_abbrs[:8])
            + " — spell each one out at first use, e.g. 'solid-state drive (SSD)' "
            "or 'Quad-Level Cell (QLC)'"
        )

    prompts = card.get("prompts") or []
    if len(prompts) < MIN_PROMPTS:
        errors.append(f"need >= {MIN_PROMPTS} prompts (got {len(prompts)})")
    for i, p in enumerate(prompts):
        q = str(p.get("question", "")).strip()
        a = str(p.get("answer", "")).strip()
        if not q or not q.endswith("?"):
            errors.append(f"prompt {i+1}: question must end with '?'")
        if len(a) < 20:
            errors.append(f"prompt {i+1}: answer too short to be answerable from the body")

    return (not errors), errors