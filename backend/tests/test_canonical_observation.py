from __future__ import annotations

import os
import json
import tempfile
import unittest

import db

from memory.application.observation import (
    CanonicalBotFactObserver,
    CanonicalObservationEvent,
    CanonicalSummaryObserver,
)
from memory.infrastructure import MemorySchemaManager
from memory.ports import MemoryDatabasePort


class _PathDatabase(MemoryDatabasePort):
    def __init__(self, path: str) -> None:
        self.path = path

    async def connect(self, table: str, group_id: int | None, *, write: bool = False):
        return db.connect(self.path)


class _FactWriter:
    def __init__(self) -> None:
        self.command = None

    async def ingest(self, command):
        self.command = command
        return ("bot-fact:1",)


class CanonicalObservationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.path = tempfile.mktemp(suffix="_canonical_observation.db")
        self.database = _PathDatabase(self.path)
        await MemorySchemaManager(self.database).ensure_group(7)

    async def asyncTearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    async def test_fact_observer_emits_canonical_bot_scope_command(self) -> None:
        writer = _FactWriter()
        observer = CanonicalBotFactObserver(
            self.database, writer
        )
        ids = await observer.observe(CanonicalObservationEvent(
            bot_id=5, group_id=7, role="developer", bot_name="Dev",
            message_id=42, text="The project uses SQLite for durable storage.",
            provider="", model="", thread_id="thread:1",
        ))
        self.assertEqual(ids, ("bot-fact:1",))
        self.assertEqual(writer.command.scope.actor_id, "bot:5")
        self.assertEqual(writer.command.scope.group_id, 7)
        self.assertTrue(writer.command.facts)
        self.assertEqual(writer.command.source_id, "42")

    async def test_summary_observer_persists_canonical_watermark(self) -> None:
        async with db.connect(self.path) as conn:
            await conn.execute(
                """CREATE TABLE messages (
                   id INTEGER PRIMARY KEY, group_id INTEGER, member_id INTEGER,
                   content TEXT, is_deleted INTEGER DEFAULT 0)"""
            )
            for message_id in range(1, 6):
                await conn.execute(
                    """INSERT INTO messages
                       (id,group_id,member_id,content,is_deleted)
                       VALUES (?,?,?,?,0)""",
                    (message_id, 7, 5, f"decision {message_id}")
                )
            await conn.commit()

        async def model(*_args, **_kwargs):
            return {"content": "- canonical summary"}

        observer = CanonicalSummaryObserver(self.database, model, threshold=5)
        result = await observer.observe(CanonicalObservationEvent(
            bot_id=5, group_id=7, role="developer", bot_name="Dev",
            message_id=5, text="decision 5", provider="", model="",
        ))
        self.assertFalse(result["skipped"])
        async with db.connect(self.path) as conn:
            async with conn.execute(
                "SELECT kind,content,metadata_json FROM memory_records WHERE kind='summary'"
            ) as cur:
                row = await cur.fetchone()
        self.assertEqual(row[0], "summary")
        self.assertEqual(row[1], "- canonical summary")
        self.assertEqual(json.loads(row[2])["covered_through_id"], 5)

    async def test_reflection_observer_writes_reflection_and_thread_watermark(self) -> None:
        async with db.connect(self.path) as conn:
            for index in range(5):
                await conn.execute(
                    """INSERT INTO memory_records
                       (record_id,kind,group_id,bot_id,status,content,importance,
                        metadata_json,evidence_json,created_by,effective_from,created_at,updated_at)
                       VALUES (?,?,?,?, 'provisional', ?, ?, ?, '{}', ?, ?, ?, ?)""",
                    (f"fact:{index}", "fact", 7, 5, f"important fact {index}", 0.8,
                     json.dumps({"thread_id": "thread:1"}), "bot:5",
                     (index + 1) * 1000, (index + 1) * 1000, (index + 1) * 1000),
                )
            await conn.commit()

        class _ReflectionWriter:
            def __init__(self):
                self.command = None

            async def ingest(self, command):
                self.command = command
                return ("bot-reflection:1",)

        writer = _ReflectionWriter()

        async def model(*_args, **_kwargs):
            return {"content": "- durable insight | 0.9"}

        from memory.application.observation import CanonicalReflectionObserver
        observer = CanonicalReflectionObserver(
            self.database, writer, model, min_facts=5, importance_threshold=1.0
        )
        result = await observer.observe(CanonicalObservationEvent(
            bot_id=5, group_id=7, role="developer", bot_name="Dev",
            message_id=5, text="", provider="", model="", thread_id="thread:1",
        ))
        self.assertEqual(result["insights"], 1)
        self.assertEqual(writer.command.scope.actor_id, "bot:5")
        async with db.connect(self.path) as conn:
            async with conn.execute(
                "SELECT metadata_json FROM memory_records WHERE kind='reflection_watermark'"
            ) as cur:
                watermark = await cur.fetchone()
        self.assertEqual(json.loads(watermark[0])["thread_id"], "thread:1")

    async def test_tool_compression_commits_episode_and_marks_events(self) -> None:
        async with db.connect(self.path) as conn:
            await conn.execute(
                """CREATE TABLE tool_events (
                   id INTEGER PRIMARY KEY, ts INTEGER, group_id INTEGER,
                   bot_id INTEGER, thread_id TEXT, tool TEXT,
                   args_summary TEXT, result_summary TEXT, is_error INTEGER,
                   files_touched TEXT, command TEXT, compressed INTEGER DEFAULT 0)"""
            )
            for event_id in range(1, 4):
                await conn.execute(
                    """INSERT INTO tool_events
                       (id,ts,group_id,bot_id,thread_id,tool,args_summary,
                        result_summary,is_error,files_touched,command,compressed)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (event_id, event_id * 1000, 7, 5, "thread:1", "read_file",
                     "{}", "ok", 0, "[]", None),
                )
            await conn.commit()

        async def model(*_args, **_kwargs):
            return {"content": "- repeated tool workflow | 0.8"}

        from memory.application.observation import CanonicalToolCompressionObserver
        observer = CanonicalToolCompressionObserver(
            self.database, model, threshold=3, max_batch=3
        )
        result = await observer.observe(CanonicalObservationEvent(
            bot_id=5, group_id=7, role="developer", bot_name="Dev",
            message_id=3, text="", provider="", model="",
        ))
        self.assertEqual(result["compressed"], 3)
        async with db.connect(self.path) as conn:
            async with conn.execute("SELECT COUNT(*) FROM memory_records WHERE kind='tool_episode'") as cur:
                self.assertEqual((await cur.fetchone())[0], 1)
            async with conn.execute("SELECT COUNT(*) FROM tool_events WHERE compressed=0") as cur:
                self.assertEqual((await cur.fetchone())[0], 0)


if __name__ == "__main__":
    unittest.main()
