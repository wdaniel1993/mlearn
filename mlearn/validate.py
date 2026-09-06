"""Hard validation gates (spec 4.2 validate). All must pass before a card
is stored. Failure -> regenerate, max 3 attempts, then drop the item."""
from __future__ import annotations

import re
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
MAX_INF_TEXT_CHARS = 100  # infographic: per-<text> cap (no overflow walls)

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?%?")
_MERMAID_FENCE_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.S)
_SVG_TAG_RE = re.compile(r"<(script|foreignObject|foreignobject)\b", re.I)
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


def infographic_valid(svg: str) -> tuple[bool, str]:
    """Visual-lane gate: infographic_svg must be a small, self-contained SVG."""
    if not svg or not svg.strip():
        return False, "empty"
    if len(svg) > MAX_INF_CHARS:
        return False, f"too large: {len(svg)} chars > {MAX_INF_CHARS}"
    if _SVG_TAG_RE.search(svg):
        return False, "script / foreignObject banned"
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
    # Canvas-fill gate: a near-full-bleed background rect must cover the canvas
    # (kills the 'big empty band below the poster' failure mode)
    covered = any(
        _localname(el.tag) == "rect"
        and _num(el.get("x")) <= 0.02 * vb_w
        and _num(el.get("y")) <= 0.02 * vb_h
        and _num(el.get("x")) + _num(el.get("width")) >= 0.98 * vb_w
        and _num(el.get("y")) + _num(el.get("height")) >= 0.98 * vb_h
        for el in root.iter()
    )
    if not covered:
        return False, "canvas not filled: need a full-bleed background rect"
    # Fill-height gate: the lowest text baseline must reach ~80% of canvas height
    # (kills the 'big empty band under the poster' failure mode)
    max_y = max(
        (_num(el.get("y")) for el in root.iter() if _localname(el.tag) == "text"),
        default=0.0,
    )
    if max_y < 0.60 * vb_h:
        return False, (f"text stops at y={max_y:.0f} (< 60% of {vb_h:.0f} canvas height)"
                       f" — content must fill the canvas")
    n_texts = 0
    for el in root.iter():
        if _localname(el.tag) == "text":
            n_texts += 1
            txt = "".join(el.itertext())
            if len(txt) > MAX_INF_TEXT_CHARS:
                return False, f"<text> too long ({len(txt)} chars > {MAX_INF_TEXT_CHARS})"
    if n_texts == 0:
        return False, "no <text> elements — an infographic must carry text"
    if n_texts > MAX_INF_TEXTS:
        return False, f"{n_texts} <text> elements > {MAX_INF_TEXTS}"
    return True, ""


def infographic_text(svg: str) -> str:
    """All text content of the infographic (used for the figures gate)."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return ""
    return "\n".join("".join(el.itertext()) for el in root.iter()
                     if _localname(el.tag) == "text")


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


def validate_card(card: dict, source_body: str, tools_dir: str | Path) -> tuple[bool, list[str]]:
    """All hard gates. Return (ok, [error strings])."""
    errors: list[str] = []

    if not anchor_in_body(card.get("anchor_quote", ""), source_body):
        errors.append("anchor_quote not found verbatim in source body (C3)")
    if anchor_word_count(card.get("anchor_quote", "")) > MAX_ANCHOR_WORDS:
        errors.append(f"anchor_quote > {MAX_ANCHOR_WORDS} words (got "
                      f"{anchor_word_count(card['anchor_quote'])})")

    diagram_src = str(card.get("diagram_src", "") or "")
    infographic = str(card.get("infographic_svg") or "")
    fences = body_mermaid_fences(card.get("body_md", ""))
    hero_lines = [ln for ln in diagram_src.splitlines()
                  if ln.strip() and not ln.strip().startswith("%%")]
    if hero_lines:
        ok, err = mermaid_valid(diagram_src, tools_dir)
        if not ok:
            errors.append(f"mermaid parse failed: {err} (C6)")
        if len(hero_lines) > MAX_DIAGRAM_LINES:
            errors.append(f"diagram too busy: {len(hero_lines)} lines > {MAX_DIAGRAM_LINES} "
                          f"(simple, very understandable diagrams only)")
        figures_visual = diagram_src
    else:
        ok, err = infographic_valid(infographic)
        if not ok:
            errors.append(f"infographic invalid: {err} (C6)")
        figures_visual = infographic_text(infographic) if ok else ""
    if not hero_lines and not infographic.strip() and not fences:
        errors.append("need at least one visual: diagram_src, infographic_svg, "
                      "or an inline mermaid fence (C6)")

    for i, fence in enumerate(fences):
        fl = [ln for ln in fence.splitlines()
              if ln.strip() and not ln.strip().startswith("%%")]
        if len(fl) > MAX_DIAGRAM_LINES:
            errors.append(f"inline mermaid fence {i + 1} too busy: {len(fl)} lines "
                          f"> {MAX_DIAGRAM_LINES}")
        ok, err = mermaid_valid(fence, tools_dir)
        if not ok:
            errors.append(f"inline mermaid fence {i + 1} parse failed: {err} (C6)")

    ok, err = figures_pass(
        card.get("diagram_type", ""), figures_visual,
        card.get("figures_json"), source_body,
    )
    if not ok:
        errors.append(f"figures gate failed: {err} (C4)")

    words = count_words(card.get("body_md", ""))
    if not (MIN_BODY_WORDS <= words <= MAX_BODY_WORDS):
        errors.append(f"body word count {words} outside [{MIN_BODY_WORDS}, {MAX_BODY_WORDS}]")

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