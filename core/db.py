"""SQLite schema and row operations for layer 1.

The schema is where principle 2 stops being a slogan. ``text_units.blob_sha``
and ``text_units.locator`` are NOT NULL with a foreign key to ``blobs``, so a
unit of text that cannot be reopened in the original bytes cannot be inserted
at all.

History rule: blobs are immutable and every version a document ever had stays
recorded in ``document_versions``. Text units, in contrast, describe the
*current* content of a document, so re-extraction replaces them.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .ids import document_id, source_id, text_unit_id
from .provenance import Evidence, Span, locator_from_dict

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs (
    sha256      TEXT PRIMARY KEY CHECK (length(sha256) = 64),
    size        INTEGER NOT NULL,
    media_type  TEXT NOT NULL,
    first_seen  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,
    connector   TEXT NOT NULL,
    uri         TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    revision    TEXT NOT NULL DEFAULT '',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    UNIQUE (connector, uri)
);

CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    blob_sha    TEXT NOT NULL REFERENCES blobs(sha256),
    media_type  TEXT NOT NULL,
    size        INTEGER NOT NULL,
    relevance   INTEGER NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    UNIQUE (source_id, path)
);

CREATE TABLE IF NOT EXISTS document_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    blob_sha    TEXT NOT NULL REFERENCES blobs(sha256),
    revision    TEXT NOT NULL DEFAULT '',
    seen_utc    TEXT NOT NULL,
    UNIQUE (document_id, blob_sha)
);

CREATE TABLE IF NOT EXISTS text_units (
    id                 TEXT PRIMARY KEY,
    document_id        TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    blob_sha           TEXT NOT NULL REFERENCES blobs(sha256),
    locator_kind       TEXT NOT NULL,
    locator            TEXT NOT NULL,
    ordinal            INTEGER NOT NULL,
    text               TEXT NOT NULL CHECK (length(text) > 0),
    char_count         INTEGER NOT NULL,
    verbatim           INTEGER NOT NULL DEFAULT 1,
    extractor          TEXT NOT NULL,
    relevance          INTEGER NOT NULL,
    sensitivity        TEXT NOT NULL DEFAULT 'none',
    sensitivity_labels TEXT NOT NULL DEFAULT '',
    created_utc        TEXT NOT NULL,
    UNIQUE (document_id, blob_sha, locator, ordinal)
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id           TEXT PRIMARY KEY,
    connector    TEXT NOT NULL,
    target       TEXT NOT NULL,
    source_id    TEXT REFERENCES sources(id) ON DELETE SET NULL,
    started_utc  TEXT NOT NULL,
    finished_utc TEXT,
    status       TEXT NOT NULL,
    counters     TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ingest_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES ingest_runs(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    document_id  TEXT,
    blob_sha     TEXT,
    media_type   TEXT NOT NULL DEFAULT '',
    size         INTEGER NOT NULL DEFAULT 0,
    outcome      TEXT NOT NULL CHECK (
                     outcome IN ('extracted', 'unchanged', 'skipped', 'failed')
                 ),
    reason       TEXT NOT NULL DEFAULT '',
    detail       TEXT NOT NULL DEFAULT '',
    extractor    TEXT NOT NULL DEFAULT '',
    units        INTEGER NOT NULL DEFAULT 0,
    recorded_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id);
CREATE INDEX IF NOT EXISTS idx_units_document ON text_units(document_id);
CREATE INDEX IF NOT EXISTS idx_units_blob ON text_units(blob_sha);
CREATE INDEX IF NOT EXISTS idx_units_sensitivity ON text_units(sensitivity);
CREATE INDEX IF NOT EXISTS idx_events_run ON ingest_events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_outcome ON ingest_events(outcome, reason);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(SCHEMA)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()
    return connection


@dataclass(frozen=True)
class StoredUnit:
    """A text unit as it comes back out of the database."""

    id: str
    document_id: str
    path: str
    evidence: Evidence
    ordinal: int
    extractor: str
    relevance: int
    sensitivity: str
    sensitivity_labels: tuple[str, ...]
    verbatim: bool

    @property
    def span(self) -> Span:
        return self.evidence.span

    @property
    def text(self) -> str:
        return self.evidence.text


class Store:
    """Row operations over the layer 1 tables."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def close(self) -> None:
        self.connection.close()

    def commit(self) -> None:
        self.connection.commit()

    # -- sources ---------------------------------------------------------

    def upsert_source(
        self, connector: str, uri: str, label: str = "", revision: str = ""
    ) -> str:
        identifier = source_id(connector, uri)
        now = utcnow()
        self.connection.execute(
            """
            INSERT INTO sources (id, connector, uri, label, revision, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                label = excluded.label,
                revision = excluded.revision,
                last_seen = excluded.last_seen
            """,
            (identifier, connector, uri, label, revision, now, now),
        )
        return identifier

    def get_source(self, identifier: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM sources WHERE id = ?", (identifier,)
        ).fetchone()

    def list_sources(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute("SELECT * FROM sources ORDER BY last_seen DESC")
        )

    # -- blobs -----------------------------------------------------------

    def record_blob(self, sha: str, size: int, media_type: str) -> None:
        self.connection.execute(
            """
            INSERT INTO blobs (sha256, size, media_type, first_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(sha256) DO NOTHING
            """,
            (sha, size, media_type, utcnow()),
        )

    def has_blob(self, sha: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM blobs WHERE sha256 = ?", (sha,)
        ).fetchone()
        return row is not None

    # -- documents -------------------------------------------------------

    def current_blob_for(self, source: str, path: str) -> str | None:
        row = self.connection.execute(
            "SELECT blob_sha FROM documents WHERE source_id = ? AND path = ?",
            (source, path),
        ).fetchone()
        return row["blob_sha"] if row else None

    def upsert_document(
        self,
        source: str,
        path: str,
        blob_sha: str,
        media_type: str,
        size: int,
        relevance: int,
        revision: str = "",
    ) -> str:
        identifier = document_id(source, path)
        now = utcnow()
        self.connection.execute(
            """
            INSERT INTO documents
                (id, source_id, path, blob_sha, media_type, size, relevance,
                 first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                blob_sha = excluded.blob_sha,
                media_type = excluded.media_type,
                size = excluded.size,
                relevance = excluded.relevance,
                last_seen = excluded.last_seen
            """,
            (identifier, source, path, blob_sha, media_type, size, relevance, now, now),
        )
        self.connection.execute(
            """
            INSERT INTO document_versions (document_id, blob_sha, revision, seen_utc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(document_id, blob_sha) DO NOTHING
            """,
            (identifier, blob_sha, revision, now),
        )
        return identifier

    def get_document(self, identifier: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM documents WHERE id = ?", (identifier,)
        ).fetchone()

    def document_versions(self, identifier: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM document_versions WHERE document_id = ? ORDER BY id",
                (identifier,),
            )
        )

    def list_documents(self, source: str | None = None) -> list[sqlite3.Row]:
        if source:
            return list(
                self.connection.execute(
                    "SELECT * FROM documents WHERE source_id = ? ORDER BY path",
                    (source,),
                )
            )
        return list(self.connection.execute("SELECT * FROM documents ORDER BY path"))

    # -- text units ------------------------------------------------------

    def replace_text_units(self, document: str, units: Sequence["PendingUnit"]) -> int:
        """Text units describe current content, so a re-extraction replaces them."""
        self.connection.execute(
            "DELETE FROM text_units WHERE document_id = ?", (document,)
        )
        now = utcnow()
        rows = []
        for ordinal, unit in enumerate(units):
            span = unit.evidence.span
            rows.append(
                (
                    text_unit_id(document, span, ordinal),
                    document,
                    span.blob_sha,
                    span.locator.kind,
                    json.dumps(span.locator.to_dict(), sort_keys=True, ensure_ascii=False),
                    ordinal,
                    unit.evidence.text,
                    len(unit.evidence.text),
                    int(unit.verbatim),
                    unit.extractor,
                    unit.relevance,
                    unit.sensitivity,
                    ",".join(unit.sensitivity_labels),
                    now,
                )
            )
        self.connection.executemany(
            """
            INSERT INTO text_units
                (id, document_id, blob_sha, locator_kind, locator, ordinal, text,
                 char_count, verbatim, extractor, relevance, sensitivity,
                 sensitivity_labels, created_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return len(rows)

    def get_unit(self, identifier: str) -> StoredUnit | None:
        row = self.connection.execute(
            """
            SELECT u.*, d.path AS path
            FROM text_units u
            JOIN documents d ON d.id = u.document_id
            WHERE u.id = ?
            """,
            (identifier,),
        ).fetchone()
        return _stored_unit(row) if row else None

    def count_units(self, document: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS n FROM text_units WHERE document_id = ?", (document,)
        ).fetchone()
        return row["n"]

    def units_for_document(self, document: str) -> list[StoredUnit]:
        rows = self.connection.execute(
            """
            SELECT u.*, d.path AS path
            FROM text_units u
            JOIN documents d ON d.id = u.document_id
            WHERE u.document_id = ?
            ORDER BY u.ordinal
            """,
            (document,),
        )
        return [_stored_unit(row) for row in rows]

    def find_units(self, needle: str, limit: int = 20) -> list[StoredUnit]:
        """Plain substring lookup. Real retrieval is a later layer's job."""
        rows = self.connection.execute(
            """
            SELECT u.*, d.path AS path
            FROM text_units u
            JOIN documents d ON d.id = u.document_id
            WHERE u.text LIKE ?
            ORDER BY u.relevance DESC, u.char_count ASC
            LIMIT ?
            """,
            (f"%{needle}%", limit),
        )
        return [_stored_unit(row) for row in rows]

    def sensitive_units(self, limit: int = 50) -> list[StoredUnit]:
        rows = self.connection.execute(
            """
            SELECT u.*, d.path AS path
            FROM text_units u
            JOIN documents d ON d.id = u.document_id
            WHERE u.sensitivity != 'none'
            ORDER BY u.sensitivity DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [_stored_unit(row) for row in rows]

    # -- summary ---------------------------------------------------------

    def counts(self) -> dict[str, int]:
        tables = ("sources", "documents", "document_versions", "blobs", "text_units")
        summary = {}
        for table in tables:
            row = self.connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            summary[table] = row["n"]
        return summary


def _stored_unit(row: sqlite3.Row) -> StoredUnit:
    span = Span(blob_sha=row["blob_sha"], locator=locator_from_dict(json.loads(row["locator"])))
    labels = tuple(part for part in (row["sensitivity_labels"] or "").split(",") if part)
    return StoredUnit(
        id=row["id"],
        document_id=row["document_id"],
        path=row["path"],
        evidence=Evidence(span=span, text=row["text"], origin=row["path"]),
        ordinal=row["ordinal"],
        extractor=row["extractor"],
        relevance=row["relevance"],
        sensitivity=row["sensitivity"],
        sensitivity_labels=labels,
        verbatim=bool(row["verbatim"]),
    )


@dataclass(frozen=True)
class PendingUnit:
    """A text unit on its way into the store, before it gets an id."""

    evidence: Evidence
    extractor: str
    relevance: int
    sensitivity: str = "none"
    sensitivity_labels: tuple[str, ...] = ()
    verbatim: bool = True


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
