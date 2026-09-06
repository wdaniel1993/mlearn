# Infographic generation guidelines

Source: AntV Infographic design guide — https://infographic.antv.vision/learn/infographic-design
(distilled; the model-facing contract lives in `mlearn/generate.py` VISUAL RULE).

## Lane model
- Prefer `infographic_spec` (AntV engine, wide banner) for numbers, stat-contrasts,
  steps, comparisons. The engine owns layout, typography, spacing, alignment —
  templates implement the guide's specs (4px grid, 24/16/18/14 type scale, ≤2
  alignments, item spacing 16–24px, 60% graphic / 40% text).
- `infographic_svg` (raw hand-written poster) stays as the fallback lane.
- Mermaid stays preferred for mechanisms, structures, flows.

## Template choice (spec lane)
Match the template family to the content structure:
- `list-*` (list-grid-simple, list-column-done-list, list-pyramid-badge-card)
  — modular lists, pyramids, levels, "the N things"
- `sequence-*` (sequence-steps-badge-card, sequence-timeline-plain-text)
  — flows, steps, timelines (path-oriented layouts)
- `compare-*` (compare-swot, compare-binary-horizontal-simple-fold)
  — contrasts, pros/cons (symmetrical balance)
- `chart-*` (chart-column-simple, chart-bar-plain-text)
  — plain quantitative comparison
- `relation-*` (relation-network-simple-circle-node)
  — hub, intersecting relations (radial layout)

## Data discipline (model-controlled)
1. ONE message per item. Label = headline fact (number or 1–3 words);
   desc = plain explanation; never repeat the label's numbers in desc
   (redundancy: the graphic already shows the number).
2. Hero statistic first — visual hierarchy; then supporting items.
3. Parallel phrasing across same-level items.
4. 3–6 items; short title; terse text (banner space is tight).
5. Neutral factual tone: no metaphors, emotions, strong cultural references.
6. Order = reading order (left→right, Z-flow).

## Fallback lane (raw SVG poster, 800×520)
Everything from the guide that the engine would otherwise guarantee:
- Dark theme, full-bleed background, all content within the canvas
  (lowest text ≥ 75% of height — gate-enforced)
- Type hierarchy: headline (large, bold) → one hero stat (biggest) →
  3–4 blocks → takeaway banner; crisp sans-serif stack
- Left-aligned text; generous whitespace around title and groups;
  grouping via containers (rounded rects), grid-aligned padding
- No shadows/gradients abuse, no overlapping text/boxes, no icon clutter
- Banned in ALL lanes: scripts, event handlers, javascript:, foreignObject
  (raw lane), external refs

## Gate guarantees (validated per card)
XML-parseable, ≤15 KB, viewBox with 4 numbers, text caps (200 chars/element,
≤40 elements), tight-canvas rule for the raw lane only (engine banners are
tight by construction); AntV output forced to the dark theme in the renderer
(default 'light' theme text was ~1.5:1 on the app's dark card; dark is ~17:1).