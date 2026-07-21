"""Composition root for the Memory module.

Only this module chooses concrete adapters. Domain and application code never
import it, which keeps in-process, IPC, and future service deployments
interchangeable.
"""
from __future__ import annotations

from memory.adapters.runtime import LegacyConversationMemoryAdapter


def build_memory_client(bot: dict | None = None) -> LegacyConversationMemoryAdapter:
    """Build the current in-process client behind stable Memory contracts."""
    from ai.memory_provider import get_memory_provider

    return LegacyConversationMemoryAdapter(get_memory_provider(bot))

