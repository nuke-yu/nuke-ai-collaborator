"""Deterministic engine routing by file extension (the CODE-side decision).

The model chooses the *capability* (definition / references / …) via the
code_intel tool; the code chooses the *engine* by language here. Phase 1.5 ships
only the Python (jedi) engine; the JS/LSP engine plugs in at Phase 2 with no tool
change. An unsupported/unavailable language returns None — the tool then tells
the model to fall back to the textual `search` tool.
"""
from __future__ import annotations

from executors.code_intel.engine import CodeIntelEngine
from executors.code_intel.jedi_engine import JediEngine
from executors.code_intel.lsp_engine import LspEngine

_JEDI = JediEngine()
_TS_LSP = LspEngine()

# ext → engine. jedi for Python (in-process library); typescript-language-server
# for JS/TS (LSP over stdio). Both are static analysis run in the worker; an
# unavailable engine (binary absent) makes get_engine return None → fall back to
# the textual `search` tool.
_ENGINE_BY_EXT: dict[str, CodeIntelEngine] = {
    ".py": _JEDI, ".pyi": _JEDI,
    ".js": _TS_LSP, ".jsx": _TS_LSP, ".mjs": _TS_LSP, ".cjs": _TS_LSP,
    ".ts": _TS_LSP, ".tsx": _TS_LSP, ".mts": _TS_LSP, ".cts": _TS_LSP,
}


def get_engine(ext: str) -> CodeIntelEngine | None:
    engine = _ENGINE_BY_EXT.get((ext or "").lower())
    if engine is None or not engine.available():
        return None
    return engine


def supported_extensions() -> tuple[str, ...]:
    return tuple(_ENGINE_BY_EXT)
