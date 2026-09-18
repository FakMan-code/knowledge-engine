"""Provenance primitives.

Principle 2 of the product: nothing is asserted without being openable in the
source. Everything in this module refuses to exist without a blob and a
locator, so an assertion with no evidence fails at construction instead of
becoming a row with empty columns.

A locator says *where inside the blob*. It is typed per medium because a line
range does not describe a PDF page and a page does not describe a spreadsheet
cell. Adding a medium means adding a locator, never widening an existing one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_REGISTRY: dict[str, type["Locator"]] = {}


class ProvenanceError(ValueError):
    """Raised when something would exist without being traceable to a source."""


def _register(cls: type["Locator"]) -> type["Locator"]:
    if not cls.kind:
        raise ProvenanceError(f"{cls.__name__} must declare a kind")
    if cls.kind in _REGISTRY:
        raise ProvenanceError(f"duplicate locator kind: {cls.kind}")
    _REGISTRY[cls.kind] = cls
    return cls


class Locator:
    """Base for every way of pointing inside a blob."""

    kind: ClassVar[str] = ""

    def payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def label(self) -> str:
        """Human phrasing shown to the operator, in Spanish."""
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.payload()}


@_register
@dataclass(frozen=True)
class LineRange(Locator):
    """Lines, 1-indexed and inclusive on both ends."""

    kind: ClassVar[str] = "lines"
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 1:
            raise ProvenanceError(f"line numbers start at 1, got {self.start}")
        if self.end < self.start:
            raise ProvenanceError(f"empty line range: {self.start}-{self.end}")

    def payload(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end}

    def label(self) -> str:
        if self.start == self.end:
            return f"línea {self.start}"
        return f"líneas {self.start}-{self.end}"


@_register
@dataclass(frozen=True)
class ByteRange(Locator):
    """Bytes, 0-indexed, end exclusive. The fallback when nothing else fits."""

    kind: ClassVar[str] = "bytes"
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ProvenanceError(f"byte offsets start at 0, got {self.start}")
        if self.end <= self.start:
            raise ProvenanceError(f"empty byte range: {self.start}-{self.end}")

    def payload(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end}

    def label(self) -> str:
        return f"bytes {self.start}-{self.end}"


@_register
@dataclass(frozen=True)
class Page(Locator):
    """A PDF page, 1-indexed, with an optional bounding box in PDF points."""

    kind: ClassVar[str] = "page"
    number: int
    bbox: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ProvenanceError(f"page numbers start at 1, got {self.number}")
        if self.bbox is not None:
            box = tuple(float(value) for value in self.bbox)
            if len(box) != 4:
                raise ProvenanceError(f"bbox needs 4 values, got {len(box)}")
            object.__setattr__(self, "bbox", box)

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"number": self.number}
        if self.bbox is not None:
            payload["bbox"] = list(self.bbox)
        return payload

    def label(self) -> str:
        return f"página {self.number}"


@_register
@dataclass(frozen=True)
class Cell(Locator):
    """A spreadsheet cell or cell range, as the sheet writes it."""

    kind: ClassVar[str] = "cell"
    sheet: str
    ref: str

    def __post_init__(self) -> None:
        if not self.sheet.strip():
            raise ProvenanceError("cell locator needs a sheet name")
        if not self.ref.strip():
            raise ProvenanceError("cell locator needs a reference")

    def payload(self) -> dict[str, Any]:
        return {"sheet": self.sheet, "ref": self.ref}

    def label(self) -> str:
        return f"{self.sheet}!{self.ref}"


@_register
@dataclass(frozen=True)
class Slide(Locator):
    """A slide, 1-indexed, optionally narrowed to one shape."""

    kind: ClassVar[str] = "slide"
    number: int
    shape: str | None = None

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ProvenanceError(f"slide numbers start at 1, got {self.number}")

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"number": self.number}
        if self.shape:
            payload["shape"] = self.shape
        return payload

    def label(self) -> str:
        if self.shape:
            return f"diapositiva {self.number} ({self.shape})"
        return f"diapositiva {self.number}"


@_register
@dataclass(frozen=True)
class Node(Locator):
    """A node inside a structured document: XPath, JSON pointer, cell index.

    ``line`` is an optional hint so the operator can also open the raw file at
    roughly the right place. It never replaces ``path``.
    """

    kind: ClassVar[str] = "node"
    path: str
    line: int | None = None

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ProvenanceError("node locator needs a path")
        if self.line is not None and self.line < 1:
            raise ProvenanceError(f"line numbers start at 1, got {self.line}")

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": self.path}
        if self.line is not None:
            payload["line"] = self.line
        return payload

    def label(self) -> str:
        if self.line is not None:
            return f"{self.path} (línea {self.line})"
        return self.path


def locator_from_dict(data: dict[str, Any]) -> Locator:
    """Rebuild a locator from its stored form."""
    if not isinstance(data, dict):
        raise ProvenanceError(f"locator must be a mapping, got {type(data).__name__}")
    kind = data.get("kind")
    if kind not in _REGISTRY:
        raise ProvenanceError(f"unknown locator kind: {kind!r}")
    payload = {key: value for key, value in data.items() if key != "kind"}
    if kind == Page.kind and payload.get("bbox") is not None:
        payload["bbox"] = tuple(payload["bbox"])
    try:
        return _REGISTRY[kind](**payload)
    except TypeError as exc:
        raise ProvenanceError(f"bad payload for locator {kind!r}: {exc}") from exc


def known_locator_kinds() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


@dataclass(frozen=True)
class Span:
    """A blob plus the place inside it. The smallest thing that can be cited."""

    blob_sha: str
    locator: Locator

    def __post_init__(self) -> None:
        sha = (self.blob_sha or "").strip().lower()
        if not SHA256_RE.match(sha):
            raise ProvenanceError(f"span needs a sha256 blob id, got {self.blob_sha!r}")
        object.__setattr__(self, "blob_sha", sha)
        if not isinstance(self.locator, Locator):
            raise ProvenanceError("span needs a Locator, not a bare dict")

    @property
    def short_sha(self) -> str:
        return self.blob_sha[:12]

    def to_dict(self) -> dict[str, Any]:
        return {"blob_sha": self.blob_sha, "locator": self.locator.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Span":
        return cls(blob_sha=data["blob_sha"], locator=locator_from_dict(data["locator"]))

    def label(self) -> str:
        return f"{self.short_sha} · {self.locator.label()}"


@dataclass(frozen=True)
class Evidence:
    """Text that can be pointed back at the bytes it came from.

    ``origin`` is a display path such as ``services/wallet/app.py``. It is a
    convenience for showing the operator a readable citation; the span is what
    makes the claim verifiable.
    """

    span: Span
    text: str
    origin: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.span, Span):
            raise ProvenanceError("evidence needs a Span")
        if not self.text or not self.text.strip():
            raise ProvenanceError("evidence needs non-empty text")

    def cite(self) -> str:
        """The citation an operator reads, e.g. ``app.py · líneas 10-42``."""
        where = self.origin or self.span.short_sha
        return f"{where} · {self.span.locator.label()}"
