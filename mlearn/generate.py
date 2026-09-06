"""generate — one LLM call per item producing card + diagram + prompts
(spec 4.3), with the retry/validate loop (spec 4.2 validate) and the
Phase-2 pipeline: harvest -> dedupe -> generate -> validate -> enqueue.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import db as db_mod
from . import embed as embed_mod
from . import project as project_mod
from . import textutil
from . import validate as validate_mod
from . import infographic as infographic_mod

log = logging.getLogger("mlearn.generate")

MAX_BODY_WORDS_FOR_LLM = 2500
MAX_RETRIES = 3
JSON_LIMIT = 3000

TOPIC_GUARDRAILS = {
    "mental_health": (
        "Topic mental_health — MECHANISM AND RESEARCH ONLY: explain mechanisms and evidence "
        "(how sleep debt affects affect regulation, what a study found, how it was designed). "
        "NEVER prescriptive (no 'do X to fix your anxiety'), never framed as addressing the "
        "reader's own condition, never diagnostic. If the article can only be rendered as "
        "advice, refuse it."
    ),
    "finance": (
        "Topic finance — how instruments, markets, and mechanisms work. NEVER buy/sell/allocate "
        "guidance, never specific-security recommendations, no performance projections."
    ),
    "self_improvement": (
        "Topic self_improvement — require a study, dataset, or primary account in the article. "
        "Reject anything whose substance is exhortation (mere encouragement)."
    ),
    "technology": "Topic technology — explain the mechanism of the technology: how it works, why.",
    "innovation": "Topic innovation — explain the mechanism behind the innovation or idea.",
    "psychology": (
        "Topic psychology — how the mind works: mechanisms, biases, effects, and their "
        "evidence. NEVER diagnostic, NEVER prescriptive: no therapy guidance, no coping "
        "advice, no 'this means you' claims. Describe the phenomenon and the studies."
    ),
}

SYSTEM = """You are the content engine of a personal microlearning system. You transform one
source article into exactly one visual 5-minute learning card. Respond with STRICT JSON
only — no prose, no markdown fences, no commentary.

The JSON must have exactly these keys:
{
  "title": "short, concrete title",
  "hook": "1-2 sentences on why this matters",
  "body_md": "200-500 words, pyramid style, easy language",
  "diagram_type": "concept" or "data",
  "diagram_src": "one mermaid diagram, parse-valid syntax (or empty string)",
  "infographic_spec": "optional AntV infographic spec (preferred infographic lane)",
  "infographic_svg": "optional hand-written SVG infographic (fallback lane only)",
  "figures": [{"value": <number>, "source": "<verbatim span from the article>"}],
  "anchor_quote": "verbatim span from the article, max 25 words",
  "prompts": [{"question": "...", "answer": "..."}]
}

VISUAL RULE — one hero visual per card, your choice:
- Prefer a mermaid diagram (diagram_src) when the card is about a mechanism,
  structure, flow, taxonomy, or process — the diagram carries the core idea alone.
- For numbers, stat-contrasts, steps, or comparisons, prefer an INFOGRAPHIC via
  infographic_spec — an AntV declarative spec (rendered as a wide banner by the
  engine; tight layout guaranteed). Choose the TEMPLATE to match the content's
  structure (AntV design guide):
    list-grid-simple / list-column-done-list / list-pyramid-badge-card — modular
      lists, pyramids, levels
    sequence-steps-badge-card / sequence-timeline-plain-text — flows, steps, timelines
    compare-binary-horizontal-simple-fold / compare-swot — contrasts, pros/cons
    chart-column-simple / chart-bar-plain-text — plain quantitative comparison
    relation-network-simple-circle-node — hub or intersecting relations
  DATA best practices (AntV infographic-design guide):
    - ONE message per item. Label = the headline fact (a number or 1-3 words);
      desc = the plain explanation. NEVER repeat the label's numbers in desc.
    - Hero statistic FIRST (visual hierarchy), then supporting items.
    - Parallel phrasing across same-level items ('92% of…', '~28% of…').
    - 3-6 items, short title, terse text everywhere (banner space is tight).
    - Neutral factual tone: no metaphors, emotions, or cultural references.
  Valid example:
    infographic list-grid-simple
    data
      title Match the market, don't beat it
      lists
        - label 92%
          desc of active funds underperformed the S&P 500 over 15 years
        - label ~28%
          desc of lifetime returns eaten by a 1% annual fee
  Keep labels ultra-short and desc under 25 words. If spec render fails it is
  fed back to you — then fix the shape or fall back to infographic_svg.
- infographic_svg is the FALLBACK lane (only when a spec is not practical):
  hand-written self-contained SVG, NO external fonts/scripts/images,
  NO foreignObject. Dark theme, viewBox about 800x520, crisp short text in a
  sans-serif stack, generous padding, a headline, ONE big highlighted number
  or statistic, 3-4 compact blocks/steps, a one-line takeaway banner, and
  content that fills the canvas (no blank band at the bottom).
- The remaining visual budget (optional): inline ```mermaid fences inside body_md
  wherever a small diagram aids a section — as many as you see fit, each small
  (<= 10 lines).
- diagram_src may be an empty string when infographic_svg is present. At least one
  of diagram_src / infographic_svg / an inline mermaid fence must exist.

Hard rules:
- anchor_quote MUST appear character-for-character in the article text I give you, and be
  at most 25 words. Without a verbatim anchor the card is rejected.
- body_md must be between 200 and 500 words — short, dense, scannable. Count your words
  before finishing; a 700-word essay is an automatic rejection.
- PYRAMID PRINCIPLE: open body_md with the single key takeaway as the first sentence —
  the conclusion up front. Then support it with a few logical steps or mechanisms in
  order, evidence or concrete numbers last.
- SIMPLE IDEAS: the takeaway must be a simple, transferable idea — one plain sentence
  anyone could repeat. Favor timeless concepts, models, frameworks, and heuristics over
  newsy specifics. Strip the article down to the idea that keeps its value in a year.
- FRAMEWORKS AND MODELS (layers, stages, types, steps): enumerate the full structure
  explicitly — all 7 OSI layers, every stage, every component — one line per item, in
  order. The mermaid diagram (flowchart/stateDiagram-v2/mindmap) must show the ENTIRE
  structure, not a fragment. Recall prompts must probe the structure itself (e.g. "name
  the layers in order").
- EASY LANGUAGE: sentences of at most 20 words, everyday words, active voice. Define any
  jargon inline the first time. Use concrete examples and numbers from the article.
  One idea per paragraph.
- SCANNABLE FORMATTING (digestion over prose): structure body_md like a well-laid-out
  note, not an essay — proper markdown headings (h2/h3, never h1) to separate sections;
  numbered or bullet lists for steps, stages, and enumerations; a markdown table when
  comparing 2-4 options or dimensions (columns such as mechanism | why it works |
  example); bold the 2-4 key terms and italicize the takeaway. Walls of plain prose are
  an automatic rejection. Structure must not bloat the card: keep the 200-500 word
  budget and the pyramid principle.
- mini-app renders markdown: headings, lists, tables, bold/italic, and the mermaid
  diagram all render natively — use them.
- Audience: a busy professional reading for 5 minutes in English as a second language.
  The card must be scannable in 90 seconds and still leave the mechanism understood.
- Never invent numbers. No headings above h3.
- The hook is the bottom line: the takeaway in one or two sentences, no hedging.
- prompts must probe the takeaway and the key mechanism steps — never trivia.
- title: short, concrete, benefit-oriented.
- diagram_type='data' REQUIRES figures populated with numbers that all appear verbatim
  somewhere in the article, each with its exact source span. If the article has no usable
  numbers, use a concept diagram.
- The diagram must carry the core idea on its own: a reader who sees only the diagram gets
  the point. Prefer flowchart, sequenceDiagram, stateDiagram-v2, mindmap for concepts,
  xychart-beta or pie ONLY when the article itself provides the numbers.
- DIAGRAM QUALITY: the diagram must be SIMPLE and VERY UNDERSTANDABLE. At most 10 lines
  of mermaid source, at most 8 nodes. Short labels (at most 4 words). One clear visual
  idea per diagram: step order, layer stack, state loop, or tree. For frameworks show the
  ENTIRE structure (all 7 OSI layers / every stage), for mechanisms show the minimal loop.
  Never decorate: no subgraphs, no fancy syntax, no long sentences inside nodes. If you
  cannot express the idea in 10 lines, simplify the idea first.
- prompts: 2-4 recall questions, each answerable from body_md alone, each ending with '?'.

{TGUARD}

If the article violates the topic rule above, or you cannot produce a card that honors
every hard rule, respond with {"error": "short reason"} instead of a broken card."""

USER = """Article: {title}
URL: {url}
Topic: {topic}
Audience: busy professional, 5 minutes, English as a second language.

ARTICLE TEXT (verbatim excerpt):
{body}
"""

RETRY_USER = """Your previous attempt failed validation. Fix EXACTLY these issues and
resubmit the full card JSON (same schema). Do not change anything else needlessly.

Issues:
{errors}

Previous attempt:
{previous}
"""


def build_system(topic: str) -> str:
    guard = TOPIC_GUARDRAILS.get(topic, "")
    return SYSTEM.replace("{TGUARD}", guard)


def call_llm(cfg: dict, system: str, user: str) -> str | None:
    gen = cfg["generate"]
    key = os.environ.get(gen.get("api_key_env", "OPENROUTER_API_KEY"), "")
    url = gen["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": gen["model"],
        "temperature": gen.get("temperature", 0.4),
        "max_tokens": JSON_LIMIT,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    effort = gen.get("reasoning_effort")
    if effort:
        # DeepSeek-family reasoning: send both accepted spellings (the local
        # OpenAI-compatible gateway tolerates both; one is honored).
        payload["reasoning_effort"] = effort
        payload["reasoning"] = {"effort": effort}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=360) as client:
        r = client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    return data["choices"][0]["message"]["content"]


def parse_json(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        if t.startswith("json"):
            t = t[4:]
        t = t.rsplit("```", 1)[0].strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start, end = t.find("{"), t.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(t[start:end + 1])
        except Exception:
            return None
    return None


def item_text(raw_path: str | None, title: str | None) -> str:
    if raw_path and Path(raw_path).is_file():
        html = Path(raw_path).read_text(errors="replace")
        text = textutil.strip_html(html)
        if text:
            return text
    return title or ""


def apply_infographic_lane(card: dict, tools_dir: str | Path) -> list[str]:
    """Materialize the infographic lane for a parsed card.

    infographic_spec (AntV DSL) wins: rendered to SVG via node and stored as
    infographic_svg. Errors are returned for the retry loop; on success the
    caller should validate with infographic_strict=False (banner aspect)."""
    spec = card.get("infographic_spec")
    if not spec or not str(spec).strip():
        return []
    ok, err = infographic_mod.spec_valid(str(spec))
    if not ok:
        return [f"infographic spec invalid: {err}"]
    svg, rerr = infographic_mod.render_spec(str(spec), tools_dir)
    if svg is None:
        return [f"infographic spec render failed: {rerr}"]
    card["infographic_svg"] = svg
    card["_infographic_lane"] = "antv"
    return []


def generate_card(cfg: dict, topic: str, title: str, url: str, body: str,
                  attempts: int = MAX_RETRIES) -> tuple[dict | None, list[str]]:
    """LLM card generation with validation-retry loop.
    Returns (card dict, reasons); reasons = [] on success."""
    system = build_system(topic)
    user = USER.format(title=title, url=url, topic=topic, body=body)
    previous = None
    errors: list[str] = []
    reasons: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            if previous:
                raw = call_llm(cfg, system, RETRY_USER.format(errors=errors, previous=json.dumps(previous)))
            else:
                raw = call_llm(cfg, system, user)
        except Exception as e:
            log.warning("llm call failed (attempt %d): %s", attempt, e)
            reasons.append(f"attempt {attempt}: llm call failed ({type(e).__name__})")
            time.sleep(2 * attempt)
            continue
        if raw is None:
            reasons.append(f"attempt {attempt}: no response")
            continue
        card = parse_json(raw)
        if card is None:
            reasons.append(f"attempt {attempt}: not valid JSON")
            continue
        if "error" in card and len(card) < 4:
            reasons.append(f"model refused: {card['error']}")
            return None, reasons
        card["cluster"] = topic
        card["source_url"] = url
        card["figures_json"] = json.dumps(card.get("figures") or [])
        lane_errors = apply_infographic_lane(card, Path(cfg.get("_base_dir", ".")) / "tools")
        if lane_errors:
            reasons.append(f"attempt {attempt}: " + "; ".join(lane_errors[:2]))
            log.warning("attempt %d infographic lane: %s", attempt, lane_errors[:2])
            previous = {k: v for k, v in card.items() if k != "source_body"}
            continue
        ok, errors = validate_mod.validate_card(
            card, body, Path(cfg.get("_base_dir", ".")) / "tools",
            infographic_strict=card.get("_infographic_lane") != "antv",
        )
        if ok:
            return card, []
        reasons.append(f"attempt {attempt}: " + "; ".join(errors[:4]))
        log.warning("attempt %d failed validation: %s", attempt, errors[:3])
        previous = {k: v for k, v in card.items() if k != "source_body"}
    return None, reasons


def run_generation(conn, cfg: dict, count: int, do_harvest: bool = True,
                   regenerate: bool = False) -> dict:
    """Phase-2 pipeline: dedupe -> generate -> validate -> enqueue.
    Allocation: round-robin over seed topics (bandit arrives in Phase 4).
    regenerate=True: archive the ready pool and re-roll it (prompt/style
    changes; old cards stay in the DB as history via status='archived').

    A generation lock (fcntl flock) prevents concurrent runs (e.g. the
    hourly tick racing a manual run): two pipelines loading their dedupe
    pools simultaneously can both insert a card for the same item."""
    import fcntl

    lock_path = Path(cfg["paths"]["data_dir"]) / "generate.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fh.close()
        log.warning("generation already running (lock held); skipping")
        return {"made": 0, "skipped_dupes": 0, "failed": 0, "card_ids": [],
                "projected": 0, "locked": True}
    try:
        return _run_generation_locked(conn, cfg, count, do_harvest, regenerate)
    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


def _run_generation_locked(conn, cfg: dict, count: int, do_harvest: bool,
                           regenerate: bool) -> dict:
    from . import harvest as harvest_mod

    log_path = Path(cfg["paths"]["data_dir"]) / "logs" / "generate.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a")
    def note(msg: str) -> None:
        log_fh.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")
        log_fh.flush()

    if do_harvest:
        r = harvest_mod.harvest(conn, cfg)
        note(f"harvest: {r}")

    if regenerate:
        rows = conn.execute(
            """SELECT c.id AS card_id, i.id AS item_id FROM cards c
               JOIN items i ON i.id = c.item_id WHERE c.status = 'ready'"""
        ).fetchall()
        for r in rows:
            conn.execute("UPDATE cards SET status = 'archived' WHERE id = ?", (r["card_id"],))
            conn.execute("UPDATE items SET processed = 0 WHERE id = ?", (r["item_id"],))
        conn.commit()
        note(f"regenerate: archived {len(rows)} ready cards for re-roll")

    pool = embed_mod.card_pool(conn)
    threshold = cfg["dedupe_threshold"]
    topics = db_mod.SEED_TOPICS
    made = skipped = failed = 0
    cards_out = []
    wc_done = False  # one wildcard slot per run (bubble counter)
    wc_rate = cfg.get("wildcard_rate", 0.15)
    # Phase 2: round-robin over topics.
    for round_i in range(1000):
        if made >= count:
            break
        for topic in topics:
            if made >= count:
                break
            item = None
            text = ""
            vec: list[float] | None = None
            is_wc = False
            # Acquisition 1: wildcard slot — once per run, touch the LEAST
            # covered topic (no taste involvement whatsoever), so unheard
            # lanes keep appearing and the filter bubble stays porous.
            # Liking the generated card later births a new cluster
            # (novelty.arm_birth), which is how new topics are born.
            if not wc_done and random.random() < wc_rate:
                least = conn.execute(
                    """SELECT s.topic AS topic, COUNT(c.id) AS n
                       FROM sources s
                       JOIN items i ON i.source_id = s.id AND i.processed = 0
                       LEFT JOIN cards c ON c.item_id = i.id AND c.status != 'archived'
                       GROUP BY s.topic ORDER BY n ASC, s.topic LIMIT 1"""
                ).fetchone()
                if least is not None:
                    wc_item = conn.execute(
                        """SELECT i.* FROM items i
                           JOIN sources s ON s.id = i.source_id
                           WHERE i.processed = 0 AND s.topic = ?
                           ORDER BY i.id LIMIT 1""", (least["topic"],),
                    ).fetchone()
                    if wc_item is not None:
                        txt = item_text(wc_item["raw_path"], wc_item["title"])
                        if len(txt) >= 120:
                            item = wc_item
                            text = txt
                            vec = embed_mod.embed_one(wc_item["title"] + ". " + txt[:1400])
                            is_wc = True
                            wc_done = True
                            note(f"wildcard acquisition: {wc_item['title']!r} "
                                 f"(topic={least['topic']}, coverage={least['n']})")
            strength = cfg.get("taste_strength", 0.0)
            if item is None and strength > 0:
                # acquisition: embedding-level taste decides WHICH unprocessed
                # item becomes a card; send order stays FIFO (next_cards)
                cands = conn.execute(
                    """SELECT i.* FROM items i
                       JOIN sources s ON s.id = i.source_id
                       WHERE i.processed = 0 AND s.topic = ?
                       ORDER BY i.id""",
                    (topic,),
                ).fetchall()
                scored: list[tuple[dict, list[float] | None, str]] = []
                for it in cands:
                    txt = item_text(it["raw_path"], it["title"])
                    if len(txt) < 120:
                        conn.execute("UPDATE items SET processed = 1 WHERE id = ?", (it["id"],))
                        conn.commit()
                        skipped += 1
                        continue
                    vec = embed_mod.embed_one(it["title"] + ". " + txt[:1400])
                    scored.append((it, vec, txt))
                if scored:
                    from . import taste as taste_mod
                    boosts = taste_mod.score_vectors(
                        [(it["id"], vec) for it, vec, _ in scored], conn, strength)
                    scored.sort(key=lambda kv: (-boosts.get(kv[0]["id"], 0.0), kv[0]["id"]))
                    item, vec, text = scored[0]
            else:
                item = conn.execute(
                    """SELECT i.* FROM items i
                       JOIN sources s ON s.id = i.source_id
                       WHERE i.processed = 0 AND s.topic = ?
                       ORDER BY i.id LIMIT 1""",
                    (topic,),
                ).fetchone()
                if item is None:
                    continue
                text = item_text(item["raw_path"], item["title"])
                if len(text) < 120:
                    conn.execute("UPDATE items SET processed = 1 WHERE id = ?", (item["id"],))
                    conn.commit()
                    skipped += 1
                    continue
                vec = embed_mod.embed_one(item["title"] + ". " + text[:1400])
            if item is None:
                continue
            dup = embed_mod.similar_to_pool(vec, pool, threshold) if vec else None
            if dup is not None:
                conn.execute("UPDATE items SET processed = 1 WHERE id = ?", (item["id"],))
                conn.commit()
                skipped += 1
                note(f"dedupe: {item['title']!r} ~ card {dup[0]} ({dup[1]:.3f})")
                continue
            body_for_llm = " ".join(text.split())
            body_for_llm = " ".join(body_for_llm.split()[:MAX_BODY_WORDS_FOR_LLM])
            card, reasons = generate_card(cfg, topic, item["title"] or "", item["url"], body_for_llm)
            if card is None:
                conn.execute("UPDATE items SET processed = 1 WHERE id = ?", (item["id"],))
                conn.commit()
                failed += 1
                detail = " | ".join(reasons[-3:]) if reasons else "unknown"
                note(f"DROP (3 attempts): {item['url']} :: {detail}")
                continue
            card_id = db_mod.insert_card(
                conn, item_id=item["id"], cluster_label=topic,
                title=card["title"], hook=card["hook"], body_md=card["body_md"],
                diagram_type=card["diagram_type"], diagram_src=card["diagram_src"],
                infographic_svg=card.get("infographic_svg"),
                figures_json=card["figures_json"], source_url=item["url"],
                anchor_quote=card["anchor_quote"],
                embedding=embed_mod.pack(vec) if vec else None,
                prompts=card["prompts"], is_wildcard=is_wc,
            )
            conn.execute("UPDATE items SET processed = 1 WHERE id = ?", (item["id"],))
            conn.commit()
            made += 1
            if vec:
                pool.append((card_id, vec))
            cards_out.append(card_id)
            note(f"CARD {card_id}: {card['title']} (topic={topic})")
        if conn.execute("SELECT COUNT(*) n FROM items WHERE processed = 0").fetchone()["n"] == 0:
            break
    log_fh.close()
    written = project_mod.write_cards(conn, cfg["paths"]["cards_dir"])
    return {"made": made, "skipped_dupes": skipped, "failed": failed, "card_ids": cards_out, "projected": len(written)}