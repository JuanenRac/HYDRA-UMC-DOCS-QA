# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_hardening.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

import os
from pathlib import Path

import pytest

from hydra_umc_docs_qa import cache as cache_module
from hydra_umc_docs_qa.cache import compute_cache_key, save_cached_index
from hydra_umc_docs_qa.highlight import highlight_terms
from hydra_umc_docs_qa.index import build_index, search
from hydra_umc_docs_qa.ingest import (
    RejectionReason,
    ingest_allowed_markdown_files,
    ingest_markdown_text,
)


class _Tty:
    def isatty(self) -> bool:
        return True


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links cannot be created on this machine")


def test_a_symlinked_markdown_file_is_not_ingested(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("password=hunter2", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "real.md").write_text("# Real\nplain text", encoding="utf-8")
    _symlink_or_skip(docs / "notes.md", secret)

    chunks, rejected = ingest_allowed_markdown_files([docs])
    assert all("hunter2" not in chunk.text for chunk in chunks)

    chunks, rejected = ingest_allowed_markdown_files([docs / "notes.md"])
    assert chunks == []
    assert [r.reason for r in rejected] == [RejectionReason.SYMLINK]


def test_a_symlinked_directory_is_not_walked(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.md").write_text("# Leak\nprivate", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "real.md").write_text("# Real\nplain text", encoding="utf-8")
    _symlink_or_skip(docs / "linked", outside)

    chunks, _ = ingest_allowed_markdown_files([docs])
    assert {chunk.heading for chunk in chunks} == {"Real"}


def test_control_characters_are_removed_when_a_file_is_ingested(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("# Title\nsafe \x1b]0;pwned\x07 text\n", encoding="utf-8")
    chunks, _ = ingest_allowed_markdown_files([doc])
    assert "\x1b" not in chunks[0].text and "\x07" not in chunks[0].text


def test_only_real_atx_headings_split_sections() -> None:
    text = "# Real\nintro\n#include <stdio.h>\n#hashtag\n## Second\nbody"
    chunks = ingest_markdown_text(text, source="x.md")
    assert [c.heading for c in chunks] == ["Real", "Second"]
    assert "#include <stdio.h>" in chunks[0].text


def test_a_fence_with_an_info_string_does_not_close_a_block() -> None:
    text = "# A\n```sh\n# comment\n```python\n# still code\n```\n## B\nafter"
    chunks = ingest_markdown_text(text, source="x.md")
    assert [c.heading for c in chunks] == ["A", "B"]


def test_unknown_query_words_lower_the_score() -> None:
    chunks = ingest_markdown_text("# Motor\nthe motor needs cooling\n# Other\nunrelated words here", source="x.md")
    index = build_index(chunks)
    covered = search(index, "motor")[0].score
    partly = search(index, "motor zzz yyy xxx qqq")[0].score
    assert partly < covered


def test_highlight_only_marks_whole_words() -> None:
    result = highlight_terms("banana alarm a", "a", stream=_Tty())
    assert "b\x1b" not in result and "n\x1b[1;33ma" not in result
    assert result.count("\x1b[1;33ma\x1b[0m") == 1


def test_cache_key_changes_with_the_package_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("# A\nx", encoding="utf-8")
    before = compute_cache_key([doc])
    monkeypatch.setattr(cache_module, "__version__", "9.9.9")
    assert compute_cache_key([doc]) != before


def test_old_cache_entries_are_pruned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYDRA_UMC_DOCS_QA_CACHE_DIR", str(tmp_path / "cache"))
    index = build_index(ingest_markdown_text("# A\nx", source="x.md"))
    for number in range(cache_module.MAX_CACHE_ENTRIES + 5):
        save_cached_index(f"{number:064x}", index, [])
    assert len(list((tmp_path / "cache").glob("*.json"))) <= cache_module.MAX_CACHE_ENTRIES
    assert not list((tmp_path / "cache").glob("*.tmp"))
