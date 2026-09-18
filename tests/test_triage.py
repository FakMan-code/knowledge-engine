import pytest

from ingest.triage import Relevance, Sensitivity, relevance_for_path, scan_sensitivity


@pytest.mark.parametrize(
    "path,expected",
    [
        ("README.md", Relevance.PRIMARY),
        ("docs/runbook-wallet.md", Relevance.PRIMARY),
        ("docker-compose.prod.yml", Relevance.PRIMARY),
        ("package.json", Relevance.PRIMARY),
        ("infra/main.tf", Relevance.PRIMARY),
        ("Dockerfile", Relevance.PRIMARY),
        ("topologia.drawio", Relevance.PRIMARY),
        ("services/wallet/app.py", Relevance.NORMAL),
        ("config/settings.ini", Relevance.NORMAL),
        ("assets/logo.svgz", Relevance.LOW),
        ("static/vendor/jquery.js", Relevance.IGNORABLE),
        ("node_modules/left-pad/index.js", Relevance.IGNORABLE),
        ("dist/app.min.js", Relevance.IGNORABLE),
        ("app.bundle.js", Relevance.IGNORABLE),
        ("package-lock.json", Relevance.IGNORABLE),
    ],
)
def test_relevance_ranks_operational_value(path, expected):
    assert relevance_for_path(path) is expected


def test_a_huge_stylesheet_is_ignorable_even_with_a_normal_name():
    assert relevance_for_path("web/app.css", size=900_000) is Relevance.IGNORABLE
    assert relevance_for_path("web/app.css", size=1_000) is Relevance.LOW


@pytest.mark.parametrize(
    "text,label",
    [
        ("AWS_KEY = AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private_key"),
        ("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github_token"),
        ("SLACK=xoxb-123456789012-abcdefghij", "slack_token"),
        ("DATABASE_URL=postgres://wallet:s3cretpass@db:5432/wallet", "connection_string"),
    ],
)
def test_credentials_are_flagged_as_secret(text, label):
    finding = scan_sensitivity(text)
    assert finding.level is Sensitivity.SECRET
    assert label in finding.labels


def test_an_assigned_password_is_flagged():
    finding = scan_sensitivity('password = "Tr0ub4dor&3"')
    assert finding.level is Sensitivity.SECRET
    assert finding.labels == ("assigned_password",)


@pytest.mark.parametrize(
    "text",
    [
        "password = changeme",
        "api_key: <your-api-key>",
        "SECRET=${WALLET_SECRET}",
        "token: xxxxxxxx",
        "password = example",
    ],
)
def test_placeholders_are_not_flagged(text):
    assert not scan_sensitivity(text).flagged


def test_an_email_is_personal_data_not_a_secret():
    finding = scan_sensitivity("Escalar a sre-wallet@fintech.com")
    assert finding.level is Sensitivity.PII
    assert finding.labels == ("email",)


def test_a_card_number_needs_to_pass_luhn():
    assert scan_sensitivity("tarjeta 4111 1111 1111 1111").level is Sensitivity.PII
    assert not scan_sensitivity("orden 4111 1111 1111 1112").flagged


def test_an_id_like_number_is_not_mistaken_for_a_card():
    assert not scan_sensitivity("ticket 123456").flagged


def test_a_secret_outranks_personal_data():
    finding = scan_sensitivity("mail: a@b.com\npassword = Tr0ub4dor&3")
    assert finding.level is Sensitivity.SECRET


def test_ordinary_text_is_clean():
    assert not scan_sensitivity("El servicio wallet depende de ledger.").flagged
