"""local — local file/folder content sources (RFC 2026-09, phase 1).

A local source (kind='local') points at a file or folder. Ingestion is
explicit: `mlearn ingest <path>` (one-shot, items carry their own topic) or
`add-local` (catalog entry picked up by `harvest`). No watch daemon.

Extraction: markdown/txt natively, docx/rtf/html via macOS textutil,
PDFs via pypdf when available. Text is snapshotted into data/raw so later
edits to the original file never drift a card's anchor.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

log = __import__("logging").getLogger("mlearn.local")

DEFAULT_EXTS = (".md", ".markdown", ".txt")
EXTRACTABLE = (".md", ".markdown", ".txt", ".docx", ".rtf", ".html", ".htm", ".pdf")
_IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".DS_Store"}
MAX_FILE_BYTES = 512 * 1024
TEXTUTIL = "/usr/bin/textutil"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_ignored(path: Path, ignore: tuple[str, ...]) -> bool:
    parts = set(path.parts)
    if parts & _IGNORE_DIRS:
        return True
    for pat in ignore:
        if pat in str(path):
            return True
    return False


def scan_files(path: Path, *, recursive: bool = True,
               exts: tuple[str, ...] = DEFAULT_EXTS,
               ignore: tuple[str, ...] = ()) -> tuple[list[Path], list[str]]:
    """Files under path matching exts, honoring ignore rules + size cap."""
    out: list[Path] = []
    reasons: list[str] = []
    if not path.exists():
        return out, [f"path does not exist: {path}"]
    if path.is_file():
        cands = [path]
    else:
        it = path.rglob("*") if recursive else path.glob("*")
        cands = [p for p in it if p.is_file()]
    for p in sorted(cands):
        if p.suffix.lower() not in exts:
            continue
        if _is_ignored(p, tuple(ignore)):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            reasons.append(f"skipped (>{MAX_FILE_BYTES//1024} KB): {p}")
            continue
        if _looks_binary(p):
            reasons.append(f"skipped (binary): {p}")
            continue
        out.append(p)
    return out, reasons


def _looks_binary(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            head = f.read(8192)
        return b"\x00" in head and p.suffix.lower() not in (".pdf",)
    except OSError:
        return True


def extract_text(path: Path) -> tuple[str | None, str | None]:
    """(text, method). method None = could not extract."""
    ext = path.suffix.lower()
    try:
        if ext in (".md", ".markdown", ".txt"):
            return path.read_text(errors="replace"), "raw"
        if ext in (".docx", ".rtf", ".html", ".htm"):
            r = subprocess.run([TEXTUTIL, "-convert", "txt", "-stdout", str(path)],
                               capture_output=True, timeout=60)
            if r.returncode == 0 and r.stdout:
                return r.stdout.decode("utf-8", "replace"), "textutil"
            return None, None
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                return None, "pdf-unavailable"
            reader = PdfReader(str(path))
            text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
            return (text or None), "pypdf"
    except Exception as e:
        log.warning("extract failed %s: %s", path, e)
        return None, None
    return None, None


def _snapshot(text: str, raw_dir: Path) -> Path:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    p = raw_dir / f"{h[:16]}.txt"
    p.write_text(text, encoding="utf-8")
    return p


def file_url(path: Path) -> str:
    return "file://" + str(path.resolve())


def ingest(conn: sqlite3.Connection, cfg: dict, path: Path, *,
           topic: str, source_id: int | None = None,
           recursive: bool = True, exts: tuple[str, ...] = DEFAULT_EXTS,
           ignore: tuple[str, ...] = ()) -> dict:
    """Scan + upsert items for a local path. Idempotent per file url."""
    raw_dir = Path(cfg["paths"]["raw_dir"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths, scan_reasons = scan_files(path, recursive=recursive, exts=exts, ignore=ignore)
    new = updated = skipped = failed = 0
    reasons = list(scan_reasons)
    for p in paths:
        url = file_url(p)
        text, method = extract_text(p)
        if not text:
            failed += 1
            reasons.append(f"{p}: no text ({method or 'unreadable'})")
            continue
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        snap = _snapshot(text, raw_dir)
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
        row = conn.execute("SELECT id, content_hash, processed FROM items WHERE url = ?", (url,)).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO items (source_id, url, title, published_at, fetched_at, content_hash, raw_path, processed, topic)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (source_id, url, p.stem, mtime, utcnow(), h, str(snap), topic))
            new += 1
        elif row["content_hash"] != h:
            live = conn.execute(
                """SELECT COUNT(*) c FROM cards WHERE item_id = ? AND status IN ('ready','served')""",
                (row["id"],)).fetchone()["c"]
            # re-mineable only when no live card depends on the old text
            reset = 1 if live == 0 else 0
            conn.execute(
                """UPDATE items SET content_hash=?, raw_path=?, title=?, published_at=?, topic=?,
                                     processed = CASE WHEN ? THEN 0 ELSE processed END
                   WHERE id = ?""",
                (h, str(snap), p.stem, mtime, topic, reset, row["id"]))
            updated += 1
        else:
            skipped += 1
    conn.commit()
    return {"new": new, "updated": updated, "skipped": skipped, "failed": failed,
            "reasons": reasons}