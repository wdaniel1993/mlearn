// Mermaid parse gate (C6-class): validates a diagram from stdin via
// mermaid.parse(). Mermaid v11 needs a DOM in Node — polyfill via jsdom
// BEFORE importing (dynamic import). Pure node — no mmdc/Chromium.
// Usage: node parse_mermaid.mjs < diagram.txt   (exit 0 = OK, 1 = parse error)
// Install once:  npm i mermaid jsdom
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>");
Object.defineProperty(globalThis, "window", { value: dom.window, configurable: true });
Object.defineProperty(globalThis, "document", { value: dom.window.document, configurable: true });
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });

const mod = await import("mermaid");
const mermaid = mod.default ?? mod.mermaid;

mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", async () => {
  try {
    await mermaid.parse(input);
    console.log("OK");
    process.exit(0);
  } catch (e) {
    const msg = (e && (e.message || e.toString())) || "parse error";
    console.error("PARSE_ERROR: " + msg.split("\n")[0]);
    process.exit(1);
  }
});
