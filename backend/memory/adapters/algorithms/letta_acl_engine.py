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

from memory.domain import MemoryScope, Principal, ScopeKind


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

    @classmethod
    def truncate_text_to_tokens(cls, text: str, max_tokens: int) -> str:
        """Bound untrusted context before it enters a model prompt.

        The project deliberately avoids a provider-specific tokenizer in this
        low-level adapter.  Use the same conservative estimator as
        ``calculate_context_budget`` and preserve the beginning of the value,
        which contains the memory envelope and provenance markers.
        """
        if not text or max_tokens <= 0:
            return ""
        if cls.estimate_tokens(text) <= max_tokens:
            return text
        marker = "\n[context truncated by memory budget]"
        body_tokens = max(1, max_tokens - cls.estimate_tokens(marker))
        # CJK is estimated at one token/character; other text at four
        # characters/token.  The multiplier is intentionally conservative.
        cjk = sum("\u4e00" <= char <= "\u9fff" for char in text)
        ascii_chars = max(0, len(text) - cjk)
        estimated_chars = min(
            len(text),
            max(1, min(ascii_chars, body_tokens * 4) + min(cjk, body_tokens)),
        )
        return text[:estimated_chars].rstrip() + marker

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
        principal: Principal | None = None,
        action: str = "read",
        requesting_actor_id: str = "",
    ) -> ACLPermissionCheck:
        """Enforce OpenMemory multi-tenant ACL access control policy (Fail-Closed)."""
        if principal is None and isinstance(requesting_actor_id, Principal):
            principal = requesting_actor_id

        if not isinstance(principal, Principal) or not principal.actor_id:
            return ACLPermissionCheck(allowed=False, reason="Missing or invalid authenticated principal.")

        valid_actions = {"read", "write", "delete", "project"}
        if action not in valid_actions:
            return ACLPermissionCheck(allowed=False, reason=f"Access denied: Unsupported action '{action}'.")

        group_set = principal.group_ids

        # Scope Kind: Personal Vault Memory
        if scope.kind == ScopeKind.PERSONAL:
            if principal.user_id is not None and principal.user_id == scope.user_id:
                return ACLPermissionCheck(allowed=True, reason="Personal vault owner access granted.")
            return ACLPermissionCheck(allowed=False, reason=f"Access denied: Personal memory belongs to user {scope.user_id}.")

        # Scope Kind: Group Memory
        if scope.kind == ScopeKind.GROUP:
            if scope.group_id is None or scope.group_id not in group_set:
                return ACLPermissionCheck(allowed=False, reason=f"Access denied: Actor is not a member of group {scope.group_id}.")

            if action in ("read", "project"):
                return ACLPermissionCheck(allowed=True, reason=f"Access granted: Group {scope.group_id} member '{action}'.")

            if action == "write":
                if principal.user_id is not None:
                    return ACLPermissionCheck(allowed=True, reason=f"Access granted: Human member write to group {scope.group_id}.")
                return ACLPermissionCheck(allowed=False, reason=f"Access denied: Bots cannot directly write to group memory without human approval.")

            if action == "delete":
                if principal.user_id is not None:
                    return ACLPermissionCheck(allowed=True, reason=f"Access granted: Human member delete in group {scope.group_id}.")
                return ACLPermissionCheck(allowed=False, reason=f"Access denied: Delete action in group memory restricted to human members.")

        # Scope Kind: Bot Memory
        if scope.kind == ScopeKind.BOT:
            # Bot Self Access
            if principal.bot_id is not None and principal.bot_id == scope.bot_id:
                if scope.group_id is not None and scope.group_id not in group_set:
                    return ACLPermissionCheck(allowed=False, reason=f"Access denied: Bot {principal.bot_id} does not belong to target group {scope.group_id}.")
                if action in ("read", "write", "delete"):
                    return ACLPermissionCheck(allowed=True, reason="Bot self-access granted.")
                return ACLPermissionCheck(allowed=False, reason="Access denied: Bot cannot project personal memory.")

            # Same Group Human Member Access to Bot Memory
            if scope.group_id is not None and scope.group_id in group_set and principal.user_id is not None:
                if action == "read":
                    return ACLPermissionCheck(allowed=True, reason=f"Access granted: Group {scope.group_id} human member read bot memory.")
                return ACLPermissionCheck(allowed=False, reason=f"Access denied: Action '{action}' on bot memory restricted to bot self.")

            return ACLPermissionCheck(allowed=False, reason=f"Access denied: Actor does not belong to bot group {scope.group_id}.")

        return ACLPermissionCheck(allowed=False, reason="Access denied: Unknown scope kind fail-closed protection.")
