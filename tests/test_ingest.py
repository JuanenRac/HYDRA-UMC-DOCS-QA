# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_ingest.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

from pathlib import Path

from hydra_umc_docs_qa.ingest import ingest_markdown_file, ingest_markdown_files, ingest_markdown_text


def test_splits_on_headings() -> None:
    text = (
        "# Title\n"
        "Intro paragraph.\n"
        "## CAN Bus Wiring\n"
        "Twisted pair, 120 ohm termination at both ends.\n"
        "## Firmware Flashing\n"
        "Use URTC-FLASHER over SWD/JTAG.\n"
    )
    chunks = ingest_markdown_text(text, source="manual.md")

    assert [chunk.heading for chunk in chunks] == ["Title", "CAN Bus Wiring", "Firmware Flashing"]
    assert "Twisted pair" in chunks[1].text
    assert all(chunk.source == "manual.md" for chunk in chunks)


def test_keeps_text_before_first_heading_under_empty_heading() -> None:
    text = "Front matter line.\n# Real Heading\nBody.\n"
    chunks = ingest_markdown_text(text, source="doc.md")

    assert chunks[0].heading == ""
    assert chunks[0].text == "Front matter line."
    assert chunks[1].heading == "Real Heading"


def test_empty_sections_are_dropped() -> None:
    text = "# Empty\n## Also Empty\n## Has Content\nSomething real.\n"
    chunks = ingest_markdown_text(text, source="doc.md")

    assert [chunk.heading for chunk in chunks] == ["Has Content"]


def test_ingest_markdown_file_reads_real_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("# Notes\nReal content on disk.\n", encoding="utf-8")

    chunks = ingest_markdown_file(path)

    assert len(chunks) == 1
    assert chunks[0].source == "notes.md"
    assert chunks[0].text == "Real content on disk."


def test_ingest_markdown_files_skips_missing_paths(tmp_path: Path) -> None:
    existing = tmp_path / "a.md"
    existing.write_text("# A\nContent A.\n", encoding="utf-8")
    missing = tmp_path / "does_not_exist.md"

    chunks = ingest_markdown_files([existing, missing])

    assert len(chunks) == 1
    assert chunks[0].source == "a.md"
