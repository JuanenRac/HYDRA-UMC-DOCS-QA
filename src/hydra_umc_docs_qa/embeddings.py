# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/embeddings.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Embedding search behind `search()`, off unless the operator asks for it.

`search()` ranks by words (TF-IDF or BM25). With an embedder attached (`--embedder SPEC`) each section also gets a vector, the
question gets one, and a section scores

    lexical score + weight * cosine similarity of the two vectors

so a section that only resembles the question (a different word form, a typo, or - with a real model - a different phrasing) can
still be found, and a section that matches by words keeps the score it always had. The lexical score is added as it is, so the
absolute thresholds (`--min-score`, `--min-coverage`) keep their meaning; the coverage of a result is always the share of the words
of the question the section really contains, so an answer that rests on similarity alone is flagged as weak by `--min-coverage`.

Two embedders can be named:

* `builtin:ngram` - the one that ships here, with no dependency: signed, hashed character 3-grams of the words, normalised. It is
  tolerant of typos and word endings. It is NOT a semantic model: "brake" and "stop" are as far apart as in plain search.
* `module:callable` - a function of the operator's own that takes a list of texts and returns one list of floats per text (for
  example a wrapper around a sentence-embedding model installed on the machine). This project takes no model dependency itself; the
  callable is imported only when the operator names it, from the operator's own installation.

Nothing here is stored in the disk cache or in a bundle: the vectors are computed when the corpus is indexed.
"""
from __future__ import annotations

import hashlib
import importlib
import math
from dataclasses import dataclass, replace
from typing import Callable, Sequence

from .index import SearchResult, TfidfIndex, _coverage, analyze

Embedder = Callable[[list[str]], Sequence[Sequence[float]]]

DEFAULT_WEIGHT = 0.5
DEFAULT_MIN_SIMILARITY = 0.5
NGRAM_DIMENSIONS = 512
BUILTIN = "builtin:ngram"


class EmbeddingError(ValueError):
    """An embedder that cannot be loaded, or that answered something that is not a list of vectors."""


def ngram_embedder(texts: list[str]) -> list[list[float]]:
    """Signed, hashed character 3-grams of the words (with the word edges marked), L2-normalised; the same text always gives the same vector."""
    out = []
    for text in texts:
        vector = [0.0] * NGRAM_DIMENSIONS
        for word in analyze(text):
            padded = f"^{word}$"
            grams = [padded[i : i + 3] for i in range(max(1, len(padded) - 2))]
            for gram in grams:
                digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
                number = int.from_bytes(digest, "big")
                vector[number % NGRAM_DIMENSIONS] += 1.0 if (number >> 63) & 1 else -1.0
        out.append(vector)
    return out


def load_embedder(spec: str) -> Embedder:
    """`builtin:ngram`, or `package.module:callable` imported from the operator's own installation."""
    if spec == BUILTIN:
        return ngram_embedder
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise EmbeddingError(f"--embedder must be {BUILTIN} or module:callable, got {spec!r}")
    try:
        function = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as error:
        raise EmbeddingError(f"--embedder {spec!r} cannot be loaded: {error}") from error
    if not callable(function):
        raise EmbeddingError(f"--embedder {spec!r} is not callable")
    return function


def _normalised(vectors: Sequence[Sequence[float]], expected: int) -> list[tuple[float, ...]]:
    if len(vectors) != expected:
        raise EmbeddingError(f"the embedder returned {len(vectors)} vector(s) for {expected} text(s)")
    out: list[tuple[float, ...]] = []
    dimension = None
    for vector in vectors:
        values = tuple(float(v) for v in vector)
        if not values or any(not math.isfinite(v) for v in values):
            raise EmbeddingError("the embedder returned an empty vector or one with a value that is not finite")
        if dimension is None:
            dimension = len(values)
        elif len(values) != dimension:
            raise EmbeddingError("the embedder returned vectors of different sizes")
        norm = math.sqrt(sum(v * v for v in values))
        out.append(tuple(v / norm for v in values) if norm else values)
    return out


@dataclass(frozen=True)
class EmbeddingIndex:
    """The vectors of the sections of an index (same order as `TfidfIndex.chunks`) and what is needed to embed a question."""

    vectors: tuple[tuple[float, ...], ...]
    embedder: Embedder
    weight: float = DEFAULT_WEIGHT
    min_similarity: float = DEFAULT_MIN_SIMILARITY

    def similarities(self, question: str) -> list[float]:
        (query,) = _normalised(self.embedder([question]), 1)
        if self.vectors and len(query) != len(self.vectors[0]):
            raise EmbeddingError("the vector of the question has another size than the vectors of the sections")
        return [max(0.0, sum(a * b for a, b in zip(query, vector, strict=True))) for vector in self.vectors]


def attach_embeddings(
    index: TfidfIndex, embedder: Embedder, *, weight: float = DEFAULT_WEIGHT, min_similarity: float = DEFAULT_MIN_SIMILARITY
) -> TfidfIndex:
    """The same index with a vector per section; `search()` then adds the similarity to the lexical score."""
    if not 0.0 < weight <= 10.0:
        raise EmbeddingError("the embedding weight must be above 0 and at most 10")
    if not 0.0 <= min_similarity < 1.0:
        raise EmbeddingError("the minimum similarity must be from 0 to below 1")
    vectors = _normalised(embedder([chunk.text for chunk in index.chunks]), len(index.chunks)) if index.chunks else []
    return replace(index, semantic=EmbeddingIndex(tuple(vectors), embedder, weight, min_similarity))


def semantic_search(index: TfidfIndex, lexical: list[SearchResult], query: str, top_k: int) -> list[SearchResult]:
    """Combine the lexical results with the embedding similarity (see the module text). `lexical` holds every section with a score above 0."""
    semantic = index.semantic
    assert isinstance(semantic, EmbeddingIndex)
    similarity = semantic.similarities(query)
    positions = {id(chunk): i for i, chunk in enumerate(index.chunks)}
    query_terms = set(analyze(query, stem=index.stem))
    merged: dict[int, SearchResult] = {}
    for result in lexical:
        i = positions[id(result.chunk)]
        merged[i] = SearchResult(chunk=result.chunk, score=result.score + semantic.weight * similarity[i], coverage=result.coverage)
    for i, value in enumerate(similarity):
        if i in merged or value < semantic.min_similarity:
            continue
        chunk = index.chunks[i]
        merged[i] = SearchResult(chunk=chunk, score=semantic.weight * value, coverage=_coverage(query_terms, set(analyze(chunk.text, stem=index.stem))))
    results = sorted(merged.values(), key=lambda r: (-r.score, positions[id(r.chunk)]))
    return results[:top_k]
