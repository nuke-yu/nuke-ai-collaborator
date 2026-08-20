from __future__ import annotations

from typing import Protocol


class WorkspacePort(Protocol):
    def read(self, path: str, offset: int | None = None, limit: int | None = None) -> str:
        ...

    def write(self, path: str, content: str) -> str:
        ...

    def grep(self, pattern: str, path: str = ".") -> list[dict[str, object]]:
        ...


class BashPort(Protocol):
    def execute(self, cmd: str, cwd: str = ".") -> str:
        ...
