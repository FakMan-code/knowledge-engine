"""Diagrams: drawio, Mermaid, PlantUML and SVG.

These need no extra dependency, because every one of them is XML or text. That
makes them the cheapest topology evidence in the organisation: somebody already
drew which service talks to which, and the arrow is written down literally.

Parsing an arrow that the file declares is not interpretation, it is reading
the format. Deciding that the arrow means a runtime dependency between two
services is layer 3's job, and it will say so as inferred.
"""

from __future__ import annotations

import base64
import binascii
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib

from ingest.extractors.base import Extractor, decode_text
from ingest.extractors.structured import _fallback, local_name
from ingest.models import ExtractedUnit

from core.provenance import LineRange, Node

MERMAID_EDGE = re.compile(
    r"^\s*(?P<from>[A-Za-z0-9_.-]+)\s*"
    r"(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*"
    r"(?P<arrow>-{2,3}>|={2,3}>|-\.->|-{2,3}x|-{2,3}o|-{2,3})\s*"
    r"(?:\|(?P<label>[^|]*)\|)?\s*"
    r"(?P<to>[A-Za-z0-9_.-]+)"
)
MERMAID_NODE = re.compile(
    r"^\s*(?P<id>[A-Za-z0-9_.-]+)\s*(?:\[(?P<square>[^\]]*)\]|\((?P<round>[^)]*)\))\s*$"
)
PLANTUML_EDGE = re.compile(
    r"^\s*(?P<from>[\"\w.:-]+)\s*(?P<arrow>-+\[?[^\]]*\]?-*>|<-+|\.+>|-{2,})\s*"
    r"(?P<to>[\"\w.:-]+)\s*(?::\s*(?P<label>.+))?$"
)
COMMENT_PREFIXES = ("%%", "'", "//", "@start", "@end", "#")


class MermaidExtractor(Extractor):
    name = "mermaid"
    media_types = ("text/x-mermaid",)
    extensions = (".mmd", ".mermaid")

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        return _lines_with_edges(decode_text(data), MERMAID_EDGE, node_re=MERMAID_NODE)


class PlantUMLExtractor(Extractor):
    name = "plantuml"
    media_types = ("text/x-plantuml",)
    extensions = (".puml", ".plantuml", ".iuml")

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        return _lines_with_edges(decode_text(data), PLANTUML_EDGE)


def _lines_with_edges(
    text: str, edge_re: re.Pattern[str], node_re: re.Pattern[str] | None = None
) -> list[ExtractedUnit]:
    """One unit per meaningful line, with the declared arrow kept in meta."""
    units: list[ExtractedUnit] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            continue
        meta: dict[str, object] = {}
        edge = edge_re.match(line)
        if edge:
            groups = edge.groupdict()
            meta = {
                "edge_from": groups["from"].strip('"'),
                "edge_to": groups["to"].strip('"'),
                "arrow": groups["arrow"].strip(),
            }
            if groups.get("label"):
                meta["edge_label"] = groups["label"].strip()
        elif node_re is not None:
            node = node_re.match(line)
            if node:
                groups = node.groupdict()
                meta = {
                    "node_id": groups["id"],
                    "node_label": (groups.get("square") or groups.get("round") or "").strip(),
                }
        units.append(
            ExtractedUnit(
                LineRange(number, number), stripped, meta, verbatim=stripped == line
            )
        )
    return units


class SVGExtractor(Extractor):
    name = "svg"
    media_types = ("image/svg+xml",)
    extensions = (".svg",)

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        text = decode_text(data)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            return _fallback(text, exc)

        units: list[ExtractedUnit] = []
        index = 0
        for element in root.iter():
            name = local_name(element.tag)
            if name not in {"text", "title", "desc", "tspan"}:
                continue
            content = " ".join("".join(element.itertext()).split())
            if not content:
                continue
            index += 1
            units.append(
                ExtractedUnit(
                    locator=Node(path=f"/svg/{name}[{index}]"),
                    text=content,
                    meta={"tag": name},
                    verbatim=False,
                )
            )
        return units


class DrawioExtractor(Extractor):
    name = "drawio"
    media_types = ("application/x-drawio",)
    extensions = (".drawio", ".dio")

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        text = decode_text(data)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            return _fallback(text, exc)

        units: list[ExtractedUnit] = []
        for page, diagram in enumerate(_diagrams(root), start=1):
            units.extend(_cells(diagram, page))
        return units


def _diagrams(root: ET.Element) -> list[ET.Element]:
    """Yield the model of each page, inflating the compressed form when needed."""
    if local_name(root.tag) == "mxGraphModel":
        return [root]

    models: list[ET.Element] = []
    for diagram in root.iter():
        if local_name(diagram.tag) != "diagram":
            continue
        inner = diagram.find("mxGraphModel")
        if inner is not None:
            models.append(inner)
            continue
        payload = (diagram.text or "").strip()
        inflated = _inflate(payload)
        if inflated is None:
            continue
        try:
            models.append(ET.fromstring(inflated))
        except ET.ParseError:
            continue
    return models


def _inflate(payload: str) -> str | None:
    """drawio stores pages base64-encoded, raw-deflated and URL-quoted."""
    if not payload:
        return None
    try:
        raw = base64.b64decode(payload)
        return urllib.parse.unquote(zlib.decompress(raw, -15).decode("utf-8"))
    except (binascii.Error, zlib.error, UnicodeDecodeError, ValueError):
        return None


def _cells(model: ET.Element, page: int) -> list[ExtractedUnit]:
    labels: dict[str, str] = {}
    cells: list[ET.Element] = []
    for cell in model.iter():
        if local_name(cell.tag) != "mxCell":
            continue
        cells.append(cell)
        identifier = cell.get("id")
        value = " ".join(_strip_html(cell.get("value", "")).split())
        if identifier and value:
            labels[identifier] = value

    units: list[ExtractedUnit] = []
    for cell in cells:
        identifier = cell.get("id", "")
        value = labels.get(identifier, "")
        source, target = cell.get("source"), cell.get("target")
        pointer = f"/diagram[{page}]/mxCell[@id='{identifier}']"
        if source or target:
            ends = f"{labels.get(source or '', source or '?')} -> {labels.get(target or '', target or '?')}"
            text = f"{value}: {ends}" if value else ends
            units.append(
                ExtractedUnit(
                    locator=Node(path=pointer),
                    text=text,
                    meta={
                        "edge_from": labels.get(source or "", source or ""),
                        "edge_to": labels.get(target or "", target or ""),
                        "edge_label": value,
                        "page": page,
                    },
                    verbatim=False,
                )
            )
        elif value:
            units.append(
                ExtractedUnit(
                    locator=Node(path=pointer),
                    text=value,
                    meta={"node_label": value, "page": page},
                    verbatim=False,
                )
            )
    return units


def _strip_html(value: str) -> str:
    """drawio labels carry inline HTML such as <br> and <b>."""
    return re.sub(r"<[^>]+>", " ", value).replace("&nbsp;", " ")
