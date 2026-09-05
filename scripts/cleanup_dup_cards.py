#!/usr/bin/env python3
"""One-off cleanup (2026-09-05): concurrent generation runs (tick + manual regen)
produced duplicate ready cards for the same item. Keep the newest ready card
per item, archive the rest."""
import sqlite3

c = sqlite3.connect("/Users/danielwallner/dev/mlearn/data/app.db")
c.row_factory = sqlite3.Row
dups = c.execute(
    "SELECT item_id, COUNT(*) n FROM cards WHERE status = 'ready' "
    "GROUP BY item_id HAVING n > 1"
).fetchall()
print("dup ready items:", [(d["item_id"], d["n"]) for d in dups])
for d in dups:
    c.execute(
        """UPDATE cards SET status = 'archived'
           WHERE status = 'ready' AND item_id = ?
             AND id < (SELECT MAX(id) FROM cards WHERE item_id = ? AND status = 'ready')""",
        (d["item_id"], d["item_id"]),
    )
c.commit()
for d in dups:
    kept = c.execute(
        "SELECT id, title FROM cards WHERE item_id = ? AND status = 'ready'",
        (d["item_id"],),
    ).fetchall()
    print(" ", d["item_id"], [dict(k) for k in kept])
print("totals:", dict(c.execute("SELECT status, COUNT(*) n FROM cards GROUP BY status").fetchall()))