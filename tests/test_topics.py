"""Topic catalog is configurable (general-purpose sharing)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mlearn"))

from mlearn import config as config_mod
from mlearn import db as db_mod
from mlearn import generate as gen_mod


def _cfg_with_topics(topics):
    cfg = config_mod.DEFAULTS.copy()
    cfg["topics"] = topics
    return cfg


def test_default_catalog_matches_seed_topics():
    # DB-level fallback mirrors the config default catalog (single source).
    assert db_mod.SEED_TOPICS == [t["name"] for t in config_mod.DEFAULTS["topics"]]
    assert len(db_mod.SEED_TOPICS) >= 1
    assert "technology" in db_mod.SEED_TOPICS


def test_custom_catalog_seeds_exactly_those_clusters(tmp_path):
    conn = db_mod.connect(str(tmp_path / "custom.db"))
    db_mod.init_db(conn)
    cfg = _cfg_with_topics([
        {"name": "astronomy", "guardrail": "Topic astronomy — explain celestial mechanics."},
        {"name": "cooking", "guardrail": "Topic cooking — explain why techniques work."},
    ])
    labels = [t["name"] for t in cfg["topics"]]
    ids = db_mod.ensure_seed_clusters(conn, labels)
    got = [r["label"] for r in conn.execute("SELECT label FROM clusters ORDER BY id")]
    assert got == ["astronomy", "cooking"]
    assert len(ids) == 2
    # no default topics leaked in
    assert "technology" not in got


def test_topic_guardrail_uses_custom_catalog():
    cfg = _cfg_with_topics([
        {"name": "astronomy", "guardrail": "Topic astronomy — explain celestial mechanics."},
    ])
    assert "celestial mechanics" in gen_mod.topic_guardrail(cfg, "astronomy")


def test_unknown_topic_gets_generic_guardrail():
    cfg = _cfg_with_topics([{"name": "astronomy", "guardrail": "specific"}])
    guard = gen_mod.topic_guardrail(cfg, "physics")
    assert guard == gen_mod.GENERIC_GUARDRAIL
    assert "mechanism" in guard


def test_build_system_embeds_custom_guardrail():
    cfg = _cfg_with_topics([{"name": "astronomy", "guardrail": "explain celestial mechanics"}])
    sys_out = gen_mod.build_system(cfg, "astronomy")
    assert "explain celestial mechanics" in sys_out
    assert "{TGUARD}" not in sys_out


def test_round_robin_covers_born_clusters(tmp_path):
    # allocation topics = ALL cluster rows (seeds + wildcard-born), not just
    # the default seed list — mirrors generate()'s live-catalog query
    conn = db_mod.connect(str(tmp_path / "rr.db"))
    db_mod.init_db(conn)
    db_mod.ensure_seed_clusters(conn, ["astronomy"])
    conn.execute("INSERT INTO clusters (label, is_seed, created_at, last_updated) VALUES ('linguistics', 0, '2026-09-06', '2026-09-06')")
    conn.commit()
    topics = [r["label"] for r in conn.execute("SELECT label FROM clusters ORDER BY id")] or db_mod.SEED_TOPICS
    assert topics == ["astronomy", "linguistics"]