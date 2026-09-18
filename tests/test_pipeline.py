import pytest

from core.workspace import Workspace
from ingest.ledger import Ledger, Outcome, Reason
from ingest.pipeline import ingest


@pytest.fixture()
def workspace(tmp_path):
    return Workspace(tmp_path / "brain")


@pytest.fixture()
def source(tmp_path):
    root = tmp_path / "wallet"
    (root / "services").mkdir(parents=True)
    (root / "README.md").write_bytes(b"# Wallet\n\nBilletera de la fintech.\n")
    (root / "services" / "app.py").write_bytes(b"def cobrar():\n    return 1\n")
    (root / "docker-compose.yml").write_bytes(
        b"services:\n  wallet:\n    image: wallet:1.2\n"
    )
    (root / "node_modules").mkdir()
    (root / "node_modules" / "left-pad.js").write_bytes(b"module.exports = 1\n")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00fake")
    (root / "vacio.txt").write_bytes(b"")
    return root


def events_by_path(workspace, run_id=None):
    store = workspace.store()
    try:
        ledger = Ledger(store)
        run_id = run_id or ledger.last_run().run_id
        return {event["path"]: event for event in ledger.events(run_id=run_id, limit=500)}
    finally:
        store.close()


def test_every_document_leaves_exactly_one_event(workspace, source):
    summary = ingest(str(source), workspace)
    events = events_by_path(workspace)
    # Four files walked, one pruned directory, and nothing else.
    assert set(events) == {
        "README.md",
        "services/app.py",
        "docker-compose.yml",
        "logo.png",
        "vacio.txt",
        "node_modules",
    }
    assert summary.documents == len(events)


def test_a_pruned_directory_is_recorded_rather_than_vanishing(workspace, source):
    ingest(str(source), workspace)
    event = events_by_path(workspace)["node_modules"]
    assert event["outcome"] == Outcome.SKIPPED
    assert event["reason"] == Reason.PRUNED_DIRECTORY


def test_an_empty_file_is_recorded(workspace, source):
    ingest(str(source), workspace)
    assert events_by_path(workspace)["vacio.txt"]["reason"] == Reason.EMPTY_FILE


def test_an_unreadable_binary_is_stored_and_reported(workspace, source):
    ingest(str(source), workspace)
    event = events_by_path(workspace)["logo.png"]
    assert event["outcome"] == Outcome.SKIPPED
    assert event["reason"] == Reason.MISSING_DEPENDENCY
    assert "pytesseract" in event["detail"]
    # The bytes are kept even though nothing could be read out of them.
    assert workspace.blobs().has(event["blob_sha"])


def test_text_documents_become_units_with_spans(workspace, source):
    ingest(str(source), workspace)
    store = workspace.store()
    try:
        document = next(
            row for row in store.list_documents() if row["path"] == "services/app.py"
        )
        units = store.units_for_document(document["id"])
        assert units
        for unit in units:
            resolution = workspace.blobs().resolve(unit.span)
            assert resolution.exact
            assert resolution.text == unit.text
    finally:
        store.close()


def test_relevance_reaches_the_stored_unit(workspace, source):
    ingest(str(source), workspace)
    store = workspace.store()
    try:
        readme = next(row for row in store.list_documents() if row["path"] == "README.md")
        code = next(row for row in store.list_documents() if row["path"] == "services/app.py")
        assert readme["relevance"] > code["relevance"]
    finally:
        store.close()


def test_running_twice_changes_nothing(workspace, source):
    ingest(str(source), workspace)
    store = workspace.store()
    try:
        first = store.counts()
    finally:
        store.close()

    ingest(str(source), workspace)
    store = workspace.store()
    try:
        assert store.counts() == first
    finally:
        store.close()

    events = events_by_path(workspace)
    assert events["README.md"]["outcome"] == Outcome.UNCHANGED
    assert events["README.md"]["reason"] == Reason.UNCHANGED_BLOB


def test_editing_a_file_creates_a_new_blob_and_keeps_the_old_one(workspace, source):
    ingest(str(source), workspace)
    store = workspace.store()
    try:
        document = next(row for row in store.list_documents() if row["path"] == "README.md")
        first_sha = document["blob_sha"]
    finally:
        store.close()

    (source / "README.md").write_bytes(b"# Wallet\n\nBilletera, ahora en 5 paises.\n")
    ingest(str(source), workspace)

    store = workspace.store()
    try:
        document = next(row for row in store.list_documents() if row["path"] == "README.md")
        second_sha = document["blob_sha"]
        versions = [row["blob_sha"] for row in store.document_versions(document["id"])]
    finally:
        store.close()

    assert second_sha != first_sha
    assert versions == [first_sha, second_sha]
    assert workspace.blobs().read(first_sha) == b"# Wallet\n\nBilletera de la fintech.\n"
    assert workspace.blobs().verify(first_sha)


def test_re_extraction_leaves_no_units_pointing_at_the_old_blob(workspace, source):
    ingest(str(source), workspace)
    (source / "README.md").write_bytes(b"# Wallet\n\nTexto nuevo.\n")
    ingest(str(source), workspace)

    store = workspace.store()
    try:
        document = next(row for row in store.list_documents() if row["path"] == "README.md")
        units = store.units_for_document(document["id"])
        assert {unit.span.blob_sha for unit in units} == {document["blob_sha"]}
    finally:
        store.close()


def test_a_vendored_file_is_addressable_but_not_extracted(workspace, tmp_path):
    root = tmp_path / "web"
    (root / "static" / "vendor").mkdir(parents=True)
    (root / "static" / "vendor" / "jquery.js").write_bytes(b"var jQuery = 1;\n")
    ingest(str(root), workspace)

    event = events_by_path(workspace)["static/vendor/jquery.js"]
    assert event["outcome"] == Outcome.SKIPPED
    assert event["reason"] == Reason.LOW_RELEVANCE
    assert workspace.blobs().has(event["blob_sha"])
    store = workspace.store()
    try:
        assert store.get_document(event["document_id"]) is not None
    finally:
        store.close()


def test_secrets_found_during_ingestion_are_flagged(workspace, tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    (root / "settings.py").write_bytes(b'DB = "postgres://wallet:s3cretpass@db:5432/w"\n')
    ingest(str(root), workspace)

    store = workspace.store()
    try:
        flagged = store.sensitive_units()
        assert [unit.sensitivity for unit in flagged] == ["secret"]
        assert "connection_string" in flagged[0].sensitivity_labels
    finally:
        store.close()


def test_a_broken_manifest_is_extracted_but_marked_degraded(workspace, tmp_path):
    root = tmp_path / "svc"
    root.mkdir()
    (root / "package.json").write_bytes(b'{"name": "wallet",')
    ingest(str(root), workspace)

    event = events_by_path(workspace)["package.json"]
    assert event["outcome"] == Outcome.EXTRACTED
    assert event["reason"] == Reason.DEGRADED_PARSE
    assert "JSONDecodeError" in event["detail"]


def test_a_failing_extractor_does_not_abort_the_run(workspace, tmp_path, monkeypatch):
    root = tmp_path / "svc"
    root.mkdir()
    (root / "roto.md").write_bytes(b"# hola\n")
    (root / "sano.py").write_bytes(b"x = 1\n")

    from ingest.extractors.markup import MarkdownExtractor

    def explode(self, data, path):
        raise RuntimeError("boom")

    monkeypatch.setattr(MarkdownExtractor, "extract", explode)
    ingest(str(root), workspace)

    events = events_by_path(workspace)
    assert events["roto.md"]["outcome"] == Outcome.FAILED
    assert events["roto.md"]["reason"] == Reason.EXTRACTOR_ERROR
    assert events["sano.py"]["outcome"] == Outcome.EXTRACTED


def test_the_gap_report_groups_by_reason(workspace, source):
    ingest(str(source), workspace)
    store = workspace.store()
    try:
        gaps = {gap.reason: gap.documents for gap in Ledger(store).gaps()}
    finally:
        store.close()
    assert gaps[Reason.PRUNED_DIRECTORY] == 1
    assert gaps[Reason.EMPTY_FILE] == 1


def test_pasted_text_is_ingested_as_its_own_source(workspace):
    note = "Incidente 2026-09-18\n" + "El servicio wallet devolvio 500 en AR.\n" * 20
    ingest(note, workspace)
    store = workspace.store()
    try:
        sources = store.list_sources()
        assert sources[0]["connector"] == "text"
        assert store.counts()["text_units"] >= 1
    finally:
        store.close()
