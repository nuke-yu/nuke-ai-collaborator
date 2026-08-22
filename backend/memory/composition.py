"""Explicit dependency composition for the Memory bounded context.

This module deliberately contains no business rules. It owns the process-local
objects assembled by the Memory composition root.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memory.infrastructure import ProjectionOutbox
from memory.module import MemoryModule
from memory.ports import (
    FactEnginePort,
    MemberDirectoryPort,
    MemorySecretPort,
    MemorySettingsPort,
    ModelPort,
    PipelineJobRepositoryPort,
    SkillWorkspacePort,
    TemporalGraphPort,
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
    member_directory: MemberDirectoryPort
    secret_provider: MemorySecretPort
    skill_workspace: SkillWorkspacePort
    fact_engine: FactEnginePort
    settings: MemorySettingsPort
    model: ModelPort
    temporal_graph: TemporalGraphPort
    pipeline_repository: PipelineJobRepositoryPort
    memory_functions: Any | None = None

    @property
    def database(self):
        return self.module.database

    @property
    def projection_outbox(self) -> ProjectionOutbox:
        return self.module.projection_outbox
