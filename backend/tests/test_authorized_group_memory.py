from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from memory.application.authorized_group import AuthorizedGroupKnowledgeService
from memory.contracts import IngestGroupFact, MemoryAuthorizationError, RecallGroupFacts
from memory.domain import MemoryScope, Principal


@pytest.mark.asyncio
async def test_authorized_group_recall_requires_matching_principal() -> None:
    delegate = AsyncMock()
    acl = AsyncMock()
    acl.check_acl.return_value = type("Check", (), {"allowed": True, "reason": ""})()
    service = AuthorizedGroupKnowledgeService(
        delegate, acl, Principal.bot(5, 9)
    )
    query = RecallGroupFacts(
        scope=MemoryScope.bot(group_id=9, bot_id=5, actor_id="bot:5"),
        query="architecture",
    )

    await service.recall_facts(query)
    acl.check_acl.assert_awaited_once()
    delegate.recall_facts.assert_awaited_once_with(query)


@pytest.mark.asyncio
async def test_authorized_group_rejects_scope_actor_mismatch() -> None:
    delegate = AsyncMock()
    acl = AsyncMock()
    service = AuthorizedGroupKnowledgeService(delegate, acl, Principal.bot(5, 9))
    command = IngestGroupFact(
        scope=MemoryScope.group(group_id=9, actor_id="bot:6"),
        statement="x",
        subject_key="x",
        source_type="bot_observation",
        source_id="s",
    )

    with pytest.raises(MemoryAuthorizationError):
        await service.ingest_fact(command)
    acl.check_acl.assert_not_awaited()
