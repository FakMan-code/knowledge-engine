"""Content-addressed storage for everything that was ever ingested.

Bytes go in under their own sha256 and are never modified afterwards. Editing a
file upstream produces a new blob and leaves the old one untouched, which is
what lets a citation written months ago still open the exact text it was based
on.

This is also where a span stops being a promise and becomes a fragment: for
line and byte locators the original bytes are sliced live, so a citation can be
checked rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ids import sha256_hex
from .provenance import ByteRange, LineRange, ProvenanceError, Span

SHARD_LENGTH = 2


class BlobNotFound(LookupError):
    """Raised when a span points at bytes this workspace does not hold."""


@dataclass(frozen=True)
class Resolution:
    """The answer to "show me what this citation actually says"."""

    span: Span
    text: str
    exact: bool
    note: str = ""

    @property
    def label(self) -> str:
        return self.span.locator.label()


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, sha: str) -> Path:
        sha = sha.strip().lower()
        return self.root / sha[:SHARD_LENGTH] / sha[SHARD_LENGTH:]

    def has(self, sha: str) -> bool:
        return self.path_for(sha).is_file()

    def put(self, data: bytes) -> str:
        """Store bytes and return their sha. Writing the same bytes twice is a no-op."""
        sha = sha256_hex(data)
        target = self.path_for(sha)
        if target.is_file():
            return sha
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and move into place, so a crash mid-write can
        # never leave a truncated blob under a hash that promises full content.
        staging = target.with_suffix(".partial")
        staging.write_bytes(data)
        staging.replace(target)
        return sha

    def read(self, sha: str) -> bytes:
        target = self.path_for(sha)
        if not target.is_file():
            raise BlobNotFound(f"no blob {sha[:12]} in {self.root}")
        return target.read_bytes()

    def read_text(self, sha: str, encoding: str = "utf-8") -> str:
        return self.read(sha).decode(encoding, errors="replace")

    def size(self, sha: str) -> int:
        target = self.path_for(sha)
        if not target.is_file():
            raise BlobNotFound(f"no blob {sha[:12]} in {self.root}")
        return target.stat().st_size

    def verify(self, sha: str) -> bool:
        """Recompute the hash. A false here means the store was tampered with."""
        return sha256_hex(self.read(sha)) == sha.strip().lower()

    def resolve(self, span: Span, stored_text: str = "") -> Resolution:
        """Reopen a span in the original bytes.

        Line and byte locators are sliced straight out of the blob, so what the
        operator reads is the source itself. Page, cell, slide and node
        locators cannot be sliced without re-running the extractor, so the text
        captured at ingestion is returned and flagged as not exact.

        ``exact`` means the returned text came out of the blob just now. Whether
        it also equals what the unit stored depends on the unit being verbatim:
        an HTML paragraph points at real lines but was saved with its tags
        stripped.
        """
        if not isinstance(span, Span):
            raise ProvenanceError("resolve needs a Span")
        locator = span.locator

        if isinstance(locator, LineRange):
            lines = self.read_text(span.blob_sha).splitlines()
            if locator.start > len(lines):
                raise ProvenanceError(
                    f"span points at line {locator.start} but the blob has {len(lines)}"
                )
            fragment = "\n".join(lines[locator.start - 1 : locator.end])
            return Resolution(span=span, text=fragment, exact=True)

        if isinstance(locator, ByteRange):
            raw = self.read(span.blob_sha)
            if locator.start >= len(raw):
                raise ProvenanceError(
                    f"span starts at byte {locator.start} but the blob has {len(raw)}"
                )
            fragment = raw[locator.start : locator.end].decode("utf-8", errors="replace")
            return Resolution(span=span, text=fragment, exact=True)

        if not self.has(span.blob_sha):
            raise BlobNotFound(f"no blob {span.short_sha} in {self.root}")
        return Resolution(
            span=span,
            text=stored_text,
            exact=False,
            note=(
                f"El locator apunta a {locator.label()}; recuperar el fragmento exacto "
                "requiere volver a correr el extractor. El blob original está guardado."
            ),
        )
