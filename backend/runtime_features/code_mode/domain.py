from __future__ import annotations

from dataclasses import dataclass


class CodeModeRejected(ValueError):
    pass


@dataclass(frozen=True)
class CodeModeLimits:
    timeout_seconds: float = 5.0
    max_steps: int = 25_000
    max_output_chars: int = 16_000


CODE_MODE_PROMPT = """
【Code Mode 批处理规则】
当需要批量读取、筛选或处理多个工作区文件时，可以调用 run_code，将逻辑写成短小的 Python 脚本。
脚本只能使用 tools.read(path, offset=None, limit=None)、tools.write(path, content)、
tools.grep(pattern, path='.')、tools.bash(cmd, cwd='.')；禁止 import、网络、任意文件 API 和动态调用。
tools.bash 仍经过现有工作区沙箱、危险命令拦截和资源限制，不得用于绕过工具权限。
已有文件必须先通过 tools.read 观察，再调用 tools.write；脚本应返回少量结构化摘要，避免打印完整日志。
""".strip()
