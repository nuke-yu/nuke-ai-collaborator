"""
executors/providers — Concrete ToolProvider implementations.

Registration order in ToolRouter matters (first match wins):
  1. SkillToolProvider    — run_skill
  2. ShellToolProvider    — run_shell
  3. McpClientToolProvider — filesystem__* / mcp:: namespace
  4. BuiltinToolProvider  — everything else (file I/O, spawn_agent, …)

"""

from .builtin import BuiltinToolProvider
from .skill import SkillToolProvider
from .shell import ShellToolProvider
from .mcp_client import McpClientToolProvider

__all__ = [
    "BuiltinToolProvider",
    "SkillToolProvider",
    "ShellToolProvider",
    "McpClientToolProvider",
]
