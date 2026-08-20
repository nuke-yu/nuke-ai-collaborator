from __future__ import annotations

import ast
import contextlib
import io
import sys
import time

from .domain import CODE_MODE_PROMPT, CodeModeLimits, CodeModeRejected
from .ports import BashPort, WorkspacePort
from .validator import ALLOWED_BUILTINS, validate


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

        output = io.StringIO()
        started = time.monotonic()
        steps = 0

        def trace(_frame, _event, _arg):
            nonlocal steps
            steps += 1
            if steps > limits.max_steps or time.monotonic() - started > limits.timeout_seconds:
                raise TimeoutError("Code Mode 超过执行预算")
            return trace

        env = {"__builtins__": {**ALLOWED_BUILTINS, "print": print}, "tools": tools}
        try:
            sys.settrace(trace)
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                exec(compile(tree, "<run_code>", "exec"), env, {})
        finally:
            sys.settrace(None)
        result = output.getvalue()
        if len(result) > limits.max_output_chars:
            result = result[:limits.max_output_chars] + "\n[Code Mode 输出已限制]"
        return result or "[Code Mode] 执行完成，无输出"


def append_code_mode_prompt(prompt: str, tool_schemas: list[dict]) -> str:
    names = {
        schema.get("function", {}).get("name")
        for schema in tool_schemas or ()
        if isinstance(schema, dict)
    }
    if "run_code" not in names or CODE_MODE_PROMPT in prompt:
        return prompt
    return f"{prompt}\n\n{CODE_MODE_PROMPT}"
