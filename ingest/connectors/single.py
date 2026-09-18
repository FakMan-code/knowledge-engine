"""The two smallest origins: one file on disk, and text someone pasted.

Pasted text earns a stable uri from its own content, so pasting the same
incident note twice updates one document instead of creating two.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from core.ids import sha256_hex
from ingest.connectors.base import Connector, ConnectorError, file_item
from ingest.models import RawItem, WalkEntry


class FileConnector(Connector):
    name = "file"

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.path = Path(target).expanduser().resolve()

    def prepare(self, clones_dir: Path) -> None:
        if not self.path.is_file():
            raise ConnectorError(f"no es un archivo: {self.path}")

    @property
    def uri(self) -> str:
        return self.path.as_posix()

    @property
    def label(self) -> str:
        return self.path.name

    def walk(self) -> Iterator[WalkEntry]:
        yield file_item(self.path, self.path.name)


class TextConnector(Connector):
    name = "text"

    def __init__(self, target: str, label: str = "pegado") -> None:
        super().__init__(target)
        self.text = target
        self._label = label
        self._sha = sha256_hex(target.encode("utf-8"))

    @property
    def uri(self) -> str:
        return f"text:{self._sha[:16]}"

    @property
    def label(self) -> str:
        return self._label

    def walk(self) -> Iterator[WalkEntry]:
        payload = self.text.encode("utf-8")

        def read() -> bytes:
            return payload

        yield RawItem(
            path=f"{self._label}.txt",
            size=len(payload),
            origin=self.uri,
            read=read,
            media_type="text/plain",
        )
