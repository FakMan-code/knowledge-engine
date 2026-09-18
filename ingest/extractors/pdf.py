"""PDF, one unit per page.

Optional: without ``pypdf`` installed the file is still stored as a blob and
recorded as a gap, so the operator learns that a runbook exists and that one
install would make it readable.

A PDF with no text layer is a scan. It is reported as its own gap rather than
being silently treated as an empty document, because the difference between
"this document says nothing" and "we cannot read this document" is exactly the
kind of thing the coverage report exists to surface.
"""

from __future__ import annotations

import io

from ingest.extractors.base import Extractor
from ingest.models import EmptyExtraction, ExtractedUnit

from core.provenance import Page

try:
    import pypdf
except ImportError:  # optional dependency
    pypdf = None


class PDFExtractor(Extractor):
    name = "pdf"
    media_types = ("application/pdf",)
    extensions = (".pdf",)
    requirement = "pypdf"
    install_hint = "pip install pypdf"

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        self.require()
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages: list[tuple[int, str, tuple[float, float, float, float] | None]] = []
        blank: list[int] = []

        for number, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception as exc:  # pypdf raises a wide range on damaged files
                raise EmptyExtraction("pdf_ilegible", f"página {number}: {exc}") from exc
            if not text:
                blank.append(number)
                continue
            pages.append((number, text, _box(page)))

        if not pages:
            raise EmptyExtraction(
                "pdf_sin_capa_de_texto",
                f"{len(blank)} página(s) sin texto; probablemente escaneado",
            )

        total = len(reader.pages)
        return [
            ExtractedUnit(
                locator=Page(number, bbox=box),
                text=text,
                meta={"pages": total, "pages_without_text": blank} if blank else {"pages": total},
                verbatim=False,
            )
            for number, text, box in pages
        ]


def _box(page) -> tuple[float, float, float, float] | None:
    try:
        media = page.mediabox
        return (float(media.left), float(media.bottom), float(media.right), float(media.top))
    except (AttributeError, TypeError, ValueError):
        return None
