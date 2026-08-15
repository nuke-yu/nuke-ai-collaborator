"""Explicit dependency composition for the Memory bounded context.

This module deliberately contains no business rules. It owns the process-local
objects assembled by the Memory composition root.
"""
from __future__ import annotations

from dataclasses import dataclass

from memory.infrastructure import ProjectionOutbox
from memory.module import MemoryModule
from memory.ports import (
    FactEnginePort,
    MemberDirectoryPort,
    MemorySecretPort,
    MemorySettingsPort,
    ModelPort,
    SkillWorkspacePort,
)


@dataclass(slots=True)
class MemoryComposition:
    """Process-local Memory dependencies.

    A composition is intentionally cheap to construct and owns no background
    task until ``MemoryModule.start()`` is called by the host lifecycle.  This
    keeps construction deterministic in tests and prevents import-time side
    effects.
    """

    module: MemoryModule
    member_directory: MemberDirectoryPort | None = None
    secret_provider: MemorySecretPort | None = None
    skill_workspace: SkillWorkspacePort | None = None
    fact_engine: FactEnginePort | None = None
    settings: MemorySettingsPort | None = None
    model: ModelPort | None = None

    @property
    def database(self):
        return self.module.database

    @property
    def projection_outbox(self) -> ProjectionOutbox:
        return self.module.projection_outbox
