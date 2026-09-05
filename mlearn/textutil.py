"""HTML -> plain text extraction (stdlib only). Canonical implementation;
tools/html2text.py is a thin CLI wrapper used for offline seed work."""
from __future__ import annotations

import re
from html.parser import HTMLParser


class TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "ul", "ol",
             "blockquote", "pre", "section", "tr", "figcaption"}
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer",
            "header", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BLOCK and not self.skip_depth:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCK and not self.skip_depth:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(data)

    def text(self) -> str:
        raw = "".join(self.out)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


def strip_html(html: str) -> str:
    p = TextExtractor()
    p.feed(html)
    return p.text()