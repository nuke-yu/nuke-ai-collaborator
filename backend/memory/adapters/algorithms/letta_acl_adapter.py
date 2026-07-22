"""Letta Context Budget & OpenMemory ACL Algorithm Adapter implementing MemoryAlgorithmPort."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from memory.adapters.algorithms.letta_acl_engine import (
    ACLPermissionCheck, ContextBudgetAllocation, LettaOpenMemoryEngine)
from memory.contracts import MemoryHit, ObserveMemory, RecallMemory
from memory.domain import MemoryScope, Principal
from memory.ports.infrastructure import AlgorithmDescriptor, MemoryAlgorithmPort


class LettaACLAlgorithmAdapter:
    """Adapter wrapping Letta Context Budgeting and OpenMemory Security ACL."""

    descriptor = AlgorithmDescriptor(
        algorithm_id="nuke.letta_openmemory.budget_acl",
        source="Letta (Apache-2.0) / OpenMemory ACL Spec",
        version="v1.0",
        license="Apache-2.0",
        capabilities=("context_budgeting", "scope_acl_isolation", "token_allocation"),
    )

    def __init__(self, engine: LettaOpenMemoryEngine | None = None) -> None:
        self._engine = engine or LettaOpenMemoryEngine()

    async def calculate_budget(
        self,
        max_tokens: int,
        system_prompt: str,
        working_memory: str,
        recall_memory: str,
        tool_schemas: Sequence[Mapping[str, Any]] = (),
    ) -> ContextBudgetAllocation:
        """Calculate token budget allocation across context components."""
        return self._engine.calculate_context_budget(
            max_tokens, system_prompt, working_memory, recall_memory, tool_schemas
        )

    async def check_acl(
        self,
        scope: MemoryScope,
        principal: Principal | None = None,
        action: str = "read",
        requesting_actor_id: str = "",
    ) -> ACLPermissionCheck:
        """Check OpenMemory security ACL permissions."""
        return self._engine.check_acl_access(
            scope, principal=principal, action=action, requesting_actor_id=requesting_actor_id
        )
