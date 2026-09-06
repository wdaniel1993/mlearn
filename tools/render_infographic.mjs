// Render an AntV Infographic DSL spec (stdin) to an SVG string (stdout).
// Usage: echo '<spec>' | node render_infographic.mjs
//
// NOTE: we do NOT use @antv/infographic/ssr's renderToString: it waits for the
// 'loaded' event (= image/font load promises never settle under linkedom,
// hard 10s timeout). We listen for 'rendered' instead, which fires as soon as
// the SVG tree is composed, and export with embedResources:false.
import { parseHTML, DOMParser } from 'linkedom';
import {
  Infographic, exportToSVG, setDefaultFont,
} from '@antv/infographic';
import { loadResource, waitForSvgLoads } from './node_modules/@antv/infographic/esm/resource/index.js';

setDefaultFont('system-ui, -apple-system, "Segoe UI", Roboto, sans-serif');

const { document, window } = parseHTML(
  '<!DOCTYPE html><html><body><div id="container"></div></body></html>'
);
Object.assign(globalThis, { window, document, DOMParser });
const classes = [
  'HTMLElement', 'HTMLDivElement', 'HTMLSpanElement', 'HTMLImageElement',
  'HTMLCanvasElement', 'HTMLInputElement', 'HTMLButtonElement', 'Element',
  'Node', 'Text', 'Comment', 'DocumentFragment', 'Document', 'XMLSerializer',
  'MutationObserver',
  'SVGElement', 'SVGSVGElement', 'SVGGraphicsElement', 'SVGGElement',
  'SVGPathElement', 'SVGRectElement', 'SVGCircleElement', 'SVGTextElement',
  'SVGLineElement', 'SVGPolygonElement', 'SVGPolylineElement',
  'SVGEllipseElement', 'SVGImageElement', 'SVGDefsElement', 'SVGUseElement',
  'SVGClipPathElement', 'SVGLinearGradientElement', 'SVGRadialGradientElement',
  'SVGStopElement', 'SVGPatternElement', 'SVGMaskElement',
  'SVGForeignObjectElement', 'Image',
];
classes.forEach((name) => { if (window[name]) globalThis[name] = window[name]; });
if (!document.fonts) {
  const fontSet = new Set();
  Object.defineProperty(document, 'fonts', {
    value: {
      add: (f) => fontSet.add(f), delete: (f) => fontSet.delete(f),
      has: (f) => fontSet.has(f), clear: () => fontSet.clear(),
      forEach: (cb) => fontSet.forEach(cb),
      entries: () => fontSet.entries(), keys: () => fontSet.keys(),
      values: () => fontSet.values(),
      [Symbol.iterator]: () => fontSet[Symbol.iterator](),
      get size() { return fontSet.size; },
      get ready() { return Promise.resolve(this); },
      check: () => true, load: () => Promise.resolve([]),
    },
  });
}

let spec = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { spec += chunk; });
process.stdin.on('end', async () => {
  const container = document.getElementById('container');
  let infographic;
  const timer = setTimeout(() => {
    console.error('[render_infographic] timeout');
    process.exit(1);
  }, 20000);
  try {
    infographic = new Infographic({
      container, editable: false, width: 800, height: 520,
      theme: 'dark',
    });
    // Icons are fetched async from a remote service. The engine's internal
    // loads do not survive under linkedom, so PRE-LOAD every icon keyword
    // into the module resource cache: the engine's own loadResource then
    // resolves from cache synchronously and its <use> refs get real defs.
    const keywords = [...spec.matchAll(/^\s*icon\s+(.+)$/gm)]
      .map((m) => m[1].trim()).filter((k) => k.length > 1);
    const preSvg = document.createElementNS(
      'http://www.w3.org/2000/svg', 'svg');
    preSvg.setAttribute('width', '1'); preSvg.setAttribute('height', '1');
    document.body.appendChild(preSvg);
    await Promise.all(keywords.map(async (kw) => {
      try {
        await Promise.race([
          loadResource(preSvg, 'icon', kw, { label: kw }),
          new Promise((r) => setTimeout(r, 6000)),
        ]);
      } catch (e) { /* keyword fetch failed — icon slot stays empty */ }
    }));
    const svg = await new Promise((resolve, reject) => {
      infographic.on('rendered', async ({ node }) => {
        try {
          // Icons/images load asynchronously from a remote service; wait a
          // bounded time for them so the export carries real icon art
          // (previously every <use href="#rsc-..."> referenced a def that
          // never existed -> blank icon slots). embedResources inlines the
          // fetched images as data URIs (self-contained svg; fonts were
          // already pinned to the system stack via setDefaultFont, so no
          // font fetching happens).
          await Promise.race([
            waitForSvgLoads(15_000),
            new Promise((r) => setTimeout(r, 15_000)),
          ]);
          if (process.env.DEBUG_ICONS) {
            const h = node.outerHTML;
            console.error(`[debug] symbols=${(h.match(/<symbol/g) || []).length} `
              + `uses=${(h.match(/<use/g) || []).length} `
              + `rscDefs=${(h.match(/id="rsc-/g) || []).length} `
              + `images=${(h.match(/<image/g) || []).length}`);
          }
          const out = await exportToSVG(node, { embedResources: true });
          // The exporter drops loaded <symbol> defs under linkedom; re-attach
          // any icon symbols that landed in the live tree so every <use> has
          // a real target (blank icon slots bug).
          let s = out.outerHTML;
          const liveSymbols = Array.from(node.querySelectorAll('symbol'))
            .filter((sym) => sym.id && !s.includes(`id="${sym.id}"`));
          if (liveSymbols.length) {
            const frag = liveSymbols.map((sym) => sym.outerHTML).join('');
            s = s.replace(/<\/svg>\s*$/, `<defs>${frag}</defs></svg>`);
          }
          resolve(s);
        } catch (e) {
          reject(new Error(e && e.message ? e.message : JSON.stringify(e)));
        }
      });
      infographic.on('error', (e) => reject(new Error(
        e && e.message ? e.message
          : (typeof e === 'string' ? e : JSON.stringify(e, null, 1)))));
      infographic.render(spec);
    });
    process.stdout.write(svg);
  } catch (e) {
    const errText = e && e.message ? e.message
      : (typeof e === 'string' ? e : JSON.stringify(e, null, 1));
    console.error('[render_infographic] ' + errText);
    process.exit(1);
  } finally {
    clearTimeout(timer);
    if (infographic) infographic.destroy();
  }
});