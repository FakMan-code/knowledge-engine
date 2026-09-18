"""Two judgements made at the door, neither of them destructive.

**Relevance** ranks how likely a document is to answer an operational question.
A minified bundle is still stored and still addressable; it just never gets to
outrank a runbook. This is the one idea worth keeping from the previous engine,
where it was called ``retrieval_rank``.

**Sensitivity** flags text that looks like a credential or personal data. This
is a fintech: the point is that a later layer can refuse to echo a secret into
an answer. It is a guardrail for accidental disclosure inside an authorised
corpus, not a security control, and it does not replace deciding what to ingest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path

LARGE_ASSET_BYTES = 400_000


class Relevance(IntEnum):
    IGNORABLE = 0
    LOW = 1
    NORMAL = 2
    PRIMARY = 3


class Sensitivity(StrEnum):
    NONE = "none"
    PII = "pii"
    SECRET = "secret"


# What a service actually announces about itself: how it is built, deployed and
# operated. These outrank source code because they answer the topology and
# runtime facets directly.
MANIFEST_NAMES = {
    "package.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "pyproject.toml",
    "requirements.txt",
    "pipfile",
    "go.mod",
    "cargo.toml",
    "gemfile",
    "composer.json",
    "dockerfile",
    "containerfile",
    "chart.yaml",
    "values.yaml",
    "serverless.yml",
    "makefile",
    "jenkinsfile",
    "procfile",
}
MANIFEST_PREFIXES = ("docker-compose", "readme", "runbook", "playbook", "changelog")
MANIFEST_SUFFIXES = (".tf", ".tfvars")
DOC_SUFFIXES = (".md", ".markdown", ".rst", ".pdf", ".docx", ".drawio", ".mmd", ".puml")

CODE_SUFFIXES = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".scala", ".cs", ".go",
    ".rb", ".php", ".rs", ".swift", ".c", ".h", ".cpp", ".sql", ".sh", ".ps1",
    ".yml", ".yaml", ".json", ".toml", ".xml", ".ini", ".conf", ".properties",
)

IGNORABLE_DIR_PARTS = {
    "vendor",
    "vendors",
    "third_party",
    "third-party",
    "bower_components",
    "node_modules",
    "ckeditor",
    "tinymce",
    "site-packages",
    "migrations",
}
LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "pnpm-lock.yaml",
    "gemfile.lock",
    "cargo.lock",
    "composer.lock",
    "go.sum",
}


def relevance_for_path(path: str, size: int = 0) -> Relevance:
    posix = (path or "").replace("\\", "/").lower()
    name = Path(posix).name
    suffix = Path(posix).suffix
    parts = set(posix.split("/"))

    if parts & IGNORABLE_DIR_PARTS or name in LOCKFILES:
        return Relevance.IGNORABLE
    if ".min." in name or name.endswith((".min.js", ".min.css", ".map", ".bundle.js")):
        return Relevance.IGNORABLE
    if size >= LARGE_ASSET_BYTES and suffix in {".js", ".css"}:
        return Relevance.IGNORABLE

    if name in MANIFEST_NAMES or name.startswith(MANIFEST_PREFIXES):
        return Relevance.PRIMARY
    if suffix in MANIFEST_SUFFIXES or suffix in DOC_SUFFIXES:
        return Relevance.PRIMARY
    if "/docs/" in f"/{posix}" or posix.startswith("docs/"):
        return Relevance.PRIMARY

    if suffix in CODE_SUFFIXES:
        return Relevance.NORMAL
    return Relevance.LOW


# Values that exist to be replaced. Flagging them trains people to ignore the
# flag, which is worse than not having one.
PLACEHOLDER = re.compile(
    r"(?i)^(changeme|change_me|your[_-]?\w*|<[^>]*>|\$\{[^}]*\}|\{\{[^}]*\}\}|"
    r"x{3,}|\*{3,}|\.{3,}|example|placeholder|dummy|none|null|true|false|test)$"
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")),
    (
        "connection_string",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:([^\s:/@]{4,})@"),
    ),
)

ASSIGNED_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"client[_-]?secret|authorization)\b\s*[:=]\s*[\"']?([^\s\"',;]{6,})"
)

EMAIL = re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


@dataclass(frozen=True)
class SensitivityFinding:
    level: Sensitivity
    labels: tuple[str, ...]

    @property
    def flagged(self) -> bool:
        return self.level is not Sensitivity.NONE


CLEAN = SensitivityFinding(Sensitivity.NONE, ())


def scan_sensitivity(text: str) -> SensitivityFinding:
    labels: list[str] = []

    for label, pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        captured = match.group(1) if match.groups() else ""
        if captured and PLACEHOLDER.match(captured):
            continue
        labels.append(label)

    for match in ASSIGNED_SECRET.finditer(text):
        value = match.group(2)
        if PLACEHOLDER.match(value):
            continue
        labels.append(f"assigned_{match.group(1).lower().replace('-', '_')}")
        break

    if labels:
        return SensitivityFinding(Sensitivity.SECRET, tuple(dict.fromkeys(labels)))

    personal: list[str] = []
    if EMAIL.search(text):
        personal.append("email")
    if any(_luhn(match.group()) for match in CARD_CANDIDATE.finditer(text)):
        personal.append("card_number")

    if personal:
        return SensitivityFinding(Sensitivity.PII, tuple(personal))
    return CLEAN


def _luhn(candidate: str) -> bool:
    """A 16-digit number is only a card number if the checksum says so."""
    digits = [int(char) for char in candidate if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0
