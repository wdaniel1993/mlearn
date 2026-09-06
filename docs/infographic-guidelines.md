# Infographic design & generation guide

Sources:
- https://infographic.antv.vision/learn/infographic-design (AntV design principles)
- https://github.com/antvis/Infographic/tree/main/skills (official LLM skill,
  `infographic-creator` / `infographic-syntax-creator`)
The model-facing contract lives in `mlearn/generate.py` (VISUAL RULE).

## 1. Lane model (our pipeline)

- Exactly ONE main image per card: the **infographic** — `infographic_spec`
  (AntV engine banner, preferred) or the hand-written `infographic_svg`
  fallback (self-contained poster, dark theme, full-bleed, content fills ≥75%
  of canvas height). There is no other main image.
- Mermaid: ZERO TO MANY, ONLY as inline ```mermaid fences embedded in
  `body_md` (each parse-valid, ≤10 lines). `diagram_src` is always empty —
  a hero mermaid fails the hard gate.
- The engine implements the guide's layout specs (4px grid, 24/16/18/14 type
  scale, ≤2 alignments, item spacing 16–24px, 60% graphic / 40% text) — we
  never hand-position anything in the spec lane.

## 2. AntV spec syntax (official skill)

- First line: `infographic <template-name>`.
- `data` (and `theme`) blocks: TWO-space indentation; array items start with
  `- `; keys are `key value`.
- **Icons are mandatory on every main data item** (lists/sequences/compares/
  nodes): `icon <semantic keyword phrase>` — spaces, never hyphens
  (`icon rocket launch`, `icon shield check`, `icon chart line`,
  `icon arrow up`, `icon banknote`, `icon users`). Only pure chart data
  points (values) skip icons.
- Use the ONE data field matching the template family — never mix:
  - `list-*` → `lists` (`- label ... [desc/value/icon]`)
  - `sequence-*` → `sequences` (`- label ... [icon]`, optional `order asc`)
  - `chart-*` → `values` (`- label <category>` / `value <number>`, ordered)
  - `compare-*` → `compares` (binary: exactly 2 roots, each side under its
    own `children`; compare-swot: multiple roots)
  - `relation-*` → `nodes` + `relations` (arrows: `A - 读写 -> B`)
  - `hierarchy-*` → `root` with nested `children`
- `value` is a bare number; units live in `label`/`desc`.
- `theme` block is ALLOWED for richness, constrained:
  - `palette` = 2–5 BRIGHT hexes (relative luminance ≥ 0.25 — the engine's
    background is DARK #1F1F1F and text is white; dark palettes fail the
    luminance gate): `#22d3ee #22c55e #10b981 #f59e0b #f97316 #eab308`
  - `stylize linear-gradient | radial-gradient` only.

## 3. Template selection (VERIFIED against our engine — only these)

| Content | Template | Data field |
|---|---|---|
| stats / trends over time | chart-line-plain-text | values |
| group / ranking of numbers | chart-column-simple | values |
| shares / proportions | chart-pie-donut-plain-text / chart-wordcloud | values |
| step-by-step / evolution | sequence-snake-steps-simple / sequence-funnel-simple | sequences |
| multi-actor interaction | sequence-interaction-default-compact-card | sequences (swimlanes) + relations |
| two-sided comparison | compare-hierarchy-left-right-circle-node-pill-badge | compares (2 roots + children) |
| 2×2 matrix / quadrant | compare-quadrant-quarter-simple-card | compares (4 roots) |
| point list / N facts | list-grid-badge-card / list-row-horizontal-icon-arrow | lists |
| money / value flow | list-waterfall-badge-card | lists (value + desc) |
| level stacks / ladder | list-column-done-list | lists |
| hierarchy / tree | hierarchy-tree-tech-style-badge-card | root + children |
| mind map / branches | hierarchy-mindmap-branch-gradient-capsule-item | root + children |
| node / process graph | relation-dagre-flow-tb-simple-circle-node | nodes + relations |

**AVOID (engine silently drops labels, gate rejects):** compare-binary-*,
compare-swot (roots vanish), list-pyramid-* (6-item cap).

Prefer real graphics (graphs, pies, icons, swimlanes, quadrants) over bare
text boxes; `list-*` grids are the fallback. Every banner gets a bright
palette and item icons — the "alive" requirement. The full verified gallery
lives in the mlearn-ops skill: `references/infographic-gallery.md`.

## 4. Data discipline (model-controlled)

1. ONE message per item. Label = headline fact (number or 1–3 words);
   desc = plain explanation; never repeat the label's numbers in desc.
2. Hero statistic FIRST (visual hierarchy), then supporting items.
3. Parallel phrasing across same-level items.
4. 3–8 items, short title, terse text.
5. Neutral factual tone: no metaphors, emotions, cultural references.
6. Order = reading order.

## 5. Self-check (mirrors the official skill checklist)

- [ ] First line is `infographic <template-name>`
- [ ] ONE data field used, matching the template family (lists/sequences/
      values/compares/nodes/root)
- [ ] Every list/sequence/compare/node item has a sensible `icon`
- [ ] `theme` palette uses 2–5 BRIGHT hexes (luminance ≥ 0.25); stylize is
      linear/radial-gradient at most
- [ ] compare-binary: exactly 2 roots, sides under `children`
- [ ] chart-* uses ordered `values` with `label` + `value`
- [ ] No JSON/explanation/multiple code blocks around the spec

## 6. Gates (auto, per card)

- Spec lane: syntax valid → node SSR render → every declared label present
  in the SVG (truncation detector) → SVG gates (parseable, ≤15 KB, viewBox,
  no scripts/event handlers/external refs, ≤200 chars per text, ≤40 text
  elements) → non-strict layout (engine banners are tight by construction).
- Raw lane: same SVG gates with STRICT layout (full-bleed bg, lowest text
  baseline ≥75% of canvas height).
- Banned everywhere: scripts, `on*=`/`javascript:`, external hrefs, hero
  mermaid, missing main image.