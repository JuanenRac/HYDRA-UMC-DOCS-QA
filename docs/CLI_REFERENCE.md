# HYDRA-UMC-DOCS-QA — CLI Reference

`hydra-umc-docs-qa` is a Python console script (`src/hydra_umc_docs_qa/main.py`,
installed as an entry point via `pyproject.toml`). Every example below was
captured from a real run of the installed CLI — not written from memory.

## Usage

```
$ hydra-umc-docs-qa -h
usage: hydra-umc-docs-qa [-h] {query} ...

Docs-QA (Hailo-10) - retrieval-augmented technical assistant grounded in the
ecosystem's own documentation.

positional arguments:
  {query}
    query     Real TF-IDF lexical search over ingested Markdown docs.

options:
  -h, --help  show this help message and exit
```

Bare invocation (no subcommand) prints identity/version/role and exits `0`:

```
$ hydra-umc-docs-qa
HYDRA-UMC-DOCS-QA v0.0.5
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

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | ok — including an honest "no relevant passages found" miss |
| `1` | no documents were ingested (every `--docs` path was missing or disallowed) |
