import asyncio

import pytest

from scripts.rebuild_chroma_fact_ids import _rebuild_vector_ids

from memory.adapters.projections import maintenance


def test_rebuild_deletes_only_target_bot_facts_and_reflections():
    rows = {
        "ids": ["fact-a", "refl-a", "tools-a", "fact-other-group", "fact-other-bot"],
        "metadatas": [
            {"group_id": 3, "bot_id": 10, "mem_type": "fact"},
            {"group_id": 3, "bot_id": 10, "mem_type": "reflection"},
            {"group_id": 3, "bot_id": 10, "mem_type": "tool_episode"},
            {"group_id": 4, "bot_id": 10, "mem_type": "fact"},
            {"group_id": 3, "bot_id": 11, "mem_type": "fact"},
        ],
    }

    selected = _rebuild_vector_ids(rows, {(3, 10)})

    assert selected == ["fact-a", "refl-a"]


class _Collection:
    def __init__(self, rows):
        self.rows = rows
        self.upserts = []
        self.deletes = []

    def get(self, **kwargs):
        return self.rows

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def delete(self, **kwargs):
        self.deletes.append(kwargs)


def test_legacy_migration_refuses_missing_scope_before_writes(monkeypatch):
    collection = _Collection({
        "ids": ["42_0"],
        "documents": ["fact"],
        "metadatas": [{}],
    })
    monkeypatch.setattr(maintenance, "_get_collection", lambda: collection)

    stats = asyncio.run(maintenance.migrate_legacy_fact_ids(dry_run=True))
    assert stats["missing_scope"] == 1
    assert collection.upserts == []
    assert collection.deletes == []

    with pytest.raises(maintenance.LegacyFactMigrationError):
        asyncio.run(maintenance.migrate_legacy_fact_ids())
    assert collection.upserts == []
    assert collection.deletes == []


def test_legacy_migration_namespaces_group_and_bot_in_id(monkeypatch):
    collection = _Collection({
        "ids": ["42_0"],
        "documents": ["fact"],
        "metadatas": [{"group_id": 7, "bot_id": 3}],
    })
    monkeypatch.setattr(maintenance, "_get_collection", lambda: collection)

    stats = asyncio.run(maintenance.migrate_legacy_fact_ids())
    assert stats["migrated"] == 1
    assert collection.upserts[0]["ids"] == ["fact_3_7_42_0"]
    assert collection.deletes == [{"ids": ["42_0"]}]
