"""Minimal HTML -> text extraction for Factorio auxiliary pages.

We don't want a full markdown converter dependency. The auxiliary pages
have clear structure: we pull out the main ``<div class="container-inner">``
content, strip tags, preserve heading markers and list bullets so the
output is still grep-friendly.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Iterable


class _TextExtractor(HTMLParser):
    # Tags whose text we drop entirely.
    DROP = {"script", "style", "noscript", "svg"}
    # Block tags that inject a newline on close.
    BLOCK = {
        "p",
        "div",
        "section",
        "article",
        "li",
        "tr",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "pre",
        "blockquote",
        "table",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._skip = 0
        self._last_link_href: str | None = None

    # ---- handlers ----------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.DROP:
            self._skip += 1
            return
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            self._out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self._out.append("\n- ")
        elif tag == "br":
            self._out.append("\n")
        elif tag == "pre":
            self._out.append("\n```\n")
        elif tag == "code":
            self._out.append("`")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.DROP:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "pre":
            self._out.append("\n```\n")
        elif tag == "code":
            self._out.append("`")
        elif tag in self.BLOCK:
            self._out.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self._out.append(data)

    # ---- result ------------------------------------------------------
    def text(self) -> str:
        raw = "".join(self._out)
        # Collapse 3+ newlines to 2.
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        # Collapse horizontal whitespace.
        raw = re.sub(r"[ \t]+", " ", raw)
        # Trim trailing whitespace on each line.
        raw = "\n".join(line.rstrip() for line in raw.splitlines())
        return raw.strip()


def html_to_text(html: str) -> str:
    # Isolate the content pane when possible; Factorio uses a predictable
    # wrapper that excludes the nav bar / footer from extraction.
    m = re.search(r'<div class="container-inner">(.*?)</div>\s*</div>\s*</body>', html, re.DOTALL)
    if m:
        html = m.group(1)
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


_HREF_RE = re.compile(r'href="([^"#?]+\.html)(?:#[^"]*)?"', re.IGNORECASE)


def extract_links(html: str) -> list[str]:
    """Return unique .html hrefs in document order."""
    seen: set[str] = set()
    out: list[str] = []
    for href in _HREF_RE.findall(html):
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


def pick_local_links(html: str, prefix: str) -> list[str]:
    """Return hrefs that sit under ``prefix`` (e.g. 'auxiliary/')."""
    links = extract_links(html)
    return [h for h in links if h.startswith(prefix)]


def filter_links(links: Iterable[str], keep_prefixes: tuple[str, ...]) -> list[str]:
    return [h for h in links if h.startswith(keep_prefixes)]
