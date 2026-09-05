"""Selector: FSRS grading, serving, cap, decay, thompson floor, arm birth."""
from datetime import datetime, timedelta, timezone

from mlearn import db as db_mod
from mlearn import novelty
from mlearn import select

from conftest import seed_three


def pytest_approx(x):
    import pytest
    return pytest.approx(x, abs=1e-6)


def _all_prompts(db):
    return db.execute("SELECT * FROM prompts ORDER BY id").fetchall()


def _finance_prompt(db):
    return db.execute(
        """SELECT p.* FROM prompts p JOIN cards c ON c.id = p.card_id
           JOIN clusters cl ON cl.id = c.cluster_id
           WHERE cl.label = 'finance' LIMIT 1"""
    ).fetchone()


def test_grade_updates_fsrs_state(db, cfg):
    seed_three(db)
    p = _finance_prompt(db)
    before = select.now()
    res = select.grade_prompt(db, cfg, p["id"], 3)
    assert res["grade"] == 3
    assert res["stability"] > 0
    assert res["difficulty"] > 0
    assert res["reps"] == 1
    row = db.execute("SELECT * FROM prompts WHERE id = ?", (p["id"],)).fetchone()
    assert row["due_at"] > before.isoformat()
    assert db.execute("SELECT COUNT(*) n FROM grades").fetchone()["n"] == 1
    # bandit update: good -> alpha +1
    cluster = db.execute("SELECT * FROM clusters WHERE label = 'finance'").fetchone()
    assert cluster["alpha"] == pytest_approx(2.0)


def test_again_lowers_beta_raises_lapses(db, cfg):
    seed_three(db)
    p = _finance_prompt(db)
    select.grade_prompt(db, cfg, p["id"], 1)
    row = db.execute("SELECT * FROM prompts WHERE id = ?", (p["id"],)).fetchone()
    assert row["lapses"] == 1
    beta = db.execute("SELECT beta FROM clusters WHERE label = 'finance'").fetchone()["beta"]
    assert beta == pytest_approx(2.0)


def test_serving_marks_served_and_schedules_prompts(db, cfg):
    seed_three(db)
    res = select.next_cards(db, cfg, 2)
    assert len(res["cards"]) == 2
    assert all(c["kind"] == "discovery" for c in res["cards"])
    served = db.execute("SELECT * FROM cards WHERE status = 'served'").fetchall()
    assert len(served) == 2
    assert all(s["served_at"] for s in served)
    for s in served:
        ps = db.execute("SELECT * FROM prompts WHERE card_id = ?", (s["id"],)).fetchall()
        assert all(p["due_at"] for p in ps)


def test_daily_cap(db, cfg):
    seed_three(db)
    cfg["daily_cap"] = 2
    res = select.next_cards(db, cfg, 5)
    assert len(res["cards"]) == 2
    served_today = db.execute(
        "SELECT COUNT(*) n FROM cards WHERE served_at IS NOT NULL"
    ).fetchone()["n"]
    assert served_today == 2
    res = select.next_cards(db, cfg, 3)
    assert res["cards"] == []
    assert "cap" in res["reason"]


def test_due_prompts_evening_retention(db, cfg):
    """Evening SR: served cards' due prompts surface, oldest first; `next`
    (morning) stays pure discovery and never returns retention."""
    seed_three(db)
    select.next_cards(db, cfg, 3)  # serve all three, prompts due now +1d
    db.execute("UPDATE prompts SET due_at = ? WHERE id = 1",
               (select._iso(select.now() - timedelta(days=3)),))
    db.execute("UPDATE prompts SET due_at = ? WHERE id = 2",
               (select._iso(select.now() - timedelta(days=1)),))
    db.commit()
    due = select.due_prompts(db, 5)
    assert [p["prompt_id"] for p in due] == [1, 2]  # oldest first
    assert due[0]["card_id"] == 1 and due[0]["title"]
    # morning push: even with due prompts, only discovery cards
    res = select.next_cards(db, cfg, 3)
    assert all(c["kind"] == "discovery" for c in res["cards"])
    # ack: reviewed + due pushed one day out
    a = select.ack_prompt(db, 1)
    assert a["acked"]
    due2 = select.due_prompts(db, 5)
    assert 1 not in [p["prompt_id"] for p in due2]
    assert select.ack_prompt(db, 999)["error"]


def test_granular_taste_shapes_acquisition_not_sending(db, cfg, monkeypatch):
    """"\"More content like that\" = embedding-level, applied at ACQUISITION:
    items near a positively-signalled card get generated first, far items
    deferred. Sending itself is strict FIFO regardless of taste."""
    import mlearn.taste as taste_mod
    seed_three(db)
    cards = [dict(r) for r in db.execute(
        "SELECT id, cluster_id FROM cards ORDER BY id").fetchall()]
    # card1+card2 similar embeddings, card3 far away (same cluster as card2)
    vecs = {cards[0]["id"]: [1.0, 0.0], cards[1]["id"]: [0.98, 0.02],
            cards[2]["id"]: [0.0, 1.0]}
    monkeypatch.setattr(taste_mod.embed_mod, "card_pool",
                        lambda conn: [(cid, v) for cid, v in vecs.items()])
    db.execute("INSERT INTO signals (card_id, kind, created_at) VALUES (?, 'more_like_this', ?)",
               (cards[0]["id"], select._iso(select.now())))
    db.commit()
    # acquisition: candidate items 101 (near pos) and 102 (far neg-ish)
    scores = taste_mod.score_vectors(
        [(101, [0.99, 0.01]), (102, [0.1, 0.9])], db, 1.0)
    assert scores[101] > scores[102] + 0.5
    # negative signal drives the far candidate negative
    db.execute("INSERT INTO signals (card_id, kind, created_at) VALUES (?, 'less_like_this', ?)",
               (cards[2]["id"], select._iso(select.now())))
    db.commit()
    scores2 = taste_mod.score_vectors(
        [(101, [0.99, 0.01]), (102, [0.1, 0.9])], db, 1.0)
    assert scores2[101] > 0 and scores2[102] < 0
    # sending: FIFO even with taste signals present
    pick = select._pick_ready_card(db, "round-robin", None)
    assert pick is not None and pick["id"] == 1


def test_decide_tinder_consume(db, cfg):
    """Tinder swipe = feedback + consume: like schedules SR prompts, dislike/
    skip consume without scheduling; deck advances FIFO."""
    seed_three(db)
    # like: served + prompts due tomorrow + positive signal
    r = select.decide(db, cfg, 1, "like")
    assert r["consumed"] and r["next_ready"] == 2
    assert db.execute("SELECT status FROM cards WHERE id=1").fetchone()["status"] == "served"
    due = db.execute("SELECT due_at FROM prompts WHERE card_id=1").fetchall()
    assert all(d["due_at"] is not None for d in due)
    sig = db.execute("SELECT kind FROM signals WHERE card_id=1").fetchone()["kind"]
    assert sig == "more_like_this"
    # dislike: served, prompts NOT scheduled, negative signal
    db.execute("UPDATE cards SET status='served', served_at=NULL WHERE id=2")
    db.commit()
    r2 = select.decide(db, cfg, 2, "dislike")
    assert r2["next_ready"] == 3
    assert db.execute("SELECT status FROM cards WHERE id=2").fetchone()["status"] == "served"
    due2 = db.execute("SELECT due_at FROM prompts WHERE card_id=2").fetchall()
    assert all(d["due_at"] is None for d in due2)
    # skip: consume, no SR scheduling
    nxt = select.decide(db, cfg, 3, "skip")["next_ready"]
    assert nxt is None  # deck empty


def test_search_empty_query_fifo_browse(db, cfg):
    """Mini-app browse with no query: oldest live card first (FIFO, matching
    serving); queries flip to relevance order; status filter applies."""
    seed_three(db)
    r = select.search(db, cfg, "")
    assert [c["id"] for c in r["cards"]] == [1, 2, 3]
    assert r["total"] == 3
    db.execute("UPDATE cards SET status = 'served', served_at = ? WHERE id = 1",
               (select._iso(select.now()),))
    db.commit()
    r2 = select.search(db, cfg, "", status="ready")
    assert [c["id"] for c in r2["cards"]] == [2, 3]
    assert r2["total"] == 2
    r3 = select.search(db, cfg, "", status="served")
    assert [c["id"] for c in r3["cards"]] == [1]


def test_discovery_open_consumes_card(db, cfg):
    """Morning deep-link tap = implicit serve: ready -> served, prompts enter
    the FSRS loop (due +1d); the card will never be re-pushed."""
    seed_three(db)
    r = select.signal(db, cfg, 1, "discovery_open")
    assert r["consumed"] is True
    status = db.execute("SELECT status FROM cards WHERE id = 1").fetchone()["status"]
    assert status == "served"
    due = db.execute("SELECT due_at FROM prompts WHERE card_id = 1").fetchall()
    assert all(d["due_at"] is not None for d in due)
    # second tap: already served -> no double consume
    r2 = select.signal(db, cfg, 1, "discovery_open")
    assert r2["consumed"] is False
    # plain signals never consume
    select.signal(db, cfg, 2, "more_like_this")
    assert db.execute("SELECT status FROM cards WHERE id = 2").fetchone()["status"] == "ready"


def test_fifo_sending_unconditional(db, cfg):
    """Serving is strict FIFO: oldest ready card first, taste irrelevant."""
    seed_three(db)
    pick = select._pick_ready_card(db, "round-robin", None)
    assert pick is not None and pick["id"] == 1


def test_thompson_floor_and_shift(db, cfg):
    """Phase-4 acceptance: after 50 synthetic grades favoring one topic,
    allocation shifts measurably toward it while every cluster keeps >= 3%.
    Note: untouched arms stay Beta(1,1) (mean 0.5), so 'shifts measurably'
    means clearly above baseline, not a majority share."""
    seed_three(db)
    cfg["allocation_policy"] = "thompson"
    for _ in range(5):  # 10 prompts x 5 rounds = 50 synthetic grades
        for p in _all_prompts(db):
            label = db.execute(
                "SELECT cl.label FROM prompts p JOIN cards c ON c.id = p.card_id "
                "JOIN clusters cl ON cl.id = c.cluster_id WHERE p.id = ?", (p["id"],)
            ).fetchone()["label"]
            grade = 4 if label == "finance" else 1
            select.grade_prompt(db, cfg, p["id"], grade)
    # 20 draws: stochastic by design; any single draw can lose the Beta race,
    # what must shift is the expectation.
    finance_shares, other_bad_shares = [], []
    for _ in range(20):
        weights = select.thompson_allocation(db, cfg["exploration_floor"])
        alloc = {db.execute("SELECT label FROM clusters WHERE id = ?", (k,)).fetchone()["label"]: v
                 for k, v in weights.items()}
        assert all(v >= cfg["exploration_floor"] - 1e-9 for v in alloc.values()), "floor respected"
        assert abs(sum(alloc.values()) - 1.0) < 1e-6
        finance_shares.append(alloc["finance"])
        other_bad_shares.append(max(alloc["mental_health"], alloc["technology"]))
    mean_finance = sum(finance_shares) / len(finance_shares)
    mean_bad = sum(other_bad_shares) / len(other_bad_shares)
    assert mean_finance > 0.3, f"finance mean share {mean_finance:.3f} should shift off baseline ~0.2"
    assert mean_finance > 3 * mean_bad, \
        f"finance {mean_finance:.3f} should clearly beat punished clusters {mean_bad:.3f}"


def test_decay_pulls_posteriors_toward_1(db, cfg):
    db.execute("UPDATE clusters SET alpha = 10.0, beta = 10.0, last_updated = '2020-01-01T00:00:00+00:00'")
    res = select.decay_clusters(db, cfg)
    assert res["decayed"] == 6
    row = db.execute("SELECT alpha, beta FROM clusters LIMIT 1").fetchone()
    assert row["alpha"] == pytest_approx(1 + 9 * 0.95)
    assert row["beta"] == pytest_approx(1 + 9 * 0.95)


def test_arm_birth(db, cfg):
    card_ids = seed_three(db)
    # make one card a wildcard + give it an embedding
    cid = card_ids[2]
    vec = [0.1 * i for i in range(8)]
    db.execute("UPDATE cards SET is_wildcard = 1, embedding = ? WHERE id = ?",
               (db_mod.pack_vec(vec), cid))
    card = db.execute("SELECT * FROM cards WHERE id = ?", (cid,)).fetchone()
    new_id = novelty.arm_birth(db, cfg, card)
    assert new_id is not None
    cl = db.execute("SELECT * FROM clusters WHERE id = ?", (new_id,)).fetchone()
    assert cl["alpha"] == 2.0 and cl["beta"] == 1.0
    assert cl["birth_card_id"] == cid and cl["is_seed"] == 0
    assert db.execute("SELECT cluster_id FROM cards WHERE id = ?", (cid,)).fetchone()["cluster_id"] == new_id
    again = novelty.arm_birth(db, cfg, card)  # idempotent
    assert again == new_id