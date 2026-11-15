# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_disk_faults_simulation.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
#
# evidence=simulator: a real DocsQaServer on a loopback port with the REAL loaders, while the disk under it goes wrong on a schedule drawn from a fixed
# seed: a document that vanishes, one that is overwritten with bytes that are not text, one that is cut off or emptied, a corpus bundle that is altered,
# cut in half or removed, and then put right again. What must hold: the service keeps answering from the index it has, says in /stats what it could not
# read and why, never answers 500 to a question, and takes the documents back at the next reload. Nothing leaves this machine.
# =============================================================================
from __future__ import annotations

import json
import random
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hydra_umc_docs_qa.api import BundleLoader, DocsQaServer, build_server_index
from hydra_umc_docs_qa.bundle import build_bundle

SEED = 20260101
AUTH = {"Authorization": "Bearer t"}

GOOD_A = "# CAN Bus Wiring\nTwisted pair CAN bus wiring needs 120 ohm termination at both ends.\n"
GOOD_B = "# Firmware Flashing\nFlash URTC firmware over SWD or JTAG using URTC-FLASHER.\n"


def _call(url: str, method: str = "GET", headers: dict[str, str] | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=b"" if method == "POST" else None)
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
def _serving(index, sources: list[str], loader) -> Iterator[tuple[str, DocsQaServer]]:
    server = DocsQaServer(("127.0.0.1", 0), index, sources, [], loader=loader, token="t")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _ask(base: str, question: str) -> tuple[int, list[str]]:
    status, body = _call(f"{base}/query?{urllib.parse.urlencode({'q': question})}", headers=AUTH)
    return status, [r["chunk"]["source"] for r in body.get("results", [])]


def _reload(base: str) -> tuple[int, dict]:
    return _call(f"{base}/reload", method="POST", headers=AUTH)


def _stats(base: str) -> dict:
    return _call(f"{base}/stats")[1]


# what each fault does to the second document: (how the file is spoiled, what /stats must say about it, whether its content is still answered)
FAULTS = {
    "vanishes": (lambda p: p.unlink(missing_ok=True), "file not found", False),
    "not-text": (lambda p: p.write_bytes(b"# Flash\n\xff\xfe\xfa not text \x80\x81"), "not valid UTF-8", False),
    "emptied": (lambda p: p.write_bytes(b""), None, False),
    "cut-off": (lambda p: p.write_bytes(GOOD_B.encode("utf-8")[:17]), None, False),
    "restored": (lambda p: p.write_text(GOOD_B, encoding="utf-8"), None, True),
}


def test_a_document_that_goes_wrong_on_disk_never_takes_the_answers_of_the_others_away(tmp_path: Path) -> None:
    a, b = tmp_path / "wiring.md", tmp_path / "flashing.md"
    a.write_text(GOOD_A, encoding="utf-8")
    b.write_text(GOOD_B, encoding="utf-8")
    index, sources, _ = build_server_index([a, b])
    random_ = random.Random(SEED)
    schedule = [random_.choice(sorted(FAULTS)) for _ in range(24)]
    # every fault and the repair appear at least once, whatever the seed draws
    schedule[:5] = ["vanishes", "restored", "not-text", "cut-off", "emptied"]
    with _serving(index, sources, lambda: build_server_index([a, b])) as (base, server):
        for fault in schedule:
            spoil, message, still_answered = FAULTS[fault]
            spoil(b)
            status, body = _reload(base)
            assert status == 200 and body["reloaded"] is True, (fault, body)
            # the first document keeps being found, always
            assert _ask(base, "can bus termination")[1][:1] == ["wiring.md"], fault
            stats = _stats(base)
            if message:
                assert any(message in line for line in stats["rejectedDocuments"]), (fault, stats["rejectedDocuments"])
            else:
                assert stats["rejectedDocuments"] == [], (fault, stats["rejectedDocuments"])
            found = _ask(base, "flash firmware SWD JTAG")
            assert found[0] == 200, (fault, found)
            assert ("flashing.md" in found[1]) is still_answered, (fault, found)
            assert stats["lastReloadError"] is None
        assert server.reloads == len(schedule)
        # the same seed gives the same schedule on every run
        again = random.Random(SEED)
        assert [again.choice(sorted(FAULTS)) for _ in range(24)][5:] == schedule[5:]


def test_when_every_document_is_lost_the_service_keeps_the_index_it_has_and_takes_them_back(tmp_path: Path) -> None:
    a, b = tmp_path / "wiring.md", tmp_path / "flashing.md"
    a.write_text(GOOD_A, encoding="utf-8")
    b.write_text(GOOD_B, encoding="utf-8")
    index, sources, _ = build_server_index([a, b])
    with _serving(index, sources, lambda: build_server_index([a, b])) as (base, server):
        before = server.index
        a.unlink()
        b.unlink()
        status, body = _reload(base)
        assert status == 500 and body["reloaded"] is False and "previous index is kept" in body["error"]
        assert server.index is before
        assert _ask(base, "can bus termination")[0] == 200
        assert "previous index is kept" in _stats(base)["lastReloadError"]
        a.write_text(GOOD_A, encoding="utf-8")
        assert _reload(base)[0] == 200
        assert _stats(base)["lastReloadError"] is None
        assert _ask(base, "can bus termination")[1][:1] == ["wiring.md"]


def test_a_corpus_bundle_that_is_altered_cut_or_removed_is_refused_and_the_new_release_is_taken_when_it_is_right(tmp_path: Path) -> None:
    docs = tmp_path / "docs-src"
    docs.mkdir()
    (docs / "wiring.md").write_text(GOOD_A, encoding="utf-8")
    index, _, _ = build_server_index([docs])
    assert index is not None
    bundle = tmp_path / "corpus.json"
    build_bundle(index, "0.3.0", bundle)
    good = bundle.read_bytes()
    loader = BundleLoader(bundle, None, True)
    first, sources, _ = loader()
    assert first is not None
    newer = tmp_path / "newer.json"
    (docs / "flashing.md").write_text(GOOD_B, encoding="utf-8")
    bigger, _, _ = build_server_index([docs])
    assert bigger is not None
    build_bundle(bigger, "0.3.1", newer)
    damage = {
        "altered": lambda: bundle.write_bytes(good.replace(b"termination", b"terminatiom", 1)),
        "cut in half": lambda: bundle.write_bytes(good[: len(good) // 2]),
        "not json": lambda: bundle.write_bytes(b"\x00\x01 this is not a bundle"),
        "removed": lambda: bundle.unlink(),
    }
    with _serving(first, sources, loader) as (base, server):
        before = server.index
        for name in random.Random(SEED).sample(sorted(damage), k=len(damage)):
            damage[name]()
            status, body = _reload(base)
            assert status == 500 and body["reloaded"] is False, (name, body)
            assert server.index is before, name
            assert _stats(base)["lastReloadError"], name
            assert _ask(base, "can bus termination")[1][:1] == ["wiring.md"], name
            bundle.write_bytes(good)
        assert server.reloads == 0
        bundle.write_bytes(newer.read_bytes())
        status, body = _reload(base)
        assert status == 200 and body["reloaded"] is True
        stats = _stats(base)
        assert stats["release"] == "0.3.1" and stats["lastReloadError"] is None and stats["reloads"] == 1
        assert _ask(base, "flash firmware SWD JTAG")[1][:1] == ["flashing.md"]
