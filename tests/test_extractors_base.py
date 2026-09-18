import pytest

from core.provenance import LineRange, Node
from ingest.extractors import select_extractor
from ingest.extractors.base import line_windows
from ingest.extractors.markup import HTMLExtractor, MarkdownExtractor
from ingest.extractors.notebook import NotebookExtractor
from ingest.extractors.structured import (
    CSVExtractor,
    JSONExtractor,
    TOMLExtractor,
    XMLExtractor,
)
from ingest.extractors.text import TextExtractor


def slice_lines(source: bytes, locator: LineRange) -> str:
    lines = source.decode("utf-8").splitlines()
    return "\n".join(lines[locator.start - 1 : locator.end])


# -- windows -------------------------------------------------------------


def test_a_small_file_is_one_window():
    assert list(line_windows("uno\ndos\ntres")) == [(1, 3, "uno\ndos\ntres")]


def test_windows_are_contiguous_and_cover_every_line():
    text = "\n".join(f"linea {n}" for n in range(1, 301))
    windows = list(line_windows(text, max_lines=100, max_chars=100_000))
    assert [(w[0], w[1]) for w in windows] == [(1, 100), (101, 200), (201, 300)]


def test_blank_only_windows_are_dropped():
    assert list(line_windows("\n\n\n")) == []


# -- text ----------------------------------------------------------------


def test_code_keeps_line_locators_that_slice_back_exactly():
    source = b"def cobrar():\n    return 1\n"
    units = TextExtractor().extract(source, "wallet.py")
    assert len(units) == 1
    assert isinstance(units[0].locator, LineRange)
    assert slice_lines(source, units[0].locator) == units[0].text


def test_yaml_is_read_as_addressable_lines():
    source = b"services:\n  wallet:\n    image: wallet:1.2\n"
    extractor = select_extractor("application/x-yaml", "docker-compose.yml")
    units = extractor.extract(source, "docker-compose.yml")
    assert extractor.name == "text"
    assert units[0].text == source.decode().rstrip("\n")


# -- markdown ------------------------------------------------------------


MARKDOWN = b"""# Wallet

Servicio de billetera.

## Dependencias

Depende de ledger.

## Procedimiento

1. Revisar la cola.
"""


def test_markdown_splits_on_headings():
    units = MarkdownExtractor().extract(MARKDOWN, "README.md")
    headings = [unit.meta.get("heading") for unit in units]
    assert headings == ["Wallet", "Dependencias", "Procedimiento"]


def test_markdown_sections_slice_back_to_the_source():
    units = MarkdownExtractor().extract(MARKDOWN, "README.md")
    for unit in units:
        assert slice_lines(MARKDOWN, unit.locator) == unit.text


def test_markdown_records_the_heading_trail():
    units = MarkdownExtractor().extract(MARKDOWN, "README.md")
    assert units[1].meta["heading_trail"] == "Wallet > Dependencias"


def test_a_heading_inside_a_fenced_block_is_not_a_section():
    source = b"# Real\n\n```\n# no es titulo\n```\n"
    units = MarkdownExtractor().extract(source, "README.md")
    assert [unit.meta.get("heading") for unit in units] == ["Real"]


# -- html ----------------------------------------------------------------


def test_html_keeps_block_text_and_drops_scripts():
    source = b"<html><body><p>Hola NOC</p><script>var x=1;</script></body></html>"
    units = HTMLExtractor().extract(source, "page.html")
    assert [unit.text for unit in units] == ["Hola NOC"]
    assert units[0].meta["tag"] == "p"


def test_html_units_carry_the_line_they_came_from():
    source = b"<html>\n<body>\n<p>uno</p>\n<p>dos</p>\n</body>\n</html>"
    units = HTMLExtractor().extract(source, "page.html")
    assert [unit.locator.start for unit in units] == [3, 4]


# -- json ----------------------------------------------------------------


PACKAGE = b"""{
  "name": "wallet",
  "dependencies": {
    "ledger-client": "^2.0.0"
  }
}
"""


def test_json_units_are_addressed_by_pointer():
    units = JSONExtractor().extract(PACKAGE, "package.json")
    assert isinstance(units[0].locator, Node)
    assert units[0].locator.path == "/"


def test_a_large_json_object_is_split_by_key():
    payload = b'{"a": "%s", "b": "%s"}' % (b"x" * 3000, b"y" * 3000)
    units = JSONExtractor().extract(payload, "big.json")
    assert [unit.locator.path for unit in units] == ["/a", "/b"]


def test_json_nodes_carry_a_line_hint():
    payload = b'{"a": "%s", "b": "%s"}' % (b"x" * 3000, b"y" * 3000)
    units = JSONExtractor().extract(payload, "big.json")
    assert all(unit.locator.line == 1 for unit in units)


def test_broken_json_falls_back_to_lines_and_says_why():
    units = JSONExtractor().extract(b'{"roto": ', "bad.json")
    assert isinstance(units[0].locator, LineRange)
    assert "JSONDecodeError" in units[0].meta["parse_error"]


# -- toml, csv, xml ------------------------------------------------------


def test_toml_is_addressed_by_pointer():
    units = TOMLExtractor().extract(b'[tool]\nname = "wallet"\n', "pyproject.toml")
    assert isinstance(units[0].locator, Node)


def test_csv_windows_quote_the_files_own_lines():
    rows = b"servicio,pais\n" + b"".join(b"wallet,AR\n" for _ in range(200))
    units = CSVExtractor().extract(rows, "servicios.csv")
    assert len(units) == 2
    assert units[0].text.startswith("servicio,pais")
    for unit in units:
        assert slice_lines(rows, unit.locator) == unit.text
        assert unit.verbatim


def test_csv_windows_do_not_overlap():
    rows = b"servicio,pais\n" + b"".join(b"wallet,AR\n" for _ in range(200))
    units = CSVExtractor().extract(rows, "servicios.csv")
    assert units[0].locator.end + 1 == units[1].locator.start


def test_xml_units_are_addressed_by_element_path():
    source = b"<compose>\n  <service>wallet</service>\n  <service>ledger</service>\n</compose>"
    units = XMLExtractor().extract(source, "compose.xml")
    assert [unit.locator.path for unit in units] == [
        "/compose/service[1]",
        "/compose/service[2]",
    ]
    assert [unit.text for unit in units] == ["wallet", "ledger"]


def test_broken_xml_falls_back_to_lines():
    units = XMLExtractor().extract(b"<a><b></a>", "bad.xml")
    assert isinstance(units[0].locator, LineRange)
    assert "ParseError" in units[0].meta["parse_error"]


# -- notebooks -----------------------------------------------------------


def test_a_notebook_yields_one_unit_per_cell():
    source = (
        b'{"cells": ['
        b'{"cell_type": "markdown", "source": ["# titulo"]},'
        b'{"cell_type": "code", "source": ["print(1)"]},'
        b'{"cell_type": "code", "source": []}'
        b"]}"
    )
    units = NotebookExtractor().extract(source, "analisis.ipynb")
    assert [unit.text for unit in units] == ["# titulo", "print(1)"]
    assert units[0].meta["cell_type"] == "markdown"


# -- registry ------------------------------------------------------------


@pytest.mark.parametrize(
    "media_type,path,expected",
    [
        ("text/markdown", "README.md", "markdown"),
        ("application/json", "package.json", "json"),
        ("application/x-ipynb+json", "a.ipynb", "notebook"),
        ("text/html", "a.html", "html"),
        ("text/csv", "a.csv", "csv"),
        ("application/toml", "pyproject.toml", "toml"),
        ("text/xml", "pom.xml", "xml"),
        ("text/x-python", "app.py", "text"),
        ("application/x-yaml", "compose.yml", "text"),
    ],
)
def test_the_registry_routes_each_format(media_type, path, expected):
    assert select_extractor(media_type, path).name == expected


def test_a_binary_format_has_no_base_extractor():
    assert select_extractor("application/octet-stream", "blob.bin") is None
