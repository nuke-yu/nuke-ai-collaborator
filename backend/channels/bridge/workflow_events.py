"""Runtime projection from committed Group workflow observations to Channel outbox."""
from __future__ import annotations

import json
import time
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from channels.bridge.binding import ChannelBindingStore
from channels.stores import sanitize_text_for_storage

from .group_outbox import GroupChannelOutboxWriter
from .outbound import OutboundEventProjector, OutboundPolicyError


_EVENT_TYPES = {
    "workflow_completed": "workflow.completed",
    "workflow_failed": "workflow.failed",
    "permission_requested": "permission_requested",
    "artifact_produced": "artifact_produced",
    "session_recovered": "session_recovered",
    "task_stuck": "task_stuck",
}


class WorkflowProjectionResult(StrEnum):
    IDLE = "idle"
    PROJECTED = "projected"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTERED = "dead_lettered"
    LEASE_LOST = "lease_lost"


_PROJECTION_DDL = """CREATE TABLE IF NOT EXISTS group_channel_projection_queue (
    source_event_id TEXT PRIMARY KEY,
    group_id INTEGER NOT NULL,
    observation_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL,
    lease_owner TEXT,
    lease_expires_at INTEGER,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)"""


async def initialize_workflow_channel_projections(
    conn: Any,
    group_id: int,
    *,
    backfill: bool = True,
) -> None:
    """Initialize projection state and backfill durable observations idempotently."""
    await conn.execute(_PROJECTION_DDL)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_channel_projection_due "
        "ON group_channel_projection_queue(state,next_attempt_at)"
    )
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_observations'"
    ) as cursor:
        if await cursor.fetchone() is None or not backfill:
            return
    now = int(time.time() * 1000)
    event_types = tuple(_EVENT_TYPES)
    placeholders = ",".join("?" for _ in event_types)
    await conn.execute(
        f"""INSERT OR IGNORE INTO group_channel_projection_queue
            (source_event_id,group_id,observation_json,next_attempt_at,created_at,updated_at)
            SELECT observation_id,group_id,envelope_json,?,?,?
            FROM workflow_observations
            WHERE group_id=? AND event_type IN ({placeholders})""",
        (now, now, now, group_id, *event_types),
    )


async def enqueue_workflow_channel_projections(
    conn: Any,
    group_id: int,
    observations: Iterable[dict[str, Any]],
) -> int:
    """Persist projection intents in the same Group transaction as observations."""
    await initialize_workflow_channel_projections(conn, group_id, backfill=False)
    now = int(time.time() * 1000)
    written = 0
    for observation in observations:
        if str(observation.get("event_type") or "") not in _EVENT_TYPES:
            continue
        source_event_id = str(observation.get("event_id") or "").strip()
        if not source_event_id:
            raise ValueError("workflow Channel projection requires event_id")
        cursor = await conn.execute(
            """INSERT OR IGNORE INTO group_channel_projection_queue
               (source_event_id,group_id,observation_json,next_attempt_at,created_at,updated_at)
               VALUES(?,?,?,?,?,?)""",
            (source_event_id, group_id, json.dumps(observation, ensure_ascii=False), now, now, now),
        )
        written += int(cursor.rowcount == 1)
    return written


class WorkflowChannelProjectionRelay:
    """Compensate durable workflow observations into the Group delivery outbox."""

    def __init__(
        self,
        group_db_path: str | Path,
        binding_store: ChannelBindingStore,
        *,
        max_attempts: int = 10,
        retry_delay_ms: int = 1_000,
        lease_ms: int = 30_000,
        owner_id: str | None = None,
    ) -> None:
        if max_attempts <= 0 or retry_delay_ms < 0 or lease_ms <= 0:
            raise ValueError("projection retry and lease settings are invalid")
        self.group_db_path = str(group_db_path)
        self.binding_store = binding_store
        self.max_attempts = max_attempts
        self.retry_delay_ms = retry_delay_ms
        self.lease_ms = lease_ms
        self.owner_id = owner_id or f"workflow-channel-projector:{uuid.uuid4()}"

    async def run_once(self, group_id: int, *, now_ms: int | None = None) -> WorkflowProjectionResult:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        async with aiosqlite.connect(self.group_db_path) as conn:
            await initialize_workflow_channel_projections(conn, group_id)
            await conn.commit()
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                """UPDATE group_channel_projection_queue
                   SET state='retrying',lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                   WHERE state='projecting' AND lease_expires_at<=?""",
                (now, now),
            )
            async with conn.execute(
                """SELECT source_event_id,observation_json,attempts
                   FROM group_channel_projection_queue
                   WHERE group_id=? AND state IN ('pending','retrying') AND next_attempt_at<=?
                   ORDER BY created_at LIMIT 1""",
                (group_id, now),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await conn.commit()
                return WorkflowProjectionResult.IDLE
            source_event_id, observation_json, attempts = row
            cursor = await conn.execute(
                """UPDATE group_channel_projection_queue
                   SET state='projecting',attempts=attempts+1,lease_owner=?,lease_expires_at=?,updated_at=?
                   WHERE source_event_id=? AND state IN ('pending','retrying')""",
                (self.owner_id, now + self.lease_ms, now, source_event_id),
            )
            if cursor.rowcount != 1:
                await conn.rollback()
                return WorkflowProjectionResult.LEASE_LOST
            await conn.commit()

        try:
            observation = json.loads(observation_json)
            if not Path(self.binding_store.path).exists():
                raise OSError("Channel binding registry is unavailable")
            bindings = await self.binding_store.list_active_for_group(group_id)
            async with aiosqlite.connect(self.group_db_path) as conn:
                await conn.execute("BEGIN IMMEDIATE")
                await _append_workflow_channel_events(conn, group_id, [observation], bindings)
                cursor = await conn.execute(
                    """UPDATE group_channel_projection_queue
                       SET state='projected',last_error=NULL,lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                       WHERE source_event_id=? AND state='projecting' AND lease_owner=?""",
                    (int(time.time() * 1000), source_event_id, self.owner_id),
                )
                if cursor.rowcount != 1:
                    await conn.rollback()
                    return WorkflowProjectionResult.LEASE_LOST
                await conn.commit()
            return WorkflowProjectionResult.PROJECTED
        except Exception as exc:
            attempt = int(attempts) + 1
            state = "dead_letter" if attempt >= self.max_attempts else "retrying"
            result = WorkflowProjectionResult.DEAD_LETTERED if state == "dead_letter" else WorkflowProjectionResult.RETRY_SCHEDULED
            async with aiosqlite.connect(self.group_db_path) as conn:
                cursor = await conn.execute(
                    """UPDATE group_channel_projection_queue
                       SET state=?,next_attempt_at=?,last_error=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                       WHERE source_event_id=? AND state='projecting' AND lease_owner=?""",
                    (
                        state,
                        now + self.retry_delay_ms,
                        sanitize_text_for_storage(str(exc), 2_000),
                        int(time.time() * 1000),
                        source_event_id,
                        self.owner_id,
                    ),
                )
                await conn.commit()
            return result if cursor.rowcount == 1 else WorkflowProjectionResult.LEASE_LOST


async def append_workflow_channel_events(
    conn: Any,
    group_id: int,
    observations: Iterable[dict[str, Any]],
    binding_store: ChannelBindingStore,
) -> int:
    """Append notification intents to the caller's active Group transaction."""
    bindings = await binding_store.list_active_for_group(group_id)
    return await _append_workflow_channel_events(conn, group_id, observations, bindings)


async def _append_workflow_channel_events(
    conn: Any,
    group_id: int,
    observations: Iterable[dict[str, Any]],
    bindings: Iterable[Any],
) -> int:
    if not bindings:
        return 0
    written = 0
    for observation in observations:
        event_type = _EVENT_TYPES.get(str(observation.get("event_type") or ""))
        if event_type is None:
            continue
        context = observation.get("context") or {}
        payload = {
            "observation": observation.get("payload") or {},
            "workflow_id": context.get("workflow_id"),
            "stage_id": context.get("stage_id"),
            "session_id": context.get("session_id"),
        }
        for binding in bindings:
            try:
                envelope = OutboundEventProjector(binding).project(
                    event_type,
                    payload,
                    event_id=str(observation.get("event_id") or "").strip(),
                    session_id=str(context.get("session_id") or "") or None,
                    trace_id=str(observation.get("trace_id") or ""),
                )
            except OutboundPolicyError:
                continue
            if await GroupChannelOutboxWriter.append(conn, envelope):
                written += 1
    return written
