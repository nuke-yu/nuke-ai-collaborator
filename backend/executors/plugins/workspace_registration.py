"""Registration assembly for workspace-related builtin tools."""
from __future__ import annotations

from pydantic import BaseModel, Field


def register_workspace_tools(
    *,
    tool_executor,
    workspace_tools,
    handlers: dict,
    permission_hook,
    shell_guard,
    secret_redactor,
    output_truncator,
) -> None:
    """Install hooks and all workspace-adjacent builtin tool handlers."""
    tool_executor.add_before_hook(permission_hook)
    tool_executor.add_before_hook(shell_guard)
    tool_executor.add_after_hook(secret_redactor)
    tool_executor.add_after_hook(output_truncator)
    for tool_def in workspace_tools:
        tool_executor.register(tool_def, handlers[tool_def.name])

    from executors.plugins import search_tool
    tool_executor.register(search_tool.SEARCH_TOOL_DEF, search_tool._handle_search)
    from executors.plugins import code_intel_tool
    tool_executor.register(code_intel_tool.CODE_INTEL_TOOL_DEF, code_intel_tool._handle_code_intel)

    from executors.plugins import memory_search_tool
    tool_executor.register(memory_search_tool.SEARCH_MEMORY_TOOL_DEF, memory_search_tool._handle_search_memory)
    tool_executor.register(memory_search_tool.MEMORY_TIMELINE_TOOL_DEF, memory_search_tool._handle_memory_timeline)
    tool_executor.register(memory_search_tool.MEMORY_FETCH_TOOL_DEF, memory_search_tool._handle_memory_fetch)

    class McpAuthenticateParams(BaseModel):
        server: str = Field(..., description="mcp_servers.json 中的 server 名")

    from executors.base import ToolDef
    tool_executor.register(
        ToolDef(
            name="mcp_authenticate",
            description="为需要 OAuth 授权的 remote MCP server 发起授权，返回授权链接交给用户在浏览器打开",
            parameters=McpAuthenticateParams,
        ),
        handlers["mcp_authenticate"],
    )
