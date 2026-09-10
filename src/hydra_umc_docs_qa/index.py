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

# QA-02 (P1): the old
# ASCII-only [a-z0-9]+ pattern returned [] for any query with no Latin
# text at all - a real Chinese/Japanese-language question against this
# project's own translated README corpus could never match anything,
# regardless of how many terms it actually shared with the indexed
# text. `_LATIN_TOKEN_RE` now also covers Latin-1 Supplement/Extended-A/B
# (U+00C0-U+024F: accented Spanish/French/Italian/German letters like
# n-tilde/e-acute/u-umlaut/German sz) so those languages' own accented
# words tokenize as real, whole words instead of losing their accented
# character.
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9À-ɏ]+")
# CJK ideographs (Han) and Japanese kana - the ranges actually used by
# this ecosystem's own zho/jpn README translations: Hiragana+Katakana
# (U+3040-U+30FF), CJK Unified Ideographs Extension A (U+3400-U+4DBF),
# CJK Unified Ideographs (U+4E00-U+9FFF), CJK Compatibility Ideographs
# (U+F900-U+FAFF).
_CJK_RUN_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]+")
_SCAN_RE = re.compile(f"{_CJK_RUN_RE.pattern}|{_LATIN_TOKEN_RE.pattern}")


def _cjk_bigrams(run: str) -> list[str]:
    """CJK text has no whitespace between words, so treating a whole run
    as one token would swallow an entire sentence into a single term that
    can only ever match an identical sentence. Overlapping 2-character
    windows are the same well-established, dictionary-free fix real
    search engines without a language-specific segmenter use (e.g.
    Lucene/Elasticsearch's own CJK bigram analyzer) - no extra AI/NLP
    service required. A lone single-character run still becomes its own
    real one-character token rather than being dropped."""
    if len(run) < 2:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def tokenize(text: str) -> list[str]:
    """Lowercase tokenization - real for ASCII, accented-Latin AND CJK
    text alike, with the same normalization shared by indexing and
    querying (both call this one function)."""
    text = text.lower()
    tokens: list[str] = []
    for match in _SCAN_RE.finditer(text):
        run = match.group(0)
        if _CJK_RUN_RE.fullmatch(run):
            tokens.extend(_cjk_bigrams(run))
        else:
            tokens.append(run)
    return tokens


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
    # `a.keys() & b.keys()` is a set, and CPython's set iteration order
    # depends on string hash values, which are randomized per process by
    # default (PYTHONHASHSEED). IEEE-754 float addition is not
    # associative, so summing in a hash-seed-dependent order makes `dot` a
    # function of which process happened to run the query, not just of
    # the corpus and query text - a real determinism hole even where it
    # doesn't happen to move the final ranking for a given corpus.
    # Sorting the shared terms first removes that dependency outright:
    # the summation order, and therefore the score, is now a pure
    # function of the corpus and query alone.
    shared = sorted(a.keys() & b.keys())
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
    if top_k < 0:
        # `results[:top_k]` below would otherwise hit Python's negative-slice
        # semantics (e.g. top_k=-1 returns everything but the lowest-ranked
        # result) instead of erroring or returning nothing - a real,
        # CLI-reachable edge case (`--top-k -1`), not just a defensive guard.
        raise ValueError(f"top_k must be >= 0, got {top_k}")

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
