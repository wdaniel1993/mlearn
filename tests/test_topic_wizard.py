"""Phase 2: catalog wizard — propose/validate/apply + catalog-in-sources.yaml."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mlearn"))

import pytest

from mlearn import config as config_mod
from mlearn import db as db_mod
from mlearn import generate as generate_mod
from mlearn import topic as topic_mod

GOOD = {
    "name": "stochastic_processes",
    "guardrail": (
        "Topic stochastic_processes — explain the mechanism of random processes: "
        "definitions, properties, and the math behind them. NEVER prescriptive, "
        "never diagnostic."),
    "description": "Random processes in math and nature",
    "sources": [
        {"name": "Example Feed", "url": "https://example.org",
         "feed_url": "https://example.org/rss", "notes": "good fit"},
    ],
}


@pytest.fixture
def env(tmp_path):
    cfg = config_mod.DEFAULTS.copy()
    cfg["paths"] = {
        "data_dir": str(tmp_path / "data"), "raw_dir": str(tmp_path / "data" / "raw"),
        "cards_dir": str(tmp_path / "cards"), "db": str(tmp_path / "t.db"),
        "sources": str(tmp_path / "sources.yaml"), "config": str(tmp_path / "c.yaml"),
    }
    cfg["_base_dir"] = str(tmp_path)
    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    db_mod.ensure_seed_clusters(conn, ["tech"])
    cfg["_topics_cache"] = None
    return cfg, conn


def test_load_topics_falls_back_to_defaults(env):
    cfg, _ = env
    topics = config_mod.load_topics(cfg)
    assert topics == config_mod.DEFAULTS["topics"]


def test_load_topics_reads_sources_yaml(env, tmp_path):
    cfg, _ = env
    (tmp_path / "sources.yaml").write_text(
        "topics:\n  - name: cooking\n    guardrail: Topic cooking — explain how "
        "techniques and processes actually work. Never prescriptive.\n"
        "sources: []\n")
    cfg.pop("_topics_cache", None)
    topics = config_mod.load_topics(cfg)
    assert [t["name"] for t in topics] == ["cooking"]


def test_validate_good_proposal(env):
    cfg, _ = env
    assert topic_mod.validate(cfg, GOOD, check_network=False) == []


def test_validate_rules(env):
    cfg, _ = env
    dup = dict(GOOD, name="technology")  # exists in DEFAULT catalog
    assert any("already exists" in e for e in topic_mod.validate(cfg, dup, False))
    bad = dict(GOOD, name="Not Valid!")
    assert any("name must match" in e for e in topic_mod.validate(cfg, bad, False))
    nog = dict(GOOD, guardrail="short")
    assert any("guardrail too short" in e for e in topic_mod.validate(cfg, nog, False))
    nolic = dict(GOOD, sources=[])
    assert any("sources must be" in e for e in topic_mod.validate(cfg, nolic, False))
    badsrc = dict(GOOD, sources=[{"name": "X", "url": "ftp://x", "feed_url": "x"}])
    assert any("url must be http" in e for e in topic_mod.validate(cfg, badsrc, False))


def test_apply_writes_catalog_and_seeds(env, tmp_path):
    cfg, conn = env
    res = topic_mod.apply(cfg, GOOD)
    assert res["topic"] == "stochastic_processes"
    assert res["sources_added"] == 1
    doc = config_mod.load_sources_doc(cfg)
    names = [t["name"] for t in doc["topics"]]
    assert "stochastic_processes" in names
    assert any(s["topic"] == "stochastic_processes" for s in doc["sources"])
    # cluster seeded
    cl = db_mod.cluster_by_label(conn, "stochastic_processes")
    assert cl is not None and cl["is_seed"] == 1
    # source row in DB with probation
    row = conn.execute("SELECT status, kind FROM sources WHERE url = ?",
                       ("https://example.org",)).fetchone()
    assert row is not None and row["status"] == "probation" and row["kind"] == "rss"
    # idempotence guard
    with pytest.raises(ValueError):
        topic_mod.apply(cfg, GOOD)
    # load_topics now sees the new catalog entry
    cfg.pop("_topics_cache", None)
    assert any(t["name"] == "stochastic_processes" for t in config_mod.load_topics(cfg))


def test_apply_skips_duplicate_source_urls(env):
    cfg, _ = env
    (Path(cfg["paths"]["sources"])).write_text(
        "sources:\n  - name: Example Feed\n    url: https://example.org\n"
        "    feed_url: https://example.org/rss\n    topic: tech\n    status: trusted\n")
    cfg.pop("_topics_cache", None)
    res = topic_mod.apply(cfg, GOOD)
    assert res["sources_skipped"] == 1  # url already present, no duplicate row


def test_propose_strict_json(env, monkeypatch):
    cfg, _ = env
    payload = '{"name": "quantum_computing", "guardrail": "Topic quantum_computing — ' \
              'explain the mechanics of qubits and gates with evidence. Never ' \
              'prescriptive.", "description": "d", "sources": [{"name": "S", ' \
              '"url": "https://s.example", "feed_url": "https://s.example/feed"}]}'
    monkeypatch.setattr(generate_mod, "call_llm", lambda cfg, sys, user: payload)
    prop = topic_mod.propose(cfg, "quantum computing")
    assert prop["name"] == "quantum_computing"
    assert prop["sources"][0]["url"].startswith("https://")


def test_propose_strips_fences(env, monkeypatch):
    cfg, _ = env
    payload = '```json\n{"name":"x","guardrail":"' + "g" * 60 + '","sources":[]}\n```'
    monkeypatch.setattr(generate_mod, "call_llm", lambda cfg, sys, user: payload)
    prop = topic_mod.propose(cfg, "x")
    assert prop["name"] == "x"