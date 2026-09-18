"""What every extractor has to provide, and the pieces they all share.

The rule that keeps this layer honest: **an extractor never interprets**. It
turns bytes into text that can be pointed at, and stops. Deciding that a line
is a dependency, a log statement or a provider is layer 2's job. Mixing the two
is how the previous engine ended up with claims nobody could verify.

Line-addressable formats get a ``LineRange`` so the citation can be sliced
straight out of the blob later. Only formats where a line means nothing, such
as a spreadsheet cell or a diagram node, fall back to ``Node``.
"""

from __future__ import annotations

import importlib.util
from typing import ClassVar, Iterable, Iterator

from ingest.models import ExtractedUnit, ExtractionUnavailable

MAX_LINES = 120
MAX_CHARS = 4000


def decode_text(data: bytes) -> str:
    """Decode as UTF-8, tolerating a BOM.

    A file in a legacy encoding comes back with replacement characters rather
    than silently wrong text, which makes the problem visible to whoever reads
    the citation.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def line_windows(
    text: str, max_lines: int = MAX_LINES, max_chars: int = MAX_CHARS
) -> Iterator[tuple[int, int, str]]:
    """Cut text into windows, yielding ``(start_line, end_line, chunk)``.

    Lines are 1-indexed and inclusive, matching ``LineRange``. Windows do not
    overlap: this layer is about being able to reopen a fragment, and a later
    layer that wants neighbouring context can read the adjacent window.
    """
    lines = text.splitlines()
    if not lines:
        return

    start = 1
    buffer: list[str] = []
    size = 0
    for offset, line in enumerate(lines, start=1):
        would_be = size + len(line) + 1
        if buffer and (len(buffer) >= max_lines or would_be > max_chars):
            if any(item.strip() for item in buffer):
                yield start, offset - 1, "\n".join(buffer)
            start = offset
            buffer = []
            size = 0
        buffer.append(line)
        size += len(line) + 1
    if buffer and any(item.strip() for item in buffer):
        yield start, start + len(buffer) - 1, "\n".join(buffer)


def find_line(lines: Iterable[str], needle: str, start: int = 0) -> int | None:
    """First 1-indexed line at or after ``start`` containing ``needle``."""
    for offset, line in enumerate(lines):
        if offset >= start and needle in line:
            return offset + 1
    return None


class Extractor:
    name: ClassVar[str] = ""
    media_types: ClassVar[tuple[str, ...]] = ()
    extensions: ClassVar[tuple[str, ...]] = ()
    requirement: ClassVar[str] = ""
    install_hint: ClassVar[str] = ""

    def handles(self, media_type: str, path: str) -> bool:
        if media_type in self.media_types:
            return True
        lowered = path.lower()
        return any(lowered.endswith(suffix) for suffix in self.extensions)

    def available(self) -> bool:
        """False when the optional dependency behind this extractor is missing."""
        if not self.requirement:
            return True
        return importlib.util.find_spec(self.requirement) is not None

    def require(self) -> None:
        if not self.available():
            raise ExtractionUnavailable(self.name, self.requirement, self.install_hint)

    def extract(self, data: bytes, path: str) -> list[ExtractedUnit]:
        raise NotImplementedError
