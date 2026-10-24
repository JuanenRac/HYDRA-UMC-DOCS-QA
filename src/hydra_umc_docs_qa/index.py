# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/index.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
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
from dataclasses import dataclass, field

from .ingest import DocChunk

# the old
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


SCORERS = ("tfidf", "bm25")

# BM25 constants: how fast a term repeated in a chunk stops adding to the score, and how much the length of the chunk is held against it.
BM25_K1 = 1.5
BM25_B = 0.75

# Endings taken off a Latin word by `light_stem`, longest first. Deliberately short: a stemmer that cuts too much joins words that
# are not related, which is worse for a manual than a stemmer that cuts too little. A word is only cut when what is left has at least
# four letters, and a word with a digit in it (a part number, a port, a version) is never touched.
_STEM_SUFFIXES = (
    "amente", "mente", "ations", "ation", "aciones", "acion", "ingly", "ments", "ement", "idades", "idade", "idad",
    "ando", "iendo", "ings", "ing", "edly", "ers", "ies", "ed", "er", "es", "en", "os", "as", "s",
)


def light_stem(token: str) -> str:
    """A cautious suffix stripper for the Latin-script languages of this ecosystem (it leaves CJK tokens and anything with a digit alone)."""
    if not token.isalpha():
        return token
    if any("぀" <= c <= "ヿ" or "㐀" <= c <= "鿿" for c in token):
        return token
    for suffix in _STEM_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            if suffix == "ies":
                return token[: -len(suffix)] + "y"
            return token[: -len(suffix)]
    return token


def analyze(text: str, *, stem: bool = False) -> list[str]:
    """The terms of a text: the same function indexes and queries, so the two always agree."""
    tokens = tokenize(text)
    return [light_stem(t) for t in tokens] if stem else tokens


@dataclass(frozen=True)
class TfidfIndex:
    """A real index over the chunks: one sparse term-weight vector per chunk for TF-IDF, and the raw term counts for BM25.

    `scorer`, `stem` and `heading_boost` are how it was built; a query is analysed the same way. The defaults (TF-IDF, no stemming, no boost)
    are what the index always was."""

    chunks: tuple[DocChunk, ...]
    idf: dict[str, float]
    chunk_vectors: tuple[dict[str, float], ...]
    scorer: str = "tfidf"
    stem: bool = False
    heading_boost: int = 1
    term_counts: tuple[dict[str, int], ...] = ()
    average_length: float = 0.0
    document_frequency: dict[str, int] = field(default_factory=dict)


def _term_frequencies(tokens: list[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = sum(counts.values())
    if total == 0:
        return {}
    return {term: count / total for term, count in counts.items()}


def build_index(chunks: list[DocChunk], *, scorer: str = "tfidf", stem: bool = False, heading_boost: int = 1) -> TfidfIndex:
    """Build a real index over the given chunks.

    `scorer` is `tfidf` (cosine over TF-IDF vectors, the default) or `bm25`. `stem` strips the common endings of a word before it is indexed
    or looked up. `heading_boost` counts the words of a section's heading that many times (1: as they are), so a section titled after the
    thing asked about ranks above one that only mentions it."""
    if scorer not in SCORERS:
        raise ValueError(f"scorer must be one of {SCORERS}, got {scorer!r}")
    if isinstance(heading_boost, bool) or not isinstance(heading_boost, int) or not 1 <= heading_boost <= 10:
        raise ValueError(f"heading_boost must be a whole number from 1 to 10, got {heading_boost!r}")
    chunk_tokens = []
    for chunk in chunks:
        tokens = analyze(chunk.text, stem=stem)
        if heading_boost > 1 and chunk.heading:
            tokens += analyze(chunk.heading, stem=stem) * (heading_boost - 1)
        chunk_tokens.append(tokens)
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

    if scorer == "bm25":
        counts = tuple(dict(Counter(tokens)) for tokens in chunk_tokens)
        average = sum(len(t) for t in chunk_tokens) / doc_count if doc_count else 0.0
        return TfidfIndex(
            chunks=tuple(chunks), idf=idf, chunk_vectors=tuple(chunk_vectors), scorer=scorer, stem=stem, heading_boost=heading_boost,
            term_counts=counts, average_length=average, document_frequency=dict(doc_freq),
        )
    return TfidfIndex(chunks=tuple(chunks), idf=idf, chunk_vectors=tuple(chunk_vectors), scorer=scorer, stem=stem, heading_boost=heading_boost)


def _cosine_similarity(a: dict[str, float], b: dict[str, float], extra_norm_squared: float = 0.0) -> float:
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
    norm_b = math.sqrt(sum(weight * weight for weight in b.values()) + extra_norm_squared)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass(frozen=True)
class SearchResult:
    chunk: DocChunk
    score: float
    # the share of the distinct words of the question that this section contains (1.0: all of them)
    coverage: float = 1.0


def _coverage(query_terms: set[str], chunk_terms: set[str]) -> float:
    return len(query_terms & chunk_terms) / len(query_terms) if query_terms else 0.0


def search(index: TfidfIndex, query: str, *, top_k: int = 5) -> list[SearchResult]:
    """Rank real chunks by their score for `query`, highest first.

    With TF-IDF the score is the cosine similarity. Query terms outside the index's vocabulary add nothing to the match but DO
    count in the length of the query, weighted like the rarest possible term, so a question the corpus covers only in part scores
    lower than one it covers fully instead of looking just as confident. With BM25 the score is the sum, over the words of the
    question, of their weight in the section (it has no upper bound of 1). `coverage` of each result says what share of the words
    of the question the section holds.
    """
    if top_k < 0:
        # `results[:top_k]` below would otherwise hit Python's negative-slice
        # semantics (e.g. top_k=-1 returns everything but the lowest-ranked
        # result) instead of erroring or returning nothing - a real,
        # CLI-reachable edge case (`--top-k -1`), not just a defensive guard.
        raise ValueError(f"top_k must be >= 0, got {top_k}")

    query_tokens = analyze(query, stem=index.stem)
    distinct = set(query_tokens)
    if index.scorer == "bm25":
        results = _search_bm25(index, distinct)
    else:
        query_tf = _term_frequencies(query_tokens)
        query_vector = {
            term: weight * index.idf[term] for term, weight in query_tf.items() if term in index.idf
        }
        # the largest idf a term of this corpus could have is that of a term seen in no chunk
        unseen_idf = math.log(1 + len(index.chunks)) + 1.0
        unknown_norm_squared = sum(
            (weight * unseen_idf) ** 2 for term, weight in query_tf.items() if term not in index.idf
        )
        results = [
            SearchResult(chunk=chunk, score=_cosine_similarity(query_vector, vector, unknown_norm_squared), coverage=_coverage(distinct, set(vector)))
            for chunk, vector in zip(index.chunks, index.chunk_vectors, strict=True)
        ]
    results = [result for result in results if result.score > 0.0]
    results.sort(key=lambda result: result.score, reverse=True)
    return results[:top_k]


def _search_bm25(index: TfidfIndex, distinct: set[str]) -> list[SearchResult]:
    n = len(index.chunks)
    terms = sorted(t for t in distinct if t in index.document_frequency)
    weights = {t: math.log(1.0 + (n - index.document_frequency[t] + 0.5) / (index.document_frequency[t] + 0.5)) for t in terms}
    out = []
    for chunk, counts in zip(index.chunks, index.term_counts, strict=True):
        length = sum(counts.values())
        norm = BM25_K1 * (1.0 - BM25_B + BM25_B * (length / index.average_length if index.average_length else 1.0))
        score = 0.0
        for term in terms:
            tf = counts.get(term, 0)
            if tf:
                score += weights[term] * (tf * (BM25_K1 + 1.0)) / (tf + norm)
        out.append(SearchResult(chunk=chunk, score=score, coverage=_coverage(distinct, set(counts))))
    return out


def answerable(results: list[SearchResult], *, min_score: float = 0.0, min_coverage: float = 0.0) -> tuple[bool, str]:
    """Whether the best section is good enough to be given to whatever writes the answer, and if not, why.

    A model handed a section that only shares a word with the question tends to answer from it anyway. `min_score` and `min_coverage`
    (a share of the words of the question, 0 to 1) are the cell's own choice: both zero accepts any match, as before."""
    if not results:
        return False, "no section shares a word with the question"
    best = max(results, key=lambda r: r.score)
    if best.score < min_score:
        return False, f"the best section scores {best.score:.3f}, below the minimum {min_score:.3f}"
    top_coverage = max(r.coverage for r in results)
    if top_coverage < min_coverage:
        return False, f"no section holds more than {top_coverage:.0%} of the words of the question (minimum {min_coverage:.0%})"
    return True, ""
