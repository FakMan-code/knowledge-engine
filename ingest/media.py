"""Deciding what a file is, before deciding what to do with it.

Extension first because it is cheap and usually right, then the standard
library, then a look at the bytes. The point is never to be clever: it is to
hand the extractor registry a stable label.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

# Entered by no walk. Recorded as pruned so the omission is visible, but not
# descended into: a single node_modules can hold more files than the system
# being documented.
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "bower_components",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".terraform",
    ".gradle",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "target",
    "coverage",
    ".next",
    ".nuxt",
}

BY_EXTENSION = {
    # code
    ".py": "text/x-python",
    ".pyi": "text/x-python",
    ".js": "text/javascript",
    ".jsx": "text/javascript",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".ts": "text/x-typescript",
    ".tsx": "text/x-typescript",
    ".java": "text/x-java",
    ".kt": "text/x-kotlin",
    ".scala": "text/x-scala",
    ".cs": "text/x-csharp",
    ".go": "text/x-go",
    ".rb": "text/x-ruby",
    ".php": "text/x-php",
    ".rs": "text/x-rust",
    ".swift": "text/x-swift",
    ".c": "text/x-c",
    ".h": "text/x-c",
    ".cpp": "text/x-c++",
    ".hpp": "text/x-c++",
    ".sql": "text/x-sql",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".ps1": "text/x-powershell",
    ".bat": "text/x-bat",
    ".tf": "text/x-terraform",
    ".tfvars": "text/x-terraform",
    ".gradle": "text/x-gradle",
    # markup and data
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".rst": "text/x-rst",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "text/xml",
    ".xsd": "text/xml",
    ".json": "application/json",
    ".jsonl": "application/json",
    ".yml": "application/x-yaml",
    ".yaml": "application/x-yaml",
    ".toml": "application/toml",
    ".ini": "text/x-ini",
    ".cfg": "text/x-ini",
    ".conf": "text/x-ini",
    ".properties": "text/x-ini",
    ".env": "text/x-ini",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    # notebooks and diagrams
    ".ipynb": "application/x-ipynb+json",
    ".drawio": "application/x-drawio",
    ".dio": "application/x-drawio",
    ".mmd": "text/x-mermaid",
    ".mermaid": "text/x-mermaid",
    ".puml": "text/x-plantuml",
    ".plantuml": "text/x-plantuml",
    ".iuml": "text/x-plantuml",
    ".svg": "image/svg+xml",
    # documents
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    # images
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}

BY_FILENAME = {
    "dockerfile": "text/x-dockerfile",
    "containerfile": "text/x-dockerfile",
    "makefile": "text/x-makefile",
    "jenkinsfile": "text/x-groovy",
    "procfile": "text/x-ini",
    "requirements.txt": "text/x-requirements",
    "pipfile": "application/toml",
    "gemfile": "text/x-ruby",
    ".gitignore": "text/plain",
    ".dockerignore": "text/plain",
    ".env": "text/x-ini",
}

TEXTUAL_PREFIXES = ("text/",)
TEXTUAL_TYPES = {
    "application/json",
    "application/x-yaml",
    "application/toml",
    "application/x-ipynb+json",
    "application/x-drawio",
    "image/svg+xml",
    "application/xml",
}


def guess_media_type(path: str, sniff: bytes = b"") -> str:
    name = Path(path).name.lower()
    if name in BY_FILENAME:
        return BY_FILENAME[name]
    suffix = Path(name).suffix
    if suffix in BY_EXTENSION:
        return BY_EXTENSION[suffix]
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    if sniff and looks_binary(sniff):
        return "application/octet-stream"
    return "text/plain"


def looks_binary(data: bytes) -> bool:
    """A NUL byte in the first block is the cheapest reliable signal."""
    return b"\x00" in data[:8192]


def is_textual(media_type: str) -> bool:
    if media_type in TEXTUAL_TYPES:
        return True
    return any(media_type.startswith(prefix) for prefix in TEXTUAL_PREFIXES)
