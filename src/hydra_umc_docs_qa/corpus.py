# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/corpus.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The corpus manifest: which documents the assistant is allowed to answer from.

`--docs` takes any path the caller types, which is fine for a person trying a question and not for a service that answers a technician on a
machine: whatever sits in a directory becomes something the assistant may quote. A manifest says it explicitly:

    {
      "roots":   ["/srv/hydra/HYDRA-UMC-STUDIO", "/srv/hydra/HYDRA-UMC-DOCS/docs"],
      "exclude": ["**/node_modules/**", "**/drafts/**"],
      "documents": [{"path": "/srv/hydra/HYDRA-UMC-STUDIO/README.md", "sha256": "<hex>"}]
    }

* `roots` are the only places documents are read from (directories; a link to a directory is not followed). A pinned document outside every root is refused.
* `exclude` are `fnmatch` patterns of the path relative to its root, with `/` as separator; a match is left out.
* `documents` pin files by SHA-256: a pinned file whose bytes differ is not ingested (it was edited since somebody approved it), and a pinned file that is
  missing is reported. Files under the roots that are not pinned are ingested while the manifest pins nothing; with at least one pin, `strict` (default
  true) ingests ONLY the pinned documents, so a new file dropped into a root does not become quotable until somebody pins it.

Every ingested section carries the hash of its document, so an answer can cite `source#section@sha256` and anyone can check the citation.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .ingest import ALLOWED_SUFFIXES, RejectedDocument, RejectionReason, _walk_markdown_files

MAX_MANIFEST_BYTES = 256 * 1024


class CorpusError(ValueError):
    """The manifest cannot be used."""


@dataclass(frozen=True)
class CorpusManifest:
    roots: tuple[Path, ...]
    exclude: tuple[str, ...]
    pins: dict[Path, str]
    strict: bool


def _strings(raw: object, label: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) and x for x in raw):
        raise CorpusError(f"{label} must be a list of non-empty strings")
    return list(raw)


def parse_manifest(raw: object) -> CorpusManifest:
    if not isinstance(raw, dict):
        raise CorpusError("the corpus manifest must be a JSON object")
    roots = [Path(r) for r in _strings(raw.get("roots"), "roots")]
    if not roots:
        raise CorpusError("roots must name at least one directory: a corpus with no root has nothing it may quote")
    pins: dict[Path, str] = {}
    documents = raw.get("documents", [])
    if not isinstance(documents, list):
        raise CorpusError("documents must be a list")
    for entry in documents:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("sha256"), str):
            raise CorpusError("each document needs a path and a sha256")
        digest = entry["sha256"].lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise CorpusError(f"the sha256 of {entry['path']!r} is not 64 hexadecimal characters")
        pins[Path(entry["path"])] = digest
    strict = raw.get("strict", True)
    if not isinstance(strict, bool):
        raise CorpusError("strict must be true or false")
    return CorpusManifest(tuple(roots), tuple(_strings(raw.get("exclude"), "exclude")), pins, strict)


def load_manifest(path: str | Path) -> CorpusManifest:
    target = Path(path)
    try:
        if target.stat().st_size > MAX_MANIFEST_BYTES:
            raise CorpusError(f"the corpus manifest is larger than {MAX_MANIFEST_BYTES} bytes")
        raw = json.loads(target.read_text(encoding="utf-8"), parse_constant=lambda name: (_ for _ in ()).throw(CorpusError(f"{name} is not valid JSON")))
    except OSError as error:
        raise CorpusError(f"cannot read the corpus manifest: {error}") from error
    except json.JSONDecodeError as error:
        raise CorpusError(f"the corpus manifest is not JSON: {error}") from error
    return parse_manifest(raw)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _excluded(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch("/" + relative, pattern) for pattern in patterns)


def collect(manifest: CorpusManifest) -> tuple[list[Path], list[RejectedDocument]]:
    """The documents the manifest allows and the ones it refused (with the reason). The result is sorted, so two runs agree."""
    allowed: dict[Path, None] = {}
    rejected: list[RejectedDocument] = []
    roots = []
    for root in manifest.roots:
        if root.is_symlink() or not root.is_dir():
            rejected.append(RejectedDocument(path=root, reason=RejectionReason.MISSING))
            continue
        roots.append(root)
    for root in roots:
        for path in _walk_markdown_files(root):
            if _excluded(path, root, manifest.exclude):
                continue
            allowed[path] = None
    candidates = list(allowed)
    for pinned in manifest.pins:
        if not any(_inside(pinned, root) for root in roots):
            rejected.append(RejectedDocument(path=pinned, reason=RejectionReason.OUTSIDE_ROOTS))
            continue
        if pinned.is_symlink() or not pinned.is_file():
            rejected.append(RejectedDocument(path=pinned, reason=RejectionReason.MISSING))
            continue
        if pinned.suffix.lower() not in ALLOWED_SUFFIXES:
            rejected.append(RejectedDocument(path=pinned, reason=RejectionReason.DISALLOWED_EXTENSION))
            continue
        if pinned.resolve() not in {p.resolve() for p in candidates}:
            # a pinned file that an exclusion pattern removed stays out: the exclusion wins over the pin
            continue
    pinned_by_resolved = {p.resolve(): digest for p, digest in manifest.pins.items()}
    result: list[Path] = []
    for path in sorted(candidates):
        digest = pinned_by_resolved.get(path.resolve())
        if digest is None:
            if manifest.pins and manifest.strict:
                continue  # not pinned: a new file in a root is not quotable until somebody pins it
            result.append(path)
            continue
        if sha256_of(path) != digest:
            rejected.append(RejectedDocument(path=path, reason=RejectionReason.HASH_MISMATCH))
            continue
        result.append(path)
    return result, rejected
