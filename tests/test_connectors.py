import pytest

from ingest.connectors import (
    ConnectorError,
    FileConnector,
    FilesystemConnector,
    GitConnector,
    TextConnector,
    select_connector,
)
from ingest.connectors.git import clone_slug, looks_like_git_url
from ingest.models import PrunedDirectory, RawItem


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "services" / "wallet").mkdir(parents=True)
    (tmp_path / "services" / "wallet" / "app.py").write_bytes(b"print('hola')\n")
    (tmp_path / "README.md").write_bytes(b"# wallet\n")
    (tmp_path / "node_modules" / "left-pad").mkdir(parents=True)
    (tmp_path / "node_modules" / "left-pad" / "index.js").write_bytes(b"module.exports=1\n")
    return tmp_path


def test_a_directory_is_walked_recursively(tree):
    connector = FilesystemConnector(str(tree))
    connector.prepare(tree / "clones")
    items = [entry for entry in connector.walk() if isinstance(entry, RawItem)]
    assert sorted(item.path for item in items) == [
        "README.md",
        "services/wallet/app.py",
    ]


def test_a_pruned_directory_is_announced_not_hidden(tree):
    connector = FilesystemConnector(str(tree))
    pruned = [entry for entry in connector.walk() if isinstance(entry, PrunedDirectory)]
    assert [entry.path for entry in pruned] == ["node_modules"]
    assert pruned[0].reason == "pruned_directory"


def test_items_defer_reading_until_asked(tree):
    connector = FilesystemConnector(str(tree))
    item = next(
        entry
        for entry in connector.walk()
        if isinstance(entry, RawItem) and entry.path == "README.md"
    )
    assert item.size == len("# wallet\n")
    assert item.read() == b"# wallet\n"


def test_a_missing_directory_fails_on_prepare(tmp_path):
    connector = FilesystemConnector(str(tmp_path / "nope"))
    with pytest.raises(ConnectorError):
        connector.prepare(tmp_path)


def test_a_single_file_becomes_one_document(tmp_path):
    target = tmp_path / "runbook.md"
    target.write_bytes(b"# pasos\n")
    connector = FileConnector(str(target))
    connector.prepare(tmp_path)
    items = list(connector.walk())
    assert [item.path for item in items] == ["runbook.md"]


def test_pasted_text_gets_a_uri_derived_from_its_content():
    first = TextConnector("mismo texto")
    second = TextConnector("mismo texto")
    other = TextConnector("otro texto")
    assert first.uri == second.uri
    assert first.uri != other.uri
    assert first.uri.startswith("text:")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/wallet",
        "git@github.com:acme/wallet.git",
        "https://bitbucket.org/acme/wallet.git",
        "https://internal.example.com/acme/wallet.git",
    ],
)
def test_git_urls_are_recognised(url):
    assert looks_like_git_url(url)
    assert isinstance(select_connector(url), GitConnector)


@pytest.mark.parametrize("value", ["C:/repos/wallet", "./notes.md", ""])
def test_non_git_values_are_not_mistaken_for_repositories(value):
    assert not looks_like_git_url(value)


def test_clone_slug_is_stable_and_distinguishes_same_named_repos():
    first = clone_slug("https://github.com/acme/wallet.git")
    again = clone_slug("https://github.com/acme/wallet.git")
    other_org = clone_slug("https://github.com/other/wallet.git")
    assert first == again
    assert first.startswith("wallet-")
    assert first != other_org


def test_selection_prefers_a_real_path_over_guessing(tree):
    assert isinstance(select_connector(str(tree)), FilesystemConnector)
    assert isinstance(select_connector(str(tree / "README.md")), FileConnector)


def test_an_unsupported_url_says_what_would_work():
    with pytest.raises(ConnectorError, match="Confluence"):
        select_connector("https://wiki.example.com/display/NOC/Runbook")


def test_the_planned_mcp_connector_is_refused_honestly():
    with pytest.raises(ConnectorError, match="todavía no está implementado"):
        select_connector("anything", kind="mcp")


def test_an_unknown_kind_lists_the_available_ones():
    with pytest.raises(ConnectorError, match="Disponibles"):
        select_connector("anything", kind="telepathy")


def test_a_nonexistent_path_is_not_silently_treated_as_text():
    with pytest.raises(ConnectorError, match="no existe la ruta"):
        select_connector("C:/no/such/place.txt")
