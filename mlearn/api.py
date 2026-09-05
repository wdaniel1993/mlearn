"""Optional read API (spec 9.2). Read-only except POST /grade and /signal.

Binds to localhost by default. Any tunnel/exposure is the operator's concern,
not the package's. Run: `mlearn api` (uvicorn on 127.0.0.1:8311).

sqlite3 connections are per-request (FastAPI handlers run on worker threads;
a connection created in the app thread must not be shared into them).
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from . import config as config_mod
from . import db as db_mod
from . import select as select_mod


class GradeIn(BaseModel):
    prompt_id: int
    grade: int
    latency_ms: int | None = None


class SignalIn(BaseModel):
    card_id: int
    kind: str


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or config_mod.resolve_paths(config_mod.load())
    db_path = cfg["paths"]["db"]
    app = FastAPI(title="mlearn", version="0.3.0")

    def get_conn():
        conn = db_mod.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    @app.get("/health")
    def health():
        return {"ok": True, "db": db_path}

    @app.get("/cards")
    def cards(status: str | None = None, topic: str | None = None,
              limit: int = 20, offset: int = 0,
              conn=Depends(get_conn)):
        limit = max(1, min(limit, 100))
        q = ("SELECT c.id, c.title, c.hook, c.status, c.source_url, c.anchor_quote, "
             "cl.label AS topic, c.created_at, c.is_wildcard "
             "FROM cards c JOIN clusters cl ON cl.id = c.cluster_id WHERE 1=1")
        params: list = []
        if status:
            q += " AND c.status = ?"
            params.append(status)
        if topic:
            q += " AND cl.label = ?"
            params.append(topic)
        q += " ORDER BY c.id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    @app.get("/cards/{card_id}")
    def card(card_id: int, conn=Depends(get_conn)):
        row = conn.execute(
            """SELECT c.id, c.title, c.hook, c.body_md, c.diagram_type, c.diagram_src,
                      c.figures_json, c.source_url, c.anchor_quote, c.status,
                      cl.label AS topic, c.is_wildcard, c.created_at
               FROM cards c JOIN clusters cl ON cl.id = c.cluster_id
               WHERE c.id = ?""", (card_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="card not found")
        prompts = conn.execute(
            "SELECT id, question, answer, due_at, reps, lapses FROM prompts "
            "WHERE card_id = ? ORDER BY id", (card_id,)
        ).fetchall()
        return {**dict(row), "prompts": [dict(p) for p in prompts]}

    @app.get("/next")
    def next_cards(count: int = 3, conn=Depends(get_conn)):
        count = max(1, min(count, 20))
        return select_mod.next_cards(conn, cfg, count)

    @app.get("/due")
    def due(limit: int = 20, conn=Depends(get_conn)):
        rows = conn.execute(
            """SELECT pr.id, pr.question, pr.due_at, c.title, c.id AS card_id
               FROM prompts pr JOIN cards c ON c.id = pr.card_id
               WHERE pr.due_at IS NOT NULL AND pr.due_at <= ?
                     AND c.status = 'served'
               ORDER BY pr.due_at LIMIT ?""",
            (select_mod._iso(select_mod.now()), max(1, min(limit, 100))),
        ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/grade")
    def grade(g: GradeIn, conn=Depends(get_conn)):
        if g.grade not in (1, 2, 3, 4):
            raise HTTPException(status_code=422, detail="grade must be 1-4")
        try:
            res = select_mod.grade_prompt(conn, cfg, g.prompt_id, g.grade,
                                          latency_ms=g.latency_ms)
        except KeyError:
            raise HTTPException(status_code=404, detail="prompt not found")
        return res

    @app.post("/signal")
    def signal(s: SignalIn, conn=Depends(get_conn)):
        if s.kind not in select_mod.SIGNAL_EFFECTS:
            raise HTTPException(status_code=422,
                                detail=f"kind must be one of {sorted(select_mod.SIGNAL_EFFECTS)}")
        try:
            return select_mod.signal(conn, cfg, s.card_id, s.kind)
        except KeyError:
            raise HTTPException(status_code=404, detail="card not found")

    @app.get("/stats")
    def stats(conn=Depends(get_conn)):
        counts = {
            r["status"]: r["n"]
            for r in conn.execute("SELECT status, COUNT(*) n FROM cards GROUP BY status")
        }
        clusters = [
            dict(r)
            for r in conn.execute(
                """SELECT cl.id, cl.label, cl.alpha, cl.beta, cl.is_seed, COUNT(c.id) AS cards
                   FROM clusters cl LEFT JOIN cards c ON c.cluster_id = cl.id
                   GROUP BY cl.id ORDER BY label"""
            )
        ]
        grades = {r["grade"]: r["n"]
                  for r in conn.execute("SELECT grade, COUNT(*) n FROM grades GROUP BY grade")}
        due_now = conn.execute(
            "SELECT COUNT(*) n FROM prompts WHERE due_at IS NOT NULL AND due_at <= ?",
            (select_mod._iso(select_mod.now()),),
        ).fetchone()["n"]
        return {
            "cards": counts, "prompts_due_now": due_now,
            "clusters": clusters, "grades": grades,
        }

    return app


app = create_app()  # uvicorn mlearn.api:app