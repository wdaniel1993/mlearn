"""topic — the new-category wizard (RFC 2026-09, phase 2).

`mlearn topic add "<phrase>"` -> LLM proposes a catalog entry {name,
guardrail, description, sources[]}; validation gates it (feed parses,
url reachable); preview is ALWAYS shown; apply happens only on confirm
(--yes skips the confirm, --dry-run prints and exits).

The catalog's single source of truth is sources.yaml (`topics:` section);
clusters are seeded from it by `init` and `topic apply`.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import yaml

from . import config as config_mod
from . import db as db_mod
from . import generate as generate_mod

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

PROPOSE_SYSTEM = '''You are the catalog architect of a microlearning engine. A user
proposes a NEW topic they want cards for. Produce a STRICT JSON catalog entry (no prose,
no markdown fences):

{
  "name": "snake_case_topic_id (max 32 chars, lowercase, starts with a letter)",
  "guardrail": "one-paragraph LLM guardrail in the house style: explain MECHANISMS and
                EVIDENCE; never prescriptive, never diagnostic, never personal advice.
                State what the topic IS and what to reject.",
  "description": "one sentence what this topic covers",
  "sources": [
    {"name": "Site name", "url": "https://site.example", "feed_url": "https://site.example/feed",
     "notes": "why this source fits the topic"}
  ]
}

Source requirements: 2-4 REAL, reputable sites whose RSS feeds (or regular article
outlets) actually cover the topic with mechanism/evidence density; prefer outlets with
stable, well-known feeds. Never invent URLs. If you cannot name real sources, return
{"sources": []} rather than fabricating.
The guardrail must match the existing style, e.g.:
"Topic finance — how instruments, markets, and mechanisms work. NEVER buy/sell/allocate
guidance, never specific-security recommendations, no performance projections."
'''


def propose(cfg: dict, phrase: str) -> dict:
    """Ask the LLM for a catalog proposal (strict JSON)."""
    user = (
        f"Proposed new topic (user wording): \"{phrase}\".\n"
        "Existing topic names in the catalog: "
        + ", ".join(t["name"] for t in config_mod.load_topics(cfg)) + ".\n"
        "Return the JSON catalog entry."
    )
    raw = generate_mod.call_llm(cfg, PROPOSE_SYSTEM, user)
    if not raw:
        raise RuntimeError("LLM returned nothing for the topic proposal")
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("LLM proposal was not JSON: " + text[:200])
    return json.loads(text[start:end + 1])


def _existing_names(cfg: dict) -> set[str]:
    names = {t["name"] for t in config_mod.load_topics(cfg)}
    doc = config_mod.load_sources_doc(cfg)
    names |= {s.get("topic", "") for s in doc.get("sources", [])}
    names.discard("")
    return names


def _check_source(s: dict, check_network: bool) -> list[str]:
    errs = []
    if not s.get("name") or not str(s["name"]).strip():
        errs.append("source missing 'name'")
    url = s.get("url") or ""
    feed = s.get("feed_url") or ""
    if not re.match(r"^https?://", url):
        errs.append(f"source {s.get('name')}: url must be http(s): {url}")
    if not re.match(r"^https?://", feed):
        errs.append(f"source {s.get('name')}: feed_url must be http(s): {feed}")
    elif check_network:
        try:
            import feedparser
            import httpx
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                r = client.get(feed, headers={"User-Agent": generate_mod_hint()})
                if r.status_code >= 400:
                    errs.append(f"source {s.get('name')}: feed HTTP {r.status_code}")
                else:
                    parsed = feedparser.parse(r.content)
                    if not getattr(parsed, "entries", None):
                        errs.append(f"source {s.get('name')}: feed has no entries")
        except Exception as e:
            errs.append(f"source {s.get('name')}: feed unreachable ({type(e).__name__})")
    return errs


def generate_mod_hint() -> str:
    try:
        from . import harvest as harvest_mod
        return harvest_mod.UA
    except Exception:
        return "mlearn/0.3"


def validate(cfg: dict, proposal: dict, check_network: bool = True) -> list[str]:
    """Catalog rules. Returns list of error strings (empty = valid)."""
    errs: list[str] = []
    if not isinstance(proposal, dict):
        return ["proposal is not an object"]
    name = str(proposal.get("name") or "").strip()
    if not NAME_RE.match(name):
        errs.append(f"name must match {NAME_RE.pattern!r}: {name!r}")
    elif name in _existing_names(cfg):
        errs.append(f"topic already exists: {name}")
    guardrail = str(proposal.get("guardrail") or "").strip()
    if len(guardrail) < 40:
        errs.append("guardrail too short (< 40 chars) or missing")
    if len(guardrail) > 600:
        errs.append("guardrail too long (> 600 chars)")
    sources = proposal.get("sources") or []
    if not isinstance(sources, list) or not 1 <= len(sources) <= 4:
        errs.append("sources must be a list of 1-4 entries")
    else:
        for s in sources:
            if not isinstance(s, dict):
                errs.append("source entry is not an object")
                continue
            errs.extend(_check_source(s, check_network))
    return errs


def apply(cfg: dict, proposal: dict) -> dict:
    """Write the catalog entry into sources.yaml + seed the cluster/rows."""
    name = str(proposal["name"]).strip()
    src_path = Path(cfg["paths"]["sources"])
    doc = config_mod.load_sources_doc(cfg)
    topics = doc.setdefault("topics", [])
    if any(t.get("name") == name for t in topics):
        raise ValueError(f"topic already in catalog: {name}")
    topics.append({
        "name": name,
        "guardrail": str(proposal["guardrail"]).strip(),
        "description": str(proposal.get("description") or "").strip() or None,
    })
    entries = []
    for s in proposal.get("sources") or []:
        entries.append({
            "name": str(s["name"]).strip(),
            "url": str(s["url"]).strip(),
            "feed_url": str(s["feed_url"]).strip(),
            "topic": name,
            "status": "probation",
            "notes": str(s.get("notes") or "").strip() or None,
        })
    existing_urls = {e.get("url") for e in doc.setdefault("sources", [])}
    added_sources = [e for e in entries if e["url"] not in existing_urls]
    doc["sources"].extend(added_sources)
    src_path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))

    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    cluster_ids = db_mod.ensure_seed_clusters(conn, [name])
    sync = db_mod.upsert_sources(conn, added_sources, prune=False)
    cfg.pop("_topics", None)  # invalidate the catalog cache
    return {
        "topic": name,
        "cluster_id": cluster_ids[0],
        "sources_added": len(added_sources),
        "sources_skipped": len(entries) - len(added_sources),
        "sync": sync,
    }


def pretty(proposal: dict) -> str:
    name = proposal.get("name") or "?"
    lines = [f"topic: {name}",
             f"guardrail: {(proposal.get('guardrail') or '').strip()[:300]}"]
    desc = (proposal.get("description") or "").strip()
    if desc:
        lines.append(f"description: {desc}")
    for i, s in enumerate(proposal.get("sources") or [], 1):
        lines.append(f"  {i}. {s.get('name')} — {s.get('url')} (feed {s.get('feed_url')})")
    return "\n".join(lines)