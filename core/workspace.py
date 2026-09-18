"""Where a workspace keeps its bytes and its rows.

One directory holds everything a run produces, so a NOC analyst can point the
tool at a different folder per environment or wipe one without touching the
others.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .blobs import BlobStore
from .db import Store, connect

ENV_ROOT = "NOC_BRAIN_ROOT"


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def resolve(cls, root: str | Path | None = None) -> "Workspace":
        if root:
            return cls(Path(root).expanduser().resolve())
        from_env = os.environ.get(ENV_ROOT)
        if from_env:
            return cls(Path(from_env).expanduser().resolve())
        return cls(Path.cwd().resolve())

    @property
    def db_path(self) -> Path:
        return self.root / "store" / "ingest.db"

    @property
    def blobs_dir(self) -> Path:
        return self.root / "blobs"

    @property
    def clones_dir(self) -> Path:
        """Working copies for connectors that need one, such as git."""
        return self.root / "store" / "clones"

    def blobs(self) -> BlobStore:
        return BlobStore(self.blobs_dir)

    def store(self) -> Store:
        return Store(connect(self.db_path))
