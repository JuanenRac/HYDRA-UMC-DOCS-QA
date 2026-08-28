# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_ingest.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

from pathlib import Path

from hydra_umc_docs_qa.ingest import (
    RejectionReason,
    ingest_allowed_markdown_files,
    ingest_markdown_file,
    ingest_markdown_files,
    ingest_markdown_text,
    validate_doc_path,
)


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


def test_chunk_index_disambiguates_repeated_headings_in_one_source() -> None:
    text = "# Notes\nFirst section.\n# Notes\nSecond section.\n"
    chunks = ingest_markdown_text(text, source="dup.md")

    assert [chunk.index for chunk in chunks] == [0, 1]
    assert chunks[0].text == "First section."
    assert chunks[1].text == "Second section."


def test_citation_index_recovers_the_exact_cited_passage(tmp_path: Path) -> None:
    # A real traceability round-trip: given a chunk's (source, index)
    # citation key, re-ingesting the same source file from scratch must
    # recover byte-identical text - proving the citation is a real,
    # stable pointer back to the original passage, not just a label.
    doc = tmp_path / "manual.md"
    doc.write_text(
        "# CAN Bus Wiring\nTwisted pair wiring.\n# Firmware Flashing\nFlash over SWD.\n",
        encoding="utf-8",
    )
    first_pass = ingest_markdown_file(doc)
    cited = first_pass[1]

    second_pass = ingest_markdown_file(doc)
    recovered = next(c for c in second_pass if c.source == cited.source and c.index == cited.index)

    assert recovered.text == cited.text
    assert recovered.heading == cited.heading


def test_validate_doc_path_accepts_real_markdown(tmp_path: Path) -> None:
    ok = tmp_path / "notes.md"
    ok.write_text("# Notes\nReal content.\n", encoding="utf-8")

    assert validate_doc_path(ok) is None


def test_validate_doc_path_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "ghost.md"

    issue = validate_doc_path(missing)

    assert issue is not None
    assert issue.reason is RejectionReason.MISSING
    assert "not found" in issue.describe()


def test_validate_doc_path_rejects_disallowed_extension(tmp_path: Path) -> None:
    disallowed = tmp_path / "secrets.env"
    disallowed.write_text("API_KEY=xyz", encoding="utf-8")

    issue = validate_doc_path(disallowed)

    assert issue is not None
    assert issue.reason is RejectionReason.DISALLOWED_EXTENSION
    assert ".env" in issue.describe()


def test_ingest_allowed_markdown_files_separates_valid_from_rejected(tmp_path: Path) -> None:
    good = tmp_path / "a.md"
    good.write_text("# A\nContent A.\n", encoding="utf-8")
    missing = tmp_path / "missing.md"
    disallowed = tmp_path / "notes.txt"
    disallowed.write_text("plain text, not markdown", encoding="utf-8")

    chunks, rejected = ingest_allowed_markdown_files([good, missing, disallowed])

    assert len(chunks) == 1
    assert chunks[0].source == "a.md"
    reasons = {doc.path: doc.reason for doc in rejected}
    assert reasons == {
        missing: RejectionReason.MISSING,
        disallowed: RejectionReason.DISALLOWED_EXTENSION,
    }


def test_ingest_allowed_markdown_files_accepts_uppercase_and_markdown_suffix(tmp_path: Path) -> None:
    upper = tmp_path / "UPPER.MD"
    upper.write_text("# Upper\nContent.\n", encoding="utf-8")
    long_suffix = tmp_path / "long.markdown"
    long_suffix.write_text("# Long\nContent.\n", encoding="utf-8")

    chunks, rejected = ingest_allowed_markdown_files([upper, long_suffix])

    assert len(chunks) == 2
    assert rejected == []
