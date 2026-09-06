"""Markdown projection writer (spec 8).

Obsidian-compatible, write-only output. Editing a file never changes system
state; SQLite is the source of truth and can regenerate everything.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import yaml


def slugify(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:60].strip("-") or "card"


def render_card(card: sqlite3.Row, prompts: list[sqlite3.Row],
                infographic_name: str | None = None) -> str:
    frontmatter = {
        "id": card["id"],
        "title": card["title"],
        "topic": card["cluster_label"],
        "cluster": card["cluster_label"],
        "source": card["source_url"],
        "anchor": card["anchor_quote"],
        "created": card["created_at"][:10],
        "status": card["status"],
        "wildcard": bool(card["is_wildcard"]),
        "tags": ["mlearn", card["cluster_label"]],
    }
    md = ["---", yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip(), "---", ""]
    md.append(f"> Why this matters: {card['hook']}")
    md.append("")
    # one-main-image contract: diagram_src is ALWAYS empty (mermaid lives as
    # inline fences in body_md) — no hero placeholder
    if (card["diagram_src"] or "").strip():
        md.append("```mermaid")
        md.append(card["diagram_src"].rstrip())
        md.append("```")
        md.append("")
    if infographic_name:
        md.append(f"![[{infographic_name}]]")
        md.append("")
    md.append(card["body_md"].rstrip())
    md.append("")
    md.append("---")
    md.append("### Recall")
    for i, p in enumerate(prompts, 1):
        md.append(f"{i}. **Q:** {p['question']}")
        md.append(f"   **A:** {p['answer']}")
    md.append("")
    md.append(f"[Source]({card['source_url']})")
    md.append("")
    return "\n".join(md)


def write_cards(conn: sqlite3.Connection, cards_dir: str | Path,
                since: str | None = None) -> list[Path]:
    """Regenerate the markdown projection. Returns paths.

    Archived cards are NOT written (a re-rolled card replaces its old file),
    and stale files — present on disk but with no live card id in the DB —
    are pruned so Obsidian never shows obsolete content."""
    cards_dir = Path(cards_dir).resolve()
    rows = conn.execute(
        """SELECT c.*, cl.label AS cluster_label
           FROM cards c JOIN clusters cl ON cl.id = c.cluster_id
           WHERE (? IS NULL OR c.created_at >= ?) AND c.status != 'archived'
           ORDER BY c.id""",
        (since, since),
    ).fetchall()
    written: list[Path] = []
    expected: set[Path] = set()
    for card in rows:
        prompts = conn.execute(
            "SELECT * FROM prompts WHERE card_id = ? ORDER BY id", (card["id"],)
        ).fetchall()
        topic_dir = cards_dir / card["cluster_label"]
        topic_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{card['created_at'][:10]}-{slugify(card['title'])}"
        path = topic_dir / f"{stem}.md"
        inf_name = None
        if card["infographic_svg"]:
            inf_name = f"{stem}_infographic.svg"
            (topic_dir / inf_name).write_text(card["infographic_svg"], encoding="utf-8")
            expected.add(topic_dir / inf_name)
        path.write_text(render_card(card, prompts, inf_name), encoding="utf-8")
        expected.add(path)
        written.append(path)
    _prune_stale(cards_dir, expected)
    return written


def _prune_stale(cards_dir: Path, expected: set[Path]) -> int:
    """Remove projection files that this run did not write (.md cards and
    .svg infographic attachments).

    Covers dead card ids AND files from older DB generations whose ids were
    reused by new cards (a plain id check would keep them alive forever)."""
    removed = 0
    if not cards_dir.is_dir():
        return 0
    for md in list(cards_dir.rglob("*.md")) + list(cards_dir.rglob("*.svg")):
        try:
            if md.resolve() not in expected:
                md.unlink()
                removed += 1
        except OSError:
            pass
    return removed