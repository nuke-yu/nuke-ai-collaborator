"""Code-intelligence engines (semantic definition/references/hover/symbols).

Phase 1.5: Python via jedi (in-process, static, no LSP protocol, no Docker).
Phase 2:   JS/others via a language server running inside the group sandbox.

Mirrors the operation surface of OpenCode's `lsp` tool, but the Python half
short-cuts the LSP protocol with jedi-as-a-library. Same seam shape as
executors/plugins/shell_backend.py: a pure-interface engine + a router that
selects the engine deterministically by file extension.
"""
