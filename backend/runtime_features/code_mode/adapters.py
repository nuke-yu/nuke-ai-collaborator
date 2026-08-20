from __future__ import annotations

import asyncio
import re
from typing import Callable

from .domain import CodeModeRejected
from .ports import BashPort, WorkspacePort


class WorkspaceAdapter(WorkspacePort):
    def __init__(self, *, bot_id: int, group_id: int, session_id: str | None):
        self.bot_id = bot_id
        self.group_id = group_id
        self.session_id = session_id

    def read(self, path: str, offset: int | None = None, limit: int | None = None) -> str:
        from workspace import read_file
        return asyncio.run(read_file(
            self.bot_id, path, offset=offset, limit=limit,
            group_id=self.group_id, session_id=self.session_id,
        ))

    def write(self, path: str, content: str) -> str:
        from workspace import write_file
        return asyncio.run(write_file(
            self.bot_id, path, content, group_id=self.group_id,
            session_id=self.session_id,
        ))

    def grep(self, pattern: str, path: str = ".") -> list[dict[str, object]]:
        if len(pattern) > 300:
            raise CodeModeRejected("grep pattern 过长")
        expression = re.compile(pattern)
        from workspace import group_workspace
        root = group_workspace(self.group_id).resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise CodeModeRejected("grep 路径越界")
        paths = [target] if target.is_file() else sorted(target.rglob("*"))
        matches: list[dict[str, object]] = []
        for item in paths:
            if len(matches) >= 100 or not item.is_file() or item.stat().st_size > 1_000_000:
                continue
            try:
                for line_number, line in enumerate(item.read_text(encoding="utf-8").splitlines(), 1):
                    if expression.search(line):
                        matches.append({"path": str(item.relative_to(root)), "line": line_number, "text": line[:500]})
                        if len(matches) >= 100:
                            break
            except (OSError, UnicodeDecodeError):
                continue
        return matches


class CallbackBashAdapter(BashPort):
    def __init__(self, callback: Callable[[str, str], str] | None):
        self._callback = callback

    def execute(self, cmd: str, cwd: str = ".") -> str:
        if self._callback is None:
            raise CodeModeRejected("Code Mode 未配置 BashPort")
        return self._callback(cmd, cwd)
