"""next --no-serve peek mode: teaser payload WITHOUT consumption."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mlearn"))

import pytest

from mlearn import config as config_mod
from mlearn import db as db_mod
from mlearn import select as select_mod


@pytest.fixture
def deck(tmp_path):
    cfg = config_mod.DEFAULTS.copy()
    cfg["paths"] = {
        "data_dir": str(tmp_path), "raw_dir": str(tmp_path / "raw"),
        "cards_dir": str(tmp_path / "cards"), "db": str(tmp_path / "t.db"),
        "sources": str(tmp_path / "sources.yaml"), "config": str(tmp_path / "c.yaml"),
    }
    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    db_mod.ensure_seed_clusters(conn, ["tech"])
    db_mod.upsert_sources(conn, [{
        "name": "S", "url": "https://ex.io/rss", "feed_url": None,
        "topic": "tech", "status": "trusted", "added_at": "2026-09-07",
    }])
    src = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()
    cl = conn.execute("SELECT id FROM clusters LIMIT 1").fetchone()
    for i in range(2):
        conn.execute("INSERT INTO items (source_id,title,url,processed,fetched_at,content_hash) VALUES (?,?,?,1,'2026-09-07','h')",
                     (src["id"], f"I{i}", f"https://ex.io/{i}"))
        item = conn.execute("SELECT id FROM items ORDER BY id DESC LIMIT 1").fetchone()
        cur = conn.execute(
            """INSERT INTO cards (item_id,cluster_id,title,hook,body_md,diagram_type,diagram_src,
               source_url,anchor_quote,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (item["id"], cl["id"], f"Card {i}", "hook", "Body with enough words.", "concept",
             "", f"https://ex.io/{i}", "a verbatim anchor quote", "ready", "2026-09-07"))
        conn.execute("INSERT INTO prompts (card_id, question, answer, due_at) VALUES (?, 'Q?', 'A.', NULL)",
                     (cur.lastrowid,))
    conn.commit()
    return cfg, conn


def test_peek_returns_payload_without_consuming(deck):
    cfg, conn = deck
    out = select_mod.next_cards(conn, cfg, 1, serve=False)
    assert out["cards"] and out["cards"][0]["title"] == "Card 0"  # FIFO oldest
    cid = out["cards"][0]["card_id"]
    row = conn.execute("SELECT status, served_at FROM cards WHERE id=?", (cid,)).fetchone()
    assert row["status"] == "ready"          # not consumed
    assert row["served_at"] is None
    due = conn.execute("SELECT COUNT(*) c FROM prompts WHERE card_id=? AND due_at IS NOT NULL", (cid,)).fetchone()
    assert due["c"] == 0                     # prompts not scheduled
    # source counter untouched
    n = conn.execute("SELECT cards_served FROM sources").fetchone()["cards_served"]
    assert n == 0


def test_serve_still_consumes(deck):
    cfg, conn = deck
    out = select_mod.next_cards(conn, cfg, 1, serve=True)
    cid = out["cards"][0]["card_id"]
    row = conn.execute("SELECT status FROM cards WHERE id=?", (cid,)).fetchone()
    assert row["status"] == "served"
    due = conn.execute("SELECT COUNT(*) c FROM prompts WHERE card_id=? AND due_at IS NOT NULL", (cid,)).fetchone()
    assert due["c"] == 1
    assert conn.execute("SELECT cards_served FROM sources").fetchone()["cards_served"] == 1