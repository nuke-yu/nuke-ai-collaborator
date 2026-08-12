"""Durable Letta-style working/archival memory blocks."""
from __future__ import annotations

import hashlib
import time
from typing import Any

from memory.infrastructure import SQLiteMemoryDatabase

_database = None


def configure_database(database) -> None:
    """Inject the host database port at the composition boundary."""
    global _database
    _database = database


def _get_database():
    global _database
    if _database is None:
        _database = SQLiteMemoryDatabase()
    return _database


async def _connect(table_name: str, group_id: int, *, write: bool):
    connection = _get_database().connect(table_name, group_id, write=write)
    if hasattr(connection, "__await__"):
        connection = await connection
    return connection


def _select_blocks(records: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    """Application-level bounded selector; tokenizer/budget stays in its port."""
    terms = {part.lower() for part in str(query or "").split() if part.strip()}
    if terms:
        records = [
            item for item in records
            if terms & {part.lower() for part in str(item.get("content", "")).split()}
        ]
    ranked = sorted(
        records,
        key=lambda item: (
            len(terms & {part.lower() for part in str(item.get("content", "")).split()}),
            float(item.get("importance", 0.0)),
            int(item.get("last_accessed_at", 0)),
        ),
        reverse=True,
    )
    return ranked[: max(0, limit)]


async def _ensure(db) -> None:
    await db.execute(
        """CREATE TABLE IF NOT EXISTS letta_memory_blocks (
            block_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL, bot_id INTEGER,
            content TEXT NOT NULL, importance REAL NOT NULL DEFAULT 0.5,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','evicted')),
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            last_accessed_at INTEGER NOT NULL
        )"""
    )
    await db.execute(
        """CREATE INDEX IF NOT EXISTS idx_letta_memory_blocks_scope
           ON letta_memory_blocks(group_id,bot_id,status,importance,last_accessed_at)"""
    )


async def write_memory_block(
    *, group_id: int, bot_id: int | None, content: str,
    importance: float = 0.5, block_id: str | None = None,
) -> str:
    """Persist one deduplicated archival block in the physical group DB."""
    value = str(content or "").strip()
    if not value:
        raise ValueError("memory block content is required")
    if not 0 <= importance <= 1:
        raise ValueError("importance must be between 0 and 1")
    block_id = block_id or "letta:" + hashlib.sha256(
        f"{group_id}:{bot_id}:{value}".encode()
    ).hexdigest()[:24]
    now = int(time.time() * 1000)
    async with await _connect("letta_memory_blocks", group_id, write=True) as db:
        await _ensure(db)
        await db.execute(
            """INSERT INTO letta_memory_blocks
               (block_id,group_id,bot_id,content,importance,created_at,updated_at,last_accessed_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(block_id) DO UPDATE SET content=excluded.content,
               importance=excluded.importance,status='active',updated_at=excluded.updated_at""",
            (block_id, group_id, bot_id, value, importance, now, now, now),
        )
        await db.commit()
    return block_id


async def read_memory_blocks(
    *, group_id: int, bot_id: int | None, query: str, limit: int = 5,
) -> list[dict[str, Any]]:
    """Read active archival blocks through the bounded Letta lexical selector."""
    async with await _connect("letta_memory_blocks", group_id, write=True) as db:
        await _ensure(db)
        async with db.execute(
            """SELECT block_id,content,importance,created_at,updated_at,last_accessed_at
               FROM letta_memory_blocks WHERE group_id=? AND (bot_id=? OR bot_id IS NULL)
                 AND status='active'""",
            (group_id, bot_id),
        ) as cur:
            rows = await cur.fetchall()
        records = [
            {"block_id": row[0], "content": row[1], "importance": row[2],
             "created_at": row[3], "updated_at": row[4], "last_accessed_at": row[5]}
            for row in rows
        ]
        selected = _select_blocks(records, query, limit)
        now = int(time.time() * 1000)
        for item in selected:
            await db.execute(
                "UPDATE letta_memory_blocks SET last_accessed_at=? WHERE block_id=? AND group_id=?",
                (now, item["block_id"], group_id),
            )
        await db.commit()
    return selected


async def evict_memory_blocks(*, group_id: int, bot_id: int | None, keep: int = 100) -> int:
    """Evict the lowest-value active blocks while retaining an audit row."""
    if keep < 1:
        raise ValueError("keep must be positive")
    async with await _connect("letta_memory_blocks", group_id, write=True) as db:
        await _ensure(db)
        async with db.execute(
            """SELECT block_id FROM letta_memory_blocks
               WHERE group_id=? AND (bot_id=? OR bot_id IS NULL) AND status='active'
               ORDER BY importance DESC,last_accessed_at DESC,block_id DESC""",
            (group_id, bot_id),
        ) as cur:
            ids = [str(row[0]) for row in await cur.fetchall()]
        stale = ids[keep:]
        if stale:
            placeholders = ",".join("?" for _ in stale)
            await db.execute(
                f"UPDATE letta_memory_blocks SET status='evicted',updated_at=? "
                f"WHERE group_id=? AND block_id IN ({placeholders})",
                (int(time.time() * 1000), group_id, *stale),
            )
        await db.commit()
    return len(stale)
