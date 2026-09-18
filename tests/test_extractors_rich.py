import base64
import urllib.parse
import zlib

import pytest

from core.provenance import LineRange, Node
from ingest.extractors import availability_report, missing_dependencies, select_extractor
from ingest.extractors.diagram import (
    DrawioExtractor,
    MermaidExtractor,
    PlantUMLExtractor,
    SVGExtractor,
)
from ingest.extractors.image import ImageOCRExtractor
from ingest.extractors.office import DocxExtractor, PptxExtractor, XlsxExtractor
from ingest.extractors.pdf import PDFExtractor
from ingest.models import ExtractionUnavailable

# -- mermaid and plantuml ------------------------------------------------


def test_mermaid_edges_keep_both_ends():
    source = b"flowchart LR\n  wallet --> ledger\n  wallet -->|cobra| fees\n"
    units = MermaidExtractor().extract(source, "topologia.mmd")
    edges = [u.meta for u in units if "edge_from" in u.meta]
    assert [(e["edge_from"], e["edge_to"]) for e in edges] == [
        ("wallet", "ledger"),
        ("wallet", "fees"),
    ]
    assert edges[1]["edge_label"] == "cobra"


def test_mermaid_nodes_keep_their_label():
    units = MermaidExtractor().extract(b"flowchart LR\n  wallet[Billetera]\n", "t.mmd")
    labels = [u.meta.get("node_label") for u in units if "node_label" in u.meta]
    assert labels == ["Billetera"]


def test_mermaid_comments_are_not_units():
    units = MermaidExtractor().extract(b"%% comentario\nflowchart LR\n", "t.mmd")
    assert [u.text for u in units] == ["flowchart LR"]


def test_mermaid_units_are_line_addressable():
    units = MermaidExtractor().extract(b"flowchart LR\n  a --> b\n", "t.mmd")
    assert isinstance(units[-1].locator, LineRange)
    assert units[-1].locator.start == 2


def test_plantuml_edges_are_captured():
    source = b"@startuml\nwallet --> ledger : debita\n@enduml\n"
    units = PlantUMLExtractor().extract(source, "t.puml")
    edges = [u.meta for u in units if "edge_from" in u.meta]
    assert edges[0]["edge_from"] == "wallet"
    assert edges[0]["edge_to"] == "ledger"
    assert edges[0]["edge_label"] == "debita"


# -- svg -----------------------------------------------------------------


def test_svg_keeps_only_the_readable_parts():
    source = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b"<title>Topologia</title><path d=\"M0 0\"/><text>wallet</text>"
        b"</svg>"
    )
    units = SVGExtractor().extract(source, "mapa.svg")
    assert [u.text for u in units] == ["Topologia", "wallet"]
    assert isinstance(units[0].locator, Node)


# -- drawio --------------------------------------------------------------


PLAIN_DRAWIO = b"""<mxfile>
  <diagram name="Pagina-1">
    <mxGraphModel>
      <root>
        <mxCell id="1" value="wallet"/>
        <mxCell id="2" value="ledger"/>
        <mxCell id="3" value="debita" edge="1" source="1" target="2"/>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""


def test_drawio_resolves_edge_ends_to_their_labels():
    units = DrawioExtractor().extract(PLAIN_DRAWIO, "topologia.drawio")
    edge = [u for u in units if "edge_from" in u.meta][0]
    assert edge.meta["edge_from"] == "wallet"
    assert edge.meta["edge_to"] == "ledger"
    assert edge.text == "debita: wallet -> ledger"


def test_drawio_nodes_become_units():
    units = DrawioExtractor().extract(PLAIN_DRAWIO, "topologia.drawio")
    assert [u.text for u in units if "node_label" in u.meta] == ["wallet", "ledger"]


def test_drawio_labels_lose_their_inline_html():
    source = b'<mxGraphModel><root><mxCell id="1" value="wallet&lt;br&gt;core"/></root></mxGraphModel>'
    units = DrawioExtractor().extract(source, "t.drawio")
    assert units[0].text == "wallet core"


def test_drawio_reads_the_compressed_page_format():
    inner = (
        '<mxGraphModel><root><mxCell id="1" value="wallet"/>'
        '<mxCell id="2" value="ledger"/>'
        '<mxCell id="3" value="" edge="1" source="1" target="2"/></root></mxGraphModel>'
    )
    deflated = zlib.compress(urllib.parse.quote(inner).encode("utf-8"), 9)[2:-4]
    payload = base64.b64encode(deflated).decode("ascii")
    source = f"<mxfile><diagram>{payload}</diagram></mxfile>".encode("utf-8")

    units = DrawioExtractor().extract(source, "comprimido.drawio")
    assert [u.text for u in units if "node_label" in u.meta] == ["wallet", "ledger"]
    assert [u.text for u in units if "edge_from" in u.meta] == ["wallet -> ledger"]


def test_an_unreadable_drawio_falls_back_to_lines():
    units = DrawioExtractor().extract(b"<mxfile><diagram>%%%</diagram>", "t.drawio")
    assert isinstance(units[0].locator, LineRange)


# -- routing -------------------------------------------------------------


@pytest.mark.parametrize(
    "media_type,path,expected",
    [
        ("application/x-drawio", "t.drawio", "drawio"),
        ("image/svg+xml", "t.svg", "svg"),
        ("text/x-mermaid", "t.mmd", "mermaid"),
        ("text/x-plantuml", "t.puml", "plantuml"),
        ("application/pdf", "runbook.pdf", "pdf"),
        ("image/png", "captura.png", "image-ocr"),
    ],
)
def test_rich_formats_route_to_their_extractor(media_type, path, expected):
    assert select_extractor(media_type, path).name == expected


# -- optional dependencies ----------------------------------------------


@pytest.mark.parametrize(
    "extractor",
    [PDFExtractor(), DocxExtractor(), XlsxExtractor(), PptxExtractor(), ImageOCRExtractor()],
)
def test_a_missing_dependency_is_refused_with_an_install_hint(extractor):
    if extractor.available():
        pytest.skip(f"{extractor.name} tiene su dependencia instalada")
    with pytest.raises(ExtractionUnavailable) as raised:
        extractor.extract(b"cualquier cosa", "archivo")
    assert raised.value.hint


def test_diagram_extractors_never_need_an_install():
    for extractor in (DrawioExtractor(), SVGExtractor(), MermaidExtractor(), PlantUMLExtractor()):
        assert extractor.available()
        assert extractor.requirement == ""


def test_the_availability_report_covers_every_extractor():
    report = availability_report()
    assert {item.name for item in report} >= {
        "pdf",
        "docx",
        "xlsx",
        "pptx",
        "image-ocr",
        "drawio",
        "text",
    }
    for item in missing_dependencies():
        assert item.requirement and item.hint
