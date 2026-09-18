"""What every connector has to provide.

A connector answers three questions: where did this come from, what version of
it are we looking at, and what documents does it contain. Nothing else. Reading
bytes, deciding relevance and extracting text all happen downstream, so adding
Confluence later means writing one class, not touching the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Iterator

from ingest.models import RawItem, WalkEntry


class ConnectorError(RuntimeError):
    """The source could not be reached or is not what it claimed to be."""


class Connector:
    name: ClassVar[str] = ""

    def __init__(self, target: str) -> None:
        self.target = target

    def prepare(self, clones_dir: Path) -> None:
        """Do any fetching needed before walking. Most connectors need none."""

    @property
    def uri(self) -> str:
        """Stable identity of the origin. Two runs over the same source match."""
        raise NotImplementedError

    @property
    def label(self) -> str:
        return self.uri

    @property
    def revision(self) -> str:
        """Version of the whole source, when the origin has one. Git gives a commit."""
        return ""

    def walk(self) -> Iterator[WalkEntry]:
        raise NotImplementedError


def file_item(path: Path, relative: str, origin: str = "", revision: str = "") -> RawItem:
    """Build a raw item without reading the file yet."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0

    def read() -> bytes:
        return path.read_bytes()

    return RawItem(
        path=relative,
        size=size,
        origin=origin or str(path),
        read=read,
        revision=revision,
    )
