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


def test_granular_taste_boosts_similar_cards(db, cfg, monkeypatch):
    """Positive signal on card A raises A's near-neighbor weight far above a
    topic sibling: taste works on the EMBEDDING level, not the category."""
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
    boosts = taste_mod.boost_scores(db, 1.0, [c["id"] for c in cards])
    assert boosts[cards[1]["id"]] > boosts[cards[2]["id"]] + 0.5
    # negative signal on the FAR card drives its boost negative
    db.execute("INSERT INTO signals (card_id, kind, created_at) VALUES (?, 'less_like_this', ?)",
               (cards[2]["id"], select._iso(select.now())))
    db.commit()
    boosts2 = taste_mod.boost_scores(db, 1.0, [c["id"] for c in cards])
    assert boosts2[cards[1]["id"]] > 0 and boosts2[cards[2]["id"]] < 0


def test_taste_zero_without_feedback(db, cfg):
    import mlearn.taste as taste_mod
    seed_three(db)
    boosts = taste_mod.boost_scores(db, 1.0, [1, 2, 3])
    assert all(b == 0.0 for b in boosts.values())


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