"""The five things layer 1 has to get right, checked on a deliberately ugly folder.

1. Nothing disappears in silence.
2. Ingesting twice duplicates nothing and re-extracts nothing.
3. Changing a file produces a new blob and leaves the old one intact.
4. Every unit resolves back to the original bytes.
5. Without the optional dependencies the run still completes and reports gaps.

The PDF and OCR assertions adapt to what is installed, so the suite is
meaningful on a bare machine and stricter on a fully equipped one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.provenance import LineRange
from core.workspace import Workspace
from ingest.extractors.image import ImageOCRExtractor
from ingest.extractors.pdf import PDFExtractor
from ingest.ledger import Ledger, Outcome, Reason
from ingest.pipeline import ingest
from tests.mixed_corpus import build

PDF_READY = PDFExtractor().available()
OCR_READY = ImageOCRExtractor().available()


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    return build(tmp_path_factory.mktemp("corpus") / "fuentes")


@pytest.fixture(scope="module")
def ingested(tmp_path_factory, corpus):
    workspace = Workspace(tmp_path_factory.mktemp("brain"))
    summary = ingest(str(corpus), workspace)
    return workspace, summary


def events_of(workspace, run_id=None) -> dict[str, dict]:
    store = workspace.store()
    try:
        ledger = Ledger(store)
        run_id = run_id or ledger.last_run().run_id
        return {event["path"]: event for event in ledger.events(run_id=run_id, limit=1000)}
    finally:
        store.close()


def files_on_disk(corpus: Path) -> set[str]:
    return {
        path.relative_to(corpus).as_posix()
        for path in corpus.rglob("*")
        if path.is_file() and "node_modules" not in path.parts
    }


# -- 1. nothing disappears in silence ------------------------------------


def test_every_file_on_disk_has_exactly_one_recorded_outcome(ingested, corpus):
    workspace, _ = ingested
    events = events_of(workspace)
    recorded = {path for path, event in events.items() if event["reason"] != Reason.PRUNED_DIRECTORY}
    assert recorded == files_on_disk(corpus)


def test_the_skipped_directory_is_named_in_the_ledger(ingested):
    workspace, _ = ingested
    events = events_of(workspace)
    assert events["repo-typescript/node_modules"]["reason"] == Reason.PRUNED_DIRECTORY


def test_every_skip_carries_a_reason(ingested):
    workspace, _ = ingested
    for path, event in events_of(workspace).items():
        if event["outcome"] in (Outcome.SKIPPED, Outcome.FAILED):
            assert event["reason"], f"{path} se salteó sin motivo"


def test_nothing_failed_outright(ingested):
    workspace, _ = ingested
    failures = {
        path: event["detail"]
        for path, event in events_of(workspace).items()
        if event["outcome"] == Outcome.FAILED
    }
    assert failures == {}


# -- 2. idempotency ------------------------------------------------------


def test_a_second_run_duplicates_nothing(tmp_path, corpus):
    workspace = Workspace(tmp_path / "brain")
    ingest(str(corpus), workspace)
    store = workspace.store()
    try:
        first = store.counts()
    finally:
        store.close()

    ingest(str(corpus), workspace)
    store = workspace.store()
    try:
        assert store.counts() == first
    finally:
        store.close()


def test_a_second_run_re_extracts_nothing(tmp_path, corpus):
    workspace = Workspace(tmp_path / "brain")
    ingest(str(corpus), workspace)
    ingest(str(corpus), workspace)

    outcomes = {event["outcome"] for event in events_of(workspace).values()}
    assert Outcome.EXTRACTED not in outcomes


# -- 3. the previous blob stays intact -----------------------------------


def test_editing_a_file_leaves_the_previous_blob_readable(tmp_path, corpus):
    workspace = Workspace(tmp_path / "brain")
    ingest(str(corpus), workspace)

    target = corpus / "repo-python" / "README.md"
    original = target.read_bytes()
    store = workspace.store()
    try:
        before = next(
            row for row in store.list_documents() if row["path"] == "repo-python/README.md"
        )["blob_sha"]
    finally:
        store.close()

    try:
        edited = original.replace(b"Billetera digital.", b"Billetera digital unificada.")
        assert edited != original
        target.write_bytes(edited)
        ingest(str(corpus), workspace)
        store = workspace.store()
        try:
            document = next(
                row for row in store.list_documents() if row["path"] == "repo-python/README.md"
            )
            versions = [row["blob_sha"] for row in store.document_versions(document["id"])]
        finally:
            store.close()

        assert document["blob_sha"] != before
        assert versions == [before, document["blob_sha"]]
        assert workspace.blobs().read(before) == original
        assert workspace.blobs().verify(before)
    finally:
        target.write_bytes(original)


# -- 4. every unit reopens in the original bytes -------------------------


def test_every_unit_reopens_in_the_blob_it_points_at(ingested):
    workspace, _ = ingested
    blobs = workspace.blobs()
    store = workspace.store()
    try:
        proven: set[str] = set()
        for document in store.list_documents():
            for unit in store.units_for_document(document["id"]):
                resolution = blobs.resolve(unit.span, stored_text=unit.text)
                if not isinstance(unit.span.locator, LineRange):
                    assert resolution.note
                    continue
                assert resolution.exact
                if unit.verbatim:
                    assert resolution.text == unit.text, unit.evidence.cite()
                    proven.add(unit.path)
                else:
                    # Normalised text still has to come from the lines it claims.
                    assert unit.text.split()[0] in resolution.text, unit.evidence.cite()

        # Every plain-text document in the corpus cites itself byte for byte.
        assert proven == {
            "repo-python/README.md",
            "repo-python/requirements.txt",
            "repo-python/docker-compose.yml",
            "repo-python/services/wallet.py",
            "repo-python/.env",
            "repo-typescript/src/index.ts",
            "docs/incidentes.csv",
            "docs/flujo.mmd",
        }
    finally:
        store.close()


def test_a_normalising_reader_does_not_claim_a_literal_citation(ingested):
    workspace, _ = ingested
    store = workspace.store()
    try:
        html = next(
            row
            for row in store.list_documents()
            if row["path"] == "docs/confluence-export.html"
        )
        code = next(
            row
            for row in store.list_documents()
            if row["path"] == "repo-python/services/wallet.py"
        )
        assert not any(unit.verbatim for unit in store.units_for_document(html["id"]))
        assert all(unit.verbatim for unit in store.units_for_document(code["id"]))
    finally:
        store.close()


def test_a_citation_reads_like_something_an_operator_can_open(ingested):
    workspace, _ = ingested
    store = workspace.store()
    try:
        units = store.find_units("ledger", limit=5)
        assert units
        assert all("·" in unit.evidence.cite() for unit in units)
    finally:
        store.close()


# -- what the ugly folder actually yielded -------------------------------


def test_the_drawio_gave_up_its_arrows(ingested):
    workspace, _ = ingested
    store = workspace.store()
    try:
        document = next(
            row for row in store.list_documents() if row["path"] == "docs/topologia.drawio"
        )
        texts = [unit.text for unit in store.units_for_document(document["id"])]
    finally:
        store.close()
    assert "debita: wallet-core -> ledger" in texts
    assert "notifica: wallet-core -> Proveedor SMS" in texts


def test_the_confluence_export_kept_its_prose_and_dropped_its_scripts(ingested):
    workspace, _ = ingested
    store = workspace.store()
    try:
        document = next(
            row
            for row in store.list_documents()
            if row["path"] == "docs/confluence-export.html"
        )
        texts = [unit.text for unit in store.units_for_document(document["id"])]
    finally:
        store.close()
    assert any("logs en formato JSON" in text for text in texts)
    assert not any("analytics" in text for text in texts)


def test_the_compose_file_is_addressable_line_by_line(ingested):
    workspace, _ = ingested
    store = workspace.store()
    try:
        document = next(
            row
            for row in store.list_documents()
            if row["path"] == "repo-python/docker-compose.yml"
        )
        units = store.units_for_document(document["id"])
    finally:
        store.close()
    assert units
    assert "ledger" in units[0].text


def test_the_credential_in_the_env_file_is_flagged(ingested):
    workspace, _ = ingested
    store = workspace.store()
    try:
        flagged = {unit.path for unit in store.sensitive_units()}
    finally:
        store.close()
    assert "repo-python/.env" in flagged


def test_the_lockfile_and_the_vendored_bundle_stay_addressable_without_units(ingested):
    workspace, _ = ingested
    events = events_of(workspace)
    for path in ("repo-typescript/package-lock.json", "vendor/jquery.min.js"):
        event = events[path]
        assert event["reason"] == Reason.LOW_RELEVANCE
        assert workspace.blobs().has(event["blob_sha"])
        assert event["units"] == 0


# -- 5. degrading cleanly ------------------------------------------------


@pytest.mark.skipif(PDF_READY, reason="pypdf está instalado; se prueba el camino completo")
def test_without_pypdf_the_run_completes_and_names_the_missing_install(ingested):
    workspace, _ = ingested
    event = events_of(workspace)["docs/runbook.pdf"]
    assert event["outcome"] == Outcome.SKIPPED
    assert event["reason"] == Reason.MISSING_DEPENDENCY
    assert "pypdf" in event["detail"]
    assert workspace.blobs().has(event["blob_sha"])


@pytest.mark.skipif(not PDF_READY, reason="pypdf no está instalado")
def test_with_pypdf_the_text_layer_is_read_by_page(ingested):
    workspace, _ = ingested
    store = workspace.store()
    try:
        document = next(
            row for row in store.list_documents() if row["path"] == "docs/runbook.pdf"
        )
        units = store.units_for_document(document["id"])
    finally:
        store.close()
    assert units
    assert units[0].span.locator.kind == "page"
    assert "Runbook wallet" in units[0].text


@pytest.mark.skipif(not PDF_READY, reason="pypdf no está instalado")
def test_a_scanned_pdf_is_reported_as_its_own_gap(ingested):
    workspace, _ = ingested
    event = events_of(workspace)["docs/escaneado.pdf"]
    assert event["outcome"] == Outcome.SKIPPED
    assert event["reason"] == "pdf_sin_capa_de_texto"


@pytest.mark.skipif(OCR_READY, reason="tesseract está disponible")
def test_without_ocr_the_screenshot_is_a_declared_gap(ingested):
    workspace, _ = ingested
    event = events_of(workspace)["docs/captura.png"]
    assert event["outcome"] == Outcome.SKIPPED
    assert event["reason"] == Reason.MISSING_DEPENDENCY
    assert workspace.blobs().has(event["blob_sha"])


def test_the_gap_report_tells_the_operator_what_to_install(ingested):
    workspace, _ = ingested
    store = workspace.store()
    try:
        gaps = Ledger(store).gaps()
    finally:
        store.close()
    assert gaps
    assert all(gap.reason for gap in gaps)
    assert all(gap.documents > 0 for gap in gaps)
