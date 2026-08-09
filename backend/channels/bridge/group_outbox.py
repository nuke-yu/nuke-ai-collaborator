"""Group-owned durable outbox and relay for Channel notifications.

The writer is deliberately connection-oriented: callers append on the same
Group SQLite connection and transaction that commits the business event. The
relay is the only component that crosses into Channel-owned persistence.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

from channels.core import ChannelConversation, ChannelIdentity, OutboundEnvelope
from channels.stores import ChannelPayloadTooLargeError, ChannelStore, sanitize_text_for_storage


class GroupChannelOutboxError(RuntimeError):
    pass


_DDL = """CREATE TABLE IF NOT EXISTS group_channel_event_outbox (
    event_id TEXT PRIMARY KEY,
    source_event_id TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL,
    lease_owner TEXT,
    lease_expires_at INTEGER,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)"""


async def initialize_group_channel_outbox(db: aiosqlite.Connection) -> None:
    await db.execute(_DDL)
    columns = {row[1] for row in await (await db.execute("PRAGMA table_info(group_channel_event_outbox)")).fetchall()}
    if "source_event_id" not in columns:
        await db.execute("ALTER TABLE group_channel_event_outbox ADD COLUMN source_event_id TEXT NOT NULL DEFAULT ''")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_group_channel_outbox_due ON group_channel_event_outbox(state,next_attempt_at)")


class GroupChannelOutboxWriter:
    """Append a Channel intent inside an already-open Group transaction."""

    @staticmethod
    async def append(db: aiosqlite.Connection, envelope: OutboundEnvelope) -> bool:
        if not db.in_transaction:
            raise GroupChannelOutboxError("Group Channel outbox append must run inside the Group transaction")
        await initialize_group_channel_outbox(db)
        now = int(time.time() * 1000)
        raw_payload = json.dumps({"outbound": envelope.to_dict()}, ensure_ascii=False, sort_keys=True)
        if len(raw_payload.encode("utf-8")) > 256_000:
            raise ChannelPayloadTooLargeError("Group Channel outbox payload exceeds 256000 bytes")
        payload = _safe_json({"outbound": envelope.to_dict()})
        cursor = await db.execute(
            """INSERT OR IGNORE INTO group_channel_event_outbox
               (event_id,source_event_id,group_id,event_type,payload_json,next_attempt_at,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (envelope.idempotency_key, envelope.source_event_id or envelope.idempotency_key, envelope.group_id, envelope.event_type, payload, now, now, now),
        )
        return cursor.rowcount == 1


class GroupChannelOutboxRelay:
    """Move committed Group outbox entries to Channel Store with replay safety."""

    def __init__(self, group_db_path: str | Path, channel_store: ChannelStore, *, lease_ms: int = 30_000, owner_id: str | None = None):
        self.group_db_path = str(group_db_path)
        self.channel_store = channel_store
        self.lease_ms = lease_ms
        self.owner_id = owner_id or f"group-channel-relay:{uuid.uuid4()}"
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")

    async def relay_once(self, *, now_ms: int | None = None) -> bool:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        async with aiosqlite.connect(self.group_db_path) as db:
            await initialize_group_channel_outbox(db)
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """UPDATE group_channel_event_outbox
                   SET state='retrying',lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                   WHERE state='sending' AND lease_expires_at<=?""",
                (now, now),
            )
            async with db.execute(
                """SELECT event_id,payload_json,attempts FROM group_channel_event_outbox
                   WHERE state IN ('pending','retrying') AND next_attempt_at<=?
                   ORDER BY created_at LIMIT 1""",
                (now,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                return False
            event_id, payload_json, attempts = row
            await db.execute(
                """UPDATE group_channel_event_outbox
                   SET state='sending',attempts=attempts+1,lease_owner=?,lease_expires_at=?,updated_at=?
                   WHERE event_id=?""",
                (self.owner_id, now + self.lease_ms, now, event_id),
            )
            await db.commit()
        try:
            envelope = _outbound_from_json(payload_json)
            await self.channel_store.enqueue_outbound(envelope)
        except Exception as exc:
            await self._finish(event_id, "retrying", str(exc), now + 1_000)
        else:
            await self._finish(event_id, "forwarded", None, now)
        return True

    async def _finish(self, event_id: str, state: str, error: str | None, next_attempt_at: int) -> None:
        now = int(time.time() * 1000)
        async with aiosqlite.connect(self.group_db_path) as db:
            await db.execute(
                """UPDATE group_channel_event_outbox
                   SET state=?,last_error=?,next_attempt_at=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                   WHERE event_id=? AND state='sending' AND lease_owner=?""",
                (state, _sanitize_text(error or "", 2_000) or None, next_attempt_at, now, event_id, self.owner_id),
            )
            await db.commit()


def _outbound_from_json(payload_json: str) -> OutboundEnvelope:
    payload = json.loads(payload_json)
    data = payload["outbound"]
    return OutboundEnvelope(
        identity=ChannelIdentity(**data["identity"]),
        conversation=ChannelConversation(**data["conversation"]),
        event_type=data["event_type"],
        payload=data["payload"],
        idempotency_key=data["idempotency_key"],
        reply_to_external_id=data.get("reply_to_external_id"),
        group_id=data.get("group_id"),
        session_id=data.get("session_id"),
        source_event_id=data.get("source_event_id"),
    )


def _sanitize_text(value: str, limit: int) -> str:
    return sanitize_text_for_storage(value, limit)


def _safe_json(value: Any) -> str:
    def clean(item: Any, depth: int = 0) -> Any:
        if depth > 8:
            return "[TRUNCATED_DEPTH]"
        if isinstance(item, str):
            return _sanitize_text(item, 10_000)
        if isinstance(item, dict):
            return {_sanitize_text(str(k), 256): clean(v, depth + 1) for k, v in list(item.items())[:1_000]}
        if isinstance(item, (list, tuple)):
            return [clean(v, depth + 1) for v in list(item)[:1_000]]
        return item
    encoded = json.dumps(clean(value), ensure_ascii=False, sort_keys=True)
    if len(encoded.encode()) > 256_000:
        raise ChannelPayloadTooLargeError("Group Channel outbox payload exceeds 256000 bytes")
    return encoded
