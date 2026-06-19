"""Code-intelligence engine interface + data types (pure, no deps on engines).

Coordinates are **editor convention** at this boundary: 1-based line AND
1-based character (matching OpenCode's lsp tool). Engines convert to whatever
their backend wants internally (e.g. jedi uses 1-based line, 0-based column).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Location:
    """A resolved symbol location. `file` is an absolute path; the tool layer
    rewrites it to a workspace-relative path for display."""
    file: str
    line: int          # 1-based
    character: int      # 1-based
    snippet: str = ""   # symbol description / signature line


@runtime_checkable
class CodeIntelEngine(Protocol):
    def available(self) -> bool:
        """Whether this engine can run right now (backend importable/installed)."""
        ...

    async def definition(self, file: Path, line: int, character: int, root: Path) -> list[Location]:
        ...

    async def references(self, file: Path, line: int, character: int, root: Path) -> list[Location]:
        ...

    async def hover(self, file: Path, line: int, character: int, root: Path) -> str:
        ...

    async def document_symbols(self, file: Path, root: Path) -> list[Location]:
        ...
