"""A whole directory tree, recursively.

The previous engine could only admit a single file, which meant a NOC analyst
could not point the tool at the folder where the documentation actually lives.
This is the connector that does the everyday work.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from ingest.connectors.base import Connector, ConnectorError, file_item
from ingest.media import SKIP_DIRS
from ingest.models import PrunedDirectory, WalkEntry


class FilesystemConnector(Connector):
    name = "filesystem"

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.root = Path(target).expanduser().resolve()

    def prepare(self, clones_dir: Path) -> None:
        if not self.root.is_dir():
            raise ConnectorError(f"no es un directorio: {self.root}")

    @property
    def uri(self) -> str:
        return self.root.as_posix()

    @property
    def label(self) -> str:
        return self.root.name

    def walk(self) -> Iterator[WalkEntry]:
        yield from walk_tree(self.root)


def walk_tree(root: Path, revision: str = "") -> Iterator[WalkEntry]:
    """Walk a tree, announcing the directories it refuses to enter."""
    for current, dirs, files in os.walk(root):
        here = Path(current)
        pruned = sorted(name for name in dirs if name in SKIP_DIRS)
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
        for name in pruned:
            relative = (here / name).relative_to(root).as_posix()
            yield PrunedDirectory(path=relative, reason="pruned_directory")
        for name in sorted(files):
            path = here / name
            relative = path.relative_to(root).as_posix()
            yield file_item(path, relative, revision=revision)
