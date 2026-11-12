# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_embeddings.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
#
# Embedding search behind search(): the built-in character n-gram embedder, an operator-supplied embedder (a fake model with
# a synonym table), the combination with the lexical score, the thresholds and the command line.
# =============================================================================
from __future__ import annotations

import math
import sys
import types
from pathlib import Path

import pytest

from hydra_umc_docs_qa.embeddings import (
    EmbeddingError,
    attach_embeddings,
    load_embedder,
    ngram_embedder,
)
from hydra_umc_docs_qa.index import answerable, build_index, search
from hydra_umc_docs_qa.ingest import DocChunk
from hydra_umc_docs_qa.main import main


def _chunk(index: int, heading: str, text: str) -> DocChunk:
    return DocChunk(source="manual.md", heading=heading, text=text, index=index)


CHUNKS = [
    _chunk(0, "Brakes", "Engage the brake before opening the guard door."),
    _chunk(1, "Wiring", "Twisted pair CAN bus wiring needs 120 ohm termination."),
    _chunk(2, "Flashing", "Flashing the bootloader erases the application."),
]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


# ------------------------------------------------------------------ the built-in embedder
def test_ngram_vectors_are_deterministic_and_sized() -> None:
    first = ngram_embedder(["CAN bus termination"])
    assert first == ngram_embedder(["CAN bus termination"])
    assert len(first[0]) == 512 and any(first[0])


def test_ngram_tolerates_typos_and_word_forms_but_not_synonyms() -> None:
    base, typo, form, other, synonym = ngram_embedder(["termination", "terminaton", "terminating", "bootloader", "stop"])
    assert _cosine(base, typo) > 0.6
    assert _cosine(base, form) > 0.4
    assert _cosine(base, other) < 0.3
    brake = ngram_embedder(["brake"])[0]
    assert _cosine(brake, synonym) < 0.3  # it is not a semantic model, and the docs say so


def test_an_empty_text_gives_a_zero_vector_not_an_error() -> None:
    (vector,) = ngram_embedder([""])
    assert not any(vector)


# ------------------------------------------------------------------ loading an embedder
def test_load_embedder_accepts_the_builtin_and_a_module_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_embedder("builtin:ngram") is ngram_embedder
    module = types.ModuleType("fake_model_module")
    module.embed = lambda texts: [[1.0, 0.0] for _ in texts]  # type: ignore[attr-defined]
    module.not_callable = 3  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_model_module", module)
    assert load_embedder("fake_model_module:embed") is module.embed  # type: ignore[attr-defined]
    for bad in ("nonsense", "fake_model_module:missing", "no_such_module_at_all:f", "fake_model_module:not_callable", ":x", "x:"):
        with pytest.raises(EmbeddingError):
            load_embedder(bad)


def test_a_bad_embedder_answer_is_refused() -> None:
    index = build_index(CHUNKS)
    with pytest.raises(EmbeddingError, match="3 text"):
        attach_embeddings(index, lambda texts: [[1.0]])
    with pytest.raises(EmbeddingError, match="not finite"):
        attach_embeddings(index, lambda texts: [[float("nan")] for _ in texts])
    with pytest.raises(EmbeddingError, match="different sizes"):
        attach_embeddings(index, lambda texts: [[1.0] * (i + 1) for i, _ in enumerate(texts)])
    with pytest.raises(EmbeddingError):
        attach_embeddings(index, ngram_embedder, weight=0.0)
    with pytest.raises(EmbeddingError):
        attach_embeddings(index, ngram_embedder, min_similarity=1.0)


# ------------------------------------------------------------------ the combination with the lexical score
def test_without_an_embedder_search_is_unchanged() -> None:
    index = build_index(CHUNKS)
    assert index.semantic is None
    assert [r.chunk.index for r in search(index, "CAN bus termination")] == [1]
    assert search(index, "terminaton") == []  # a typo finds nothing in plain search


def test_a_typo_finds_the_section_through_the_embedding() -> None:
    index = attach_embeddings(build_index(CHUNKS), ngram_embedder, min_similarity=0.2)
    results = search(index, "terminaton wirng")
    assert results and results[0].chunk.index == 1
    assert results[0].coverage == 0.0  # no word in common: flagged as weak by --min-coverage
    assert answerable(results, min_coverage=0.5)[0] is False


def test_lexical_matches_keep_their_score_and_gain_the_similarity() -> None:
    plain = build_index(CHUNKS)
    mixed = attach_embeddings(plain, ngram_embedder, weight=0.5)
    lexical = search(plain, "CAN bus termination")[0]
    combined = search(mixed, "CAN bus termination")[0]
    assert combined.chunk is lexical.chunk
    assert lexical.score < combined.score <= lexical.score + 0.5 + 1e-9
    assert combined.coverage == lexical.coverage == 1.0


def test_a_fake_semantic_model_finds_a_synonym() -> None:
    synonyms = {"stop": 0, "brake": 0, "wiring": 1, "bootloader": 2}

    def model(texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vector = [0.0, 0.0, 0.0]
            for word in text.lower().replace(".", "").split():
                if word in synonyms:
                    vector[synonyms[word]] += 1.0
            out.append(vector)
        return out

    index = attach_embeddings(build_index(CHUNKS), model, weight=1.0, min_similarity=0.5)
    results = search(index, "how do I stop")
    assert results[0].chunk.index == 0 and results[0].coverage == 0.0
    assert search(build_index(CHUNKS), "how do I stop") == []


def test_sections_below_the_minimum_similarity_stay_out() -> None:
    index = attach_embeddings(build_index(CHUNKS), ngram_embedder, min_similarity=0.99)
    assert search(index, "terminaton") == []


def test_top_k_and_order_are_deterministic() -> None:
    index = attach_embeddings(build_index(CHUNKS), ngram_embedder, min_similarity=0.0)
    first = [r.chunk.index for r in search(index, "bus", top_k=2)]
    assert first == [r.chunk.index for r in search(index, "bus", top_k=2)]
    assert len(first) <= 2
    assert search(index, "bus", top_k=0) == []
    with pytest.raises(ValueError):
        search(index, "bus", top_k=-1)


def test_an_empty_index_attaches_nothing_to_embed() -> None:
    index = attach_embeddings(build_index([]), ngram_embedder)
    assert search(index, "anything") == []


# ------------------------------------------------------------------ the command line
def _doc(tmp_path: Path) -> Path:
    doc = tmp_path / "manual.md"
    doc.write_text(
        "# Wiring\nTwisted pair CAN bus wiring needs 120 ohm termination.\n# Flashing\nFlashing the bootloader erases the application.\n",
        encoding="utf-8",
    )
    return doc


def test_query_with_the_embedder_finds_a_typo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc = _doc(tmp_path)
    assert main(["query", "terminaton", "--docs", str(doc)]) == 0
    assert "No relevant passages" in capsys.readouterr().out
    assert main(["query", "terminaton", "--docs", str(doc), "--embedder", "builtin:ngram", "--embedding-min-similarity", "0.2"]) == 0
    assert "Wiring" in capsys.readouterr().out


def test_query_refuses_a_bad_embedder_spec(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["query", "x", "--docs", str(_doc(tmp_path)), "--embedder", "nonsense"]) == 2
    assert "--embedder" in capsys.readouterr().out
    assert main(["query", "x", "--docs", str(_doc(tmp_path)), "--embedder", "builtin:ngram", "--embedding-weight", "0"]) == 2


# ------------------------------------------------------------------ the HTTP service
def test_the_service_answers_a_typo_when_the_index_carries_embeddings(tmp_path: Path) -> None:
    import json
    import threading
    import urllib.request

    from hydra_umc_docs_qa.api import DocsQaServer, build_server_index

    index, sources, refused = build_server_index([_doc(tmp_path)])
    index = attach_embeddings(index, ngram_embedder, min_similarity=0.2)
    server = DocsQaServer(("127.0.0.1", 0), index, sources, refused)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/query?q=terminaton&top_k=3"
        with urllib.request.urlopen(url, timeout=5) as response:
            body = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert body["results"] and body["results"][0]["chunk"]["heading"] == "Wiring"
