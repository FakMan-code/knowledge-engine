"""JSON, TOML, CSV and XML: formats with a shape worth keeping.

These get ``Node`` locators carrying the path to the value, because
``/services/wallet/depends_on`` says something a line number never will. The
node also carries a line hint so the operator can still open the raw file
nearby; the hint is found by scanning the text in document order and is
explicitly a hint, never the identity of the value.

A file that fails to parse is not dropped. It falls back to line windows and
says so in ``parse_error``, so a broken manifest stays readable and the
breakage stays visible.
"""

from __future__ import annotations

import csv
import io
import json
import tomllib
import xml.etree.ElementTree as ET
from typing import Any

from ingest.extractors.base import Extractor, MAX_CHARS, MAX_LINES, decode_text, line_windows
from ingest.models import ExtractedUnit

from core.provenance import LineRange, Node

ROOT_POINTER = "/"


def _fallback(text: str, error: Exception) -> list[ExtractedUnit]:
    reason = f"{type(error).__name__}: {error}"
    return [
        ExtractedUnit(LineRange(start, end), chunk, {"parse_error": reason})
        for start, end, chunk in line_windows(text)
    ]


def _line_hint(lines: list[str], needle: str, cursor: int) -> tuple[int | None, int]:
    """Find ``needle`` at or after ``cursor``, returning the 1-indexed line."""
    for offset in range(cursor, len(lines)):
        if needle in lines[offset]:
            return offset + 1, offset
    return None, cursor


def _walk(value: Any, pointer: str, out: list[tuple[str, str]]) -> None:
    """Emit the deepest nodes that still fit in one unit."""
    rendered = json.dumps(value, indent=2, ensure_ascii=False)
    if len(rendered) <= MAX_CHARS or not isinstance(value, (dict, list)):
        out.append((pointer, rendered))
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _walk(child, f"{pointer.rstrip('/')}/{key}", out)
        return
    for index, child in enumerate(value):
        _walk(child, f"{pointer.rstrip('/')}/{index}", out)


def _units_from_tree(value: Any, text: str) -> list[ExtractedUnit]:
    lines = text.splitlines()
    nodes: list[tuple[str, str]] = []
    _walk(value, ROOT_POINTER, nodes)

    units: list[ExtractedUnit] = []
    cursor = 0
    for pointer, rendered in nodes:
        segment = pointer.rstrip("/").rsplit("/", 1)[-1]
        line = None
        if segment:
            line, cursor = _line_hint(lines, f'"{segment}"', cursor)
            if line is None:
                line, cursor = _line_hint(lines, segment, cursor)
        units.append(
            ExtractedUnit(
                locator=Node(path=pointer, line=line),
                text=rendered,
                meta={"pointer": pointer},
                verbatim=False,
            )
        )
    return units


class JSONExtractor(Extractor):
    name = "json"
    media_types = ("application/json",)
    extensions = (".json",)

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        text = decode_text(data)
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, RecursionError) as exc:
            return _fallback(text, exc)
        return _units_from_tree(value, text)


class TOMLExtractor(Extractor):
    name = "toml"
    media_types = ("application/toml",)
    extensions = (".toml",)

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        text = decode_text(data)
        try:
            value = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            return _fallback(text, exc)
        return _units_from_tree(value, text)


class CSVExtractor(Extractor):
    name = "csv"
    media_types = ("text/csv", "text/tab-separated-values")
    extensions = (".csv", ".tsv")

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        text = decode_text(data)
        lines = text.splitlines()
        delimiter = "\t" if path.lower().endswith(".tsv") else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        try:
            rows = [(reader.line_num, row) for row in reader]
        except csv.Error as exc:
            return _fallback(text, exc)
        if not rows:
            return []

        header_end = rows[0][0]
        body = rows[1:]
        if not body:
            return [_rows_unit(lines, 1, header_end, columns=len(rows[0][1]))]

        units: list[ExtractedUnit] = []
        for index in range(0, len(body), MAX_LINES):
            batch = body[index : index + MAX_LINES]
            # The first window starts at line 1 so it carries the column names;
            # the rest start where the previous one ended. Windows quote the
            # file's own lines, so a citation stays checkable against the bytes.
            start = 1 if index == 0 else body[index - 1][0] + 1
            units.append(_rows_unit(lines, start, batch[-1][0], columns=len(rows[0][1])))
        return units


def _rows_unit(lines: list[str], start: int, end: int, columns: int) -> ExtractedUnit:
    return ExtractedUnit(
        locator=LineRange(start, end),
        text="\n".join(lines[start - 1 : end]),
        meta={"columns": columns, "rows": end - start + 1},
    )


class XMLExtractor(Extractor):
    name = "xml"
    media_types = ("text/xml", "application/xml")
    extensions = (".xml", ".xsd", ".xsl", ".pom")

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        text = decode_text(data)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            return _fallback(text, exc)
        return units_from_xml(root, text)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def units_from_xml(root: ET.Element, text: str) -> list[ExtractedUnit]:
    """Walk an XML tree in document order, emitting elements that carry text."""
    lines = text.splitlines()
    units: list[ExtractedUnit] = []
    cursor = 0

    def visit(element: ET.Element, prefix: str) -> None:
        nonlocal cursor
        counts: dict[str, int] = {}
        for child in element:
            name = local_name(child.tag)
            counts[name] = counts.get(name, 0) + 1
            pointer = f"{prefix}/{name}[{counts[name]}]"
            line, cursor = _line_hint(lines, f"<{name}", cursor)
            content = " ".join((child.text or "").split())
            attributes = " ".join(
                f"{local_name(key)}={value!r}" for key, value in child.attrib.items()
            )
            body = " ".join(part for part in (content, attributes) if part)
            if body:
                units.append(
                    ExtractedUnit(
                        locator=Node(path=pointer, line=line),
                        text=body,
                        meta={"tag": name},
                        verbatim=False,
                    )
                )
            visit(child, pointer)

    visit(root, f"/{local_name(root.tag)}")
    return units
