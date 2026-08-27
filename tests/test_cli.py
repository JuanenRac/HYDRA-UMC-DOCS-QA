# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_cli.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

from pathlib import Path

import pytest

from hydra_umc_docs_qa.main import main


def test_bare_invocation_prints_identity(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "HYDRA-UMC-DOCS-QA v" in captured.out
    assert "Docs-QA" in captured.out


def test_query_against_real_markdown_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc = tmp_path / "manual.md"
    doc.write_text(
        "# CAN Bus Wiring\n"
        "Twisted pair CAN bus wiring needs 120 ohm termination at both ends.\n"
        "# Firmware Flashing\n"
        "Flash URTC firmware over SWD or JTAG using URTC-FLASHER.\n",
        encoding="utf-8",
    )

    exit_code = main(["query", "CAN bus termination", "--docs", str(doc)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "CAN Bus Wiring" in captured.out
    assert "manual.md" in captured.out


def test_query_with_no_matching_terms_is_an_honest_miss(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = tmp_path / "manual.md"
    doc.write_text("# Firmware Flashing\nFlash URTC firmware over SWD or JTAG.\n", encoding="utf-8")

    exit_code = main(["query", "quantum entanglement lasagna", "--docs", str(doc)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "No relevant passages found" in captured.out


def test_query_defaults_to_this_repos_own_readme_and_changelog(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["query", "RAG documentation retrieval"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "README.md" in captured.out or "CHANGELOG.md" in captured.out


def test_query_does_not_crash_on_emoji_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Real bug found via live verification: printing a snippet containing
    # an emoji (this ecosystem's own READMEs are full of them) crashed on
    # Windows consoles using the default cp1252 codepage. main() now
    # reconfigures stdout to UTF-8 with a safe fallback - this asserts it
    # no longer raises, not just that the string happens to be present.
    doc = tmp_path / "manual.md"
    doc.write_text(
        "# CAN Bus Wiring\n"
        "\U0001f50d Twisted pair CAN bus wiring needs 120 ohm termination.\n",
        encoding="utf-8",
    )

    exit_code = main(["query", "CAN bus wiring", "--docs", str(doc)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "CAN Bus Wiring" in captured.out
