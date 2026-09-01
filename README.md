<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="HYDRA-UMC-DOCS-QA banner" width="100%">
</p>

# 📚 HYDRA-UMC-DOCS-QA

<p align="center">🇺🇸 <b>English</b> | <a href="README_spa.md">🇪🇸 Español</a> | <a href="README_fra.md">🇫🇷 Français</a> | <a href="README_ita.md">🇮🇹 Italiano</a> | <a href="README_deu.md">🇩🇪 Deutsch</a> | <a href="README_zho.md">🇨🇳 简体中文</a> | <a href="README_jpn.md">🇯🇵 日本語</a></p>

### 🤖 RAG-Based AI Assistant for Hardware Maintenance & Documentation

<p align="left">
  <img src="https://img.shields.io/badge/Licencia-GPL%203.0-blue.svg" alt="GPL 3.0">
  <img src="https://img.shields.io/badge/Method-RAG%20%2F%20Vector%20Search-orange.svg" alt="RAG">
  <img src="https://img.shields.io/badge/Platform-Hailo--10-green.svg" alt="Hailo-10">
</p>

---

## 1. 🛠️ TECHNICAL OVERVIEW

**HYDRA-UMC-DOCS-QA** is a specialized Retrieval-Augmented Generation (RAG) assistant designed for on-site technicians and developers. It provides instant, grounded answers to technical questions about the HYDRA-UMC ecosystem.

It has "read" all manuals, schematic documentation, and source code across the ecosystem, allowing it to assist in troubleshooting, pinout queries, and firmware compilation without needing an internet connection.

### Key Features:
* 🔍 **Local Vector Search (v0):** Real, stdlib-only TF-IDF lexical retrieval over local Markdown docs. *(implemented as real lexical search - not yet embedding-based semantic search, and PDF ingestion is still future work; see BUILD & RUN below)*
* 🔒 **Real document allowlist:** only existing `.md`/`.markdown` files are ever ingested and cited - every other `--docs` path (missing, or a disallowed file type) is rejected with a distinct, printed reason instead of being silently skipped or silently read as if it were real documentation. *(implemented)*
* 🔗 **Traceable citations:** every result cites `source#index` - a stable, disambiguating pointer back to the exact ingested passage, deterministically recoverable by re-parsing the same source. *(implemented)*
* 🎯 **Deterministic scoring:** ranking is a pure function of the corpus and query text, independent of the interpreter's per-process hash seed. *(implemented)*
* 🤖 **Grounded Reasoning:** Answers are strictly based on the provided project documentation.
* 🎙️ **Voice Integration:** Integrated with VOICE-UI for hands-free maintenance support.
* 🛠️ **Code Awareness:** Can explain firmware modules and CAN protocol specifics.
* 👨‍👩‍👧 **Cognitive AI Node Child:** Runs as one of four sibling services
  under [HYDRA-UMC-COGNITIVE-NODE](https://github.com/JuanenRac/HYDRA-UMC-COGNITIVE-NODE)
  (alongside VLA-Engine, Voice-UI and Semantic-Planner), sharing its
  parent's HydraOS image and model weights instead of keeping its own
  copies.
* 📦 **Odometer Versioning:** Every real build bumps `pyproject.toml`'s
  own version automatically (`bump_version.py`) - no manual version edits.

---

## 2. 🔄 RAG PIPELINE FLOW

```mermaid
flowchart LR
    Q["User Question"] --> VEC["Vector Query"]
    DB[("Project Knowledge Base")] --> VEC
    VEC --> CONTEXT["Contextual Snippets"]
    CONTEXT --> LLM["Hailo-10 LLM"]
    LLM --> ANS["Grounded Technical Answer"]
```

---

## 3. 🧱 ARCHITECTURE & DESIGN DECISIONS

This repository is a **child** of the Cognitive AI Node family - its
parent, [HYDRA-UMC-COGNITIVE-NODE](https://github.com/JuanenRac/HYDRA-UMC-COGNITIVE-NODE),
owns the shared HydraOS image and quantized model weights, and wires this
service into `docker-compose.yml` alongside its three siblings
(VLA-Engine, Voice-UI, Semantic-Planner):

* **Why this child has no hardware/firmware/`os/`/`models/` of its
  own.** It runs entirely on the CM5 + Hailo-10 M.2 module already owned
  by the parent - keeping model weights and the HydraOS image
  centralized in one place avoids four divergent multi-gigabyte copies
  across the family.
* **Why a `src/` layout.** Keeps the installable package
  (`hydra_umc_docs_qa`) separate from repo-root tooling
  (`bump_version.py`), matching the layout used by every other Python
  project across the ecosystem.
* **Why the entry point only prints identity/version/role today.** This
  is the andamiaje (scaffolding) stage: proving the package installs,
  compiles and imports cleanly - on the actual target Python version - is
  a prerequisite for adding real vector-search/RAG ingestion logic later,
  and keeps that later work isolated from packaging concerns.
* **How this fits the rest of the ecosystem.** This assistant grounds
  its answers in the ecosystem's own documentation, giving its sibling
  HYDRA-UMC-SEMANTIC-PLANNER a technical-knowledge source it can query
  during reasoning, and offering on-site technicians hands-free
  troubleshooting support alongside HYDRA-UMC-VOICE-UI.
* **Why `index.py`'s retrieval is real TF-IDF, not an embedding model.**
  Embedding-based semantic search needs a real model dependency (and
  ideally the Hailo-10 this project's own README already targets) - a
  pure-stdlib TF-IDF/cosine-similarity index is real, testable, and
  needs nothing beyond Python itself, giving this project a working
  retrieval kernel today that a future embedding-based index can be
  swapped in behind the same `search()` contract without touching the
  CLI or the ingestion step.
* **Why an unmatched query returns an honest miss, not a fallback
  answer.** v0 has no LLM synthesis step - a question whose words don't
  overlap the ingested corpus gets `No relevant passages found`
  (see `main.py`), never a guessed or hallucinated response dressed up
  as a real retrieval result.
* **Why a disallowed `--docs` path is rejected, not silently skipped or
  silently ingested.** A QA assistant that's supposed to be "grounded in
  the ecosystem's own documentation" must not quietly read and cite
  whatever file a caller happens to point it at - a missing path or a
  non-Markdown file (a stray `.env`, a firmware binary) gets a real,
  distinct `REJECTED ... : <reason>` line (see `ingest.py`'s
  `validate_doc_path`) instead of an aggregate "found nothing" that
  hides *why*.
* **Why cosine similarity sorts the shared terms before summing them.**
  `dict.keys() & dict.keys()` is a set, and CPython's set iteration order
  depends on per-process string hash randomization - summing
  floating-point terms in that order would make a score depend on which
  process happened to run the query, not only on the corpus and query
  text. Sorting first makes the score a pure, reproducible function of
  its real inputs.
* **Why citations carry a `#index`, not just a filename.** A bare
  filename can't disambiguate two sections sharing a heading (or, later,
  two ingested files sharing a name) - `DocChunk.index` is each chunk's
  real ordinal position within its own source, giving every citation a
  stable key a caller can use to deterministically recover the exact
  passage again.

---

## 📂 DIRECTORY STRUCTURE

```text
HYDRA-UMC-DOCS-QA/
├── src/hydra_umc_docs_qa/
│   ├── ingest.py            # Real Markdown ingestion -> heading-scoped DocChunks
│   ├── index.py              # Real stdlib-only TF-IDF index + cosine-similarity search
│   └── main.py                # Entry point + real `query` subcommand
├── tests/                   # Real tests: ingestion, allowlist, ranking, determinism, end-to-end CLI
├── docs/                    # Documentation and technical manuals
├── images/                  # Media and diagrams
├── scripts/                 # Utility scripts
├── build/                   # Local build output (git-ignored)
├── pyproject.toml           # Package metadata (version odometer-bumped on every real build)
├── bump_version.py          # Odometer-style version bump (used by build.sh/.bat)
├── build.sh / build.bat     # Create venv, install (with dev extras), verify import, run tests
└── run.sh / run.bat         # Run the entry point (forwards args, e.g. `query`)
```

> **Note:** `hardware/` and `firmware/` were pruned - this node runs on an
> existing CM5 + Hailo-10 M.2 module with no hardware/firmware design of
> its own. `os/` and `models/` were also pruned - the HydraOS image and
> the shared Hailo-10 model weights live in the parent
> `HYDRA-UMC-COGNITIVE-NODE`, which this project attaches to as a
> service (see its `docker-compose.yml`).

---

## ⚙️ BUILD & RUN GUIDE

Requires Python >= 3.10.

```bash
# Linux / macOS / Git Bash
./build.sh   # creates .venv, installs the package (editable), verifies import
./run.sh     # runs the entry point

# Windows (cmd)
build.bat
run.bat
```

`build.sh`/`build.bat` bump the version (odometer-style, see
`bump_version.py`) before every real build, and run the real test suite
(`pytest tests/`). Expected output of a bare `run.sh`:

```text
HYDRA-UMC-DOCS-QA v0.0.4
Docs-QA (Hailo-10) - retrieval-augmented technical assistant grounded in the ecosystem's own documentation.
```

The real `query` subcommand searches ingested Markdown - by default this
repo's own `README.md`/`CHANGELOG.md`, or any real files passed via
`--docs`:

```bash
./run.sh query "CAN bus wiring" --top-k 3
./run.sh query "vector search retrieval" --docs docs/manual.md --docs docs/spec.md

# Windows
run.bat query "CAN bus wiring" --top-k 3
```

A question whose words don't overlap the ingested corpus prints an
honest `No relevant passages found` - v0 is real lexical retrieval, not
a generative answer.

Each result cites `source#index` (e.g. `manual.md#1`) - a stable,
traceable pointer to that exact chunk. A `--docs` path that's missing or
not `.md`/`.markdown` is rejected and reported, not silently ignored:

```text
$ ./run.sh query "firmware flashing" --docs manual.md ghost.md secret.env
REJECTED ghost.md: file not found (no source)
REJECTED secret.env: disallowed extension .env - only .markdown, .md are ingested
Top 1 passage(s) for: "firmware flashing"

1. [0.289] manual.md#1 - Firmware Flashing
   Flash URTC firmware over SWD or JTAG using URTC-FLASHER.
```

### 🩺 Troubleshooting

* **`python: command not found` / build fails at step 1.** Requires
  Python >= 3.10 on `PATH`. On Windows, install from
  [python.org](https://python.org) and make sure "Add to PATH" was
  checked during setup; `python3` is the usual name on Linux/macOS.
* **`build.sh` fails to activate the venv.** `python3 -m venv .venv`
  lays out the activate script differently per platform:
  `.venv/bin/activate` on Linux/macOS, `.venv/Scripts/activate` on
  Windows (also true for a Windows Python venv used from Git Bash).
  `build.sh` already checks both paths - if it still fails, delete
  `.venv/` and re-run `./build.sh` to rebuild it from scratch.
* **`pip install -e .` fails.** Usually a stale `.venv/`. Delete the
  `.venv/` folder and re-run `./build.sh`/`build.bat` to recreate it.
* **`import OK` never prints.** Means `python -c "import
  hydra_umc_docs_qa"` itself failed - re-run with the venv active to
  see the real traceback.

---

## 🚀 ROADMAP
* **Phase 1:** VLA engine deployment and multi-modal input processing on Hailo-10.
* **Phase 2:** Semantic planner integration with swarm behavioral models and long-term memory.
* **Phase 3:** Voice UI low-latency local execution and industrial noise cancellation.
* **Phase 4:** Interactive schematic visualization linked to QA answers and RAG optimization for edge devices.

---

## 🔗 RELATED PROJECTS

This project is part of a larger robotics ecosystem by the same author (JuanenRac / Electro Hobby 3D), spanning firmware, control software, AI nodes, and fleet tooling.

### Family

**Parent:** **[HYDRA-UMC-COGNITIVE-NODE](https://github.com/JuanenRac/HYDRA-UMC-COGNITIVE-NODE)** — the Integration Hub that owns this assistant's shared HydraOS image/weights and wires it into the cognitive workflow.

**Siblings:**
- **[HYDRA-UMC-VOICE-UI](https://github.com/JuanenRac/HYDRA-UMC-VOICE-UI)** — STT/TTS gateway for the shared planner this assistant also grounds.
- **[HYDRA-UMC-SEMANTIC-PLANNER](https://github.com/JuanenRac/HYDRA-UMC-SEMANTIC-PLANNER)** — the LLM planner this assistant's RAG answers feed into.
- **[HYDRA-UMC-VLA-ENGINE](https://github.com/JuanenRac/HYDRA-UMC-VLA-ENGINE)** — turns vision data into action tokens for the same planner.

This assistant has no relations outside its own family beyond what's covered above.

### Rest of the Ecosystem

**HYDRA-UMC platform** — the multi-robot micro-factory cell
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — the motherboard itself: Raspberry Pi CM5 host + dual-core STM32H745 real-time co-processor, orchestrating up to 8 distributed robot arms over CAN-OTA/SPI-OTA.
- **[HYDRA-UMC SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — headless Express/WebSocket backend that owns robot state.
- **[HYDRA-UMC STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** — web-based control dashboard.
- **[HYDRA-UMC-ANDROID-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-ANDROID-CONTROL)** — Android control app for HYDRA-UMC.
- **[HYDRA-UMC-IOS-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-IOS-CONTROL)** — iOS/iPadOS control app for HYDRA-UMC.
- **[HYDRA-UMC-SUITE](https://github.com/JuanenRac/HYDRA-UMC-SUITE)** — desktop swarm command center.
- **[HYDRA-UMC-EDITOR-URDF](https://github.com/JuanenRac/HYDRA-UMC-EDITOR-URDF)** — desktop graphical URDF creator/editor.
- **[HYDRA-UMC-DSI](https://github.com/JuanenRac/HYDRA-UMC-DSI)** — native touchscreen UI for HYDRA-UMC.

**URTC platform** — the tool head controller every HYDRA-UMC robot arm carries
- **[URTC](https://github.com/JuanenRac/URTC)** — Universal Robot Tool Controller, firmware.
- **[URTC Flasher](https://github.com/JuanenRac/URTC-FLASHER)** — desktop CAN-OTA + SWD/JTAG flashing tool.
- **[URTC Tester](https://github.com/JuanenRac/URTC-TESTER)** — desktop live CAN-bus diagnostic tool.
- **[URTC Web Studio](https://github.com/JuanenRac/URTC-WEB-STUDIO)** — browser-based alternative to the 2 desktop tools above.

**👁️ Vision AI Node (Hailo-8)**
- [HYDRA-UMC-VISION-NODE](https://github.com/JuanenRac/HYDRA-UMC-VISION-NODE)
- [HYDRA-UMC-VISION-STREAMER](https://github.com/JuanenRac/HYDRA-UMC-VISION-STREAMER)
- [HYDRA-UMC-DETECTION-HEF](https://github.com/JuanenRac/HYDRA-UMC-DETECTION-HEF)
- [HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)
- [HYDRA-UMC-VISUAL-SERVOING-API](https://github.com/JuanenRac/HYDRA-UMC-VISUAL-SERVOING-API)

**🐝 Orchestration & Swarm**
- [HYDRA-UMC-ORCHESTRATOR](https://github.com/JuanenRac/HYDRA-UMC-ORCHESTRATOR)
- [HYDRA-UMC-SWARM-SYNC](https://github.com/JuanenRac/HYDRA-UMC-SWARM-SYNC)
- [HYDRA-UMC-PATH-PLANNER-3D](https://github.com/JuanenRac/HYDRA-UMC-PATH-PLANNER-3D)
- [HYDRA-UMC-JOB-DISPATCHER](https://github.com/JuanenRac/HYDRA-UMC-JOB-DISPATCHER)
- [HYDRA-UMC-NODE-HEALING](https://github.com/JuanenRac/HYDRA-UMC-NODE-HEALING)

**🎮 Digital Twin & Simulation**
- [HYDRA-UMC-TWIN](https://github.com/JuanenRac/HYDRA-UMC-TWIN)
- [HYDRA-UMC-PHYSICS-REPLICA](https://github.com/JuanenRac/HYDRA-UMC-PHYSICS-REPLICA)
- [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)
- [HYDRA-UMC-SYNTHETIC-DATA-GEN](https://github.com/JuanenRac/HYDRA-UMC-SYNTHETIC-DATA-GEN)

**📊 Data & Analytics**
- [HYDRA-UMC-DATALAKE](https://github.com/JuanenRac/HYDRA-UMC-DATALAKE)
- [HYDRA-UMC-TELEMETRY-COLLECTOR](https://github.com/JuanenRac/HYDRA-UMC-TELEMETRY-COLLECTOR)
- [HYDRA-UMC-ANOMALY-DETECTOR](https://github.com/JuanenRac/HYDRA-UMC-ANOMALY-DETECTOR)
- [HYDRA-UMC-PRODUCTION-REPORTS](https://github.com/JuanenRac/HYDRA-UMC-PRODUCTION-REPORTS)

**🏭 Industrial Gateway**
- [HYDRA-UMC-GATEWAY-INDUSTRIAL](https://github.com/JuanenRac/HYDRA-UMC-GATEWAY-INDUSTRIAL)
- [HYDRA-UMC-OPCUA-SERVER](https://github.com/JuanenRac/HYDRA-UMC-OPCUA-SERVER)
- [HYDRA-UMC-MQTT-BROKER](https://github.com/JuanenRac/HYDRA-UMC-MQTT-BROKER)
- [HYDRA-UMC-MTCONNECT-ADAPTER](https://github.com/JuanenRac/HYDRA-UMC-MTCONNECT-ADAPTER)

**🛠️ Complementary Tools**
- [URTC-SMART-RACK](https://github.com/JuanenRac/URTC-SMART-RACK)
- [URTC-VISION-TOOL](https://github.com/JuanenRac/URTC-VISION-TOOL)
- [HYDRA-UMC-WATCH](https://github.com/JuanenRac/HYDRA-UMC-WATCH)
- [HYDRA-UMC-TOOL-CLI](https://github.com/JuanenRac/HYDRA-UMC-TOOL-CLI)
- [HYDRA-UMC-DASHBOARD-AI](https://github.com/JuanenRac/HYDRA-UMC-DASHBOARD-AI)

---

## 👤 AUTHOR
**JuanenRac** (Electro Hobby 3D)
📧 electrohobby3d@gmail.com

## 📜 LICENSE
GPL-3.0 - See LICENSE for details.
