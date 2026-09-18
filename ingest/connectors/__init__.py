"""Choosing how to reach a source.

Detection is deliberate and boring: a git URL, an existing directory, an
existing file, or pasted text. Anything else is refused out loud with the list
of what would work, because guessing wrong about a source is worse than asking.

An `mcp` connector belongs here when Confluence and Jira arrive. It will
implement the same three properties and the same walk, and nothing downstream
will change.
"""

from __future__ import annotations

from pathlib import Path

from ingest.connectors.base import Connector, ConnectorError
from ingest.connectors.filesystem import FilesystemConnector
from ingest.connectors.git import GitConnector, looks_like_git_url
from ingest.connectors.single import FileConnector, TextConnector

CONNECTORS: dict[str, type[Connector]] = {
    FilesystemConnector.name: FilesystemConnector,
    GitConnector.name: GitConnector,
    FileConnector.name: FileConnector,
    TextConnector.name: TextConnector,
}

PLANNED = ("mcp",)


def select_connector(target: str, kind: str | None = None) -> Connector:
    """Pick a connector for ``target``, or explain why none fits."""
    value = (target or "").strip()
    if not value:
        raise ConnectorError("no se indicó ninguna fuente")

    if kind:
        if kind in PLANNED:
            raise ConnectorError(f"el conector '{kind}' todavía no está implementado")
        if kind not in CONNECTORS:
            known = ", ".join(sorted(CONNECTORS))
            raise ConnectorError(f"conector desconocido '{kind}'. Disponibles: {known}")
        return CONNECTORS[kind](value)

    if looks_like_git_url(value):
        return GitConnector(value)

    path = Path(value).expanduser()
    if path.is_dir():
        return FilesystemConnector(value)
    if path.is_file():
        return FileConnector(value)

    if "://" in value:
        raise ConnectorError(
            f"no sé leer {value}. Hoy entran repos git, carpetas, archivos y texto pegado. "
            "Confluence y Jira llegan por MCP más adelante."
        )
    if "\n" in value or len(value) > 200:
        return TextConnector(value)

    raise ConnectorError(
        f"no existe la ruta '{value}' y no parece una URL de git ni un texto pegado"
    )


__all__ = [
    "CONNECTORS",
    "Connector",
    "ConnectorError",
    "FileConnector",
    "FilesystemConnector",
    "GitConnector",
    "TextConnector",
    "select_connector",
]
