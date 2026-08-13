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
from memory.ports import MemoryACLPort, PersonalKnowledgePort, PersonalVaultPolicyPort


class AuthorizedPersonalKnowledgeService:
    """Fail-closed application service around the personal knowledge port."""

    def __init__(
        self,
        delegate: PersonalKnowledgePort,
        acl: MemoryACLPort,
        principal: Principal,
        vault_policy: PersonalVaultPolicyPort | None = None,
    ) -> None:
        self._delegate = delegate
        self._acl = acl
        self._principal = principal
        self._vault_policy = vault_policy

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

    async def get_record_impact(self, scope: MemoryScope, record_id: str) -> Mapping[str, Any]:
        await self._authorize(scope, "read")
        return await self._delegate.get_record_impact(scope, record_id)

    async def delete(self, scope: MemoryScope) -> bool:
        await self._authorize(scope, "delete")
        return await self._delegate.delete(scope)

    async def delete_record(self, scope: MemoryScope, record_id: str) -> bool:
        await self._authorize(scope, "delete")
        return await self._delegate.delete_record(scope, record_id)

    async def revoke_projection(self, scope: MemoryScope, projection_id: str) -> bool:
        await self._authorize(scope, "delete")
        return await self._delegate.revoke_projection(scope, projection_id)

    async def _authorize(self, scope: MemoryScope, action: str) -> None:
        if scope.actor_id != self._principal.actor_id:
            raise MemoryAuthorizationError(
                "memory scope actor does not match authenticated principal"
            )
        check = await self._acl.check_acl(
            scope, principal=self._principal, action=action
        )
        # OpenMemory-style explicit rules can only tighten the platform ACL.
        # An allow rule never grants access that the Nuke scope matrix denied.
        if check.allowed and self._principal.user_id is not None and self._vault_policy is not None:
            try:
                explicit = await self._vault_policy.evaluate_rule(
                    user_id=self._principal.user_id,
                    subject_type="user",
                    subject_id=str(self._principal.user_id),
                    object_type=scope.kind.value,
                    object_id=(
                        str(scope.bot_id)
                        if scope.bot_id is not None
                        else str(scope.group_id or self._principal.user_id)
                    ),
                    action=action,
                )
                if explicit is False:
                    from dataclasses import replace

                    check = replace(
                        check,
                        allowed=False,
                        reason="Access denied: explicit OpenMemory ABAC deny rule.",
                    )
            except Exception:
                # Policy lookup failure is an authorization failure.  Never
                # turn an unavailable explicit-deny store into an allow.
                from dataclasses import replace

                check = replace(
                    check,
                    allowed=False,
                    reason="Personal Vault policy unavailable; access denied.",
                )
        if self._principal.user_id is not None and self._vault_policy is not None:
            try:
                await self._vault_policy.record_audit(
                    user_id=self._principal.user_id,
                    actor_id=self._principal.actor_id,
                    scope_kind=scope.kind.value,
                    group_id=scope.group_id,
                    bot_id=scope.bot_id,
                    action=action,
                    allowed=check.allowed,
                    reason=check.reason or "",
                )
            except Exception:
                # Authorization is not complete until its audit record is
                # durable.  Fail closed rather than allowing an unaudited
                # Personal Vault operation.
                from dataclasses import replace

                check = replace(
                    check,
                    allowed=False,
                    reason="Personal Vault audit unavailable; access denied.",
                )
        if not check.allowed:
            raise MemoryAuthorizationError(check.reason or "memory access denied")
