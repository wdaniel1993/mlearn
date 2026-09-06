"""Schema contracts: ids, sources sync, triggers, embedding round-trip."""
import json
import sqlite3

import pytest

from mlearn import db as db_mod
from mlearn.project import slugify

from conftest import seed_three


def test_init_creates_schema_and_seed_clusters(db):
    assert db.execute("SELECT COUNT(*) n FROM clusters WHERE is_seed = 1").fetchone()["n"] == 6
    assert db.execute("SELECT COUNT(*) n FROM profile").fetchone()["n"] == 1
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_seed_clusters_idempotent(db):
    ids1 = db_mod.ensure_seed_clusters(db)
    ids2 = db_mod.ensure_seed_clusters(db)
    assert ids1 == ids2


def test_upsert_sources_preserves_counters(db):
    srcs = [{"name": "A", "url": "https://a.example", "feed_url": "https://a.example/feed",
             "topic": "technology", "status": "trusted",
             "meta": {"kind": "wikipedia", "pages": ["Yield curve"]}}]
    db_mod.upsert_sources(db, srcs)
    row = db.execute("SELECT * FROM sources WHERE url = 'https://a.example'").fetchone()
    assert json.loads(row["meta"]) == {"kind": "wikipedia", "pages": ["Yield curve"]}
    db.execute("UPDATE sources SET cards_served = 7, grade_sum = 21 WHERE url = 'https://a.example'")
    db_mod.upsert_sources(db, [{"name": "A2", "url": "https://a.example", "feed_url": None,
                                "topic": "technology", "status": "probation",
                                "meta": {"kind": "wikipedia", "pages": ["Habit"]}}])
    row = db.execute("SELECT * FROM sources WHERE url = 'https://a.example'").fetchone()
    assert row["name"] == "A2"
    assert row["cards_served"] == 7
    assert row["grade_sum"] == 21
    assert row["status"] == "probation"
    assert json.loads(row["meta"]) == {"kind": "wikipedia", "pages": ["Habit"]}


def test_upsert_sources_prunes_vanished_urls(db):
    old = [{"name": "A", "url": "https://a.example", "feed_url": "https://a.example/feed",
            "topic": "technology", "status": "trusted"},
           {"name": "B", "url": "https://b.example", "feed_url": "https://b.example/feed",
            "topic": "finance", "status": "probation"}]
    db_mod.upsert_sources(db, old)
    # B gets history -> must be retired, not deleted; A vanishes with no history -> deleted
    db_mod.insert_item(db, url="https://b.example/article1", title="t", source_id=2,
                       content_hash="h1")
    res = db_mod.upsert_sources(db, [old[0]])
    assert res["removed"] == 0
    assert res["retired"] == 1
    b = db.execute("SELECT * FROM sources WHERE url = 'https://b.example'").fetchone()
    assert b["status"] == "retired" and b["feed_url"] is None
    assert db.execute("SELECT COUNT(*) n FROM sources").fetchone()["n"] == 2
    # now A vanishes too (fresh sync with neither) -> deleted
    res = db_mod.upsert_sources(db, [])
    assert res["removed"] == 1
    assert db.execute("SELECT COUNT(*) n FROM sources").fetchone()["n"] == 1


def test_insert_card_unknown_cluster_raises(db):
    with pytest.raises(ValueError):
        db_mod.insert_card(db, item_id=None, cluster_label="nope", title="t", hook="h",
                           body_md="b" * 500, diagram_type="concept", diagram_src="flowchart TD\nA-->B",
                           infographic_svg=None,
                           figures_json=None, source_url="https://x.example", anchor_quote="q",
                           prompts=[])


def test_card_roundtrip(db):
    ids = seed_three(db)
    cards = db_mod.list_cards(db)
    assert len(cards) == 3
    c = [x for x in cards if x["id"] == ids[0]][0]   # first seed = technology card
    assert c["cluster_label"] == "technology"
    prompts = db_mod.prompts_for_card(db, ids[0])
    assert len(prompts) >= 2


def test_grades_append_only(db):
    seed_three(db)
    db.execute("INSERT INTO grades (prompt_id, card_id, grade, created_at) VALUES (1, 1, 3, 'x')")
    db.commit()
    gid = db.execute("SELECT MAX(id) id FROM grades").fetchone()["id"]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(f"DELETE FROM grades WHERE id = {gid}")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(f"UPDATE grades SET grade = 4 WHERE id = {gid}")


def test_embedding_pack_roundtrip():
    vec = [0.5, -0.25, 3.5, 1024.0]  # exactly representable in float32
    blob = db_mod.pack_vec(vec)
    assert len(blob) == 4 * 4
    assert db_mod.unpack_vec(blob) == vec
    assert db_mod.unpack_vec(None) is None


def test_slugify():
    assert slugify("Why Compilers Hoist Loop Invariants") == "why-compilers-hoist-loop-invariants"
    assert slugify("  a!!b  ") == "a-b"
    assert slugify("999") == "999"