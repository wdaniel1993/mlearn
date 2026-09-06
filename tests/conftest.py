"""Shared fixtures: a fresh in-tmp DB per test + minimal cfg."""
import json
import sqlite3
from pathlib import Path

import pytest

from mlearn import db as db_mod

REPO = Path(__file__).resolve().parent.parent
SEEDS = REPO / "seeds" / "phase1_cards.json"


def make_cfg(tmp_path: Path) -> dict:
    paths = {
        "data_dir": str(tmp_path / "data"),
        "raw_dir": str(tmp_path / "data" / "raw"),
        "cards_dir": str(tmp_path / "cards"),
        "db": str(tmp_path / "data" / "app.db"),
        "sources": str(REPO / "sources.yaml"),
    }
    cfg = {
        "buffer_target": 20, "buffer_floor": 8, "batch_size": 12,
        "daily_cap": 5, "discovery_ratio": 0.7, "wildcard_rate": 0.15,
        "exploration_floor": 0.03, "dedupe_threshold": 0.92,
        "profile_lambda": 0.1, "decay_factor": 0.95,
        "probation_cards": 20, "max_probation_sources": 3,
        "retention_target": 0.9, "allocation_policy": "round-robin",
        "paths": paths, "_base_dir": str(REPO),
    }
    return cfg


@pytest.fixture()
def db(tmp_path):
    conn = db_mod.connect(str(tmp_path / "app.db"))
    db_mod.init_db(conn)
    db_mod.ensure_seed_clusters(conn)
    return conn


@pytest.fixture()
def cfg(tmp_path):
    return make_cfg(tmp_path)


def seed_three(conn):
    """The three Phase-1 seed cards (already validation-passed)."""
    cards = json.loads(SEEDS.read_text())
    ids = []
    for c in cards:
        item_id = db_mod.insert_item(
            conn, url=c["source_url"], title=c["title"], source_id=None,
            content_hash=f"seed:{c['source_url']}",
        )
        cid = db_mod.insert_card(
            conn, item_id=item_id, cluster_label=c["cluster"],
            title=c["title"], hook=c["hook"], body_md=c["body_md"],
            diagram_type=c["diagram_type"], diagram_src=c["diagram_src"],
            infographic_svg=None,
            figures_json=json.dumps(c.get("figures") or []),
            source_url=c["source_url"], anchor_quote=c["anchor_quote"],
            prompts=c["prompts"],
        )
        ids.append(cid)
    return ids