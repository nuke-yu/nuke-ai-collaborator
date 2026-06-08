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
        return bridge.schema_for(name) is not None

    async def execute(self, name: str, arguments: dict, context: dict) -> tuple[str, bool]:
        verdict = await self._check_permission(name, arguments, context)
        if verdict:
            return verdict, True
        return await bridge.request(
            name, arguments,
            group_id=context.get("group_id"),
            trace_id=context.get("trace_id"),
        )

    def _needs_approval(self, name: str, tool: str) -> bool:
        """Per-server approval decision shipped by the collector (require_approval_all
        / approval_tools / write heuristic). Falls back to the write-name heuristic
        only if the flag is absent (e.g. schema not yet received)."""
        s = bridge.schema_for(name)
        if s is not None and "needs_approval" in s:
            return bool(s["needs_approval"])
        from executors.providers.mcp_client import _MCP_WRITE_TOOLS
        return tool in _MCP_WRITE_TOOLS

    async def _check_permission(self, name: str, arguments: dict, context: dict) -> str | None:
        """Worker-side HIL for MCP tools (collector runs them pre-authorized).

        Mirrors the old in-provider _check_hil: tools the owning server flags for
        approval go through the permissions pipeline; missing ruleset fails closed."""
        server, sep, tool = name.partition("__")
        # Fail-safe: an un-namespaced name (no '__') can't be classified — require
        # approval rather than silently skipping HIL. (Collector always prefixes,
        # so this is defensive.)
        if sep and not self._needs_approval(name, tool):
            return None  # no approval required for this tool
        perm_name = f"mcp::{server}::{tool}" if sep else f"mcp::{name}"
        import permissions
        ruleset = context.get("ruleset")
        if ruleset is None:
            return (f"[MCP安全拦截] '{name}' 是写操作类/不可分类工具，"
                    f"但当前会话没有 ruleset，出于安全已拒绝执行")
        result = await permissions.check(
            tool_name=perm_name,
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
