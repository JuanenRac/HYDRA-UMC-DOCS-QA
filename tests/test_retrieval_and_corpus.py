# =============================================================================
# HYDRA-UMC-DOCS-QA - tests/test_retrieval_and_corpus.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""BM25, stemming and the heading boost, the "no answer" threshold, the corpus manifest with hashes and citations, and the reload of the index."""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from hydra_umc_docs_qa import main as docs_main
from hydra_umc_docs_qa.api import DocsQaServer, build_corpus_index, build_server_index
from hydra_umc_docs_qa.corpus import CorpusError, collect, load_manifest, parse_manifest, sha256_of
from hydra_umc_docs_qa.index import answerable, build_index, light_stem, search
from hydra_umc_docs_qa.ingest import DocChunk, RejectionReason, ingest_markdown_file, ingest_markdown_text

DOC = (
    "# CAN bus wiring\n"
    "Twisted pair cables need a 120 ohm termination at both ends of the bus.\n"
    "# Firmware flashing\n"
    "Flash the firmware over SWD with the flasher. The firmware image is signed.\n"
    "# Troubleshooting\n"
    "If the bus shows errors, check the termination and the wiring of the bus.\n"
)


def chunks() -> list[DocChunk]:
    return ingest_markdown_text(DOC, source="manual.md")


# ---- scorers ----


def test_bm25_ranks_the_section_that_is_about_the_question_first() -> None:
    index = build_index(chunks(), scorer="bm25")
    results = search(index, "bus termination", top_k=3)
    assert results[0].chunk.heading in ("CAN bus wiring", "Troubleshooting")
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)
    assert all(r.score > 0 for r in results)


def test_bm25_scores_are_deterministic_and_zero_for_unrelated_words() -> None:
    index = build_index(chunks(), scorer="bm25")
    assert search(index, "bus termination") == search(index, "bus termination")
    assert search(index, "quantum entanglement") == []


def test_a_word_repeated_in_a_section_does_not_run_away_with_bm25() -> None:
    spam = [DocChunk("a.md", "spam", "bus " * 200), DocChunk("a.md", "real", "the bus needs a termination resistor at both ends"), DocChunk("a.md", "other", "nothing related here")]
    results = search(build_index(spam, scorer="bm25"), "bus termination", top_k=3)
    assert results[0].chunk.heading == "real", "saturating term frequency: the section that has both words wins"


def test_the_default_is_still_tfidf_and_an_unknown_scorer_is_refused() -> None:
    assert build_index(chunks()).scorer == "tfidf"
    with pytest.raises(ValueError):
        build_index(chunks(), scorer="magic")
    for bad in (0, 11, True, 1.5):
        with pytest.raises(ValueError):
            build_index(chunks(), heading_boost=bad)  # type: ignore[arg-type]


# ---- stemming ----


def test_the_stemmer_is_cautious() -> None:
    assert light_stem("robots") == "robot"
    assert light_stem("wires") == "wir" or light_stem("wires") == "wire" or light_stem("wires").startswith("wir")
    assert light_stem("flashing") == "flash"
    assert light_stem("flashed") == "flash"
    assert light_stem("entries") == "entry"
    assert light_stem("can") == "can", "a short word is left alone"
    assert light_stem("bus") == "bus"
    assert light_stem("0x7f3") == "0x7f3", "a part number or an identifier with a digit is never cut"
    assert light_stem("日本語") == "日本語"


def test_with_stemming_a_question_in_another_form_finds_the_section() -> None:
    plain = build_index(chunks())
    stemmed = build_index(chunks(), stem=True)
    assert search(plain, "flashes the firmwares") != search(stemmed, "flashes the firmwares")
    results = search(stemmed, "flashing firmwares", top_k=1)
    assert results and results[0].chunk.heading == "Firmware flashing"


# ---- heading boost ----


def test_the_heading_boost_puts_the_section_named_after_the_question_first() -> None:
    sections = [
        DocChunk("m.md", "Safety", "The firmware update never runs while the cell is moving. Firmware firmware."),
        DocChunk("m.md", "Firmware update", "Use the flasher. It signs nothing by itself."),
    ]
    question = "firmware update"
    plain = search(build_index(sections, scorer="bm25"), question, top_k=2)
    boosted = search(build_index(sections, scorer="bm25", heading_boost=4), question, top_k=2)
    assert [r.chunk.heading for r in plain] == ["Safety"], "headings are not indexed unless asked: the section named after the question is not even found"
    assert boosted[0].chunk.heading == "Firmware update"


# ---- answerable ----


def test_coverage_is_the_share_of_the_question_a_section_holds() -> None:
    index = build_index(chunks())
    full = search(index, "bus termination", top_k=1)[0]
    half = search(index, "bus xylophone", top_k=1)[0]
    assert full.coverage == 1.0
    assert half.coverage == 0.5


def test_a_question_nothing_answers_is_not_answerable() -> None:
    ok, reason = answerable([], min_score=0.0, min_coverage=0.0)
    assert not ok and "shares a word" in reason
    index = build_index(chunks())
    results = search(index, "bus xylophone zebra quartz", top_k=3)
    assert answerable(results)[0], "with no minimum any match is an answer, as before"
    ok, reason = answerable(results, min_coverage=0.6)
    assert not ok and "words of the question" in reason
    ok, reason = answerable(results, min_score=99.0)
    assert not ok and "below the minimum" in reason


# ---- citations and the corpus manifest ----


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_every_section_of_a_file_carries_the_hash_of_the_file(tmp_path: Path) -> None:
    doc = _write(tmp_path / "manual.md", DOC)
    sections = ingest_markdown_file(doc)
    expected = hashlib.sha256(doc.read_bytes()).hexdigest()
    assert {c.sha256 for c in sections} == {expected}
    assert sections[1].citation.endswith(f"#1@{expected}")
    assert ingest_markdown_text(DOC, source="x.md")[0].citation == "x.md#0", "text that did not come from a file has no hash to cite"


def test_a_manifest_needs_roots_and_well_formed_pins() -> None:
    with pytest.raises(CorpusError, match="at least one directory"):
        parse_manifest({"roots": []})
    with pytest.raises(CorpusError):
        parse_manifest([])
    with pytest.raises(CorpusError, match="64 hexadecimal"):
        parse_manifest({"roots": ["/x"], "documents": [{"path": "/x/a.md", "sha256": "abc"}]})
    with pytest.raises(CorpusError):
        parse_manifest({"roots": ["/x"], "strict": "yes"})
    with pytest.raises(CorpusError):
        parse_manifest({"roots": ["/x"], "exclude": [1]})


def test_only_the_roots_are_read_and_the_exclusions_are_honoured(tmp_path: Path) -> None:
    _write(tmp_path / "repo" / "README.md", DOC)
    _write(tmp_path / "repo" / "drafts" / "wip.md", "# Draft\nnot approved\n")
    _write(tmp_path / "repo" / "docs" / "guide.md", "# Guide\nsomething\n")
    _write(tmp_path / "elsewhere" / "secret.md", "# Secret\nnever quoted\n")
    manifest = parse_manifest({"roots": [str(tmp_path / "repo")], "exclude": ["drafts/*", "**/drafts/**"]})
    paths, refused = collect(manifest)
    assert [p.name for p in paths] == ["guide.md", "README.md"] or sorted(p.name for p in paths) == ["README.md", "guide.md"]
    assert refused == []
    assert not any("secret" in p.name or "wip" in p.name for p in paths)


def test_a_root_that_is_missing_is_reported_and_not_silently_ignored(tmp_path: Path) -> None:
    paths, refused = collect(parse_manifest({"roots": [str(tmp_path / "nowhere")]}))
    assert paths == [] and refused[0].reason is RejectionReason.MISSING


def test_a_pinned_document_whose_bytes_changed_is_not_ingested(tmp_path: Path) -> None:
    good = _write(tmp_path / "r" / "good.md", "# Good\napproved text\n")
    edited = _write(tmp_path / "r" / "edited.md", "# Edited\noriginal text\n")
    pins = [{"path": str(good), "sha256": sha256_of(good)}, {"path": str(edited), "sha256": sha256_of(edited)}]
    edited.write_text("# Edited\ntext somebody changed afterwards\n", encoding="utf-8")
    paths, refused = collect(parse_manifest({"roots": [str(tmp_path / "r")], "documents": pins}))
    assert [p.name for p in paths] == ["good.md"]
    assert refused[0].reason is RejectionReason.HASH_MISMATCH and "edited" in refused[0].describe() and "pinned" in refused[0].describe()


def test_with_pins_a_new_file_in_a_root_is_not_quotable_until_somebody_pins_it(tmp_path: Path) -> None:
    pinned = _write(tmp_path / "r" / "approved.md", "# A\nok\n")
    _write(tmp_path / "r" / "new.md", "# N\ndropped in later\n")
    pins = [{"path": str(pinned), "sha256": sha256_of(pinned)}]
    strict, _ = collect(parse_manifest({"roots": [str(tmp_path / "r")], "documents": pins}))
    assert [p.name for p in strict] == ["approved.md"]
    loose, _ = collect(parse_manifest({"roots": [str(tmp_path / "r")], "documents": pins, "strict": False}))
    assert sorted(p.name for p in loose) == ["approved.md", "new.md"]


def test_a_pin_outside_every_root_is_refused(tmp_path: Path) -> None:
    outside = _write(tmp_path / "outside" / "x.md", "# X\nx\n")
    _write(tmp_path / "root" / "a.md", "# A\na\n")
    paths, refused = collect(parse_manifest({"roots": [str(tmp_path / "root")], "documents": [{"path": str(outside), "sha256": sha256_of(outside)}]}))
    assert any(r.reason is RejectionReason.OUTSIDE_ROOTS for r in refused)
    assert all("outside" not in str(p) for p in paths)


def test_a_manifest_file_is_read_with_its_limits(tmp_path: Path) -> None:
    path = _write(tmp_path / "corpus.json", json.dumps({"roots": [str(tmp_path)]}))
    assert load_manifest(path).roots == (tmp_path,)
    with pytest.raises(CorpusError):
        load_manifest(tmp_path / "missing.json")
    _write(tmp_path / "bad.json", "{nope")
    with pytest.raises(CorpusError):
        load_manifest(tmp_path / "bad.json")
    _write(tmp_path / "nan.json", '{"roots": ["/x"], "strict": NaN}')
    with pytest.raises(CorpusError):
        load_manifest(tmp_path / "nan.json")


# ---- the service: reload, token, thresholds ----


def _serve(**kwargs):
    server = DocsQaServer(("127.0.0.1", 0), *kwargs.pop("state"), **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _stop(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _request(url: str, method: str = "GET", token: str | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_query_carries_citations_coverage_and_an_answerable_flag(tmp_path: Path) -> None:
    doc = _write(tmp_path / "manual.md", DOC)
    server, thread, base = _serve(state=build_server_index([doc]))
    try:
        status, body = _request(f"{base}/query?q=termination+bus&top_k=2")
        assert status == 200 and body["answerable"] is True
        first = body["results"][0]
        assert first["citation"].startswith("manual.md#") and first["citation"].endswith("@" + sha256_of(doc))
        assert 0 < first["coverage"] <= 1
    finally:
        _stop(server, thread)


def test_a_question_below_the_threshold_gets_no_context(tmp_path: Path) -> None:
    doc = _write(tmp_path / "manual.md", DOC)
    server, thread, base = _serve(state=build_server_index([doc]), min_coverage=0.7)
    try:
        status, body = _request(f"{base}/query?q=bus+xylophone+zebra")
        assert status == 200 and body["answerable"] is False and body["results"] == []
        assert "words of the question" in body["reason"]
        # a request may be stricter than the service, never looser
        status, body = _request(f"{base}/query?q=bus+termination&min_coverage=0.1")
        assert body["answerable"] is True
        status, body = _request(f"{base}/query?q=bus+termination&min_coverage=1.5")
        assert status == 400
    finally:
        _stop(server, thread)


def test_reload_needs_a_token_and_picks_up_a_changed_document(tmp_path: Path) -> None:
    doc = _write(tmp_path / "manual.md", "# One\nalpha beta\n")
    loader = lambda: build_server_index([doc])  # noqa: E731
    server, thread, base = _serve(state=loader(), loader=loader, token="0123456789abcdef")
    try:
        assert _request(f"{base}/reload", "POST")[0] == 401
        assert _request(f"{base}/reload", "POST", "wrong-token-wrong")[0] == 401
        assert _request(f"{base}/query?q=gamma", token="0123456789abcdef")[1]["results"] == []
        doc.write_text("# One\nalpha beta\n# Two\ngamma delta\n", encoding="utf-8")
        status, body = _request(f"{base}/reload", "POST", "0123456789abcdef")
        assert status == 200 and body["reloaded"] is True and body["chunks"] == 2
        assert _request(f"{base}/query?q=gamma", token="0123456789abcdef")[1]["results"]
        stats = _request(f"{base}/stats")[1]
        assert stats["chunks"] == 2 and stats["reloads"] == 1 and stats["reloadable"] is True and stats["lastReloadError"] is None
        assert stats["builtAtMs"] > 0
    finally:
        _stop(server, thread)


def test_without_a_token_reload_is_refused_and_a_failed_reload_keeps_the_index(tmp_path: Path) -> None:
    doc = _write(tmp_path / "manual.md", "# One\nalpha beta\n")
    loader = lambda: build_server_index([doc])  # noqa: E731
    server, thread, base = _serve(state=loader(), loader=loader)
    try:
        status, body = _request(f"{base}/reload", "POST")
        assert status == 403 and "token" in body["error"]
        # the programmatic reload (SIGHUP): the document disappears, the index stays
        doc.unlink()
        ok, message = server.reload()
        assert not ok and "kept" in message
        assert _request(f"{base}/query?q=alpha")[1]["results"], "the answers did not go away"
        assert _request(f"{base}/stats")[1]["lastReloadError"]
    finally:
        _stop(server, thread)


def test_a_service_started_without_a_loader_says_it_cannot_reload() -> None:
    server, thread, base = _serve(state=build_server_index([]), token="0123456789abcdef")
    try:
        assert _request(f"{base}/reload", "POST", "0123456789abcdef")[0] == 409
    finally:
        _stop(server, thread)


def test_query_needs_the_token_when_there_is_one_and_stats_stays_open(tmp_path: Path) -> None:
    doc = _write(tmp_path / "manual.md", DOC)
    server, thread, base = _serve(state=build_server_index([doc]), token="0123456789abcdef")
    try:
        assert _request(f"{base}/query?q=bus")[0] == 401
        assert _request(f"{base}/query?q=bus", token="0123456789abcdef")[0] == 200
        assert _request(f"{base}/stats")[0] == 200
    finally:
        _stop(server, thread)


def test_the_corpus_index_is_built_from_the_manifest(tmp_path: Path) -> None:
    _write(tmp_path / "r" / "a.md", DOC)
    _write(tmp_path / "r" / "private" / "b.md", "# B\nquoted by mistake\n")
    manifest = parse_manifest({"roots": [str(tmp_path / "r")], "exclude": ["private/*"]})
    index, sources, _rejected = build_corpus_index(manifest, scorer="bm25", stem=True)
    assert index is not None and index.scorer == "bm25" and index.stem is True
    assert len(sources) == 1 and not any("b.md" in s for s in sources)


# ---- the command line ----


def test_the_command_line_takes_a_corpus_a_scorer_and_prints_the_hash_in_the_citation(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYDRA_UMC_DOCS_QA_CACHE_DIR", str(tmp_path / "cache"))
    doc = _write(tmp_path / "r" / "manual.md", DOC)
    corpus = _write(tmp_path / "corpus.json", json.dumps({"roots": [str(tmp_path / "r")], "documents": [{"path": str(doc), "sha256": sha256_of(doc)}]}))
    code = docs_main.main(["query", "bus termination", "--corpus", str(corpus), "--scorer", "bm25", "--stem", "--heading-boost", "3"])
    out = capsys.readouterr().out
    assert code == 0
    assert "manual.md#" in out and "@" + sha256_of(doc)[:12] in out


def test_a_bad_corpus_or_option_stops_the_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYDRA_UMC_DOCS_QA_CACHE_DIR", str(tmp_path / "cache"))
    assert docs_main.main(["query", "x", "--corpus", str(tmp_path / "missing.json")]) == 2
    assert docs_main.main(["query", "x", "--docs", str(_write(tmp_path / "a.md", DOC)), "--heading-boost", "99"]) == 2
    assert docs_main.main(["serve", "--port", "0", "--min-coverage", "3"]) == 2
    assert docs_main.main(["serve", "--port", "0", "--addr", "0.0.0.0"]) == 2, "away from loopback a token is required"
    assert "token" in capsys.readouterr().err.lower()
