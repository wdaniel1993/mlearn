# Deck example — a tinder-style consumer of the headless API

One static HTML page, zero dependencies. It turns the ready pool into a
swipe deck: see the card (banner + body), then `like` / `dislike` / `skip`.
Exactly the interaction a chat-bot card, a smart-display UI, or a Telegram
mini app needs — three HTTP calls:

| Call | Purpose |
|---|---|
| `GET  /cards?status=ready` | fetch the FIFO pool |
| `GET  /cards/{id}` | full card: `infographic_svg` banner, `body_md`, prompts |
| `POST /decide {card_id, action}` | feedback + consume in one move (`like`/`dislike`/`skip`) |

`like` schedules the card's recall prompts (spaced repetition starts);
`skip` removes it without feedback; the deck shrinks.

## Run

```bash
mlearn api &          # local REST API on 127.0.0.1:8311
# serve the example (any static server works)
python3 -m http.server 8080 --directory examples/deck
# open http://127.0.0.1:8080/
```

If the API should be reachable from another host (tunnel, LAN), that is the
operator's call — the API binds to localhost by default.

## Adapting

- **Body rendering**: the example prints plain text; real consumers render
  the markdown (`marked`, etc.) and the raw inline mermaid fences
  (` ```mermaid `) client-side.
- **Rich previews**: `infographic_svg` is a full inline SVG — render it as-is
  (it is dark-themed and self-contained).
- **No API server?** The CLI covers every call headlessly: `mlearn cards`,
  `mlearn card <id>`, `mlearn decide <id> like` (see integrations/hermes for
  a subprocess wrapper usable as agent/MCP tools).