# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/highlight.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real console highlighting of matched search terms in `query` output.

Plain ANSI SGR codes, not the `rich` package: this project is real,
deliberately stdlib-only (see pyproject.toml - only `pytest` is a
dependency, and only for `dev`), and highlighting a handful of already-
tokenized words in already-printed text is well within reach of a few
ANSI escape sequences - pulling in a full terminal-rendering library
would be disproportionate to what this feature actually needs.
"""
from __future__ import annotations

import os
import re
import sys

from .index import tokenize

_BOLD_YELLOW = "\x1b[1;33m"
_RESET = "\x1b[0m"


def _supports_color(stream: object = None) -> bool:
    """Real, conservative check: only highlight when stdout is a real
    terminal and the caller hasn't opted out via NO_COLOR (an
    already-established convention: https://no-color.org). Piping
    `query` output to a file or another tool must get plain, undecorated
    text, not raw escape codes mixed into it."""
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream if stream is not None else sys.stdout
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def highlight_terms(text: str, query: str, *, stream: object = None) -> str:
    """Wraps every real, whole occurrence of a query term in `text` with
    bold-yellow ANSI codes, using the exact same `tokenize()` normalization
    index.py already uses to build/search the TF-IDF index - so what gets
    highlighted here is always the real, literal thing that actually made
    this passage match, not a separately-guessed pattern that could drift
    from the real retrieval logic. Returns `text` unchanged when color
    output is not appropriate (see `_supports_color`) or the query yields
    no real terms to highlight."""
    terms = sorted(set(tokenize(query)), key=len, reverse=True)
    if not terms or not _supports_color(stream):
        return text

    # Longest terms first so a shorter term that is a substring of a
    # longer one (e.g. CJK bigrams sharing a character) never carves up a
    # match the longer term should have highlighted whole.
    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    return pattern.sub(lambda m: f"{_BOLD_YELLOW}{m.group(0)}{_RESET}", text)
