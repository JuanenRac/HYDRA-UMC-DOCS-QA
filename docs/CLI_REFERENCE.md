# HYDRA-UMC-DOCS-QA — CLI Reference

`hydra-umc-docs-qa` is a Python console script (`src/hydra_umc_docs_qa/main.py`,
installed as an entry point via `pyproject.toml`). Every example below was
captured from a real run of the installed CLI — not written from memory.

## Usage

```
$ hydra-umc-docs-qa -h
usage: hydra-umc-docs-qa [-h] {query,serve,catalog,bundle} ...

Docs-QA (Hailo-10) - retrieval-augmented technical assistant grounded in the
ecosystem's own documentation.

positional arguments:
  {query,serve,catalog,bundle}
    query        Real TF-IDF lexical search over ingested Markdown docs.
    serve        Run 'query' as a JSON/HTTP API (GET /query?q=...&top_k=N) -
                 unlike the CLI, which re-ingests and re-indexes the corpus
                 on every invocation, this builds the real TfidfIndex ONCE
                 at startup and reuses it for every query.

options:
  -h, --help     show this help message and exit
```

Bare invocation (no subcommand) prints identity/version/role and exits `0`:

```
$ hydra-umc-docs-qa
HYDRA-UMC-DOCS-QA v0.1.3
Docs-QA (Hailo-10) - retrieval-augmented technical assistant grounded in the ecosystem's own documentation.
```

## Commands

### `query <question> [--docs FILE [FILE ...]] [--top-k N]`

```
$ hydra-umc-docs-qa query -h
usage: hydra-umc-docs-qa query [-h] [--docs DOCS [DOCS ...]] [--top-k TOP_K]
                               question

positional arguments:
  question              The question to search for.

options:
  -h, --help            show this help message and exit
  --docs DOCS [DOCS ...]
                        Markdown files to ingest (default: this repo's own
                        README.md/CHANGELOG.md).
  --top-k TOP_K         Maximum number of passages to print (default: 5).
```

Real, stdlib-only TF-IDF lexical search (not embedding-based semantic
search, and no LLM synthesis step — see the project README's own
roadmap) over ingested Markdown. Without `--docs`, it searches this
repo's own `README.md`/`CHANGELOG.md`.

**A real match**, against this repo's own README:

```
$ hydra-umc-docs-qa query "RAG documentation retrieval"
Top 5 passage(s) for: "RAG documentation retrieval"

1. [0.194] HYDRA-UMC-DOCS-QA/README.md#3 - 1. TECHNICAL OVERVIEW
   HYDRA-UMC-DOCS-QA is a specialized Retrieval-Augmented Generation (RAG) assistant designed for on-site technicians and developers. It provides instant, grou...

2. [0.109] HYDRA-UMC-DOCS-QA/README.md#2 - RAG-Based AI Assistant for Hardware Maintenance & Documentation
   HYDRA-UMC-DOCS-QA is a specialized Retrieval-Augmented Generation (RAG) assistant...
```

Each citation is `source#index` — a stable pointer to the exact ingested
chunk (its 0-based ordinal position within that source file), not just
an ambiguous filename. `source` is qualified with the owning project's
own directory name whenever the file lives under one (found by walking
up for the nearest `hydra-umc.project.json`) — so `README.md` from two
different repositories never collide in a citation; a file outside any
such project still falls back to its bare filename. Re-parsing the same
source at the same index always recovers the identical passage.

**An honest miss** — a question whose words don't overlap the corpus at
all:

```
$ hydra-umc-docs-qa query "quantum entanglement lasagna"
No relevant passages found for: "quantum entanglement lasagna"
(v0 is real lexical TF-IDF retrieval only - a question whose words don't overlap the corpus gets an honest miss, not a guess.)
```

**A mix of a real, a missing, and a disallowed `--docs` path** — every
`--docs` path is validated: only existing `.md`/`.markdown` files are
ever ingested and cited. A missing or disallowed path is rejected and
reported, and the query still answers from whatever real documents
remain:

```
$ hydra-umc-docs-qa query "firmware flashing" --docs manual.md ghost.md secret.env
REJECTED ghost.md: file not found (no source)
REJECTED secret.env: disallowed extension .env - only .markdown, .md are ingested
Top 1 passage(s) for: "firmware flashing"

1. [0.289] manual.md#1 - Firmware Flashing
   Flash URTC firmware over SWD or JTAG using URTC-FLASHER.
```

If **every** `--docs` path is rejected, the command exits `1` with a
clear, honest failure instead of a silent empty result:

```
$ hydra-umc-docs-qa query "anything" --docs notes.pdf
REJECTED notes.pdf: disallowed extension .pdf - only .markdown, .md are ingested
No documents ingested - check the --docs paths.
$ echo $?
1
```

### `serve [--docs FILE [FILE ...]] [--addr ADDR] [--port PORT]`

Runs the exact same `search()` the CLI's own `query` subcommand uses, but
as a long-running JSON/HTTP API (`src/hydra_umc_docs_qa/api.py`, stdlib
`http.server`) instead of a one-shot CLI call. Real difference from the
CLI: the CLI re-ingests and re-indexes the corpus on every single
invocation (fine for a one-shot command); `serve` ingests and builds the
real `TfidfIndex` ONCE at startup and reuses it for every query.
`--docs` defaults to this repo's own `README.md`/`CHANGELOG.md`, same as
the CLI. `--addr`/`--port` default to `127.0.0.1:8110`.

Real startup output — one document, `manual.md`, real-indexed:

```
$ hydra-umc-docs-qa serve --docs manual.md --port 8110
[docs-qa] indexed 1 document(s): manual.md
[docs-qa] HTTP API listening on 127.0.0.1:8110
[docs-qa] GET /query?q=...&top_k=N, GET /stats
```

`GET /query?q=QUESTION[&top_k=N]` — same search as the CLI's `query`,
JSON-shaped. Real output against the same `manual.md` fixture used above
(`# Firmware Flashing` / `Flash URTC firmware over SWD or JTAG using
URTC-FLASHER.`) — note the score (`0.2887`) matches the CLI's own
`[0.289]` for the identical query and text:

```
$ curl -s "http://127.0.0.1:8110/query?q=firmware+flashing"
{"question": "firmware flashing", "results": [{"chunk": {"source": "manual.md", "heading": "Firmware Flashing", "text": "Flash URTC firmware over SWD or JTAG using URTC-FLASHER.", "index": 0}, "score": 0.28867513459481287}]}
```

**The sentence that answers.** A passage can be long and the sentence that matters is often not the first one. `query --extractive` prints, for each passage, the sentence that holds the most words of the question (the same word matching as the retrieval; the earliest wins a tie, and a passage with no word in common gives back its start) instead of its first 160 characters. `GET /query?q=...&extractive=1` adds the same sentence as an `answer` field to each result. Nothing is generated: the text is always a piece of the passage, and `citation` still names where it comes from.

`GET /stats` — which documents this server instance actually indexed at
startup, and which were rejected (file names only, never absolute paths; a
symbolic link is rejected, and `top_k` is capped at 50):

```
$ curl -s http://127.0.0.1:8110/stats
{"ingestedSources": ["manual.md"], "rejectedDocuments": [], "indexed": true}
```

A missing required query parameter is a real `400`, not a crash:

```
$ curl -s -w '\nHTTP:%{http_code}\n' http://127.0.0.1:8110/query
{"error": "missing required query param: q"}
HTTP:400
```

If **every** `--docs` path given to `serve` is rejected at startup, the
server still starts (so `/stats` stays inspectable), but `GET /query`
reports a real `503` instead of crashing or silently returning nothing:

```
$ hydra-umc-docs-qa serve --docs notes.pdf --port 8110
REJECTED notes.pdf: file not found (no source)
WARNING: no documents were successfully ingested - GET /query will report 503 until this is fixed.
[docs-qa] HTTP API listening on 127.0.0.1:8110
[docs-qa] GET /query?q=...&top_k=N, GET /stats

$ curl -s -w '\nHTTP:%{http_code}\n' "http://127.0.0.1:8110/query?q=anything"
{"error": "no documents were successfully ingested at startup - nothing to search"}
HTTP:503
```

Any other path is a real `404`:

```
$ curl -s -w '\nHTTP:%{http_code}\n' http://127.0.0.1:8110/nope
{"error": "not found"}
HTTP:404
```

### `catalog --mcu-protocol FILE [--urtc-tools FILE] --out DIR`

Writes the error codes of the controller and the tool profiles of URTC as Markdown documents, one section per code or tool, so that the normal ingestion indexes them and an answer can cite `source#section@sha256`. `--mcu-protocol` is the `protocol.py` of the controller bridge (its `STATUS_*` and `VERIFY_FAIL_REASON_*` codes become `mcu-error-codes.md`); `--urtc-tools` is the `spec/tools.json` of URTC (`urtc-tool-profiles.md`). Both files are only read. The meaning of each controller code is written in the catalog; a code that the source defines and the catalog does not know is listed with a line saying that no description was written for it.

```
$ hydra-umc-docs-qa catalog --mcu-protocol protocol.py --urtc-tools tools.json --out corpus/
wrote corpus/mcu-error-codes.md
wrote corpus/urtc-tool-profiles.md
```

Once those documents are in the corpus, `GET /error?code=0x05` (a number) or `GET /error?code=STATUS_VERIFY_FAIL` (a name) of `serve` returns every section that defines the code, with its kind, text and citation: `{"code": "0x05", "found": true, "matches": [...]}`. A code that no section defines gives `"found": false` and an empty list, never a guess.

### `bundle --release X --out FILE [--key-file FILE] [--docs ... | --corpus MANIFEST]`

Builds the index once and writes it as one file for a release: the release name, the source and SHA-256 of every document, how the index was built, the index, a digest of all that and, with `--key-file`, an HMAC-SHA256 signature of the digest. The same documents and release give the same file. `serve --bundle FILE` loads it instead of indexing documents on the machine; `--bundle-key-file` names the key the signature is checked with. A bundle whose digest or signature does not match is refused, and so is one that is unsigned or whose signature cannot be checked, unless `--allow-unsigned-bundle` says so (off by default). `GET /stats` shows `release` and `bundleSigned`; `POST /reload` and `SIGHUP` read the file again.

```
$ hydra-umc-docs-qa bundle --release 0.3.0 --docs corpus/ --key-file key.txt --out corpus-0.3.0.json
wrote corpus-0.3.0.json: release 0.3.0, 2 document(s), 21 section(s), signed, digest 5d0f...
$ hydra-umc-docs-qa serve --bundle corpus-0.3.0.json --bundle-key-file key.txt
```

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | ok — including an honest "no relevant passages found" miss; `serve` (clean shutdown via `Ctrl-C`) |
| `1` | no documents were ingested (every `--docs` path was missing or disallowed) |

`serve` itself never exits with `1` for a bad request — a malformed
query or unknown route is a real HTTP error status (`400`/`404`/`503`),
not a process exit; the process itself only stops on `Ctrl-C`.


## `GET /health` (off by default)

A uniform health route, the same in every service of the family. It is closed unless the operator opens it with `serve --health-open` or `HYDRA_UMC_DOCS_QA_HEALTH_OPEN=1`: closed, it answers `404 {"error": "not found"}` like any route that does not exist.

Open, it answers `200` with `{"status": "ok", "service": "hydra-umc-docs-qa", "version": "<version>"}` to a caller on loopback. A caller that is not on loopback must present the bearer token of the service (a service started without a token never answers one). The body never carries a path, a setting or a secret. The health path of the project manifest (`health_path`) is unchanged.

`serve --identity-key-file FILE` (or `HYDRA_UMC_DOCS_QA_IDENTITY_KEY_FILE`; off by default) accepts the signed token of HYDRA-UMC-SERVER next to the token of the service: a viewer may read but not change, and a request with no credential is refused even on loopback, `GET /health` and `GET /stats` included. A container health check then runs `python -m hydra_umc_docs_qa.server_identity probe --port PORT --key-file FILE`, which presents a short-lived token signed with the same key file.
