import sqlite3

import pytest

from core.db import PendingUnit, Store, connect
from core.ids import sha256_hex
from core.provenance import Evidence, LineRange, Span


@pytest.fixture()
def store(tmp_path):
    connection = connect(tmp_path / "store.db")
    store = Store(connection)
    yield store
    store.close()


def seed_document(store, text=b"hola\nmundo\n", path="app.py"):
    sha = sha256_hex(text)
    source = store.upsert_source("filesystem", "C:/repo")
    store.record_blob(sha, len(text), "text/x-python")
    document = store.upsert_document(source, path, sha, "text/x-python", len(text), 2)
    return source, document, sha


def test_schema_enforces_foreign_keys(store):
    row = store.connection.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_a_unit_cannot_point_at_a_blob_that_does_not_exist(store):
    _, document, _ = seed_document(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            """
            INSERT INTO text_units
                (id, document_id, blob_sha, locator_kind, locator, ordinal, text,
                 char_count, extractor, relevance, created_utc)
            VALUES ('u1', ?, ?, 'lines', '{}', 0, 'x', 1, 'test', 1, 'now')
            """,
            (document, "b" * 64),
        )


def test_a_unit_cannot_be_inserted_without_provenance(store):
    _, document, sha = seed_document(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            """
            INSERT INTO text_units
                (id, document_id, blob_sha, locator_kind, locator, ordinal, text,
                 char_count, extractor, relevance, created_utc)
            VALUES ('u1', ?, NULL, 'lines', '{}', 0, 'x', 1, 'test', 1, 'now')
            """,
            (document,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            """
            INSERT INTO text_units
                (id, document_id, blob_sha, locator_kind, locator, ordinal, text,
                 char_count, extractor, relevance, created_utc)
            VALUES ('u2', ?, ?, 'lines', NULL, 0, 'x', 1, 'test', 1, 'now')
            """,
            (document, sha),
        )


def test_a_unit_cannot_hold_empty_text(store):
    _, document, sha = seed_document(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            """
            INSERT INTO text_units
                (id, document_id, blob_sha, locator_kind, locator, ordinal, text,
                 char_count, extractor, relevance, created_utc)
            VALUES ('u1', ?, ?, 'lines', '{}', 0, '', 0, 'test', 1, 'now')
            """,
            (document, sha),
        )


def test_an_event_outcome_must_be_one_of_the_four(store):
    store.connection.execute(
        """
        INSERT INTO ingest_runs (id, connector, target, started_utc, status)
        VALUES ('r1', 'filesystem', 'C:/repo', 'now', 'running')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            """
            INSERT INTO ingest_events (run_id, path, outcome, recorded_utc)
            VALUES ('r1', 'app.py', 'maybe', 'now')
            """
        )


def test_upserting_the_same_source_twice_keeps_one_row(store):
    first = store.upsert_source("filesystem", "C:/repo", label="wallet")
    second = store.upsert_source("filesystem", "C:/repo", label="wallet renamed")
    assert first == second
    assert len(store.list_sources()) == 1
    assert store.get_source(first)["label"] == "wallet renamed"


def test_a_new_blob_adds_a_version_and_keeps_the_previous_one(store):
    source, document, first_sha = seed_document(store)
    second = b"hola\nmundo nuevo\n"
    second_sha = sha256_hex(second)
    store.record_blob(second_sha, len(second), "text/x-python")
    store.upsert_document(source, "app.py", second_sha, "text/x-python", len(second), 2)

    versions = [row["blob_sha"] for row in store.document_versions(document)]
    assert versions == [first_sha, second_sha]
    assert store.get_document(document)["blob_sha"] == second_sha
    assert store.has_blob(first_sha)


def test_units_round_trip_with_their_span(store):
    _, document, sha = seed_document(store)
    span = Span(blob_sha=sha, locator=LineRange(1, 2))
    pending = PendingUnit(
        evidence=Evidence(span=span, text="hola\nmundo", origin="app.py"),
        extractor="text",
        relevance=2,
        sensitivity="secret",
        sensitivity_labels=("token",),
    )
    assert store.replace_text_units(document, [pending]) == 1

    stored = store.units_for_document(document)[0]
    assert stored.span == span
    assert stored.text == "hola\nmundo"
    assert stored.sensitivity_labels == ("token",)
    assert stored.evidence.cite() == "app.py · líneas 1-2"
    assert store.get_unit(stored.id).id == stored.id


def test_re_extraction_replaces_units_but_not_blobs(store):
    _, document, sha = seed_document(store)
    span = Span(blob_sha=sha, locator=LineRange(1, 2))
    store.replace_text_units(
        document,
        [PendingUnit(Evidence(span, "primero", "app.py"), "text", 2)],
    )
    store.replace_text_units(
        document,
        [PendingUnit(Evidence(span, "segundo", "app.py"), "text", 2)],
    )
    units = store.units_for_document(document)
    assert [unit.text for unit in units] == ["segundo"]
    assert store.has_blob(sha)


def test_unit_ids_are_deterministic(store):
    _, document, sha = seed_document(store)
    span = Span(blob_sha=sha, locator=LineRange(1, 2))
    pending = [PendingUnit(Evidence(span, "hola", "app.py"), "text", 2)]
    store.replace_text_units(document, pending)
    first = store.units_for_document(document)[0].id
    store.replace_text_units(document, pending)
    assert store.units_for_document(document)[0].id == first
