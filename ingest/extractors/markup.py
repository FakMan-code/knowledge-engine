"""Markdown and HTML.

Both are split along the seams the author already put in: headings for
Markdown, block elements for HTML. A section is a better unit than an arbitrary
window because a runbook step or a service description tends to live under one
heading.

Both keep ``LineRange`` locators, so the citation can still be sliced out of the
original bytes and checked.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from ingest.extractors.base import Extractor, MAX_CHARS, decode_text, line_windows
from ingest.models import ExtractedUnit

from core.provenance import LineRange

HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

BLOCK_TAGS = {
    "p",
    "li",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "blockquote",
    "dd",
    "dt",
    "figcaption",
    "caption",
}
DROPPED_TAGS = {"script", "style", "noscript", "svg"}


class MarkdownExtractor(Extractor):
    name = "markdown"
    media_types = ("text/markdown", "text/x-rst")
    extensions = (".md", ".markdown")

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        text = decode_text(data)
        lines = text.splitlines()
        units: list[ExtractedUnit] = []

        for start, end, trail in _sections(lines):
            body = "\n".join(lines[start - 1 : end])
            if not body.strip():
                continue
            meta = {"heading": trail[-1]} if trail else {}
            if trail:
                meta["heading_trail"] = " > ".join(trail)
            if len(body) <= MAX_CHARS:
                units.append(ExtractedUnit(LineRange(start, end), body, meta))
                continue
            # A long section is still one section: keep the heading on every
            # window so a citation from deep inside it stays readable.
            for sub_start, sub_end, chunk in line_windows(body):
                units.append(
                    ExtractedUnit(
                        LineRange(start + sub_start - 1, start + sub_end - 1),
                        chunk,
                        dict(meta),
                    )
                )
        return units


def _sections(lines: list[str]) -> list[tuple[int, int, list[str]]]:
    """Split on headings, returning 1-indexed inclusive ranges and the trail."""
    if not lines:
        return []

    starts: list[tuple[int, int, str]] = []
    fenced = False
    for offset, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING.match(line)
        if match:
            starts.append((offset, len(match.group(1)), match.group(2)))

    if not starts:
        return [(1, len(lines), [])]

    sections: list[tuple[int, int, list[str]]] = []
    if starts[0][0] > 1:
        sections.append((1, starts[0][0] - 1, []))

    trail: list[str] = []
    for index, (line_no, level, title) in enumerate(starts):
        trail = trail[: level - 1]
        while len(trail) < level - 1:
            trail.append("")
        trail.append(title)
        end = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(lines)
        sections.append((line_no, end, [item for item in trail if item]))
    return sections


class HTMLExtractor(Extractor):
    name = "html"
    media_types = ("text/html",)
    extensions = (".html", ".htm")

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        parser = _BlockCollector()
        parser.feed(decode_text(data))
        parser.close()
        return [
            ExtractedUnit(LineRange(start, end), text, {"tag": tag}, verbatim=False)
            for tag, start, end, text in parser.blocks
            if text.strip()
        ]


class _BlockCollector(HTMLParser):
    """Collects the text of block elements along with the lines they span."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, int, int, str]] = []
        self._stack: list[tuple[str, int, list[str]]] = []
        self._dropping = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in DROPPED_TAGS:
            self._dropping += 1
            return
        if tag in BLOCK_TAGS:
            self._stack.append((tag, self.getpos()[0], []))

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED_TAGS:
            self._dropping = max(0, self._dropping - 1)
            return
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                name, start, parts = self._stack.pop(index)
                text = " ".join(" ".join(parts).split())
                if text:
                    self.blocks.append((name, start, max(start, self.getpos()[0]), text))
                break

    def handle_data(self, data: str) -> None:
        if self._dropping or not self._stack:
            return
        self._stack[-1][2].append(data)
