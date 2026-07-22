"""Authorization boundary for personal-memory application use cases."""
from __future__ import annotations

from typing import Any, Mapping

from memory.contracts import (
    CreatePersonalProjection,
    CreatePersonalRecord,
    FormatProjectedContext,
    IngestPersonalKnowledge,
    MemoryAuthorizationError,
    ObservePersonalHabit,
)
from memory.domain import MemoryScope, Principal
from memory.ports import MemoryACLPort, PersonalKnowledgePort


class AuthorizedPersonalKnowledgeService:
    """Fail-closed application service around the personal knowledge port."""

    def __init__(
        self,
        delegate: PersonalKnowledgePort,
        acl: MemoryACLPort,
        principal: Principal,
    ) -> None:
        self._delegate = delegate
        self._acl = acl
        self._principal = principal

    async def create_record(self, command: CreatePersonalRecord) -> str:
        await self._authorize(command.scope, "write")
        return await self._delegate.create_record(command)

    async def create_projection(self, command: CreatePersonalProjection) -> str:
        await self._authorize(command.scope, "project")
        target = MemoryScope.group(
            group_id=command.target_group_id,
            actor_id=self._principal.actor_id,
            purpose="personal_projection_target",
        )
        await self._authorize(target, "project")
        return await self._delegate.create_projection(command)

    async def ingest(self, command: IngestPersonalKnowledge) -> str:
        await self._authorize(command.scope, "write")
        return await self._delegate.ingest(command)

    async def observe_habit(self, command: ObservePersonalHabit) -> str:
        await self._authorize(command.scope, "write")
        return await self._delegate.observe_habit(command)

    async def format_projected_context(self, command: FormatProjectedContext) -> str:
        await self._authorize(command.scope, "read")
        if command.scope.group_id is not None:
            target = MemoryScope.group(
                group_id=command.scope.group_id,
                actor_id=self._principal.actor_id,
                purpose="personal_projection_read",
            )
            await self._authorize(target, "read")
        return await self._delegate.format_projected_context(command)

    async def rebuild(self, scope: MemoryScope) -> Mapping[str, Any]:
        await self._authorize(scope, "write")
        return await self._delegate.rebuild(scope)

    async def export(self, scope: MemoryScope) -> Mapping[str, Any]:
        await self._authorize(scope, "read")
        return await self._delegate.export(scope)

    async def delete(self, scope: MemoryScope) -> bool:
        await self._authorize(scope, "delete")
        return await self._delegate.delete(scope)

    async def _authorize(self, scope: MemoryScope, action: str) -> None:
        if scope.actor_id != self._principal.actor_id:
            raise MemoryAuthorizationError(
                "memory scope actor does not match authenticated principal"
            )
        check = await self._acl.check_acl(
            scope, principal=self._principal, action=action
        )
        if not check.allowed:
            raise MemoryAuthorizationError(check.reason or "memory access denied")
