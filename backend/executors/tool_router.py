"""
executors/tool_router.py — ToolRouter

Thin coordinator that aggregates tool schemas from all registered providers
and routes execute() calls to the correct one.

Design constraints (per FP1/FP4 decisions):
  - Does NOT replace tool_executor.  Builtin tools keep flowing through
    tool_executor.execute() via BuiltinToolProvider.
  - Global before/after hooks in tool_executor remain the single interception
    point for security/permission checks; ToolRouter does NOT add a second
    hook layer.
  - Schema aggregation: get_all_schemas() merges builtin + MCP schemas.
    tool_loop_v1 can call this instead of tool_executor.get_schemas() once
    MCP tools should be visible to the LLM.

Typical wiring (app startup):
    from executors.tool_router import router
    from executors.providers import BuiltinToolProvider
    from executors.providers.mcp_client import McpClientToolProvider

    router.register_provider(BuiltinToolProvider())
    for p in McpClientToolProvider.from_config("backend/mcp_servers.json"):
        await p.initialize()
        router.register_provider(p)
"""
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from executors.base import ToolProvider

logger = logging.getLogger(__name__)


class ToolRouter:
    """
    Central dispatcher: one provider per tool kind.

    Providers are queried in registration order; the first one whose
    can_handle() returns True handles the call.  BuiltinToolProvider should
    always be registered last so that mcp:: / skill:: providers get first
    refusal on their namespaced tools.
    """

    def __init__(self) -> None:
        self._providers: list["ToolProvider"] = []

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    def register_provider(self, provider: "ToolProvider") -> None:
        self._providers.append(provider)
        logger.info(f"ToolRouter: registered provider '{provider.provider_id}'")

    def unregister_provider(self, provider_id: str) -> None:
        before = len(self._providers)
        self._providers = [p for p in self._providers if p.provider_id != provider_id]
        if len(self._providers) < before:
            logger.info(f"ToolRouter: unregistered provider '{provider_id}'")

    # ------------------------------------------------------------------ #
    # Schema aggregation
    # ------------------------------------------------------------------ #

    def get_all_schemas(self) -> list[dict]:
        """
        Return OpenAI-format schemas for every tool across all providers.

        This is the replacement for tool_executor.get_schemas(tool_names) when
        MCP tools should also be visible to the LLM.
        """
        schemas = []
        for provider in self._providers:
            for td in provider.discover_tools():
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": td.name,
                        "description": td.description,
                        "parameters": td.parameters,
                    },
                })
        return schemas

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    async def execute(self, name: str, arguments: dict, context: dict | None = None) -> tuple[str, bool]:
        """
        Route a tool call to the first matching provider.

        Note: global before/after hooks in tool_executor are applied inside
        BuiltinToolProvider.execute() already (it delegates to
        tool_executor.execute()).  MCP calls bypass those hooks intentionally
        — MCP results should be treated as untrusted external data and
        inspected at the HIL level, not filtered by keyword-matching hooks.
        """
        ctx = context or {}
        for provider in self._providers:
            if provider.can_handle(name):
                logger.debug(f"ToolRouter: routing '{name}' → {provider.provider_id}")
                return await provider.execute(name, arguments, ctx)

        return f"[错误] 未找到能处理工具 '{name}' 的提供者", True

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def close_all(self) -> None:
        """Shut down all providers that implement close() (e.g. MCP subprocesses)."""
        for provider in self._providers:
            if hasattr(provider, "close"):
                try:
                    await provider.close()
                except Exception as e:
                    logger.warning(f"Error closing provider '{provider.provider_id}': {e}")
        self._providers.clear()


# Module-level singleton — import and use directly:
#   from executors.tool_router import router
router = ToolRouter()
