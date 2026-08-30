# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_index.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import pytest

from hydra_umc_docs_qa.index import build_index, search, tokenize
from hydra_umc_docs_qa.ingest import DocChunk


def _sample_chunks() -> list[DocChunk]:
    return [
        DocChunk(
            source="wiring.md",
            heading="CAN Bus Wiring",
            text="Twisted pair CAN bus wiring needs 120 ohm termination at both ends.",
        ),
        DocChunk(
            source="firmware.md",
            heading="Firmware Flashing",
            text="Flash URTC firmware over SWD or JTAG using URTC-FLASHER.",
        ),
        DocChunk(
            source="search.md",
            heading="Retrieval",
            text="TF-IDF retrieval ranks documents by term frequency and inverse document frequency.",
        ),
    ]


def test_tokenize_lowercases_and_strips_punctuation() -> None:
    assert tokenize("CAN-Bus Wiring!") == ["can", "bus", "wiring"]


def test_search_ranks_matching_chunk_first() -> None:
    index = build_index(_sample_chunks())

    results = search(index, "CAN bus termination", top_k=3)

    assert results
    assert results[0].chunk.heading == "CAN Bus Wiring"
    assert results[0].score > 0.0


def test_search_returns_empty_for_unrelated_query() -> None:
    index = build_index(_sample_chunks())

    results = search(index, "quantum entanglement lasagna", top_k=3)

    assert results == []


def test_search_respects_top_k() -> None:
    index = build_index(_sample_chunks())

    results = search(index, "firmware CAN retrieval frequency", top_k=1)

    assert len(results) == 1


def test_build_index_on_empty_chunks_does_not_crash() -> None:
    index = build_index([])

    assert search(index, "anything", top_k=5) == []


def test_search_rejects_negative_top_k() -> None:
    # Real, CLI-reachable edge case: results[:top_k] with a negative top_k
    # would otherwise hit Python's negative-slice semantics (top_k=-1
    # silently drops only the lowest-ranked result) instead of erroring.
    index = build_index(_sample_chunks())

    with pytest.raises(ValueError):
        search(index, "CAN bus termination", top_k=-1)
