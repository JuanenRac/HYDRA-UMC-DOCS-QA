# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/extract.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The sentence of a passage that answers a question best.

A passage can be several paragraphs long and the sentence that matters is
often not the first one. `best_sentence` splits the passage into sentences,
counts how many different words of the question each one holds (with the same
`tokenize()` the index uses, so what counts as a match is what retrieval
counted) and returns the best one; the earliest wins a tie, and a passage with
no sentence in common with the question gives back its start, as the plain
snippet does. Nothing is generated: the text returned is always a piece of the
passage.
"""
from __future__ import annotations

import re

from .index import tokenize

# a sentence ends at . ! ? or a CJK full stop followed by a space or the end, or at a blank line;
# a dot inside a number or a name (1.5, v0.2, file.md) does not end it
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])|\n\s*\n")


def split_sentences(text: str) -> list[str]:
    parts = [" ".join(part.split()) for part in _SENTENCE_END.split(text)]
    return [part for part in parts if part]


def best_sentence(text: str, question: str, *, max_len: int = 240) -> str:
    """The sentence of `text` with the most distinct words of `question`, cut to `max_len`."""
    sentences = split_sentences(text)
    if not sentences:
        return ""
    wanted = set(tokenize(question))
    best, best_hits = sentences[0], 0
    for sentence in sentences:
        hits = len(wanted & set(tokenize(sentence)))
        if hits > best_hits:
            best, best_hits = sentence, hits
    if len(best) > max_len:
        best = best[:max_len].rstrip() + "..."
    return best
