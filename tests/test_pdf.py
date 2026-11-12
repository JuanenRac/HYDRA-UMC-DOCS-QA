# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_pdf.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
#
# PDF ingestion: a small PDF writer builds real files (plain and Flate-compressed streams, a page tree, escapes, hex and
# UTF-16 strings), and the reader, the switch, the gates and the CLI are checked against them.
# =============================================================================
from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from hydra_umc_docs_qa.ingest import (
    DocChunk,
    RejectionReason,
    allowed_suffixes,
    ingest_allowed_markdown_files,
    ingest_markdown_file,
    partition_allowed_paths,
    set_pdf_enabled,
    validate_doc_path,
)
from hydra_umc_docs_qa.main import main
from hydra_umc_docs_qa.pdf_text import PdfError, pdf_pages, text_of_content


def make_pdf(pages: list[bytes], *, compress: bool = False, shuffle: bool = False, encrypt: bool = False) -> bytes:
    """A PDF whose page i shows the content stream pages[i]. Object 1 is the catalog, 2 the page tree, then page and content objects."""
    objects: dict[int, bytes] = {}
    page_numbers = [3 + 2 * i for i in range(len(pages))]
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = b"<< /Type /Pages /Count %d /Kids [" % len(pages) + b" ".join(b"%d 0 R" % n for n in page_numbers) + b"] >>"
    for number, content in zip(page_numbers, pages, strict=True):
        objects[number] = b"<< /Type /Page /Parent 2 0 R /Contents %d 0 R >>" % (number + 1)
        if compress:
            body = zlib.compress(content)
            objects[number + 1] = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(body) + body + b"\nendstream"
        else:
            objects[number + 1] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
    order = list(objects)
    if shuffle:
        order.reverse()
    out = bytearray(b"%PDF-1.4\n")
    for number in order:
        out += b"%d 0 obj\n" % number + objects[number] + b"\nendobj\n"
    trailer = b"<< /Root 1 0 R" + (b" /Encrypt 99 0 R" if encrypt else b"") + b" >>"
    out += b"trailer\n" + trailer + b"\n%%EOF\n"
    return bytes(out)


def text_page(*lines: str) -> bytes:
    body = b"BT /F1 12 Tf 72 700 Td 14 TL\n"
    for i, line in enumerate(lines):
        body += (b"(%s) Tj\n" % line.encode("latin-1")) + (b"0 -14 Td\n" if i + 1 < len(lines) else b"")
    return body + b"ET"


# ---------------------------------------------------------------- the content stream reader
def test_tj_and_line_breaks() -> None:
    assert text_of_content(b"BT 72 700 Td (Hello) Tj 0 -14 Td (World) Tj ET").split() == ["Hello", "World"]


def test_tj_array_puts_a_space_where_the_gap_is_wide() -> None:
    text = text_of_content(b"BT [(Wir)-30(ing) -400 (CAN)] TJ ET")
    assert text.strip() == "Wiring CAN"


def test_literal_string_escapes() -> None:
    assert text_of_content(rb"BT (a\(b\)c\\d\101\n) Tj ET").strip() == "a(b)c\\dA"
    assert text_of_content(b"BT (nested (parens) stay) Tj ET").strip() == "nested (parens) stay"
    assert text_of_content(b"BT (join\\\nline) Tj ET").strip() == "joinline"


def test_hex_and_utf16_strings() -> None:
    assert text_of_content(b"BT <48656C6C6F> Tj ET").strip() == "Hello"
    assert text_of_content(b"BT <FEFF00480069> Tj ET").strip() == "Hi"
    assert text_of_content(b"BT <4865 6C6C 6F2> Tj ET").strip().startswith("Hello")  # an odd digit count is padded


def test_latin1_accents_use_winansi() -> None:
    assert text_of_content(b"BT (Se\\361al de se\\361al) Tj ET").strip() == "Señal de señal"


def test_quote_operators_start_a_new_line() -> None:
    assert text_of_content(b"BT (one) Tj (two) ' ET").split() == ["one", "two"]


def test_comments_and_garbage_do_not_break_the_reader() -> None:
    text_of_content(b"% a comment\nBT <<ab>> 3 0 R ] ) (ok) Tj ET")
    assert "ok" in text_of_content(b"BT (ok) Tj ET")


def test_a_stream_that_never_shows_text_does_not_grow_without_limit() -> None:
    assert text_of_content(b"1 " * 50000) == ""


# ---------------------------------------------------------------- whole documents
def test_pages_in_order_from_the_page_tree() -> None:
    data = make_pdf([text_page("Chapter one", "CAN bus"), text_page("Chapter two")], shuffle=True)
    pages = pdf_pages(data)
    assert pages == ["Chapter one\nCAN bus", "Chapter two"]


def test_flate_streams_are_read() -> None:
    assert pdf_pages(make_pdf([text_page("Compressed text")], compress=True)) == ["Compressed text"]


def test_a_page_without_text_is_kept_as_an_empty_page_but_the_document_needs_some() -> None:
    data = make_pdf([b"q 1 0 0 1 0 0 cm Q", text_page("Only the second page")])
    # the first stream has no BT/ET so it is not a text stream: the page is empty, the second one is numbered 2
    assert pdf_pages(data) == ["", "Only the second page"]


def test_a_document_with_no_text_is_refused_with_the_reason() -> None:
    with pytest.raises(PdfError, match="no readable text"):
        pdf_pages(make_pdf([b"q Q"]))


def test_encrypted_and_non_pdf_files_are_refused() -> None:
    with pytest.raises(PdfError, match="encrypted"):
        pdf_pages(make_pdf([text_page("secret")], encrypt=True))
    with pytest.raises(PdfError, match="not a PDF"):
        pdf_pages(b"just some text")


def test_a_decompression_bomb_is_cut_not_expanded() -> None:
    bomb = zlib.compress(b"BT (x) Tj ET " + b" " * (20 * 1024 * 1024))
    data = (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Contents 4 0 R >>\nendobj\n4 0 obj\n<< /Filter /FlateDecode >>\nstream\n" + bomb + b"\nendstream\nendobj\n"
    )
    with pytest.raises(PdfError, match="no readable text"):
        pdf_pages(data)  # the stream is over the limit, so it is skipped and nothing readable is left


def test_without_a_page_tree_every_content_stream_is_a_page() -> None:
    data = b"%PDF-1.5\n7 0 obj\n<< >>\nstream\nBT (Loose page) Tj ET\nendstream\nendobj\n"
    assert pdf_pages(data) == ["Loose page"]


# ---------------------------------------------------------------- ingestion and the switch
def test_pdf_is_not_an_allowed_extension_by_default(tmp_path: Path) -> None:
    path = tmp_path / "manual.pdf"
    path.write_bytes(make_pdf([text_page("hello")]))
    assert ".pdf" not in allowed_suffixes()
    issue = validate_doc_path(path)
    assert issue is not None and issue.reason is RejectionReason.DISALLOWED_EXTENSION
    chunks, rejected = ingest_allowed_markdown_files([path])
    assert chunks == [] and len(rejected) == 1


def test_the_environment_variable_turns_it_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "manual.pdf"
    path.write_bytes(make_pdf([text_page("hello")]))
    monkeypatch.setenv("HYDRA_UMC_DOCS_QA_PDF", "1")
    assert ".pdf" in allowed_suffixes() and validate_doc_path(path) is None
    set_pdf_enabled(False)  # the explicit switch wins over the environment
    assert ".pdf" not in allowed_suffixes()


def test_a_pdf_becomes_one_cited_section_per_page(tmp_path: Path) -> None:
    set_pdf_enabled(True)
    path = tmp_path / "manual.pdf"
    raw = make_pdf([text_page("CAN bus wiring", "needs 120 ohm termination"), text_page("Flashing the bootloader")])
    path.write_bytes(raw)
    chunks, rejected = ingest_allowed_markdown_files([path])
    assert rejected == []
    assert [c.heading for c in chunks] == ["Page 1", "Page 2"]
    assert chunks[0].text == "CAN bus wiring\nneeds 120 ohm termination"
    assert all(isinstance(c, DocChunk) and len(c.sha256) == 64 for c in chunks)
    assert chunks[1].citation.startswith("manual.pdf#1@")
    assert ingest_markdown_file(path) == chunks  # the same file gives the same sections


def test_a_directory_walk_finds_pdfs_only_when_on(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\ntext", encoding="utf-8")
    (tmp_path / "b.pdf").write_bytes(make_pdf([text_page("pdf text")]))
    assert [p.name for p in partition_allowed_paths([tmp_path])[0]] == ["a.md"]
    set_pdf_enabled(True)
    assert [p.name for p in partition_allowed_paths([tmp_path])[0]] == ["a.md", "b.pdf"]


def test_an_unreadable_pdf_is_reported_not_indexed(tmp_path: Path) -> None:
    set_pdf_enabled(True)
    scanned = tmp_path / "scan.pdf"
    scanned.write_bytes(make_pdf([b"q Q"]))
    chunks, rejected = ingest_allowed_markdown_files([scanned])
    assert chunks == []
    assert rejected[0].reason is RejectionReason.UNDECODABLE
    assert "no readable text" in rejected[0].describe()


def test_the_pdf_limit_is_larger_than_the_markdown_one(tmp_path: Path) -> None:
    set_pdf_enabled(True)
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024))
    assert validate_doc_path(big) is None  # 5 MiB is over the Markdown limit, under the PDF one
    md = tmp_path / "big.md"
    md.write_bytes(b"x" * (5 * 1024 * 1024))
    issue = validate_doc_path(md)
    assert issue is not None and issue.reason is RejectionReason.TOO_LARGE


# ---------------------------------------------------------------- the command line
def test_query_finds_a_pdf_page_with_the_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "manual.pdf"
    path.write_bytes(make_pdf([text_page("Flashing the bootloader"), text_page("CAN bus termination needs 120 ohm")]))

    assert main(["query", "CAN bus termination", "--docs", str(path)]) == 1  # off: the file is rejected
    assert "disallowed extension" in capsys.readouterr().out

    assert main(["query", "CAN bus termination", "--docs", str(path), "--pdf"]) == 0
    out = capsys.readouterr().out
    assert "Page 2" in out and "manual.pdf#1@" in out


def test_the_flag_is_per_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "manual.pdf"
    path.write_bytes(make_pdf([text_page("hello world")]))
    assert main(["query", "hello", "--docs", str(path), "--pdf"]) == 0
    capsys.readouterr()
    assert main(["query", "hello", "--docs", str(path)]) == 1  # the next run is off again
