"""Python code intelligence via jedi (in-process, static analysis).

jedi does NOT execute the analyzed code — it's safe to run in the worker reading
the group's files (same read-only access as read_file), so this engine needs no
sandbox and ships independent of the Docker substrate.

Coordinate conversion: the engine boundary is 1-based line + 1-based character;
jedi wants 1-based line + 0-based column, so column = character - 1 on input and
character = name.column + 1 on output. jedi calls are sync/CPU-bound, so each is
run in a thread to keep the event loop responsive.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from executors.code_intel.engine import Location

_JEDI_AVAILABLE: bool | None = None


def _location(name) -> Location | None:
    if name is None or name.module_path is None or not name.line:
        return None
    return Location(
        file=str(name.module_path),
        line=name.line,
        character=(name.column or 0) + 1,         # jedi col 0-based → 1-based
        snippet=(name.description or "").strip(),
    )


class JediEngine:
    def available(self) -> bool:
        global _JEDI_AVAILABLE
        if _JEDI_AVAILABLE is None:
            try:
                import jedi  # noqa: F401
                _JEDI_AVAILABLE = True
            except Exception:
                _JEDI_AVAILABLE = False
        return _JEDI_AVAILABLE

    @staticmethod
    def _script(file: Path, root: Path):
        import jedi
        return jedi.Script(path=str(file), project=jedi.Project(str(root)))

    async def definition(self, file, line, character, root):
        return await asyncio.to_thread(self._definition_sync, file, line, character, root)

    def _definition_sync(self, file, line, character, root) -> list[Location]:
        names = self._script(file, root).goto(line, character - 1, follow_imports=True)
        return [loc for n in names if (loc := _location(n))]

    async def references(self, file, line, character, root):
        return await asyncio.to_thread(self._references_sync, file, line, character, root)

    def _references_sync(self, file, line, character, root) -> list[Location]:
        names = self._script(file, root).get_references(line, character - 1, include_builtins=False)
        return [loc for n in names if (loc := _location(n))]

    async def hover(self, file, line, character, root):
        return await asyncio.to_thread(self._hover_sync, file, line, character, root)

    def _hover_sync(self, file, line, character, root) -> str:
        names = self._script(file, root).help(line, character - 1)
        if not names:
            return ""
        n = names[0]
        parts = [f"{n.type} {n.name}".strip() if n.type else (n.name or "")]
        try:
            sigs = n.get_signatures()
            if sigs:
                parts.append(sigs[0].to_string())
        except Exception:
            pass
        try:
            doc = n.docstring(raw=True)
            if doc:
                parts.append(doc)
        except Exception:
            pass
        return "\n".join(p for p in parts if p).strip()

    async def document_symbols(self, file, root):
        return await asyncio.to_thread(self._document_symbols_sync, file, root)

    def _document_symbols_sync(self, file, root) -> list[Location]:
        names = self._script(file, root).get_names(
            all_scopes=True, definitions=True, references=False
        )
        return [loc for n in names if (loc := _location(n))]
