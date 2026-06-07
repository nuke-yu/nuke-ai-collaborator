"""
providers/builtin.py — BuiltinToolProvider

Wraps the existing tool_executor._registry so that the system has a proper
ToolProvider interface around built-in Python-function tools without requiring
any plugin to change its register_tools() call.

Design constraints (per FP1/FP4 decisions):
  - No new hook layer added here; global hooks in tool_executor remain the
    single interception point for now.
  - execute() delegates entirely to tool_executor.execute() so all existing
    alias normalisation, truncation guards, and error-prefix detection stay
    in one place and are not duplicated.
  - discover_tools() is synchronous (builtin tools are statically registered
    at startup; no async discovery needed).
"""
import logging

from executors.base import ToolDef, ToolProvider
from executors import tool_executor

logger = logging.getLogger(__name__)


class BuiltinToolProvider(ToolProvider):
    """
    Thin provider wrapping all tools registered via tool_executor.register().

    can_handle() returns True for any name present in the registry at the time
    of the call — which means it acts as the *catch-all* provider and should
    always be registered last in a ToolRouter so that more-specific providers
    (e.g. a future mcp:: prefix provider) get first refusal.
    """

    @property
    def provider_id(self) -> str:
        return "builtin"

    def discover_tools(self) -> list[ToolDef]:
        """Return all ToolDefs currently in the registry (static snapshot)."""
        return list(tool_executor._registry.values())

    def can_handle(self, name: str) -> bool:
        return name in tool_executor._registry

    async def execute(self, name: str, arguments: dict, context: dict) -> tuple[str, bool]:
        """Delegate fully to tool_executor.execute() — no behaviour change."""
        return await tool_executor.execute(name, arguments, context=context)
