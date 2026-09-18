# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/conftest.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real, shared test fixtures.

Every test runs against its own real, isolated index-cache directory
(cache.py's own `HYDRA_UMC_DOCS_QA_CACHE_DIR` override) instead of the
real developer/CI machine's actual `~/.cache` - a real test run must
never read or write outside `tmp_path`.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_docs_qa_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYDRA_UMC_DOCS_QA_CACHE_DIR", str(tmp_path / ".docs-qa-cache"))
