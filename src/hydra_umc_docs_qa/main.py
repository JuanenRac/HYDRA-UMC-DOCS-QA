# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/main.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Entry point for HYDRA-UMC-DOCS-QA.

Bare invocation prints identity/version/role, unchanged from the
scaffolding stage. The real v0 retrieval work lives behind the `query`
subcommand: real TF-IDF lexical search (see index.py) over real ingested
Markdown (see ingest.py) - not yet the embedding-based semantic search or
the Hailo-10 LLM synthesis step the README's own roadmap describes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .ingest import ingest_allowed_markdown_files
from .index import build_index, search

PROJECT_NAME = "HYDRA-UMC-DOCS-QA"
ROLE = (
    "Docs-QA (Hailo-10) - retrieval-augmented technical assistant "
    "grounded in the ecosystem's own documentation."
)

# This file lives at src/hydra_umc_docs_qa/main.py - two parents up is the
# repository root, where this project's own real README.md/CHANGELOG.md
# live, used as the default corpus when --docs is not given.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DOCS = (_REPO_ROOT / "README.md", _REPO_ROOT / "CHANGELOG.md")

_SNIPPET_LEN = 160


def _print_identity() -> None:
    print(f"{PROJECT_NAME} v{__version__}")
    print(ROLE)


def _run_query(question: str, docs: list[Path], top_k: int) -> int:
    chunks, rejected = ingest_allowed_markdown_files(docs)
    for doc in rejected:
        print(f"REJECTED {doc.describe()}")

    if not chunks:
        print("No documents ingested - check the --docs paths.")
        return 1

    index = build_index(chunks)
    results = search(index, question, top_k=top_k)

    if not results:
        print(f'No relevant passages found for: "{question}"')
        print(
            "(v0 is real lexical TF-IDF retrieval only - a question whose words "
            "don't overlap the corpus gets an honest miss, not a guess.)"
        )
        return 0

    print(f'Top {len(results)} passage(s) for: "{question}"\n')
    for rank, result in enumerate(results, start=1):
        heading = result.chunk.heading or "(document start)"
        snippet = result.chunk.text.replace("\n", " ").strip()
        if len(snippet) > _SNIPPET_LEN:
            snippet = snippet[:_SNIPPET_LEN].rstrip() + "..."
        print(f"{rank}. [{result.score:.3f}] {result.chunk.source}#{result.chunk.index} - {heading}")
        print(f"   {snippet}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hydra-umc-docs-qa", description=ROLE)
    subparsers = parser.add_subparsers(dest="command")

    query_parser = subparsers.add_parser(
        "query", help="Real TF-IDF lexical search over ingested Markdown docs."
    )
    query_parser.add_argument("question", help="The question to search for.")
    query_parser.add_argument(
        "--docs",
        nargs="+",
        type=Path,
        default=None,
        help="Markdown files to ingest (default: this repo's own README.md/CHANGELOG.md).",
    )
    query_parser.add_argument(
        "--top-k", type=int, default=5, help="Maximum number of passages to print (default: 5)."
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    # Real ingested Markdown (this ecosystem's own READMEs) is full of
    # emoji - the default Windows console codepage (cp1252) can't encode
    # them and would otherwise crash mid-print. Reconfigure to UTF-8 with
    # a safe fallback instead of losing real output to a stack trace.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "query":
        docs = args.docs if args.docs is not None else list(_DEFAULT_DOCS)
        return _run_query(args.question, docs, args.top_k)

    _print_identity()
    return 0


if __name__ == "__main__":
    sys.exit(main())
