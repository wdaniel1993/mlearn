"""Embeddings — always local (spec: bge-small-en-v1.5, small enough for a Pi).

Uses fastembed (ONNX runtime) so no torch is needed. Embeddings are stored
as float32 BLOBs in SQLite; the corpus is small enough for brute-force
cosine below ~50k cards.
"""
from __future__ import annotations

import math

import numpy as np

from .db import pack_vec, unpack_vec

MODEL = "BAAI/bge-small-en-v1.5"

_model = None


def get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding  # lazy: heavy import
        _model = TextEmbedding(model_name=MODEL)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return [v.tolist() for v in get_model().embed(texts)]


def embed_one(text: str) -> list[float] | None:
    if not text.strip():
        return None
    return embed([text])[0]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    aa = np.asarray(a, dtype=np.float32)
    bb = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(aa) * np.linalg.norm(bb))
    if denom == 0:
        return 0.0
    return float(np.dot(aa, bb) / denom)


def card_pool(conn) -> list[tuple[int, list[float]]]:
    """(card_id, embedding) for every live card that has one (archived excluded —
    regenerations would dedupe against their own old embeddings)."""
    pool = []
    for row in conn.execute(
        "SELECT id, embedding FROM cards WHERE embedding IS NOT NULL "
        "AND status != 'archived'"
    ):
        vec = unpack_vec(row["embedding"])
        if vec:
            pool.append((row["id"], vec))
    return pool


def similar_to_pool(vec: list[float], pool: list[tuple[int, list[float]]],
                    threshold: float) -> tuple[int, float] | None:
    """Best match above threshold -> (card_id, score), else None."""
    best_id, best = None, threshold
    for cid, other in pool:
        s = cosine(vec, other)
        if s >= best:
            best_id, best = cid, s
    if best_id is None:
        return None
    return best_id, best


def pack(v: list[float]) -> bytes:
    return pack_vec(v)