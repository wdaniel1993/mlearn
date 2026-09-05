"""Source scouting (spec 5) — the allowlist self-expands under supervision.

Candidates: domains cited by >= 2 distinct trusted sources. Probation:
20 served cards then promote/blacklist/retry. Cap on concurrent probation
sources so unproven content never dominates a feed.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import db as db_mod

UA = "mlearn/0.2 (+https://github.com/wdaniel1993/mlearn; personal research engine)"

HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
SKIP_DOMAINS = {
    "twitter.com", "x.com", "facebook.com", "linkedin.com", "instagram.com",
    "youtube.com", "youtu.be", "t.me", "reddit.com", "wikipedia.org",
    "archive.org", "cloudflare.com", "google.com", "gstatic.com",
}
FEED_PROBES = ["/feed", "/feed/", "/rss", "/rss.xml", "/atom.xml", "/index.xml"]


def _external_domains(html: str) -> set[str]:
    domains = set()
    for m in HREF_RE.findall(html):
        if not m.startswith(("http://", "https://")):
            continue
        host = urlparse(m).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host and host not in SKIP_DOMAINS:
            domains.add(host)
    return domains


def discover_candidates(conn, cfg: dict) -> list[dict]:
    """Domains cited by >= 2 distinct trusted sources become candidates."""
    trusted_ids = {
        r["id"] for r in conn.execute("SELECT id FROM sources WHERE status = 'trusted'")
    }
    if not trusted_ids:
        return []
    counts: dict[str, set[int]] = {}
    topic_counter: dict[str, Counter] = {}
    rows = conn.execute(
        "SELECT id, source_id, raw_path FROM items WHERE processed = 1"
    ).fetchall()
    for item in rows:
        if item["source_id"] not in trusted_ids:
            continue
        path = item["raw_path"]
        if not path or not Path(path).is_file():
            continue
        try:
            html = Path(path).read_text(errors="ignore")[:200_000]
        except OSError:
            continue
        src_topic = conn.execute("SELECT topic FROM sources WHERE id = ?",
                                 (item["source_id"],)).fetchone()["topic"]
        for d in _external_domains(html):
            counts.setdefault(d, set()).add(item["source_id"])
            topic_counter.setdefault(d, Counter())[src_topic] += 1
    created = []
    for d, srcids in counts.items():
        if len(srcids) < 2:
            continue
        if conn.execute("SELECT 1 FROM sources WHERE url LIKE ?",
                        (f"%//{d}%",)).fetchone():
            continue
        topic = topic_counter[d].most_common(1)[0][0]
        cur = conn.execute(
            """INSERT INTO sources (name, url, feed_url, topic, status, added_at, notes)
               VALUES (?, ?, NULL, ?, 'candidate', ?, ?)""",
            (d, f"https://{d}", topic, db_mod.utcnow(),
             f"scout: cited by {len(srcids)} trusted sources"),
        )
        conn.commit()
        created.append({"name": d, "url": f"https://{d}", "topic": topic,
                        "citations": len(srcids), "id": cur.lastrowid})
    return created


def probe_feed(domain: str) -> str | None:
    """Best-effort feed discovery for a candidate domain."""
    with httpx.Client(timeout=15, follow_redirects=True,
                      headers={"User-Agent": UA}) as client:
        for probe in FEED_PROBES:
            try:
                r = client.get(f"https://{domain}{probe}")
                if r.status_code == 200:
                    ctype = r.headers.get("content-type", "")
                    head = r.content[:200].decode(errors="ignore").lower()
                    if "xml" in ctype or "<rss" in head or "<feed" in head or "atom" in head:
                        return f"https://{domain}{probe}"
            except httpx.HTTPError:
                continue
    return None


def promote_probation(conn, cfg: dict) -> dict:
    """Promotion pass (spec 5): after probation_cards served, compare mean grade
    against the trusted baseline."""
    trusted = conn.execute(
        "SELECT grade_sum, cards_served FROM sources WHERE status = 'trusted'"
    ).fetchall()
    served_trusted = [t for t in trusted if t["cards_served"] > 0]
    baseline = (sum(t["grade_sum"] / t["cards_served"] for t in served_trusted)
                / len(served_trusted)) if served_trusted else 2.5
    promoted, blacklisted, stayed = [], [], []
    for s in conn.execute("SELECT * FROM sources WHERE status = 'probation'").fetchall():
        if s["cards_served"] < cfg["probation_cards"]:
            continue
        mean_g = s["grade_sum"] / s["cards_served"]
        if mean_g >= baseline:
            conn.execute("UPDATE sources SET status = 'trusted', promoted_at = ? WHERE id = ?",
                         (db_mod.utcnow(), s["id"]))
            promoted.append(s["name"])
        elif mean_g < baseline - 0.4:
            conn.execute("UPDATE sources SET status = 'blacklisted' WHERE id = ?", (s["id"],))
            blacklisted.append(s["name"])
        else:
            stayed.append(s["name"])
    conn.commit()
    return {"baseline": round(baseline, 3), "promoted": promoted,
            "blacklisted": blacklisted, "stayed": stayed}


def run_scout(conn, cfg: dict) -> dict:
    """Candidate discovery + promotion pass + probation feed adoption."""
    discovered = discover_candidates(conn, cfg)
    adopted = []
    probation_count = conn.execute(
        "SELECT COUNT(*) n FROM sources WHERE status = 'probation'"
    ).fetchone()["n"]
    for cand in discovered[:cfg["max_probation_sources"] - probation_count]:
        feed = probe_feed(cand["name"])
        if feed:
            conn.execute("UPDATE sources SET feed_url = ?, status = 'probation' WHERE id = ?",
                         (feed, cand["id"]))
            adopted.append({"name": cand["name"], "feed_url": feed})
    conn.commit()
    promo = promote_probation(conn, cfg)
    return {"candidates": discovered, "adopted": adopted, "promotion": promo}