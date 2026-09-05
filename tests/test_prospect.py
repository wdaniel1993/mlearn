"""Prospector contracts: verdict parsing, bridging, seen-state, failure paths."""
import json

import pytest

from mlearn import db as db_mod
from mlearn import prospect as prospect_mod


def _pop_science_source(conn, name="Quanta Magazine", topic="innovation"):
    conn.execute(
        "INSERT INTO sources (name, url, feed_url, topic, status, added_at) "
        "VALUES (?, ?, ?, ?, 'trusted', '2026-09-05T08:00:00')",
        (name, f"https://{name.lower().replace(' ', '')}.example", None, topic),
    )
    return conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()["id"]


def _item(conn, source_id, title="What makes a memory?", url=None):
    conn.execute(
        """INSERT INTO items (source_id, url, title, fetched_at, content_hash, raw_path, processed)
           VALUES (?, ?, ?, '2026-09-05T08:00:00', 'prospect-test', '', 0)""",
        (source_id, url or f"https://example.org/{title.replace(' ', '-')}", title),
    )
    return conn.execute("SELECT id FROM items ORDER BY id DESC LIMIT 1").fetchone()["id"]


@pytest.fixture()
def pool(db, cfg):
    src = _pop_science_source(db)
    ids = [_item(db, src, f"Article {i}") for i in range(3)]
    return src, ids


def test_bridges_timeless_idea(monkeypatch, db, cfg, pool):
    src, ids = pool
    monkeypatch.setattr(prospect_mod, "call_llm", lambda cfg, s, u: json.dumps({
        "items": [
            {"id": ids[0], "timeless": True, "idea": "Memory reconsolidation",
             "wiki_title": "Memory consolidation", "why": "mechanism with studies"},
            {"id": ids[1], "timeless": False, "idea": None, "wiki_title": None,
             "why": "week's news"},
        ]}))
    monkeypatch.setattr(prospect_mod, "_wiki_extract", lambda t: ("x" * 2000, None))
    res = prospect_mod.prospect(db, cfg, count=5)
    assert res["reviewed"] == 3
    assert res["bridged"] == 1
    assert len(res["ideas"]) == 1
    row = db.execute(
        "SELECT url, source_id, processed FROM items WHERE url LIKE 'https://en.wikipedia.org/%'"
    ).fetchone()
    assert row is not None
    assert row["url"] == "https://en.wikipedia.org/wiki/Memory_consolidation"
    assert row["source_id"] == src  # discovery credit stays with the pop-science source
    assert row["processed"] == 0  # normal pipeline picks it up
    state = json.loads(open(cfg["paths"]["data_dir"] + "/prospect_state.json").read())
    assert set(state["seen"]) == set(ids)  # reviewed ids never re-reviewed


def test_does_not_bridge_existing_page(monkeypatch, db, cfg, pool):
    src, ids = pool
    db_mod.insert_item(db, url="https://en.wikipedia.org/wiki/Habit", title="Habit",
                       source_id=None, content_hash="h0")
    monkeypatch.setattr(prospect_mod, "call_llm", lambda cfg, s, u: json.dumps({
        "items": [{"id": ids[0], "timeless": True, "idea": "Habits",
                   "wiki_title": "Habit", "why": "classic"}]}))
    res = prospect_mod.prospect(db, cfg, count=5)
    assert res["bridged"] == 0


def test_garbage_verdicts_do_not_crash(monkeypatch, db, cfg, pool):
    src, ids = pool
    monkeypatch.setattr(prospect_mod, "call_llm", lambda cfg, s, u: "not json at all")
    res = prospect_mod.prospect(db, cfg, count=5)
    assert res["reviewed"] == 3
    assert res["bridged"] == 0
    assert "error" in res
    state = json.loads(open(cfg["paths"]["data_dir"] + "/prospect_state.json").read())
    assert set(state["seen"]) == set(ids)


def test_seen_state_skips_reviewed(monkeypatch, db, cfg, pool):
    src, ids = pool
    monkeypatch.setattr(prospect_mod, "call_llm",
                        lambda cfg, s, u: json.dumps({"items": []}))
    prospect_mod.prospect(db, cfg, count=2)
    seen_payload = []
    monkeypatch.setattr(prospect_mod, "call_llm", lambda cfg, s, u: (
        seen_payload.append(json.loads(u)) or json.dumps({"items": []})))
    prospect_mod.prospect(db, cfg, count=5)
    payloads = [p["articles"] for p in seen_payload]
    # items reviewed in the first pass are never shipped again
    reviewed_ids = {i["id"] for i in (payloads[0] if payloads else [])}
    second_items = [i["id"] for p in payloads[1:] for i in p]
    assert all(i not in second_items for i in reviewed_ids)