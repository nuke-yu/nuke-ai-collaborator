"""Safe Letta-style active memory functions and working-set paging.

This controller is deliberately narrower than the ToolRouter.  It exposes
bounded memory operations to an agent while keeping ACL, canonical writes and
context assembly in the Memory application boundary.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from memory.contracts import (
    ExtractedFactObservation,
    IngestBotFactObservations,
    MemoryAuthorizationError,
    MemoryOperationError,
)
from memory.domain import MemoryScope, Principal, ScopeKind
from memory.domain.safety import safe_memory_text
from memory.ports import MemoryACLPort, MemoryDatabasePort, ProjectionOutboxPort

from .bot_facts import BotFactObservationService


def _estimate_tokens(value: str) -> int:
    cjk = sum("\u4e00" <= char <= "\u9fff" for char in value)
    return cjk + max(0, (len(value) - cjk) // 4)


@dataclass(frozen=True, slots=True)
class MemoryFunctionResult:
    operation: str
    records: tuple[Mapping[str, Any], ...] = ()
    record_ids: tuple[str, ...] = ()
    working_memory: tuple[Mapping[str, Any], ...] = ()


@dataclass(slots=True)
class LettaWorkingMemory:
    """Run-local working set; it never mutates the Tool Loop message list."""

    records: list[dict[str, Any]] = field(default_factory=list)

    def write(self, content: str, *, importance: float = 0.5, max_items: int = 20) -> None:
        value = safe_memory_text(content)
        if not value:
            return
        self.records = [item for item in self.records if item.get("content") != value]
        self.records.append({
            "content": value,
            "importance": max(0.0, min(1.0, float(importance))),
            "updated_at": int(time.time() * 1000),
        })
        self.records = self.records[-max(1, max_items):]

    def page(self, max_tokens: int) -> tuple[Mapping[str, Any], ...]:
        if max_tokens <= 0:
            return ()
        selected: list[dict[str, Any]] = []
        used = 0
        for item in sorted(
            self.records,
            key=lambda value: (float(value.get("importance", 0)), int(value.get("updated_at", 0))),
            reverse=True,
        ):
            cost = _estimate_tokens(str(item.get("content", "")))
            if cost and used + cost <= max_tokens:
                selected.append(dict(item))
                used += cost
        return tuple(selected)


class LettaMemoryFunctionController:
    """Application boundary for model-initiated memory operations."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        projection_outbox: ProjectionOutboxPort,
        acl: MemoryACLPort,
    ) -> None:
        self._database = database
        self._acl = acl
        self._facts = BotFactObservationService(database, projection_outbox)

    @staticmethod
    def tool_schemas() -> tuple[dict[str, Any], ...]:
        """Return the opt-in, bounded schemas exposed to a model."""
        return (
            {"type": "function", "function": {"name": "memory_read", "description": "Read relevant durable memory for this bot and group.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "memory_write", "description": "Write a verified observation to bot memory; include its source id.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "source_id": {"type": "string"}, "importance": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["content", "source_id"]}}},
        )

    async def _authorize(self, scope: MemoryScope, action: str) -> None:
        if scope.group_id is None:
            raise MemoryAuthorizationError("Letta memory functions require a group scope")
        principal: Principal
        if scope.actor_id.startswith("bot:") and scope.bot_id is not None:
            principal = Principal.bot(scope.bot_id, group_id=scope.group_id)
        elif scope.actor_id.startswith("user:"):
            principal = Principal.user(int(scope.actor_id.split(":", 1)[1]), [scope.group_id])
        else:
            principal = Principal(actor_id=scope.actor_id, group_ids=frozenset({scope.group_id}))
        decision = await self._acl.check_acl(scope, principal=principal, action=action)
        if not decision.allowed:
            raise MemoryAuthorizationError(decision.reason)

    async def memory_read(self, scope: MemoryScope, query: str, *, limit: int = 5) -> MemoryFunctionResult:
        await self._authorize(scope, "read")
        if not query.strip():
            raise ValueError("memory_read query is required")
        if limit < 1 or limit > 50:
            raise ValueError("memory_read limit must be between 1 and 50")
        terms = tuple(dict.fromkeys(part.lower() for part in query.split() if part.strip()))
        bot_filter = " AND (bot_id=? OR bot_id IS NULL)" if scope.kind is ScopeKind.BOT else ""
        params: list[Any] = [scope.group_id]
        if scope.kind is ScopeKind.BOT:
            params.append(scope.bot_id)
        async with await self._database.connect("memory_records", scope.group_id, write=False) as db:
            async with db.execute(
                """SELECT record_id,content,importance,created_at,updated_at,source_ids
                   FROM memory_records
                   WHERE group_id=? AND status IN ('active','provisional')""" + bot_filter,
                tuple(params[:1 + (1 if scope.kind is ScopeKind.BOT else 0)]),
            ) as cursor:
                rows = await cursor.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            content = safe_memory_text(row[1])
            overlap = sum(1 for term in terms if term in content.lower())
            if overlap:
                result.append({
                    "record_id": str(row[0]), "content": content,
                    "importance": float(row[2] or 0), "created_at": int(row[3] or 0),
                    "updated_at": int(row[4] or 0), "source_ids": str(row[5] or "[]"),
                    "_score": overlap,
                })
        result.sort(key=lambda item: (item["_score"], item["importance"], item["updated_at"]), reverse=True)
        return MemoryFunctionResult("memory_read", records=tuple(result[:limit]))

    async def memory_write(
        self, scope: MemoryScope, content: str, *, source_id: str, importance: float = 0.5,
        projection_id: str | None = None,
    ) -> MemoryFunctionResult:
        await self._authorize(scope, "write")
        if scope.kind is not ScopeKind.BOT or scope.bot_id is None or scope.actor_id != f"bot:{scope.bot_id}":
            raise MemoryAuthorizationError("memory_write requires the owning bot scope")
        value = safe_memory_text(content)
        source = source_id.strip()
        if not value or not source:
            raise ValueError("memory_write content and source_id are required")
        projection = projection_id or "letta-write:" + source
        ids = await self._facts.ingest(IngestBotFactObservations(
            scope=scope,
            source_id=source,
            facts=(ExtractedFactObservation(value, importance, projection),),
            role="letta_memory_function",
            provider="letta_runtime",
            model="active_memory_function",
            thread_id=scope.thread_id or "",
        ))
        return MemoryFunctionResult("memory_write", record_ids=ids)

    async def execute(self, scope: MemoryScope, operation: str, arguments: Mapping[str, Any]) -> MemoryFunctionResult:
        """Dispatch only the bounded memory function allow-list."""
        if operation == "memory_read":
            return await self.memory_read(scope, str(arguments.get("query", "")), limit=int(arguments.get("limit", 5)))
        if operation == "memory_write":
            return await self.memory_write(
                scope, str(arguments.get("content", "")), source_id=str(arguments.get("source_id", "")),
                importance=float(arguments.get("importance", 0.5)),
                projection_id=str(arguments.get("projection_id", "")).strip() or None,
            )
        raise MemoryOperationError(f"unsupported Letta memory function: {operation}")
