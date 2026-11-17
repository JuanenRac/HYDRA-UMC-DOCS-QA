"""A reload does not go back to an older corpus release unless the operator allowed it."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from hydra_umc_docs_qa.api import BundleLoader, DocsQaServer, build_server_index, release_order
from hydra_umc_docs_qa.bundle import build_bundle
from hydra_umc_docs_qa.main import build_parser

GOOD_A = "# CAN Bus Wiring\nTwisted pair CAN bus wiring needs 120 ohm termination at both ends.\n"
GOOD_B = "# Firmware Flashing\nFlash URTC firmware over SWD or JTAG using URTC-FLASHER.\n"


def _bundle(tmp_path: Path, name: str, release: str, text: str) -> Path:
    docs = tmp_path / f"docs-{name}"
    docs.mkdir()
    (docs / "doc.md").write_text(text, encoding="utf-8")
    index, _, _ = build_server_index([docs])
    assert index is not None
    out = tmp_path / f"{name}.json"
    build_bundle(index, release, out)
    return out


def _server(path: Path, allow_downgrade: bool) -> DocsQaServer:
    loader = BundleLoader(path, None, True, None, allow_downgrade)
    index, sources, rejected = loader()
    return DocsQaServer(("127.0.0.1", 0), index, sources, rejected, loader=loader, token="t", release=loader.release, bundle_signed=loader.signed)


def test_the_order_of_releases_reads_their_numbers() -> None:
    assert release_order("0.3.1") > release_order("0.3.0") > release_order("0.2.9")
    assert release_order("0.10.0") > release_order("0.9.0")
    assert release_order("0.3.0-rc1") == release_order("0.3.0")


def test_a_reload_to_an_older_release_is_refused_and_keeps_the_index(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "new", "0.3.1", GOOD_A)
    older = _bundle(tmp_path, "old", "0.3.0", GOOD_B)
    server = _server(bundle, allow_downgrade=False)
    server.loader.path = older  # type: ignore[union-attr]
    chunks_before = len(server.index.chunks)  # type: ignore[union-attr]
    ok, message = server.reload()
    assert ok is False and "older" in message and "0.3.0" in message
    assert server.release == "0.3.1" and server.reloads == 0 and server.last_reload_conflict is True
    assert len(server.index.chunks) == chunks_before  # type: ignore[union-attr]
    server.loader.path = _bundle(tmp_path, "newer", "0.3.2", GOOD_B)  # type: ignore[union-attr]
    assert server.reload() == (True, "")
    assert server.release == "0.3.2" and server.last_reload_conflict is False


def test_the_refusal_answers_409_and_the_switch_lets_a_reload_go_back(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "new", "0.3.1", GOOD_A)
    older = _bundle(tmp_path, "old", "0.3.0", GOOD_B)
    for allowed, expected in ((False, 409), (True, 200)):
        server = _server(bundle, allowed)
        server.loader.path = older  # type: ignore[union-attr]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/reload", method="POST", data=b"", headers={"Authorization": "Bearer t"})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    status, body = response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                status, body = error.code, json.loads(error.read())
            assert status == expected and body["reloaded"] is (expected == 200)
            assert server.release == ("0.3.0" if allowed else "0.3.1")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


def test_the_option_is_on_the_command_line_and_off_by_default() -> None:
    parser = build_parser()
    assert parser.parse_args(["serve"]).allow_bundle_downgrade is False
    assert parser.parse_args(["serve", "--allow-bundle-downgrade"]).allow_bundle_downgrade is True
