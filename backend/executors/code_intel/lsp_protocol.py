"""Pure LSP helpers — JSON-RPC Content-Length framing + param/response shaping.

No I/O, no process management → fully unit-testable. The stateful transport
lives in lsp_engine.py.

Coordinate convention: our engine boundary is 1-based line + 1-based character
(editor convention, matching jedi_engine). LSP is 0-based for both, so we
subtract 1 going in and add 1 coming out.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import pathname2url

from executors.code_intel.engine import Location


# --- JSON-RPC framing -------------------------------------------------------

def encode_frame(message: dict) -> bytes:
    body = json.dumps(message).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def iter_frames(buffer: bytes) -> tuple[list[dict], bytes]:
    """Parse every complete Content-Length frame in buffer.
    Returns (messages, remaining_buffer) — remaining holds a partial frame."""
    messages: list[dict] = []
    while True:
        sep = buffer.find(b"\r\n\r\n")
        if sep == -1:
            break
        header = buffer[:sep].decode("ascii", errors="replace")
        length = None
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    length = None
        if length is None:
            buffer = buffer[sep + 4:]      # malformed header → skip it
            continue
        start = sep + 4
        if len(buffer) < start + length:
            break                          # body not fully arrived yet
        body = buffer[start:start + length]
        try:
            messages.append(json.loads(body))
        except ValueError:
            pass
        buffer = buffer[start + length:]
    return messages, buffer


# --- URI <-> path -----------------------------------------------------------

def path_to_uri(path) -> str:
    return "file://" + pathname2url(str(Path(path)))


def uri_to_path(uri: str) -> str:
    return unquote(urlparse(uri).path)


# --- message + param builders ----------------------------------------------

def request(req_id: int, method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}


def notification(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize_params(root: str) -> dict:
    uri = path_to_uri(root)
    return {
        "processId": None,
        "rootUri": uri,
        "capabilities": {},
        "workspaceFolders": [{"uri": uri, "name": "root"}],
    }


def did_open_params(file: str, text: str, language_id: str) -> dict:
    return {"textDocument": {
        "uri": path_to_uri(file), "languageId": language_id, "version": 1, "text": text,
    }}


def did_change_params(file: str, text: str, version: int) -> dict:
    """Full-document sync — re-send the whole text so a warm server sees the
    bot's latest on-disk edits before a query."""
    return {
        "textDocument": {"uri": path_to_uri(file), "version": version},
        "contentChanges": [{"text": text}],
    }


def position_params(file: str, line: int, character: int) -> dict:
    # 1-based (our boundary) → 0-based (LSP)
    return {
        "textDocument": {"uri": path_to_uri(file)},
        "position": {"line": line - 1, "character": character - 1},
    }


def references_params(file: str, line: int, character: int) -> dict:
    return {**position_params(file, line, character),
            "context": {"includeDeclaration": True}}


def document_symbol_params(file: str) -> dict:
    return {"textDocument": {"uri": path_to_uri(file)}}


# --- response parsers -------------------------------------------------------

def _loc_from_uri_range(uri: str, rng: dict, snippet: str = "") -> Location:
    start = rng.get("start", {})
    return Location(
        file=uri_to_path(uri),
        line=start.get("line", 0) + 1,            # 0-based → 1-based
        character=start.get("character", 0) + 1,
        snippet=snippet,
    )


def parse_locations(result) -> list[Location]:
    """definition/references result → [Location]. Handles Location, Location[],
    and LocationLink[] (which carry targetUri/targetSelectionRange)."""
    if result is None:
        return []
    items = result if isinstance(result, list) else [result]
    out: list[Location] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        uri = it.get("uri") or it.get("targetUri")
        rng = it.get("range") or it.get("targetSelectionRange") or it.get("targetRange")
        if uri and rng:
            out.append(_loc_from_uri_range(uri, rng))
    return out


def parse_document_symbols(result, file: str) -> list[Location]:
    """documentSymbol result → [Location]. Handles DocumentSymbol[] (nested,
    .selectionRange) and SymbolInformation[] (.location)."""
    if not result:
        return []
    out: list[Location] = []

    def walk(sym: dict):
        if "location" in sym:                      # SymbolInformation
            loc = sym["location"]
            uri, rng = loc.get("uri"), loc.get("range")
            if uri and rng:
                out.append(_loc_from_uri_range(uri, rng, snippet=sym.get("name", "")))
        else:                                       # DocumentSymbol
            rng = sym.get("selectionRange") or sym.get("range")
            if rng:
                out.append(_loc_from_uri_range(path_to_uri(file), rng, snippet=sym.get("name", "")))
            for child in sym.get("children", []) or []:
                walk(child)

    for sym in result:
        if isinstance(sym, dict):
            walk(sym)
    return out


def parse_hover(result) -> str:
    """hover result → text. contents may be str / MarkupContent / MarkedString[]."""
    if not result:
        return ""
    contents = result.get("contents")
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents.strip()
    if isinstance(contents, dict):
        return (contents.get("value") or "").strip()
    if isinstance(contents, list):
        parts = []
        for c in contents:
            parts.append(c if isinstance(c, str) else c.get("value", ""))
        return "\n".join(p for p in parts if p).strip()
    return ""
