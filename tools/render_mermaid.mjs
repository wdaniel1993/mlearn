// Render a mermaid diagram (stdin) to SVG under jsdom (no Chromium needed).
// Mermaid v11 render() is DOM-only; text metrics fall back to approximations,
// which is fine for QA (we check non-empty shape + structure, not beauty).
// Used by mlearn/visualqa.py.
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>");
Object.defineProperty(globalThis, "window", { value: dom.window, configurable: true });
Object.defineProperty(globalThis, "document", { value: dom.window.document, configurable: true });
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });

// jsdom lacks constructable stylesheets (mermaid v11 uses them)
// jsdom's CSSStyleSheet is incomplete; mermaid v11 uses constructable
// stylesheets via the bare global — always install a working polyfill.
globalThis.CSSStyleSheet =
dom.window.CSSStyleSheet = class {
    constructor() { this.cssRules = []; }
    replaceSync(css) { this.cssRules = [{ cssText: css }]; }
    insertRule(rule, index = 0) {
      const i = index === undefined || index === null ? this.cssRules.length : index;
      this.cssRules.splice(i, 0, { cssText: rule });
      return i;
    }
    deleteRule() {}
};
if (!dom.window.document.adoptedStyleSheets) {
  Object.defineProperty(dom.window.document, "adoptedStyleSheets", {
    value: [], configurable: true,
  });
}
// jsdom SVG elements lack measurement APIs mermaid relies on
if (!dom.window.SVGElement.prototype.getBBox) {
  dom.window.SVGElement.prototype.getBBox = function () {
    return { x: 0, y: 0, width: 0, height: 0 };
  };
}
if (!dom.window.SVGElement.prototype.getComputedTextLength) {
  dom.window.SVGElement.prototype.getComputedTextLength = function () {
    return 0;
  };
}

const mod = await import("mermaid");
const mermaid = mod.default ?? mod.mermaid;

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "loose",
  theme: "dark",
  fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
});

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", async () => {
  try {
    const { svg } = await mermaid.render("qaDiagram", input.trim());
    process.stdout.write(svg);
  } catch (e) {
    const msg = e && e.message ? e.message : String(e);
    process.stderr.write("MERMAID_RENDER_ERROR: " + msg.slice(0, 400));
    process.exitCode = 1;
  }
});