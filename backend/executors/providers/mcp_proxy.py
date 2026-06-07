"""McpProxyProvider — worker-side stand-in for MCP tools.

The real MCP connections live in the cross-group collector process. In a worker
this provider advertises the collector's tool schemas (received over the bus and
cached in the MCP bridge) and forwards execution to the collector.

Trust boundary: permission/HIL runs HERE — the worker has the ruleset/broadcaster
and the sub-agent attenuation; the collector executes pre-authorized calls.
"""
from executors.base import ToolDef, ToolProvider
from executors.mcp_bridge import bridge


class McpProxyProvider(ToolProvider):
    @property
    def provider_id(self) -> str:
        return "mcp:proxy"

    def discover_tools(self) -> list[ToolDef]:
        defs = []
        for s in bridge.schemas:
            f = s.get("function", {})
            if f.get("name"):
                defs.append(ToolDef(
                    name=f["name"],
                    description=f.get("description", ""),
                    parameters=f.get("parameters") or {},
                ))
        return defs

    def can_handle(self, name: str) -> bool:
        return any(s.get("function", {}).get("name") == name for s in bridge.schemas)

    async def execute(self, name: str, arguments: dict, context: dict) -> tuple[str, bool]:
        verdict = await self._check_permission(name, arguments, context)
        if verdict:
            return verdict, True
        return await bridge.request(
            name, arguments,
            group_id=context.get("group_id"),
            trace_id=context.get("trace_id"),
        )

    async def _check_permission(self, name: str, arguments: dict, context: dict) -> str | None:
        """Worker-side HIL for MCP tools (collector runs them pre-authorized).

        Mirrors the old in-provider _check_hil: write-class tools require approval
        via the permissions pipeline; missing ruleset fails closed."""
        server, _, tool = name.partition("__")
        from executors.providers.mcp_client import _MCP_WRITE_TOOLS
        if tool not in _MCP_WRITE_TOOLS:
            return None  # read-class MCP tools: no prompt (preserve prior default)
        import permissions
        ruleset = context.get("ruleset")
        if ruleset is None:
            return (f"[MCP安全拦截] '{server}/{tool}' 是写操作类工具，"
                    f"但当前会话没有 ruleset，出于安全已拒绝执行")
        result = await permissions.check(
            tool_name=f"mcp::{server}::{tool}",
            arguments=arguments,
            ruleset=ruleset,
            bot_id=context.get("bot_id"),
            broadcaster=context.get("broadcaster"),
            group_id=context.get("group_id"),
            spawn_depth=context.get("spawn_depth", 0),
        )
        if result["action"] == "deny":
            return f"[MCP权限拒绝] {result.get('reason', '权限拒绝')}"
        return None
