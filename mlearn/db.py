"""SQLite schema, migrations, and thin access helpers.

SQLite is the single source of truth. Markdown (cards/*.md) is a generated
projection and is never read back as state.
"""
from __future__ import annotations

import json
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL
);

-- Source allowlist. The shared, forkable asset.
CREATE TABLE IF NOT EXISTS sources (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  url           TEXT NOT NULL UNIQUE,
  feed_url      TEXT,
  topic         TEXT NOT NULL,
  status        TEXT NOT NULL,   -- candidate|probation|trusted|blacklisted
  added_at      TEXT NOT NULL,
  promoted_at   TEXT,
  cards_served  INTEGER DEFAULT 0,
  grade_sum     REAL    DEFAULT 0,
  notes         TEXT,
  meta          TEXT            -- JSON: {"kind": "wikipedia", "pages": [...]}
);

-- Raw fetched items, pre-card.
CREATE TABLE IF NOT EXISTS items (
  id            INTEGER PRIMARY KEY,
  source_id     INTEGER REFERENCES sources(id),
  url           TEXT NOT NULL UNIQUE,
  title         TEXT,
  published_at  TEXT,
  fetched_at    TEXT NOT NULL,
  content_hash  TEXT NOT NULL,
  raw_path      TEXT,            -- cached body on disk
  processed     INTEGER DEFAULT 0
);

-- Bandit arms. Created in this order so cards can reference them.
CREATE TABLE IF NOT EXISTS clusters (
  id            INTEGER PRIMARY KEY,
  label         TEXT NOT NULL,
  centroid      BLOB,
  alpha         REAL DEFAULT 1.0,
  beta          REAL DEFAULT 1.0,
  is_seed       INTEGER DEFAULT 0,
  birth_card_id INTEGER,         -- set when born from a wildcard
  created_at    TEXT NOT NULL,
  last_updated  TEXT
);

-- The 5-minute unit.
CREATE TABLE IF NOT EXISTS cards (
  id            INTEGER PRIMARY KEY,
  item_id       INTEGER REFERENCES items(id),
  cluster_id    INTEGER REFERENCES clusters(id),
  title         TEXT NOT NULL,
  hook          TEXT NOT NULL,   -- 1-2 sentences: why this matters
  body_md       TEXT NOT NULL,   -- ~600-900 words
  diagram_type  TEXT NOT NULL,   -- concept|data
  diagram_src   TEXT NOT NULL,   -- mermaid, parse-validated
  infographic_svg TEXT,          -- optional self-contained SVG infographic (visual lane)
  infographic_spec TEXT NOT NULL DEFAULT '',  -- AntV spec the banner came from (improve/QA)
  figures_json  TEXT,            -- required when diagram_type='data'
  source_url    TEXT NOT NULL,
  anchor_quote  TEXT NOT NULL,   -- verbatim, <= 25 words
  embedding     BLOB,
  status        TEXT NOT NULL,   -- ready|served|archived
  is_wildcard   INTEGER DEFAULT 0,
  created_at    TEXT NOT NULL,
  served_at     TEXT
);

-- Derived recall prompts. These, not cards, enter FSRS.
CREATE TABLE IF NOT EXISTS prompts (
  id            INTEGER PRIMARY KEY,
  card_id       INTEGER REFERENCES cards(id),
  question      TEXT NOT NULL,
  answer        TEXT NOT NULL,
  stability     REAL,
  difficulty    REAL,
  reps          INTEGER DEFAULT 0,
  lapses        INTEGER DEFAULT 0,
  due_at        TEXT,
  last_review   TEXT
);

-- Append-only event log. Never update or delete (enforced by triggers).
CREATE TABLE IF NOT EXISTS grades (
  id            INTEGER PRIMARY KEY,
  prompt_id     INTEGER REFERENCES prompts(id),
  card_id       INTEGER REFERENCES cards(id),
  grade         INTEGER NOT NULL,  -- 1=again 2=hard 3=good 4=easy
  latency_ms    INTEGER,
  created_at    TEXT NOT NULL
);

-- Explicit taste signals, separate from grades.
CREATE TABLE IF NOT EXISTS signals (
  id            INTEGER PRIMARY KEY,
  card_id       INTEGER REFERENCES cards(id),
  kind          TEXT NOT NULL,   -- more_like_this|less_like_this|skip|opened_source
  created_at    TEXT NOT NULL
);

-- Single-row EMA interest vector.
CREATE TABLE IF NOT EXISTS profile (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  vector        BLOB,
  updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_cards_status  ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_cluster ON cards(cluster_id);
CREATE INDEX IF NOT EXISTS idx_items_hash    ON items(content_hash);
CREATE INDEX IF NOT EXISTS idx_items_source  ON items(source_id);
CREATE INDEX IF NOT EXISTS idx_prompts_due   ON prompts(due_at);
CREATE INDEX IF NOT EXISTS idx_prompts_card  ON prompts(card_id);
CREATE INDEX IF NOT EXISTS idx_grades_prompt ON grades(prompt_id);
CREATE INDEX IF NOT EXISTS idx_signals_card  ON signals(card_id);

-- Grades are an append-only event log.
CREATE TRIGGER IF NOT EXISTS trg_grades_no_update
BEFORE UPDATE ON grades BEGIN
  SELECT RAISE(ABORT, 'grades is append-only; update forbidden');
END;
CREATE TRIGGER IF NOT EXISTS trg_grades_no_delete
BEFORE DELETE ON grades BEGIN
  SELECT RAISE(ABORT, 'grades is append-only; delete forbidden');
END;
"""

# Seed topics become the initial clusters (spec 4.1). A starting point,
# not a ceiling — wildcard arm birth escapes them.
SEED_TOPICS = [
    "self_improvement",
    "mental_health",
    "innovation",
    "technology",
    "finance",
    "psychology",
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # idempotent column migrations for pre-feature databases
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sources)").fetchall()}
    if "meta" not in cols:
        conn.execute("ALTER TABLE sources ADD COLUMN meta TEXT")
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(cards)").fetchall()}
    if "infographic_svg" not in ccols:
        conn.execute("ALTER TABLE cards ADD COLUMN infographic_svg TEXT")
    if "infographic_spec" not in ccols:
        conn.execute(
            "ALTER TABLE cards ADD COLUMN infographic_spec TEXT NOT NULL DEFAULT ''")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.execute(
        "INSERT OR IGNORE INTO profile (id, vector, updated_at) VALUES (1, NULL, ?)",
        (utcnow(),),
    )
    conn.commit()


def ensure_seed_clusters(conn: sqlite3.Connection, labels: list[str] | None = None) -> list[int]:
    """Create missing seed clusters; return their ids in input order."""
    labels = labels or SEED_TOPICS
    created = []
    for label in labels:
        row = conn.execute("SELECT id FROM clusters WHERE label = ?", (label,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO clusters (label, is_seed, created_at, last_updated) VALUES (?, 1, ?, ?)",
                (label, utcnow(), utcnow()),
            )
            created.append(cur.lastrowid)
        else:
            created.append(row["id"])
    conn.commit()
    return created


def upsert_sources(conn: sqlite3.Connection, sources: list[dict]) -> dict:
    """Sync sources.yaml into the sources table (keyed by url). Counters
    (cards_served, grade_sum) are preserved across syncs.

    sources.yaml is authoritative: rows whose url vanished from the file are
    removed if they have no history, otherwise retired (feed_url=NULL,
    status='retired') so harvest skips them without losing counters."""
    added = updated = removed = retired = 0
    for s in sources:
        existing = conn.execute("SELECT id FROM sources WHERE url = ?", (s["url"],)).fetchone()
        meta = json.dumps(s.get("meta")) if isinstance(s.get("meta"), dict) else None
        if existing is None:
            conn.execute(
                """INSERT INTO sources (name, url, feed_url, topic, status, added_at, notes, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (s["name"], s["url"], s.get("feed_url"), s["topic"],
                 s.get("status", "candidate"), utcnow(), s.get("notes"), meta),
            )
            added += 1
        else:
            conn.execute(
                """UPDATE sources SET name=?, feed_url=?, topic=?, status=?, notes=?, meta=?
                   WHERE url = ?""",
                (s["name"], s.get("feed_url"), s["topic"],
                 s.get("status", "candidate"), s.get("notes"), meta, s["url"]),
            )
            updated += 1
    yaml_urls = {s["url"] for s in sources}
    for row in conn.execute("SELECT * FROM sources"):
        if row["url"] in yaml_urls:
            continue
        has_history = conn.execute(
            "SELECT 1 FROM items WHERE source_id = ? LIMIT 1", (row["id"],)
        ).fetchone() is not None
        if has_history:
            conn.execute(
                "UPDATE sources SET status = 'retired', feed_url = NULL WHERE id = ?",
                (row["id"],),
            )
            retired += 1
        else:
            conn.execute("DELETE FROM sources WHERE id = ?", (row["id"],))
            removed += 1
    conn.commit()
    return {"added": added, "updated": updated, "removed": removed, "retired": retired}


def cluster_by_label(conn: sqlite3.Connection, label: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM clusters WHERE label = ?", (label,)).fetchone()


def insert_item(conn: sqlite3.Connection, *, url: str, title: str | None,
                source_id: int | None, content_hash: str, published_at: str | None = None,
                raw_path: str | None = None) -> int:
    """INSERT OR IGNORE on url; returns the item id (new or existing)."""
    row = conn.execute("SELECT id FROM items WHERE url = ?", (url,)).fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute(
        """INSERT INTO items (source_id, url, title, published_at, fetched_at,
                              content_hash, raw_path, processed)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (source_id, url, title, published_at, utcnow(), content_hash, raw_path),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def insert_card(conn: sqlite3.Connection, *, item_id: int | None, cluster_label: str,
                title: str, hook: str, body_md: str, diagram_type: str, diagram_src: str,
                infographic_svg: str | None, infographic_spec: str = "",
                figures_json: str | None,
                source_url: str, anchor_quote: str,
                embedding: bytes | None = None, is_wildcard: bool = False,
                prompts: list[dict] | None = None) -> int:
    """Insert a card plus its recall prompts. Status starts 'ready'."""
    cluster = cluster_by_label(conn, cluster_label)
    if cluster is None:
        raise ValueError(f"unknown cluster label: {cluster_label}")
    cur = conn.execute(
        """INSERT INTO cards (item_id, cluster_id, title, hook, body_md, diagram_type,
                              diagram_src, infographic_svg, infographic_spec, figures_json, source_url,
                              anchor_quote, embedding, status, is_wildcard, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)""",
        (item_id, cluster["id"], title, hook, body_md, diagram_type, diagram_src,
         infographic_svg, infographic_spec, figures_json, source_url, anchor_quote, embedding,
         int(is_wildcard), utcnow()),
    )
    assert cur.lastrowid is not None
    card_id = cur.lastrowid
    for p in prompts or []:
        conn.execute(
            "INSERT INTO prompts (card_id, question, answer) VALUES (?, ?, ?)",
            (card_id, p["question"], p["answer"]),
        )
    conn.commit()
    return card_id


def list_cards(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    q = """SELECT c.*, cl.label AS cluster_label
           FROM cards c JOIN clusters cl ON cl.id = c.cluster_id"""
    if status:
        q += " WHERE c.status = ?"
        return conn.execute(q, (status,)).fetchall()
    return conn.execute(q).fetchall()


def prompts_for_card(conn: sqlite3.Connection, card_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM prompts WHERE card_id = ? ORDER BY id", (card_id,)).fetchall()


def pack_vec(xs: list[float]) -> bytes:
    return struct.pack(f"<{len(xs)}f", *xs)


def unpack_vec(b: bytes | None) -> list[float] | None:
    if not b:
        return None
    return list(struct.unpack(f"<{len(b) // 4}f", b))