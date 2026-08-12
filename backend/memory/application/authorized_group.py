"""Authorization boundary for group-memory application use cases."""
from __future__ import annotations

from memory.contracts import IngestGroupFact, MemoryAuthorizationError, RecallGroupFacts
from memory.domain import Principal
from memory.ports import GroupKnowledgePort, MemoryACLPort


class AuthorizedGroupKnowledgeService:
    """Fail-closed ACL wrapper for canonical group facts."""

    def __init__(
        self,
        delegate: GroupKnowledgePort,
        acl: MemoryACLPort,
        principal: Principal,
    ) -> None:
        self._delegate = delegate
        self._acl = acl
        self._principal = principal

    async def ingest_fact(self, command: IngestGroupFact) -> str:
        await self._authorize(command.scope, "write")
        return await self._delegate.ingest_fact(command)

    async def recall_facts(self, query: RecallGroupFacts):
        await self._authorize(query.scope, "read")
        return await self._delegate.recall_facts(query)

    async def _authorize(self, scope, action: str) -> None:
        if scope.actor_id != self._principal.actor_id:
            raise MemoryAuthorizationError(
                "memory scope actor does not match authenticated principal"
            )
        result = await self._acl.check_acl(
            scope, principal=self._principal, action=action
        )
        if not result.allowed:
            raise MemoryAuthorizationError(result.reason or "group memory access denied")
