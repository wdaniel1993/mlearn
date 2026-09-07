"""Phase 1: local content sources — scan/extract/ingest + harvest branch + topic joins."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mlearn"))

import pytest

from mlearn import config as config_mod
from mlearn import db as db_mod
from mlearn import generate as generate_mod
from mlearn import harvest as harvest_mod
from mlearn import local as local_mod


@pytest.fixture
def env(tmp_path):
    cfg = config_mod.DEFAULTS.copy()
    cfg["paths"] = {
        "data_dir": str(tmp_path / "data"), "raw_dir": str(tmp_path / "data" / "raw"),
        "cards_dir": str(tmp_path / "cards"), "db": str(tmp_path / "t.db"),
        "sources": str(tmp_path / "sources.yaml"), "config": str(tmp_path / "c.yaml"),
    }
    cfg["taste_strength"] = 0.0
    cfg["generate"] = dict(cfg["generate"], max_retries=1)
    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    db_mod.ensure_seed_clusters(conn, ["tech", "finance"])
    return cfg, conn


def _mkfile(root: Path, name: str, content: str) -> Path:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_scan_extract_ingest_new(env):
    cfg, conn = env
    root = Path("/tmp") / "mlearn_l1"
    root.mkdir(exist_ok=True)
    _mkfile(root, "a.md", "# Alpha\n\nDeep text about alpha mechanisms and results. " * 40)
    _mkfile(root, "b/sub/b.txt", "Beta notes. " * 60)
    _mkfile(root, "skip.bin", "nope")
    res = local_mod.ingest(conn, cfg, root, topic="tech", source_id=None)
    assert res["new"] == 2, res
    assert res["failed"] == 0
    rows = conn.execute("SELECT url, topic, processed FROM items ORDER BY url").fetchall()
    assert all(r["topic"] == "tech" for r in rows)
    assert all(r["processed"] == 0 for r in rows)
    assert all(r["url"].startswith("file://") for r in rows)
    # re-ingest is idempotent
    res2 = local_mod.ingest(conn, cfg, root, topic="tech", source_id=None)
    assert res2["new"] == 0 and res2["skipped"] == 2


def test_ingest_refresh_on_edit(env):
    cfg, conn = env
    f = _mkfile(Path("/tmp/mlearn_l2"), "doc.md", "Version one text. " * 30)
    local_mod.ingest(conn, cfg, f, topic="tech")
    row = conn.execute("SELECT id, content_hash FROM items WHERE url = ?",
                       (local_mod.file_url(f),)).fetchone()
    h1 = row["content_hash"]
    f.write_text("Version two different content here. " * 30)
    local_mod.ingest(conn, cfg, f, topic="tech")
    row = conn.execute("SELECT content_hash, processed FROM items WHERE url = ?",
                       (local_mod.file_url(f),)).fetchone()
    assert row["content_hash"] != h1  # refreshed
    assert row["processed"] == 0      # no live card -> re-mineable


def test_refresh_keeps_processed_when_live_card(env):
    cfg, conn = env
    db_mod.upsert_sources(conn, [{"name": "S", "url": "https://x.io", "feed_url": None,
                                  "topic": "tech", "status": "trusted"}], prune=False)
    f = _mkfile(Path("/tmp/mlearn_l3"), "doc.md", "Old stable content. " * 30)
    local_mod.ingest(conn, cfg, f, topic="tech")
    item = conn.execute("SELECT * FROM items WHERE url = ?", (local_mod.file_url(f),)).fetchone()
    card_id = db_mod.insert_card(
        conn, item_id=item["id"], cluster_label="tech", title="T", hook="H",
        body_md="B. " * 60, diagram_type="concept", diagram_src="",
        infographic_svg=None, figures_json=None,
        source_url=item["url"], anchor_quote="Old stable", prompts=[])
    conn.execute("UPDATE items SET processed = 1 WHERE id = ?", (item["id"],))
    conn.commit()
    f.write_text("Edited content now different. " * 30)
    local_mod.ingest(conn, cfg, f, topic="tech")
    row = conn.execute("SELECT content_hash, processed FROM items WHERE url = ?",
                       (local_mod.file_url(f),)).fetchone()
    assert row["processed"] == 1  # live card depends on it -> keep processed


def test_harvest_local_source(env):
    cfg, conn = env
    root = Path("/tmp/mlearn_l4")
    root.mkdir(exist_ok=True)
    _mkfile(root, "n1.md", "Harvestable local content. " * 40)
    src = {
        "name": "Local Notes", "url": local_mod.file_url(root), "feed_url": None,
        "topic": "tech", "status": "probation", "kind": "local",
        "meta": {"ext": [".md"], "recursive": True},
    }
    db_mod.upsert_sources(conn, [src], prune=False)
    res = harvest_mod.harvest(conn, cfg)
    assert res["new_items"] >= 1
    row = conn.execute("SELECT i.topic FROM items i WHERE i.url LIKE 'file://%'").fetchone()
    assert row is not None and row["topic"] == "tech"


def test_kind_column_migration_backfills_wikipedia(env):
    cfg, conn = env
    db_mod.upsert_sources(conn, [{"name": "W", "url": "https://en.wikipedia.org/wiki/Test",
                                  "feed_url": None, "topic": "tech", "status": "trusted",
                                  "meta": {"kind": "wikipedia", "pages": ["A"]}}], prune=False)
    assert conn.execute("SELECT kind FROM sources WHERE url = ?",
                        ("https://en.wikipedia.org/wiki/Test",)).fetchone()["kind"] == "wikipedia"


def test_generate_picks_orphan_local_items_by_items_topic(env):
    """One-shot ingest (source_id NULL) must be pickable via items.topic."""
    cfg, conn = env
    f = _mkfile(Path("/tmp/mlearn_l5"), "m.md", "Some topic material. " * 40)
    local_mod.ingest(conn, cfg, f, topic="finance")  # source_id=None
    item = conn.execute("SELECT * FROM items WHERE url = ?", (local_mod.file_url(f),)).fetchone()
    assert item["source_id"] is None
    # the round-robin pick (generate path) must find it
    got = conn.execute(
        """SELECT i.* FROM items i LEFT JOIN sources s ON s.id = i.source_id
           WHERE i.processed = 0 AND COALESCE(i.topic, s.topic) = 'finance'
           ORDER BY i.id LIMIT 1""").fetchone()
    assert got is not None and got["id"] == item["id"]


def test_upsert_sources_prune_flag(env):
    cfg, conn = env
    db_mod.upsert_sources(conn, [{"name": "A", "url": "https://a.io", "feed_url": None,
                                  "topic": "tech", "status": "trusted"}], prune=False)
    db_mod.upsert_sources(conn, [{"name": "B", "url": "https://b.io", "feed_url": None,
                                  "topic": "tech", "status": "trusted"}], prune=False)
    n = conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"]
    assert n == 2  # no pruning
    # default prune=True removes rows missing from the file (no history -> delete)
    db_mod.upsert_sources(conn, [{"name": "B", "url": "https://b.io", "feed_url": None,
                                  "topic": "tech", "status": "trusted"}])
    rows = conn.execute("SELECT url, status FROM sources ORDER BY url").fetchall()
    assert [dict(r) for r in rows] == [{"url": "https://b.io", "status": "trusted"}]