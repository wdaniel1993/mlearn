"""Concept prospecting: popular-science articles -> timeless ideas -> stable sources.

The LLM names the timeless idea behind recent pop-science articles. When an idea
has a Wikipedia article, that page is harvested into the item pool as a stable,
verifiable reference (anchor-able, robot-free, versioned). Discovery credit
(topic, scout counters) stays with the pop-science source that surfaced the idea.

Pipeline position: runs off the generation path (no lock) — the daily push runs
a small pass (default 5 articles); `mlearn prospect --count N` manually.
Every reviewed article id is recorded in data/prospect_state.json so nothing is
re-reviewed (the LLM judgment is the expensive part).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .generate import call_llm, parse_json
from .harvest import DELAY_SECONDS, _wiki_extract, _wiki_url
from .textutil import strip_html

log = __import__("logging").getLogger("mlearn.prospect")

# Pop-science sources whose articles may point at timeless ideas.
PROSPECT_SOURCES = [
    "Quanta Magazine",
    "Aeon",
    "Psyche",
    "Ness Labs",
    "PsyBlog",
    "Ars Technica",
    "MIT Technology Review",
    "IEEE Spectrum",
    "LessWrong",
]

PROSPECT_EXCERPT_WORDS = 1200  # words of article body shipped to the LLM per item

PROSPECT_SYSTEM = """You are the concept prospector of a personal microlearning engine.
A list of recent popular-science articles follows. For each, decide whether it teaches a
SIMPLE, TIMELESS, TRANSFERABLE idea — a model, mechanism, law, effect, bias, or pattern
that keeps its value for years (e.g. the OSI model, compound interest, the availability
heuristic). News-of-the-week specifics, product stories, and event coverage are NOT
timeless ideas.

Respond with STRICT JSON only, no prose:
{"items": [{"id": <article id>, "timeless": true/false, "idea": "one-sentence idea name",
"wiki_title": "exact Wikipedia article title" or null, "why": "one short reason"}]}
Set wiki_title to null when no Wikipedia article covers the idea well."""

def _state_path(cfg: dict) -> Path:
    return Path(cfg["paths"]["data_dir"]) / "prospect_state.json"


def _load_state(cfg: dict) -> dict:
    p = _state_path(cfg)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            pass
    return {"seen": []}


def _save_state(cfg: dict, state: dict) -> None:
    p = _state_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state))


def _excerpt(raw_path: str, words: int = PROSPECT_EXCERPT_WORDS) -> str:
    try:
        text = strip_html(Path(raw_path).read_text(errors="replace"))
    except OSError:
        return ""
    return " ".join(text.split()[:words])


def _insert_bridged(conn: sqlite3.Connection, cfg: dict, source_id: int,
                    title: str, text: str) -> bool:
    """Fetch/insert the Wikipedia page for a bridged idea. Returns True if a new
    item landed. Caches the raw text under the same content-hash scheme."""
    url = _wiki_url(title)
    if conn.execute("SELECT 1 FROM items WHERE url = ?", (url,)).fetchone():
        return False
    if not text:
        text, _ = _wiki_extract(title)
        time.sleep(DELAY_SECONDS)
    if not text or len(text.strip()) < 800:
        return False
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    if conn.execute("SELECT 1 FROM items WHERE content_hash = ?", (h,)).fetchone():
        return False
    raw_dir = Path(cfg["paths"]["data_dir"]) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{h[:16]}.html").write_text(text, encoding="utf-8")
    cur = conn.execute(
        """INSERT INTO items (source_id, url, title, published_at, fetched_at,
                              content_hash, raw_path, processed)
           VALUES (?, ?, ?, NULL, ?, ?, ?, 0)
           ON CONFLICT(url) DO NOTHING""",
        (source_id, url, title.strip().replace("_", " "),
         time.strftime("%Y-%m-%dT%H:%M:%S"), h, str(raw_dir / f"{h[:16]}.html")),
    )
    conn.commit()
    return cur.rowcount > 0


def prospect(conn: sqlite3.Connection, cfg: dict, count: int = 5) -> dict:
    """Review up to `count` recent unprocessed pop-science items; bridge ideas
    that map to Wikipedia articles. Returns a summary dict."""
    state = _load_state(cfg)
    seen = set(state["seen"])
    placeholders = ",".join("?" for _ in PROSPECT_SOURCES)
    rows = conn.execute(
        f"""SELECT i.id, i.title, i.url, i.raw_path, i.source_id, s.name AS source_name
            FROM items i JOIN sources s ON s.id = i.source_id
            WHERE s.name IN ({placeholders}) AND i.processed = 0
              AND i.url NOT LIKE 'https://en.wikipedia.org/wiki/%'
            ORDER BY i.fetched_at DESC""",
        tuple(PROSPECT_SOURCES),
    ).fetchall()
    candidates = [dict(r) for r in rows if r["id"] not in seen][:count]
    if not candidates:
        return {"reviewed": 0, "bridged": 0, "seen_total": len(seen), "ideas": []}

    payload = [
        {"id": c["id"], "title": c["title"], "url": c["url"],
         "excerpt": _excerpt(c["raw_path"])[:8000]}
        for c in candidates
    ]
    user = json.dumps({"articles": payload})
    raw = call_llm(cfg, PROSPECT_SYSTEM, user)
    parsed = parse_json(raw) if raw else None
    if parsed is None:
        verdicts = []
    else:
        verdicts = parsed.get("items") or []
    # A call that consumed the budget counts as reviewed, even when the model
    # returned garbage — no free re-runs over the same articles.
    for c in candidates:
        seen.add(c["id"])
    if not verdicts:
        state["seen"] = sorted(seen)
        _save_state(cfg, state)
        return {"reviewed": len(candidates), "bridged": 0, "seen_total": len(seen),
                "ideas": [], "error": "no parseable verdicts"}

    ideas: list[dict] = []
    bridged = 0
    for v in verdicts:
        item_id = v.get("id")
        if item_id is None:
            continue
        seen.add(item_id)
        cand = next((c for c in candidates if c["id"] == item_id), None)
        if not cand:
            continue
        if v.get("timeless"):
            ideas.append({"id": item_id, "idea": v.get("idea"), "from": cand["source_name"]})
        if v.get("timeless") and v.get("wiki_title"):
            if _insert_bridged(conn, cfg, cand["source_id"], v["wiki_title"], ""):
                bridged += 1
    state["seen"] = sorted(seen)
    _save_state(cfg, state)
    return {"reviewed": len(candidates), "bridged": bridged,
            "seen_total": len(state["seen"]), "ideas": ideas}