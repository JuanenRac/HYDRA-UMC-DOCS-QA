# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_extract.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from hydra_umc_docs_qa.api import DocsQaServer, build_server_index
from hydra_umc_docs_qa.extract import best_sentence, split_sentences
from hydra_umc_docs_qa.main import main

PASSAGE = (
    "Twisted pair cable is used for the bus. Each end of the bus needs a 120 ohm termination resistor. "
    "Keep the stubs short."
)


def test_the_sentence_with_most_words_of_the_question_wins_even_when_it_is_not_the_first() -> None:
    assert best_sentence(PASSAGE, "bus termination resistor") == "Each end of the bus needs a 120 ohm termination resistor."


def test_a_tie_goes_to_the_earliest_sentence() -> None:
    assert best_sentence("First has bus. Second has bus.", "bus") == "First has bus."


def test_without_a_common_word_the_start_of_the_passage_comes_back() -> None:
    assert best_sentence(PASSAGE, "zzz") == "Twisted pair cable is used for the bus."


def test_a_dot_inside_a_number_or_a_file_name_does_not_end_a_sentence() -> None:
    assert split_sentences("Version 1.5 reads file.md first. Then it stops.") == ["Version 1.5 reads file.md first.", "Then it stops."]


def test_blank_lines_and_cjk_full_stops_end_a_sentence() -> None:
    assert split_sentences("one line\n\nanother line") == ["one line", "another line"]
    assert split_sentences("第一句。第二句。") == ["第一句。", "第二句。"]


def test_a_long_sentence_is_cut_and_an_empty_passage_gives_nothing() -> None:
    assert best_sentence("word " * 100, "word", max_len=20).endswith("...")
    assert best_sentence("   ", "word") == ""


def test_the_returned_text_is_always_part_of_the_passage() -> None:
    answer = best_sentence(PASSAGE, "stubs")
    assert answer in PASSAGE


def test_the_query_command_prints_the_best_sentence_with_extractive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc = tmp_path / "manual.md"
    doc.write_text("# CAN Bus\n" + "Filler sentence about nothing. " * 12 + "The termination resistor is 120 ohm.\n", encoding="utf-8")
    assert main(["query", "termination resistor", "--docs", str(doc), "--extractive"]) == 0
    out = capsys.readouterr().out
    assert "The termination resistor is 120 ohm." in out
    capsys.readouterr()
    assert main(["query", "termination resistor", "--docs", str(doc)]) == 0
    assert "The termination resistor is 120 ohm." not in capsys.readouterr().out  # beyond the first 160 characters


def test_the_api_adds_the_answer_sentence_only_when_asked(tmp_path: Path) -> None:
    doc = tmp_path / "manual.md"
    doc.write_text("# CAN Bus\n" + "Filler sentence about nothing. " * 12 + "The termination resistor is 120 ohm.\n", encoding="utf-8")
    index, sources, rejected = build_server_index([doc])
    server = DocsQaServer(("127.0.0.1", 0), index, sources, rejected)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/query?q=termination+resistor&extractive=1", timeout=5) as response:
            body = json.loads(response.read())
        assert body["results"][0]["answer"] == "The termination resistor is 120 ohm."
        with urllib.request.urlopen(f"{base}/query?q=termination+resistor", timeout=5) as response:
            assert "answer" not in json.loads(response.read())["results"][0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
