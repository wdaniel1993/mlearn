"""Markdown projection writer (spec 8).

Obsidian-compatible, write-only output. Editing a file never changes system
state; SQLite is the source of truth and can regenerate everything.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

import yaml


def slugify(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:60].strip("-") or "card"


def render_card(card: sqlite3.Row, prompts: list[sqlite3.Row]) -> str:
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
    md.append("```mermaid")
    md.append(card["diagram_src"].rstrip())
    md.append("```")
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
    """Regenerate the full (or since-date) markdown projection. Returns paths."""
    cards_dir = Path(cards_dir)
    cards = conn.execute(
        """SELECT c.*, cl.label AS cluster_label
           FROM cards c JOIN clusters cl ON cl.id = c.cluster_id
           WHERE (? IS NULL OR c.created_at >= ?)
           ORDER BY c.id""",
        (since, since),
    ).fetchall()
    written: list[Path] = []
    for card in cards:
        prompts = conn.execute(
            "SELECT * FROM prompts WHERE card_id = ? ORDER BY id", (card["id"],)
        ).fetchall()
        topic_dir = cards_dir / card["cluster_label"]
        topic_dir.mkdir(parents=True, exist_ok=True)
        path = topic_dir / f"{card['created_at'][:10]}-{slugify(card['title'])}.md"
        path.write_text(render_card(card, prompts), encoding="utf-8")
        written.append(path)
    return written