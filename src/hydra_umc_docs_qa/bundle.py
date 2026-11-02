# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/bundle.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A corpus bundle: the index of one release, built once and shipped as a file.

Indexing on the small computer at every start costs time and ties the answers to whatever files happen to be on its disk. A bundle is built
where the release is built (a CI job, a build machine), carries the name of the release and the hash of every document it was made from, and
is loaded by the service as it is: the same release gives the same answers everywhere, and the citations (`source#section@sha256`) name the
exact bytes.

The file is a JSON object: `format`, `release`, the `documents` it was built from (source and SHA-256), how the index was built, the `index`
itself, a `digest` (SHA-256 of everything else, written in a canonical form) and, when a key was given at build time, a `signature`
(HMAC-SHA256 of the digest with that key). Building is deterministic: the same documents and release give the same file.

Loading checks the digest always, and the signature against the key the service was given. A bundle that is not signed, or whose signature
cannot be checked, is refused unless the service was explicitly told to accept unsigned bundles (off by default). Signing with an asymmetric
key, so that the service holds only a public key, is not implemented here: HMAC uses the standard library only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .cache import _index_from_json, _index_to_json
from .index import TfidfIndex

BUNDLE_FORMAT = "hydra-umc-docs-qa-bundle/1"
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MIN_KEY_LENGTH = 16
_RELEASE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.]+)?$")


class BundleError(ValueError):
    """The bundle cannot be built or must not be loaded."""


@dataclass(frozen=True)
class LoadedBundle:
    index: TfidfIndex
    release: str
    documents: tuple[tuple[str, str], ...]
    signed: bool


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def read_key(path: str | Path) -> bytes:
    """The signing key from a file (text, surrounding whitespace ignored). Raises BundleError when it is unreadable or too short."""
    try:
        key = Path(path).read_text(encoding="utf-8").strip().encode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise BundleError(f"cannot read the key file: {error}") from error
    if len(key) < MIN_KEY_LENGTH:
        raise BundleError(f"the key must have at least {MIN_KEY_LENGTH} characters")
    return key


def _index_payload(index: TfidfIndex) -> dict[str, object]:
    payload = _index_to_json(index)
    if index.scorer == "bm25":
        payload["term_counts"] = list(index.term_counts)
        payload["average_length"] = index.average_length
        payload["document_frequency"] = index.document_frequency
    return payload


def _index_from_payload(data: object, build: dict[str, object]) -> TfidfIndex:
    base = _index_from_json(data)
    scorer = build.get("scorer", "tfidf")
    if scorer not in ("tfidf", "bm25") or not isinstance(build.get("stem", False), bool):
        raise BundleError("the build options of the bundle are not valid")
    heading_boost = build.get("heading_boost", 1)
    if isinstance(heading_boost, bool) or not isinstance(heading_boost, int):
        raise BundleError("the build options of the bundle are not valid")
    extra: dict[str, object] = {}
    if scorer == "bm25":
        assert isinstance(data, dict)
        counts = data.get("term_counts")
        frequency = data.get("document_frequency")
        average = data.get("average_length")
        if not isinstance(counts, list) or not isinstance(frequency, dict) or not isinstance(average, (int, float)):
            raise BundleError("the BM25 data of the bundle is incomplete")
        extra = {"term_counts": tuple(counts), "average_length": float(average), "document_frequency": dict(frequency)}
    return TfidfIndex(
        chunks=base.chunks, idf=base.idf, chunk_vectors=base.chunk_vectors,
        scorer=str(scorer), stem=bool(build.get("stem", False)), heading_boost=heading_boost, **extra,  # type: ignore[arg-type]
    )


def build_bundle(index: TfidfIndex, release: str, out: Path, *, key: bytes | None = None) -> dict[str, object]:
    """Writes the bundle of `index` for `release` to `out`; returns the header (release, documents, digest, signed)."""
    if not _RELEASE.fullmatch(release):
        raise BundleError(f"the release {release!r} is not a version such as 0.3.0")
    if not index.chunks:
        raise BundleError("an empty index is not a corpus")
    documents = sorted({(c.source, c.sha256) for c in index.chunks})
    body = {
        "format": BUNDLE_FORMAT,
        "release": release,
        "documents": [{"source": s, "sha256": h} for s, h in documents],
        "build": {"scorer": index.scorer, "stem": index.stem, "heading_boost": index.heading_boost},
        "index": _index_payload(index),
    }
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    signature = hmac.new(key, digest.encode("ascii"), hashlib.sha256).hexdigest() if key else None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_canonical({**body, "digest": digest, "signature": signature}))
    return {"release": release, "documents": len(documents), "chunks": len(index.chunks), "digest": digest, "signed": signature is not None}


def load_bundle(path: str | Path, *, key: bytes | None = None, allow_unsigned: bool = False) -> LoadedBundle:
    """Reads and checks a bundle. The digest must match; the signature must match `key`, unless `allow_unsigned` says the service accepts
    a bundle without a checked signature (an unsigned one, or one when no key was given)."""
    target = Path(path)
    try:
        if target.stat().st_size > MAX_BUNDLE_BYTES:
            raise BundleError("the bundle is too large")
        data = json.loads(target.read_bytes().decode("utf-8"))
    except OSError as error:
        raise BundleError(f"cannot read the bundle: {error}") from error
    except (UnicodeDecodeError, ValueError) as error:
        raise BundleError(f"the bundle is not valid JSON: {error}") from error
    if not isinstance(data, dict) or data.get("format") != BUNDLE_FORMAT:
        raise BundleError("this is not a corpus bundle of a format this service reads")
    digest, signature = data.get("digest"), data.get("signature")
    body = {k: v for k, v in data.items() if k not in ("digest", "signature")}
    if not isinstance(digest, str) or not hmac.compare_digest(hashlib.sha256(_canonical(body)).hexdigest(), digest):
        raise BundleError("the digest of the bundle does not match its content: it was changed after it was built")
    checked = False
    if key is not None and isinstance(signature, str):
        expected = hmac.new(key, digest.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise BundleError("the signature of the bundle does not match the key")
        checked = True
    elif not allow_unsigned:
        raise BundleError(
            "the signature of the bundle could not be checked (it is unsigned or no key was given); "
            "give the key file, or accept unsigned bundles explicitly"
        )
    release = body.get("release")
    documents = body.get("documents")
    build = body.get("build")
    if not isinstance(release, str) or not _RELEASE.fullmatch(release) or not isinstance(documents, list) or not isinstance(build, dict):
        raise BundleError("the header of the bundle is malformed")
    try:
        index = _index_from_payload(body.get("index"), build)
        listed = tuple((str(d["source"]), str(d["sha256"])) for d in documents)
    except (KeyError, TypeError, ValueError) as error:
        raise BundleError(f"the index of the bundle is malformed: {error}") from error
    return LoadedBundle(index=index, release=release, documents=listed, signed=checked)
