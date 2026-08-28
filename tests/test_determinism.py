# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_determinism.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real cross-process determinism check for the `query` command.

`index.py`'s cosine similarity used to sum floating-point terms in
`dict.keys() & dict.keys()` set-intersection order, which CPython derives
from string hash values - randomized per process by default via
PYTHONHASHSEED. Two runs of the exact same query against the exact same
corpus could therefore rank two near-tied passages differently depending
on nothing but which process happened to run it. This test drives the
real CLI as a subprocess under three different, explicit hash seeds and
asserts byte-identical output - the only way to actually exercise the
hash-randomization axis from inside a single pytest process (which has
one fixed seed for its own lifetime).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_query_output_is_identical_across_process_hash_seeds(tmp_path: Path) -> None:
    doc = tmp_path / "manual.md"
    doc.write_text(
        "# CAN Bus Wiring\n"
        "Twisted pair CAN bus wiring needs 120 ohm termination at both ends.\n"
        "# Firmware Flashing\n"
        "Flash URTC firmware over SWD or JTAG using URTC-FLASHER.\n"
        "# Retrieval\n"
        "TF-IDF retrieval ranks documents by term frequency and inverse document frequency.\n"
        "# Wiring Notes\n"
        "CAN bus retrieval firmware wiring termination frequency notes.\n",
        encoding="utf-8",
    )

    outputs = []
    for seed in ("0", "1", "1337"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "hydra_umc_docs_qa.main",
                "query",
                "CAN bus firmware retrieval wiring termination frequency",
                "--docs",
                str(doc),
            ],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        outputs.append(result.stdout)

    assert outputs[0] == outputs[1]
    assert outputs[0] == outputs[2]
