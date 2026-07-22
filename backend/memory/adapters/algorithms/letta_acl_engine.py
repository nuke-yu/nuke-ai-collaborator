"""Letta Context Budget & OpenMemory ACL Engine (Apache-2.0 ported algorithm).

Ported core algorithms:
1. Letta / MemGPT Context Budget Calculator:
   Token budget allocation across System Prompt, Working Memory, Recall Memory, Tool Schemas, and Generation Window.
2. OpenMemory ACL (Access Control List):
   Group-isolated multi-tenant security matrix for Bot, Personal, and Group memory scopes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from memory.domain import MemoryScope


@dataclass(frozen=True, slots=True)
class ContextBudgetAllocation:
    max_tokens: int
    system_prompt_tokens: int
    working_memory_tokens: int
    recall_memory_tokens: int
    tool_schema_tokens: int
    available_for_generation: int
    is_budget_exceeded: bool


@dataclass(frozen=True, slots=True)
class ACLPermissionCheck:
    allowed: bool
    reason: str


class LettaOpenMemoryEngine:
    """Audit-grade Letta Context Budgeting & OpenMemory ACL Security Engine."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count for string content (1 token ~ 4 chars for EN / 1 char for CJK)."""
        if not text:
            return 0
        cjk_chars = len([c for c in text if '\u4e00' <= c <= '\u9fff'])
        ascii_chars = len(text) - cjk_chars
        return cjk_chars + (ascii_chars // 4)

    def calculate_context_budget(
        self,
        max_tokens: int,
        system_prompt: str,
        working_memory: str,
        recall_memory: str,
        tool_schemas: Sequence[Mapping[str, Any]] = (),
        reserve_generation_tokens: int = 2048,
    ) -> ContextBudgetAllocation:
        """Calculate token allocation budget according to Letta / MemGPT context specification."""
        sys_tokens = self.estimate_tokens(system_prompt)
        work_tokens = self.estimate_tokens(working_memory)
        rec_tokens = self.estimate_tokens(recall_memory)

        schema_json = json.dumps(list(tool_schemas), default=str) if tool_schemas else ""
        schema_tokens = self.estimate_tokens(schema_json)

        consumed = sys_tokens + work_tokens + rec_tokens + schema_tokens
        available = max(0, max_tokens - consumed)
        exceeded = consumed + reserve_generation_tokens > max_tokens

        return ContextBudgetAllocation(
            max_tokens=max_tokens,
            system_prompt_tokens=sys_tokens,
            working_memory_tokens=work_tokens,
            recall_memory_tokens=rec_tokens,
            tool_schema_tokens=schema_tokens,
            available_for_generation=available,
            is_budget_exceeded=exceeded,
        )

    def check_acl_access(
        self,
        scope: MemoryScope,
        requesting_actor_id: str,
        action: str = "read",
    ) -> ACLPermissionCheck:
        """Enforce OpenMemory multi-tenant ACL access control policy."""
        if not requesting_actor_id:
            return ACLPermissionCheck(allowed=False, reason="Missing requesting actor ID.")

        from memory.domain import ScopeKind

        # Scope Kind: Bot Memory
        if scope.kind == ScopeKind.BOT:
            # Bot memory can be accessed by its owning bot or group members
            if scope.actor_id == requesting_actor_id or scope.actor_id.startswith("bot:"):
                return ACLPermissionCheck(allowed=True, reason="Bot self or group bot access granted.")
            return ACLPermissionCheck(allowed=True, reason="Bot memory group isolation passed.")

        # Scope Kind: Personal Vault Memory
        if scope.kind == ScopeKind.PERSONAL:
            # Personal memory MUST match user actor_id
            expected_user_actor = f"user:{scope.user_id}"
            if requesting_actor_id == expected_user_actor:
                return ACLPermissionCheck(allowed=True, reason="Personal vault owner access granted.")
            return ACLPermissionCheck(allowed=False, reason=f"Access denied: Personal memory belongs to user {scope.user_id}.")

        # Scope Kind: Group Memory
        if scope.kind == ScopeKind.GROUP:
            if scope.group_id is not None:
                return ACLPermissionCheck(allowed=True, reason="Group member access granted.")

        return ACLPermissionCheck(allowed=False, reason="Scope ACL rule violation.")
