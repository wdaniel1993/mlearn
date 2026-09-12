# UI screenshots

Captures the Telegram mini app (the reference UI) into `docs/screenshots/` —
the images the README links. Requires the mini app to be running and
reachable (defaults assume the Mac Mini deployment: local API
`http://127.0.0.1:8088`, Tailscale URL `http://127.0.0.1:8088/`).

## Setup (one time)

```bash
npm install        # pulls puppeteer-core only (node_modules is gitignored)
```

## Run

```bash
node shoot.js
```

This regenerates `retention-queue.png`, `retention-revealed.png`,
`card-detail.png`, `card-detail-mono.png`, and `deck.png`. The script fails
loudly (non-zero exit) if a captured surface doesn't match expectations
(missing question, wrong grade-button count, reveal not opening, mono
toggle not flipping) — so it doubles as a smoke test of the app's UI wiring.

`SHOT_CARD=<id>` forces the card used for the detail + mono shots (pick a
served card that has an infographic; default: first due card via the
retention queue's "View card" link).

## How it works

1. Mints a **real signed initData** with the mini app's own helper
   (`python3 server.py --make-test-initdata` in ~/dev/tr-chart-app; the bot
   token comes from `~/.hermes/.env`) and exchanges it for a real API token
   against the live server. No fake tokens, no test data — every screenshot
   shows genuine engine output.
2. Drives Chrome for Testing headless (auto-discovered under
   `~/.agent-browser/browsers/`, newest `chrome-*` wins).
3. Stubs **only** `fetch('/api/verify')` in-page (the browser has no
   Telegram client to run the real WebApp handshake); every other request is
   real.

## Env overrides

| Var | Default |
|---|---|
| `SHOT_APP_URL` | `http://127.0.0.1:8088/` |
| `SHOT_API` | `http://127.0.0.1:8088` |
| `SHOT_APP_DIR` | `~/dev/tr-chart-app` |
| `SHOT_CHROME` | newest `~/.agent-browser/browsers/chrome-*` |
| `SHOT_OUT` | `<repo>/docs/screenshots` |
| `SHOT_INIT` | mint at runtime |

## Gotchas

- The app's own Telegram SDK script runs in the page and can overwrite a
  `window.Telegram` stub installed before it — the harness leans on the
  verify-fetch stub + real token instead, which is why it works.
- The `#ret` route and retention queue only exist on a current mini-app
  build — regenerate there, not against a stale checkout.
- Date stamp in the captured button URLs (`&v=...`) is intentional; the
  app ignores it.