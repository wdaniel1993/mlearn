"""research — the quality-gated research pass (RFC 2026-09, phase 4).

Trigger: automatic but conditional on material quality. The primary item is
scored with a cheap deterministic heuristic (word count + section/paragraph
density). ABOVE threshold  -> the first page is detailed enough: no research.
BELOW threshold          -> auto-research (corpus provider): synthesize
context from OTHER items across the whole corpus (same topic, any source —
this is the multi-source consideration: rss + wikipedia + local together)
and curated further-reading links (Wikipedia search, title-verified).

Web provider is ON DEMAND (--provider web): nothing network-heavy runs
unprompted; corpus is pure local work (fastembed + sqlite).
"""
from __future__ import annotations

import re

from . import db as db_mod
from . import embed as embed_mod
from . import generate as generate_mod  # reuse item_text + token limits

# --- detail heuristic -------------------------------------------------------
DETAIL_WORDS = 1500     # >= words => rich material
DETAIL_SECTIONS = 2     # markdown headings >= this counts as structured
DETAIL_BLOCKS = 8       # fallback structure signal: paragraphs

_HEADING_RE = re.compile(r"^#{1,4}\s+\S", re.M)


def detail_score(text: str) -> tuple[int, int, int]:
    """(word_count, heading_count, paragraph_count) for the heuristic."""
    words = len(str(text).split())
    headings = len(_HEADING_RE.findall(str(text))) if words else 0
    if headings >= DETAIL_SECTIONS:
        blocks = headings
    else:
        raw = re.sub(r"^#{1,4}\s.*$", "", str(text), flags=re.M)
        blocks = len([b for b in re.split(r"\n\s*\n", raw) if b.strip()])
    return words, headings, blocks


def needs_research(text: str) -> bool:
    """False when the primary material is already detailed (no research)."""
    words, headings, blocks = detail_score(text)
    if words >= DETAIL_WORDS and (headings >= DETAIL_SECTIONS or blocks >= DETAIL_BLOCKS):
        return False
    return True


# --- corpus provider (default: zero network) ---------------------------------
def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def research_context(conn, cfg: dict, item: dict, limit: int = 3) -> list[dict]:
    """Cross-source context: top-limit OTHER unprocessed items in the same
    topic (any source kind — rss, wikipedia, local), scored by embedding
    similarity to the candidate. Excludes the primary item."""
    topic = item.get("topic") or conn.execute(
        """SELECT s.topic FROM items i LEFT JOIN sources s ON s.id = i.source_id
           WHERE i.id = ?""", (item["id"],)
    ).fetchone()["topic"]
    cands = conn.execute(
        """SELECT i.* FROM items i
           LEFT JOIN sources s ON s.id = i.source_id
           WHERE i.id != ? AND COALESCE(i.topic, s.topic) = ?
           ORDER BY i.id LIMIT 40""",
        (item["id"], topic),
    ).fetchall()
    if not cands:
        return []
    query = str(item.get("title") or "") + ". " + \
        " ".join(str(generate_mod.item_text(item.get("raw_path"), item.get("title"))).split())[:1400]
    qv = embed_mod.embed_one(query)
    scored: list[tuple[float, dict]] = []
    for cand in cands:
        text = generate_mod.item_text(cand["raw_path"], cand["title"])
        if len(text) < 120:
            continue
        cv = embed_mod.embed_one(str(cand["title"]) + ". " + text[:1400])
        scored.append((_cosine(qv, cv), dict(cand)))
    scored.sort(key=lambda kv: (-kv[0], kv[1]["id"]))
    out = []
    for _, cand in scored[:limit]:
        out.append({
            "id": cand["id"], "title": cand["title"], "url": cand["url"],
            "body": generate_mod.item_text(cand["raw_path"], cand["title"]),
        })
    return out


def research_links(conn, cfg: dict, item: dict, count: int = 3) -> list[dict]:
    """Title-verified further-reading links: Wikipedia search for the topic,
    fetch each page's extract (verifies title + fetches). Max `count`."""
    from . import harvest as harvest_mod
    out: list[dict] = []
    query = str(item.get("title") or "").strip()
    if not query:
        return out
    try:
        params = {"action": "query", "list": "search", "srsearch": query,
                  "srlimit": str(count + 1), "format": "json"}
        import httpx
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            r = client.get(harvest_mod.WIKI_API, params=params,
                           headers={"User-Agent": harvest_mod.WIKI_UA})
            r.raise_for_status()
            hits = r.json().get("query", {}).get("search", [])
    except Exception:
        return out
    for hit in hits:
        if len(out) >= count:
            break
        title = hit.get("title", "")
        if not title or harvest_mod._wiki_url(title) == str(item.get("url") or ""):
            continue
        text, _ = harvest_mod._wiki_extract(title)
        if not text:
            continue
        out.append({
            "url": harvest_mod._wiki_url(title),
            "title": title.replace("_", " "),
        })
    return out


def research(conn, cfg: dict, item: dict, primary_text: str | None = None,
             provider: str = "corpus", context_limit: int = 3,
             link_count: int = 3) -> dict:
    """Full research pass for one candidate item.
    Returns {needed, context, links}. context/links are [] when not needed."""
    text = primary_text if primary_text is not None else \
        generate_mod.item_text(item.get("raw_path"), item.get("title"))
    if not needs_research(text):
        return {"needed": False, "context": [], "links": []}
    out: dict = {"needed": True, "context": [], "links": []}
    if provider in ("corpus", "web"):
        out["context"] = research_context(conn, cfg, item, limit=context_limit)
        # Wikipedia-verified links are part of corpus research (polite API);
        # provider='web' later extends to search engines (on demand only).
        out["links"] = research_links(conn, cfg, item, count=link_count)
    return out