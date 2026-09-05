"""harvest — pull new items from trusted/probation sources (spec 4.2).

Respects robots.txt (best-effort), conditional GET via ETag, polite rate
limits, caches bodies to data/raw/, and skips on content_hash match.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx

log = logging.getLogger("mlearn.harvest")

UA = "mlearn/0.2 (+https://github.com/wdaniel1993/mlearn; personal research engine)"

MAX_ITEMS_PER_SOURCE = 15
DELAY_SECONDS = 1.2
FEED_TIMEOUT = 30
BODY_TIMEOUT = 40

# Wikipedia: the MediaWiki API is public infrastructure for polite
# programmatic access (documented API etiquette, 429 + Retry-After enforced
# server-side). robots.txt disallows /w/ wholesale for crawlers, so the
# wikipedia source kind intentionally bypasses the generic robots gate —
# the API itself is the gate here. Keep ~1.2 s between page fetches.
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = UA
MAX_WIKI_PAGES = 12

_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def _robots_ok(url: str, ua: str) -> bool:
    domain = url.split("/")[2]
    if domain not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.set_url(f"https://{domain}/robots.txt")
            rp.read()
        except Exception:
            rp = None  # best-effort: allow if robots cannot be fetched
        _robots[domain] = rp
    rp = _robots[domain]
    if rp is None:
        return True
    try:
        return rp.can_fetch(ua, url)
    except Exception:
        return True


def _etags_path(cfg: dict) -> Path:
    return Path(cfg["paths"]["data_dir"]) / "etags.json"


def _load_etags(cfg: dict) -> dict:
    p = _etags_path(cfg)
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _save_etags(cfg: dict, etags: dict) -> None:
    _etags_path(cfg).write_text(json.dumps(etags, indent=1))


def fetch_feed(feed_url: str, cfg: dict) -> list[dict]:
    """Conditional GET on the feed. Returns entries (empty on 304)."""
    etags = _load_etags(cfg)
    headers = {"User-Agent": UA}
    if feed_url in etags:
        headers["If-None-Match"] = etags[feed_url]
    with httpx.Client(timeout=FEED_TIMEOUT, follow_redirects=True) as client:
        r = client.get(feed_url, headers=headers)
        if r.status_code == 304:
            return []
        r.raise_for_status()
        etag = r.headers.get("etag")
        if etag:
            etags[feed_url] = etag
            _save_etags(cfg, etags)
        feed = feedparser.parse(r.content)
    entries = []
    for e in feed.entries:
        if not getattr(e, "link", None):
            continue
        entries.append({
            "url": e.link,
            "title": getattr(e, "title", None) or e.link,
            "summary": getattr(e, "summary", None) or "",
            "published_at": _pub_iso(e),
        })
    return entries


def _pub_iso(entry) -> str | None:
    try:
        import email.utils
        if getattr(entry, "published_parsed", None):
            return time.strftime("%Y-%m-%dT%H:%M:%S", entry.published_parsed) + "Z"
        if getattr(entry, "updated_parsed", None):
            return time.strftime("%Y-%m-%dT%H:%M:%S", entry.updated_parsed) + "Z"
    except Exception:
        pass
    return None


def fetch_body(url: str) -> bytes | None:
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}
    with httpx.Client(timeout=BODY_TIMEOUT, follow_redirects=True) as client:
        r = client.get(url, headers=headers)
        if r.status_code >= 400:
            return None
        return r.content


def _wiki_extract(title: str) -> tuple[str | None, str | None]:
    """Full plain-text extract of a Wikipedia page via the public API.

    Returns (text, lastmod_iso). Retries once on 429 honoring Retry-After."""
    params = {
        "action": "query", "prop": "extracts", "explaintext": "1",
        "format": "json", "titles": title,
    }
    headers = {"User-Agent": WIKI_UA}
    with httpx.Client(timeout=BODY_TIMEOUT, follow_redirects=True) as client:
        for attempt in (0, 1):
            r = client.get(WIKI_API, params=params, headers=headers)
            if r.status_code == 429 and attempt == 0:
                retry = _retry_after(r.headers.get("retry-after"))
                time.sleep(min(retry, 30))
                continue
            r.raise_for_status()
            data = r.json()
            pages = data.get("query", {}).get("pages", {})
            for pg in pages.values():
                if "extract" in pg:
                    return pg["extract"], pg.get("touched")
            return None, None
    return None, None


def _retry_after(value: str | None) -> float:
    try:
        return max(0.0, float(value or 0))
    except ValueError:
        return 1.0


def _wikipedia_items(src, raw_dir: Path) -> tuple[list[dict], list[str]]:
    """Curated page-catalog items (source meta kind='wikipedia')."""
    try:
        meta = json.loads(src["meta"])
    except (TypeError, ValueError):
        return [], [f"{src['name']}: malformed meta"]
    pages = meta.get("pages", [])
    items: list[dict] = []
    for title in pages[:MAX_WIKI_PAGES]:
        url = "https://en.wikipedia.org/wiki/" + title.strip().replace(" ", "_")
        text, lastmod = _wiki_extract(title)
        if not text:
            continue
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        path = raw_dir / f"{h[:16]}.html"
        path.write_text(text, encoding="utf-8")
        items.append({
            "url": url,
            "title": title.strip().replace("_", " "),
            "published_at": lastmod,
            "body_is_text": True,
            "content_hash": h,
            "raw_path": str(path),
        })
        time.sleep(DELAY_SECONDS)
    return items, []


def harvest(conn, cfg: dict) -> dict:
    """Fetch new items from trusted + probation sources. Idempotent per item url."""
    log.info("harvest start")
    raw_dir = Path(cfg["paths"]["raw_dir"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources = conn.execute(
        "SELECT * FROM sources WHERE status IN ('trusted','probation') ORDER BY id"
    ).fetchall()
    new_items = skipped = failed = 0
    reasons = []
    for src in sources:
        meta = None
        if src["meta"]:
            try:
                meta = json.loads(src["meta"])
            except ValueError:
                meta = None
        if meta and meta.get("kind") == "wikipedia":
            # Public-API source: no robots gate (see WIKI_API note), own fetcher.
            wiki_items, wiki_errs = _wikipedia_items(src, raw_dir)
            reasons.extend(wiki_errs)
            for it in wiki_items:
                url = it["url"]
                if conn.execute("SELECT 1 FROM items WHERE url = ?", (url,)).fetchone():
                    skipped += 1
                    continue
                if conn.execute("SELECT 1 FROM items WHERE content_hash = ?",
                                (it["content_hash"],)).fetchone():
                    skipped += 1
                    continue
                cur = conn.execute(
                    """INSERT INTO items (source_id, url, title, published_at, fetched_at,
                                          content_hash, raw_path, processed)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                       ON CONFLICT(url) DO NOTHING""",
                    (src["id"], url, it["title"], it["published_at"],
                     time.strftime("%Y-%m-%dT%H:%M:%S"), it["content_hash"], it["raw_path"]),
                )
                if cur.rowcount:
                    new_items += 1
            conn.commit()
            continue
        feed_url = src["feed_url"]
        if not feed_url:
            continue
        if not _robots_ok(feed_url, UA):
            reasons.append(f"{src['name']}: robots.txt disallows feed")
            continue
        try:
            entries = fetch_feed(feed_url, cfg)
        except Exception as e:
            reasons.append(f"{src['name']} feed failed: {type(e).__name__}: {e}")
            continue
        time.sleep(DELAY_SECONDS)
        for entry in entries[:MAX_ITEMS_PER_SOURCE]:
            url = entry["url"]
            if conn.execute("SELECT 1 FROM items WHERE url = ?", (url,)).fetchone():
                skipped += 1
                continue
            body = fetch_body(url)
            if body is None:
                failed += 1
                continue
            h = hashlib.sha1(body).hexdigest()
            if conn.execute("SELECT 1 FROM items WHERE content_hash = ?", (h,)).fetchone():
                skipped += 1
                continue
            path = raw_dir / f"{h[:16]}.html"
            path.write_bytes(body)
            cur = conn.execute(
                """INSERT INTO items (source_id, url, title, published_at, fetched_at,
                                      content_hash, raw_path, processed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                   ON CONFLICT(url) DO NOTHING""",
                (src["id"], url, entry["title"], entry["published_at"],
                 time.strftime("%Y-%m-%dT%H:%M:%S"), h, str(path)),
            )
            if cur.rowcount:
                new_items += 1
            time.sleep(DELAY_SECONDS)
        conn.commit()
    return {
        "new_items": new_items,
        "skipped": skipped,
        "failed": failed,
        "feed_issues": reasons,
    }