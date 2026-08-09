"""Compatibility adapter for the physically isolated Personal Vault."""
from __future__ import annotations

from typing import Any, Mapping

from memory.contracts import (CreatePersonalProjection, CreatePersonalRecord,
                              FormatProjectedContext, IngestPersonalKnowledge, MemoryOperationError,
                              ObservePersonalHabit)
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

    async def ingest(self, command: IngestPersonalKnowledge) -> str:
        user_id = self._user_id(command.scope)
        from ai.personal_vault import ingest_knowledge
        return await ingest_knowledge(
            user_id=user_id, kind=command.kind, statement=command.statement,
            source_type=command.source_type, source_id=command.source_id,
            speaker=command.speaker, subject=command.subject or str(user_id),
            context_kind=command.context_kind, observed_at=command.observed_at,
            asserted_by_user=command.asserted_by_user, sensitivity=command.sensitivity,
        )

    async def observe_habit(self, command: ObservePersonalHabit) -> str:
        user_id = self._user_id(command.scope)
        from ai.personal_vault import observe_habit
        return await observe_habit(
            user_id=user_id, habit_key=command.habit_key, statement=command.statement,
            source_type=command.source_type, source_id=command.source_id,
            context_kind=command.context_kind, observed_at=command.observed_at,
            polarity=command.polarity,
        )

    async def format_projected_context(self, command: FormatProjectedContext) -> str:
        user_id = self._user_id(command.scope)
        if command.scope.group_id is None:
            return ""
        from ai.personal_vault import format_projected_context
        return await format_projected_context(
            user_id=user_id,
            group_id=command.scope.group_id,
            bot_id=command.scope.bot_id,
            purpose=command.purpose,
            char_budget=command.char_budget,
            session_id=command.scope.run_id or "",
        )


    async def rebuild(self, scope: MemoryScope) -> Mapping[str, Any]:
        user_id = self._user_id(scope)
        from ai.personal_vault import rebuild_vault
        return await rebuild_vault(user_id)

    async def export(self, scope: MemoryScope) -> Mapping[str, Any]:
        user_id = self._user_id(scope)
        from ai.personal_vault import export_vault
        return await export_vault(user_id)

    async def delete(self, scope: MemoryScope) -> bool:
        user_id = self._user_id(scope)
        from ai.personal_vault import delete_vault
        return await delete_vault(user_id)

    async def delete_record(self, scope: MemoryScope, record_id: str) -> bool:
        user_id = self._user_id(scope)
        from ai.personal_vault import delete_record
        return await delete_record(user_id=user_id, record_id=record_id)

    async def revoke_projection(self, scope: MemoryScope, projection_id: str) -> bool:
        user_id = self._user_id(scope)
        from ai.personal_vault import revoke_projection
        return await revoke_projection(user_id=user_id, projection_id=projection_id)

    @staticmethod
    def _user_id(scope: MemoryScope) -> int:
        if scope.kind is not ScopeKind.PERSONAL or scope.user_id is None:
            raise MemoryOperationError("personal knowledge operation requires personal scope")
        return scope.user_id
