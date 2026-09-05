# mlearn

A headless microlearning engine. Discovers content from a curated source
allowlist, generates visual 5-minute cards with recall prompts, schedules them
for retention, and learns what to serve next from grade feedback.

See `mlearn-spec.md` (build brief) for the full design.

## Principles

- **SQLite is the single source of truth.** Markdown is a generated
  projection, never read back as state.
- **The only external write path is `grade`** (plus `signal`). Everything
  else is read-only.
- **Every card carries a verbatim anchor quote from its source.** No anchor,
  no card.
- **No invented numbers.** `diagram_type='data'` requires figures extracted
  verbatim from the source.
- **Core is headless.** No Telegram, no HTTP UI, no Hermes coupling in the
  core package.

## Quickstart

```bash
uv sync                 # or: uv pip install -e '.[fsrs,embed,api,test]'
mlearn init             # create db, load sources.yaml, seed topic clusters
mlearn seed seeds/phase1_cards.json   # dev: ingest hand-written cards
mlearn export           # regenerate markdown projection (Obsidian-compatible)
mlearn scout            # candidate discovery + promotion pass
mlearn harvest          # pull new items from the allowlist
mlearn tick             # cron entry: refill the buffer if below floor
mlearn next --count 3   # interleaved discovery + retention, instantly
mlearn grade <prompt_id> <1-4>   # the only external write path
mlearn signal <card_id> <kind>   # more_like_this|less_like_this|skip
mlearn search "query"   # semantic search over cards
mlearn stats            # buffer depth, cluster posteriors, grade dist
```

Every command supports `--json`.

## Build status

- [x] Phase 1 — store and projection (init, seed, export; Obsidian + Mermaid verified)
- [ ] Phase 2 — pipeline (harvest, dedupe, generate, validate)
- [ ] Phase 3 — buffer and serving (tick, next, grade, FSRS)
- [ ] Phase 4 — recommender (Thompson sampling, EMA, decay, exploration floor)
- [ ] Phase 5 — novelty and scouting (wildcard, arm birth, probation)
- [ ] Phase 6 — interfaces (read API, Hermes tool wrappers, Telegram)

## Layout

```
mlearn/
├── mlearn/           # core package (headless)
├── integrations/     # consumer adapters (hermes tool wrappers, …)
├── sources.yaml      # the shared, forkable allowlist (commons)
├── data/             # gitignored: app.db, raw bodies
└── cards/            # gitignored: markdown projection
```