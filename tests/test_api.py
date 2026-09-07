# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real end-to-end HTTP tests: a real DocsQaServer (ThreadingHTTPServer)
hit with real urllib requests - same convention as this family's other
test_api.py files, reusing this repo's own tests/test_cli.py fixture
shapes."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hydra_umc_docs_qa.api import DocsQaServer, build_server_index


def _write_manual(tmp_path: Path) -> Path:
    doc = tmp_path / "manual.md"
    doc.write_text(
        "# CAN Bus Wiring\n"
        "Twisted pair CAN bus wiring needs 120 ohm termination at both ends.\n"
        "# Firmware Flashing\n"
        "Flash URTC firmware over SWD or JTAG using URTC-FLASHER.\n",
        encoding="utf-8",
    )
    return doc


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@contextmanager
def running_server(docs: list[Path]) -> Iterator[str]:
    index, ingested_sources, rejected_descriptions = build_server_index(docs)
    server = DocsQaServer(("127.0.0.1", 0), index, ingested_sources, rejected_descriptions)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_query_against_real_markdown_file(tmp_path: Path) -> None:
    doc = _write_manual(tmp_path)
    with running_server([doc]) as base:
        q = urllib.parse.quote("CAN bus termination")
        status, body = _get(f"{base}/query?q={q}")
        assert status == 200
        assert body["results"]
        assert body["results"][0]["chunk"]["heading"] == "CAN Bus Wiring"
        assert body["results"][0]["chunk"]["source"] == "manual.md"


def test_query_with_no_matching_terms_is_an_honest_miss(tmp_path: Path) -> None:
    doc = tmp_path / "manual.md"
    doc.write_text("# Firmware Flashing\nFlash URTC firmware over SWD or JTAG.\n", encoding="utf-8")
    with running_server([doc]) as base:
        q = urllib.parse.quote("quantum entanglement lasagna")
        status, body = _get(f"{base}/query?q={q}")
        assert status == 200
        assert body["results"] == []


def test_query_top_k(tmp_path: Path) -> None:
    doc = _write_manual(tmp_path)
    with running_server([doc]) as base:
        q = urllib.parse.quote("firmware")
        status, body = _get(f"{base}/query?q={q}&top_k=1")
        assert status == 200
        assert len(body["results"]) <= 1


def test_query_missing_q_param(tmp_path: Path) -> None:
    doc = _write_manual(tmp_path)
    with running_server([doc]) as base:
        status, body = _get(f"{base}/query")
        assert status == 400


def test_query_returns_503_when_nothing_was_ingested(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.md"
    with running_server([missing]) as base:
        status, body = _get(f"{base}/query?q=test")
        assert status == 503


def test_stats(tmp_path: Path) -> None:
    doc = _write_manual(tmp_path)
    with running_server([doc]) as base:
        status, body = _get(f"{base}/stats")
        assert status == 200
        assert body["indexed"] is True
        assert "manual.md" in body["ingestedSources"]


def test_stats_reports_rejected_documents(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.md"
    with running_server([missing]) as base:
        status, body = _get(f"{base}/stats")
        assert status == 200
        assert body["indexed"] is False
        assert len(body["rejectedDocuments"]) == 1


def test_not_found(tmp_path: Path) -> None:
    doc = _write_manual(tmp_path)
    with running_server([doc]) as base:
        status, body = _get(f"{base}/nope")
        assert status == 404
