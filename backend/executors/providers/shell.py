"""
providers/shell.py — ShellToolProvider

⚠️  DO NOT REGISTER THIS PROVIDER.  ⚠️
================================================================================
This is dormant Plan B scaffolding. It is intentionally NOT registered with the
ToolRouter (see runtime/entry.py — workers register only MCP providers + the
Builtin catch-all). It sits on NO execution path today.

execute() below calls the run_shell handler DIRECTLY and therefore BYPASSES the
global before/after hooks — i.e. the permission check (_permission_check_hook)
AND the dangerous-command guard (_default_shell_guard) that normally gate
run_shell in tool_executor.

The ToolRouter is FIRST-MATCH. If you register this provider, run_shell calls
will match here first and reach execute() WITHOUT any hook ever firing — a
fail-open security regression (the exact one fixed in commit d5ab65e; see
docs/TOOL-ROUTER-STRATEGIC-SOLUTION.md §四.3 / §八).

Currently run_shell stays in tool_executor's registry and is dispatched via
tool_executor.execute() (tool_loop_v1._dispatch_tool), so its hooks DO fire.
That guarantee holds ONLY because this provider is unregistered.

Before this provider may be registered, Plan B 阶段 1 must land first: hooks
must be lifted into ToolRouter.execute() as a non-bypassable pipeline. Until
then: leave it unregistered.
================================================================================
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

    ⚠️ DO NOT REGISTER — see module docstring. execute() bypasses the global
    permission/danger hooks; the ToolRouter is first-match, so registering this
    would let run_shell reach execute() with NO hook firing (security
    regression). run_shell is correctly served via tool_executor today.
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
