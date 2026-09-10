# Changelog: HYDRA-UMC-DOCS-QA 📚

All notable changes to this project will be documented in this file. The
version number follows this ecosystem's "odometer" scheme: PATCH +1 on
every real build, rolling into MINOR past 9 (`0.0.9` -> `0.1.0`); MAJOR is
bumped manually only. See `bump_version.py`.

## [0.0.8] - QA-01/QA-02/QA-03: fenced code, CJK/accented search, canonical citations

- **QA-01 (P2):**
  `ingest_markdown_text` treated any line starting with `#` as a real
  heading, even inside a fenced ` ``` `/`~~~` code block - a
  `# Shell comment` inside an Install section's ```sh fence wrongly split
  the section around it. Fixed with a real, CommonMark-consistent fence
  tracker: once inside a fence, only a line starting with the SAME
  character repeated at least as many times as the opener closes it, so
  every line in between (headings included) is kept as real body text.
- **QA-02 (P1):** the tokenizer's old `[a-z0-9]+` pattern
  returned `[]` for any query with no Latin text at all - a real
  Chinese/Japanese-language question against this project's own
  translated README corpus could never match anything. Fixed with a real,
  dictionary-free dual approach: accented Latin words (Spanish/French/
  Italian/German) now tokenize whole instead of losing their accented
  character, and CJK ideograph/kana runs are split into overlapping
  2-character bigrams - the same well-established technique real search
  engines without a language-specific segmenter use (Lucene/
  Elasticsearch's own CJK bigram analyzer), not a call to another AI
  service. Both indexing and querying share this one `tokenize()`, so
  normalization stays identical on both sides.
- **QA-03 (P2):** `ingest_markdown_file` identified every
  document by `path.name` alone, making `README.md` from two different
  repositories indistinguishable in a citation. New `canonical_source()`
  walks upward from the file for the nearest `hydra-umc.project.json` -
  the manifest every real repo in this ecosystem already carries at its
  own root - and qualifies the source with that project's own directory
  name (e.g. `HYDRA-UMC-STUDIO/README.md`); a document outside any such
  project still falls back to the bare filename, unchanged. Per-revision
  and per-line-range identity remain real, deferred future work - this
  closes the concrete, testable bar (two README files sharing the same
  heading/text now resolve to distinguishable, project-correct sources),
  not the full aspirational scope.
- 11 new regression tests (51 total, up from 40) covering all three
  findings, including real end-to-end retrieval proofs for Chinese,
  Japanese and accented-Spanish queries (not just tokenizer unit checks).
  `python -m pytest tests/ -v`: all passing.
- **New `Dockerfile`**, closing the real gap HYDRA-UMC-COGNITIVE-NODE's
  own `docker-compose.yml` named ("do not have published Dockerfiles
  yet"). Same `--addr`/`--port` CLI the real CM5 systemd unit
  (`systemd/hydra-umc-docs-qa.service`) already runs, bound to `0.0.0.0`
  instead of `127.0.0.1` - a container's own network namespace is the
  real isolation boundary here, not the loopback bind, and `127.0.0.1`
  inside a container would be unreachable from HYDRA-UMC-COGNITIVE-NODE's
  own container over the compose network. `--docs` points at the real,
  copied-in `/app` instead of relying on `main.py`'s own
  "this repo's own README.md/CHANGELOG.md" default, which resolves from
  `__file__`'s own on-disk location and would miss after a real
  `pip install .` (site-packages, not a git checkout) - uses this same
  release's own directory-recursion support to pick up README, CHANGELOG
  and `docs/CLI_REFERENCE.md` together. Non-root. Not build-tested (no
  Docker runtime on this dev machine) - every path/flag matches the one
  already verified live on the real CM5.
- **`ingest.py`'s `--docs` now accepts directories** (new
  `expand_doc_paths()`, recursive, sorted for a deterministic ingestion
  order) - `--docs` used to require explicit file paths one by one, and the
  default corpus was just this repo's own README/CHANGELOG -
  contradicting the promise of having "read every manual... across the
  ecosystem". A caller can now point `--docs` at a whole checkout root
  and every real `.md`/`.markdown` file under it (still gated through
  the existing allowlist/size checks) gets ingested. A file path still
  passes through unchanged, including one that gets rejected downstream.
- **`.github/workflows/ci.yml`** - the real `tests/` pytest suite is now
  actually installed and run in CI, matching the fix already landed in
  sibling repos this session. This repo's own baseline workflow only ever
  compile-checked (`py_compile`) `.py` files - it never ran `pytest`, so a
  regression in `tests/` could be merged without CI ever failing. CI-only
  fix, no runtime code changed, no version bump.

## [0.0.7] - Real v0: JSON/HTTP server mode, plus CM5 deployment

- **`api.py`** (new) - `GET /query` reaches the exact same `search()`
  the CLI's own `query` subcommand already runs, but real difference
  from the CLI: `build_server_index()` ingests and builds the real
  `TfidfIndex` ONCE at server startup, not on every single request -
  the CLI's own re-ingest-per-invocation is fine for a one-shot command,
  wasteful for a service handling one query after another. Real gap this
  closes: this project's own TF-IDF retrieval was only ever reachable as
  a one-shot CLI.
- **`main.py`** - new `serve` subcommand (`--docs`/`--addr`/`--port`,
  default `127.0.0.1:8110`, same `--docs` default as the CLI - this
  repo's own README.md/CHANGELOG.md).
- **`systemd/hydra-umc-docs-qa.service`** (new) - loopback-only unit for
  `HYDRA-UMC-OS/provisioning/install_docs_qa.sh` (new, that repo), same
  stdlib "copy src/ + PYTHONPATH" shape as `install_datalake.sh` - plus
  this repo's own README.md/CHANGELOG.md, so the default corpus resolves
  to something real once deployed.
- 8 new tests (`tests/test_api.py`, real end-to-end HTTP, reusing this
  repo's own `tests/test_cli.py` fixture shapes) - 37 total.

## [0.0.6] - Rejected a negative --top-k instead of silently mis-slicing

### Fixed
- **`index.py`'s `search()`** - `results[:top_k]` had no guard against a
  negative `top_k`; Python's own negative-slice semantics (e.g. `top_k=-1`)
  silently returned all but the lowest-ranked result instead of erroring or
  returning nothing - a real, CLI-reachable edge case (`query "..." --top-k
  -1`), not just a defensive concern. `search()` now raises `ValueError` for
  `top_k < 0`, and `main.py`'s `query` subcommand catches it and reports a
  clean `ERROR: ...` line (exit 1) instead of letting the wrong results
  print silently. 2 new tests (`test_index.py`, `test_cli.py`) = 29 total.
- Markdown supplied through the CLI allowlist is now bounded to 4 MiB before
  it is read. Oversized inputs receive a distinct rejection reason, preserving
  the query's honest citation boundary without unbounded local file reads.
- Removed an exact project-count reference from the technical overview.

### Added
- Copyright/license header on every source file and build/run script.
- `CHANGELOG.md` (this file).
- Extended documentation across `README.md` and its 4 translations:
  advanced technical/architecture section, detailed build/run
  troubleshooting, and a full "Related Projects" section.

### Changed
- Inline comments explaining the *why* behind non-obvious decisions
  (versioning scheme, src-layout, why this child has no hardware/
  firmware/os/models of its own).

## [0.0.5] - Deterministic scoring, traceable citations, real document allowlist
### Added
- **A real document allowlist gate** (`ingest.py`'s `validate_doc_path`/`ingest_allowed_markdown_files`) - only `.md`/`.markdown` files that actually exist on disk are ever ingested and cited. Every other `--docs` path (missing, or a non-Markdown file - a stray `.env`, firmware binary, unrelated source file) is rejected with a distinct, printed reason (`file not found` vs `disallowed extension`) instead of being silently skipped or silently read as if it were legitimate documentation. `query` now prints one `REJECTED ...` line per bad path, still answers from whatever real documents remain, and reports the existing honest "No documents ingested" failure only when nothing valid is left.
- **Traceable citations**: `DocChunk` now carries a real `index` - its 0-based ordinal position within its own source document - alongside `source`/`heading`. Citations print as `source#index` (e.g. `manual.md#1`), a stable key that disambiguates repeated headings within one file and lets a citation be deterministically recovered by re-parsing the same source (proven by a real round-trip test).
- 21 new tests (`test_ingest.py`, `test_cli.py`, `test_determinism.py`) covering the full allowlist validation matrix, the citation round-trip, and cross-process output determinism - 26 tests total.

### Fixed
- `index.py`'s cosine similarity summed shared TF-IDF terms in `dict.keys() & dict.keys()` set order, which depends on CPython's per-process string hash randomization (`PYTHONHASHSEED`) rather than only on the corpus and query text - a real (if narrow) determinism hole for a retrieval system whose whole point is reproducible answers. Sorting the shared terms before summing removes the hash-seed dependency entirely.

## [0.0.4] - Real v0 lexical retrieval ("Local Vector Search")
### Added
- `ingest.py` - real Markdown ingestion: splits a document into
  heading-scoped `DocChunk`s (text before the first heading is kept
  under an empty heading so front-matter is never silently dropped).
  PDF ingestion (mentioned in the README's own Key Features) is still
  future work.
- `index.py` - a real, stdlib-only TF-IDF index and cosine-similarity
  `search()` over ingested chunks. Deliberately lexical (term-overlap),
  not embedding-based semantic search - honest naming for v0, needs no
  ML dependency, and gives a real, testable retrieval kernel a future
  embedding-based index can be swapped in behind the same `search()`
  contract.
- `main.py` - new `query` subcommand: `hydra-umc-docs-qa query "<question>"
  [--docs PATH...] [--top-k N]`. Defaults to ingesting this repo's own
  real `README.md`/`CHANGELOG.md` when `--docs` isn't given. A query
  whose words don't overlap the corpus prints an honest "no relevant
  passages found" instead of a guess - v0 has no LLM synthesis step.
  Bare invocation (no subcommand) is unchanged: identity/version/role.
- 15 new real tests (`tests/`) - chunking edge cases (front-matter,
  empty sections), ranking correctness (a matching chunk scores above an
  unrelated one, an unrelated query returns no results), and a real
  end-to-end CLI round-trip against a real Markdown file on disk.

### Fixed
- **Real bug found via live end-to-end verification** (not a hypothetical
  review catch): printing a retrieved snippet containing an emoji (this
  ecosystem's own READMEs are full of them, e.g. `🔍 **Local Vector
  Search:**`) crashed with `UnicodeEncodeError` on a Windows console
  using the default `cp1252` codepage. `main()` now reconfigures
  `stdout`/`stderr` to UTF-8 with a safe replace-on-error fallback before
  printing anything, so real Windows usage never crashes on real
  ecosystem content. Verified live (no crash, results printed) after the
  fix, with a regression test.

## [0.0.2]
### Added
- Initial Python scaffolding: `pyproject.toml` (setuptools, src-layout),
  `src/hydra_umc_docs_qa/__init__.py` + `main.py` (real entry point -
  prints identity/version/role, exits 0).
- `bump_version.py` - odometer-style version bump applied to
  `pyproject.toml` and mirrored into `__init__.py`.
- `build.sh` / `build.bat` - create/activate a venv, install the package
  editable, verify it compiles and imports.
- `run.sh` / `run.bat` - run the entry point.
