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
import os
import signal
import sys
from typing import Any
from pathlib import Path

from . import __version__
from .health_route import health_open_requested
from .server_identity import identity_key_from
from .access import bind_problem, load_token
from .api import BundleLoader, DocsQaServer, build_corpus_index, build_server_index
from .bundle import BundleError, build_bundle, load_bundle, read_key
from .corpus_signature import SignatureError, read_public_key
from .catalog import CatalogError, build_catalog
from .corpus import CorpusError, collect, load_manifest
from .cache import compute_cache_key, load_cached_index, save_cached_index
from .extract import best_sentence
from .highlight import highlight_terms
from .ingest import RejectedDocument, RejectionReason, ingest_markdown_file, partition_allowed_paths, set_pdf_enabled
from .embeddings import DEFAULT_MIN_SIMILARITY, DEFAULT_WEIGHT, EmbeddingError, attach_embeddings, load_embedder
from .pdf_text import PdfError
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
REQUIRE_SIGNED_ENV = "HYDRA_UMC_DOCS_QA_REQUIRE_SIGNED_CORPUS"
PUBLIC_KEY_ENV = "HYDRA_UMC_DOCS_QA_BUNDLE_PUBLIC_KEY"


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
    embedding: tuple[str, float, float] | None = None,
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
            except (UnicodeDecodeError, PdfError):
                undecodable.append(RejectedDocument(path=path, reason=RejectionReason.UNDECODABLE))
        index = build_index(chunks, scorer=scorer, stem=stem, heading_boost=heading_boost)
        if use_cache:
            save_cached_index(cache_key, index, undecodable)

    for doc in undecodable:
        print(f"REJECTED {doc.describe()}")

    if embedding is not None and index.chunks:
        spec, weight, minimum = embedding
        index = attach_embeddings(index, load_embedder(spec), weight=weight, min_similarity=minimum)

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
    health_open: bool = False,
    corpus: Path | None = None,
    scorer: str = "tfidf",
    stem: bool = False,
    heading_boost: int = 1,
    token_file: str | None = None,
    min_score: float = 0.0,
    min_coverage: float = 0.0,
    bundle: Path | None = None,
    bundle_key_file: Path | None = None,
    allow_unsigned_bundle: bool = False,
    bundle_public_key: str | None = None,
    bundle_fallback_local: bool = False,
    identity_key_file: str | None = None,
    embedding: tuple[str, float, float] | None = None,
) -> int:
    try:
        token = load_token(token_file)
        identity_key = identity_key_from(identity_key_file)
        problem = bind_problem(addr, token) if identity_key is None else None
        if problem:
            print(f"error: {problem}", file=sys.stderr)
            return 2
        if not (0.0 <= min_coverage <= 1.0) or min_score < 0.0:
            raise ValueError("--min-score must be 0 or more and --min-coverage between 0 and 1")
        options = {"scorer": scorer, "stem": stem, "heading_boost": heading_boost}
        loader: Any
        if bundle is not None:
            public = read_public_key(bundle_public_key) if bundle_public_key else None
            bundle_loader = BundleLoader(bundle, read_key(bundle_key_file) if bundle_key_file else None, allow_unsigned_bundle, public)
            loader = bundle_loader
            if bundle_fallback_local:
                try:
                    bundle_loader()
                except BundleError as exc:
                    print(f"WARNING: the corpus bundle was refused ({exc}); indexing the local documents instead, so the answers are NOT from the signed corpus")
                    bundle = None
        if bundle is not None:
            pass
        elif corpus is not None:
            manifest = load_manifest(corpus)

            def loader():
                # the manifest is read again on every reload: a root or a pin that was edited takes effect
                return build_corpus_index(load_manifest(corpus), **options)

            build_corpus_index(manifest, **options)  # type: ignore[arg-type]  # fails here, before the port is bound, if the options are unusable
        else:
            def loader():
                return build_server_index(docs, **options)
        if embedding is not None:
            spec, weight, minimum = embedding
            embedder = load_embedder(spec)
            plain_loader = loader

            def loader():  # the vectors are made again on every reload, from the sections it brings
                found, sources, refused = plain_loader()
                if found is not None and found.chunks:
                    found = attach_embeddings(found, embedder, weight=weight, min_similarity=minimum)
                return found, sources, refused

            for name in ("release", "signed"):
                if hasattr(plain_loader, name):
                    setattr(loader, name, getattr(plain_loader, name))
        index, ingested_sources, rejected_descriptions = loader()
    except (ValueError, CorpusError, BundleError, SignatureError, EmbeddingError) as exc:
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
        release=getattr(loader, "release", None), bundle_signed=getattr(loader, "signed", False),
    )
    server.identity_key = identity_key
    print(f"[docs-qa] HTTP API listening on {addr}:{port}")
    print("[docs-qa] GET /query?q=...&top_k=N, GET /error?code=..., GET /stats" + (", POST /reload (and SIGHUP)" if token or identity_key is not None else "") + ("; tokens of HYDRA-UMC-SERVER accepted, nothing is open" if identity_key is not None else ""))
    if not token and identity_key is None:
        print("[docs-qa] no token: reload is off (give --token-file to allow POST /reload); SIGHUP reloads the documents all the same")
    if hasattr(signal, "SIGHUP"):
        def _on_hangup(_signum, _frame) -> None:
            ok, message = server.reload()
            print(f"[docs-qa] SIGHUP: {'reloaded' if ok else 'reload failed - ' + message}")

        signal.signal(signal.SIGHUP, _on_hangup)
    try:
        server.health_open = health_open_requested(health_open)
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
    parser.add_argument(
        "--pdf", action="store_true",
        help="Also ingest .pdf documents (one section per page that has text; stdlib reader, no OCR, encrypted files refused). Off by default, or set HYDRA_UMC_DOCS_QA_PDF=1.",
    )
    parser.add_argument("--scorer", choices=("tfidf", "bm25"), default="tfidf", help="Ranking: cosine over TF-IDF (default) or BM25.")
    parser.add_argument("--stem", action="store_true", help="Strip the common endings of a word before indexing and looking it up.")
    parser.add_argument("--heading-boost", type=int, default=1, dest="heading_boost", help="Index the words of a section's heading, repeated this many times minus one (1 to 10; default 1: headings are not indexed).")


def _add_embedding_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--embedder", default=None, metavar="SPEC",
        help="Add embedding similarity to the ranking: builtin:ngram (hashed character 3-grams, typo-tolerant, not a semantic model) or module:callable "
        "(your own function from texts to vectors, e.g. around a sentence-embedding model). Off by default.",
    )
    parser.add_argument(
        "--embedding-weight", type=float, default=DEFAULT_WEIGHT, dest="embedding_weight",
        help=f"A section scores its lexical score plus this weight times its cosine similarity (default {DEFAULT_WEIGHT}).",
    )
    parser.add_argument(
        "--embedding-min-similarity", type=float, default=DEFAULT_MIN_SIMILARITY, dest="embedding_min_similarity",
        help=f"A section with no word in common with the question is found only above this similarity (default {DEFAULT_MIN_SIMILARITY}).",
    )


def _embedding_from(args: argparse.Namespace) -> tuple[str, float, float] | None:
    return None if args.embedder is None else (args.embedder, args.embedding_weight, args.embedding_min_similarity)


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
    _add_embedding_options(query_parser)

    serve_parser = subparsers.add_parser(
        "serve",
        help="Run 'query' as a JSON/HTTP API (GET /query?q=...&top_k=N) - unlike the CLI, "
             "which re-ingests and re-indexes the corpus on every invocation, this builds "
             "the real TfidfIndex ONCE at startup and reuses it for every query.",
    )
    serve_parser.add_argument("--addr", default="127.0.0.1", help="address to bind the HTTP API to")
    serve_parser.add_argument("--health-open", action="store_true", help="answer GET /health (open to loopback, to other callers only with the bearer token); closed by default, or set HYDRA_UMC_DOCS_QA_HEALTH_OPEN=1")
    serve_parser.add_argument("--port", type=int, default=8110, help="port for the HTTP API")
    _add_retrieval_options(serve_parser)
    _add_embedding_options(serve_parser)
    serve_parser.add_argument("--identity-key-file", default=None, dest="identity_key_file", help="file with the signing key of HYDRA-UMC-SERVER (its JWT_SECRET): a valid token of the SERVER is then accepted, every route (GET /health and GET /stats too) demands an identity and a viewer may read but not change (off by default; or set HYDRA_UMC_DOCS_QA_IDENTITY_KEY_FILE)")
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
        "--bundle", type=Path, default=None,
        help="Load the index of a release from a corpus bundle (see 'bundle') instead of indexing documents here.",
    )
    serve_parser.add_argument(
        "--bundle-key-file", type=Path, default=None, dest="bundle_key_file",
        help="File with the key the bundle was signed with; its signature is checked against it.",
    )
    serve_parser.add_argument(
        "--allow-unsigned-bundle", action="store_true", dest="allow_unsigned_bundle",
        help="Accept a bundle whose signature cannot be checked (unsigned, or no key given). Off by default: the bundle is refused. "
             "HYDRA_UMC_DOCS_QA_REQUIRE_SIGNED_CORPUS=0 does the same; with --bundle-public-key the release signature is always required.",
    )
    serve_parser.add_argument(
        "--bundle-public-key", default=None, dest="bundle_public_key",
        help="Public key (64 hexadecimal characters, or a file with them or a PEM) of the release signature of the bundle: FILE.sha256 and FILE.sig "
             "must verify over its exact bytes before it is loaded (or set HYDRA_UMC_DOCS_QA_BUNDLE_PUBLIC_KEY). The service never holds a private key.",
    )
    serve_parser.add_argument(
        "--bundle-fallback-local", action="store_true", dest="bundle_fallback_local",
        help="With --bundle and --docs/--corpus: when the bundle is refused at startup, say so in a WARNING and index those local documents instead of stopping. Off by default.",
    )
    serve_parser.add_argument(
        "--docs",
        nargs="+",
        type=Path,
        default=None,
        help="Markdown files or directories to ingest at startup (directories are "
             "searched recursively; default: this repo's own README.md/CHANGELOG.md).",
    )

    verify_parser = subparsers.add_parser(
        "verify-bundle", help="Check the release signature of a corpus bundle (FILE.sha256 and FILE.sig) against a public key, without loading it into a service."
    )
    verify_parser.add_argument("bundle", type=Path, help="The bundle file.")
    verify_parser.add_argument("--public-key", required=True, dest="public_key", help="64 hexadecimal characters, or a file with them or an Ed25519 PEM.")

    catalog_parser = subparsers.add_parser(
        "catalog", help="Write the error codes of the controller and the URTC tool profiles as documents the assistant can index and cite."
    )
    catalog_parser.add_argument("--mcu-protocol", type=Path, required=True, dest="mcu_protocol", help="The protocol.py of the controller bridge (read only).")
    catalog_parser.add_argument("--urtc-tools", type=Path, default=None, dest="urtc_tools", help="The spec/tools.json of URTC (read only).")
    catalog_parser.add_argument("--out", type=Path, required=True, help="Directory the documents are written to.")

    bundle_parser = subparsers.add_parser(
        "bundle", help="Build the index of a release once and write it as a file (optionally signed) that 'serve --bundle' loads."
    )
    bundle_parser.add_argument("--release", required=True, help="The release the corpus belongs to, for example 0.3.0.")
    bundle_parser.add_argument("--out", type=Path, required=True, help="The bundle file to write.")
    bundle_parser.add_argument("--key-file", type=Path, default=None, dest="key_file", help="File with the key to sign the bundle with (HMAC-SHA256).")
    bundle_parser.add_argument("--docs", nargs="+", type=Path, default=None, help="Markdown files or directories to index.")
    _add_retrieval_options(bundle_parser)

    return parser


def _run_catalog(args: argparse.Namespace) -> int:
    try:
        written = build_catalog(args.mcu_protocol, args.urtc_tools, args.out)
    except CatalogError as exc:
        print(f"ERROR: {exc}")
        return 2
    for path in written:
        print(f"wrote {path}")
    return 0


def _run_bundle(args: argparse.Namespace) -> int:
    try:
        if args.corpus is not None:
            index, _sources, rejected = build_corpus_index(load_manifest(args.corpus), scorer=args.scorer, stem=args.stem, heading_boost=args.heading_boost)
        else:
            docs = args.docs if args.docs is not None else list(_DEFAULT_DOCS)
            index, _sources, rejected = build_server_index(docs, scorer=args.scorer, stem=args.stem, heading_boost=args.heading_boost)
        for description in rejected:
            print(f"REJECTED {description}")
        if index is None:
            print("ERROR: no document could be indexed, so there is nothing to put in a bundle")
            return 1
        header = build_bundle(index, args.release, args.out, key=read_key(args.key_file) if args.key_file else None)
    except (ValueError, CorpusError, BundleError) as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"wrote {args.out}: release {header['release']}, {header['documents']} document(s), {header['chunks']} section(s), "
          f"{'signed' if header['signed'] else 'NOT signed'}, digest {header['digest']}")
    return 0


def _run_verify_bundle(args: argparse.Namespace) -> int:
    try:
        loaded = load_bundle(args.bundle, public_key=read_public_key(args.public_key))
    except (BundleError, SignatureError) as exc:
        print(f"VERIFY=FAIL {exc}")
        return 1
    print(f"VERIFY=PASS release {loaded.release}, {len(loaded.documents)} document(s), signed with the trusted release key")
    return 0


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
    # the switch belongs to this run only: a flag turns it on, otherwise the environment decides
    set_pdf_enabled(True if getattr(args, "pdf", False) else None)

    if args.command == "query":
        docs = args.docs if args.docs is not None else list(_DEFAULT_DOCS)
        try:
            return _run_query(args.question, docs, args.top_k, corpus=args.corpus, scorer=args.scorer, stem=args.stem, heading_boost=args.heading_boost, extractive=args.extractive, embedding=_embedding_from(args))
        except (ValueError, EmbeddingError) as exc:
            print(f"ERROR: {exc}")
            return 2
    if args.command == "serve":
        docs = args.docs if args.docs is not None else list(_DEFAULT_DOCS)
        return _run_serve(
            args.addr, args.port, docs, corpus=args.corpus, scorer=args.scorer, stem=args.stem, heading_boost=args.heading_boost,
            token_file=args.token_file, min_score=args.min_score, min_coverage=args.min_coverage,
            bundle=args.bundle, bundle_key_file=args.bundle_key_file,
            allow_unsigned_bundle=args.allow_unsigned_bundle or os.environ.get(REQUIRE_SIGNED_ENV, "").strip().lower() in ("0", "false", "no", "off"),
            bundle_public_key=args.bundle_public_key or os.environ.get(PUBLIC_KEY_ENV) or None,
            bundle_fallback_local=args.bundle_fallback_local and (args.docs is not None or args.corpus is not None),
            health_open=args.health_open, identity_key_file=args.identity_key_file,
            embedding=_embedding_from(args),
        )
    if args.command == "catalog":
        return _run_catalog(args)
    if args.command == "bundle":
        return _run_bundle(args)
    if args.command == "verify-bundle":
        return _run_verify_bundle(args)

    _print_identity()
    return 0


if __name__ == "__main__":
    sys.exit(main())
