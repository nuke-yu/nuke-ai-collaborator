"""Restricted local Code Mode runtime.

The generated program gets a tiny SDK, not Python's filesystem, process, or
network APIs.  Workspace writes still pass through the normal Read-Before-
Mutate gate and permission path.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path


class CodeModeRejected(ValueError):
    pass


@dataclass(frozen=True)
class CodeModeLimits:
    timeout_seconds: float = 5.0
    max_steps: int = 25_000
    max_output_chars: int = 16_000


_ALLOWED_TOOLS = frozenset({"read", "write", "grep"})
_ALLOWED_BUILTINS = {
    "bool": bool, "dict": dict, "enumerate": enumerate, "float": float,
    "int": int, "len": len, "list": list, "range": range, "sorted": sorted,
    "str": str, "tuple": tuple, "zip": zip,
}
_BLOCKED_NAMES = frozenset({
    "__builtins__", "__import__", "eval", "exec", "compile", "open",
    "globals", "locals", "input", "breakpoint", "help", "quit", "exit",
})


def _validate(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith,
                             ast.Try, ast.Raise, ast.Lambda, ast.While,
                             ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Global, ast.Nonlocal, ast.Delete,
                             ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            raise CodeModeRejected(f"禁止的 Code Mode 语法: {type(node).__name__}")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int) and abs(node.value) > 100_000:
                raise CodeModeRejected("数字字面量超过资源限制")
            if isinstance(node.value, str) and len(node.value) > 10_000:
                raise CodeModeRejected("字符串字面量超过资源限制")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            if isinstance(node.right, ast.Constant) and isinstance(node.right.value, int) and node.right.value > 10_000:
                raise CodeModeRejected("重复展开规模超过资源限制")
        if isinstance(node, ast.Name) and node.id in _BLOCKED_NAMES:
            raise CodeModeRejected(f"禁止访问名称: {node.id}")
        if isinstance(node, ast.Attribute):
            if not isinstance(node.value, ast.Name) or node.value.id != "tools":
                raise CodeModeRejected("只允许访问 tools SDK 方法")
            if node.attr not in _ALLOWED_TOOLS:
                raise CodeModeRejected(f"禁止的 SDK 方法: {node.attr}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id not in _ALLOWED_BUILTINS and node.func.id != "print":
                raise CodeModeRejected(f"禁止调用: {node.func.id}")
            if isinstance(node.func, ast.Attribute):
                if not isinstance(node.func.value, ast.Name) or node.func.value.id != "tools" or node.func.attr not in _ALLOWED_TOOLS:
                    raise CodeModeRejected("禁止调用非 SDK 方法")
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                raise CodeModeRejected("禁止动态调用")


class _SDK:
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


def run_code(
    code: str, *, bot_id: int, group_id: int | None, session_id: str | None,
    limits: CodeModeLimits = CodeModeLimits(),
) -> str:
    if not isinstance(code, str) or not code.strip():
        raise CodeModeRejected("code 不能为空")
    if group_id is None:
        raise CodeModeRejected("Code Mode 必须绑定 group_id")
    if len(code) > 30_000:
        raise CodeModeRejected("code 超过 30,000 字符限制")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise CodeModeRejected(f"代码语法错误: {exc}") from exc
    _validate(tree)

    output = io.StringIO()
    started = time.monotonic()
    steps = 0

    def trace(_frame, _event, _arg):
        nonlocal steps
        steps += 1
        if steps > limits.max_steps or time.monotonic() - started > limits.timeout_seconds:
            raise TimeoutError("Code Mode 超过执行预算")
        return trace

    env = {
        "__builtins__": {**_ALLOWED_BUILTINS, "print": print},
        "tools": _SDK(bot_id=bot_id, group_id=group_id, session_id=session_id),
    }
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
