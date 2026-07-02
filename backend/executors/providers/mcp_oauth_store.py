"""Persistent OAuth token storage for MCP servers (collector-owned).

MCP OAuth tokens are cross-group, collector-local state — not chat/group data —
so they live in a small dedicated SQLite file the collector owns, rather than the
central chat DB. One row per server; implements the mcp SDK's TokenStorage
protocol (get/set tokens + client info), so OAuthClientProvider can persist and
refresh tokens across collector restarts.
"""
import asyncio
import logging
import time
import os
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

# Env-overridable (read-only / container deploys can't write next to the source).
_DEFAULT_DB = os.environ.get("MCP_OAUTH_DB") or str(
    Path(__file__).resolve().parent.parent.parent / "mcp_oauth.db"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_oauth (
    server_name      TEXT PRIMARY KEY,
    tokens_json      TEXT,
    client_info_json TEXT,
    updated_at       REAL
)
"""

# One shared connection per db_path (avoids per-op connect churn — each aiosqlite
# connection spins up its own OS thread). In the collector (single process/loop)
# this is one long-lived connection reused for all reads/writes; aiosqlite
# serializes ops on a connection, so concurrent refresh is safe without a lock.
# Keyed by path so tests using unique temp DBs never reuse a connection across
# event loops; call aclose_all() on shutdown / in test teardown.
_conns: dict[str, aiosqlite.Connection] = {}
_conns_lock = asyncio.Lock()


async def _get_conn(path: str) -> aiosqlite.Connection:
    c = _conns.get(path)
    if c is not None:
        return c
    async with _conns_lock:
        c = _conns.get(path)
        if c is None:
            c = await aiosqlite.connect(path)
            await c.execute(_SCHEMA)
            await c.commit()
            _conns[path] = c
    return c


async def aclose_all() -> None:
    """Close all cached connections (collector shutdown / test teardown)."""
    for path, c in list(_conns.items()):
        try:
            await c.close()
        except Exception:
            log.exception("MCP OAuth store: failed to close cached connection for %s", path)
    _conns.clear()


class MCPTokenStorage:
    """SDK TokenStorage impl, one instance per MCP server."""

    def __init__(self, server_name: str, db_path: str | Path | None = None):
        self.server_name = server_name
        self.db_path = str(db_path or _DEFAULT_DB)

    async def _read(self, column: str) -> str | None:
        c = await _get_conn(self.db_path)
        cur = await c.execute(
            f"SELECT {column} FROM mcp_oauth WHERE server_name=?", (self.server_name,)
        )
        row = await cur.fetchone()
        return row[0] if row and row[0] else None

    async def _write(self, column: str, value: str) -> None:
        # NOTE: `column` is f-string-interpolated but is ALWAYS an internal literal
        # ("tokens_json" / "client_info_json") — never external input. Do NOT
        # extend this to accept caller-supplied column names (SQL injection).
        c = await _get_conn(self.db_path)
        await c.execute(
            f"INSERT INTO mcp_oauth (server_name, {column}, updated_at) "
            f"VALUES (?, ?, ?) "
            f"ON CONFLICT(server_name) DO UPDATE SET "
            f"{column}=excluded.{column}, updated_at=excluded.updated_at",
            (self.server_name, value, time.time()),
        )
        await c.commit()

    # ── SDK TokenStorage protocol ──────────────────────────────────────────
    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        raw = await self._read("tokens_json")
        return OAuthToken.model_validate_json(raw) if raw else None

    async def set_tokens(self, tokens) -> None:
        await self._write("tokens_json", tokens.model_dump_json())

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        raw = await self._read("client_info_json")
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def set_client_info(self, client_info) -> None:
        await self._write("client_info_json", client_info.model_dump_json())

    async def clear(self) -> None:
        """Drop this server's stored credentials (e.g. on auth revocation)."""
        c = await _get_conn(self.db_path)
        await c.execute("DELETE FROM mcp_oauth WHERE server_name=?", (self.server_name,))
        await c.commit()
