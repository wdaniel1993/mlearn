"""REST API smoke tests (mlearn.api.create_app + TestClient), incl. /decide."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mlearn"))

from fastapi.testclient import TestClient

from mlearn import config as config_mod
from mlearn import db as db_mod
from mlearn.api import create_app


def _seed(tmp_path):
    cfg = config_mod.DEFAULTS.copy()
    cfg["paths"] = {
        "data_dir": str(tmp_path),
        "raw_dir": str(tmp_path / "raw"),
        "cards_dir": str(tmp_path / "cards"),
        "db": str(tmp_path / "api.db"),
        "sources": str(tmp_path / "sources.yaml"),
        "config": str(tmp_path / "config.yaml"),
    }
    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    db_mod.ensure_seed_clusters(conn, ["tech"])
    db_mod.upsert_sources(conn, [{
        "name": "Example", "url": "https://example.org/rss", "feed_url": None,
        "topic": "tech", "status": "trusted", "added_at": "2026-09-06",
    }])
    src = conn.execute("SELECT id FROM sources WHERE url = 'https://example.org/rss'").fetchone()
    conn.execute(
        "INSERT INTO items (source_id, title, url, processed, fetched_at, content_hash) VALUES (?, 'Item', 'https://example.org/item', 1, '2026-09-06T00:00:00', 'abc123')",
        (src["id"],),
    )
    item = conn.execute("SELECT id FROM items ORDER BY id DESC LIMIT 1").fetchone()
    cluster = conn.execute("SELECT id FROM clusters WHERE label='tech'").fetchone()
    cur = conn.execute(
        """INSERT INTO cards (item_id, cluster_id, title, hook, body_md, diagram_type,
           diagram_src, source_url, anchor_quote, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (item["id"], cluster["id"], "Test card", "why it matters",
         "Body with enough words to look real.", "concept", "",
         "https://example.org/item", "a verbatim anchor quote", "ready", "2026-09-06"),
    )
    card_id = cur.lastrowid
    conn.execute(
        "INSERT INTO prompts (card_id, question, answer, due_at) VALUES (?, 'Q?', 'A.', NULL)",
        (card_id,),
    )
    conn.commit()
    return cfg


def test_api_health_and_cards(tmp_path):
    cfg = _seed(tmp_path)
    client = TestClient(create_app(cfg))
    assert client.get("/health").status_code == 200
    cards = client.get("/cards?status=ready").json()
    assert len(cards) == 1
    assert cards[0]["title"] == "Test card"


def test_api_card_detail(tmp_path):
    cfg = _seed(tmp_path)
    client = TestClient(create_app(cfg))
    d = client.get("/cards/1").json()
    assert d["title"] == "Test card"
    assert "body_md" in d
    assert client.get("/cards/999").status_code == 404


def test_api_decide_like_consumes(tmp_path):
    cfg = _seed(tmp_path)
    client = TestClient(create_app(cfg))
    r = client.post("/decide", json={"card_id": 1, "action": "like"})
    assert r.status_code == 200
    body = r.json()
    assert body["consumed"] is True
    assert body["action"] == "like"
    cards = client.get("/cards?status=ready").json()
    assert len(cards) == 0  # deck shrank


def test_api_decide_validation(tmp_path):
    cfg = _seed(tmp_path)
    client = TestClient(create_app(cfg))
    assert client.post("/decide", json={"card_id": 1, "action": "yolo"}).status_code == 422
    assert client.post("/decide", json={"card_id": 999, "action": "skip"}).status_code == 404