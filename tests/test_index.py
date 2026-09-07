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


def test_tokenize_keeps_accented_latin_words_whole() -> None:
    # QA-02 (ecosystem-wide software-improvements audit, P1): the old
    # [a-z0-9]+ pattern silently dropped any accented character, so
    # "über" tokenized as just "ber" - a real, if partial, loss for
    # Spanish/French/Italian/German queries against this ecosystem's own
    # translated READMEs.
    assert tokenize("Über Café Robôt niño") == ["über", "café", "robôt", "niño"]


def test_tokenize_splits_cjk_runs_into_overlapping_bigrams() -> None:
    # QA-02: CJK text has no whitespace between words - the old
    # ASCII-only pattern returned [] for it entirely (a real Chinese/
    # Japanese query could never match anything), and a naive \w+-style
    # fix would instead swallow an entire run into one giant token that
    # could only match an identical run. Overlapping bigrams are the
    # real, dictionary-free middle ground.
    assert tokenize("机器人") == ["机器", "器人"]
    assert tokenize("机") == ["机"]


def test_search_finds_the_right_chinese_section_by_real_term_overlap() -> None:
    # Real end-to-end proof, not just "tokenize returns something": a
    # partial Chinese query must rank the section it genuinely overlaps
    # first, and never the unrelated one, exactly as English already does.
    chunks = [
        DocChunk(
            source="a.md",
            heading="CAN Bus",
            text="机器人的CAN总线接线需要120欧姆终端电阻",
        ),
        DocChunk(
            source="b.md",
            heading="Firmware",
            text="使用SWD或JTAG刷写URTC固件",
        ),
    ]
    index = build_index(chunks)

    results = search(index, "CAN总线接线终端电阻", top_k=2)

    assert results
    assert results[0].chunk.heading == "CAN Bus"
    assert results[0].score > 0.0


def test_search_finds_the_right_japanese_section_by_real_term_overlap() -> None:
    chunks = [
        DocChunk(
            source="a.md",
            heading="Wiring",
            text="ロボットの配線には終端抵抗が必要です",
        ),
        DocChunk(
            source="b.md",
            heading="Firmware",
            text="SWDまたはJTAGでファームウェアを書き込みます",
        ),
    ]
    index = build_index(chunks)

    results = search(index, "配線と終端抵抗", top_k=2)

    assert results
    assert results[0].chunk.heading == "Wiring"
    assert results[0].score > 0.0


def test_search_finds_the_right_spanish_section_with_accented_terms() -> None:
    # Closes the same QA-02 criterion for a fifth language: "niño",
    # "cableado" and "última" all carry a real accented character or
    # tilde that the old ASCII-only tokenizer would have mangled.
    chunks = [
        DocChunk(
            source="a.md",
            heading="Cableado",
            text="El cableado del robôt requiere una conexión a tierra en su última fase",
        ),
        DocChunk(
            source="b.md",
            heading="Firmware",
            text="Usa SWD o JTAG para grabar el firmware de URTC",
        ),
    ]
    index = build_index(chunks)

    results = search(index, "conexión a tierra del cableado", top_k=2)

    assert results
    assert results[0].chunk.heading == "Cableado"
    assert results[0].score > 0.0


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
