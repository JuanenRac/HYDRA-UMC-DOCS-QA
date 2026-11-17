"""GET /stats can be made to ask for the bearer token (--stats-auth); by default it stays open for the health probe."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from hydra_umc_docs_qa.api import DocsQaServer, build_server_index
from hydra_umc_docs_qa.main import build_parser, main


def _get(base: str, headers: dict[str, str] | None = None) -> int:
    request = urllib.request.Request(f"{base}/stats", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            json.loads(response.read())
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _serve(tmp_path: Path, stats_auth: bool) -> tuple[DocsQaServer, threading.Thread]:
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "doc.md").write_text("# CAN\nTermination of the bus is 120 ohm.\n", encoding="utf-8")
    index, sources, rejected = build_server_index([docs])
    server = DocsQaServer(("127.0.0.1", 0), index, sources, rejected, token="secret-for-the-test")
    server.stats_auth = stats_auth
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.mark.parametrize(("stats_auth", "without_token", "with_token"), [(False, 200, 200), (True, 401, 200)])
def test_stats_asks_for_the_token_only_when_told_to(tmp_path: Path, stats_auth: bool, without_token: int, with_token: int) -> None:
    server, thread = _serve(tmp_path, stats_auth)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _get(base) == without_token
        assert _get(base, {"Authorization": "Bearer secret-for-the-test"}) == with_token
        assert _get(base, {"Authorization": "Bearer wrong"}) == (401 if stats_auth else 200)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_the_option_is_off_by_default_and_needs_a_token(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert build_parser().parse_args(["serve"]).stats_auth is False
    docs = tmp_path / "doc.md"
    docs.write_text("# CAN\nTermination of the bus is 120 ohm.\n", encoding="utf-8")
    assert main(["serve", "--docs", str(docs), "--stats-auth"]) == 2
    assert "--token-file" in capsys.readouterr().err
