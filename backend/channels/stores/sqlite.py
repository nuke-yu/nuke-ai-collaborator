"""SQLite store owned by the standalone Channel module.

The store deliberately knows only Channel contracts.  Group identifiers may be
recorded as opaque binding metadata after a Bridge is established, but this
module never opens or imports Group persistence.
"""
from __future__ import annotations

import json
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

import aiosqlite

from channels.core import InboundEnvelope, OutboundEnvelope


class DeliveryState(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    RETRYING = "retrying"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


_DDL = (
    """CREATE TABLE IF NOT EXISTS channel_messages (
        message_key TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        external_tenant_id TEXT NOT NULL,
        external_conversation_id TEXT NOT NULL,
        external_user_id TEXT,
        external_message_id TEXT NOT NULL,
        direction TEXT NOT NULL,
        event_type TEXT NOT NULL DEFAULT 'message.received',
        payload_json TEXT NOT NULL,
        binding_id TEXT,
        group_id INTEGER,
        created_at INTEGER NOT NULL,
        UNIQUE(channel, external_tenant_id, external_conversation_id, external_message_id, direction)
    )""",
    """CREATE TABLE IF NOT EXISTS channel_delivery_outbox (
        idempotency_key TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        external_tenant_id TEXT NOT NULL,
        external_conversation_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        reply_to_external_id TEXT,
        group_id INTEGER,
        session_id TEXT,
        state TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at INTEGER NOT NULL,
        external_message_id TEXT,
        last_error TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_channel_delivery_due ON channel_delivery_outbox(state, next_attempt_at)",
)


class ChannelStore:
    """Serialized access to Channel-owned SQLite state."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if not self.path.strip():
            raise ValueError("channel store path is required")

    async def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            for statement in _DDL:
                await db.execute(statement)
            await db.commit()

    async def record_inbound(self, envelope: InboundEnvelope) -> bool:
        """Persist an inbound message once; return False for a duplicate event."""
        now = int(time.time() * 1000)
        payload = json.dumps(envelope.to_dict(), ensure_ascii=False, sort_keys=True)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO channel_messages
                   (message_key,channel,external_tenant_id,external_conversation_id,
                    external_user_id,external_message_id,direction,event_type,payload_json,
                    binding_id,group_id,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    envelope.idempotency_key,
                    envelope.channel,
                    envelope.external_tenant_id,
                    envelope.external_group_id,
                    envelope.external_user_id,
                    envelope.external_message_id,
                    "inbound",
                    "message.received",
                    payload,
                    envelope.binding_id,
                    envelope.group_id,
                    now,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def enqueue_outbound(self, envelope: OutboundEnvelope) -> bool:
        """Persist one outbound delivery intent; duplicate keys are idempotent."""
        now = int(time.time() * 1000)
        payload = json.dumps(dict(envelope.payload), ensure_ascii=False, sort_keys=True)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO channel_delivery_outbox
                   (idempotency_key,channel,external_tenant_id,external_conversation_id,
                    event_type,payload_json,reply_to_external_id,group_id,session_id,
                    state,next_attempt_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?)""",
                (
                    envelope.idempotency_key,
                    envelope.identity.channel,
                    envelope.identity.external_tenant_id,
                    envelope.conversation.external_conversation_id,
                    envelope.event_type,
                    payload,
                    envelope.reply_to_external_id,
                    envelope.group_id,
                    envelope.session_id,
                    now,
                    now,
                    now,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def get_delivery(self, idempotency_key: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM channel_delivery_outbox WHERE idempotency_key=?",
                (idempotency_key,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    async def claim_due_delivery(self, *, now_ms: int | None = None) -> dict[str, Any] | None:
        """Atomically claim one due delivery for a future Connector worker."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                """SELECT idempotency_key FROM channel_delivery_outbox
                   WHERE state IN ('pending','retrying') AND next_attempt_at<=?
                   ORDER BY created_at ASC LIMIT 1""",
                (now,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                return None
            key = row[0]
            await db.execute(
                """UPDATE channel_delivery_outbox
                   SET state='sending', attempts=attempts+1, updated_at=?
                   WHERE idempotency_key=? AND state IN ('pending','retrying')""",
                (now, key),
            )
            await db.commit()
        return await self.get_delivery(key)

    async def mark_sent(self, idempotency_key: str, external_message_id: str) -> bool:
        now = int(time.time() * 1000)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE channel_delivery_outbox
                   SET state='sent', external_message_id=?, last_error=NULL, updated_at=?
                   WHERE idempotency_key=? AND state='sending'""",
                (external_message_id, now, idempotency_key),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def mark_failed(self, idempotency_key: str, error: str, *, retry_at_ms: int | None = None) -> bool:
        now = int(time.time() * 1000)
        state = DeliveryState.RETRYING.value if retry_at_ms is not None else DeliveryState.DEAD_LETTER.value
        next_attempt = retry_at_ms if retry_at_ms is not None else now
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE channel_delivery_outbox
                   SET state=?, last_error=?, next_attempt_at=?, updated_at=?
                   WHERE idempotency_key=? AND state='sending'""",
                (state, str(error)[:2000], next_attempt, now, idempotency_key),
            )
            await db.commit()
            return cursor.rowcount == 1
