# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
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
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .index import TfidfIndex, build_index, search
from .ingest import ingest_allowed_markdown_files


def build_server_index(docs: list[Path]) -> tuple[TfidfIndex | None, list[str], list[str]]:
    """Real ingest+index, run once at server startup. Returns
    (index-or-None, ingested source names, rejected doc descriptions) -
    `index` is None only when every real document was rejected or the
    doc list was empty, which GET /query reports as 503 rather than
    crashing on a build_index([]) call."""
    chunks, rejected = ingest_allowed_markdown_files(docs)
    rejected_descriptions = [doc.describe() for doc in rejected]
    if not chunks:
        return None, [], rejected_descriptions
    index = build_index(chunks)
    ingested_sources = sorted({chunk.source for chunk in chunks})
    return index, ingested_sources, rejected_descriptions


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    def default(o: object) -> object:
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
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


class Handler(BaseHTTPRequestHandler):
    server: "DocsQaServer"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # quiet by default, same reasoning as this family's other api.py files

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/query":
            self._handle_query()
        elif path == "/stats":
            _write_json(self, 200, {
                "ingestedSources": self.server.ingested_sources,
                "rejectedDocuments": self.server.rejected_descriptions,
                "indexed": self.server.index is not None,
            })
        else:
            _write_error(self, 404, "not found")

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
        try:
            results = search(self.server.index, params["q"], top_k=top_k)
        except ValueError as e:
            _write_error(self, 400, str(e))
            return
        _write_json(self, 200, {"question": params["q"], "results": [asdict(r) for r in results]})


class DocsQaServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], index: TfidfIndex | None, ingested_sources: list[str], rejected_descriptions: list[str]) -> None:
        super().__init__(address, Handler)
        self.index = index
        self.ingested_sources = ingested_sources
        self.rejected_descriptions = rejected_descriptions
