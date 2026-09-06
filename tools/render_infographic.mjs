// Render an AntV Infographic DSL spec (stdin) to an SVG string (stdout).
// Usage: echo '<spec>' | node render_infographic.mjs
//
// NOTE: we do NOT use @antv/infographic/ssr's renderToString: it waits for the
// 'loaded' event (= image/font load promises never settle under linkedom,
// hard 10s timeout). We listen for 'rendered' instead, which fires as soon as
// the SVG tree is composed, and export with embedResources:false.
import { parseHTML, DOMParser } from 'linkedom';
import { Infographic, exportToSVG, setDefaultFont } from '@antv/infographic';

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
    const svg = await new Promise((resolve, reject) => {
      infographic.on('rendered', ({ node }) => {
        exportToSVG(node, { embedResources: false })
          .then((s) => resolve(s.outerHTML))
          .catch(reject);
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