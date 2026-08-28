# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/ingest.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real Markdown ingestion: splits documents into heading-scoped chunks.

PDF ingestion (mentioned in the README's Key Features) is still future
work - this v0 pass only ingests Markdown, the format every real README/
CHANGELOG in this ecosystem is already written in.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

# Only real Markdown is ever ingested and cited. Without this gate, any
# `--docs` path a caller supplies (a stray .env, a firmware .bin, an
# unrelated source file) would be read as plain text and silently quoted
# back as if it were legitimate, sourced documentation - a real
# information-disclosure and citation-integrity risk once this project is
# exposed as an API rather than run by hand.
ALLOWED_SUFFIXES = frozenset({".md", ".markdown"})


@dataclass(frozen=True)
class DocChunk:
    """One heading-scoped section of a real Markdown document.

    `index` is the chunk's 0-based ordinal position within its own source
    document - a stable, traceable citation key alongside (source,
    heading) that disambiguates repeated/duplicate headings within one
    file and lets a caller deterministically recover the exact cited
    passage by re-parsing the same source.
    """

    source: str
    heading: str
    text: str
    index: int = 0


def ingest_markdown_text(text: str, *, source: str) -> list[DocChunk]:
    """Split real Markdown text into chunks, one per heading section.

    Text before the first heading (if any) is kept under an empty heading
    so front-matter (badges, banners) is never silently dropped.
    """
    chunks: list[DocChunk] = []
    heading = ""
    body_lines: list[str] = []

    def flush() -> None:
        body = "\n".join(body_lines).strip()
        if body:
            chunks.append(DocChunk(source=source, heading=heading, text=body, index=len(chunks)))

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            flush()
            heading = stripped.lstrip("#").strip()
            body_lines = []
        else:
            body_lines.append(line)
    flush()
    return chunks


def ingest_markdown_file(path: Path) -> list[DocChunk]:
    """Ingest one real Markdown file from disk."""
    text = path.read_text(encoding="utf-8")
    return ingest_markdown_text(text, source=path.name)


def ingest_markdown_files(paths: Iterable[Path]) -> list[DocChunk]:
    """Ingest several real Markdown files, skipping ones that don't exist.

    Lenient by design - a low-level building block for callers that
    already trust every path they pass in. `ingest_allowed_markdown_files`
    below is the real, honest gate a caller taking paths from outside
    (a CLI flag, an API request) should use instead.
    """
    chunks: list[DocChunk] = []
    for path in paths:
        if path.is_file():
            chunks.extend(ingest_markdown_file(path))
    return chunks


class RejectionReason(str, Enum):
    """Why a requested document was not ingested - a real, distinct reason
    a caller can act on, never a silent drop."""

    MISSING = "missing"
    DISALLOWED_EXTENSION = "disallowed_extension"


@dataclass(frozen=True)
class RejectedDocument:
    path: Path
    reason: RejectionReason

    def describe(self) -> str:
        if self.reason is RejectionReason.MISSING:
            return f"{self.path}: file not found (no source)"
        suffix = self.path.suffix or "(none)"
        return f"{self.path}: disallowed extension {suffix} - only {', '.join(sorted(ALLOWED_SUFFIXES))} are ingested"


def validate_doc_path(path: Path) -> RejectedDocument | None:
    """Real allowlist gate: `None` if `path` is a real, permitted document,
    else the real reason it was rejected. Checked in this order because a
    missing file's extension is meaningless to report."""
    if not path.is_file():
        return RejectedDocument(path=path, reason=RejectionReason.MISSING)
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        return RejectedDocument(path=path, reason=RejectionReason.DISALLOWED_EXTENSION)
    return None


def ingest_allowed_markdown_files(paths: Iterable[Path]) -> tuple[list[DocChunk], list[RejectedDocument]]:
    """Ingest only real, permitted Markdown documents. Every path that
    fails `validate_doc_path` is reported back as a `RejectedDocument`
    instead of being silently skipped or silently ingested as text - the
    real gate `main.py`'s `query` command runs every `--docs` path
    through."""
    chunks: list[DocChunk] = []
    rejected: list[RejectedDocument] = []
    for path in paths:
        issue = validate_doc_path(path)
        if issue is not None:
            rejected.append(issue)
            continue
        chunks.extend(ingest_markdown_file(path))
    return chunks, rejected
