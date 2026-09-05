"""Granular taste: embedding-level feedback, not topic-level.

A ready card's discovery weight is multiplied by (1 + strength * boost), where

    boost(card) = mean cosine(card, positive cards) - mean cosine(card, negative cards)

Positive cards: explicit "more_like_this" signals, implicit discovery opens
(deep-link clicks in the morning push), and grades good/easy.
Negative cards: "less_like_this" signals and grade again.

Because the comparison happens in embedding space, "more content like that"
means *that concept*, not *that topic* — psychology liked does not boost all
psychology; the liked *mechanism* does.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import embed as embed_mod

POS_SIGNALS = ("more_like_this", "discovery_open")
NEG_SIGNALS = ("less_like_this",)
POS_GRADES = (3, 4)  # good, easy
NEG_GRADES = (1,)  # again


def feedback_maps(conn: sqlite3.Connection, days: int = 30) -> tuple[set[int], set[int]]:
    """Card ids with positive / negative feedback within the window."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    pos: set[int] = set()
    neg: set[int] = set()

    marks_pos = ",".join("?" * len(POS_SIGNALS))
    marks_neg = ",".join("?" * len(NEG_SIGNALS))
    for row in conn.execute(
        f"SELECT DISTINCT card_id FROM signals WHERE kind IN ({marks_pos}) AND created_at >= ?",
        (*POS_SIGNALS, cutoff),
    ):
        pos.add(row["card_id"])
    for row in conn.execute(
        f"SELECT DISTINCT card_id FROM signals WHERE kind IN ({marks_neg}) AND created_at >= ?",
        (*NEG_SIGNALS, cutoff),
    ):
        neg.add(row["card_id"])

    marks_pg = ",".join("?" * len(POS_GRADES))
    marks_ng = ",".join("?" * len(NEG_GRADES))
    for row in conn.execute(
        f"""SELECT DISTINCT p.card_id FROM grades g JOIN prompts p ON p.id = g.prompt_id
            WHERE g.grade IN ({marks_pg}) AND g.created_at >= ?""",
        (*POS_GRADES, cutoff),
    ):
        pos.add(row["card_id"])
    for row in conn.execute(
        f"""SELECT DISTINCT p.card_id FROM grades g JOIN prompts p ON p.id = g.prompt_id
            WHERE g.grade IN ({marks_ng}) AND g.created_at >= ?""",
        (*NEG_GRADES, cutoff),
    ):
        neg.add(row["card_id"])

    return pos - neg, neg - pos


def boost_scores(conn: sqlite3.Connection, strength: float,
                 candidates: list[int]) -> dict[int, float]:
    """Boost per candidate card id in [-1, 1]-ish scale (0 when no feedback).

    Uses the same embedding pool as semantic search; cards without vectors
    (e.g. wip in tests) get a neutral 0.
    """
    pos, neg = feedback_maps(conn)
    if not pos and not neg:
        return {c: 0.0 for c in candidates}

    pool = {cid: v for cid, v in embed_mod.card_pool(conn)}
    pos_vecs = [pool[c] for c in pos if c in pool]
    neg_vecs = [pool[c] for c in neg if c in pool]

    out: dict[int, float] = {}
    for c in candidates:
        vec = pool.get(c)
        if vec is None:
            out[c] = 0.0
            continue
        plus = sum(embed_mod.cosine(vec, p) for p in pos_vecs) / len(pos_vecs) if pos_vecs else 0.0
        minus = sum(embed_mod.cosine(vec, n) for n in neg_vecs) / len(neg_vecs) if neg_vecs else 0.0
        out[c] = max(-1.0, min(1.0, plus - minus)) * strength
    return out


def taste_weight(boost: float) -> float:
    """Multiplicative weight: 1 + boost, floored so nothing dies entirely."""
    return max(0.05, 1.0 + boost)


def score_vectors(candidates: list[tuple[int, list[float] | None]],
                  conn: sqlite3.Connection, strength: float) -> dict[int, float]:
    """ACQUISITION taste: boost per candidate (item id, embedding).

    Same embedding math as boost_scores, but for the generation queue: items
    whose content resembles positively-signalled concepts are generated first
    ("more content like that"), disliked concepts are deferred. Pure function
    over already-computed vectors — callers embed the candidates themselves."""
    pos, neg = feedback_maps(conn)
    if not pos and not neg:
        return {i: 0.0 for i, _ in candidates}
    pool = {cid: v for cid, v in embed_mod.card_pool(conn)}
    pos_vecs = [pool[c] for c in pos if c in pool]
    neg_vecs = [pool[c] for c in neg if c in pool]
    out: dict[int, float] = {}
    for item_id, vec in candidates:
        if not vec:
            out[item_id] = 0.0
            continue
        plus = sum(embed_mod.cosine(vec, p) for p in pos_vecs) / len(pos_vecs) if pos_vecs else 0.0
        minus = sum(embed_mod.cosine(vec, n) for n in neg_vecs) / len(neg_vecs) if neg_vecs else 0.0
        out[item_id] = max(-1.0, min(1.0, plus - minus)) * strength
    return out