#!/usr/bin/env node
/**
 * mlearn UI screenshots — captures the Telegram mini app surfaces into
 * docs/screenshots/ (referenced by the README).
 *
 * How it works:
 *  1. Mints a REAL signed initData via the mini app's own helper
 *     (`server.py --make-test-initdata`; BOT_TOKEN comes from ~/.hermes/.env)
 *     and exchanges it for a real API token against the live server.
 *  2. Drives Chrome (headless) with only /api/verify stubbed in-page — every
 *     other call is real, so the captures show genuine data.
 *  3. Deep links: ?startapp=ret (retention queue), ?startapp=deck (deck).
 *
 * Requirements: node >= 18, Chrome for Testing (auto-discovered under
 * ~/.agent-browser/browsers/ or set SHOT_CHROME), the mini app running
 * (default http://127.0.0.1:8088, or set SHOT_API) and reachable under
 * SHOT_APP_URL.
 *
 * Usage:
 *   npm install          # once (installs puppeteer-core; dir is gitignored)
 *   node shoot.js
 *
 * Env overrides:
 *   SHOT_APP_URL  app base URL            (default http://127.0.0.1:8088/)
 *   SHOT_API      live server API origin  (default http://127.0.0.1:8088)
 *   SHOT_APP_DIR  mini-app repo for initData minting (default ~/dev/tr-chart-app)
 *   SHOT_CHROME   explicit Chrome binary  (default: newest ~/.agent-browser/browsers/chrome-*)
 *   SHOT_OUT      output dir              (default <repo>/docs/screenshots)
 *   SHOT_INIT     explicit initData       (skips minting)
 */
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const REPO = path.resolve(__dirname, '..', '..');
const APP_URL = process.env.SHOT_APP_URL || 'http://127.0.0.1:8088/';
const API = process.env.SHOT_API || 'http://127.0.0.1:8088';
const APP_DIR = process.env.SHOT_APP_DIR || path.join(os.homedir(), 'dev', 'tr-chart-app');
const OUT = process.env.SHOT_OUT || path.join(REPO, 'docs', 'screenshots');

function findChrome() {
  if (process.env.SHOT_CHROME) return process.env.SHOT_CHROME;
  const base = path.join(os.homedir(), '.agent-browser', 'browsers');
  if (!fs.existsSync(base)) throw new Error('no ~/.agent-browser/browsers — set SHOT_CHROME');
  const candidates = fs.readdirSync(base)
    .filter((d) => d.startsWith('chrome-'))
    .sort()
    .reverse();
  for (const c of candidates) {
    const bin = path.join(base, c, 'Google Chrome for Testing.app', 'Contents', 'MacOS', 'Google Chrome for Testing');
    if (fs.existsSync(bin)) return bin;
  }
  throw new Error('no Chrome for Testing found under ' + base + ' — set SHOT_CHROME');
}

function mintInitData() {
  if (process.env.SHOT_INIT) return process.env.SHOT_INIT.trim();
  let out;
  try {
    out = execFileSync('python3', [path.join(APP_DIR, 'server.py'), '--make-test-initdata'],
      { cwd: APP_DIR, encoding: 'utf8', timeout: 120, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) {
    throw new Error('initData minting failed: ' + String(e.stderr || e.message).slice(-300));
  }
  const line = out.trim().split(/\r?\n/).filter(Boolean).pop();
  if (!line || !line.includes('hash=')) throw new Error('initData minting failed: ' + out.slice(-200));
  return line;
}

(async () => {
  const INIT = mintInitData();

  // real token from the live server; page-side fetch is stubbed ONLY for
  // /api/verify — everything else is genuine
  const v = await fetch(API + '/api/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ initData: INIT }),
  });
  const { token } = await v.json();
  if (!token) throw new Error('live verify failed: HTTP ' + v.status);

  const browser = await puppeteer.launch({
    executablePath: findChrome(),
    headless: 'new',
    args: ['--no-sandbox', '--force-device-scale-factor=2'],
    defaultViewport: { width: 390, height: 844, deviceScaleFactor: 2 },
  });
  const page = await browser.newPage();
  const PATCH = `
    const __tok = ${JSON.stringify(token)};
    const __init = ${JSON.stringify(INIT)};
    window.Telegram = { WebApp: { initData: __init, initDataUnsafe: { user: { id: 1, first_name: 'Test' }, start_param: '' }, ready() {}, expand() {} } };
    const __fetch = window.fetch.bind(window);
    window.fetch = (url, opts) => {
      if (String(url).includes('/api/verify')) {
        return Promise.resolve(new Response(JSON.stringify({ token: __tok, user: { id: 1, first_name: 'Test' } }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return __fetch(url, opts);
    };
  `;
  await page.evaluateOnNewDocument(PATCH);
  page.on('console', (m) => { if (m.type() === 'error') console.log('CONSOLE ERR:', m.text().slice(0, 110)); });

  fs.mkdirSync(OUT, { recursive: true });
  const shot = async (name) => {
    await new Promise((r) => setTimeout(r, 2500));
    await page.screenshot({ path: path.join(OUT, name + '.png') });
    console.log('shot:', name);
  };

  // 1 — retention queue via deep link (real data: due questions, one per card)
  await page.goto(APP_URL + '?startapp=ret&v=' + Date.now(), { waitUntil: 'networkidle2', timeout: 60000 });
  await page.waitForFunction(() => document.querySelector('#view-lret') && document.querySelector('#view-lret').classList.contains('active'), { timeout: 30000 });
  const q = await page.evaluate(() => ({
    count: (document.querySelector('#ret-count') || {}).textContent || '',
    hasQuestion: document.querySelector('#ret-card').textContent.length > 20,
    hasReveal: !!document.querySelector('#ret-reveal'),
    hasCardLink: !!document.querySelector('#ret-cardlink'),
    gradeButtons: document.querySelectorAll('#ret-card [data-grade]').length,
    hash: location.hash,
  }));
  if (!q.hasQuestion || q.gradeButtons !== 4) throw new Error('retention UI mismatch: ' + JSON.stringify(q));
  console.log('RETENTION OK:', JSON.stringify(q));
  await shot('retention-queue');
  await page.evaluate(() => document.querySelector('#ret-reveal').click());
  await new Promise((r) => setTimeout(r, 600));
  if (!(await page.evaluate(() => document.querySelector('#ret-ans').style.display === 'block')))
    throw new Error('answer reveal failed');
  await shot('retention-revealed');
  await page.evaluate(() => document.querySelector('#ret-cardlink').click());
  await page.waitForFunction(() => document.querySelector('#view-lcard').classList.contains('active'), { timeout: 20000 });
  await shot('card-detail');
  console.log('VIEW CARD:', await page.evaluate(() => ((document.querySelector('#l-card h3') || {}).textContent || '').slice(0, 55)));

  // 2 — discovery deck
  await page.goto(APP_URL + '?startapp=deck&v=' + Date.now(), { waitUntil: 'networkidle2', timeout: 60000 });
  await page.waitForFunction(() => document.querySelector('#m-deck-card') && document.querySelector('#m-deck-card').textContent.length > 50, { timeout: 30000 });
  await shot('deck');
  console.log('DECK:', await page.evaluate(() => ((document.querySelector('#m-deck-card h3') || {}).textContent || '').slice(0, 55)));

  await browser.close();
  console.log('DONE — screenshots in ' + OUT);
})().catch((e) => { console.error('FAIL:', e.message); process.exit(1); });