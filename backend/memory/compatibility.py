"""Narrow compatibility primitives for pre-canonical callers.

This module contains no Memory business logic and no vector-store access.  It
only exposes the canonical SQLite router to old public function facades while
those facades are being retired.
"""
from __future__ import annotations

from memory.infrastructure import SQLiteMemoryDatabase


async def _memory_db(table_name: str, group_id: int | None, *, write: bool):
    """Resolve a legacy call against a fresh canonical router.

    The router cache is intentionally scoped to one call here because legacy
    test/tools can replace the process DB path between invocations.
    """
    return await SQLiteMemoryDatabase().connect(table_name, group_id, write=write)

__all__ = ["_memory_db"]
