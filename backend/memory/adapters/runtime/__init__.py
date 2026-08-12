"""Runtime adapters exposing the Memory contracts."""

from .legacy import LegacyConversationMemoryAdapter, LegacyMemoryProvider
from .learning_legacy import LegacyLearningAdapter, LegacyPipelineJobAdapter
from .personal_legacy import LegacyPersonalKnowledgeAdapter
from .personal_vault_policy import LegacyPersonalVaultPolicyAdapter
from .projection_legacy import (LegacyExperienceProjectionDelivery,
                                LegacyExperienceProjectionReconciler,
                                LegacyBotMemoryProjectionReader,
                                LegacyMemoryProjectionDelivery,
                                LegacyMemoryProjectionReconciler,
                                redact_projection_content,
                                redact_projection_error)
from .sqlite_legacy import LegacySQLiteMemoryDatabase, legacy_memory_database

__all__ = ["LegacyConversationMemoryAdapter", "LegacyLearningAdapter", "LegacyMemoryProvider",
           "LegacyPersonalKnowledgeAdapter", "LegacyPipelineJobAdapter",
           "LegacyPersonalVaultPolicyAdapter",
           "LegacyExperienceProjectionDelivery", "LegacyExperienceProjectionReconciler",
           "LegacyBotMemoryProjectionReader",
           "LegacyMemoryProjectionDelivery", "LegacyMemoryProjectionReconciler",
           "LegacySQLiteMemoryDatabase", "legacy_memory_database",
           "redact_projection_content", "redact_projection_error"]
