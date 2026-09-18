# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_cache.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import time
from pathlib import Path

from hydra_umc_docs_qa.cache import compute_cache_key, load_cached_index, save_cached_index
from hydra_umc_docs_qa.index import build_index
from hydra_umc_docs_qa.ingest import ingest_markdown_file


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_round_trips_a_real_index_through_disk(tmp_path: Path) -> None:
    doc = tmp_path / "manual.md"
    _write(doc, "# CAN Bus Wiring\nTwisted pair CAN bus wiring needs 120 ohm termination.\n")

    key = compute_cache_key([doc])
    assert load_cached_index(key) is None  # real miss before anything is saved

    index = build_index(ingest_markdown_file(doc))
    save_cached_index(key, index, [])

    cached = load_cached_index(key)
    assert cached is not None
    loaded_index, undecodable = cached
    assert undecodable == []
    assert loaded_index.chunks == index.chunks
    assert loaded_index.idf == index.idf


def test_cache_key_changes_when_the_real_file_content_changes(tmp_path: Path) -> None:
    doc = tmp_path / "manual.md"
    _write(doc, "# A\nOriginal content.\n")
    key_before = compute_cache_key([doc])

    # mtime_ns resolution can be coarser than a tight test loop on some
    # filesystems - force a real, distinguishable mtime rather than
    # relying on the clock alone.
    time.sleep(0.01)
    _write(doc, "# A\nReal, different content.\n")
    key_after = compute_cache_key([doc])

    assert key_before != key_after


def test_cache_key_accounts_for_a_missing_file(tmp_path: Path) -> None:
    doc = tmp_path / "manual.md"
    _write(doc, "# A\nSome content.\n")
    missing = tmp_path / "ghost.md"

    key_with_present_file = compute_cache_key([doc])
    key_with_missing_file = compute_cache_key([doc, missing])

    assert key_with_present_file != key_with_missing_file


def test_a_corrupted_cache_file_is_treated_as_a_real_miss_not_a_crash(tmp_path: Path) -> None:
    doc = tmp_path / "manual.md"
    _write(doc, "# A\nSome content.\n")
    key = compute_cache_key([doc])

    index = build_index(ingest_markdown_file(doc))
    save_cached_index(key, index, [])

    from hydra_umc_docs_qa.cache import _cache_file

    _cache_file(key).write_bytes(b"not a real pickle payload")

    assert load_cached_index(key) is None
