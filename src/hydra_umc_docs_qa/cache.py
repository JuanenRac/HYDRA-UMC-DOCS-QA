# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/cache.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Disk-persisted TF-IDF index cache for the CLI `query` command.

api.py's own header comment already names the real gap this closes: the
CLI's `query` subcommand ingests and rebuilds the whole TfidfIndex from
scratch on every single invocation, unlike `serve`, which builds it once
at startup and reuses it. That is fine for a single ad-hoc question, but
wasteful for a caller running `query` repeatedly (a shell loop, a human
iterating on phrasing) against the same, unchanged corpus.

Real, stdlib-only persistence: `pickle`, matching this project's own
stdlib-only convention (no numpy/sqlite dependency needed for a handful
of Python dataclasses and dicts of floats). The cache key is a real
content fingerprint - the resolved path, size and mtime of every
document that will be ingested - so any real change to the corpus
(edit, add, remove, rename) is a real cache miss, never a stale hit.
"""
from __future__ import annotations

import hashlib
import os
import pickle
from pathlib import Path

from .index import TfidfIndex
from .ingest import RejectedDocument

_CACHE_FORMAT_VERSION = 1


def cache_dir() -> Path:
    """Real on-disk cache location: `HYDRA_UMC_DOCS_QA_CACHE_DIR` wins
    when set (used by this project's own tests, and by any caller that
    must not touch a real user home directory), else XDG_CACHE_HOME when
    set, else `~/.cache` - the same convention real Linux tools (and the
    real CM5 deployment target) already follow."""
    override = os.environ.get("HYDRA_UMC_DOCS_QA_CACHE_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".cache"
    return root / "hydra-umc-docs-qa"


def compute_cache_key(paths: list[Path]) -> str:
    """A real content-aware key over exactly the documents that will be
    ingested: each one's resolved path, size and mtime. Paths are sorted
    first so the key does not depend on the real filesystem-dependent
    order `--docs`/directory expansion happened to produce."""
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: str(p.resolve())):
        try:
            stat = path.stat()
        except OSError:
            digest.update(f"{path}:missing".encode("utf-8"))
            continue
        digest.update(f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
    return digest.hexdigest()


def _cache_file(key: str) -> Path:
    return cache_dir() / f"{key}.pkl"


def load_cached_index(key: str) -> tuple[TfidfIndex, list[RejectedDocument]] | None:
    """Loads a real, previously-persisted index for this exact key.
    Returns None on any miss, corruption, or foreign/stale payload - a
    bad cache file is never trusted, just treated as absent so the
    caller falls back to a real from-scratch re-index instead of
    crashing or silently serving a wrong result."""
    path = _cache_file(key)
    if not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            payload = pickle.load(f)
    except (pickle.UnpicklingError, EOFError, OSError, AttributeError, ImportError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("format_version") != _CACHE_FORMAT_VERSION:
        return None
    if payload.get("key") != key:
        return None
    index = payload.get("index")
    undecodable = payload.get("undecodable")
    if not isinstance(index, TfidfIndex) or not isinstance(undecodable, list):
        return None
    return index, undecodable


def save_cached_index(key: str, index: TfidfIndex, undecodable: list[RejectedDocument]) -> None:
    """Persists the real, just-built index to disk so the next real CLI
    invocation against the exact same documents (same key) can load it
    instead of re-ingesting and re-indexing from scratch. Best-effort:
    written to a temp file and atomically renamed into place, and any
    write failure (read-only filesystem, no permission) degrades to "no
    cache" rather than failing an otherwise-successful query."""
    directory = cache_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": _CACHE_FORMAT_VERSION,
            "key": key,
            "index": index,
            "undecodable": undecodable,
        }
        tmp_path = directory / f".{key}.pkl.tmp"
        with tmp_path.open("wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp_path.replace(_cache_file(key))
    except OSError:
        pass
