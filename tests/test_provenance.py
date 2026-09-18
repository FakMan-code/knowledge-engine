import pytest

from core.provenance import (
    ByteRange,
    Cell,
    Evidence,
    LineRange,
    Node,
    Page,
    ProvenanceError,
    Slide,
    Span,
    known_locator_kinds,
    locator_from_dict,
)

SHA = "a" * 64


def test_span_requires_a_real_sha256():
    with pytest.raises(ProvenanceError):
        Span(blob_sha="not-a-sha", locator=LineRange(1, 2))


def test_span_rejects_a_bare_dict_locator():
    with pytest.raises(ProvenanceError):
        Span(blob_sha=SHA, locator={"kind": "lines", "start": 1, "end": 2})


def test_span_normalises_case():
    span = Span(blob_sha=SHA.upper(), locator=LineRange(1, 2))
    assert span.blob_sha == SHA
    assert span.short_sha == "a" * 12


@pytest.mark.parametrize(
    "locator",
    [
        LineRange(10, 42),
        ByteRange(0, 128),
        Page(3),
        Page(3, bbox=(1.0, 2.0, 3.0, 4.0)),
        Cell("Hoja1", "B7"),
        Slide(2, shape="Titulo"),
        Node("/mxfile/diagram/mxCell[@id='7']"),
        Node("/cells/3", line=12),
    ],
)
def test_locator_survives_a_roundtrip(locator):
    assert locator_from_dict(locator.to_dict()) == locator


def test_every_registered_kind_is_reachable():
    assert set(known_locator_kinds()) == {
        "lines",
        "bytes",
        "page",
        "cell",
        "slide",
        "node",
    }


def test_unknown_locator_kind_is_rejected():
    with pytest.raises(ProvenanceError):
        locator_from_dict({"kind": "telepathy"})


@pytest.mark.parametrize(
    "factory",
    [
        lambda: LineRange(0, 4),
        lambda: LineRange(9, 8),
        lambda: ByteRange(-1, 4),
        lambda: ByteRange(5, 5),
        lambda: Page(0),
        lambda: Cell("", "B7"),
        lambda: Node("   "),
    ],
)
def test_impossible_locators_fail_at_construction(factory):
    with pytest.raises(ProvenanceError):
        factory()


def test_labels_read_like_a_citation():
    assert LineRange(10, 42).label() == "líneas 10-42"
    assert LineRange(7, 7).label() == "línea 7"
    assert Page(3).label() == "página 3"
    assert Cell("Hoja1", "B7").label() == "Hoja1!B7"


def test_evidence_needs_text():
    span = Span(blob_sha=SHA, locator=LineRange(1, 2))
    with pytest.raises(ProvenanceError):
        Evidence(span=span, text="   ")


def test_evidence_cites_its_origin():
    span = Span(blob_sha=SHA, locator=LineRange(10, 42))
    evidence = Evidence(span=span, text="def main():", origin="services/wallet/app.py")
    assert evidence.cite() == "services/wallet/app.py · líneas 10-42"
