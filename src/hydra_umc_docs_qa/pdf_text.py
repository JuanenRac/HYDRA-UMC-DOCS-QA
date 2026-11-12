# =============================================================================
# HYDRA-UMC-DOCS-QA - src/hydra_umc_docs_qa/pdf_text.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The text of a PDF, read with the standard library alone.

This reads the PDFs that carry their text as text: the page content streams (plain or Flate-compressed) are scanned for the
text-showing operators (`Tj`, `TJ`, `'`, `"`) and the strings they show are decoded as Latin-1 (WinAnsi) or UTF-16BE.
It does NOT do OCR, so a scanned page (an image) gives no text, and it does not read the font maps (`ToUnicode`), so a PDF
whose fonts use custom glyph codes (many subset CID fonts) gives garbled or empty text; such a page is left out and a document
with no readable page is refused with a reason (`PdfError`) instead of being indexed as noise. Encrypted PDFs are refused.

The reading is bounded: a size limit on every decompressed stream and on the whole document, and a limit on the pages, so a
hostile file cannot make a scan unbounded (a "zip bomb" inside a PDF stream is cut at the limit).
"""
from __future__ import annotations

import re
import zlib

MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PAGES = 2000
# a gap in a TJ array wider than this (thousandths of an em) is a space between words
_SPACE_GAP = 250


class PdfError(ValueError):
    """A PDF that cannot be read for text, with the reason."""


_OBJ_RE = re.compile(rb"(?<![0-9])(\d{1,9})\s+(\d{1,5})\s+obj\b(.*?)\bendobj", re.DOTALL)
_STREAM_RE = re.compile(rb"stream\r?\n")
_PAGE_RE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")
_PAGES_RE = re.compile(rb"/Type\s*/Pages(?![A-Za-z])")
_CATALOG_RE = re.compile(rb"/Type\s*/Catalog")
_REF = rb"(\d+)\s+\d+\s+R"
_CONTENTS_RE = re.compile(rb"/Contents\s*(?:" + _REF + rb"|\[(.*?)\])", re.DOTALL)
_KIDS_RE = re.compile(rb"/Kids\s*\[(.*?)\]", re.DOTALL)
_ROOT_PAGES_RE = re.compile(rb"/Pages\s*" + _REF)
_REF_RE = re.compile(_REF)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _inflate(data: bytes, budget: list[int]) -> bytes | None:
    """Decompress a Flate stream up to the limits; None when it is damaged or over the limits."""
    decompressor = zlib.decompressobj()
    try:
        out = decompressor.decompress(data, MAX_STREAM_BYTES)
    except zlib.error:
        return None
    if decompressor.unconsumed_tail:
        return None  # larger than the limit
    budget[0] -= len(out)
    if budget[0] < 0:
        raise PdfError("the document expands to more than the limit of decompressed data")
    return out


def _objects(data: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    """Object number -> (dictionary text, raw stream or None)."""
    objects: dict[int, tuple[bytes, bytes | None]] = {}
    for match in _OBJ_RE.finditer(data):
        number = int(match.group(1))
        body = match.group(3)
        stream_match = _STREAM_RE.search(body)
        if stream_match is None:
            objects[number] = (body, None)
            continue
        end = body.rfind(b"endstream")
        raw = body[stream_match.end() : end if end >= 0 else len(body)]
        objects[number] = (body[: stream_match.start()], raw.rstrip(b"\r\n"))
    return objects


def _content_streams(objects: dict[int, tuple[bytes, bytes | None]], budget: list[int]) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    for number, (dictionary, raw) in objects.items():
        if raw is None:
            continue
        if b"/FlateDecode" in dictionary or b"/Fl " in dictionary:
            content = _inflate(raw, budget)
            if content is None:
                continue
        elif b"/Filter" in dictionary:
            continue  # another filter (LZW, ASCII85, JPEG, ...): not text we can read
        else:
            content = raw
        if b"BT" in content and b"ET" in content:
            out[number] = content
    return out


def _page_order(objects: dict[int, tuple[bytes, bytes | None]]) -> list[int]:
    """The page objects in document order (the Kids tree from the catalog), else by object number."""
    pages = sorted(n for n, (d, _) in objects.items() if _PAGE_RE.search(d))
    root = next((m.group(1) for n, (d, _) in objects.items() if _CATALOG_RE.search(d) for m in [_ROOT_PAGES_RE.search(d)] if m), None)
    if root is None:
        return pages
    ordered: list[int] = []
    seen: set[int] = set()

    def walk(number: int) -> None:
        if number in seen or number not in objects:
            return
        seen.add(number)
        dictionary = objects[number][0]
        kids = _KIDS_RE.search(dictionary)
        if _PAGES_RE.search(dictionary) and kids:
            for ref in _REF_RE.finditer(kids.group(1)):
                walk(int(ref.group(1)))
        elif _PAGE_RE.search(dictionary):
            ordered.append(number)

    walk(int(root))
    rest = [n for n in pages if n not in ordered]
    return ordered + rest


_ESCAPES = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12, ord("("): 40, ord(")"): 41, ord("\\"): 92}
_WHITESPACE = b" \t\r\n\x0c\x00"
_DELIMS = b"()<>[]{}/%"
_ARRAY = object()  # marks where an array starts among the operands


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    return raw.decode("cp1252", errors="replace")


def _read_string(buf: bytes, pos: int) -> tuple[bytes, int]:
    """A literal string starting after its opening parenthesis."""
    out = bytearray()
    depth = 1
    n = len(buf)
    while pos < n:
        c = buf[pos]
        if c == 0x5C and pos + 1 < n:  # backslash
            nxt = buf[pos + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                pos += 2
            elif 0x30 <= nxt <= 0x37:
                j = pos + 1
                while j < n and j < pos + 4 and 0x30 <= buf[j] <= 0x37:
                    j += 1
                out.append(int(buf[pos + 1 : j], 8) & 0xFF)
                pos = j
            elif nxt in b"\r\n":
                pos += 2
                if nxt == 0x0D and pos < n and buf[pos] == 0x0A:
                    pos += 1
            else:
                out.append(nxt)
                pos += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), pos + 1
        out.append(c)
        pos += 1
    return bytes(out), pos


def text_of_content(content: bytes) -> str:
    """The text a page content stream shows, lines broken where the text moves down."""
    out: list[str] = []
    operands: list[object] = []
    pos, n = 0, len(content)
    while pos < n:
        c = content[pos]
        if len(operands) > 4096:
            operands = []  # a stream that never shows anything: do not let the operands grow without limit
        if c in _WHITESPACE:
            pos += 1
        elif c == 0x25:  # comment
            while pos < n and content[pos] not in b"\r\n":
                pos += 1
        elif c == 0x28:
            raw, pos = _read_string(content, pos + 1)
            operands.append(raw)
        elif c == 0x3C and content[pos + 1 : pos + 2] != b"<":  # hex string
            end = content.find(b">", pos)
            end = n if end < 0 else end
            digits = re.sub(rb"[^0-9A-Fa-f]", b"", content[pos + 1 : end])
            if len(digits) % 2:
                digits += b"0"
            operands.append(bytes.fromhex(digits.decode("ascii")))
            pos = end + 1
        elif c == 0x5B:  # [
            operands.append(_ARRAY)
            pos += 1
        elif c == 0x5D:  # ]
            items: list[object] = []
            while operands and operands[-1] is not _ARRAY:
                items.append(operands.pop())
            if operands:
                operands.pop()
            operands.append(list(reversed(items)))
            pos += 1
        elif c in b"<>{}":
            pos += 1 if content[pos : pos + 2] not in (b"<<", b">>") else 2
        elif c == 0x2F:  # name
            j = pos + 1
            while j < n and content[j] not in _WHITESPACE and content[j] not in _DELIMS:
                j += 1
            operands.append(content[pos:j])
            pos = j
        else:
            j = pos
            while j < n and content[j] not in _WHITESPACE and content[j] not in _DELIMS:
                j += 1
            token = content[pos:j]
            pos = max(j, pos + 1)
            try:
                operands.append(float(token))
                continue
            except ValueError:
                pass
            _operator(token, operands, out)
            operands = []
    return "".join(out)


def _operator(op: bytes, operands: list[object], out: list[str]) -> None:
    if op == b"Tj" and operands and isinstance(operands[-1], bytes):
        out.append(_decode(operands[-1]))
    elif op in (b"'", b'"') and operands and isinstance(operands[-1], bytes):
        out.append("\n" + _decode(operands[-1]))
    elif op == b"TJ" and operands and isinstance(operands[-1], list):
        for item in operands[-1]:
            if isinstance(item, bytes):
                out.append(_decode(item))
            elif isinstance(item, float) and item < -_SPACE_GAP:
                out.append(" ")
    elif op in (b"Td", b"TD") and len(operands) >= 2 and isinstance(operands[-1], float):
        out.append("\n" if operands[-1] != 0 else " ")
    elif op in (b"T*", b"ET"):
        out.append("\n")
    elif op == b"Tm":
        out.append("\n")


def pdf_pages(data: bytes) -> list[str]:
    """The text of each page that has any, in document order. Raises PdfError when the file is not a readable PDF."""
    if not data.lstrip().startswith(b"%PDF-") and b"%PDF-" not in data[:1024]:
        raise PdfError("not a PDF (no %PDF- header)")
    if re.search(rb"/Encrypt\s*(?:\d+\s+\d+\s+R|<<)", data):
        raise PdfError("the PDF is encrypted")
    budget = [MAX_TOTAL_BYTES]
    objects = _objects(data)
    streams = _content_streams(objects, budget)
    order = _page_order(objects)[:MAX_PAGES]
    texts: list[str] = []
    for page in order:
        match = _CONTENTS_RE.search(objects[page][0])
        refs: list[int] = []
        if match:
            if match.group(1):
                refs = [int(match.group(1))]
            else:
                refs = [int(m.group(1)) for m in _REF_RE.finditer(match.group(2))]
        texts.append("\n".join(text_of_content(streams[r]) for r in refs if r in streams))
    if not order:
        # no page tree we can follow (page objects inside object streams): every content stream is a page, in file order
        texts = [text_of_content(streams[n]) for n in sorted(streams)[:MAX_PAGES]]
    cleaned = []
    for text in texts:
        text = _CONTROL_RE.sub("", text.replace("\r", "\n"))
        lines = [" ".join(line.split()) for line in text.split("\n")]
        cleaned.append("\n".join(line for line in lines if line))
    if not any(len(t.strip()) >= 2 for t in cleaned):
        raise PdfError("no readable text (a scanned page, or fonts with custom glyph codes; this reader does no OCR)")
    return cleaned
