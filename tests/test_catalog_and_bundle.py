# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_catalog_and_bundle.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The error-code catalog (exact lookup with citation) and the signed corpus bundle of a release."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from hydra_umc_docs_qa.api import BundleLoader, DocsQaServer, build_server_index
from hydra_umc_docs_qa.bundle import BundleError, build_bundle, load_bundle, read_key
from hydra_umc_docs_qa.catalog import (
    CatalogError,
    build_catalog,
    lookup_code,
    normalise_code,
    parse_mcu_codes,
    parse_tool_profiles,
)
from hydra_umc_docs_qa.main import main

PROTOCOL = """\
STATUS_LISTENING = 0x01
STATUS_VERIFY_OK = 0x04
STATUS_VERIFY_FAIL = 0x05
STATUS_ERROR = 0xFF
VERIFY_FAIL_REASON_CRC32 = 0x02
VERIFY_FAIL_REASON_ROLLBACK = 0x05
STATUS_BRAND_NEW = 0x20
OFS_STATUS = 0x05
"""

TOOLS = {
    "format": 1,
    "tools": [
        {
            "id": 5, "name": "DRILL", "display_name": "Drill / PCB Spindle", "description": "Spindle drive.", "kind": "actuator",
            "limits": {"min": 1000, "max": 24000, "default": 12000}, "watchdog_ms": 250, "pins_used": ["PA0", "PB0"],
            "features": ["RPM readback"], "expansion_board": "none",
        }
    ],
}


def _catalog(tmp_path: Path) -> Path:
    protocol = tmp_path / "protocol.py"
    protocol.write_text(PROTOCOL, encoding="utf-8")
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps(TOOLS), encoding="utf-8")
    out = tmp_path / "catalog"
    build_catalog(protocol, tools, out)
    return out


def _index(tmp_path: Path):
    out = _catalog(tmp_path)
    index, _sources, rejected = build_server_index([out / "mcu-error-codes.md", out / "urtc-tool-profiles.md"])
    assert index is not None and not rejected
    return index


def test_only_status_and_failure_codes_are_read() -> None:
    names = [entry.name for entry in parse_mcu_codes(PROTOCOL)]
    assert "OFS_STATUS" not in names
    assert names[0] == "STATUS_LISTENING" and "VERIFY_FAIL_REASON_ROLLBACK" in names


def test_a_code_without_description_says_so_instead_of_inventing_one() -> None:
    entry = next(e for e in parse_mcu_codes(PROTOCOL) if e.name == "STATUS_BRAND_NEW")
    assert "No description was written" in entry.text


def test_lookup_by_name_and_by_number_with_citation(tmp_path: Path) -> None:
    index = _index(tmp_path)
    by_name = lookup_code(index, "status verify fail")
    assert [m["name"] for m in by_name] == ["STATUS_VERIFY_FAIL"]
    assert by_name[0]["kind"] == "controller bootloader status"
    assert "@" in str(by_name[0]["citation"])  # source#section@sha256
    by_number = lookup_code(index, "0x05")
    assert {m["name"] for m in by_number} == {"STATUS_VERIFY_FAIL", "VERIFY_FAIL_REASON_ROLLBACK", "DRILL"}
    assert lookup_code(index, "5") == by_number


def test_unknown_or_hostile_code_finds_nothing(tmp_path: Path) -> None:
    index = _index(tmp_path)
    assert lookup_code(index, "E42") == []
    assert lookup_code(index, "STATUS_ERROR; drop") == []
    assert lookup_code(index, "x" * 200) == []
    assert normalise_code("") == (None, None)


def test_tool_profile_is_a_section_with_its_numbers(tmp_path: Path) -> None:
    [match] = lookup_code(_index(tmp_path), "drill")
    assert "1000 to 24000 (default 12000)" in match["text"] and "250 ms" in match["text"]


def test_bad_sources_are_refused(tmp_path: Path) -> None:
    with pytest.raises(CatalogError):
        parse_tool_profiles({"tools": [{"name": "x"}]})
    empty = tmp_path / "p.py"
    empty.write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(CatalogError):
        build_catalog(empty, None, tmp_path / "o")
    with pytest.raises(CatalogError):
        build_catalog(tmp_path / "missing.py", None, tmp_path / "o")


def test_catalog_command_writes_documents(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    protocol = tmp_path / "protocol.py"
    protocol.write_text(PROTOCOL, encoding="utf-8")
    assert main(["catalog", "--mcu-protocol", str(protocol), "--out", str(tmp_path / "o")]) == 0
    assert (tmp_path / "o" / "mcu-error-codes.md").is_file() and "wrote" in capsys.readouterr().out


def _key(tmp_path: Path, text: str = "a-long-enough-secret-key") -> Path:
    path = tmp_path / "key.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_bundle_is_deterministic_and_round_trips(tmp_path: Path) -> None:
    index = _index(tmp_path)
    key = read_key(_key(tmp_path))
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    build_bundle(index, "0.3.0", first, key=key)
    build_bundle(index, "0.3.0", second, key=key)
    assert first.read_bytes() == second.read_bytes()
    loaded = load_bundle(first, key=key)
    assert loaded.release == "0.3.0" and loaded.signed
    assert [c.citation for c in loaded.index.chunks] == [c.citation for c in index.chunks]
    assert {m["name"] for m in lookup_code(loaded.index, "STATUS_ERROR")} == {"STATUS_ERROR"}


def test_bundle_with_changed_content_or_wrong_key_is_refused(tmp_path: Path) -> None:
    index = _index(tmp_path)
    key = read_key(_key(tmp_path))
    path = tmp_path / "a.json"
    build_bundle(index, "0.3.0", path, key=key)
    with pytest.raises(BundleError, match="signature"):
        load_bundle(path, key=b"another-key-of-enough-length")
    path.write_text(path.read_text(encoding="utf-8").replace("0.3.0", "9.9.9", 1), encoding="utf-8")
    with pytest.raises(BundleError, match="digest"):
        load_bundle(path, key=key)


def test_unsigned_bundle_needs_the_explicit_switch(tmp_path: Path) -> None:
    index = _index(tmp_path)
    path = tmp_path / "u.json"
    build_bundle(index, "0.3.0", path)
    with pytest.raises(BundleError, match="could not be checked"):
        load_bundle(path)
    with pytest.raises(BundleError, match="could not be checked"):
        load_bundle(path, key=b"a-long-enough-secret-key")
    assert load_bundle(path, allow_unsigned=True).signed is False


def test_bad_release_key_and_garbage_are_refused(tmp_path: Path) -> None:
    index = _index(tmp_path)
    with pytest.raises(BundleError):
        build_bundle(index, "latest", tmp_path / "x.json")
    with pytest.raises(BundleError):
        read_key(_key(tmp_path, "short"))
    junk = tmp_path / "junk.json"
    junk.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(BundleError):
        load_bundle(junk, allow_unsigned=True)


def test_bm25_bundle_keeps_its_ranking(tmp_path: Path) -> None:
    out = _catalog(tmp_path)
    from hydra_umc_docs_qa.index import search

    index, _s, _r = build_server_index([out / "mcu-error-codes.md"], scorer="bm25")
    assert index is not None
    path = tmp_path / "bm.json"
    build_bundle(index, "1.0", path)
    loaded = load_bundle(path, allow_unsigned=True).index
    assert [r.chunk.citation for r in search(loaded, "image crc32 mismatch")] == [r.chunk.citation for r in search(index, "image crc32 mismatch")]


def test_bundle_commands_and_server_report_release_and_error_endpoint(tmp_path: Path) -> None:
    out = _catalog(tmp_path)
    key_file = _key(tmp_path)
    bundle = tmp_path / "corpus.json"
    assert main(["bundle", "--release", "0.3.0", "--out", str(bundle), "--key-file", str(key_file), "--docs", str(out)]) == 0
    loader = BundleLoader(bundle, read_key(key_file), False)
    index, sources, rejected = loader()
    server = DocsQaServer(("127.0.0.1", 0), index, sources, rejected, loader=loader, release=loader.release, bundle_signed=loader.signed)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        stats = json.loads(urllib.request.urlopen(f"{base}/stats", timeout=5).read())
        assert stats["release"] == "0.3.0" and stats["bundleSigned"] is True
        found = json.loads(urllib.request.urlopen(f"{base}/error?code=STATUS_ERROR", timeout=5).read())
        assert found["found"] and found["matches"][0]["value"] == 255
        missing = json.loads(urllib.request.urlopen(f"{base}/error?code=E42", timeout=5).read())
        assert missing["found"] is False and missing["matches"] == []
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"{base}/error", timeout=5)
        assert err.value.code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_serve_refuses_an_unchecked_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _catalog(tmp_path)
    bundle = tmp_path / "corpus.json"
    assert main(["bundle", "--release", "0.3.0", "--out", str(bundle), "--docs", str(out)]) == 0
    assert main(["serve", "--bundle", str(bundle), "--port", "0"]) == 2
    assert "could not be checked" in capsys.readouterr().err
