# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Plain JSON/HTTP surface (stdlib http.server) - same convention as this
family's other api.py files. Real difference from the CLI's own `query`
subcommand: the CLI re-ingests and re-indexes the corpus on every single
invocation (fine for a one-shot command, wasteful for a service handling
one query after another) - this server ingests and builds the real
TfidfIndex ONCE at startup (`build_server_index()`), then GET /query only
ever calls the already-fast `search()` against that cached index.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .access import authorized, reject
from .corpus import CorpusManifest, collect
from .extract import best_sentence
from .index import TfidfIndex, answerable, build_index, search
from .ingest import RejectedDocument, ingest_allowed_markdown_files


def build_server_index(
    docs: list[Path], *, scorer: str = "tfidf", stem: bool = False, heading_boost: int = 1
) -> tuple[TfidfIndex | None, list[str], list[str]]:
    """Real ingest+index, run once at server startup. Returns
    (index-or-None, ingested source names, rejected doc descriptions) -
    `index` is None only when every real document was rejected or the
    doc list was empty, which GET /query reports as 503 rather than
    crashing on a build_index([]) call."""
    chunks, rejected = ingest_allowed_markdown_files(docs)
    return _finish(chunks, rejected, scorer, stem, heading_boost)


def build_corpus_index(
    manifest: CorpusManifest, *, scorer: str = "tfidf", stem: bool = False, heading_boost: int = 1
) -> tuple[TfidfIndex | None, list[str], list[str]]:
    """The same, for the documents a corpus manifest allows (see corpus.py): only those are read, and a pinned one whose bytes changed is left out."""
    paths, refused = collect(manifest)
    chunks, rejected = ingest_allowed_markdown_files(paths)
    return _finish(chunks, list(refused) + list(rejected), scorer, stem, heading_boost)


def _finish(
    chunks: list, rejected: list[RejectedDocument], scorer: str, stem: bool, heading_boost: int
) -> tuple[TfidfIndex | None, list[str], list[str]]:
    rejected_descriptions = [doc.describe() for doc in rejected]
    if not chunks:
        return None, [], rejected_descriptions
    index = build_index(chunks, scorer=scorer, stem=stem, heading_boost=heading_boost)
    ingested_sources = sorted({chunk.source for chunk in chunks})
    return index, ingested_sources, rejected_descriptions


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    def default(o: object) -> object:
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)  # type: ignore[call-overload]
        return str(o)
    body = json.dumps(payload, default=default).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_error(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    _write_json(handler, status, {"error": message})


def _query_params(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    parsed = urlparse(handler.path)
    values = parse_qs(parsed.query, keep_blank_values=True)
    return {key: value[0] for key, value in values.items() if value}


MAX_TOP_K = 50


class Handler(BaseHTTPRequestHandler):
    server: "DocsQaServer"

    # Seconds a client may stay silent on its connection before the server
    # drops it, so a client that connects and sends nothing cannot hold a
    # handler thread forever. Socket I/O only.
    timeout = 15.0

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # quiet by default, same reasoning as this family's other api.py files

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/query":
            if not authorized(self, self.server.token):
                reject(self)
                return
            self._handle_query()
        elif path == "/stats":
            # the health path of the manifest: open, and numbers and file names only
            server = self.server
            index = server.index
            _write_json(self, 200, {
                "ingestedSources": server.ingested_sources,
                # file names only: the absolute paths of the server's disk stay on the server
                "rejectedDocuments": [re.sub(r"^.*[\\/]", "", d) for d in server.rejected_descriptions],
                "indexed": index is not None,
                "chunks": len(index.chunks) if index is not None else 0,
                "builtAtMs": server.built_at_ms,
                "scorer": index.scorer if index is not None else None,
                "stem": index.stem if index is not None else None,
                "headingBoost": index.heading_boost if index is not None else None,
                "minScore": server.min_score,
                "minCoverage": server.min_coverage,
                "reloadable": server.loader is not None,
                "reloads": server.reloads,
                "lastReloadError": server.last_reload_error,
            })
        else:
            _write_error(self, 404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/reload":
            _write_error(self, 404, "not found")
            return
        # a reload re-reads the disk of the server: only a caller that holds the token may ask for it, and without a token nobody may
        if not self.server.token:
            _write_error(self, 403, "reload needs a bearer token: start the service with --token-file")
            return
        if not authorized(self, self.server.token):
            reject(self)
            return
        if self.server.loader is None:
            _write_error(self, 409, "this service was not started from a document list it can read again")
            return
        ok, message = self.server.reload()
        index = self.server.index
        _write_json(self, 200 if ok else 500, {
            "reloaded": ok,
            "error": None if ok else message,
            "chunks": len(index.chunks) if index is not None else 0,
            "builtAtMs": self.server.built_at_ms,
        })

    def _handle_query(self) -> None:
        if self.server.index is None:
            _write_error(self, 503, "no documents were successfully ingested at startup - nothing to search")
            return
        params = _query_params(self)
        if "q" not in params:
            _write_error(self, 400, "missing required query param: q")
            return
        try:
            top_k = int(params.get("top_k", "5"))
        except ValueError:
            _write_error(self, 400, "top_k must be an integer")
            return
        if top_k > MAX_TOP_K:
            _write_error(self, 400, f"top_k must be at most {MAX_TOP_K}")
            return
        try:
            # a request may ask for a stricter answer than the service gives, never a looser one
            min_score = max(self.server.min_score, float(params.get("min_score", "0")))
            min_coverage = max(self.server.min_coverage, float(params.get("min_coverage", "0")))
            if not (0.0 <= min_coverage <= 1.0) or min_score < 0.0:
                raise ValueError("min_score must be 0 or more and min_coverage between 0 and 1")
            results = search(self.server.index, params["q"], top_k=top_k)
        except ValueError as e:
            _write_error(self, 400, str(e))
            return
        ok, reason = answerable(results, min_score=min_score, min_coverage=min_coverage)
        if not ok and results:
            # sections that only share a word with the question are not handed on as if they answered it
            _write_json(self, 200, {"question": params["q"], "answerable": False, "reason": reason, "results": []})
            return
        payload = [{**asdict(r), "citation": r.chunk.citation} for r in results]
        if params.get("extractive") in ("1", "true"):
            # the sentence of each passage that best answers the question (a piece of the passage, nothing generated)
            for item, r in zip(payload, results, strict=False):
                item["answer"] = best_sentence(r.chunk.text, params["q"])
        _write_json(self, 200, {"question": params["q"], "answerable": ok, "reason": reason, "results": payload})


class DocsQaServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        index: TfidfIndex | None,
        ingested_sources: list[str],
        rejected_descriptions: list[str],
        *,
        loader=None,
        token: str = "",
        min_score: float = 0.0,
        min_coverage: float = 0.0,
    ) -> None:
        super().__init__(address, Handler)
        self.index = index
        self.ingested_sources = ingested_sources
        self.rejected_descriptions = rejected_descriptions
        # `loader()` builds a fresh (index, sources, rejected) from the same documents; `reload()` swaps it in
        self.loader = loader
        self.token = token
        self.min_score = min_score
        self.min_coverage = min_coverage
        self.built_at_ms = int(time.time() * 1000)
        self.reloads = 0
        self.last_reload_error: str | None = None
        self._reload_lock = threading.Lock()

    def reload(self) -> tuple[bool, str]:
        """Builds the index again from the documents on disk and swaps it in. A reload that fails, or that finds nothing to index, leaves the index
        the service has and says why: a half-written file must not take the answers away."""
        if self.loader is None:
            return False, "no loader"
        with self._reload_lock:
            try:
                index, sources, rejected = self.loader()
            except Exception as error:  # noqa: BLE001 - any failure keeps the old index
                self.last_reload_error = f"{type(error).__name__}: {error}"[:300]
                return False, self.last_reload_error
            if index is None:
                self.last_reload_error = "no document could be ingested: the previous index is kept"
                self.rejected_descriptions = rejected
                return False, self.last_reload_error
            self.index, self.ingested_sources, self.rejected_descriptions = index, sources, rejected
            self.built_at_ms = int(time.time() * 1000)
            self.reloads += 1
            self.last_reload_error = None
            return True, ""
