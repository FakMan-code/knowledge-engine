"""Jupyter notebooks.

A notebook is JSON, but chopping it by JSON pointer would scatter a single cell
across units and bury the code in metadata. One cell is one unit.
"""

from __future__ import annotations

import json

from ingest.extractors.base import Extractor, decode_text
from ingest.extractors.structured import _fallback, _line_hint
from ingest.models import ExtractedUnit

from core.provenance import Node


class NotebookExtractor(Extractor):
    name = "notebook"
    media_types = ("application/x-ipynb+json",)
    extensions = (".ipynb",)

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        text = decode_text(data)
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            return _fallback(text, exc)

        cells = document.get("cells")
        if not isinstance(cells, list):
            return _fallback(text, ValueError("el notebook no tiene una lista 'cells'"))

        lines = text.splitlines()
        units: list[ExtractedUnit] = []
        cursor = 0
        for index, cell in enumerate(cells):
            if not isinstance(cell, dict):
                continue
            source = cell.get("source", "")
            body = "".join(source) if isinstance(source, list) else str(source)
            if not body.strip():
                continue
            kind = cell.get("cell_type", "code")
            line, cursor = _line_hint(lines, '"cell_type"', cursor)
            units.append(
                ExtractedUnit(
                    locator=Node(path=f"/cells/{index}", line=line),
                    text=body,
                    meta={"cell_type": kind},
                    verbatim=False,
                )
            )
        return units
