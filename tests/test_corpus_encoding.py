# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_corpus_encoding.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A corpus manifest in another encoding."""
from __future__ import annotations

import pytest

from hydra_umc_docs_qa.corpus import CorpusError, load_manifest


def test_a_corpus_manifest_in_another_encoding_is_refused_with_its_reason(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_bytes(b'{"note": "se\xf1al"}')
    with pytest.raises(CorpusError, match="not JSON"):
        load_manifest(path)
