from __future__ import annotations

import ast

from .domain import CODE_MODE_PROMPT, CodeModeLimits, CodeModeRejected
from .ports import BashPort, CodeExecutionPort, WorkspacePort
from .validator import validate


class CodeTools:
    def __init__(self, workspace: WorkspacePort, bash: BashPort):
        self._workspace = workspace
        self._bash = bash

    def read(self, path: str, offset: int | None = None, limit: int | None = None) -> str:
        return self._workspace.read(path, offset, limit)

    def write(self, path: str, content: str) -> str:
        return self._workspace.write(path, content)

    def grep(self, pattern: str, path: str = ".") -> list[dict[str, object]]:
        return self._workspace.grep(pattern, path)

    def bash(self, cmd: str, cwd: str = ".") -> str:
        if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 10_000:
            raise CodeModeRejected("bash 命令不能为空且不得超过 10,000 字符")
        return self._bash.execute(cmd, cwd)


class CodeModeService:
    """Application service; all environment effects arrive through ports."""
    def __init__(self, executor: CodeExecutionPort):
        self._executor = executor

    def run(self, code: str, tools: CodeTools, limits: CodeModeLimits) -> str:
        if not isinstance(code, str) or not code.strip():
            raise CodeModeRejected("code 不能为空")
        if len(code) > 30_000:
            raise CodeModeRejected("code 超过 30,000 字符限制")
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            raise CodeModeRejected(f"代码语法错误: {exc}") from exc
        validate(tree)
        return self._executor.execute(code, tools, limits)


def append_code_mode_prompt(prompt: str, tool_schemas: list[dict]) -> str:
    names = {
        schema.get("function", {}).get("name")
        for schema in tool_schemas or ()
        if isinstance(schema, dict)
    }
    if "run_code" not in names or CODE_MODE_PROMPT in prompt:
        return prompt
    return f"{prompt}\n\n{CODE_MODE_PROMPT}"
