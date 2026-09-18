"""Deterministic identifiers.

The same input yields the same id on any machine and any run. Ingesting the
same source twice must not create a second row, so ids come from content and
position rather than from a counter or a clock.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .provenance import Span

ID_LENGTH = 32


def sha256_hex(data: bytes) -> str:
    """The blob id: the full sha256 of the bytes, never truncated."""
    return hashlib.sha256(data).hexdigest()


def _digest(*parts: str) -> str:
    joined = "\u0000".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:ID_LENGTH]


def canonical_json(value: Any) -> str:
    """Stable JSON so the same structure always hashes the same way."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def source_id(connector: str, uri: str) -> str:
    return _digest("source", connector, uri)


def document_id(source: str, path: str) -> str:
    return _digest("document", source, path.replace("\\", "/"))


def text_unit_id(document: str, span: Span, ordinal: int) -> str:
    """Ordinal disambiguates units that legitimately share a locator."""
    return _digest(
        "unit",
        document,
        span.blob_sha,
        canonical_json(span.locator.to_dict()),
        str(ordinal),
    )
