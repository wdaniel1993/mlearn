"""Hard validation gates (spec 4.2 validate). All must pass before a card
is stored. Failure -> regenerate, max 3 attempts, then drop the item."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

MIN_BODY_WORDS = 400
MAX_BODY_WORDS = 1100
MAX_ANCHOR_WORDS = 25
MIN_PROMPTS = 2

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?%?")


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

    ok, err = mermaid_valid(card.get("diagram_src", ""), tools_dir)
    if not ok:
        errors.append(f"mermaid parse failed: {err} (C6)")

    ok, err = figures_pass(
        card.get("diagram_type", ""), card.get("diagram_src", ""),
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