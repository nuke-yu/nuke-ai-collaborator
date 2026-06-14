from executors import tool_executor

async def dispatch_tool(name: str, arguments: dict, context: dict) -> tuple[str, bool]:
    """Dispatch a tool call to the right executor, returning (result, is_error).

    Routing policy (deliberately NOT "everything through the router"):
      - Builtin / skill / shell tools (anything registered in tool_executor)
        stay on tool_executor.execute() so the global before-hooks — permission
        check + run_shell danger guard — still fire. The ToolRouter is
        first-match and would route run_shell → ShellProvider, skipping those
        hooks (a silent security regression).
      - MCP tools are NOT in tool_executor's registry, so route ONLY those
        through the router (→ McpClientToolProvider, which applies its own HIL
        gate + timeout). Guarded by has_providers() so a worker/test without
        MCP falls straight through to tool_executor.
    """
    if not tool_executor.has_tool(name):
        from executors.tool_router import router as _tool_router
        if _tool_router.has_providers():
            return await _tool_router.execute(name, arguments, context=context)
    return await tool_executor.execute(name, arguments, context=context)


async def execute_tool_call(name: str, arguments: dict, context: dict) -> str:
    """String-only wrapper around dispatch_tool (used by the minimal test loop)."""
    res, _ = await dispatch_tool(name, arguments, context)
    return res
