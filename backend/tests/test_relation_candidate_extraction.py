from unittest.mock import AsyncMock

import pytest

from memory.application.relations import CanonicalRelationService
from memory.domain import MemoryRelationType, MemoryScope


@pytest.mark.asyncio
async def test_relation_candidates_reject_unknown_and_keep_valid(monkeypatch):
    service = CanonicalRelationService(None)
    created = []

    async def fake_create(command):
        created.append(command)
        return "relation:1"

    monkeypatch.setattr(service, "create", fake_create)
    ai = AsyncMock(return_value={"content": "[{\"from_record_id\":\"a\",\"to_record_id\":\"b\",\"relation_type\":\"supports\",\"evidence\":{\"score\":0.9}},{\"from_record_id\":\"x\",\"to_record_id\":\"y\",\"relation_type\":\"unknown\"}]"})
    result = await service.create_from_candidates(
        scope=MemoryScope.group(group_id=7, actor_id="bot:1"), text="a supports b", source_id="case:1", ai_call_fn=ai
    )
    assert result == ("relation:1",)
    assert created[0].relation_type is MemoryRelationType.SUPPORTS
