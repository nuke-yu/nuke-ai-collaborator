"""Runtime adapters exposing the Memory contracts."""

from .legacy import LegacyConversationMemoryAdapter, LegacyMemoryProvider
from .learning_legacy import LegacyLearningAdapter
from .personal_legacy import LegacyPersonalKnowledgeAdapter

__all__ = ["LegacyConversationMemoryAdapter", "LegacyLearningAdapter", "LegacyMemoryProvider",
           "LegacyPersonalKnowledgeAdapter"]
