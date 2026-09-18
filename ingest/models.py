"""The contracts layer 1 passes around.

A connector produces walk entries. An extractor turns bytes into extracted
units. Everything else in the layer is plumbing between those two shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core.provenance import Locator

MAX_BLOB_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class RawItem:
    """One document a connector found, not yet read into memory.

    ``read`` is deferred so the pipeline can refuse an enormous file by size
    before pulling it into memory.
    """

    path: str
    size: int
    origin: str
    read: Callable[[], bytes]
    media_type: str = ""
    revision: str = ""


@dataclass(frozen=True)
class PrunedDirectory:
    """A directory the walk deliberately did not enter.

    Emitted so that skipping ``node_modules`` is a recorded decision rather
    than a silent absence.
    """

    path: str
    reason: str


WalkEntry = RawItem | PrunedDirectory


@dataclass(frozen=True)
class ExtractedUnit:
    """A piece of text an extractor could point back at the bytes it came from.

    ``verbatim`` says whether ``text`` is literally what the blob holds at
    ``locator``. It is true for code and Markdown, and false wherever the
    extractor had to render: HTML with its tags removed, a re-serialised JSON
    node, a PDF page. Only a verbatim unit can be proven byte for byte against
    the source, and the difference has to be visible rather than assumed.
    """

    locator: Locator
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    verbatim: bool = True


class EmptyExtraction(RuntimeError):
    """The extractor understood the file and found nothing to read.

    A scanned PDF with no text layer is the typical case. This is a gap worth
    reporting, not a failure: the blob is stored and the reason is recorded, so
    the coverage report can say what the organisation would gain by OCRing it.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class ExtractionUnavailable(RuntimeError):
    """An extractor exists for this format but its dependency is not installed.

    Carries the install hint so the gap report can tell the operator exactly
    what to install to widen coverage.
    """

    def __init__(self, extractor: str, requirement: str, hint: str = "") -> None:
        super().__init__(f"{extractor} necesita {requirement}")
        self.extractor = extractor
        self.requirement = requirement
        self.hint = hint or f"pip install {requirement}"
