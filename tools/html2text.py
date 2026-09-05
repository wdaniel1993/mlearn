#!/usr/bin/env python3
"""Strip HTML to plain text (stdlib only) for seed source bodies."""
import html
import re
import sys
from html.parser import HTMLParser


class TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "ul", "ol",
             "blockquote", "pre", "section", "tr", "figcaption"}
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer",
            "header", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip_depth = 0
        self.prev_block = False

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

    def text(self):
        raw = "".join(self.out)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


if __name__ == "__main__":
    parser = TextExtractor()
    parser.feed(sys.stdin.read())
    print(parser.text())