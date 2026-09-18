"""A git repository, pinned to the commit it was read at.

The commit matters more than it looks: it is what lets a citation made today
still say which version of the file it was talking about when someone rereads
it after three deploys.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterator

from core.ids import sha256_hex
from ingest.connectors.base import Connector, ConnectorError
from ingest.connectors.filesystem import walk_tree
from ingest.models import WalkEntry

GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "dev.azure.com")
SLUG_SAFE = re.compile(r"[^a-z0-9._-]+")


def looks_like_git_url(target: str) -> bool:
    value = (target or "").strip().lower()
    if not value:
        return False
    if value.startswith(("git@", "git://", "ssh://git@")):
        return True
    if value.endswith(".git"):
        return True
    return any(host in value for host in GIT_HOSTS)


def clone_slug(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git").lower()
    tail = SLUG_SAFE.sub("-", tail).strip("-") or "repo"
    return f"{tail}-{sha256_hex(url.encode('utf-8'))[:8]}"


class GitConnector(Connector):
    name = "git"

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.url = target.strip()
        self.root: Path | None = None
        self._revision = ""

    def prepare(self, clones_dir: Path) -> None:
        destination = Path(clones_dir) / clone_slug(self.url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if (destination / ".git").is_dir():
            self._run(["git", "fetch", "--quiet", "--all"], cwd=destination)
            self._run(["git", "reset", "--quiet", "--hard", "@{upstream}"], cwd=destination)
        else:
            self._run(["git", "clone", "--quiet", self.url, str(destination)])
        self.root = destination
        self._revision = self._run(
            ["git", "rev-parse", "HEAD"], cwd=destination
        ).strip()

    @property
    def uri(self) -> str:
        return self.url

    @property
    def label(self) -> str:
        return clone_slug(self.url).rsplit("-", 1)[0]

    @property
    def revision(self) -> str:
        return self._revision

    def walk(self) -> Iterator[WalkEntry]:
        if self.root is None:
            raise ConnectorError("prepare() has to run before walk()")
        yield from walk_tree(self.root, revision=self._revision)

    @staticmethod
    def _run(command: list[str], cwd: Path | None = None) -> str:
        try:
            done = subprocess.run(
                command,
                cwd=str(cwd) if cwd else None,
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise ConnectorError("git no está instalado o no está en el PATH") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip().splitlines()
            raise ConnectorError(
                f"git {command[1]} falló: {detail[-1] if detail else exc}"
            ) from exc
        return done.stdout
