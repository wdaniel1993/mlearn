"""generate — one LLM call per item producing card + diagram + prompts
(spec 4.3), with the retry/validate loop (spec 4.2 validate) and the
Phase-2 pipeline: harvest -> dedupe -> generate -> validate -> enqueue.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import db as db_mod
from . import embed as embed_mod
from . import project as project_mod
from . import textutil
from . import validate as validate_mod

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
}

SYSTEM = """You are the content engine of a personal microlearning system. You transform one
source article into exactly one visual 5-minute learning card. Respond with STRICT JSON
only — no prose, no markdown fences, no commentary.

The JSON must have exactly these keys:
{
  "title": "short, concrete title",
  "hook": "1-2 sentences on why this matters",
  "body_md": "600-900 words, markdown, no headings above h3, mechanism-first teaching",
  "diagram_type": "concept" or "data",
  "diagram_src": "one mermaid diagram, parse-valid syntax",
  "figures": [{"value": <number>, "source": "<verbatim span from the article>"}],
  "anchor_quote": "verbatim span from the article, max 25 words",
  "prompts": [{"question": "...", "answer": "..."}]
}

Hard rules:
- anchor_quote MUST appear character-for-character in the article text I give you, and be
  at most 25 words. Without a verbatim anchor the card is rejected.
- body_md must be between 600 and 900 words — roughly 5 to 7 full paragraphs of dense,
  mechanism-first teaching prose. Count your words before finishing; a 250-word body is
  an automatic rejection. Teach the mechanism — how and why it works, with the article's
  actual facts. No headings above h3. Never invent numbers.
- The diagram must carry the core idea on its own: a reader who sees only the diagram gets
  the point. Prefer flowchart, sequenceDiagram, stateDiagram-v2, mindmap for concepts,
  xychart-beta or pie ONLY when the article itself provides the numbers.
- diagram_type='data' REQUIRES figures populated with numbers that all appear verbatim
  somewhere in the article, each with its exact source span. If the article has no usable
  numbers, use a concept diagram.
- prompts: 2-4 recall questions, each answerable from body_md alone, each ending with '?'.

{TGUARD}

If the article violates the topic rule above, or you cannot produce a card that honors
every hard rule, respond with {"error": "short reason"} instead of a broken card."""

USER = """Article: {title}
URL: {url}
Topic: {topic}

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
        ok, errors = validate_mod.validate_card(card, body, Path(cfg.get("_base_dir", ".")) / "tools")
        if ok:
            return card, []
        reasons.append(f"attempt {attempt}: " + "; ".join(errors[:4]))
        log.warning("attempt %d failed validation: %s", attempt, errors[:3])
        previous = {k: v for k, v in card.items() if k != "source_body"}
    return None, reasons


def run_generation(conn, cfg: dict, count: int, do_harvest: bool = True) -> dict:
    """Phase-2 pipeline: dedupe -> generate -> validate -> enqueue.
    Allocation: round-robin over seed topics (bandit arrives in Phase 4)."""
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

    pool = embed_mod.card_pool(conn)
    threshold = cfg["dedupe_threshold"]
    topics = db_mod.SEED_TOPICS
    made = skipped = failed = 0
    cards_out = []
    # Phase 2: round-robin over topics.
    for round_i in range(1000):
        if made >= count:
            break
        for topic in topics:
            if made >= count:
                break
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
                figures_json=card["figures_json"], source_url=item["url"],
                anchor_quote=card["anchor_quote"],
                embedding=embed_mod.pack(vec) if vec else None,
                prompts=card["prompts"],
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