#!/usr/bin/env python3
"""Spot-check regenerated cards: word count (200-500), pyramid first sentence,
anchor, prompts answered, mermaid parse via the engine's own gate."""
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path.home() / "dev" / "mlearn"
c = sqlite3.connect(REPO / "data" / "app.db")
c.row_factory = sqlite3.Row

for cid in (45, 46, 47, 50):
    card = c.execute(
        "SELECT id, title, body_md, anchor_quote, diagram_src, status FROM cards WHERE id=?",
        (cid,),
    ).fetchone()
    if not card:
        print(cid, "missing")
        continue
    words = len(card["body_md"].split())
    first = card["body_md"].strip().split(".")[0][:90]
    prompts = c.execute(
        "SELECT COUNT(*) AS n FROM prompts WHERE card_id = ?", (cid,)
    ).fetchone()
    parse = subprocess.run(
        ["node", "tools/parse.mjs"],
        cwd=REPO, input=card["diagram_src"], capture_output=True, text=True,
    )
    in_band = 200 <= words <= 500
    print(f"#{cid} [{card['status']}] {card['title'][:48]}")
    print(f"   words: {words} {'OK' if in_band else '!!OUT OF BAND'} | first: {first}...")
    print(f"   anchor: {card['anchor_quote'][:60]} | prompts: {prompts['n']} | mermaid: {parse.stdout.strip()}")