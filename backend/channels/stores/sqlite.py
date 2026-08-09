"""SQLite store owned by the standalone Channel module.

The store deliberately knows only Channel contracts.  Group identifiers may be
recorded as opaque binding metadata after a Bridge is established, but this
module never opens or imports Group persistence.
"""
from __future__ import annotations

import json
import hashlib
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

import aiosqlite

from channels.core import InboundEnvelope, OutboundEnvelope
from executors.redaction import redact_secrets


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
    """CREATE TABLE IF NOT EXISTS channel_audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        idempotency_key TEXT NOT NULL,
        event_type TEXT NOT NULL,
        details_json TEXT NOT NULL,
        created_at INTEGER NOT NULL
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
        payload = _safe_json_text(envelope.to_dict())
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
        payload = _safe_json_text(dict(envelope.payload))
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
                (state, _sanitize_text(str(error), _MAX_ERROR_LENGTH), next_attempt, now, idempotency_key),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def record_audit(self, idempotency_key: str, event_type: str, details: dict[str, Any]) -> None:
        now = int(time.time() * 1000)
        safe_details = _safe_json_text(details)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO channel_audit_events (idempotency_key,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (idempotency_key, str(event_type), safe_details, now),
            )
            await db.commit()

    async def list_audit(self, idempotency_key: str) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT event_type,details_json,created_at FROM channel_audit_events WHERE idempotency_key=? ORDER BY id",
                (idempotency_key,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {"event_type": row["event_type"], "details": json.loads(row["details_json"]), "created_at": row["created_at"]}
            for row in rows
        ]


_MAX_STRING_LENGTH = 10_000
_MAX_KEY_LENGTH = 256
_MAX_ERROR_LENGTH = 2_000
_MAX_DEPTH = 8
_MAX_ELEMENTS = 1_000
_MAX_JSON_BYTES = 256_000


def _sanitize_text(value: str, limit: int = _MAX_STRING_LENGTH) -> str:
    """Redact the complete value before applying a storage length limit."""
    safe = redact_secrets(value)[0]
    return safe[:limit]


def _sanitize_json(value: Any, *, depth: int = 0) -> Any:
    """Redact and bound JSON before any Channel-owned persistence."""
    if depth > _MAX_DEPTH:
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_ELEMENTS]
        return {
            _sanitize_text(str(key), _MAX_KEY_LENGTH): _sanitize_json(item, depth=depth + 1)
            for key, item in items
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_json(item, depth=depth + 1) for item in list(value)[:_MAX_ELEMENTS]]
    return value


def _safe_json_text(value: Any) -> str:
    safe = _sanitize_json(value)
    encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) <= _MAX_JSON_BYTES:
        return encoded
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return json.dumps({"_truncated": True, "sha256": digest}, ensure_ascii=False, sort_keys=True)
