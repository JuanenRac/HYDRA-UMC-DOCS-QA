# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_ingest.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

from pathlib import Path

from hydra_umc_docs_qa.ingest import (
    MAX_DOCUMENT_BYTES,
    RejectionReason,
    canonical_source,
    expand_doc_paths,
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


def test_heading_like_lines_inside_a_fenced_code_block_do_not_split_the_section() -> None:
    # QA-01 (ecosystem-wide software-improvements audit, P2): a real
    # "# Shell comment" line inside a ```sh fence used to be read as a
    # genuine heading, wrongly splitting the Install section around it.
    text = (
        "## Install\n"
        "Run this:\n"
        "```sh\n"
        "# Shell comment, not a heading\n"
        "pip install .\n"
        "```\n"
        "Done.\n"
        "## Usage\n"
        "Real usage text.\n"
    )
    chunks = ingest_markdown_text(text, source="manual.md")

    assert [chunk.heading for chunk in chunks] == ["Install", "Usage"]
    assert "# Shell comment, not a heading" in chunks[0].text
    assert "pip install ." in chunks[0].text
    assert "Done." in chunks[0].text


def test_a_real_heading_right_after_a_fence_closes_still_splits() -> None:
    text = "## Install\n```sh\necho hi\n```\n## Usage\nReal usage text.\n"
    chunks = ingest_markdown_text(text, source="manual.md")

    assert [chunk.heading for chunk in chunks] == ["Install", "Usage"]


def test_a_shorter_fence_of_the_other_character_does_not_close_the_block() -> None:
    # Per CommonMark, only the SAME fence character, repeated at least as
    # many times as the opener, actually closes a fenced block - a ```
    # inside a ~~~~ block (or a shorter run of the same character) must
    # not end it early.
    text = "## Notes\n~~~~\n# still code\n```\n# still code too\n~~~~\n## Next\nReal text.\n"
    chunks = ingest_markdown_text(text, source="manual.md")

    assert [chunk.heading for chunk in chunks] == ["Notes", "Next"]
    assert "# still code" in chunks[0].text
    assert "# still code too" in chunks[0].text


def test_canonical_source_qualifies_with_the_owning_project_directory(tmp_path: Path) -> None:
    # QA-03 (ecosystem-wide software-improvements audit, P2): path.name
    # alone makes README.md from two different repositories indistinguishable.
    project = tmp_path / "HYDRA-UMC-FAKE-PROJECT"
    (project / "docs").mkdir(parents=True)
    (project / "hydra-umc.project.json").write_text("{}", encoding="utf-8")
    doc = project / "docs" / "GUIDE.md"
    doc.write_text("# Guide\nContent.\n", encoding="utf-8")

    assert canonical_source(doc) == "HYDRA-UMC-FAKE-PROJECT/docs/GUIDE.md"


def test_canonical_source_falls_back_to_the_bare_filename_outside_any_project(tmp_path: Path) -> None:
    # No hydra-umc.project.json anywhere above this file - same, unchanged
    # behavior as before this fix for a document outside any real project.
    doc = tmp_path / "standalone.md"
    doc.write_text("# Notes\nContent.\n", encoding="utf-8")

    assert canonical_source(doc) == "standalone.md"


def test_two_readmes_with_identical_headings_get_distinguishable_project_qualified_sources(
    tmp_path: Path,
) -> None:
    # The real closure criterion: two README.md files sharing the exact
    # same heading/text must still resolve to distinguishable citations
    # that each point at the correct project.
    for name in ("HYDRA-UMC-ALPHA", "HYDRA-UMC-BETA"):
        project = tmp_path / name
        project.mkdir()
        (project / "hydra-umc.project.json").write_text("{}", encoding="utf-8")
        (project / "README.md").write_text("# Overview\nSame heading, same text.\n", encoding="utf-8")

    alpha_chunks = ingest_markdown_file(tmp_path / "HYDRA-UMC-ALPHA" / "README.md")
    beta_chunks = ingest_markdown_file(tmp_path / "HYDRA-UMC-BETA" / "README.md")

    assert alpha_chunks[0].source == "HYDRA-UMC-ALPHA/README.md"
    assert beta_chunks[0].source == "HYDRA-UMC-BETA/README.md"
    assert alpha_chunks[0].source != beta_chunks[0].source


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


def test_validate_doc_path_rejects_oversized_markdown_before_reading(tmp_path: Path) -> None:
    oversized = tmp_path / "large.md"
    oversized.write_bytes(b"# " + b"x" * MAX_DOCUMENT_BYTES)

    issue = validate_doc_path(oversized)

    assert issue is not None
    assert issue.reason is RejectionReason.TOO_LARGE
    assert str(MAX_DOCUMENT_BYTES) in issue.describe()


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


def test_expand_doc_paths_finds_markdown_recursively(tmp_path: Path) -> None:
    # Found in an ecosystem-wide software-improvements audit: --docs used
    # to require explicit file paths with no directory recursion.
    (tmp_path / "top.md").write_text("# Top\nContent.\n", encoding="utf-8")
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)
    (nested / "nested.md").write_text("# Nested\nContent.\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("not markdown", encoding="utf-8")

    expanded = expand_doc_paths([tmp_path])

    assert expanded == sorted(expanded)  # deterministic order
    assert set(expanded) == {tmp_path / "top.md", nested / "nested.md"}


def test_expand_doc_paths_passes_non_directories_through_unchanged(tmp_path: Path) -> None:
    real_file = tmp_path / "a.md"
    real_file.write_text("# A\nContent.\n", encoding="utf-8")
    missing = tmp_path / "ghost.md"  # never created

    assert expand_doc_paths([real_file, missing]) == [real_file, missing]


def test_ingest_allowed_markdown_files_accepts_a_directory(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "one.md").write_text("# One\nContent one.\n", encoding="utf-8")
    (docs_dir / "two.md").write_text("# Two\nContent two.\n", encoding="utf-8")

    chunks, rejected = ingest_allowed_markdown_files([docs_dir])

    assert rejected == []
    assert {chunk.source for chunk in chunks} == {"one.md", "two.md"}
