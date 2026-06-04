"""DFT-053: process-wide serialized SQLite writer.

Every query previously opened its own aiosqlite connection, and aiosqlite runs
each connection on a dedicated OS thread — so two write coroutines genuinely
hit SQLite's single write lock from two threads at once, producing
"database is locked" under concurrent message writes / status updates.

This module funnels writes through ONE shared connection-per-database guarded by
ONE async lock, so write transactions never overlap and SQLite never sees
competing writers. Reads keep using db.connect() (WAL allows concurrent readers).

CELL-03 (Project-Cell Isolation V3): state is keyed by (event-loop id, db_path)
so a single worker can hold an independent serialized writer for each of the
per-group private databases it owns — each path gets its own lock and connection,
and writes to different group DBs never contend. `write_connect()` / `aclose_writer()`
take an optional path and default to DB_PATH, so existing single-DB call sites are
unchanged. Keying by loop id also keeps unit tests (each with a fresh loop) from
reusing a lock or connection bound to a dead loop.
"""
import asyncio
import os
from contextlib import asynccontextmanager

import aiosqlite

from db.context import resolve as _route

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")

import weakref

# (loop_id, db_path) -> {"conn": aiosqlite.Connection | None, "lock": asyncio.Lock}
_state: dict[tuple[int, str], dict] = {}


def _resolve(path: str | None) -> str:
    # CELL-04: explicit path wins; else the bound current group DB (contextvar);
    # else DB_PATH (read at call time so tests that monkeypatch it still route).
    return _route(path, DB_PATH)


def _conn_state(db_path: str) -> dict:
    loop = asyncio.get_running_loop()
    lid = id(loop)
    key = (lid, db_path)
    st = _state.get(key)
    if st is None:
        st = {"conn": None, "lock": asyncio.Lock()}
        _state[key] = st
        
        # Cleanup callback to remove stale keys when the event loop is garbage collected
        def _cleanup(loop_id):
            for k in [k for k in list(_state) if k[0] == loop_id]:
                _state.pop(k, None)
        
        weakref.finalize(loop, _cleanup, lid)
    return st


async def _get_write_conn(st: dict, db_path: str) -> aiosqlite.Connection:
    if st["conn"] is None:
        # DFT-066: aiosqlite runs each connection on its own OS thread. A writer
        # connection is only closed by aclose_writer() on graceful app shutdown /
        # group eviction — in tests (and any abnormal exit) nothing closes it,
        # leaving a NON-daemon thread that blocks interpreter exit and hangs the
        # process. Mark the thread daemon before it starts so process exit is never
        # blocked; each write is committed per-call, so an abrupt exit is WAL
        # crash-safe.
        conn = aiosqlite.connect(db_path)
        conn.daemon = True
        conn = await conn
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        st["conn"] = conn
    return st["conn"]


@asynccontextmanager
async def write_connect(path: str | None = None):
    """Yield the serialized write connection for `path` (default DB_PATH) while
    holding that database's write lock.

    Drop-in for `db.get_db()` at write call sites: the yielded object is an
    aiosqlite connection, so existing query helpers (save_message, ...) work
    unchanged. Concurrent callers to the SAME db queue on its lock; callers to
    DIFFERENT dbs (e.g. different group private DBs) never contend.
    """
    db_path = _resolve(path)
    st = _conn_state(db_path)
    async with st["lock"]:
        conn = await _get_write_conn(st, db_path)
        yield conn


async def aclose_writer(path: str | None = None) -> None:
    """Close write connection(s) for the current loop.

    - path given  → close just that database's writer (e.g. group eviction).
    - path is None → close ALL of this loop's writers (app shutdown).
    """
    lid = id(asyncio.get_running_loop())
    if path is not None:
        keys = [(lid, _resolve(path))]
    else:
        keys = [k for k in list(_state) if k[0] == lid]
    for k in keys:
        st = _state.pop(k, None)
        if st and st["conn"] is not None:
            await st["conn"].close()
