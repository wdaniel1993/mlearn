"""Novelty — the wildcard slot and arm birth (spec 6.4).

A bandit can only explore arms it already knows. Wildcards deliberately
serve from the maximum-distance region (bottom quartile of cosine to the
profile vector), and a well-graded wildcard is born as a new cluster with
optimistic priors — this is how the system escapes its seed set.
"""
from __future__ import annotations

import random

from . import db as db_mod
from . import embed as embed_mod
from .project import slugify


def wildcard_candidates(conn, cfg: dict) -> list:
    """Ready cards ranked by distance from the profile vector; the bottom
    quartile of similarity is the wildcard pool. No profile yet -> all."""
    prof = conn.execute("SELECT vector FROM profile WHERE id = 1").fetchone()
    pv = db_mod.unpack_vec(prof["vector"]) if prof else None
    cards = conn.execute(
        """SELECT c.*, cl.label AS cluster_label
           FROM cards c JOIN clusters cl ON cl.id = c.cluster_id
           WHERE c.status = 'ready' ORDER BY c.id"""
    ).fetchall()
    if pv is None:
        return list(cards)
    scored = []
    for c in cards:
        cv = db_mod.unpack_vec(c["embedding"])
        sim = embed_mod.cosine(cv, pv) if cv else 0.5
        scored.append((sim, c))
    scored.sort(key=lambda t: t[0])
    return [c for _, c in scored[: max(1, len(scored) // 4)]]


def pick_wildcard(conn, cfg: dict):
    """Serve one card from the wildcard pool. Exempt from profile ranking,
    NOT exempt from validation (all stored cards already passed it)."""
    pool = wildcard_candidates(conn, cfg)
    if not pool:
        return None
    row = random.choice(pool)
    conn.execute("UPDATE cards SET is_wildcard = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    return row


def arm_birth(conn, cfg: dict, card_row) -> int | None:
    """Wildcard card graded >= 3 or more_like_this -> new cluster with
    optimistic priors (alpha=2.0, beta=1.0), centroid = card embedding,
    card moved into it. Idempotent per card via birth_card_id."""
    existing = conn.execute(
        "SELECT id FROM clusters WHERE birth_card_id = ?", (card_row["id"],)
    ).fetchone()
    if existing is not None:
        return existing["id"]
    vec = db_mod.unpack_vec(card_row["embedding"])
    if vec is None:
        return None
    label = slugify(card_row["title"])[:48]
    cur = conn.execute(
        """INSERT INTO clusters (label, centroid, alpha, beta, is_seed,
                                 birth_card_id, created_at, last_updated)
           VALUES (?, ?, 2.0, 1.0, 0, ?, ?, ?)""",
        (label, db_mod.pack_vec(vec), card_row["id"], db_mod.utcnow(), db_mod.utcnow()),
    )
    conn.execute("UPDATE cards SET cluster_id = ? WHERE id = ?",
                 (cur.lastrowid, card_row["id"]))
    return cur.lastrowid