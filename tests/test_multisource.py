"""Phase 3: multi-source references — schema v2, insert, C8 gate, api surface."""
import os
import sys
from pathlib import Path

os.environ["MLEARN_VISUAL_QA"] = "0"  # unit tests use stub banners; visual QA is
                                      # exercised by the infographic suites

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mlearn"))

import pytest

from mlearn import config as config_mod
from mlearn import db as db_mod
from mlearn import project as project_mod
from mlearn import validate as validate_mod

BODY = ("Mechanisms of memory consolidation in the sleeping brain involve "
        "hippocampal replay and cortical integration. " * 15)


@pytest.fixture
def env(tmp_path):
    cfg = config_mod.DEFAULTS.copy()
    cfg["paths"] = {
        "data_dir": str(tmp_path / "data"), "raw_dir": str(tmp_path / "data" / "raw"),
        "cards_dir": str(tmp_path / "cards"), "db": str(tmp_path / "t.db"),
        "sources": str(tmp_path / "sources.yaml"), "config": str(tmp_path / "c.yaml"),
    }
    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    db_mod.ensure_seed_clusters(conn, ["tech"])
    return cfg, conn


def _item(conn, url="https://x.io/1", title="Item 1"):
    return db_mod.insert_item(conn, url=url, title=title, source_id=None,
                              content_hash=f"h:{url}", raw_path=None)


def _card(conn, **kw):
    kw.setdefault("item_id", _item(conn))
    kw.setdefault("cluster_label", "tech")
    kw.setdefault("title", "Memory consolidation")
    kw.setdefault("hook", "Why sleep strengthens memory.")
    kw.setdefault("body_md", BODY)
    kw.setdefault("diagram_type", "concept")
    kw.setdefault("diagram_src", "")
    kw.setdefault("infographic_svg", "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 400'><text x='10' y='30'>Memory consolidation</text><text x='10' y='380'>replay and integration</text></svg>")
    kw.setdefault("figures_json", None)
    kw.setdefault("source_url", "https://x.io/1")
    kw.setdefault("anchor_quote", "Mechanisms of memory")
    kw.setdefault("prompts", [{"question": "Q1?", "answer": "A1"}, {"question": "Q2?", "answer": "A2"}])
    return db_mod.insert_card(conn, **kw)


def test_schema_v2_tables_exist(env):
    cfg, conn = env
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"card_items", "card_links"} <= names
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 3


def test_schema_v3_bw_column(env):
    """v3 adds the mono (e-ink) infographic column."""
    cfg, conn = env
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cards)").fetchall()}
    assert "infographic_svg_bw" in cols


def test_migration_backfills_primary_references(env):
    """A card whose card_items rows were lost re-backfills on init (v2)."""
    cfg, conn = env
    item = _item(conn)
    cid = _card(conn, item_id=item)
    conn.execute("DELETE FROM card_items")   # simulate pre-v2 / lost junction
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM card_items").fetchone()["c"] == 0
    db_mod.init_db(conn)  # idempotent re-run backfills
    rows = conn.execute("SELECT role FROM card_items WHERE card_id = ?", (cid,)).fetchall()
    assert [r["role"] for r in rows] == ["primary"]
    db_mod.init_db(conn)  # second run: no duplicates (PK)
    assert conn.execute("SELECT COUNT(*) c FROM card_items").fetchone()["c"] == 1


def test_insert_card_context_and_links(env):
    cfg, conn = env
    primary = _item(conn, "https://x.io/1", "P")
    ctx1 = _item(conn, "https://x.io/2", "C1")
    ctx2 = _item(conn, "https://x.io/3", "C2")
    cid = _card(conn, item_id=primary,
                context_item_ids=[ctx1, ctx2, ctx1],  # dup deduped
                further_reading=[
                    {"url": "https://deep.example", "title": "Deep dive"},
                    {"url": "file:///Users/daniel/notes/x.md", "title": "Local note"},
                    {"url": "https://extra.example", "title": "Extra"},
                    {"url": "https://overflow.example", "title": "Cut off"},  # >3
                    {"url": "", "title": "stub"},  # empty url dropped
                ])
    rows = conn.execute(
        "SELECT role, ord FROM card_items WHERE card_id=? ORDER BY ord", (cid,)).fetchall()
    assert [(r["role"], r["ord"]) for r in rows] == [("primary", 0), ("context", 1), ("context", 2)]
    links = conn.execute("SELECT url, ord FROM card_links WHERE card_id=? ORDER BY ord",
                         (cid,)).fetchall()
    assert len(links) == 3  # capped at 3, empty dropped
    assert links[0]["url"] == "https://deep.example"


def test_validate_further_reading_gate(env):
    cfg, conn = env
    base = {
        "anchor_quote": "Mechanisms of memory consolidation", "diagram_src": "",
        "infographic_svg": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 400'><text x='10' y='30'>Memory consolidation</text><text x='10' y='380'>replay and integration</text></svg>",
        "diagram_type": "concept", "body_md": BODY,
        "title": "Memory consolidation", "hook": "Why sleep matters.",
        "figures": [],
        "prompts": [{"question": "What does hippocampal replay do?",
                     "answer": "It replays experiences during sleep to strengthen memory traces."},
                    {"question": "Where does integration happen?",
                     "answer": "In the cortex, which receives replayed patterns from the hippocampus."}],
    }
    ok, errs = validate_mod.validate_card(base, BODY, "tools")
    assert ok and not any("C8" in e for e in errs)  # absent = fine
    bad = dict(base, further_reading=[{"url": "ftp://x", "title": "t"}])
    ok, errs = validate_mod.validate_card(bad, BODY, "tools")
    assert not ok and any("C8" in e for e in errs)
    dup = dict(base, further_reading=[{"url": "https://a.io", "title": "1"},
                                      {"url": "https://a.io", "title": "2"}])
    ok, errs = validate_mod.validate_card(dup, BODY, "tools")
    assert any("duplicate" in e for e in errs)
    many = dict(base, further_reading=[{"url": f"https://a{i}.io", "title": f"t{i}"}
                                       for i in range(4)])
    ok, errs = validate_mod.validate_card(many, BODY, "tools")
    assert any("1-3" in e for e in errs)
    good = dict(base, further_reading=[{"url": "https://a.io", "title": "Link"},
                                       {"url": "file:///tmp/note.md", "title": "Local"}])
    ok, errs = validate_mod.validate_card(good, BODY, "tools")
    assert ok and not any("C8" in e for e in errs)


def test_render_card_includes_references_and_links(env):
    cfg, conn = env
    primary = _item(conn, "https://x.io/1", "P")
    ctx = _item(conn, "https://x.io/2", "Context title")
    cid = _card(conn, item_id=primary, context_item_ids=[ctx],
                further_reading=[{"url": "https://deep.example", "title": "Deep dive"}])
    row = conn.execute(
        "SELECT c.*, cl.label AS cluster_label FROM cards c JOIN clusters cl ON "
        "cl.id = c.cluster_id WHERE c.id = ?", (cid,)).fetchone()
    prompts = db_mod.prompts_for_card(conn, cid)
    refs = conn.execute(
        "SELECT ci.role, i.title, i.url FROM card_items ci JOIN items i ON i.id = ci.item_id "
        "WHERE ci.card_id = ? ORDER BY ci.ord", (cid,)).fetchall()
    links = conn.execute(
        "SELECT url, title FROM card_links WHERE card_id = ? ORDER BY ord", (cid,)).fetchall()
    md = project_mod.render_card(row, prompts, None, refs, links)
    assert "### Sources" in md and "Primary" in md and "Considered" in md
    assert "### Further reading" in md and "Deep dive" in md


def test_api_card_returns_references(env):
    from fastapi.testclient import TestClient
    from mlearn.api import create_app
    cfg, conn = env
    primary = _item(conn, "https://x.io/1", "P")
    ctx = _item(conn, "https://x.io/2", "C")
    cid = _card(conn, item_id=primary, context_item_ids=[ctx],
                further_reading=[{"url": "https://deep.example", "title": "Deep"}])
    app = create_app(cfg)
    with TestClient(app) as client:
        r = client.get(f"/cards/{cid}")
        assert r.status_code == 200
        data = r.json()
        assert len(data["references"]) >= 2
        assert any(x["role"] == "context" for x in data["references"])
        assert data["further_reading"][0]["url"] == "https://deep.example"