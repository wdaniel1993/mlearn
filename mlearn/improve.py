"""In-place card improvement — the polish path for the early days of the app.

Regeneration (archive + re-roll) is the wrong tool when a card only needs a
targeted fix: it burns the item, risks losing the topic on 3 strikes, and
rewrites what already works. `improve_card` keeps the SAME card id, item,
prompts row, serving counters and cluster links, asks the model to return
ONLY the fields in scope, re-runs the full validation gates on the merged
result, and updates the row in place. On failure the old card stays served
untouched — improving never loses content.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

# scope -> fields the model may return
SCOPES: dict[str, set[str]] = {
    "content": {"title", "hook", "body_md", "prompts", "anchor_quote"},
    "banner": {"infographic_spec"},
    "all": {"title", "hook", "body_md", "prompts", "anchor_quote",
            "infographic_spec"},
}

IMPROVE_USER = """You are improving ONE card of a personal microlearning deck.
Improve it IN PLACE — do not rewrite what is already good.

Current card (id {card_id}):
{card_json}

Source: {url}
Your note: {note}

Scope: change ONLY these fields — {scope}
Return a JSON object containing ONLY the fields you actually changed. Do not
repeat unchanged fields. Allowed values:
- title: crisp, specific
- hook: 1-2 sentences why this matters
- body_md: {word_range} words, short scannable markdown, inline ```mermaid
  fences allowed (flowchart LR, labeled edges, never stateDiagram loops)
- prompts: 2-4 recall Q&A pairs matching the body
- anchor_quote: verbatim quote from the source (<= 25 words)
- infographic_spec: ONLY if the banner itself needs fixing — an AntV spec
  (line 1: 'infographic <template>'; data with label+desc+icon per item;
  theme palette of 3-5 BRIGHT hexes). The banner renders dark; if the note
  is not about the banner, do not touch it.

Constraints that always apply: every abbreviation is spelled out at first
use (QLC (Quad-Level Cell)); body stays 300-500 words; keep the visual
rules: ONE main image (the banner), mermaid only inline; numbers stay
faithful to the source.
"""


def _allowed(scope: str) -> set[str]:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope} (use content|banner|all)")
    return SCOPES[scope]


def _load_card(conn, card_id: int):
    row = conn.execute(
        """SELECT c.*, i.raw_path AS item_raw, i.title AS item_title
           FROM cards c LEFT JOIN items i ON i.id = c.item_id
           WHERE c.id = ? AND c.status != 'archived'""", (card_id,)
    ).fetchone()
    if row is None:
        return None
    prompts = conn.execute(
        "SELECT question, answer FROM prompts WHERE card_id = ? ORDER BY id",
        (card_id,),
    ).fetchall()
    return row, [dict(p) for p in prompts]


def _card_json(row, prompts) -> dict:
    d = {
        "title": row["title"], "hook": row["hook"], "body_md": row["body_md"],
        "anchor_quote": row["anchor_quote"], "prompts": prompts,
    }
    spec = (row["infographic_spec"] or "").strip()
    if spec:
        d["infographic_spec"] = spec
    return d


def improve_card(conn, cfg: dict, card_id: int, note: str = "",
                 scope: str = "content", attempts: int = 3) -> dict:
    """Improve one card in place. Returns a summary dict."""
    from . import config as _c  # noqa: F401  (config already resolved by caller)
    from . import db as db_mod
    from . import embed as embed_mod
    from . import generate as gen
    from . import validate as val_mod
    from . import visualqa as vqa_mod

    tools_dir = Path(cfg.get("_base_dir", ".")) / "tools"
    loaded = _load_card(conn, card_id)
    if loaded is None:
        return {"card_id": card_id, "ok": False, "changed": [], "error": "card not found"}
    row, prompts = loaded
    # source text for the verbatim gates (anchor quote / figures)
    source_text = ""
    if row["item_raw"]:
        try:
            source_text = gen.item_text(row["item_raw"], row["item_title"] or "")
        except Exception:
            source_text = ""
    source_text = " ".join(source_text.split())[:4000]
    allowed = _allowed(scope)

    base = _card_json(row, prompts)
    reasons: list[str] = []
    for attempt in range(1, attempts + 1):
        user = IMPROVE_USER.format(
            card_id=card_id, card_json=json.dumps(base, ensure_ascii=False),
            url=row["source_url"], note=note or "(general polish)",
            scope=", ".join(sorted(allowed)),
            word_range="300-500",
        )
        if reasons:
            user += "\n\nYour previous attempt failed: " + "; ".join(reasons[-3:])
            user += ("\nReturn ONLY the corrected fields. If the failure is about "
                     "abbreviations, add parenthetical definitions.")
        raw = gen.call_llm(cfg, gen.build_system(""), user)
        if raw is None:
            reasons.append(f"attempt {attempt}: no response")
            continue
        patch = gen.parse_json(raw)
        if not isinstance(patch, dict) or not patch:
            reasons.append(f"attempt {attempt}: not a JSON object")
            continue
        patch = {k: v for k, v in patch.items() if k in allowed}
        if not patch:
            reasons.append("attempt %d: no fields in scope returned" % attempt)
            continue
        if scope == "banner" and "infographic_spec" not in patch:
            reasons.append(
                "attempt %d: banner scope requires a re-emitted infographic_spec "
                "(the banner renders from the spec; icons were blank — return the "
                "spec so the banner re-renders)" % attempt)
            continue
        if "prompts" in patch and not isinstance(patch["prompts"], list):
            reasons.append("attempt %d: prompts must be a list" % attempt)
            continue
        if "infographic_spec" in patch:
            spec = str(patch["infographic_spec"]).strip()
            if not spec.startswith("infographic "):
                reasons.append(
                    "attempt %d: infographic_spec must start with 'infographic <template>'"
                    % attempt)
                continue
            tmp_card = {"diagram_src": "", "infographic_spec": spec,
                        "_infographic_lane": "antv"}
            lane_errs = gen.apply_infographic_lane(tmp_card, tools_dir)
            if lane_errs:
                reasons.append("attempt %d: banner failed: %s" % (attempt, lane_errs[0]))
                continue
            patch["infographic_svg"] = tmp_card["infographic_svg"]
            # keep diagram_type/diagram_src semantics (empty src: main image is banner)

        # merge patch over the OLD card and validate the FULL card
        merged = {
            "title": patch.get("title", base["title"]),
            "hook": patch.get("hook", base["hook"]),
            "body_md": patch.get("body_md", base["body_md"]),
            "diagram_src": row["diagram_src"] or "",
            "diagram_type": row["diagram_type"],
            "figures": json.loads(row["figures_json"] or "[]") if row["figures_json"] else [],
            "infographic_svg": patch.get("infographic_svg", row["infographic_svg"] or ""),
            "anchor_quote": patch.get("anchor_quote", row["anchor_quote"]),
            "prompts": patch.get("prompts", prompts),
            "source_url": row["source_url"],
            "cluster": "",
            "figures_json": row["figures_json"] or "",
        }
        body = source_text
        ok, errors_ = val_mod.validate_card(
            merged, body, tools_dir, infographic_strict=False,
            banner_check=("infographic_spec" in patch))
        if not ok:
            reasons.append(f"attempt {attempt}: " + "; ".join(errors_[:4]))
            continue
        _apply(conn, card_id, row, patch)
        conn.commit()
        return {
            "card_id": card_id, "ok": True, "changed": sorted(patch.keys()),
            "attempts": attempt,
        }
    return {"card_id": card_id, "ok": False, "changed": [], "error": reasons[-1]
            if reasons else "unknown"}


def _apply(conn, card_id: int, row, patch: dict) -> None:
    """Write the patch to the cards table; refresh embedding + prompts."""
    from . import embed as embed_mod

    col_fields = {}
    new_prompts = None
    for f in ("title", "hook", "body_md", "anchor_quote"):
        if f in patch:
            col_fields[f] = patch[f]
    if "infographic_svg" in patch:
        col_fields["infographic_svg"] = patch["infographic_svg"]
    if "infographic_spec" in patch:
        col_fields["infographic_spec"] = patch["infographic_spec"]
    if "prompts" in patch:
        new_prompts = patch["prompts"]
    # embedding follows the new title/body (dedupe + taste read it)
    new_title = col_fields.get("title", row["title"])
    new_body = col_fields.get("body_md", row["body_md"])
    if new_title != row["title"] or new_body != row["body_md"]:
        vec = embed_mod.embed_one(new_title + ". " + " ".join(new_body.split())[:1400])
        if vec is not None:
            col_fields["embedding"] = embed_mod.pack(vec)

    if col_fields:
        sets = ", ".join(f"{f} = ?" for f in col_fields)
        conn.execute(f"UPDATE cards SET {sets} WHERE id = ?",
                     [*col_fields.values(), card_id])
    if new_prompts is not None:
        conn.execute("DELETE FROM prompts WHERE card_id = ?", (card_id,))
        for p in new_prompts:
            conn.execute(
                "INSERT INTO prompts (card_id, question, answer) VALUES (?, ?, ?)",
                (card_id, p.get("question", ""), p.get("answer", "")),
            )