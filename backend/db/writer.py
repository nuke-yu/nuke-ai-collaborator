"""DFT-053: process-wide serialized SQLite writer.

Every query previously opened its own aiosqlite connection, and aiosqlite runs
each connection on a dedicated OS thread — so two write coroutines genuinely
hit SQLite's single write lock from two threads at once, producing
"database is locked" under concurrent message writes / status updates.

This module funnels writes through ONE shared connection guarded by ONE async
lock, so write transactions never overlap and SQLite never sees competing
writers. Reads keep using db.connect() (WAL allows concurrent readers).

State is keyed by event-loop id so unit tests (each with a fresh loop) never
reuse a lock or connection bound to a dead loop.
"""
import asyncio
import os
from contextlib import asynccontextmanager

import aiosqlite

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")

_state: dict[int, dict] = {}


def _loop_state() -> dict:
    loop = asyncio.get_running_loop()
    st = _state.get(id(loop))
    if st is None:
        st = {"conn": None, "lock": asyncio.Lock()}
        _state[id(loop)] = st
    return st


async def _get_write_conn(st: dict) -> aiosqlite.Connection:
    if st["conn"] is None:
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        st["conn"] = conn
    return st["conn"]


@asynccontextmanager
async def write_connect():
    """Yield the shared write connection while holding the serial write lock.

    Drop-in for `db.get_db()` at write call sites: the yielded object is an
    aiosqlite connection, so existing query helpers (save_message, ...) work
    unchanged. Concurrent callers queue on the lock instead of racing SQLite.
    """
    st = _loop_state()
    async with st["lock"]:
        conn = await _get_write_conn(st)
        yield conn


async def aclose_writer() -> None:
    """Close the current loop's write connection (call on app shutdown)."""
    loop = asyncio.get_running_loop()
    st = _state.pop(id(loop), None)
    if st and st["conn"] is not None:
        await st["conn"].close()
