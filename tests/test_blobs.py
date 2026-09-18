import pytest

from core.blobs import BlobNotFound, BlobStore
from core.ids import sha256_hex
from core.provenance import ByteRange, Cell, LineRange, Page, ProvenanceError, Span

SOURCE = b"linea uno\nlinea dos\nlinea tres\n"


@pytest.fixture()
def blobs(tmp_path):
    return BlobStore(tmp_path / "blobs")


def test_putting_the_same_bytes_twice_is_idempotent(blobs):
    first = blobs.put(SOURCE)
    second = blobs.put(SOURCE)
    assert first == second == sha256_hex(SOURCE)
    assert len(list(blobs.root.rglob("*"))) == 2  # one shard dir, one file


def test_a_changed_file_leaves_the_previous_blob_intact(blobs):
    original = blobs.put(SOURCE)
    edited = blobs.put(SOURCE.replace(b"dos", b"DOS"))
    assert original != edited
    assert blobs.read(original) == SOURCE
    assert blobs.verify(original)
    assert blobs.verify(edited)


def test_blobs_are_sharded_by_prefix(blobs):
    sha = blobs.put(SOURCE)
    assert blobs.path_for(sha).parent.name == sha[:2]
    assert blobs.path_for(sha).name == sha[2:]


def test_reading_an_unknown_blob_says_so(blobs):
    with pytest.raises(BlobNotFound):
        blobs.read("f" * 64)


def test_no_partial_files_are_left_behind(blobs):
    blobs.put(SOURCE)
    assert not list(blobs.root.rglob("*.partial"))


def test_a_line_span_is_sliced_from_the_original_bytes(blobs):
    sha = blobs.put(SOURCE)
    resolution = blobs.resolve(Span(sha, LineRange(2, 3)))
    assert resolution.exact
    assert resolution.text == "linea dos\nlinea tres"
    assert resolution.label == "líneas 2-3"


def test_a_byte_span_is_sliced_from_the_original_bytes(blobs):
    sha = blobs.put(SOURCE)
    resolution = blobs.resolve(Span(sha, ByteRange(0, 9)))
    assert resolution.exact
    assert resolution.text == "linea uno"


def test_a_span_past_the_end_of_the_blob_is_an_error(blobs):
    sha = blobs.put(SOURCE)
    with pytest.raises(ProvenanceError):
        blobs.resolve(Span(sha, LineRange(99, 100)))


def test_a_page_span_returns_the_captured_text_and_admits_it_is_not_exact(blobs):
    sha = blobs.put(b"%PDF-1.4 fake")
    resolution = blobs.resolve(Span(sha, Page(3)), stored_text="texto de la pagina")
    assert not resolution.exact
    assert resolution.text == "texto de la pagina"
    assert "página 3" in resolution.note


def test_a_cell_span_for_a_missing_blob_still_fails_loudly(blobs):
    with pytest.raises(BlobNotFound):
        blobs.resolve(Span("c" * 64, Cell("Hoja1", "B2")), stored_text="x")
