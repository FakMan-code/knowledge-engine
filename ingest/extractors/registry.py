"""Choosing an extractor, and reporting the ones that cannot run.

Order matters: the first extractor that claims a document wins, and
``TextExtractor`` sits last as the catch-all for anything textual. A document
that nobody claims is not dropped quietly; the pipeline records it as skipped
with the reason, which is what turns an unreadable format into a visible gap
rather than a silent absence.
"""

from __future__ import annotations

from dataclasses import dataclass

from ingest.extractors.base import Extractor
from ingest.extractors.diagram import (
    DrawioExtractor,
    MermaidExtractor,
    PlantUMLExtractor,
    SVGExtractor,
)
from ingest.extractors.image import ImageOCRExtractor
from ingest.extractors.markup import HTMLExtractor, MarkdownExtractor
from ingest.extractors.notebook import NotebookExtractor
from ingest.extractors.office import DocxExtractor, PptxExtractor, XlsxExtractor
from ingest.extractors.pdf import PDFExtractor
from ingest.extractors.structured import (
    CSVExtractor,
    JSONExtractor,
    TOMLExtractor,
    XMLExtractor,
)
from ingest.extractors.text import TextExtractor

# Specific first, catch-all last. Diagrams come before the generic XML reader so
# a .drawio is read as nodes and arrows rather than as anonymous elements.
EXTRACTORS: list[Extractor] = [
    NotebookExtractor(),
    MarkdownExtractor(),
    HTMLExtractor(),
    DrawioExtractor(),
    SVGExtractor(),
    MermaidExtractor(),
    PlantUMLExtractor(),
    JSONExtractor(),
    TOMLExtractor(),
    CSVExtractor(),
    XMLExtractor(),
    PDFExtractor(),
    DocxExtractor(),
    XlsxExtractor(),
    PptxExtractor(),
    ImageOCRExtractor(),
    TextExtractor(),
]


@dataclass(frozen=True)
class Availability:
    name: str
    available: bool
    requirement: str
    hint: str
    formats: tuple[str, ...]


def register(extractor: Extractor, before: str = "text") -> None:
    """Insert an extractor ahead of the catch-all."""
    for index, existing in enumerate(EXTRACTORS):
        if existing.name == before:
            EXTRACTORS.insert(index, extractor)
            return
    EXTRACTORS.append(extractor)


def select_extractor(media_type: str, path: str) -> Extractor | None:
    for extractor in EXTRACTORS:
        if extractor.handles(media_type, path):
            return extractor
    return None


def availability_report() -> list[Availability]:
    return [
        Availability(
            name=extractor.name,
            available=extractor.available(),
            requirement=extractor.requirement,
            hint=extractor.install_hint,
            formats=extractor.extensions or extractor.media_types,
        )
        for extractor in EXTRACTORS
    ]


def missing_dependencies() -> list[Availability]:
    return [item for item in availability_report() if not item.available]
