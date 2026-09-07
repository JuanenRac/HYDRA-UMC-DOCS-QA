# =============================================================================
# HYDRA-UMC-DOCS-QA - Container Build: Dockerfile
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
# Real, minimal image for the TF-IDF retrieval HTTP API (api.py's own
# DocsQaServer, stdlib http.server - the base package has zero runtime
# dependencies). Same --addr/--port CLI the real CM5 systemd unit
# (systemd/hydra-umc-docs-qa.service) already runs, just bound to
# 0.0.0.0 instead of 127.0.0.1 here - a container's own network
# namespace already isolates it the way the systemd unit's loopback bind
# does on bare metal, and 127.0.0.1 inside a container would be
# unreachable from HYDRA-UMC-COGNITIVE-NODE's own container over the
# compose network. Non-root, matching that same unit's own
# User=hydra-umc-docs-qa. Consumed by HYDRA-UMC-COGNITIVE-NODE's own
# docker-compose.yml as the "docs-qa" service.
#
# --docs points at /app explicitly rather than relying on main.py's own
# "this repo's own README.md/CHANGELOG.md" default: that default resolves
# _REPO_ROOT from `__file__`'s own on-disk location, which after a real
# `pip install .` lands inside site-packages, not a git checkout -
# pointing at the real, copied-in docs here uses ingest.py's own
# directory-recursion support (expand_doc_paths()) to pick up
# README.md, CHANGELOG.md and docs/CLI_REFERENCE.md together.

FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md CHANGELOG.md LICENSE.md ./
COPY docs ./docs
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --system --create-home --home-dir /home/hydra hydra
USER hydra

EXPOSE 8110
ENTRYPOINT ["hydra-umc-docs-qa"]
CMD ["serve", "--addr", "0.0.0.0", "--port", "8110", "--docs", "/app"]
