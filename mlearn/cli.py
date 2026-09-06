"""mlearn CLI (spec 9.1). Every command supports --json."""
from __future__ import annotations

import json
from pathlib import Path

import typer
import yaml

from . import config as config_mod
from . import db as db_mod
from . import generate as generate_mod
from . import harvest as harvest_mod
from . import project as project_mod
from . import scout as scout_mod
from . import select as select_mod
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
    # Topic catalog from config (name + guardrail per topic); falls back to
    # the DEFAULT catalog when the config has no `topics:` section.
    topic_cfg = cfg.get("topics") or []
    labels = [t["name"] for t in topic_cfg] or db_mod.SEED_TOPICS
    cluster_ids = db_mod.ensure_seed_clusters(conn, labels)
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
                     zip(cluster_ids, labels)],
        "sources": sync,
    }
    if json_out:
        _json_out(result)
    else:
        print(f"db: {result['db']}")
        print(f"clusters: {len(cluster_ids)} seed topics ({', '.join(labels)})")
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
            infographic_svg=card.get("infographic_svg"),
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
def harvest(json_out: bool = typer.Option(False, "--json")):
    """Pull new items from trusted/probation sources (no generation)."""
    cfg = config_mod.resolve_paths(config_mod.load())
    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    result = harvest_mod.harvest(conn, cfg)
    if json_out:
        _json_out(result)
    else:
        print(f"new items: {result['new_items']} | skipped {result['skipped']}"
              f" | failed {result['failed']}")
        for issue in result["feed_issues"]:
            print(f"  ! {issue}")


@app.command()
def generate(count: int = typer.Option(12, "--count", min=1,
                                       help="cards to produce this run"),
             harvest_items: bool = typer.Option(True, "--harvest/--no-harvest",
                                                help="fetch new items first"),
             regenerate: bool = typer.Option(False, "--regenerate",
                                             help="archive ready pool and re-roll it"),
             workers: int = typer.Option(1, "--workers", envvar="MLEARN_WORKERS",
                                         min=1, max=8,
                                         help="parallel LLM workers (endpoint serves ~3x concurrently)"),
             json_out: bool = typer.Option(False, "--json")):
    """Discovery pipeline: harvest -> dedupe -> generate -> validate -> enqueue.
    Phase 2 allocation: round-robin over seed topics (bandit arrives in Phase 4)."""
    cfg = config_mod.resolve_paths(config_mod.load())
    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    result = generate_mod.run_generation(conn, cfg, count,
                                         do_harvest=harvest_items,
                                         regenerate=regenerate,
                                         workers=workers)
    if json_out:
        _json_out(result)
    else:
        print(f"made {result['made']} cards"
              f" | dupe-skipped {result['skipped_dupes']} | failed {result['failed']}")
        if result["card_ids"]:
            print("card ids: " + ", ".join(map(str, result["card_ids"])))


@app.command()
def improve(card_ids: list[int] = typer.Argument(None,
                                                 help="card id(s) to improve in place"),
            note: str = typer.Option("", "--note", "-n",
                                     help="what to improve (free text)"),
            scope: str = typer.Option("content", "--scope",
                                      help="content|banner|all"),
            fix_abbr: bool = typer.Option(False, "--fix-abbr",
                                          help="auto-improve ready cards with "
                                               "unexplained abbreviations"),
            json_out: bool = typer.Option(False, "--json")):
    """Improve card(s) IN PLACE: same id, only the fields in scope change,
    full validation gates re-run, the old card stays untouched on failure.
    Never burns the source item or the topic (unlike archive+re-roll)."""
    from . import improve as improve_mod

    cfg = config_mod.resolve_paths(config_mod.load())
    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    targets: list[tuple[int, str]] = []
    if fix_abbr or card_ids:
        rows = conn.execute(
            "SELECT id, title, hook, body_md, anchor_quote FROM cards "
            "WHERE status = 'ready' ORDER BY id").fetchall()
        wanted = set(card_ids or [])
        for r in rows:
            if wanted and r["id"] not in wanted:
                continue
            bad = validate_mod.unexplained_abbrs(
                r["title"], r["hook"], r["body_md"], r["anchor_quote"])
            if fix_abbr:
                if bad:
                    targets.append(
                        (r["id"], "Spell out at first use: " + ", ".join(bad[:10])))
            else:
                targets.append((r["id"], note))
        if fix_abbr:
            missing = wanted - {r["id"] for r in rows}
            for mid in sorted(missing):
                targets.append((mid, ""))  # will report card not found
    results = []
    for cid, cnote in targets:
        results.append(improve_mod.improve_card(
            conn, cfg, cid, note=cnote, scope=scope))
    ok_all = all(r["ok"] for r in results)
    if results:
        project_mod.write_cards(conn, cfg["paths"]["cards_dir"])
    if json_out:
        _json_out({"results": results, "ok_all": ok_all})
    else:
        for r in results:
            if r["ok"]:
                print(f"card {r['card_id']}: improved ({', '.join(r['changed'])}) "
                      f"in {r['attempts']} attempt(s)")
            else:
                print(f"card {r['card_id']}: NOT improved — {r.get('error', 'unknown')} "
                      "(old card kept)")
        if not results:
            print("no cards matched")


@app.command()
def next(count: int = typer.Option(1, "--count", min=1),
         json_out: bool = typer.Option(False, "--json")):
    """Serve interleaved cards + due prompts (instant; never generates)."""
    cfg, conn = _load_runtime(json_out)
    result = select_mod.next_cards(conn, cfg, count)
    if json_out:
        _json_out(result)
    else:
        if not result["cards"]:
            print(f"nothing to serve ({result.get('reason', '?')})")
            return
        for c in result["cards"]:
            print(f"[{c['kind']}] #{c['card_id']} {c['title']} ({c['topic']})")
            print(f"  prompts: " + ", ".join(str(p["prompt_id"]) for p in c["prompts"]))


@app.command()
def grade(prompt_id: int = typer.Argument(...),
          grade_value: int = typer.Argument(..., help="1=again 2=hard 3=good 4=easy"),
          json_out: bool = typer.Option(False, "--json")):
    """The only external write path (C2): FSRS + bandit + EMA update."""
    cfg, conn = _load_runtime(json_out)
    result = select_mod.grade_prompt(conn, cfg, prompt_id, grade_value)
    if json_out:
        _json_out(result)
    else:
        print(f"prompt {result['prompt_id']}: grade {result['grade']} -> due {result['due_at']}"
              f" (stability {result['stability']:.2f}, difficulty {result['difficulty']:.2f})")


@app.command()
def signal(card_id: int = typer.Argument(...),
           kind: str = typer.Argument(..., help="more_like_this|less_like_this|skip|opened_source|discovery_open"),
           json_out: bool = typer.Option(False, "--json")):
    """Explicit taste signal on a card (separate from grades)."""
    cfg, conn = _load_runtime(json_out)
    result = select_mod.signal(conn, cfg, card_id, kind)
    if json_out:
        _json_out(result)
    else:
        print(f"signal {result['kind']} on card {result['card_id']}"
              f" -> {result['cluster']} alpha={result['alpha']:.2f} beta={result['beta']:.2f}")


@app.command()
def decide(card_id: int = typer.Argument(...),
           action: str = typer.Argument(..., help="like|dislike|skip"),
           json_out: bool = typer.Option(False, "--json")):
    """Tinder-mode decision: feedback + consume in one move (deck shrinks)."""
    cfg, conn = _load_runtime(json_out)
    result = select_mod.decide(conn, cfg, card_id, action)
    if json_out:
        _json_out(result)
    else:
        print(f"decide {result['action']} on card {result['card_id']}"
              f" -> next ready: {result['next_ready']}")


@app.command()
def due(count: int = typer.Option(5, "--count", min=1, max=20),
        json_out: bool = typer.Option(False, "--json")):
    """Due recall prompts for the EVENING spaced-repetition push."""
    cfg, conn = _load_runtime(json_out)
    prompts = select_mod.due_prompts(conn, count)
    if json_out:
        _json_out({"prompts": prompts, "total": len(prompts)})
    else:
        if not prompts:
            print("no prompts due")
        for p in prompts:
            print(f"#{p['prompt_id']} [{p['topic']}] {p['title'][:40]} :: {p['question'][:70]}")


@app.command()
def ack(prompt_id: int = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json")):
    """Acknowledge an evening reminder (reviewed; due pushed +1 day)."""
    cfg, conn = _load_runtime(json_out)
    result = select_mod.ack_prompt(conn, prompt_id)
    if json_out:
        _json_out(result)
    else:
        print(f"ack {result}" if result.get("acked") else f"error: {result}")


@app.command()
def search(query: str = typer.Argument(...),
           limit: int = typer.Option(10, "--limit", min=1, max=50),
           offset: int = typer.Option(0, "--offset", min=0),
           status: str | None = typer.Option(None, "--status", help="ready|served"),
           json_out: bool = typer.Option(False, "--json")):
    """Semantic search over cards (paginated, browse-filter friendly)."""
    cfg, conn = _load_runtime(json_out)
    res = select_mod.search(conn, cfg, query, limit=limit, offset=offset, status=status)
    if json_out:
        _json_out(res)
    else:
        print(f"{res['total']} matches")
        for h in res["cards"]:
            print(f"  #{h['id']} [{h['topic']}] {h['score']:.2f} {h['title']}")


@app.command()
def tick(json_out: bool = typer.Option(False, "--json")):
    """Cron entry: refill buffer below floor, run weekly decay, report ready depth."""
    cfg = config_mod.resolve_paths(config_mod.load())
    conn = db_mod.connect(cfg["paths"]["db"])
    db_mod.init_db(conn)
    ready = conn.execute("SELECT COUNT(*) n FROM cards WHERE status = 'ready'").fetchone()["n"]
    result = {"ready": ready, "target": cfg["buffer_target"], "floor": cfg["buffer_floor"],
              "refill": False, "generated": 0, "decay": {"decayed": 0}}
    if ready < cfg["buffer_floor"]:
        result["refill"] = True
        gen = generate_mod.run_generation(conn, cfg, cfg["batch_size"])
        result["generated"] = gen["made"]
    result["decay"] = select_mod.decay_clusters(conn, cfg)
    if json_out:
        _json_out(result)
    else:
        print(f"ready {result['ready']} (floor {result['floor']}, target {result['target']})"
              + (f" -> refilled {result['generated']}" if result["refill"] else " -> ok"))


@app.command()
def api(port: int = typer.Option(8311, "--port", min=1, max=65535),
        host: str = typer.Option("127.0.0.1", "--host")):
    """Serve the optional read API (localhost by default)."""
    import uvicorn
    from .api import create_app
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


@app.command()
def prospect(count: int = typer.Option(5, "--count", min=1, max=20),
             json_out: bool = typer.Option(False, "--json")):
    """Prospect pop-science items for timeless ideas; bridge to Wikipedia pages."""
    from . import prospect as prospect_mod
    cfg, conn = _load_runtime(json_out)
    result = prospect_mod.prospect(conn, cfg, count=count)
    if json_out:
        _json_out(result)
    else:
        print(f"reviewed {result['reviewed']} | bridged {result['bridged']} "
              f"| seen total {result['seen_total']}")
        for idea in result.get("ideas", []):
            print(f"  idea: {idea['idea']}  (from {idea['from']}, item {idea['id']})")


@app.command()
def scout(json_out: bool = typer.Option(False, "--json")):
    """Candidate discovery + probation promotion pass + feed adoption."""
    cfg, conn = _load_runtime(json_out)
    result = scout_mod.run_scout(conn, cfg)
    if json_out:
        _json_out(result)
    else:
        print(f"candidates: {len(result['candidates'])}"
              f" | adopted to probation: {len(result['adopted'])}")
        promo = result["promotion"]
        print(f"promotion: +{len(promo['promoted'])} trusted"
              f" | {len(promo['blacklisted'])} blacklisted"
              f" | {len(promo['stayed'])} stayed (baseline {promo['baseline']})")
        for c in result["candidates"]:
            print(f"  candidate: {c['name']} ({c['topic']}, {c['citations']} cites)")


@app.command()
def card(card_id: int = typer.Argument(...),
         json_out: bool = typer.Option(False, "--json")):
    """Single card + its prompts (detail view for UIs)."""
    cfg, conn = _load_runtime(json_out)
    row = conn.execute(
        """SELECT c.*, cl.label AS topic FROM cards c JOIN clusters cl ON cl.id = c.cluster_id
           WHERE c.id = ?""", (card_id,)
    ).fetchone()
    if row is None:
        if json_out:
            _json_out({"error": "card not found"})
        else:
            print("card not found")
        raise typer.Exit(1)
    prompts = conn.execute(
        "SELECT id, question, answer, due_at, reps, lapses FROM prompts "
        "WHERE card_id = ? ORDER BY id", (card_id,)
    ).fetchall()
    data = {**dict(row), "prompts": [dict(p) for p in prompts]}
    if json_out:
        _json_out(data)
    else:
        print(f"#{data['id']} [{data['topic']}] {data['title']} ({data['status']})")
        print(f"  prompts: {len(data['prompts'])}")


@app.command()
def cards(status: str | None = typer.Option(None, help="ready|served|archived"),
          topic: str | None = typer.Option(None),
          q: str | None = typer.Option(None, "--q", help="semantic filter over the browse set"),
          limit: int = typer.Option(20, "--limit", min=1, max=100),
          offset: int = typer.Option(0, "--offset", min=0),
          json_out: bool = typer.Option(False, "--json")):
    """Paginated card browse (for UIs/endless scroll). With --q, the browse set is
    ranked semantically instead of by insertion order."""
    cfg, conn = _load_runtime(json_out)
    if q:
        data = select_mod.search(conn, cfg, q, limit=limit, offset=offset, status=status)
        data["limit"], data["offset"] = limit, offset
        if json_out:
            _json_out(data)
        else:
            print(f"{data['total']} matches (offset {offset})")
            for h in data["cards"]:
                print(f"  #{h['id']} [{h['topic']}] {h['score']:.2f} {h['title']}")
        return
    where, params = "WHERE 1=1", []
    if status:
        where += " AND c.status = ?"
        params.append(status)
    else:
        where += " AND c.status != 'archived'"
    if topic:
        where += " AND cl.label = ?"
        params.append(topic)
    total = conn.execute(
        f"SELECT COUNT(*) n FROM cards c JOIN clusters cl ON cl.id = c.cluster_id {where}",
        params,
    ).fetchone()["n"]
    rows = conn.execute(
        f"""SELECT c.id, c.title, c.hook, c.status, c.source_url, c.anchor_quote,
                   c.diagram_type, c.is_wildcard, c.created_at, c.served_at,
                   cl.label AS topic
            FROM cards c JOIN clusters cl ON cl.id = c.cluster_id {where}
            ORDER BY c.id ASC LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    data = {"total": total, "limit": limit, "offset": offset,
            "cards": [dict(r) for r in rows]}
    if json_out:
        _json_out(data)
    else:
        print(f"{len(rows)} of {total} cards (offset {offset})")


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