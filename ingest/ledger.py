"""The ingest ledger: one recorded outcome per document, always.

This is principle 3 made operational. Every document the walk met leaves a row
saying what happened to it and why, so "we have no logs for the wallet service"
can be answered with "nobody ingested them" or "the PDF is a scan and we have no
OCR installed", instead of with silence.

The reason codes are a closed vocabulary on purpose. They are what the coverage
report groups by, so they have to stay comparable across runs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from core.db import Store, utcnow


class Outcome(StrEnum):
    EXTRACTED = "extracted"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"
    FAILED = "failed"


class Reason:
    """Closed vocabulary. Add a constant rather than inventing a string."""

    PRUNED_DIRECTORY = "pruned_directory"
    NO_EXTRACTOR = "no_extractor"
    MISSING_DEPENDENCY = "missing_dependency"
    NOTHING_TO_EXTRACT = "nothing_to_extract"
    LOW_RELEVANCE = "low_relevance"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    EMPTY_FILE = "empty_file"
    READ_ERROR = "read_error"
    EXTRACTOR_ERROR = "extractor_error"
    DEGRADED_PARSE = "degraded_parse"
    UNCHANGED_BLOB = "unchanged_blob"


@dataclass
class RunSummary:
    run_id: str
    connector: str
    target: str
    counters: dict[str, int] = field(default_factory=dict)
    status: str = "running"
    started_utc: str = ""
    finished_utc: str = ""

    @property
    def documents(self) -> int:
        return sum(
            self.counters.get(outcome, 0)
            for outcome in (Outcome.EXTRACTED, Outcome.UNCHANGED, Outcome.SKIPPED, Outcome.FAILED)
        )

    def line(self) -> str:
        parts = [f"{name}={value}" for name, value in sorted(self.counters.items()) if value]
        return f"{self.run_id} {self.connector} {self.target} :: " + (
            ", ".join(parts) or "sin documentos"
        )


@dataclass(frozen=True)
class Gap:
    """A reason documents did not become readable, and how often it happened."""

    outcome: str
    reason: str
    documents: int
    example: str

    def line(self) -> str:
        return f"{self.documents:>5}  {self.outcome}/{self.reason}  p.ej. {self.example}"


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


class Ledger:
    def __init__(self, store: Store) -> None:
        self.store = store

    def start(self, connector: str, target: str, source_id: str | None = None) -> RunSummary:
        run_id = new_run_id()
        started = utcnow()
        self.store.connection.execute(
            """
            INSERT INTO ingest_runs (id, connector, target, source_id, started_utc, status)
            VALUES (?, ?, ?, ?, ?, 'running')
            """,
            (run_id, connector, target, source_id, started),
        )
        return RunSummary(
            run_id=run_id, connector=connector, target=target, started_utc=started
        )

    def record(
        self,
        run: RunSummary,
        path: str,
        outcome: Outcome,
        reason: str = "",
        detail: str = "",
        document_id: str | None = None,
        blob_sha: str | None = None,
        media_type: str = "",
        size: int = 0,
        extractor: str = "",
        units: int = 0,
    ) -> None:
        self.store.connection.execute(
            """
            INSERT INTO ingest_events
                (run_id, path, document_id, blob_sha, media_type, size, outcome,
                 reason, detail, extractor, units, recorded_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                path,
                document_id,
                blob_sha,
                media_type,
                size,
                str(outcome),
                reason,
                detail[:500],
                extractor,
                units,
                utcnow(),
            ),
        )
        run.counters[str(outcome)] = run.counters.get(str(outcome), 0) + 1
        if units:
            run.counters["units"] = run.counters.get("units", 0) + units

    def finish(self, run: RunSummary, status: str = "ok", source_id: str | None = None) -> RunSummary:
        run.status = status
        run.finished_utc = utcnow()
        self.store.connection.execute(
            """
            UPDATE ingest_runs
               SET finished_utc = ?, status = ?, counters = ?,
                   source_id = COALESCE(?, source_id)
             WHERE id = ?
            """,
            (run.finished_utc, status, json.dumps(run.counters, sort_keys=True), source_id, run.run_id),
        )
        self.store.commit()
        return run

    # -- reading back ----------------------------------------------------

    def runs(self, limit: int = 20) -> list[RunSummary]:
        # Timestamps are second-precision, so two runs a moment apart can tie.
        # The rowid breaks it in insertion order and keeps "last run" honest.
        rows = self.store.connection.execute(
            "SELECT * FROM ingest_runs ORDER BY started_utc DESC, rowid DESC LIMIT ?",
            (limit,),
        )
        return [
            RunSummary(
                run_id=row["id"],
                connector=row["connector"],
                target=row["target"],
                counters=json.loads(row["counters"] or "{}"),
                status=row["status"],
                started_utc=row["started_utc"],
                finished_utc=row["finished_utc"] or "",
            )
            for row in rows
        ]

    def last_run(self) -> RunSummary | None:
        runs = self.runs(limit=1)
        return runs[0] if runs else None

    def events(
        self,
        run_id: str | None = None,
        outcome: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses, params = [], []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self.store.connection.execute(
            f"SELECT * FROM ingest_events {where} ORDER BY id LIMIT ?", params
        )
        return [dict(row) for row in rows]

    def gaps(self, run_id: str | None = None) -> list[Gap]:
        """Everything that did not become readable, grouped by why."""
        clause = "AND run_id = ?" if run_id else ""
        params = [run_id] if run_id else []
        rows = self.store.connection.execute(
            f"""
            SELECT outcome, reason, COUNT(*) AS documents, MIN(path) AS example
              FROM ingest_events
             WHERE outcome IN ('skipped', 'failed') {clause}
             GROUP BY outcome, reason
             ORDER BY documents DESC
            """,
            params,
        )
        return [
            Gap(
                outcome=row["outcome"],
                reason=row["reason"],
                documents=row["documents"],
                example=row["example"],
            )
            for row in rows
        ]
