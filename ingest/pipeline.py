"""The ingest run: connector in, addressable text and a ledger out.

The shape of this function is the contract of layer 1. Every path through it
ends in exactly one ledger event, which is what makes "nothing disappears in
silence" checkable rather than aspirational.

Two decisions worth knowing:

Low relevance does not mean discarded. A vendored bundle gets its blob stored
and its document row written, so it stays addressable, and only extraction is
skipped. Someone can still ask for it by path.

An unchanged blob short-circuits extraction. Re-running over a 40k file
repository after one edit should cost one extraction, not forty thousand.
"""

from __future__ import annotations

from core.blobs import BlobStore
from core.db import PendingUnit, Store
from core.provenance import Evidence, ProvenanceError, Span
from core.workspace import Workspace
from ingest.connectors import Connector, select_connector
from ingest.extractors import select_extractor
from ingest.ledger import Ledger, Outcome, Reason, RunSummary
from ingest.media import guess_media_type, is_textual, looks_binary
from ingest.models import (
    EmptyExtraction,
    ExtractionUnavailable,
    MAX_BLOB_BYTES,
    PrunedDirectory,
    RawItem,
)
from ingest.triage import Relevance, relevance_for_path, scan_sensitivity


def ingest(
    target: str,
    workspace: Workspace | None = None,
    kind: str | None = None,
) -> RunSummary:
    workspace = workspace or Workspace.resolve()
    connector = select_connector(target, kind)
    store = workspace.store()
    try:
        return run_connector(connector, store, workspace.blobs(), workspace.clones_dir)
    finally:
        store.close()


def run_connector(
    connector: Connector, store: Store, blobs: BlobStore, clones_dir
) -> RunSummary:
    connector.prepare(clones_dir)
    source_id = store.upsert_source(
        connector.name, connector.uri, connector.label, connector.revision
    )
    ledger = Ledger(store)
    run = ledger.start(connector.name, connector.uri, source_id)

    for entry in connector.walk():
        if isinstance(entry, PrunedDirectory):
            ledger.record(
                run,
                entry.path,
                Outcome.SKIPPED,
                entry.reason,
                detail="directorio no recorrido por convención",
            )
            continue
        _ingest_item(entry, connector, store, blobs, ledger, run, source_id)

    store.commit()
    return ledger.finish(run, "ok", source_id)


def _ingest_item(
    item: RawItem,
    connector: Connector,
    store: Store,
    blobs: BlobStore,
    ledger: Ledger,
    run: RunSummary,
    source_id: str,
) -> None:
    if item.size == 0:
        ledger.record(run, item.path, Outcome.SKIPPED, Reason.EMPTY_FILE)
        return
    if item.size > MAX_BLOB_BYTES:
        ledger.record(
            run,
            item.path,
            Outcome.SKIPPED,
            Reason.TOO_LARGE,
            detail=f"{item.size} bytes supera el máximo de {MAX_BLOB_BYTES}",
            size=item.size,
        )
        return

    try:
        data = item.read()
    except OSError as exc:
        ledger.record(run, item.path, Outcome.FAILED, Reason.READ_ERROR, detail=str(exc))
        return

    media_type = item.media_type or guess_media_type(item.path, data)
    sha = blobs.put(data)
    store.record_blob(sha, len(data), media_type)

    relevance = relevance_for_path(item.path, len(data))
    previous = store.current_blob_for(source_id, item.path)
    document = store.upsert_document(
        source_id,
        item.path,
        sha,
        media_type,
        len(data),
        int(relevance),
        revision=item.revision or connector.revision,
    )

    common = {
        "document_id": document,
        "blob_sha": sha,
        "media_type": media_type,
        "size": len(data),
    }

    if previous == sha and store.count_units(document):
        ledger.record(run, item.path, Outcome.UNCHANGED, Reason.UNCHANGED_BLOB, **common)
        return

    if relevance is Relevance.IGNORABLE:
        ledger.record(
            run,
            item.path,
            Outcome.SKIPPED,
            Reason.LOW_RELEVANCE,
            detail="guardado y direccionable, sin extraer",
            **common,
        )
        return

    extractor = select_extractor(media_type, item.path)
    if extractor is None:
        reason = Reason.BINARY if looks_binary(data) and not is_textual(media_type) else Reason.NO_EXTRACTOR
        ledger.record(
            run,
            item.path,
            Outcome.SKIPPED,
            reason,
            detail=f"sin extractor para {media_type}",
            **common,
        )
        return

    try:
        extracted = extractor.extract(data, item.path)
    except ExtractionUnavailable as exc:
        ledger.record(
            run,
            item.path,
            Outcome.SKIPPED,
            Reason.MISSING_DEPENDENCY,
            detail=f"{exc.requirement} no está instalado. {exc.hint}",
            extractor=extractor.name,
            **common,
        )
        return
    except EmptyExtraction as exc:
        ledger.record(
            run,
            item.path,
            Outcome.SKIPPED,
            exc.reason,
            detail=exc.detail,
            extractor=extractor.name,
            **common,
        )
        return
    except Exception as exc:  # an extractor bug must not abort the whole run
        ledger.record(
            run,
            item.path,
            Outcome.FAILED,
            Reason.EXTRACTOR_ERROR,
            detail=f"{type(exc).__name__}: {exc}",
            extractor=extractor.name,
            **common,
        )
        return

    pending, degraded = _to_pending(extracted, sha, item.path, int(relevance), extractor.name)
    if not pending:
        ledger.record(
            run,
            item.path,
            Outcome.SKIPPED,
            Reason.NOTHING_TO_EXTRACT,
            detail=f"{extractor.name} no devolvió texto",
            extractor=extractor.name,
            **common,
        )
        return

    store.replace_text_units(document, pending)
    ledger.record(
        run,
        item.path,
        Outcome.EXTRACTED,
        reason=Reason.DEGRADED_PARSE if degraded else "",
        detail=degraded,
        extractor=extractor.name,
        units=len(pending),
        **common,
    )


def _to_pending(
    extracted, sha: str, path: str, relevance: int, extractor: str
) -> tuple[list[PendingUnit], str]:
    pending: list[PendingUnit] = []
    degraded = ""
    for unit in extracted:
        if not unit.text.strip():
            continue
        if not degraded:
            degraded = str(unit.meta.get("parse_error", ""))
        try:
            evidence = Evidence(
                span=Span(blob_sha=sha, locator=unit.locator),
                text=unit.text,
                origin=path,
                meta=unit.meta,
            )
        except ProvenanceError:
            # A unit that cannot be pointed at has no business being stored.
            continue
        finding = scan_sensitivity(unit.text)
        pending.append(
            PendingUnit(
                evidence=evidence,
                extractor=extractor,
                relevance=relevance,
                sensitivity=str(finding.level),
                sensitivity_labels=finding.labels,
                verbatim=unit.verbatim,
            )
        )
    return pending, degraded
