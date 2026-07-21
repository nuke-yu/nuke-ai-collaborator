"""Compatibility adapter for the physically isolated Personal Vault."""
from __future__ import annotations

from typing import Any, Mapping

from memory.contracts import MemoryOperationError
from memory.domain import MemoryScope, ScopeKind


class LegacyPersonalKnowledgeAdapter:
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

