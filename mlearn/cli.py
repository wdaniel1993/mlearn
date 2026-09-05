"""mlearn CLI (spec 9.1). Every command supports --json."""
from __future__ import annotations

import json
from pathlib import Path

import typer
import yaml

from . import config as config_mod
from . import db as db_mod
from . import project as project_mod
from . import validate as validate_mod

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _json_out(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _load_runtime(json_out: bool):
    """Resolve config, connect db. Returns (cfg, conn)."""
    cfg = config_mod.resolve_paths(config_mod.load())
    conn = db_mod.connect(cfg["paths"]["db"])
    return cfg, conn


@app.command()
def init(json_out: bool = typer.Option(False, "--json")):
    """Create the database, load sources.yaml, seed topic clusters."""
    cfg = config_mod.resolve_paths(config_mod.load())
    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    cluster_ids = db_mod.ensure_seed_clusters(conn)
    sources = []
    src_path = Path(cfg["paths"]["sources"])
    if src_path.is_file():
        with open(src_path) as f:
            sources = (yaml.safe_load(f) or {}).get("sources", [])
    sync = db_mod.upsert_sources(conn, sources)
    for d in (cfg["paths"]["data_dir"], cfg["paths"]["raw_dir"], cfg["paths"]["cards_dir"]):
        Path(d).mkdir(parents=True, exist_ok=True)
    result = {
        "db": cfg["paths"]["db"],
        "clusters": [{"id": cid, "label": label} for cid, label in
                     zip(cluster_ids, db_mod.SEED_TOPICS)],
        "sources": sync,
    }
    if json_out:
        _json_out(result)
    else:
        print(f"db: {result['db']}")
        print(f"clusters: {len(cluster_ids)} seed topics ({', '.join(db_mod.SEED_TOPICS)})")
        print(f"sources: +{sync['added']} added, {sync['updated']} updated")


@app.command()
def seed(file: Path = typer.Argument(..., help="JSON file of hand-written cards"),
         json_out: bool = typer.Option(False, "--json")):
    """Dev tool (Phase 1): ingest hand-written cards through the full
    validation gates. Card entries:
    title, hook, body_md, diagram_type, diagram_src, figures, anchor_quote,
    source_url, source_body, cluster (topic label), prompts[{question,answer}]"""
    cfg, conn = _load_runtime(json_out)
    data = json.loads(file.read_text())
    cards = data if isinstance(data, list) else data.get("cards", [])
    results = []
    for card in cards:
        figures_json = json.dumps(card.get("figures", [])) if card.get("figures") else None
        card["figures_json"] = figures_json  # validator reads this key
        existing = conn.execute(
            "SELECT id FROM cards WHERE source_url = ? AND title = ?",
            (card["source_url"], card["title"]),
        ).fetchone()
        if existing is not None:
            results.append({"title": card["title"], "ok": True, "card_id": existing["id"], "skipped": True})
            continue
        ok, errors = validate_mod.validate_card(card, card["source_body"],
                                                Path(cfg["_base_dir"]) / "tools")
        if not ok:
            results.append({"title": card["title"], "ok": False, "errors": errors})
            continue
        source_id = None
        src_row = conn.execute(
            "SELECT id FROM sources WHERE url = ?", (card.get("source_url"),)
        ).fetchone()
        if src_row:
            source_id = src_row["id"]
        item_id = db_mod.insert_item(
            conn, url=card["source_url"], title=card["title"], source_id=source_id,
            content_hash=f"seed:{card['source_url']}", raw_path=None,
        )
        card_id = db_mod.insert_card(
            conn, item_id=item_id, cluster_label=card["cluster"],
            title=card["title"], hook=card["hook"], body_md=card["body_md"],
            diagram_type=card["diagram_type"], diagram_src=card["diagram_src"],
            figures_json=figures_json, source_url=card["source_url"],
            anchor_quote=card["anchor_quote"], prompts=card["prompts"],
        )
        conn.execute("UPDATE items SET processed = 1 WHERE id = ?", (item_id,))
        results.append({"title": card["title"], "ok": True, "card_id": card_id})
    conn.commit()
    if json_out:
        _json_out({"seeded": results})
    else:
        for r in results:
            mark = "OK " if r["ok"] else "FAIL"
            print(f"[{mark}] {r['title']}" + (f" (card {r['card_id']})" if r["ok"] else
                  f" — {'; '.join(r['errors'])}"))


@app.command()
def export(since: str | None = typer.Option(None, help="YYYY-MM-DD; only cards created >= date"),
           json_out: bool = typer.Option(False, "--json")):
    """Regenerate the markdown projection (Obsidian-compatible)."""
    cfg, conn = _load_runtime(json_out)
    paths = project_mod.write_cards(conn, cfg["paths"]["cards_dir"], since=since)
    if json_out:
        _json_out({"written": len(paths), "dir": cfg["paths"]["cards_dir"]})
    else:
        print(f"wrote {len(paths)} files to {cfg['paths']['cards_dir']}")


@app.command()
def stats(json_out: bool = typer.Option(False, "--json")):
    """Buffer depth, card counts, grade distribution."""
    cfg, conn = _load_runtime(json_out)
    counts = {
        row["status"]: row["n"]
        for row in conn.execute("SELECT status, COUNT(*) n FROM cards GROUP BY status")
    }
    clusters = conn.execute(
        """SELECT label, alpha, beta, COUNT(c.id) AS cards
           FROM clusters cl LEFT JOIN cards c ON c.cluster_id = cl.id
           GROUP BY cl.id ORDER BY label"""
    ).fetchall()
    grades = conn.execute(
        "SELECT grade, COUNT(*) n FROM grades GROUP BY grade ORDER BY grade"
    ).fetchall()
    total_prompts = conn.execute("SELECT COUNT(*) n FROM prompts").fetchone()["n"]
    data = {
        "cards": counts,
        "total_prompts": total_prompts,
        "clusters": [dict(r) for r in clusters],
        "grades": {r["grade"]: r["n"] for r in grades},
    }
    if json_out:
        _json_out(data)
    else:
        print(f"cards: {counts or 'none'}")
        print(f"prompts: {total_prompts}")
        for c in clusters:
            print(f"cluster {c['label']}: alpha={c['alpha']:.1f} beta={c['beta']:.1f} "
                  f"cards={c['cards']}")
        print(f"grades: {dict((r['grade'], r['n']) for r in grades) or 'none'}")


if __name__ == "__main__":
    app()