# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_highlight.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

from hydra_umc_docs_qa.highlight import highlight_terms


class _FakeTty:
    def isatty(self) -> bool:
        return True


class _FakeNonTty:
    def isatty(self) -> bool:
        return False


def test_highlights_real_matched_terms_when_output_is_a_real_terminal() -> None:
    result = highlight_terms("CAN bus wiring needs termination", "CAN termination", stream=_FakeTty())
    assert "\x1b[1;33mCAN\x1b[0m" in result
    assert "\x1b[1;33mtermination\x1b[0m" in result
    # Non-matched words are left untouched.
    assert "bus wiring needs" in result


def test_is_case_insensitive_like_the_real_tokenizer(tmp_path=None) -> None:
    result = highlight_terms("Termination matters", "termination", stream=_FakeTty())
    assert "\x1b[1;33mTermination\x1b[0m" in result


def test_leaves_text_untouched_when_not_a_real_terminal() -> None:
    result = highlight_terms("CAN bus wiring needs termination", "CAN termination", stream=_FakeNonTty())
    assert result == "CAN bus wiring needs termination"
    assert "\x1b[" not in result


def test_leaves_text_untouched_when_no_color_env_var_is_set(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    result = highlight_terms("CAN bus wiring needs termination", "CAN termination", stream=_FakeTty())
    assert result == "CAN bus wiring needs termination"


def test_query_with_no_real_terms_returns_text_unchanged() -> None:
    result = highlight_terms("some text here", "", stream=_FakeTty())
    assert result == "some text here"
