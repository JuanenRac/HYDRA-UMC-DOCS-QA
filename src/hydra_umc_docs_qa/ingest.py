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
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class DocChunk:
    """One heading-scoped section of a real Markdown document."""

    source: str
    heading: str
    text: str


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
            chunks.append(DocChunk(source=source, heading=heading, text=body))

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
    """Ingest several real Markdown files, skipping ones that don't exist."""
    chunks: list[DocChunk] = []
    for path in paths:
        if path.is_file():
            chunks.extend(ingest_markdown_file(path))
    return chunks
