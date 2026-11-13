# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_api_failure_modes.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
#
# A real DocsQaServer on a loopback port, put through what goes wrong: bad parameters, a service with nothing indexed, reloads that fail or find
# nothing (the old index stays), a reload while other threads keep asking, the token and the identity gates, and what /stats must not reveal.
# Nothing here leaves this machine.
# =============================================================================
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from hydra_umc_docs_qa import server_identity as si
from hydra_umc_docs_qa.api import DocsQaServer, build_server_index

KEY = b"k" * 40


def _manual(tmp_path: Path, name: str = "manual.md", extra: str = "") -> Path:
    doc = tmp_path / name
    doc.write_text(
        "# CAN Bus Wiring\nTwisted pair CAN bus wiring needs 120 ohm termination at both ends.\n"
        "# Firmware Flashing\nFlash URTC firmware over SWD or JTAG using URTC-FLASHER.\n" + extra,
        encoding="utf-8",
    )
    return doc


def _call(url: str, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw) if raw else {}
        except ValueError:
            return error.code, {}


@contextmanager
def serving(docs: list[Path] | None = None, *, index=..., loader: Callable | None = None, **options) -> Iterator[tuple[str, DocsQaServer]]:
    if index is ...:
        built, sources, rejected = build_server_index(docs or [])
    else:
        built, sources, rejected = index, [], []
    server = DocsQaServer(("127.0.0.1", 0), built, sources, rejected, loader=loader, **options)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _q(base: str, headers: dict[str, str] | None = None, **params) -> tuple[int, dict]:
    return _call(f"{base}/query?{urllib.parse.urlencode(params)}", headers=headers)


AUTH = {"Authorization": "Bearer t"}


# --- parameters ---------------------------------------------------------------------------------------------------------------------
def test_a_question_is_needed_and_top_k_must_be_a_small_integer(tmp_path: Path) -> None:
    with serving([_manual(tmp_path)]) as (base, _):
        assert _call(f"{base}/query")[0] == 400
        assert _q(base, q="can", top_k="many")[0] == 400
        assert _q(base, q="can", top_k="1.5")[0] == 400
        status, body = _q(base, q="can", top_k="51")
        assert status == 400 and "at most 50" in body["error"]
        assert _q(base, q="can", top_k="50")[0] == 200
        assert _q(base, q="can", top_k="1")[0] == 200


def test_a_request_can_ask_for_a_stricter_answer_but_never_a_looser_one(tmp_path: Path) -> None:
    question = "CAN bus termination zeppelin"  # one word the manual never uses: three quarters of it is covered
    with serving([_manual(tmp_path)]) as (base, _):
        assert _q(base, q=question, min_coverage="1.5")[0] == 400
        assert _q(base, q=question, min_coverage="abc")[0] == 400
        assert _q(base, q=question, min_score="abc")[0] == 400
        plain = _q(base, q=question)[1]
        assert plain["answerable"] is True
        assert _q(base, q=question, min_score="-5")[1] == plain  # a negative bar is the service's bar
        stricter = _q(base, q=question, min_coverage="1")[1]
        assert stricter["answerable"] is False and stricter["results"] == []
    with serving([_manual(tmp_path)], min_coverage=1.0) as (base, _):
        # the service demands full coverage: asking for less does not lower the bar
        assert _q(base, q=question, min_coverage="0")[1] == _q(base, q=question)[1]
        assert _q(base, q=question, min_coverage="0")[1]["answerable"] is False


def test_a_question_nothing_answers_is_refused_with_no_sections(tmp_path: Path) -> None:
    with serving([_manual(tmp_path)], min_score=5.0) as (base, _):
        status, body = _q(base, q="termination")
        assert status == 200
        assert body["answerable"] is False
        assert body["results"] == []
        assert body["reason"]


def test_the_extractive_answer_is_a_sentence_of_the_passage(tmp_path: Path) -> None:
    with serving([_manual(tmp_path)]) as (base, _):
        status, body = _q(base, q="termination of the CAN bus", extractive="1")
        assert status == 200
        for item in body["results"]:
            assert item["answer"] in item["chunk"]["text"] or item["answer"] in item["chunk"]["text"].replace("\n", " ")
        status, body = _q(base, q="termination of the CAN bus", extractive="0")
        assert all("answer" not in item for item in body["results"])


def test_a_query_with_the_same_key_twice_uses_the_first_value(tmp_path: Path) -> None:
    with serving([_manual(tmp_path)]) as (base, _):
        status, body = _call(f"{base}/query?q=can+bus&q=firmware")
        assert status == 200
        assert body["question"] == "can bus"


def test_a_very_long_and_an_odd_question_never_breaks_the_service(tmp_path: Path) -> None:
    with serving([_manual(tmp_path)]) as (base, _):
        for question in ("x" * 4000, "%00", "日本語 ✓ ünïcödé", "   ", "((((", "a" * 10 + "\u0000"):
            status, _body = _q(base, q=question)
            assert status in (200, 400), question
        assert _q(base, q="can bus")[0] == 200


# --- routes and methods ---------------------------------------------------------------------------------------------------------------
def test_unknown_paths_and_methods_are_a_clean_404(tmp_path: Path) -> None:
    with serving([_manual(tmp_path)]) as (base, _):
        assert _call(f"{base}/nothing")[0] == 404
        assert _call(f"{base}/query/extra?q=can")[0] == 404
        assert _call(f"{base}/nothing", method="POST", body=b"{}")[0] == 404
        status, _ = _call(f"{base}/query?q=can", method="DELETE")
        assert status in (404, 405, 501)
        assert _q(base, q="can")[0] == 200


def test_error_lookup_needs_a_code_and_reports_an_unknown_code_as_not_found(tmp_path: Path) -> None:
    with serving([_manual(tmp_path)]) as (base, _):
        assert _call(f"{base}/error")[0] == 400
        assert _call(f"{base}/error?code=")[0] == 400
        status, body = _call(f"{base}/error?code=E9999")
        assert (status, body["found"], body["matches"]) == (200, False, [])


# --- nothing indexed -----------------------------------------------------------------------------------------------------------------------
def test_a_service_with_nothing_indexed_answers_503_but_still_reports_its_state() -> None:
    with serving(index=None) as (base, _):
        assert _q(base, q="can")[0] == 503
        assert _call(f"{base}/error?code=E1")[0] == 503
        status, body = _call(f"{base}/stats")
        assert status == 200
        assert (body["indexed"], body["chunks"], body["scorer"]) == (False, 0, None)
        assert body["reloadable"] is False


def test_the_stats_show_file_names_and_never_the_folders_of_the_server(tmp_path: Path) -> None:
    good = _manual(tmp_path)
    with serving([good, tmp_path / "missing" / "gone.md"]) as (base, server):
        server.rejected_descriptions = [str(tmp_path / "deep" / "dir" / "bad.md") + ": unreadable", "C:\\secret\\folder\\worse.md: empty"]
        status, body = _call(f"{base}/stats")
        assert status == 200
        text = json.dumps(body)
        assert str(tmp_path) not in text.replace("\\\\", "\\")
        assert "secret" not in text.replace("worse.md", "")
        assert any(item.startswith("bad.md") for item in body["rejectedDocuments"])
        assert any(item.startswith("worse.md") for item in body["rejectedDocuments"])


# --- reload ----------------------------------------------------------------------------------------------------------------------------------------
def test_reload_is_refused_without_a_token_and_with_a_wrong_one(tmp_path: Path) -> None:
    doc = _manual(tmp_path)
    loader = lambda: build_server_index([doc])  # noqa: E731
    with serving([doc], loader=loader) as (base, _):
        status, body = _call(f"{base}/reload", method="POST", body=b"")
        assert status == 403 and "token" in body["error"]
    with serving([doc], loader=loader, token="s3cret") as (base, server):
        assert _call(f"{base}/reload", method="POST", body=b"")[0] == 401
        assert _call(f"{base}/reload", method="POST", headers={"Authorization": "Bearer nope"}, body=b"")[0] == 401
        assert _call(f"{base}/reload", method="POST", headers={"Authorization": "Basic s3cret"}, body=b"")[0] == 401
        assert server.reloads == 0
        status, body = _call(f"{base}/reload", method="POST", headers={"Authorization": "Bearer s3cret"}, body=b"")
        assert (status, body["reloaded"], server.reloads) == (200, True, 1)


def test_reload_of_a_service_started_without_a_document_list_is_a_conflict(tmp_path: Path) -> None:
    with serving([_manual(tmp_path)], token="s3cret") as (base, _):
        status, body = _call(f"{base}/reload", method="POST", headers={"Authorization": "Bearer s3cret"}, body=b"")
        assert status == 409
        assert "read again" in body["error"]


def test_a_reload_that_raises_keeps_the_old_index_and_says_why(tmp_path: Path) -> None:
    doc = _manual(tmp_path)
    state = {"fail": False}

    def loader():
        if state["fail"]:
            raise RuntimeError("the disk is gone")
        return build_server_index([doc])

    with serving([doc], loader=loader, token="t") as (base, server):
        before = server.index
        state["fail"] = True
        status, body = _call(f"{base}/reload", method="POST", headers={"Authorization": "Bearer t"}, body=b"")
        assert status == 500
        assert body["reloaded"] is False and "the disk is gone" in body["error"]
        assert server.index is before
        assert _q(base, AUTH, q="can bus")[0] == 200
        assert _call(f"{base}/stats")[1]["lastReloadError"].startswith("RuntimeError")
        state["fail"] = False
        assert _call(f"{base}/reload", method="POST", headers={"Authorization": "Bearer t"}, body=b"")[0] == 200
        assert _call(f"{base}/stats")[1]["lastReloadError"] is None


def test_a_reload_that_finds_nothing_keeps_the_old_index(tmp_path: Path) -> None:
    doc = _manual(tmp_path)
    state = {"empty": False}

    def loader():
        return (None, [], ["manual.md: empty"]) if state["empty"] else build_server_index([doc])

    with serving([doc], loader=loader, token="t") as (base, server):
        chunks = len(server.index.chunks)
        state["empty"] = True
        status, body = _call(f"{base}/reload", method="POST", headers={"Authorization": "Bearer t"}, body=b"")
        assert status == 500
        assert "previous index is kept" in body["error"]
        assert body["chunks"] == chunks
        assert _q(base, AUTH, q="can bus")[0] == 200


def test_a_reload_picks_up_a_changed_document(tmp_path: Path) -> None:
    doc = _manual(tmp_path)
    with serving([doc], loader=lambda: build_server_index([doc]), token="t") as (base, server):
        assert not any("quartz" in r["chunk"]["text"] for r in _q(base, AUTH, q="quartz oscillator")[1]["results"])
        _manual(tmp_path, extra="# Clocks\nThe quartz oscillator runs at 25 MHz.\n")
        assert _call(f"{base}/reload", method="POST", headers={"Authorization": "Bearer t"}, body=b"")[0] == 200
        status, body = _q(base, AUTH, q="quartz oscillator frequency")
        assert status == 200
        assert any("quartz" in r["chunk"]["text"] for r in body["results"])
        assert server.reloads == 1


def test_queries_keep_being_answered_while_reloads_happen(tmp_path: Path) -> None:
    doc = _manual(tmp_path)
    with serving([doc], loader=lambda: build_server_index([doc]), token="t") as (base, server):
        failures: list[str] = []
        stop = threading.Event()

        def ask() -> None:
            while not stop.is_set():
                status, body = _q(base, AUTH, q="can bus termination")
                if status != 200 or "results" not in body:
                    failures.append(f"{status} {body}")

        workers = [threading.Thread(target=ask, daemon=True) for _ in range(3)]
        for worker in workers:
            worker.start()
        for _ in range(4):
            assert _call(f"{base}/reload", method="POST", headers={"Authorization": "Bearer t"}, body=b"")[0] == 200
        stop.set()
        for worker in workers:
            worker.join(timeout=5)
        assert failures == []
        assert server.reloads == 4


# --- the gates ----------------------------------------------------------------------------------------------------------------------------------
def test_with_a_token_every_data_route_needs_it_and_stats_stays_open(tmp_path: Path) -> None:
    with serving([_manual(tmp_path)], token="s3cret") as (base, _):
        assert _q(base, q="can")[0] == 401
        assert _call(f"{base}/error?code=E1")[0] == 401
        assert _call(f"{base}/stats")[0] == 200
        assert _call(f"{base}/query?q=can", headers={"Authorization": "Bearer s3cret"})[0] == 200


def test_with_the_identity_of_the_server_nothing_is_open_and_a_viewer_only_reads(tmp_path: Path) -> None:
    doc = _manual(tmp_path)
    viewer = {"Authorization": f"Bearer {si.sign_token(KEY, 'v', 'viewer')}"}
    operator = {"Authorization": f"Bearer {si.sign_token(KEY, 'o', 'operator')}"}
    with serving([doc], loader=lambda: build_server_index([doc])) as (base, server):
        server.identity_key = KEY
        assert _call(f"{base}/stats")[0] == 401
        assert _q(base, q="can")[0] == 401
        assert _call(f"{base}/stats", headers=viewer)[0] == 200
        assert _call(f"{base}/query?q=can", headers=viewer)[0] == 200
        assert _call(f"{base}/reload", method="POST", headers=viewer, body=b"")[0] in (401, 403)
        assert _call(f"{base}/reload", method="POST", headers=operator, body=b"")[0] == 200


@pytest.mark.parametrize("header", ["Bearer", "Bearer ", "bearer s3cret", "Token s3cret", "s3cret", "Bearer s3cret extra"])
def test_a_malformed_authorization_header_is_never_accepted(tmp_path: Path, header: str) -> None:
    with serving([_manual(tmp_path)], token="s3cret") as (base, _):
        assert _call(f"{base}/query?q=can", headers={"Authorization": header})[0] == 401
