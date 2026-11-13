# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/corpus_signature.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The detached release signature of a corpus bundle, checked with a public key only.

A bundle built in CI is not signed there: the signing key never leaves the machine that signs the releases. The release step writes two files
next to the bundle in the format every signed release asset of the family uses:

    FILE.sha256   `<sha256 hex>  <file name>` (the `sha256sum` format)
    FILE.sig      the Ed25519 signature of the exact bytes of FILE, in base64

The service holds the PUBLIC key only, so whoever can verify a corpus cannot make one. Verification is the reference algorithm of RFC 8032 written
with Python integers (the project has no dependencies); it handles public data only, so being slower than a C library and not constant-time does
not matter. A public key is given as 64 hexadecimal characters (optionally `ed25519-public:` in front) or as a PEM (PKIX) file.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from pathlib import Path

MAX_SIGNED_BYTES = 256 * 1024 * 1024
_PREFIX = "ed25519-public:"
_HEX32 = re.compile(r"^[0-9a-fA-F]{64}$")
_SPKI_ED25519 = bytes.fromhex("302a300506032b6570032100")

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)

Point = tuple[int, int, int, int]


class SignatureError(ValueError):
    """A signature, a digest file or a key that cannot be used, or a file that does not match them."""


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0) or 0
_G: Point = (_GX, _GY, 1, _GX * _GY % _P)


def _add(a: Point, b: Point) -> Point:
    a1 = (a[1] - a[0]) * (b[1] - b[0]) % _P
    b1 = (a[1] + a[0]) * (b[1] + b[0]) % _P
    c = 2 * a[3] * b[3] * _D % _P
    d = 2 * a[2] * b[2] % _P
    e, f, g, h = b1 - a1, d - c, d + c, b1 + a1
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(scalar: int, point: Point) -> Point:
    result: Point = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(a: Point, b: Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _decompress(data: bytes) -> Point | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """RFC 8032 verification: True only for a valid signature of `message` under the 32-byte public key."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    k = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _L
    return _equal(_mul(s, _G), _add(r, _mul(k, a)))


def read_public_key(source: str | Path) -> bytes:
    """A 32-byte public key from 64 hex characters, `ed25519-public:<hex>`, or the path of a file holding either of those or a PKIX PEM."""
    text = str(source).strip()
    if text.startswith(_PREFIX):
        text = text[len(_PREFIX):]
    if _HEX32.match(text):
        return bytes.fromhex(text)
    path = Path(str(source))
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise SignatureError(f"the public key is neither 64 hexadecimal characters nor a readable file: {error}") from error
    if content.startswith(_PREFIX):
        content = content[len(_PREFIX):]
    if _HEX32.match(content):
        return bytes.fromhex(content)
    if "BEGIN PUBLIC KEY" in content:
        body = "".join(line for line in content.splitlines() if "-----" not in line)
        try:
            der = base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError) as error:
            raise SignatureError("the PEM public key is not valid base64") from error
        if len(der) == 44 and der.startswith(_SPKI_ED25519):
            return der[12:]
    raise SignatureError("the public key must be 64 hexadecimal characters or an Ed25519 PKIX PEM")


def sidecar_paths(bundle: Path) -> tuple[Path, Path]:
    return bundle.with_name(bundle.name + ".sha256"), bundle.with_name(bundle.name + ".sig")


def verify_detached(bundle: str | Path, public: bytes, contents: bytes | None = None) -> str:
    """Checks FILE.sha256 and FILE.sig against the bytes of the bundle; returns the SHA-256 (hex) of the file. Raises SignatureError otherwise.

    A caller that parses the file passes the `contents` it read, so what was verified is exactly what it loads."""
    target = Path(bundle)
    digest_file, signature_file = sidecar_paths(target)
    if contents is None:
        try:
            if target.stat().st_size > MAX_SIGNED_BYTES:
                raise SignatureError("the bundle is too large")
            contents = target.read_bytes()
        except OSError as error:
            raise SignatureError(f"cannot read the bundle: {error}") from error
    try:
        recorded = digest_file.read_text(encoding="ascii").split()
    except (OSError, UnicodeDecodeError) as error:
        raise SignatureError(f"the digest file {digest_file.name} is missing or unreadable") from error
    actual = hashlib.sha256(contents).hexdigest()
    if not recorded or not hmac.compare_digest(recorded[0].lower(), actual):
        raise SignatureError("the bundle does not match its .sha256: it was changed after it was signed")
    try:
        encoded = signature_file.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise SignatureError(f"the signature file {signature_file.name} is missing or unreadable: the corpus is not signed") from error
    try:
        signature = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise SignatureError("the signature file is not base64") from error
    if len(signature) != 64:
        raise SignatureError("the signature file does not hold an Ed25519 signature")
    if not ed25519_verify(public, contents, signature):
        raise SignatureError("the signature of the bundle does not verify against the trusted key")
    return actual
