"""Markdown projection (spec 8): Obsidian-compatible, regenerable."""
from mlearn.project import render_card, slugify, write_cards

from conftest import seed_three


def test_render_card_structure(db):
    ids = seed_three(db)
    cid = ids[1]  # finance card
    card = [c for c in db.execute(
        "SELECT c.*, cl.label AS cluster_label FROM cards c JOIN clusters cl ON cl.id = c.cluster_id"
    ).fetchall() if c["id"] == cid][0]
    prompts = db.execute("SELECT * FROM prompts WHERE card_id = ?", (cid,)).fetchall()
    md = render_card(card, prompts)
    assert md.startswith("---\n")
    assert f"id: {cid}" in md
    assert "title: Why compounding feels invisible" in md
    assert "```mermaid" in md and "```" in md.split("```mermaid", 1)[1]
    assert "### Recall" in md
    assert "**Q:**" in md and "**A:**" in md
    assert "[Source](https://awealthofcommonsense.com" in md
    assert "> Why this matters:" in md


def test_write_cards_regenerates_identically(tmp_path, db):
    seed_three(db)
    paths1 = write_cards(db, tmp_path / "cards")
    assert len(paths1) == 3
    finance_path = [p for p in paths1 if p.parent.name == "finance"][0]
    assert finance_path.name.startswith("2026") and finance_path.name.endswith(".md")
    content1 = finance_path.read_text()
    paths2 = write_cards(db, tmp_path / "cards")
    assert [p for p in paths2 if p.parent.name == "finance"][0].read_text() == content1


def test_write_cards_prunes_archived(tmp_path, db):
    ids = seed_three(db)
    db.execute("UPDATE cards SET status = 'archived' WHERE id = ?", (ids[0],))
    db.commit()
    paths = write_cards(db, tmp_path / "cards")
    assert len(paths) == 2
    remaining = [p for p in (tmp_path / "cards").rglob("*.md")]
    assert len(remaining) == 2
    for p in remaining:
        assert f"id: {ids[0]}" not in p.read_text()[:400]


def test_slugify_path_safe():
    assert "/" not in slugify("a/b/c")
    assert slugify("")  # falls back to "card"