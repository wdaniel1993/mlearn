# RFC: multi-source cards, topic research, catalog wizard, local content sources

Status: proposed (2026-09-07) — no code written yet. Breaking changes + migrations OK.

## 1. Problem

Today a card is born from exactly ONE item of ONE source (`cards.item_id` →
`items.source_id` → one source). Four operator asks change that:

1. **Multi-source cards** — a card should take *all sources* into account, not just
   its origin article.
2. **Topic research** — when a topic is deemed worthy, do a proper research pass so
   the card ships with good pages for further reading.
3. **New-category wizard** — propose a phrase, the LLM returns a category title,
   guardrails, and good sources.
4. **Local content** — point the engine at local files/folders to extract card
   ideas/content.

Goal: one coherent architecture, minimal technical debt, safe migrations, repo stays
public/forkable.

## 2. Core idea

Unify three notions that are currently implicit:

- **source** = a *content source* with a `kind` (`rss` | `wikipedia` | `local`). The
  allowlist is unchanged; local is just another kind.
- **candidate** = a topic deemed card-worthy (from prospect, wildcard, topic wizard,
  or any source). Discovery is decoupled from generation.
- **card references** = the set of items/pages a card *considered* (context) plus
  curated *further reading* for the reader. A card no longer has one source — it has
  a primary anchor plus references.

The pipeline becomes:

```
harvest (rss + wikipedia + local)  → items
discovery (prospect/wildcard/topic)→ candidate topic
research <candidate>               → context items + further-reading links (stored)
generate <candidate>               → card (primary anchor + considered items + links)
```

"All sources taken into consideration" is realized as: candidate **selection** across
the whole corpus + a **bounded synthesis** step (2–3 context items max), NOT unbounded
context — otherwise cards blur and generation latency/cost explodes.

## 3. Target data model (schema v2)

### sources — additive column
```sql
ALTER TABLE sources ADD COLUMN kind TEXT NOT NULL DEFAULT 'rss';  -- rss|wikipedia|local
```
- `wikipedia` moves from `meta.kind` into the column (backfill); `meta` keeps its
  per-kind config (`pages`, `lists`, …).
- `local` sources: `url` = the folder/file locator (keeps UNIQUE + the forkable
  allowlist shape); `meta` = `{"ext": ["md","txt","pdf"], "recursive": true, "ignore": []}`.

### items — no change
Local files re-use items: `url` = absolute file path (or `file://`), `title` =
filename, `content_hash` = sha256. Edits to a local file refresh `raw` + hash; if a
LIVE card references the item, no regeneration (the card is the artifact); otherwise
`processed` resets so a future pass can re-mine it.

### cards — references, not a single source
New tables (cards itself keeps `item_id`/`source_url`/`anchor_quote` = the PRIMARY
anchor, for deck display + verbatim gate):
```sql
CREATE TABLE card_items (
  card_id INTEGER REFERENCES cards(id),
  item_id INTEGER REFERENCES items(id),
  role    TEXT NOT NULL,            -- primary | context
  ord     INTEGER NOT NULL,         -- display/read order
  PRIMARY KEY (card_id, item_id)
);
CREATE TABLE card_links (           -- further reading for the reader
  id      INTEGER PRIMARY KEY,
  card_id INTEGER REFERENCES cards(id),
  url     TEXT NOT NULL,            -- web URL or local file path/link only
  title   TEXT NOT NULL,
  ord     INTEGER NOT NULL
);
```
- `card_items` = what the model *considered* (context synthesis source). `card_links`
  = what the user can click ("further details"). They may overlap (a context item's
  URL can also be a link); the tables stay separate because their contracts differ
  (items: verbatim-fetchable; links: any verified URL).
- Link scope is deliberately minimal: **web links or local-file links only** (url +
  title, no notes, no internal references) — the reader clicks, the card stays lean.
- Verification gate: every `card_links.url` must be fetched + title-verified at
  research time; `anchor_quote` still validates verbatim against the primary item.

### Backfill (idempotent, on `init`)
- `UPDATE sources SET kind='wikipedia' WHERE json_extract(meta,'$.kind')='wikipedia'`
- `INSERT INTO card_items SELECT id, item_id, 'primary', 0 FROM cards`
- version bump `schema_version` 1 → 2 via a `migrate()` runner (additive columns +
  new tables only; no destructive rewrite).

## 4. Feature designs

### 4.1 Topic research (`mlearn research <id|topic>`)
New stage between discovery and generation.
- **Trigger is conditional on material quality** (auto): the primary item is scored
  with a cheap deterministic heuristic — body length (word count) + structural density
  (heading/section count). If the first page is **very detailed** (above threshold,
  e.g. ≥ 1500 words AND ≥ 2 sections), NO further research — the material already
  covers the topic; the card is built from it directly. Below threshold → auto-research.
  Rationale: research exists to compensate for thin material, not to decorate rich
  material (latency + cost stay where they pay off).
- Researcher contract: input candidate → output `{context: [item…], links: [{url,
  title}]}` (bounded: ≤ 3 context items, 2–4 links).
- Providers (pluggable, config `research.provider`):
  - **corpus** (default, zero deps): Wikipedia API (existing harvest machinery,
    robots-polite) + semantic search over our OWN items (fastembed already exists).
  - **web** (optional): search + polite fetch, reusing the blocked-page/bot-wall
    patterns already in this house; never depends on one engine (DDG HTML primary,
    fallbacks). Paywall/stub pages are rejected by the same model-refusal instinct as
    generation.
- Fetched pages become items (kind=`reference` source or their real source) so
  research output is durable and re-usable (a researched page can feed many cards).
- Wire-in: prospect candidate and wildcard candidate both route through research
  before generate; manual `mlearn research <topic>` exposes it directly.

### 4.2 Multi-source generation (generate stage)
- Generate's prompt gains: primary item (anchor stays dominant, verbatim) + up to 3
  context items (synthesize; every factual claim must be supported by at least one
  reference) + emits `further_reading` (title/url only, ≤ 3; web or local-file links).
- New gate: C8 — context items actually cited (no phantom refs), links resolved +
  title-verified, count bounds respected. Primary-anchor + existing gates unchanged.
- No context present (classic single-item path) → card = primary only. Backwards
  compatible; research is an additive quality step.

### 4.3 New-category wizard (`mlearn topic add "<phrase>"` / `--dry-run`)
- One catalog file becomes the single source of truth: **topics move out of
  `config.DEFAULTS` into `sources.yaml`** (`topics:` top-level: name + guardrail +
  optional seed sources). Config keeps a `GENERIC_GUARDRAIL`-style fallback default so
  the example/back-compat survives. This also serves the repo-hygiene goal (catalog =
  the forkable asset, one file, one writer).
- Flow: `topic add "stochastic processes"` → LLM proposes JSON `{name (snake),
  guardrail (existing tone/style), description, sources: [{name,url,feed_url,notes}]}`
  → validation: name unique/valid, guardrail non-empty, each feed parses
  (feedparser) + url reachable (429-safe) → status=`probation` → **preview always
  shown, apply only on confirm** (`--yes` skips the confirmation prompt; `--dry-run`
  prints the proposal and exits without touching anything) → append to sources.yaml
  topics+sources, seed a new cluster.

### 4.4 Local content sources (`mlearn add-local <path> --topic X` / `mlearn ingest <path>`)
- `add-local` writes a persistent `local` source into sources.yaml (folder/file, ext
  allowlist, recursive, ignore globs). `harvest` scans it on demand (no watch/auto-mine
  daemon — ingestion is always explicit).
- `ingest <path>` = the explicit one-shot (no catalog entry): parse path/folder → items
  right now. Same core code; the "direct me at files" ask works without catalog
  ceremony.
- Extraction: md/txt raw; PDF via text layer (pdftotext-style, in-repo); size caps +
  binary/`.git`/`node_modules` ignores (garbage-in guard). Privacy: local sources
  stay operator-local (paths in gitignored config; nothing personal enters the public
  repo).

## 5. Phasing (each slice lands independently, nothing breaks)

1. **Local sources** — `kind` column + `add-local`/`ingest` + harvest scan. No card
   changes. (Self-contained.)
2. **Catalog wizard** — topics into sources.yaml + `topic add`/`--dry-run` + seed
   cluster + validation.
3. **Multi-source references** — migration v2 + `card_items`/`card_links` + generate
   prompt/gate + deck/mini-app/export display of "further reading".
4. **Research pass** — researcher (corpus, then web) + wire into prospect/wildcard.

Phases 1–2 are independent and can build first; 3 is the schema-breaking one; 4
depends on 3 (links need the storage).

## 6. Devil's advocate (risks Daniel will ask about)

- **"All sources" ≠ "more context"**: unbounded multi-source input would blur card
  focus and triple LLM latency/cost. Bounded synthesis (≤2 context) + corpus-wide
  *selection* is the real win; never feed everything.
- **Research latency**: a web research pass adds minutes per card on the local
  endpoint. Mitigation: corpus-first (fast), web research parallelized + only on
  demand (`--research`), research output cached/reusable across cards.
- **Local files = garbage-in risk**: ext allowlist, size caps, ignore globs, and the
  existing verbatim-anchor + topic-guardrail gates are the quality filter. Also
  privacy: local paths never leave the machine; keep them out of the public repo.
- **LLM-proposed sources can be junk/paywalled**: probation status + feed-parse +
  reachability gate before promotion; the harvest-quality checks already exist.
- **Migration risk**: v1→v2 is additive + backfilled on `init`, idempotent, no data
  rewrite; existing DBs upgrade cleanly. Schema is the only breaking surface.
- **Deck/mini-app UI**: "further reading" is a UI addition on the card detail → per
  house rule, plan + confirm before implementing.

## 7. Decisions (2026-09-07, Daniel)

- **Context cap = 3** context items per card (primary anchor + ≤ 3 considered).
- **card_links = web links or local-file links only** — url + title, nothing more
  (no notes, no internal references).
- **Research trigger is automatic but conditional on material quality**: the primary
  item is scored with a deterministic detail heuristic (word count + section count);
  above threshold → no research, below → auto-research. Web provider stays on demand.
- **Local ingestion is explicit only** — `ingest <path>` / `add-local` + `harvest`;
  no folder watching, no auto-mine daemon.
- **Topic wizard always previews** before applying (`--yes` to skip the prompt,
  `--dry-run` to print and exit).

## 8. Repo-hygiene impact

- Catalog consolidates into sources.yaml (topics + sources + kinds) → the forkable
  asset gets cleaner, not messier. No personal data in new schema or docs.
