"""
providers/shell.py — ShellToolProvider

Owns the run_shell tool exclusively:
  - Dangerous-pattern blocking (tier-2 backstop)
  - Env sanitization (_sandbox_env)
  - CWD confinement to bot workspace
  - Port interception & memory-limit wrapping
  - asyncio subprocess management (foreground + background)
  - Output truncation via the global after-hook (still in tool_executor)

All shell-specific logic that used to live in workspace_tools.py as a global
before-hook now belongs to this provider. The global _permission_check_hook
in tool_executor still fires first (via BuiltinToolProvider's delegation path)
for permission/ruleset gating — ShellToolProvider.execute() is only reached
after hooks pass.

Note: ShellToolProvider.execute() does NOT go through tool_executor.execute().
It calls the handler directly, bypassing the global registry.  The before-hooks
(permission + shell guard) are applied by the router's BuiltinToolProvider for
the shell tool name BEFORE routing reaches here — so hook coverage is preserved.
"""
import logging

from executors.base import ToolDef, ToolProvider

logger = logging.getLogger(__name__)

# ToolDef for the shell tool (single source of truth)
SHELL_TOOL = ToolDef(
    name="run_shell",
    description="在本地执行 shell 命令，返回 stdout / stderr / exit_code",
    parameters={
        "type": "object",
        "properties": {
            "cmd":        {"type": "string",  "description": "要执行的 shell 命令"},
            "cwd":        {"type": "string",  "description": "工作目录（绝对路径），默认为用户 home 目录"},
            "timeout":    {"type": "integer", "description": "超时秒数，默认 30", "default": 30},
            "background": {"type": "boolean", "description": "后台运行，立即返回 PID", "default": False},
        },
        "required": ["cmd"],
    },
)


class ShellToolProvider(ToolProvider):
    """
    Owns run_shell and all its sandboxing machinery.

    Registering this provider with the ToolRouter is enough — it does NOT
    need to call tool_executor.register() for run_shell.  The ToolRouter
    will route run_shell calls here directly.
    """

    @property
    def provider_id(self) -> str:
        return "shell"

    def discover_tools(self) -> list[ToolDef]:
        return [SHELL_TOOL]

    def can_handle(self, name: str) -> bool:
        return name == "run_shell"

    async def execute(self, name: str, arguments: dict, context: dict) -> tuple[str, bool]:
        # Import handler from workspace_tools to avoid duplicating 100-line
        # implementation here.  The handler itself is stable and well-tested.
        from executors.plugins.workspace_tools import _handle_run_shell
        try:
            result = await _handle_run_shell(**arguments, context=context)
            result = str(result) if result is not None else "完成"
            is_error = result.startswith("[") and any(
                k in result for k in ("错误", "拒绝", "拦截", "超时", "fail", "error")
            )
            return result, is_error
        except Exception as e:
            return f"[ShellProvider 执行错误] {e}", True
