# HYDRA-UMC-DOCS-QA — CLI Reference

`hydra-umc-docs-qa` is a Python console script (`src/hydra_umc_docs_qa/main.py`,
installed as an entry point via `pyproject.toml`). Every example below was
captured from a real run of the installed CLI — not written from memory.

## Usage

```
$ hydra-umc-docs-qa -h
usage: hydra-umc-docs-qa [-h] {query,serve} ...

Docs-QA (Hailo-10) - retrieval-augmented technical assistant grounded in the
ecosystem's own documentation.

positional arguments:
  {query,serve}
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
HYDRA-UMC-DOCS-QA v0.0.7
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

1. [0.179] README.md#3 - 1. TECHNICAL OVERVIEW
   HYDRA-UMC-DOCS-QA is a specialized Retrieval-Augmented Generation (RAG) assistant designed for on-site technicians and developers. It provides instant, grou...

2. [0.107] README.md#10 - Windows (cmd)
   build.bat run.bat  build.sh/build.bat bump the version (odometer-style, see bump_version.py) before every real build, and run the real test suite (py...
```

Each citation is `source#index` — a stable pointer to the exact ingested
chunk (its 0-based ordinal position within that source file), not just
an ambiguous filename. Re-parsing the same source at the same index
always recovers the identical passage.

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

`GET /stats` — which documents this server instance actually indexed at
startup, and which were rejected:

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

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | ok — including an honest "no relevant passages found" miss; `serve` (clean shutdown via `Ctrl-C`) |
| `1` | no documents were ingested (every `--docs` path was missing or disallowed) |

`serve` itself never exits with `1` for a bad request — a malformed
query or unknown route is a real HTTP error status (`400`/`404`/`503`),
not a process exit; the process itself only stops on `Ctrl-C`.
