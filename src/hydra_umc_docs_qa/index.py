# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/index.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real, stdlib-only TF-IDF retrieval over ingested Markdown chunks.

This is real lexical (term-overlap) retrieval, not embedding-based
semantic vector search - the README's "Local Vector Search" feature is
honestly labeled v0 for exactly that reason. It needs no ML dependency,
runs anywhere Python does, and gives this project a real, testable
retrieval kernel a future embedding-based index can be swapped in behind
the same `search()` contract.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .ingest import DocChunk

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, alnum-only tokenization - real, deterministic, stdlib-only."""
    return _TOKEN_RE.findall(text.lower())


@dataclass(frozen=True)
class TfidfIndex:
    """A real TF-IDF index: one sparse term-weight vector per chunk."""

    chunks: tuple[DocChunk, ...]
    idf: dict[str, float]
    chunk_vectors: tuple[dict[str, float], ...]


def _term_frequencies(tokens: list[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = sum(counts.values())
    if total == 0:
        return {}
    return {term: count / total for term, count in counts.items()}


def build_index(chunks: list[DocChunk]) -> TfidfIndex:
    """Build a real TF-IDF index over the given chunks."""
    chunk_tokens = [tokenize(chunk.text) for chunk in chunks]
    doc_count = len(chunks)

    doc_freq: Counter[str] = Counter()
    for tokens in chunk_tokens:
        doc_freq.update(set(tokens))

    idf = {
        term: math.log((1 + doc_count) / (1 + freq)) + 1.0
        for term, freq in doc_freq.items()
    }

    chunk_vectors: list[dict[str, float]] = []
    for tokens in chunk_tokens:
        tf = _term_frequencies(tokens)
        chunk_vectors.append({term: weight * idf[term] for term, weight in tf.items()})

    return TfidfIndex(chunks=tuple(chunks), idf=idf, chunk_vectors=tuple(chunk_vectors))


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    shared = a.keys() & b.keys()
    dot = sum(a[term] * b[term] for term in shared)
    norm_a = math.sqrt(sum(weight * weight for weight in a.values()))
    norm_b = math.sqrt(sum(weight * weight for weight in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass(frozen=True)
class SearchResult:
    chunk: DocChunk
    score: float


def search(index: TfidfIndex, query: str, *, top_k: int = 5) -> list[SearchResult]:
    """Rank real chunks by cosine similarity to `query`, highest first.

    Query terms outside the index's vocabulary contribute nothing (a real,
    honest miss) rather than being silently dropped from an average that
    would understate how little the corpus actually covers the question.
    """
    query_tf = _term_frequencies(tokenize(query))
    query_vector = {
        term: weight * index.idf[term] for term, weight in query_tf.items() if term in index.idf
    }

    results = [
        SearchResult(chunk=chunk, score=_cosine_similarity(query_vector, vector))
        for chunk, vector in zip(index.chunks, index.chunk_vectors)
    ]
    results = [result for result in results if result.score > 0.0]
    results.sort(key=lambda result: result.score, reverse=True)
    return results[:top_k]
