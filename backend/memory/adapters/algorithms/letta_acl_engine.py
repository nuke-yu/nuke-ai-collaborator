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
from typing import Any, Callable, Mapping, Sequence

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
    def estimate_tokens(
        text: str, tokenizer: Callable[[str], Any] | Any | None = None
    ) -> int:
        """Estimate token count for string content (1 token ~ 4 chars for EN / 1 char for CJK)."""
        if not text:
            return 0
        if tokenizer is not None:
            try:
                encoded = tokenizer.encode(text) if hasattr(tokenizer, "encode") else tokenizer(text)
                if hasattr(encoded, "ids"):
                    return len(encoded.ids)
                if isinstance(encoded, Mapping) and "input_ids" in encoded:
                    return len(encoded["input_ids"])
                return len(encoded)
            except Exception:
                # Tokenizer failures must not break the model loop; retain the
                # deterministic conservative fallback below.
                pass
        cjk_chars = len([c for c in text if '\u4e00' <= c <= '\u9fff'])
        ascii_chars = len(text) - cjk_chars
        return cjk_chars + (ascii_chars // 4)

    @classmethod
    def truncate_text_to_tokens(
        cls, text: str, max_tokens: int, tokenizer: Callable[[str], Any] | Any | None = None
    ) -> str:
        """Bound untrusted context before it enters a model prompt.

        The project deliberately avoids a provider-specific tokenizer in this
        low-level adapter.  Use the same conservative estimator as
        ``calculate_context_budget`` and preserve the beginning of the value,
        which contains the memory envelope and provenance markers.
        """
        if not text or max_tokens <= 0:
            return ""
        if cls.estimate_tokens(text, tokenizer) <= max_tokens:
            return text
        marker = "\n[context truncated by memory budget]"
        body_tokens = max(1, max_tokens - cls.estimate_tokens(marker, tokenizer))
        if tokenizer is not None:
            # Binary search the largest prefix accepted by the real tokenizer.
            low, high = 0, len(text)
            while low < high:
                mid = (low + high + 1) // 2
                if cls.estimate_tokens(text[:mid], tokenizer) <= body_tokens:
                    low = mid
                else:
                    high = mid - 1
            return text[:low].rstrip() + marker
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
        tokenizer: Callable[[str], Any] | Any | None = None,
    ) -> ContextBudgetAllocation:
        """Calculate token allocation budget according to Letta / MemGPT context specification."""
        sys_tokens = self.estimate_tokens(system_prompt, tokenizer)
        work_tokens = self.estimate_tokens(working_memory, tokenizer)
        rec_tokens = self.estimate_tokens(recall_memory, tokenizer)

        schema_json = json.dumps(list(tool_schemas), default=str) if tool_schemas else ""
        schema_tokens = self.estimate_tokens(schema_json, tokenizer)

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

    @classmethod
    def page_memory(
        cls,
        records: Sequence[Mapping[str, Any]],
        max_tokens: int,
        tokenizer: Callable[[str], Any] | Any | None = None,
    ) -> list[dict[str, Any]]:
        """Page archival records into working memory by importance density.

        Records are sorted by explicit importance (then recency) and admitted
        until the token budget is exhausted. The method never mutates input or
        performs I/O, making it safe for active memory-function callers.
        """
        if max_tokens <= 0:
            return []
        ranked = sorted(
            (dict(record) for record in records if record.get("content")),
            key=lambda item: (
                float(item.get("importance", item.get("confidence", 0.0)) or 0.0),
                float(item.get("updated_at", item.get("created_at", 0)) or 0),
            ),
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        used = 0
        for record in ranked:
            cost = cls.estimate_tokens(str(record["content"]), tokenizer)
            if cost <= 0:
                continue
            if used + cost > max_tokens:
                continue
            selected.append(record)
            used += cost
        return selected

    @classmethod
    def memory_read(
        cls,
        records: Sequence[Mapping[str, Any]],
        query: str,
        *,
        limit: int = 5,
        tokenizer: Callable[[str], Any] | Any | None = None,
    ) -> list[dict[str, Any]]:
        """Letta-style explicit archival read without granting tool access.

        Retrieval is lexical and deterministic; callers can replace it with a
        vector provider while retaining the same bounded result contract.
        """
        query_terms = {term.lower() for term in str(query or "").split() if term}
        ranked = []
        for record in records:
            item = dict(record)
            content_terms = {term.lower() for term in str(item.get("content", "")).split()}
            overlap = len(query_terms & content_terms)
            if overlap:
                item["_memory_score"] = overlap / max(1, len(query_terms))
                ranked.append(item)
        ranked.sort(key=lambda item: (float(item["_memory_score"]), float(item.get("importance", 0) or 0)), reverse=True)
        return cls.page_memory(ranked[: max(1, limit * 2)], max_tokens=10**9, tokenizer=tokenizer)[: max(0, limit)]

    @staticmethod
    def memory_write(
        working_memory: Sequence[Mapping[str, Any]],
        content: str,
        *,
        max_items: int = 20,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Append a bounded working-memory entry with exact-content deduplication."""
        value = str(content or "").strip()
        if not value:
            return [dict(item) for item in working_memory]
        result = [dict(item) for item in working_memory if str(item.get("content", "")).strip() != value]
        entry = {"content": value}
        if metadata:
            entry.update(dict(metadata))
        result.append(entry)
        return result[-max(1, max_items):]

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
