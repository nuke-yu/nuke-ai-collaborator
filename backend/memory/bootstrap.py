"""Composition root for the Memory module.

Only this module chooses concrete adapters. Domain and application code never
import it, which keeps in-process, IPC, and future service deployments
interchangeable.
"""
from __future__ import annotations

from memory.adapters.runtime import (LegacyConversationMemoryAdapter, LegacyLearningAdapter,
                                     LegacyPersonalKnowledgeAdapter)
from memory.application import AuthorizedPersonalKnowledgeService
from memory.domain import Principal
from memory.ports import MemoryACLPort


def build_memory_client(bot: dict | None = None) -> LegacyConversationMemoryAdapter:
    """Build the current in-process client behind stable Memory contracts."""
    from ai.memory_provider import get_memory_provider

    return LegacyConversationMemoryAdapter(get_memory_provider(bot))


def build_personal_knowledge_client(principal: Principal) -> AuthorizedPersonalKnowledgeService:
    """Build the fail-closed personal-memory application boundary."""
    return AuthorizedPersonalKnowledgeService(
        LegacyPersonalKnowledgeAdapter(), build_memory_acl(), principal
    )


def build_learning_client() -> LegacyLearningAdapter:
    return LegacyLearningAdapter()


def build_memory_acl() -> MemoryACLPort:
    """Build the production ACL policy behind its stable application port."""
    from memory.adapters.algorithms import LettaACLAlgorithmAdapter

    return LettaACLAlgorithmAdapter()
