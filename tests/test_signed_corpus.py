# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_signed_corpus.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The corpus bundle with a detached release signature: valid, altered, missing, wrong key, and the same bytes for the same input.

evidence=simulator: the signatures are made here with a throw-away key and the reference algorithm of RFC 8032; no release key is involved.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hydra_umc_docs_qa import corpus_signature as cs
from hydra_umc_docs_qa.api import build_server_index
from hydra_umc_docs_qa.bundle import BundleError, build_bundle, load_bundle
from hydra_umc_docs_qa.main import main

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _public(seed: bytes) -> bytes:
    scalar, _prefix = _expand(seed)
    return _compress(cs._mul(scalar, cs._G))


def _expand(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def _compress(point: tuple[int, int, int, int]) -> bytes:
    zinv = cs._inv(point[2])
    x, y = point[0] * zinv % cs._P, point[1] * zinv % cs._P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _sign(seed: bytes, message: bytes) -> bytes:
    scalar, prefix = _expand(seed)
    public = _compress(cs._mul(scalar, cs._G))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % cs._L
    big_r = _compress(cs._mul(r, cs._G))
    k = int.from_bytes(hashlib.sha512(big_r + public + message).digest(), "little") % cs._L
    return big_r + int.to_bytes((r + k * scalar) % cs._L, 32, "little")


def _write_signed(bundle: Path, seed: bytes) -> None:
    contents = bundle.read_bytes()
    bundle.with_name(bundle.name + ".sha256").write_text(f"{hashlib.sha256(contents).hexdigest()}  {bundle.name}\n", encoding="ascii")
    bundle.with_name(bundle.name + ".sig").write_text(base64.b64encode(_sign(seed, contents)).decode("ascii") + "\n", encoding="ascii")


SEED = bytes(range(1, 33))
OTHER_SEED = bytes(range(33, 65))


def _make_bundle(tmp_path: Path, name: str = "corpus.json") -> Path:
    docs = tmp_path / "docs-src"
    docs.mkdir(exist_ok=True)
    (docs / "guide.md").write_text("# Guide\n\nThe spindle runs at twenty four thousand revolutions.\n\n## Safety\n\nStop the spindle before opening the door.\n", encoding="utf-8")
    index, _sources, _rejected = build_server_index([docs], scorer="tfidf", stem=False, heading_boost=1)
    assert index is not None
    out = tmp_path / name
    build_bundle(index, "0.3.0", out)
    return out


def test_rfc8032_vector_one_verifies_and_a_changed_message_does_not() -> None:
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert cs.ed25519_verify(public, b"", signature)
    assert not cs.ed25519_verify(public, b"x", signature)
    assert not cs.ed25519_verify(public, b"", signature[:-1] + b"\x00")
    assert not cs.ed25519_verify(public[:-1], b"", signature)


def test_a_valid_signature_loads_and_is_reported_signed(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    _write_signed(bundle, SEED)
    loaded = load_bundle(bundle, public_key=_public(SEED))
    assert loaded.signed is True and loaded.release == "0.3.0"
    assert main(["verify-bundle", str(bundle), "--public-key", _public(SEED).hex()]) == 0


def test_an_altered_bundle_is_refused(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    _write_signed(bundle, SEED)
    data = bundle.read_bytes()
    bundle.write_bytes(data.replace(b"spindle", b"spindlf", 1))
    with pytest.raises(BundleError, match="does not match its .sha256"):
        load_bundle(bundle, public_key=_public(SEED))
    # even if the attacker also rewrites the .sha256, the signature no longer fits the bytes
    bundle.with_name(bundle.name + ".sha256").write_text(f"{hashlib.sha256(bundle.read_bytes()).hexdigest()}  x\n", encoding="ascii")
    with pytest.raises(BundleError, match="does not verify"):
        load_bundle(bundle, public_key=_public(SEED), allow_unsigned=True)


def test_an_unsigned_bundle_is_refused_even_when_unsigned_ones_are_allowed(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    with pytest.raises(BundleError, match="digest file"):
        load_bundle(bundle, public_key=_public(SEED), allow_unsigned=True)
    bundle.with_name(bundle.name + ".sha256").write_text(f"{hashlib.sha256(bundle.read_bytes()).hexdigest()}  x\n", encoding="ascii")
    with pytest.raises(BundleError, match="not signed"):
        load_bundle(bundle, public_key=_public(SEED))


def test_a_signature_of_another_key_is_refused(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    _write_signed(bundle, OTHER_SEED)
    with pytest.raises(BundleError, match="does not verify against the trusted key"):
        load_bundle(bundle, public_key=_public(SEED))
    assert main(["verify-bundle", str(bundle), "--public-key", _public(SEED).hex()]) == 1


def test_a_malformed_signature_file_is_refused(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    _write_signed(bundle, SEED)
    sig = bundle.with_name(bundle.name + ".sig")
    for bad in ("not base64 !!", base64.b64encode(b"short").decode()):
        sig.write_text(bad, encoding="ascii")
        with pytest.raises(BundleError):
            load_bundle(bundle, public_key=_public(SEED))


def test_the_public_key_comes_as_hex_a_prefixed_value_a_file_or_a_pem(tmp_path: Path) -> None:
    public = _public(SEED)
    assert cs.read_public_key(public.hex()) == public
    assert cs.read_public_key("ed25519-public:" + public.hex()) == public
    key_file = tmp_path / "pub.txt"
    key_file.write_text(public.hex() + "\n", encoding="utf-8")
    assert cs.read_public_key(key_file) == public
    pem = tmp_path / "pub.pem"
    der = bytes.fromhex("302a300506032b6570032100") + public
    pem.write_text("-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PUBLIC KEY-----\n", encoding="ascii")
    assert cs.read_public_key(pem) == public
    with pytest.raises(cs.SignatureError):
        cs.read_public_key(tmp_path / "missing")
    with pytest.raises(cs.SignatureError):
        cs.read_public_key("zz")


def test_the_same_documents_and_release_give_the_same_bytes_in_any_place_and_hash_seed(tmp_path: Path) -> None:
    def build(where: Path, seed: str) -> bytes:
        project = where / "HYDRA-UMC-SAMPLE"
        (project / "docs").mkdir(parents=True)
        (project / "hydra-umc.project.json").write_text("{}", encoding="utf-8")
        (project / "README.md").write_text("# Sample\n\nThe bridge speaks over the serial line.\n", encoding="utf-8")
        (project / "docs" / "b.md").write_text("# B\n\nSecond document about the broker.\n", encoding="utf-8")
        (project / "docs" / "a.md").write_text("# A\n\nFirst document about the spindle.\n", encoding="utf-8")
        out = where / "out.json"
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(_REPO_ROOT / "src")}
        done = subprocess.run(
            [sys.executable, "-m", "hydra_umc_docs_qa.main", "bundle", "--release", "0.3.0", "--out", str(out), "--docs", str(project)],
            capture_output=True, text=True, env=env, check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        return out.read_bytes()

    first = build(tmp_path / "one", "1")
    second = build(tmp_path / "two" / "deeper", "2")
    (tmp_path / "one").mkdir(exist_ok=True)
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def _serve_args(bundle: Path, *extra: str) -> list[str]:
    return ["serve", "--bundle", str(bundle), *extra]


def test_serve_refuses_a_bundle_whose_signature_fails_without_a_fallback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = _make_bundle(tmp_path)
    _write_signed(bundle, OTHER_SEED)
    code = main(_serve_args(bundle, "--bundle-public-key", _public(SEED).hex(), "--port", "0"))
    assert code == 2
    assert "does not verify" in capsys.readouterr().err


def test_serve_falls_back_to_local_documents_with_a_warning_only_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _make_bundle(tmp_path)
    _write_signed(bundle, OTHER_SEED)
    docs = tmp_path / "docs-src"
    started: list[object] = []

    class _Stop(Exception):
        pass

    def fake_server(*args: object, **kwargs: object) -> object:
        started.append(args[1])
        raise _Stop

    monkeypatch.setattr("hydra_umc_docs_qa.main.DocsQaServer", fake_server)
    with pytest.raises(_Stop):
        main(_serve_args(bundle, "--bundle-public-key", _public(SEED).hex(), "--bundle-fallback-local", "--docs", str(docs), "--port", "0"))
    out = capsys.readouterr().out
    assert "WARNING: the corpus bundle was refused" in out and "NOT from the signed corpus" in out
    assert started and started[0] is not None


def test_the_switch_that_allows_unsigned_bundles_is_read_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = _make_bundle(tmp_path)
    monkeypatch.delenv("HYDRA_UMC_DOCS_QA_REQUIRE_SIGNED_CORPUS", raising=False)
    assert main(_serve_args(bundle, "--port", "0")) == 2  # default: only a signed corpus
    assert "could not be checked" in capsys.readouterr().err
    assert json.loads(bundle.read_text(encoding="utf-8"))["format"]
