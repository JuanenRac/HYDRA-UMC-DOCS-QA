# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_byte_order_mark.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A document saved with a byte-order mark."""
from __future__ import annotations

from hydra_umc_docs_qa.ingest import ingest_markdown_file


def test_the_first_heading_of_a_file_with_a_byte_order_mark_is_a_heading(tmp_path):
    path = tmp_path / "guide.md"
    path.write_bytes("\ufeff# Se\u00f1al de paro\n\ntexto\n".encode("utf-8"))
    chunks = ingest_markdown_file(path)
    assert [chunk.heading for chunk in chunks] == ["Se\u00f1al de paro"]
    assert "\ufeff" not in chunks[0].text
