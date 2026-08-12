from __future__ import annotations

from memory.application import CanonicalConversationMemoryService
from memory.canonical import build_conversation_memory_client


def test_canonical_entrypoint_does_not_resolve_legacy_provider() -> None:
    client = build_conversation_memory_client()

    assert isinstance(client, CanonicalConversationMemoryService)
