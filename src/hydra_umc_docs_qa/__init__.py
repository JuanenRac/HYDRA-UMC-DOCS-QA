# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/__init__.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""HYDRA-UMC-DOCS-QA - RAG technical assistant (Hailo-10).

Retrieval-Augmented Generation assistant grounded in the HYDRA-UMC
ecosystem's own manuals/schematics/source code, for on-site troubleshooting
and maintenance. Child of HYDRA-UMC-COGNITIVE-NODE in the Cognitive AI
Node category.
"""

# Single source of truth for the package version - mirrored into
# pyproject.toml's own `version =` field by bump_version.py on every real
# build, so main.py can print a version even if the package was never
# installed (e.g. run straight from src/).
__version__ = "0.0.6"