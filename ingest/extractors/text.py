"""Plain text and source code.

The workhorse. Anything textual that no more specific extractor claims ends up
here, which includes every programming language and, deliberately, YAML: there
is no YAML parser in the standard library, and a docker-compose file read as
addressable lines is more useful than a parse that depends on an extra install.
"""

from __future__ import annotations

from ingest.extractors.base import Extractor, decode_text, line_windows
from ingest.models import ExtractedUnit

from core.provenance import LineRange


class TextExtractor(Extractor):
    name = "text"
    media_types = ()

    def handles(self, media_type: str, path: str) -> bool:
        """Claimed last by the registry, so this only has to accept text."""
        return media_type.startswith("text/") or media_type in {
            "application/x-yaml",
            "application/x-sh",
        }

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        text = decode_text(data)
        return [
            ExtractedUnit(locator=LineRange(start, end), text=chunk)
            for start, end, chunk in line_windows(text)
        ]
