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
  verbatim from the source. Diagrams must parse (mermaid gate) before a card
  is stored.
- **Core is headless.** No Telegram, no HTTP UI, no Hermes coupling in the
  core package.
- **Cards teach simple ideas.** 200-500 words, pyramid principle (takeaway
  first), easy language for a busy ESL professional. Frameworks (OSI model,
  ...) are enumerated in full; recall prompts probe the structure.

## Quickstart

```bash
uv sync                 # or: uv pip install -e '.[fsrs,embed,api,test]'
mlearn init             # create db, load sources.yaml, seed topic clusters
mlearn seed seeds/phase1_cards.json   # dev: ingest hand-written cards
mlearn export           # regenerate markdown projection (Obsidian-compatible)
mlearn scout            # candidate discovery + promotion pass
mlearn harvest          # pull new items from the allowlist (feeds + Wikipedia)
mlearn prospect --count 5   # pop-science -> timeless ideas -> Wikipedia bridge
mlearn tick             # cron entry: refill the buffer if below floor
mlearn generate --count 12   # LLM card batch (locked against concurrent runs)
mlearn next --count 3   # MORNING push: pure discovery (ready cards only)
mlearn due --count 5    # EVENING push: due recall prompts (spaced repetition)
mlearn ack <prompt_id>  # acknowledge an evening reminder (due +1 day)
mlearn grade <prompt_id> <1-4>   # the only external write path
mlearn signal <card_id> <kind>   # more_like_this|less_like_this|skip|discovery_open
mlearn search "query"   # semantic search over cards
mlearn cards            # browse/paginate cards
mlearn card <id>        # one card + its recall prompts
mlearn stats            # buffer depth, cluster posteriors, grade dist
```

Every command supports `--json`.

## Sources (the forkable commons)

` sources.yaml` is the shared allowlist. Topics seed clusters:
**technology, innovation, finance, mental_health, self_improvement, psychology**.

Two source kinds:

- **RSS feeds** — robots.txt-gated, ETag-cached. Trusted pop-science and
  mechanism sources (Quanta, Aeon, Psyche, IEEE Spectrum, Ars Technica, ...).
- **Wikipedia (kind: wikipedia)** — stable concept pages via the public
  MediaWiki API (~1.2 s/page, 429 Retry-After honored; the API is public
  infrastructure, so this kind intentionally bypasses the robots gate). Each
  entry carries a curated `pages:` catalog and optional discovery `lists:`
  (human-curated "List of ..." articles) — every harvest crawls up to
  `discovery_budget` (5) new concept pages per source, dedupe-aware.

### Concept discovery

1. **List crawl** — per-wiki-source, deterministic, zero LLM cost.
2. **Prospecting** (`mlearn prospect`) — the LLM reviews recent unprocessed
   pop-science items, names timeless ideas, and bridges each to a Wikipedia
   page. Discovery credit stays with the pop-science source. Reviewed ids are
   persisted (`data/prospect_state.json`) so nothing is re-reviewed. Runs
   daily with the Telegram push; `--count N` for manual passes.

Everything discovered still passes the full funnel: anchor gate, validation
gates, topic guardrails, dedupe.

## Build status

- [x] Phase 1 — store and projection (init, seed, export; Obsidian + Mermaid verified)
- [x] Phase 2 — pipeline (harvest, dedupe, generate, validate)
- [x] Phase 3 — buffer and serving (tick, next, grade, FSRS)
- [x] Phase 4 — recommender (Thompson sampling, EMA, decay, exploration floor)
- [x] Phase 5 — novelty and scouting (wildcard slot, arm birth, probation)
- [x] Phase 6 — interfaces (read API `mlearn api`, Hermes tool wrappers, Telegram
  push cron, Hermes Control "Learn" tab)
- [x] Content standard 2026-09 — 200-500 word pyramid cards for busy ESL professionals
- [x] Concept discovery — Wikipedia list crawl + pop-science prospecting
- [x] Psychology topic — seed cluster, guardrail, 5 sources (incl. Wikipedia catalog)
- [x] Two-window day: morning discovery (hook + deep link; tap = implicit signal),
      evening spaced repetition (`due` prompts with deep links)
- [x] Granular taste — embedding-level boost/penalty on top of the topic bandit
      (like this *concept*, not this *category*)

41 tests. Generation runs on the local OpenAI-compatible endpoint
(`deepseek-v4-flash` through opencode-go); a flock guard prevents concurrent
generation runs (tick vs manual batch).

## Deployed on the Mac Mini

- **Morning push** 08:00 — `mlearn_push.py`: concept prospecting, then up to 5
  discovery hooks, each with a `t.me/<bot>?startapp=learn/<id>/disc` deep link;
  tapping the link opens the card in the app and counts as implicit positive
  feedback (`discovery_open`)
- **Evening push** 19:00 — `mlearn_retention.py`: up to 5 due recall prompts,
  each with a `learn/<id>/ret` deep link back to the card; sends are acked
  (due +1 day), FSRS rescheduling happens via in-app grades
- Hourly tick (buffer refill) — generation lock protected
- Obsidian projection into `~/dev/private-notes/Learning/mlearn/cards/`
  (pruned: only live cards are projected)
- Hermes Control "Learn" tab: stats, search, endless browse, card subpages
  with rendered markdown + mermaid (tap-to-enlarge lightbox) + labeled recall
  grading + 👍/👎/skip signals
- Read API: `mlearn api` on 127.0.0.1:8311

## Layout

```
mlearn/
├── mlearn/           # core package (headless)
├── integrations/     # consumer adapters (hermes tool wrappers, …)
├── seeds/            # hand-seeded phase-1 cards (validation fixtures)
├── scripts/          # ops helpers (dup cleanup, spotchecks)
├── sources.yaml      # the shared, forkable allowlist (commons)
├── data/             # gitignored: app.db, raw bodies, prospect state
└── cards/            # gitignored: markdown projection
```