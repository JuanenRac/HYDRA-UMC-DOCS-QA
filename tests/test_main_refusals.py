# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_main_refusals.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""What `serve`, `bundle` and `catalog` refuse before they bind a port or write a file: exit code 2 (or 1), one line, nothing written."""
from __future__ import annotations

from pathlib import Path

import pytest

from hydra_umc_docs_qa.main import main

DOC = "# Wiring\nTwisted pair CAN wiring needs 120 ohm termination at both ends.\n"


def write_doc(directory: Path) -> Path:
    path = directory / "manual.md"
    path.write_text(DOC, encoding="utf-8")
    return path


class TestServeRefusals:
    def test_an_address_away_from_loopback_without_a_token_is_refused_before_binding(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["serve", "--addr", "0.0.0.0", "--port", "0", "--docs", str(write_doc(tmp_path))])
        captured = capsys.readouterr()
        assert code == 2
        assert "not loopback" in captured.err
        assert captured.out == ""

    def test_a_token_file_that_is_missing_is_refused(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["serve", "--port", "0", "--docs", str(write_doc(tmp_path)), "--token-file", str(tmp_path / "none")])
        assert code == 2
        assert "token file" in capsys.readouterr().err

    @pytest.mark.parametrize("option,value", [("--min-coverage", "1.5"), ("--min-coverage", "-0.1"), ("--min-score", "-1")])
    def test_thresholds_out_of_range_are_refused(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str, value: str) -> None:
        code = main(["serve", "--port", "0", "--docs", str(write_doc(tmp_path)), option, value])
        assert code == 2
        assert "--min-score must be 0 or more" in capsys.readouterr().err

    def test_a_bundle_that_does_not_exist_is_refused(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["serve", "--port", "0", "--bundle", str(tmp_path / "none.bundle")])
        assert code == 2
        assert capsys.readouterr().err.startswith("error:")

    def test_a_corpus_manifest_that_does_not_exist_is_refused(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["serve", "--port", "0", "--corpus", str(tmp_path / "none.json")])
        assert code == 2
        assert capsys.readouterr().err.startswith("error:")


class TestBundle:
    def test_an_unsigned_bundle_says_so_and_is_written(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = tmp_path / "release.bundle"
        code = main(["bundle", "--release", "0.0.1", "--out", str(out), "--docs", str(write_doc(tmp_path))])
        text = capsys.readouterr().out
        assert code == 0
        assert out.is_file()
        assert "NOT signed" in text
        assert "release 0.0.1" in text

    def test_a_bundle_signed_with_a_key_says_signed(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        key = tmp_path / "key"
        key.write_text("k" * 32 + "\n", encoding="utf-8")
        out = tmp_path / "release.bundle"
        code = main(["bundle", "--release", "0.0.1", "--out", str(out), "--key-file", str(key), "--docs", str(write_doc(tmp_path))])
        text = capsys.readouterr().out
        assert code == 0
        assert "signed" in text and "NOT signed" not in text

    def test_documents_that_cannot_be_indexed_make_no_bundle(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        out = tmp_path / "release.bundle"
        code = main(["bundle", "--release", "0.0.1", "--out", str(out), "--docs", str(empty)])
        assert code == 1
        assert "nothing to put in a bundle" in capsys.readouterr().out
        assert not out.exists()

    def test_a_missing_key_file_is_refused_and_nothing_is_written(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = tmp_path / "release.bundle"
        code = main(["bundle", "--release", "0.0.1", "--out", str(out), "--key-file", str(tmp_path / "none"), "--docs", str(write_doc(tmp_path))])
        assert code == 2
        assert capsys.readouterr().out.startswith("ERROR:")
        assert not out.exists()


class TestCatalog:
    def test_a_missing_protocol_file_is_refused(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["catalog", "--mcu-protocol", str(tmp_path / "none.py"), "--out", str(tmp_path / "out")])
        assert code == 2
        assert capsys.readouterr().out.startswith("ERROR:")
