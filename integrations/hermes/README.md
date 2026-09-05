# Hermes tool gateway — mlearn

Thin wrappers over the `mlearn` CLI (spec 9.3). Plain subprocess calls with
`--json`; the same wrappers work for any MCP consumer without modification.

```python
from tools_mlearn import mlearn_next, mlearn_grade, mlearn_signal, mlearn_search, mlearn_stats

cards = mlearn_next(count=3)
mlearn_grade(prompt_id=7, grade=3)     # 1=again 2=hard 3=good 4=easy
mlearn_signal(card_id=4, kind="more_like_this")
hits = mlearn_search("memory consolidation")
```

## Wiring into Hermes

- **As tools**: register each `mlearn_*` function in the Hermes tool registry
  (e.g. `mlearn_next` -> tool schema `{count: int}`), or expose them through
  an MCP server that shells out to `tools_mlearn.py`.
- **As cron**: `python3 tools_mlearn.py tick` as the hourly buffer refill
  (`mlearn tick` is the engine-side cron entry).
- **Keys**: only `generate`/`tick` need a provider key (`API_SERVER_KEY` for
  the local opencode-go endpoint or `OPENROUTER_API_KEY`); `next`, `grade`,
  `signal`, `search`, `stats` are local-only.

## Telegram push

`~/.hermes/scripts/mlearn_push.py` (daily at 08:00 via Hermes cron) pulls
`next --count 5` and posts each card as a Telegram message to the home
channel. Grading happens in the Hermes Control "Learn" tab or by replying
to the Hermes agent (`mlearn grade <prompt_id> <1-4>`).