# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_server_identity_edges.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
#
# The signed-token check of HYDRA-UMC-SERVER, refusal by refusal (a missing, oversized, mangled, wrongly signed, expired, not-yet-valid or
# future-issued token; a missing or unknown role; an algorithm other than HS256), the decision on a request, the health probe against a
# real socket on this machine and a seeded set of damaged tokens that must never be accepted.
# =============================================================================
from __future__ import annotations

import base64
import hmac
import json
import random
import threading
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hydra_umc_docs_qa import server_identity as si

KEY = b"k" * 40
NOW = 1_800_000_000.0


def _part(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _token(claims: dict, key: bytes = KEY, header: dict | None = None) -> str:
    head = _part(json.dumps(header or {"alg": "HS256", "typ": "JWT"}).encode())
    body = _part(json.dumps(claims).encode())
    signature = hmac.new(key, f"{head}.{body}".encode("ascii"), sha256).digest()
    return f"{head}.{body}.{_part(signature)}"


def _good(**changes) -> dict:
    claims = {"username": "ana", "role": "operator", "exp": NOW + 600}
    claims.update(changes)
    return claims


def _refuses(token, fragment: str, **kwargs) -> None:
    with pytest.raises(si.IdentityError) as caught:
        si.verify_token(token, KEY, now=NOW, **kwargs)
    assert fragment in str(caught.value)


# --- verify_token -------------------------------------------------------------------------------------------------------------
def test_a_good_token_gives_the_user_and_the_role() -> None:
    assert si.verify_token(_token(_good()), KEY, now=NOW) == si.Identity("ana", "operator")


@pytest.mark.parametrize("role", ["admin", "operator", "viewer"])
def test_every_known_role_is_accepted(role) -> None:
    assert si.verify_token(_token(_good(role=role)), KEY, now=NOW).role == role


@pytest.mark.parametrize("token", [None, ""])
def test_no_token_is_refused_by_name(token) -> None:
    _refuses(token, "required")


def test_an_oversized_token_is_refused_before_any_work() -> None:
    _refuses("a." * (si.MAX_TOKEN_CHARS // 2 + 1), "malformed")


@pytest.mark.parametrize("token", ["a", "a.b", "a.b.c.d", "a..c", ".b.c", "a.b.", "..", "x" * 50])
def test_a_token_that_does_not_have_three_parts_is_malformed(token) -> None:
    _refuses(token, "malformed")


def test_a_header_that_is_not_json_or_not_an_object_is_malformed() -> None:
    body = _part(json.dumps(_good()).encode())
    for header in (_part(b"{not json"), _part(b"[]"), _part(b"\xff\xfe"), _part(b"7"), "!!!"):
        _refuses(f"{header}.{body}.{_part(b'sig')}", "malformed")


@pytest.mark.parametrize("algorithm", ["none", "HS512", "RS256", "hs256", None, 5])
def test_only_hs256_is_accepted(algorithm) -> None:
    _refuses(_token(_good(), header={"alg": algorithm}), "HS256")


def test_a_token_signed_with_another_key_or_changed_after_signing_is_refused() -> None:
    _refuses(_token(_good(), key=b"z" * 40), "signature")
    head, body, signature = _token(_good()).split(".")
    forged = _part(json.dumps(_good(role="admin", username="root")).encode())
    _refuses(f"{head}.{forged}.{signature}", "signature")


def test_a_signature_that_is_not_base64_is_refused() -> None:
    head, body, _ = _token(_good()).split(".")
    _refuses(f"{head}.{body}.@@@@", "signature")  # the decoder skips what is not base64, so it is a signature that does not match


def test_the_claims_must_be_a_json_object() -> None:
    head = _part(json.dumps({"alg": "HS256"}).encode())
    for raw in (b"[1]", b"\"text\"", b"{oops"):
        body = _part(raw)
        signature = _part(hmac.new(KEY, f"{head}.{body}".encode(), sha256).digest())
        _refuses(f"{head}.{body}.{signature}", "malformed")


def test_the_expiry_is_required_finite_and_in_the_future() -> None:
    _refuses(_token({"username": "a", "role": "viewer"}), "no expiry")
    for exp in ("soon", True, None, [], float("inf"), float("nan")):
        _refuses(_token(_good(exp=exp)), "no expiry")
    _refuses(_token(_good(exp=NOW)), "expired")  # the moment of expiry is already too late
    _refuses(_token(_good(exp=NOW - 1)), "expired")
    assert si.verify_token(_token(_good(exp=NOW + 0.5)), KEY, now=NOW).username == "ana"


def test_not_before_is_respected_and_must_be_a_finite_number() -> None:
    assert si.verify_token(_token(_good(nbf=NOW)), KEY, now=NOW).username == "ana"
    _refuses(_token(_good(nbf=NOW + 1)), "not valid yet")
    for nbf in ("now", True, float("inf"), float("nan")):
        _refuses(_token(_good(nbf=nbf)), "not valid yet")


def test_issued_in_the_future_is_tolerated_only_within_the_clock_skew() -> None:
    assert si.verify_token(_token(_good(iat=NOW + si.IAT_SKEW_SECONDS)), KEY, now=NOW).username == "ana"
    _refuses(_token(_good(iat=NOW + si.IAT_SKEW_SECONDS + 1)), "future")
    for iat in ("yesterday", True, float("nan")):
        _refuses(_token(_good(iat=iat)), "future")


def test_a_user_and_a_known_role_are_required() -> None:
    for username in (None, "", 5, ["a"]):
        _refuses(_token(_good(username=username)), "names no user")
    for role in (None, "root", "ADMIN", 1, ["admin"], ""):
        _refuses(_token(_good(role=role)), "known role")


def test_the_reason_never_holds_the_key_or_the_token() -> None:
    token = _token(_good(), key=b"z" * 40)
    with pytest.raises(si.IdentityError) as caught:
        si.verify_token(token, KEY, now=NOW)
    assert KEY.decode() not in str(caught.value)
    assert token not in str(caught.value)


# --- the decision on a request ---------------------------------------------------------------------------------------------------
class _Handler:
    def __init__(self, method: str = "GET", authorization: str | None = None) -> None:
        self.command = method
        self.headers = {"Authorization": authorization} if authorization is not None else {}


def _bearer(token: str) -> str:
    return f"Bearer {token}"


def test_without_a_key_the_service_is_open_only_when_it_has_no_token_of_its_own() -> None:
    assert si.accepts(_Handler(), "", None) is True
    assert si.accepts(_Handler(), "secret", None) is False
    assert si.accepts(_Handler(authorization=_bearer("wrong")), "secret", None) is False
    assert si.accepts(_Handler(authorization=_bearer("secret")), "secret", None) is True
    assert si.accepts(_Handler(authorization="Basic secret"), "secret", None) is False


def test_with_a_key_nothing_is_open_and_the_own_token_still_works() -> None:
    assert si.accepts(_Handler(), "", KEY) is False
    assert si.accepts(_Handler(authorization=_bearer("secret")), "secret", KEY) is True
    assert si.accepts(_Handler(authorization=_bearer("nothing")), "secret", KEY) is False


def test_a_viewer_reads_but_cannot_change_and_an_operator_can() -> None:
    viewer = si.sign_token(KEY, "view", "viewer")
    operator = si.sign_token(KEY, "ops", "operator")
    for method in ("GET", "HEAD", "OPTIONS"):
        assert si.accepts(_Handler(method, _bearer(viewer)), "", KEY) is True
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert si.accepts(_Handler(method, _bearer(viewer)), "", KEY) is False
        handler = _Handler(method, _bearer(operator))
        assert si.accepts(handler, "", KEY) is True
        assert handler.identity == si.Identity("ops", "operator")


def test_a_refused_request_leaves_no_identity_and_a_token_of_the_service_leaves_none() -> None:
    handler = _Handler("GET", _bearer("secret"))
    assert si.accepts(handler, "secret", KEY) is True
    assert handler.identity is None
    refused = _Handler("GET", _bearer(si.sign_token(b"z" * 40)))
    assert si.accepts(refused, "", KEY) is False
    assert refused.identity is None


def test_the_signed_token_of_the_probe_is_a_short_lived_viewer() -> None:
    token = si.sign_token(KEY, now=NOW)
    identity = si.verify_token(token, KEY, now=NOW)
    assert identity == si.Identity("health-probe", "viewer")
    _refuses(si.sign_token(KEY, ttl_seconds=5, now=NOW - 10), "expired")


# --- the key ----------------------------------------------------------------------------------------------------------------------------------
def test_the_key_file_rules(tmp_path: Path) -> None:
    short = tmp_path / "short.key"
    short.write_text("short", encoding="utf-8")
    with pytest.raises(ValueError, match="at least 32"):
        si.load_identity_key(str(short))
    with pytest.raises(ValueError, match="cannot read"):
        si.load_identity_key(str(tmp_path / "absent.key"))
    good = tmp_path / "good.key"
    good.write_text("  " + "k" * 40 + "\n", encoding="utf-8")
    assert si.load_identity_key(str(good)) == KEY


def test_the_error_about_a_short_key_never_prints_the_key(tmp_path: Path) -> None:
    secret = tmp_path / "s.key"
    secret.write_text("topsecret", encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        si.load_identity_key(str(secret))
    assert "topsecret" not in str(caught.value)


def test_the_key_comes_from_the_flag_then_the_environment_or_nowhere(tmp_path: Path) -> None:
    flagged, environ_file = tmp_path / "a.key", tmp_path / "b.key"
    flagged.write_text("a" * 40, encoding="utf-8")
    environ_file.write_text("b" * 40, encoding="utf-8")
    env = {si.ENV_IDENTITY_KEY_FILE: str(environ_file)}
    assert si.identity_key_from(str(flagged), env) == b"a" * 40
    assert si.identity_key_from(None, env) == b"b" * 40
    assert si.identity_key_from("  ", {}) is None
    assert si.identity_key_from(None, {si.ENV_IDENTITY_KEY_FILE: "   "}) is None


# --- the probe against a real socket on this machine ------------------------------------------------------------------------------------------
class _Service(BaseHTTPRequestHandler):
    seen: list[str | None] = []
    status = 200

    def do_GET(self):  # noqa: N802
        type(self).seen.append(self.headers.get("Authorization"))
        self.send_response(type(self).status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


@pytest.fixture
def service():
    _Service.seen = []
    _Service.status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_the_probe_without_a_key_sends_no_token_and_reports_success(service, monkeypatch) -> None:
    monkeypatch.delenv(si.ENV_IDENTITY_KEY_FILE, raising=False)
    assert si.probe(service) is True
    assert _Service.seen == [None]


def test_the_probe_with_a_key_presents_a_token_that_the_check_accepts(service, tmp_path: Path) -> None:
    key_file = tmp_path / "id.key"
    key_file.write_text("k" * 40, encoding="utf-8")
    assert si.probe(service, key_file=str(key_file)) is True
    presented = _Service.seen[0]
    assert presented is not None and presented.startswith("Bearer ")
    assert si.verify_token(presented.split(" ", 1)[1], KEY).role == "viewer"


def test_the_probe_is_false_for_a_failing_service_and_for_a_closed_port(service) -> None:
    _Service.status = 503
    assert si.probe(service) is False
    import socket

    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        closed = free.getsockname()[1]
    assert si.probe(closed, timeout=1.0) is False


def test_the_probe_command_exits_zero_one_or_two(service, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv(si.ENV_IDENTITY_KEY_FILE, raising=False)
    assert si.main(["probe", "--port", str(service)]) == 0
    _Service.status = 500
    assert si.main(["probe", "--port", str(service)]) == 1
    short = tmp_path / "short.key"
    short.write_text("short", encoding="utf-8")
    assert si.main(["probe", "--port", str(service), "--key-file", str(short)]) == 2
    assert capsys.readouterr().out.startswith("error: ")


# --- damaged tokens -----------------------------------------------------------------------------------------------------------------------------
def test_a_seeded_set_of_damaged_tokens_is_never_accepted() -> None:
    rng = random.Random(20261113)
    token = _token(_good())
    accepted = 0
    for _ in range(600):
        chars = list(token)
        for _edit in range(rng.randint(1, 3)):
            position = rng.randrange(len(chars))
            chars[position] = rng.choice("abcXYZ012-_.=!")
        damaged = "".join(chars)
        if damaged == token:
            continue
        try:
            identity = si.verify_token(damaged, KEY, now=NOW)
        except si.IdentityError:
            continue
        # a damage that only touched the unused low bits of the last base64 character decodes to the same signature
        assert identity == si.Identity("ana", "operator")
        assert damaged[:-1] == token[:-1]
        accepted += 1
    assert accepted <= 5


def test_random_text_is_never_an_identity_and_never_raises_anything_else() -> None:
    rng = random.Random(7)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.= "
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        with pytest.raises(si.IdentityError):
            si.verify_token(text, KEY, now=NOW)
