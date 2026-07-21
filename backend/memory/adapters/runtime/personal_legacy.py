"""Compatibility adapter for the physically isolated Personal Vault."""
from __future__ import annotations

from typing import Any, Mapping

from memory.contracts import CreatePersonalProjection, CreatePersonalRecord, MemoryOperationError
from memory.domain import MemoryScope, ScopeKind


class LegacyPersonalKnowledgeAdapter:
    async def create_record(self, command: CreatePersonalRecord) -> str:
        user_id = self._user_id(command.scope)
        from ai.personal_vault import add_record
        return await add_record(
            user_id=user_id,
            kind=command.kind,
            content=command.content,
            source_type=command.source_type,
            source_id=command.source_id,
            speaker=command.speaker,
            subject=str(user_id),
            authority="user_statement",
            sensitivity=command.sensitivity,
            confidence=1.0,
            explicit=True,
        )

    async def create_projection(self, command: CreatePersonalProjection) -> str:
        user_id = self._user_id(command.scope)
        if command.scope.group_id is not None and command.scope.group_id != command.target_group_id:
            raise MemoryOperationError("projection target does not match authorized group scope")
        from ai.personal_vault import project
        return await project(
            user_id=user_id,
            record_id=command.record_id,
            group_id=command.target_group_id,
            bot_id=command.target_bot_id,
            purpose=command.purpose,
            expires_at=command.expires_at,
        )

    async def export(self, scope: MemoryScope) -> Mapping[str, Any]:
        user_id = self._user_id(scope)
        from ai.personal_vault import export_vault
        return await export_vault(user_id)

    async def delete(self, scope: MemoryScope) -> bool:
        user_id = self._user_id(scope)
        from ai.personal_vault import delete_vault
        return await delete_vault(user_id)

    @staticmethod
    def _user_id(scope: MemoryScope) -> int:
        if scope.kind is not ScopeKind.PERSONAL or scope.user_id is None:
            raise MemoryOperationError("personal knowledge operation requires personal scope")
        return scope.user_id
