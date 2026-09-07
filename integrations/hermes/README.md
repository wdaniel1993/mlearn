# Agent tool gateway — mlearn

Thin wrappers over the `mlearn` CLI (spec 9.3). Plain subprocess calls with
`--json`; the same wrappers work for any agent/MCP consumer without
modification.

```python
from tools_mlearn import mlearn_next, mlearn_grade, mlearn_signal, mlearn_search, mlearn_stats

cards = mlearn_next(count=3)
mlearn_grade(prompt_id=7, grade=3)     # 1=again 2=hard 3=good 4=easy
mlearn_signal(card_id=4, kind="more_like_this")
hits = mlearn_search("memory consolidation")
```

## Wiring

- **As agent tools**: register each `mlearn_*` function in your agent's tool
  registry (e.g. `mlearn_next` -> tool schema `{count: int}`), or expose them
  through any MCP server that shells out to `tools_mlearn.py`.
- **As cron**: `python3 tools_mlearn.py tick` as the hourly buffer refill
  (`mlearn tick` is the engine-side cron entry).
- **Keys**: only `generate`/`tick` need a provider key (`API_SERVER_KEY` for
  a local OpenAI-compatible endpoint or `OPENROUTER_API_KEY`); `next`,
  `grade`, `signal`, `search`, `stats` are local-only.

## Deck-style consumption

For a tinder/deck UI, prefer the REST API over CLI wrappers — see
[examples/deck/](../../examples/deck/): one static HTML page that pulls
`status=ready` cards, renders them one at a time, and posts `like|dislike|skip`
to `/api/mlearn/decide`. The same three calls are all a chat-bot card flow
needs: `cards?status=ready`, `card?id=`, `decide`.