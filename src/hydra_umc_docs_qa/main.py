# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/main.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
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
import signal
import sys
from pathlib import Path

from . import __version__
from .access import bind_problem, load_token
from .api import DocsQaServer, build_corpus_index, build_server_index
from .corpus import CorpusError, collect, load_manifest
from .cache import compute_cache_key, load_cached_index, save_cached_index
from .extract import best_sentence
from .highlight import highlight_terms
from .ingest import RejectedDocument, RejectionReason, ingest_markdown_file, partition_allowed_paths
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


def _run_query(
    question: str,
    docs: list[Path],
    top_k: int,
    *,
    corpus: Path | None = None,
    scorer: str = "tfidf",
    stem: bool = False,
    heading_boost: int = 1,
    extractive: bool = False,
) -> int:
    if corpus is not None:
        # the documents come from the manifest (roots, exclusions, pins); the disk cache is for the plain --docs index only
        try:
            allowed, refused = collect(load_manifest(corpus))
        except CorpusError as exc:
            print(f"ERROR: {exc}")
            return 2
        valid_paths = allowed
        rejected = refused
    else:
        valid_paths, rejected = partition_allowed_paths(docs)
    for doc in rejected:
        print(f"REJECTED {doc.describe()}")
    use_cache = corpus is None and scorer == "tfidf" and not stem and heading_boost == 1

    # Real disk-persisted index cache (cache.py): a cache hit here skips
    # re-reading and re-tokenizing every document and rebuilding the
    # whole TF-IDF index from scratch - the real cost `query` otherwise
    # pays on every single invocation, even against an unchanged corpus.
    cache_key = compute_cache_key(valid_paths)
    cached = load_cached_index(cache_key) if use_cache else None
    if cached is not None:
        index, undecodable = cached
    else:
        chunks = []
        undecodable = []
        for path in valid_paths:
            try:
                chunks.extend(ingest_markdown_file(path))
            except UnicodeDecodeError:
                undecodable.append(RejectedDocument(path=path, reason=RejectionReason.UNDECODABLE))
        index = build_index(chunks, scorer=scorer, stem=stem, heading_boost=heading_boost)
        if use_cache:
            save_cached_index(cache_key, index, undecodable)

    for doc in undecodable:
        print(f"REJECTED {doc.describe()}")

    if not index.chunks:
        print("No documents ingested - check the --docs paths.")
        return 1

    try:
        results = search(index, question, top_k=top_k)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

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
        if extractive:
            snippet = best_sentence(result.chunk.text, question, max_len=_SNIPPET_LEN * 2)
        elif len(snippet) > _SNIPPET_LEN:
            snippet = snippet[:_SNIPPET_LEN].rstrip() + "..."
        snippet = highlight_terms(snippet, question)
        cited = f"{result.chunk.source}#{result.chunk.index}" + (f"@{result.chunk.sha256[:12]}" if result.chunk.sha256 else "")
        print(f"{rank}. [{result.score:.3f}] {cited} - {heading}")
        print(f"   {snippet}\n")
    return 0


def _run_serve(
    addr: str,
    port: int,
    docs: list[Path],
    *,
    corpus: Path | None = None,
    scorer: str = "tfidf",
    stem: bool = False,
    heading_boost: int = 1,
    token_file: str | None = None,
    min_score: float = 0.0,
    min_coverage: float = 0.0,
) -> int:
    try:
        token = load_token(token_file)
        problem = bind_problem(addr, token)
        if problem:
            print(f"error: {problem}", file=sys.stderr)
            return 2
        if not (0.0 <= min_coverage <= 1.0) or min_score < 0.0:
            raise ValueError("--min-score must be 0 or more and --min-coverage between 0 and 1")
        options = {"scorer": scorer, "stem": stem, "heading_boost": heading_boost}
        if corpus is not None:
            manifest = load_manifest(corpus)

            def loader():
                # the manifest is read again on every reload: a root or a pin that was edited takes effect
                return build_corpus_index(load_manifest(corpus), **options)

            build_corpus_index(manifest, **options)  # type: ignore[arg-type]  # fails here, before the port is bound, if the options are unusable
        else:
            def loader():
                return build_server_index(docs, **options)
        index, ingested_sources, rejected_descriptions = loader()
    except (ValueError, CorpusError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for description in rejected_descriptions:
        print(f"REJECTED {description}")
    if index is None:
        print("WARNING: no documents were successfully ingested - GET /query will report 503 until this is fixed.")
    else:
        print(f"[docs-qa] indexed {len(ingested_sources)} document(s): {', '.join(ingested_sources)}")

    server = DocsQaServer(
        (addr, port), index, ingested_sources, rejected_descriptions,
        loader=loader, token=token, min_score=min_score, min_coverage=min_coverage,
    )
    print(f"[docs-qa] HTTP API listening on {addr}:{port}")
    print("[docs-qa] GET /query?q=...&top_k=N, GET /stats" + (", POST /reload (and SIGHUP)" if token else ""))
    if not token:
        print("[docs-qa] no token: reload is off (give --token-file to allow POST /reload); SIGHUP reloads the documents all the same")
    if hasattr(signal, "SIGHUP"):
        def _on_hangup(_signum, _frame) -> None:
            ok, message = server.reload()
            print(f"[docs-qa] SIGHUP: {'reloaded' if ok else 'reload failed - ' + message}")

        signal.signal(signal.SIGHUP, _on_hangup)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[docs-qa] shutting down")
    return 0


def _add_retrieval_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--corpus", type=Path, default=None,
        help="Corpus manifest (JSON: roots, exclude patterns, pinned documents with their SHA-256; see corpus.py) instead of --docs.",
    )
    parser.add_argument("--scorer", choices=("tfidf", "bm25"), default="tfidf", help="Ranking: cosine over TF-IDF (default) or BM25.")
    parser.add_argument("--stem", action="store_true", help="Strip the common endings of a word before indexing and looking it up.")
    parser.add_argument("--heading-boost", type=int, default=1, dest="heading_boost", help="Index the words of a section's heading, repeated this many times minus one (1 to 10; default 1: headings are not indexed).")


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
        help="Markdown files or directories to ingest (directories are searched "
             "recursively; default: this repo's own README.md/CHANGELOG.md).",
    )
    query_parser.add_argument(
        "--top-k", type=int, default=5, help="Maximum number of passages to print (default: 5)."
    )
    query_parser.add_argument(
        "--extractive", action="store_true",
        help="Print the sentence of each passage that best answers the question instead of the first characters of the passage.",
    )
    _add_retrieval_options(query_parser)

    serve_parser = subparsers.add_parser(
        "serve",
        help="Run 'query' as a JSON/HTTP API (GET /query?q=...&top_k=N) - unlike the CLI, "
             "which re-ingests and re-indexes the corpus on every invocation, this builds "
             "the real TfidfIndex ONCE at startup and reuses it for every query.",
    )
    serve_parser.add_argument("--addr", default="127.0.0.1", help="address to bind the HTTP API to")
    serve_parser.add_argument("--port", type=int, default=8110, help="port for the HTTP API")
    _add_retrieval_options(serve_parser)
    serve_parser.add_argument(
        "--token-file", default=None, dest="token_file",
        help="File with the bearer token GET /query and POST /reload require (or HYDRA_UMC_DOCS_QA_TOKEN). Needed to bind an address that "
             "is not loopback, and without one POST /reload is refused.",
    )
    serve_parser.add_argument(
        "--min-score", type=float, default=0.0, dest="min_score",
        help="Lowest score the best section may have for a question to count as answered (0: any match). A request may ask for more, never less.",
    )
    serve_parser.add_argument(
        "--min-coverage", type=float, default=0.0, dest="min_coverage",
        help="Smallest share (0 to 1) of the words of the question that some section must hold (0: any match). A request may ask for more, never less.",
    )
    serve_parser.add_argument(
        "--docs",
        nargs="+",
        type=Path,
        default=None,
        help="Markdown files or directories to ingest at startup (directories are "
             "searched recursively; default: this repo's own README.md/CHANGELOG.md).",
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
        try:
            return _run_query(args.question, docs, args.top_k, corpus=args.corpus, scorer=args.scorer, stem=args.stem, heading_boost=args.heading_boost, extractive=args.extractive)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 2
    if args.command == "serve":
        docs = args.docs if args.docs is not None else list(_DEFAULT_DOCS)
        return _run_serve(
            args.addr, args.port, docs, corpus=args.corpus, scorer=args.scorer, stem=args.stem, heading_boost=args.heading_boost,
            token_file=args.token_file, min_score=args.min_score, min_coverage=args.min_coverage,
        )

    _print_identity()
    return 0


if __name__ == "__main__":
    sys.exit(main())
