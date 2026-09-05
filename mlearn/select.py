"""Selector — bandit discovery, FSRS retention, interleave (spec 6).

Two independent loops share the card store, interleaved at a configurable
ratio. Serving a card sets status='served' and creates FSRS entries for its
prompts with due_at = now + 1 day (spec 6.5).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from fsrs import Card as FCard
from fsrs import Rating, Scheduler, State

from . import db as db_mod
from . import embed as embed_mod

RATING_BY_GRADE = {1: Rating.Again, 2: Rating.Hard, 3: Rating.Good, 4: Rating.Easy}
SIGNAL_EFFECTS = {
    "more_like_this": ("alpha", 1.5),
    "less_like_this": ("beta", 1.5),
    "skip": ("beta", 0.3),
    "opened_source": ("alpha", 0.5),
}

_scheduler = Scheduler()


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00") if dt else None


def _parse(dt_s: str | None) -> datetime | None:
    if not dt_s:
        return None
    try:
        return datetime.fromisoformat(dt_s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_fsrs_card(p) -> FCard:
    """Map a prompts row to an fsrs Card. State is not persisted in the schema,
    so we derive it: never reviewed -> New, otherwise Review."""
    last = _parse(p["last_review"])
    due = _parse(p["due_at"])
    if last is None:
        c = FCard(state=State.Learning)
        c.due = due or (now() + timedelta(days=1))
        return c
    return FCard(
        state=State.Review,
        stability=p["stability"] or None,
        difficulty=p["difficulty"] or None,
        due=due,
        last_review=last,
    )


# ── feedback → state (spec 6.3) ─────────────────────────────────────────────

def _update_profile(conn, cfg: dict, card_row, apply: bool) -> None:
    if not apply:
        return
    card_vec = db_mod.unpack_vec(card_row["embedding"])
    if not card_vec:
        return
    lam = cfg["profile_lambda"]
    prof = conn.execute("SELECT vector FROM profile WHERE id = 1").fetchone()
    cur = db_mod.unpack_vec(prof["vector"]) if prof and prof["vector"] else None
    if cur is None:
        new_vec = card_vec
    else:
        new_vec = [(1 - lam) * a + lam * b for a, b in zip(cur, card_vec)]
    conn.execute("UPDATE profile SET vector = ?, updated_at = ? WHERE id = 1",
                 (db_mod.pack_vec(new_vec), _iso(now())))


def grade_prompt(conn, cfg: dict, prompt_id: int, grade: int,
                 latency_ms: int | None = None) -> dict:
    """The only external write path (C2). FSRS update + grade row + bandit + EMA."""
    grade = int(grade)
    if grade not in RATING_BY_GRADE:
        raise ValueError(f"grade must be 1-4, got {grade}")
    p = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
    if p is None:
        raise KeyError(f"no prompt {prompt_id}")
    card_row = conn.execute("SELECT * FROM cards WHERE id = ?", (p["card_id"],)).fetchone()

    updated, _log = _scheduler.review_card(_to_fsrs_card(p), RATING_BY_GRADE[grade])
    reps = (p["reps"] or 0) + 1
    lapses = (p["lapses"] or 0) + (1 if grade == 1 else 0)

    conn.execute(
        "INSERT INTO grades (prompt_id, card_id, grade, latency_ms, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (prompt_id, p["card_id"], grade, latency_ms, _iso(now())),
    )
    conn.execute(
        """UPDATE prompts SET stability = ?, difficulty = ?, reps = ?, lapses = ?,
                             due_at = ?, last_review = ? WHERE id = ?""",
        (updated.stability, updated.difficulty, reps, lapses,
         _iso(updated.due), _iso(now()), prompt_id),
    )

    cluster = conn.execute("SELECT * FROM clusters WHERE id = ?",
                           (card_row["cluster_id"],)).fetchone()
    alpha, beta = float(cluster["alpha"]), float(cluster["beta"])
    if grade == 1:
        beta += 1.0
    elif grade == 2:
        beta += 0.5
    elif grade == 3:
        alpha += 1.0
    elif grade == 4:
        alpha += 0.7
    conn.execute("UPDATE clusters SET alpha = ?, beta = ?, last_updated = ? WHERE id = ?",
                 (alpha, beta, _iso(now()), cluster["id"]))
    _update_profile(conn, cfg, card_row, apply=grade >= 3)
    if card_row["is_wildcard"] and grade >= 3:
        from . import novelty as novelty_mod
        new_cluster = novelty_mod.arm_birth(conn, cfg, card_row)
        if new_cluster is not None:
            conn.execute("UPDATE cards SET is_wildcard = 0 WHERE id = ?", (card_row["id"],))
    conn.commit()

    return {
        "prompt_id": prompt_id,
        "grade": grade,
        "due_at": _iso(updated.due),
        "stability": updated.stability,
        "difficulty": updated.difficulty,
        "reps": reps,
        "lapses": lapses,
        "cluster": cluster["label"],
        "alpha": alpha,
        "beta": beta,
    }


def signal(conn, cfg: dict, card_id: int, kind: str) -> dict:
    """Explicit taste signals, separate from grades (spec 3 signals)."""
    if kind not in SIGNAL_EFFECTS:
        raise ValueError(f"kind must be one of {sorted(SIGNAL_EFFECTS)}, got {kind!r}")
    card_row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if card_row is None:
        raise KeyError(f"no card {card_id}")
    field, amount = SIGNAL_EFFECTS[kind]
    conn.execute("INSERT INTO signals (card_id, kind, created_at) VALUES (?, ?, ?)",
                 (card_id, kind, _iso(now())))
    cluster = conn.execute("SELECT * FROM clusters WHERE id = ?",
                           (card_row["cluster_id"],)).fetchone()
    alpha, beta = float(cluster["alpha"]), float(cluster["beta"])
    if field == "alpha":
        alpha += amount
    else:
        beta += amount
    conn.execute("UPDATE clusters SET alpha = ?, beta = ?, last_updated = ? WHERE id = ?",
                 (alpha, beta, _iso(now()), cluster["id"]))
    _update_profile(conn, cfg, card_row, apply=(kind == "more_like_this"))
    if card_row["is_wildcard"] and kind == "more_like_this":
        from . import novelty as novelty_mod
        new_cluster = novelty_mod.arm_birth(conn, cfg, card_row)
        if new_cluster is not None:
            conn.execute("UPDATE cards SET is_wildcard = 0 WHERE id = ?", (card_row["id"],))
    conn.commit()
    return {"card_id": card_id, "kind": kind, "cluster": cluster["label"],
            "alpha": alpha, "beta": beta}


# ── allocation (spec 6.2 / Phase 4 Thompson; Phase 3 round-robin) ───────────

def thompson_allocation(conn, exploration_floor: float) -> dict[int, float]:
    """Sample each cluster's Beta(alpha, beta); keep >= floor probability.
    Floors are preserved by shrinking only the above-floor mass when the
    floored distribution overflows 1.0 (renormalizing everything would push
    floored items back under the floor)."""
    clusters = conn.execute("SELECT * FROM clusters").fetchall()
    if not clusters:
        return {}
    n = len(clusters)
    floor = min(exploration_floor, 1.0 / n if n else 1.0)
    samples = [random.betavariate(max(float(c["alpha"]), 1e-9),
                                  max(float(c["beta"]), 1e-9)) for c in clusters]
    total = sum(samples) or 1.0
    probs = [max(floor, s / total) for s in samples]
    excess = sum(probs) - 1.0
    if excess > 1e-9:
        above = [(i, p - floor) for i, p in enumerate(probs) if p > floor]
        above_mass = sum(d for _, d in above)
        if above_mass > 0:
            shrink = (above_mass - excess) / above_mass  # remove exactly `excess`
            for i, d in above:
                probs[i] = floor + d * shrink
    return {c["id"]: p for c, p in zip(clusters, probs)}


def _pick_ready_card(conn, policy: str, weights: dict[int, float] | None,
                     exclude: set[int] | None = None):
    """One ready card per allocation policy, excluding already-picked ids."""
    exclude = exclude or set()
    ready = conn.execute(
        "SELECT c.* FROM cards c WHERE c.status = 'ready' ORDER BY c.id"
    ).fetchall()
    ready = [c for c in ready if c["id"] not in exclude]
    if not ready:
        return None
    if policy == "round-robin":
        return ready[0]
    w: dict[int, float] = weights or {}
    bucket: dict[int, list] = {}
    for c in ready:
        bucket.setdefault(c["cluster_id"], []).append(c)
    remaining = list(w)
    while remaining:
        cid = random.choices(remaining, weights=[w[k] for k in remaining], k=1)[0]
        if bucket.get(cid):
            return bucket[cid][0]
        remaining.remove(cid)
    return ready[0]


# ── serving (spec 6.5) ──────────────────────────────────────────────────────

def _payload(conn, card_row, kind: str, prompts) -> dict:
    return {
        "kind": kind,
        "card_id": card_row["id"],
        "title": card_row["title"],
        "topic": card_row["cluster_label"] if "cluster_label" in card_row.keys() else None,
        "hook": card_row["hook"],
        "body_md": card_row["body_md"],
        "diagram_type": card_row["diagram_type"],
        "diagram_src": card_row["diagram_src"],
        "source_url": card_row["source_url"],
        "anchor_quote": card_row["anchor_quote"],
        "prompts": [{"prompt_id": p["id"], "question": p["question"],
                     "answer": p["answer"]} for p in prompts],
    }


def next_cards(conn, cfg: dict, count: int) -> dict:
    """Interleaved list: overdue retention first, else discovery ratio from
    ready cards, remainder from due prompts. Enforces daily_cap."""
    count = max(1, int(count))
    today = now().strftime("%Y-%m-%d")
    served_today = conn.execute(
        "SELECT COUNT(*) n FROM cards WHERE served_at LIKE ?", (today + "%",)
    ).fetchone()["n"]
    if served_today >= cfg["daily_cap"]:
        return {"cards": [], "reason": f"daily cap {cfg['daily_cap']} reached",
                "served_today": served_today}
    allowed = min(count, cfg["daily_cap"] - served_today)
    out: list[dict] = []

    cutoff = _iso(now() - timedelta(days=1))
    overdue = conn.execute(
        """SELECT pr.*, c.* FROM prompts pr JOIN cards c ON c.id = pr.card_id
           WHERE pr.due_at < ? AND c.status = 'served'
           ORDER BY pr.due_at LIMIT ?""",
        (cutoff, allowed),
    ).fetchall()
    if overdue:
        for row in overdue:
            prompts = [row]
            out.append(_payload(conn, row, "retention", prompts))
        for i, row in enumerate(overdue[:1]):
            conn.execute("UPDATE prompts SET last_review = ? WHERE id = ?",
                         (_iso(now()), row["id"]))
        conn.commit()
        return {"cards": out, "reason": "overdue retention", "served_today": served_today}

    # Discovery: ready cards under the discovery ratio, then due prompts.
    allowed = min(count, cfg["daily_cap"] - served_today)
    disc_slots = max(1, round(allowed * cfg["discovery_ratio"]))
    policy = cfg.get("allocation_policy", "round-robin")
    weights = thompson_allocation(conn, cfg["exploration_floor"]) if policy == "thompson" else None
    served_cards: list[int] = []
    prompts_to_create: list[tuple[int, str | None]] = []
    wc_picked = False
    for _ in range(disc_slots):
        card_row = None
        if (not wc_picked and random.random() < cfg["wildcard_rate"]
                and conn.execute("SELECT COUNT(*) n FROM cards WHERE status = 'ready'"
                                 ).fetchone()["n"] >= cfg.get("wildcard_min_ready", 6)):
            from . import novelty as novelty_mod
            card_row = novelty_mod.pick_wildcard(conn, cfg)
            if card_row is not None:
                wc_picked = True
        if card_row is None:
            card_row = _pick_ready_card(conn, policy, weights)
        if card_row is None:
            break
        cluster_label = conn.execute("SELECT label FROM clusters WHERE id = ?",
                                     (card_row["cluster_id"],)).fetchone()["label"]
        card_dict = dict(card_row)
        card_dict["cluster_label"] = cluster_label
        prompts = conn.execute(
            "SELECT * FROM prompts WHERE card_id = ? ORDER BY id", (card_row["id"],)
        ).fetchall()
        out.append(_payload(conn, card_dict, "discovery", prompts))
        served_cards.append(card_row["id"])
        for p in prompts:
            if p["due_at"] is None:
                prompts_to_create.append((p["id"], _iso(now() + timedelta(days=1))))
        if len(out) >= allowed:
            break

    # Fill remainder from due prompts (due <= now). If none are due, extra
    # discovery slots take the unused allowance so a request never returns
    # fewer cards than the remaining daily budget allows.
    remaining = allowed - len(out)
    if remaining > 0:
        due_prompts = conn.execute(
            """SELECT pr.*, c.* FROM prompts pr JOIN cards c ON c.id = pr.card_id
               WHERE pr.due_at IS NOT NULL AND pr.due_at <= ? AND c.status = 'served'
               ORDER BY pr.due_at LIMIT ?""",
            (_iso(now()), remaining),
        ).fetchall()
        for row in due_prompts:
            out.append(_payload(conn, row, "retention", [row]))
            remaining -= 1
    if remaining > 0:
        picked = {c["card_id"] for c in out}
        for _ in range(remaining):
            card_row = _pick_ready_card(conn, policy, weights, exclude=picked)
            if card_row is None:
                break
            picked.add(card_row["id"])
            cluster_label = conn.execute("SELECT label FROM clusters WHERE id = ?",
                                         (card_row["cluster_id"],)).fetchone()["label"]
            card_dict = dict(card_row)
            card_dict["cluster_label"] = cluster_label
            prompts = conn.execute(
                "SELECT * FROM prompts WHERE card_id = ? ORDER BY id", (card_row["id"],)
            ).fetchall()
            out.append(_payload(conn, card_dict, "discovery", prompts))
            served_cards.append(card_row["id"])
            for p in prompts:
                if p["due_at"] is None:
                    prompts_to_create.append((p["id"], _iso(now() + timedelta(days=1))))
    for cid in served_cards:
        conn.execute("UPDATE cards SET status = 'served', served_at = ? WHERE id = ?",
                     (_iso(now()), cid))
        src = conn.execute(
            """SELECT s.id FROM sources s
               JOIN items i ON i.source_id = s.id
               JOIN cards c ON c.item_id = i.id
               WHERE c.id = ?""", (cid,)
        ).fetchone()
        if src:
            conn.execute("UPDATE sources SET cards_served = cards_served + 1 WHERE id = ?",
                         (src["id"],))
    for pid, due in prompts_to_create:
        conn.execute("UPDATE prompts SET due_at = ? WHERE id = ? AND due_at IS NULL", (due, pid))
    conn.commit()
    return {"cards": out, "reason": "discovery + due", "served_today": served_today}


# ── weekly taste decay (spec 6.3) ───────────────────────────────────────────

def decay_clusters(conn, cfg: dict, older_than_days: int = 7) -> dict:
    """alpha = 1 + (alpha - 1) * decay_factor, same for beta. Without this,
    early preferences calcify and the feed stops moving."""
    cutoff = _iso(now() - timedelta(days=older_than_days))
    rows = conn.execute(
        "SELECT id, alpha, beta FROM clusters WHERE last_updated < ?", (cutoff,)
    ).fetchall()
    f = cfg["decay_factor"]
    for r in rows:
        conn.execute(
            "UPDATE clusters SET alpha = ?, beta = ?, last_updated = ? WHERE id = ?",
            (1 + (float(r["alpha"]) - 1) * f, 1 + (float(r["beta"]) - 1) * f,
             _iso(now()), r["id"]),
        )
    conn.commit()
    return {"decayed": len(rows)}


# ── search (spec 9.1) ───────────────────────────────────────────────────────

def search(conn, cfg: dict, query: str, limit: int = 10) -> list[dict]:
    vec = embed_mod.embed_one(query)
    if vec is None:
        return []
    pool = embed_mod.card_pool(conn)
    scored = [(cid, embed_mod.cosine(vec, other)) for cid, other in pool]
    scored.sort(key=lambda t: t[1], reverse=True)
    result = []
    for cid, score in scored[:limit]:
        row = conn.execute(
            """SELECT c.id, c.title, c.source_url, cl.label AS topic, c.anchor_quote
               FROM cards c JOIN clusters cl ON cl.id = c.cluster_id
               WHERE c.id = ?""", (cid,)
        ).fetchone()
        if row:
            result.append({**dict(row), "score": round(score, 4)})
    return result