# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/cache.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Disk-persisted TF-IDF index cache for the CLI `query` command.

api.py's own header comment already names the real gap this closes: the
CLI's `query` subcommand ingests and rebuilds the whole TfidfIndex from
scratch on every single invocation, unlike `serve`, which builds it once
at startup and reuses it. That is fine for a single ad-hoc question, but
wasteful for a caller running `query` repeatedly (a shell loop, a human
iterating on phrasing) against the same, unchanged corpus.

Real, stdlib-only persistence: JSON, matching this project's own
stdlib-only convention (no numpy/sqlite dependency needed for a handful
of Python dataclasses and dicts of floats). It used to be `pickle`, but
loading a pickle executes whatever the file tells it to, and the cache sits
in the user's home directory where any other process of the same user can
write: a planted `.pkl` ran code the next time `query` was used. A JSON file
can only ever be data, and it is validated field by field before use. The
cache key is a real
content fingerprint - the resolved path, size and mtime of every
document that will be ingested - so any real change to the corpus
(edit, add, remove, rename) is a real cache miss, never a stale hit.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from . import __version__
from .index import TfidfIndex
from .ingest import DocChunk, RejectedDocument, RejectionReason

# 2: JSON instead of pickle (a version 1 file is a .pkl and is never read)
_CACHE_FORMAT_VERSION = 3


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
    # an index built by another version of the tokenizer or of the scoring is not reusable
    digest.update(f"docs-qa:{__version__}:{_CACHE_FORMAT_VERSION}:".encode("utf-8"))
    for path in sorted(paths, key=lambda p: str(p.resolve())):
        try:
            stat = path.stat()
        except OSError:
            digest.update(f"{path}:missing".encode("utf-8"))
            continue
        digest.update(f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
    return digest.hexdigest()


def _cache_file(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _weights(value: object) -> dict[str, float]:
    """A term -> weight mapping, or ValueError: every key a string and every
    value a finite number."""
    if not isinstance(value, dict):
        raise ValueError("expected an object of term weights")
    out: dict[str, float] = {}
    for term, weight in value.items():
        if not isinstance(term, str) or not _is_number(weight) or weight != weight or weight in (float("inf"), float("-inf")):
            raise ValueError("a term weight is not a finite number")
        out[term] = float(weight)
    return out


def _index_from_json(data: object) -> TfidfIndex:
    if not isinstance(data, dict):
        raise ValueError("index is not an object")
    raw_chunks = data.get("chunks")
    raw_vectors = data.get("chunk_vectors")
    if not isinstance(raw_chunks, list) or not isinstance(raw_vectors, list) or len(raw_chunks) != len(raw_vectors):
        raise ValueError("chunks and chunk_vectors do not match")
    chunks = []
    for item in raw_chunks:
        if not (isinstance(item, list) and len(item) == 5 and all(isinstance(x, str) for x in item[:3]) and isinstance(item[3], int) and not isinstance(item[3], bool) and isinstance(item[4], str)):
            raise ValueError("a chunk is not [source, heading, text, index, sha256]")
        chunks.append(DocChunk(source=item[0], heading=item[1], text=item[2], index=item[3], sha256=item[4]))
    return TfidfIndex(
        chunks=tuple(chunks),
        idf=_weights(data.get("idf")),
        chunk_vectors=tuple(_weights(v) for v in raw_vectors),
    )


def _index_to_json(index: TfidfIndex) -> dict[str, object]:
    return {
        "chunks": [[c.source, c.heading, c.text, c.index, c.sha256] for c in index.chunks],
        "idf": index.idf,
        "chunk_vectors": list(index.chunk_vectors),
    }


def _rejected_from_json(data: object) -> list[RejectedDocument]:
    if not isinstance(data, list):
        raise ValueError("undecodable is not a list")
    out = []
    for item in data:
        if not (isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item)):
            raise ValueError("a rejected document is not [path, reason]")
        out.append(RejectedDocument(path=Path(item[0]), reason=RejectionReason(item[1])))
    return out


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
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict) or payload.get("format_version") != _CACHE_FORMAT_VERSION:
            return None
        if payload.get("key") != key:
            return None
        return _index_from_json(payload.get("index")), _rejected_from_json(payload.get("undecodable"))
    except (OSError, ValueError, TypeError, RecursionError):
        # json.JSONDecodeError is a ValueError; RejectionReason(...) raises ValueError too
        return None


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
            "index": _index_to_json(index),
            "undecodable": [[str(doc.path), doc.reason.value] for doc in undecodable],
        }
        # a unique temporary name: two queries over the same corpus at once must not
        # write the same file
        file_descriptor, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{key}.", suffix=".tmp")
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_name, _cache_file(key))
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        _prune_cache(directory)
    except OSError:
        pass


MAX_CACHE_ENTRIES = 20


def _prune_cache(directory: Path) -> None:
    """Keeps the newest cache files only; every distinct set of documents makes a file."""
    try:
        entries = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in entries[MAX_CACHE_ENTRIES:]:
            stale.unlink()
    except OSError:
        pass
