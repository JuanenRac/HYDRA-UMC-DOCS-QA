# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/catalog.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The error codes of the controller and the tool profiles of URTC as structured documents.

"What does the status 0x05 mean?" should be answered with the exact definition and its source, not with a passage that happens to share a
word. This module reads two files that are the source of truth (it never writes to them):

* the Python mirror of the bootloader protocol of the controller (`protocol.py`): its `STATUS_*` codes and `VERIFY_FAIL_REASON_*` codes;
* the tool profiles of URTC (`spec/tools.json`): one profile per tool, with its range, watchdog and pins.

and turns them into two Markdown documents, one section per code or tool, that the normal ingestion indexes and cites with the hash of the
document. `lookup_code` then answers a code exactly (by name or by number), from the indexed sections, with the citation.

The meaning of a code is written here (the source only has the name and the number); a code the source defines and this table does not know
is still listed, with a line saying that no description was written for it, so the catalog never invents one.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .index import TfidfIndex

MAX_SOURCE_BYTES = 2 * 1024 * 1024

KIND_STATUS = "controller bootloader status"
KIND_VERIFY_FAIL = "controller image verification failure reason"
KIND_TOOL = "URTC tool profile"

MCU_DOCUMENT = "mcu-error-codes.md"
TOOLS_DOCUMENT = "urtc-tool-profiles.md"

# what each code means; the protocol file only has the name and the number
DESCRIPTIONS: dict[str, str] = {
    "STATUS_LISTENING": "The bootloader is waiting for an update to start.",
    "STATUS_ERASING": "The bootloader is erasing the flash area that will receive the new image.",
    "STATUS_RECEIVING": "The bootloader is receiving the pages of the new image.",
    "STATUS_VERIFYING": "The bootloader is checking the image it received.",
    "STATUS_COPYING": "The bootloader is copying the verified image to its place.",
    "STATUS_VERIFY_OK": "The image passed every check.",
    "STATUS_VERIFY_FAIL": "The image failed a check; the reason is one of the VERIFY_FAIL_REASON codes.",
    "STATUS_ROLLBACK_SUSPECT": "The bootloader suspects a rollback to an older image than the one that was running.",
    "STATUS_ERROR": "The bootloader stopped on an error.",
    "VERIFY_FAIL_REASON_INCOMPLETE": "The image is incomplete: not every page arrived.",
    "VERIFY_FAIL_REASON_CRC32": "The CRC-32 of the image does not match the one in its header.",
    "VERIFY_FAIL_REASON_HMAC": "The HMAC of the image does not match: it was not signed with the expected key.",
    "VERIFY_FAIL_REASON_HARDWARE_ID": "The image is for another board than this one.",
    "VERIFY_FAIL_REASON_ROLLBACK": "The image is older than the one allowed by the anti-rollback counter.",
    "VERIFY_FAIL_REASON_HEADER": "The header of the image is malformed.",
}

_CODE_LINE = re.compile(r"^(STATUS_[A-Z0-9_]+|VERIFY_FAIL_REASON_[A-Z0-9_]+)\s*=\s*(0[xX][0-9A-Fa-f]+|\d+)\b", re.MULTILINE)
# a section heading written by this module: NAME (0xNN)
_HEADING = re.compile(r"^(?P<name>[A-Za-z0-9_]+) \((?P<hex>0x[0-9A-F]{2,4})\)$")


class CatalogError(ValueError):
    """A source file cannot be read as what it should be."""


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    value: int
    kind: str
    text: str

    @property
    def heading(self) -> str:
        return f"{self.name} (0x{self.value:02X})"


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise CatalogError(f"{path.name} is larger than {MAX_SOURCE_BYTES} bytes")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CatalogError(f"cannot read {path.name}: {error}") from error


def parse_mcu_codes(protocol_text: str) -> list[CatalogEntry]:
    """The status and verification-failure codes defined in the text of the protocol file, in the order of the file."""
    entries = []
    for name, number in _CODE_LINE.findall(protocol_text):
        kind = KIND_STATUS if name.startswith("STATUS_") else KIND_VERIFY_FAIL
        meaning = DESCRIPTIONS.get(name, "No description was written for this code yet; see the protocol file it comes from.")
        entries.append(CatalogEntry(name=name, value=int(number, 0), kind=kind, text=meaning))
    return entries


def _limit(limits: object) -> str:
    if isinstance(limits, dict) and {"min", "max", "default"} <= limits.keys():
        return f"{limits['min']} to {limits['max']} (default {limits['default']})"
    return "not stated"


def parse_tool_profiles(raw: object) -> list[CatalogEntry]:
    """One entry per tool of the URTC tool table (`spec/tools.json`)."""
    tools = raw.get("tools") if isinstance(raw, dict) else None
    if not isinstance(tools, list):
        raise CatalogError('the tool profile file must be an object with a "tools" list')
    entries = []
    for tool in tools:
        if not (isinstance(tool, dict) and isinstance(tool.get("id"), int) and isinstance(tool.get("name"), str)):
            raise CatalogError("a tool profile needs an integer id and a name")
        pins = tool.get("pins_used")
        features = tool.get("features")
        watchdog = tool.get("watchdog_ms")
        lines = [
            f"{tool.get('display_name') or tool['name']}: {tool.get('description', '')}".strip(),
            f"Kind: {tool.get('kind', 'unknown')}. Range: {_limit(tool.get('limits'))}. "
            f"Watchdog: {f'{watchdog} ms' if isinstance(watchdog, int) else 'none'}. "
            f"Expansion board: {tool.get('expansion_board', 'none')}.",
        ]
        if isinstance(pins, list) and pins:
            lines.append("Pins: " + "; ".join(str(p) for p in pins) + ".")
        if isinstance(features, list) and features:
            lines.append("Features: " + "; ".join(str(f) for f in features) + ".")
        entries.append(CatalogEntry(name=tool["name"], value=tool["id"], kind=KIND_TOOL, text="\n".join(lines)))
    return entries


def render_document(title: str, intro: str, entries: list[CatalogEntry]) -> str:
    """The Markdown the ingestion indexes: one `## NAME (0xNN)` section per entry, with its kind on the first line."""
    parts = [f"# {title}\n\n{intro}\n"]
    for entry in entries:
        parts.append(f"\n## {entry.heading}\n\nKind: {entry.kind}.\n\n{entry.text}\n")
    return "".join(parts)


def build_catalog(protocol_py: Path, tools_json: Path | None, out_dir: Path) -> list[Path]:
    """Writes the documents to `out_dir` (created if needed) and returns their paths. The sources are only read."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    mcu = parse_mcu_codes(_read(protocol_py))
    if not mcu:
        raise CatalogError("no STATUS_ or VERIFY_FAIL_REASON_ code found in the protocol file")
    target = out_dir / MCU_DOCUMENT
    target.write_text(render_document(
        "Controller error and status codes",
        "The status codes the bootloader of the controller reports and the reasons an image fails its verification, read from the protocol file.",
        mcu,
    ), encoding="utf-8", newline="\n")
    written.append(target)
    if tools_json is not None:
        try:
            raw = json.loads(_read(tools_json))
        except ValueError as error:
            raise CatalogError(f"{tools_json.name} is not JSON: {error}") from error
        target = out_dir / TOOLS_DOCUMENT
        target.write_text(render_document(
            "URTC tool profiles",
            "The tools of URTC with their range, watchdog, pins and features, read from the tool table.",
            parse_tool_profiles(raw),
        ), encoding="utf-8", newline="\n")
        written.append(target)
    return written


def normalise_code(code: str) -> tuple[str | None, int | None]:
    """A code typed by a person or a recogniser: a name (case does not matter, spaces count as underscores) or a number (0x05, 5)."""
    text = code.strip()
    if not text or len(text) > 64:
        return None, None
    if re.fullmatch(r"0[xX][0-9A-Fa-f]{1,4}|\d{1,5}", text):
        return None, int(text, 0)
    name = re.sub(r"[\s-]+", "_", text).upper()
    return (name, None) if re.fullmatch(r"[A-Z0-9_]+", name) else (None, None)


def lookup_code(index: TfidfIndex, code: str) -> list[dict[str, object]]:
    """Every section of the indexed catalog documents that defines this code, with its citation. Empty when none does (never a guess).

    A number can be defined by more than one family (a status and a failure reason); every match is returned and the kind says which."""
    name, number = normalise_code(code)
    if name is None and number is None:
        return []
    found = []
    for chunk in index.chunks:
        match = _HEADING.match(chunk.heading)
        if match is None:
            continue
        if (name is not None and match.group("name").upper() == name) or (number is not None and int(match.group("hex"), 16) == number):
            kind = re.search(r"^Kind: (.+?)\.$", chunk.text, re.MULTILINE)
            found.append({
                "name": match.group("name"),
                "value": int(match.group("hex"), 16),
                "kind": kind.group(1) if kind else "unknown",
                "text": chunk.text.strip(),
                "citation": chunk.citation,
            })
    return found
