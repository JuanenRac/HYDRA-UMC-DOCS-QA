# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_access.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The bearer token of the HTTP API, tested on the functions themselves: loopback, the token sources, the bind rule and the check."""
import io
import tempfile
import unittest
from pathlib import Path

from hydra_umc_docs_qa import access


class FakeHandler:
    """Just what the access functions touch of a request handler."""

    def __init__(self, authorization=None):
        self.headers = {} if authorization is None else {"Authorization": authorization}
        self.status = None
        self.sent = []
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.status = code

    def send_header(self, name, value):
        self.sent.append((name, value))

    def end_headers(self):
        pass


class LoopbackTests(unittest.TestCase):
    def test_loopback_names_and_addresses(self):
        for addr in ("localhost", " LOCALHOST ", "127.0.0.1", "127.9.9.9", "::1", "[::1]"):
            self.assertTrue(access.is_loopback(addr), addr)

    def test_wildcards_lan_and_garbage_are_not_loopback(self):
        for addr in ("0.0.0.0", "::", "192.168.0.180", "10.0.0.1", "example.org", "", "999.1.1.1"):
            self.assertFalse(access.is_loopback(addr), addr)


class LoadTokenTests(unittest.TestCase):
    TOKEN = "0123456789abcdef"

    def test_no_source_gives_no_token(self):
        self.assertEqual(access.load_token(None, {}), "")

    def test_environment_token_is_stripped(self):
        self.assertEqual(access.load_token(None, {access.TOKEN_ENV: f"  {self.TOKEN}\n"}), self.TOKEN)

    def test_file_wins_over_the_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t"
            path.write_text(self.TOKEN + "\n", encoding="utf-8")
            self.assertEqual(access.load_token(path, {access.TOKEN_ENV: "x" * 20}), self.TOKEN)

    def test_a_missing_file_is_a_value_error_with_one_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as caught:
                access.load_token(Path(tmp) / "absent", {})
            self.assertIn("cannot read the token file", str(caught.exception))

    def test_a_file_that_is_not_utf8_is_a_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t"
            path.write_bytes(b"\xff\xfe\xfa" * 10)
            with self.assertRaises(ValueError):
                access.load_token(path, {})

    def test_a_short_token_is_refused_at_the_boundary(self):
        with self.assertRaises(ValueError):
            access.load_token(None, {access.TOKEN_ENV: "a" * (access.MIN_TOKEN_LENGTH - 1)})
        self.assertEqual(access.load_token(None, {access.TOKEN_ENV: "a" * access.MIN_TOKEN_LENGTH}), "a" * access.MIN_TOKEN_LENGTH)


class BindProblemTests(unittest.TestCase):
    def test_loopback_needs_no_token(self):
        self.assertIsNone(access.bind_problem("127.0.0.1", ""))

    def test_away_from_loopback_without_a_token_is_a_problem_that_names_the_way_out(self):
        message = access.bind_problem("0.0.0.0", "")
        self.assertIn("--token-file", message)
        self.assertIn(access.TOKEN_ENV, message)

    def test_away_from_loopback_with_a_token_is_fine(self):
        self.assertIsNone(access.bind_problem("0.0.0.0", "t" * 16))


class AuthorizedTests(unittest.TestCase):
    TOKEN = "0123456789abcdef"

    def test_no_token_configured_lets_everything_in(self):
        self.assertTrue(access.authorized(FakeHandler(), ""))

    def test_the_exact_bearer_token_is_accepted(self):
        self.assertTrue(access.authorized(FakeHandler(f"Bearer {self.TOKEN}"), self.TOKEN))

    def test_missing_wrong_or_malformed_credentials_are_refused(self):
        for header in (None, "", "Bearer", "Bearer wrong-token-value", f"Basic {self.TOKEN}", f"bearer {self.TOKEN}", self.TOKEN, f"Bearer  {self.TOKEN}x"):
            self.assertFalse(access.authorized(FakeHandler(header), self.TOKEN), header)

    def test_surrounding_spaces_of_the_presented_token_do_not_matter(self):
        self.assertTrue(access.authorized(FakeHandler(f"Bearer {self.TOKEN} "), self.TOKEN))

    def test_a_non_ascii_token_is_compared_without_raising(self):
        self.assertFalse(access.authorized(FakeHandler("Bearer ñandú-ñandú-ñandú"), self.TOKEN))


class RejectTests(unittest.TestCase):
    def test_the_401_carries_the_challenge_and_a_json_body_of_the_right_length(self):
        handler = FakeHandler()
        access.reject(handler)
        self.assertEqual(handler.status, 401)
        headers = dict(handler.sent)
        self.assertEqual(headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(int(headers["Content-Length"]), len(handler.wfile.getvalue()))


if __name__ == "__main__":
    unittest.main()
