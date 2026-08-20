from __future__ import annotations

import asyncio
import multiprocessing
import re
import time
from typing import Callable

from .domain import CodeModeLimits, CodeModeRejected
from .ports import BashPort, WorkspacePort
from .validator import ALLOWED_BUILTINS, validate


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


def _code_worker(connection, code: str, limits: CodeModeLimits) -> None:
    """Execute untrusted code in a child process and proxy SDK calls."""
    output: list[str] = []
    started = time.monotonic()
    steps = 0

    class ChildTools:
        def _call(self, name: str, *args, **kwargs):
            connection.send(("call", name, args, kwargs))
            kind, value = connection.recv()
            if kind == "error":
                raise CodeModeRejected(value)
            return value

        def read(self, path, offset=None, limit=None):
            return self._call("read", path, offset, limit)

        def write(self, path, content):
            return self._call("write", path, content)

        def grep(self, pattern, path="."):
            return self._call("grep", pattern, path)

        def bash(self, cmd, cwd="."):
            return self._call("bash", cmd, cwd)

    def safe_print(*values, sep=" ", end="\n", **kwargs):
        if kwargs:
            raise CodeModeRejected("print 仅支持 values、sep 和 end")
        text = sep.join(str(value) for value in values) + end
        output.append(text)
        if sum(len(item) for item in output) > limits.max_output_chars:
            raise CodeModeRejected("Code Mode 输出超过限制")

    def trace(_frame, _event, _arg):
        nonlocal steps
        steps += 1
        if steps > limits.max_steps or time.monotonic() - started > limits.timeout_seconds:
            raise TimeoutError("Code Mode 超过执行预算")
        return trace

    try:
        tree = __import__("ast").parse(code, mode="exec")
        validate(tree)
        env = {"__builtins__": {**ALLOWED_BUILTINS, "print": safe_print}, "tools": ChildTools()}
        import sys
        sys.settrace(trace)
        exec(compile(tree, "<run_code>", "exec"), env, env)
        connection.send(("done", "".join(output) or "[Code Mode] 执行完成，无输出"))
    except BaseException as exc:
        connection.send(("failed", f"{type(exc).__name__}: {exc}"))
    finally:
        try:
            import sys
            sys.settrace(None)
        finally:
            connection.close()


class SubprocessCodeExecutionAdapter:
    """Run Code Mode in a disposable process with parent-mediated SDK calls."""

    def execute(self, code: str, tools: object, limits: CodeModeLimits) -> str:
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe()
        process = ctx.Process(target=_code_worker, args=(child, code, limits), daemon=True)
        process.start()
        child.close()
        deadline = time.monotonic() + limits.timeout_seconds
        try:
            while time.monotonic() < deadline:
                if not parent.poll(min(0.05, max(0.0, deadline - time.monotonic()))):
                    continue
                message = parent.recv()
                kind = message[0]
                if kind == "call":
                    _, name, args, kwargs = message
                    try:
                        value = getattr(tools, name)(*args, **kwargs)
                        parent.send(("result", value))
                    except BaseException as exc:
                        parent.send(("error", f"{type(exc).__name__}: {exc}"))
                elif kind == "done":
                    return message[1]
                elif kind == "failed":
                    raise CodeModeRejected(f"Code Mode 执行失败: {message[1]}")
            raise CodeModeRejected("Code Mode 超过执行预算")
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            parent.close()
