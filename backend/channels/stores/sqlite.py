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

from channels.core import InboundEnvelope, OutboundEnvelope, canonical_channel_instance_id
from executors.redaction import redact_secrets


class DeliveryState(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    RETRYING = "retrying"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    QUARANTINED = "quarantined"


class ChannelPayloadTooLargeError(ValueError):
    """A payload cannot be safely persisted without changing its meaning."""


_DDL = (
    """CREATE TABLE IF NOT EXISTS channel_messages (
        message_key TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        channel_instance_id TEXT NOT NULL DEFAULT '',
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
        source_event_id TEXT,
        channel TEXT NOT NULL,
        channel_instance_id TEXT NOT NULL DEFAULT '',
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
        lease_owner TEXT,
        lease_expires_at INTEGER,
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
    """CREATE TABLE IF NOT EXISTS channel_delivery_controls (
        channel TEXT PRIMARY KEY,
        paused INTEGER NOT NULL DEFAULT 0,
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
            columns = {row[1] for row in await (await db.execute("PRAGMA table_info(channel_delivery_outbox)")).fetchall()}
            if "lease_owner" not in columns:
                await db.execute("ALTER TABLE channel_delivery_outbox ADD COLUMN lease_owner TEXT")
            if "lease_expires_at" not in columns:
                await db.execute("ALTER TABLE channel_delivery_outbox ADD COLUMN lease_expires_at INTEGER")
            if "channel_instance_id" not in columns:
                await db.execute("ALTER TABLE channel_delivery_outbox ADD COLUMN channel_instance_id TEXT NOT NULL DEFAULT ''")
            if "source_event_id" not in columns:
                await db.execute("ALTER TABLE channel_delivery_outbox ADD COLUMN source_event_id TEXT")
            async with db.execute(
                "SELECT idempotency_key,channel_instance_id,state FROM channel_delivery_outbox"
            ) as cursor:
                rows = await cursor.fetchall()
            now = int(time.time() * 1000)
            for key, instance_id, state in rows:
                if str(instance_id or "").strip():
                    canonical = canonical_channel_instance_id(instance_id)
                    if canonical != instance_id:
                        await db.execute(
                            "UPDATE channel_delivery_outbox SET channel_instance_id=?,updated_at=? WHERE idempotency_key=?",
                            (canonical, now, key),
                        )
                elif state in {DeliveryState.PENDING, DeliveryState.RETRYING, DeliveryState.SENDING}:
                    error = "migration quarantined delivery without channel_instance_id"
                    await db.execute(
                        """UPDATE channel_delivery_outbox
                           SET state='quarantined',last_error=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                           WHERE idempotency_key=?""",
                        (error, now, key),
                    )
                    await db.execute(
                        """INSERT INTO channel_audit_events
                           (idempotency_key,event_type,details_json,created_at) VALUES(?,?,?,?)""",
                        (key, "delivery.quarantined", _safe_json_text({"reason": error}), now),
                    )
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
        raw_payload = json.dumps(dict(envelope.payload), ensure_ascii=False, sort_keys=True)
        if len(raw_payload.encode("utf-8")) > _MAX_JSON_BYTES:
            raise ChannelPayloadTooLargeError(f"channel payload exceeds {_MAX_JSON_BYTES} bytes")
        payload = _safe_json_text(dict(envelope.payload))
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO channel_delivery_outbox
                   (idempotency_key,source_event_id,channel,channel_instance_id,external_tenant_id,external_conversation_id,
                    event_type,payload_json,reply_to_external_id,group_id,session_id,
                    state,next_attempt_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)""",
                (
                    envelope.idempotency_key,
                    envelope.source_event_id,
                    envelope.identity.channel,
                    canonical_channel_instance_id(envelope.channel_instance_id or envelope.identity.channel),
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

    async def list_open_delivery_instances(self) -> tuple[str, ...]:
        """Return canonical instances that currently require a live dispatcher."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                """SELECT DISTINCT channel_instance_id FROM channel_delivery_outbox
                   WHERE state IN ('pending','retrying','sending')
                     AND channel_instance_id<>'' ORDER BY channel_instance_id"""
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(canonical_channel_instance_id(row[0]) for row in rows)

    async def set_channel_paused(self, channel: str, paused: bool) -> None:
        raw_channel = str(channel or "").strip()
        if not raw_channel:
            raise ValueError("channel is required")
        channel = canonical_channel_instance_id(raw_channel)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO channel_delivery_controls(channel,paused,updated_at) VALUES(?,?,?)
                   ON CONFLICT(channel) DO UPDATE SET paused=excluded.paused,updated_at=excluded.updated_at""",
                (channel, int(paused), int(time.time() * 1000)),
            )
            await db.commit()

    async def get_delivery_health(self) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT channel,state,COUNT(*) FROM channel_delivery_outbox GROUP BY channel,state"
            ) as cursor:
                rows = await cursor.fetchall()
            async with db.execute("SELECT channel FROM channel_delivery_controls WHERE paused=1 ORDER BY channel") as cursor:
                paused = [row[0] for row in await cursor.fetchall()]
            async with db.execute(
                "SELECT MIN(created_at) FROM channel_delivery_outbox WHERE state IN ('pending','retrying')"
            ) as cursor:
                oldest = (await cursor.fetchone())[0]
            async with db.execute(
                """SELECT channel_instance_id,
                          MAX(CASE WHEN state='sent' THEN updated_at END),
                          SUM(CASE WHEN state='retrying' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN state='dead_letter' THEN 1 ELSE 0 END),
                          MIN(CASE WHEN state='dead_letter' THEN created_at END)
                   FROM channel_delivery_outbox GROUP BY channel_instance_id"""
            ) as cursor:
                instance_rows = await cursor.fetchall()
        counts: dict[str, dict[str, int]] = {}
        for channel, state, count in rows:
            counts.setdefault(channel, {})[state] = int(count)
        by_instance = {
            row[0] or "unknown": {
                "last_success_at": row[1],
                "retrying": int(row[2] or 0),
                "dead_letter": int(row[3] or 0),
                "oldest_dead_letter_at": row[4],
            }
            for row in instance_rows
        }
        return {
            "by_channel": counts,
            "by_instance": by_instance,
            "paused_channels": paused,
            "oldest_pending_at": oldest,
        }

    async def replay_dead_letter(self, idempotency_key: str) -> bool:
        now = int(time.time() * 1000)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """UPDATE channel_delivery_outbox
                   SET state='retrying',next_attempt_at=?,last_error=NULL,updated_at=?
                   WHERE idempotency_key=? AND state='dead_letter'""",
                (now, now, idempotency_key),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return False
            await db.execute(
                "INSERT INTO channel_audit_events(idempotency_key,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (idempotency_key, "delivery.replayed", _safe_json_text({}), now),
            )
            await db.commit()
            return True

    async def recover_expired_deliveries(self, *, now_ms: int | None = None) -> int:
        """Return crashed ``sending`` work to the retry queue."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE channel_delivery_outbox
                   SET state='retrying', lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                   WHERE state='sending' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?""",
                (now, now),
            )
            await db.commit()
            return cursor.rowcount

    async def claim_due_delivery(
        self,
        *,
        now_ms: int | None = None,
        lease_owner: str = "channel-dispatcher",
        lease_ms: int = 30_000,
        channel: str | None = None,
        channel_instance_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim one due delivery and attach a crash-recovery lease."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        if not lease_owner.strip() or lease_ms <= 0:
            raise ValueError("lease_owner is required and lease_ms must be positive")
        await self.recover_expired_deliveries(now_ms=now)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            channel_clause = ""
            params: tuple[Any, ...] = (now,)
            if channel_instance_id:
                channel_clause = " AND channel_instance_id=?"
                params += (canonical_channel_instance_id(channel_instance_id),)
            elif channel:
                channel_clause = " AND channel=?"
                params += (channel.lower(),)
            async with db.execute(
                f"""SELECT idempotency_key FROM channel_delivery_outbox
                   WHERE state IN ('pending','retrying') AND next_attempt_at<=?
                     AND NOT EXISTS (
                       SELECT 1 FROM channel_delivery_controls c
                       WHERE c.channel=COALESCE(NULLIF(channel_delivery_outbox.channel_instance_id,''), channel_delivery_outbox.channel) AND c.paused=1
                     )
                   {channel_clause} ORDER BY created_at ASC LIMIT 1""",
                params,
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                return None
            key = row[0]
            await db.execute(
                """UPDATE channel_delivery_outbox
                   SET state='sending', attempts=attempts+1, lease_owner=?, lease_expires_at=?, updated_at=?
                   WHERE idempotency_key=? AND state IN ('pending','retrying')""",
                (lease_owner, now + lease_ms, now, key),
            )
            await db.commit()
        return await self.get_delivery(key)

    async def mark_sent(self, idempotency_key: str, external_message_id: str, *, lease_owner: str | None = None) -> bool:
        return await self.transition_delivery_with_audit(
            idempotency_key,
            DeliveryState.SENT,
            event_type="delivery.sent",
            details={},
            external_message_id=external_message_id,
            lease_owner=lease_owner,
        )

    async def renew_delivery_lease(self, idempotency_key: str, lease_owner: str, *, lease_ms: int = 30_000) -> bool:
        if not lease_owner.strip() or lease_ms <= 0:
            raise ValueError("lease_owner is required and lease_ms must be positive")
        now = int(time.time() * 1000)
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE channel_delivery_outbox SET lease_expires_at=?,updated_at=?
                   WHERE idempotency_key=? AND state='sending' AND lease_owner=?""",
                (now + lease_ms, now, idempotency_key, lease_owner),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def transition_delivery_with_audit(
        self,
        idempotency_key: str,
        state: DeliveryState,
        *,
        event_type: str,
        details: dict[str, Any],
        external_message_id: str | None = None,
        error: str | None = None,
        retry_at_ms: int | None = None,
        lease_owner: str | None = None,
    ) -> bool:
        """CAS a delivery state and append its audit event in one transaction."""
        if state not in {DeliveryState.SENT, DeliveryState.RETRYING, DeliveryState.DEAD_LETTER}:
            raise ValueError("unsupported delivery transition")
        now = int(time.time() * 1000)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT state,lease_owner FROM channel_delivery_outbox WHERE idempotency_key=?",
                (idempotency_key,),
            ) as cursor:
                current = await cursor.fetchone()
            if current is None or current[0] != DeliveryState.SENDING.value:
                await db.rollback()
                return False
            if lease_owner is not None and current[1] != lease_owner:
                await db.rollback()
                return False
            if state is DeliveryState.SENT:
                if not external_message_id:
                    await db.rollback()
                    return False
                next_attempt = now
                last_error = None
            else:
                next_attempt = retry_at_ms if retry_at_ms is not None else now
                last_error = sanitize_text_for_storage(str(error or "delivery failed"), _MAX_ERROR_LENGTH)
            cursor = await db.execute(
                """UPDATE channel_delivery_outbox
                   SET state=?, external_message_id=COALESCE(?, external_message_id), last_error=?,
                       next_attempt_at=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                   WHERE idempotency_key=? AND state='sending'""",
                (state.value, external_message_id, last_error, next_attempt, now, idempotency_key),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return False
            await db.execute(
                "INSERT INTO channel_audit_events (idempotency_key,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (idempotency_key, str(event_type), _safe_json_text(details), now),
            )
            await db.commit()
            return True

    async def mark_failed(self, idempotency_key: str, error: str, *, retry_at_ms: int | None = None, lease_owner: str | None = None) -> bool:
        state = DeliveryState.RETRYING if retry_at_ms is not None else DeliveryState.DEAD_LETTER
        return await self.transition_delivery_with_audit(
            idempotency_key,
            state,
            event_type="delivery.retrying" if retry_at_ms is not None else "delivery.dead_letter",
            details={"error": str(error), "retry_at_ms": retry_at_ms},
            error=error,
            retry_at_ms=retry_at_ms,
            lease_owner=lease_owner,
        )

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


def sanitize_text_for_storage(value: str, limit: int = _MAX_STRING_LENGTH) -> str:
    """Apply the canonical redactor before any truncation.

    Oversized values are fail-closed when no complete secret pattern can be
    established.  This deliberately avoids a second, incomplete marker list
    that could disagree with ``redact_secrets`` or miss case-insensitive forms.
    """
    text = str(value)
    if len(text) > 64_000:
        return "[TRUNCATED]"
    redacted, _count = redact_secrets(text)
    return redacted[:limit]


def _sanitize_json(value: Any, *, depth: int = 0) -> Any:
    """Redact and bound JSON before any Channel-owned persistence."""
    if depth > _MAX_DEPTH:
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, str):
        return sanitize_text_for_storage(value)
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_ELEMENTS]
        return {
            sanitize_text_for_storage(str(key), _MAX_KEY_LENGTH): _sanitize_json(item, depth=depth + 1)
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
    raise ChannelPayloadTooLargeError(f"channel payload exceeds {_MAX_JSON_BYTES} bytes")
