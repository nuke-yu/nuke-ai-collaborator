"""Paginated, resumable projection rebuild service."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

from memory.ports import MemoryDatabasePort, ProjectionOutboxPort

from .projection_audit import ProjectionAuditResult
from .vector_projection import (
    bot_memory_projection_event_id,
    enqueue_bot_memory_projection,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RebuildStatusReport:
    group_id: int
    mode: str
    status: str
    cursor_record_id: str
    total_records: int
    processed_records: int
    enqueued_intents: int
    last_error: str
    progress_percentage: float
    started_at: int
    updated_at: int
    completed_at: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BotMemoryProjectionRebuildService:
    """Incremental, audit-driven, and full resumable projection rebuild service."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        outbox: ProjectionOutboxPort,
    ) -> None:
        self._database = database
        self._outbox = outbox

    async def ensure_rebuild_schema(self, group_id: int) -> None:
        async with await self._database.connect(
            "memory_records", group_id, write=True
        ) as connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS memory_projection_rebuild_state (
                    group_id INTEGER PRIMARY KEY,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    cursor_record_id TEXT NOT NULL DEFAULT '',
                    total_records INTEGER NOT NULL DEFAULT 0,
                    processed_records INTEGER NOT NULL DEFAULT 0,
                    enqueued_intents INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    started_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    completed_at INTEGER
                )"""
            )
            await connection.commit()

    async def start_rebuild(
        self,
        group_id: int,
        *,
        mode: str = "full_rebuild",
        audit_result: ProjectionAuditResult | None = None,
    ) -> RebuildStatusReport:
        if group_id <= 0:
            raise ValueError("group_id must be positive")
        if mode not in {"incremental", "repair", "full_rebuild"}:
            raise ValueError(f"Invalid rebuild mode: {mode}")

        await self.ensure_rebuild_schema(group_id)
        now = int(time.time() * 1000)

        async with await self._database.connect(
            "memory_records", group_id, write=True
        ) as connection:
            # Count total canonical records for this group
            async with connection.execute(
                """SELECT COUNT(*) FROM memory_records
                WHERE group_id=? AND kind IN ('fact','reflection')
                  AND owner_type='bot' AND status='provisional'""",
                (group_id,),
            ) as cursor:
                total_records = int((await cursor.fetchone())[0])

            await connection.execute(
                """INSERT INTO memory_projection_rebuild_state
                (group_id, mode, status, cursor_record_id, total_records,
                 processed_records, enqueued_intents, last_error, started_at, updated_at, completed_at)
                VALUES (?, ?, 'running', '', ?, 0, 0, '', ?, ?, NULL)
                ON CONFLICT(group_id) DO UPDATE SET
                  mode=excluded.mode,
                  status='running',
                  cursor_record_id='',
                  total_records=excluded.total_records,
                  processed_records=0,
                  enqueued_intents=0,
                  last_error='',
                  started_at=excluded.started_at,
                  updated_at=excluded.updated_at,
                  completed_at=NULL""",
                (group_id, mode, total_records, now, now),
            )
            await connection.commit()

        return await self.get_status(group_id)

    async def step_rebuild(
        self,
        group_id: int,
        *,
        batch_size: int = 100,
        time_budget_ms: int = 1000,
    ) -> RebuildStatusReport:
        await self.ensure_rebuild_schema(group_id)
        status_report = await self.get_status(group_id)
        if status_report.status not in {"running", "pending"}:
            return status_report

        start_time = time.time()
        now_ms = int(start_time * 1000)
        cutoff_time = start_time + (time_budget_ms / 1000.0)

        cursor_id = status_report.cursor_record_id
        processed = status_report.processed_records
        enqueued = status_report.enqueued_intents
        mode = status_report.mode
        total = status_report.total_records

        async with await self._database.connect(
            "memory_records", group_id, write=True
        ) as connection:
            while time.time() < cutoff_time:
                async with connection.execute(
                    """SELECT record_id, kind, bot_id, content, importance,
                        source_ids, metadata_json, evidence_json,
                        COALESCE(effective_from, created_at)
                    FROM memory_records
                    WHERE group_id=? AND kind IN ('fact','reflection')
                      AND owner_type='bot' AND status='provisional'
                      AND record_id > ?
                    ORDER BY record_id ASC LIMIT ?""",
                    (group_id, cursor_id, batch_size),
                ) as cursor:
                    rows = await cursor.fetchall()

                if not rows:
                    # Rebuild completed!
                    await connection.execute(
                        """UPDATE memory_projection_rebuild_state
                        SET status='completed', updated_at=?, completed_at=?
                        WHERE group_id=?""",
                        (now_ms, now_ms, group_id),
                    )
                    await connection.commit()
                    break

                for row in rows:
                    rec_id = str(row[0])
                    kind = str(row[1])
                    bot_id = int(row[2])
                    content = str(row[3])
                    importance = float(row[4])

                    if kind == "fact":
                        proj_id = f"fact_{bot_id}_{group_id}_{rec_id}"
                        meta = {
                            "bot_id": bot_id,
                            "group_id": group_id,
                            "importance": importance,
                            "mem_type": "fact",
                        }
                    else:
                        proj_id = rec_id
                        meta = {
                            "bot_id": bot_id,
                            "group_id": group_id,
                            "importance": importance,
                            "mem_type": "reflection",
                        }

                    # Enqueue projection intent
                    await enqueue_bot_memory_projection(
                        self._outbox,
                        connection,
                        record_id=rec_id,
                        group_id=group_id,
                        projection_id=proj_id,
                        content=content,
                        metadata=meta,
                        now_ms=now_ms,
                    )
                    enqueued += 1
                    processed += 1
                    cursor_id = rec_id

                is_completed = (processed >= total)
                new_status = "completed" if is_completed else "running"
                completed_timestamp = now_ms if is_completed else None

                await connection.execute(
                    """UPDATE memory_projection_rebuild_state
                    SET cursor_record_id=?, processed_records=?, enqueued_intents=?,
                        status=?, updated_at=?, completed_at=?
                    WHERE group_id=?""",
                    (cursor_id, processed, enqueued, new_status, now_ms, completed_timestamp, group_id),
                )
                await connection.commit()
                break

        return await self.get_status(group_id)

    async def pause_rebuild(self, group_id: int) -> RebuildStatusReport:
        now_ms = int(time.time() * 1000)
        async with await self._database.connect(
            "memory_records", group_id, write=True
        ) as connection:
            await connection.execute(
                """UPDATE memory_projection_rebuild_state
                SET status='paused', updated_at=? WHERE group_id=? AND status='running'""",
                (now_ms, group_id),
            )
            await connection.commit()
        return await self.get_status(group_id)

    async def resume_rebuild(self, group_id: int) -> RebuildStatusReport:
        now_ms = int(time.time() * 1000)
        async with await self._database.connect(
            "memory_records", group_id, write=True
        ) as connection:
            await connection.execute(
                """UPDATE memory_projection_rebuild_state
                SET status='running', updated_at=? WHERE group_id=? AND status='paused'""",
                (now_ms, group_id),
            )
            await connection.commit()
        return await self.get_status(group_id)

    async def get_status(self, group_id: int) -> RebuildStatusReport:
        async with await self._database.connect(
            "memory_records", group_id, write=False
        ) as connection:
            async with connection.execute(
                """SELECT group_id, mode, status, cursor_record_id, total_records,
                    processed_records, enqueued_intents, last_error, started_at,
                    updated_at, completed_at
                FROM memory_projection_rebuild_state WHERE group_id=?""",
                (group_id,),
            ) as cursor:
                row = await cursor.fetchone()

        if not row:
            now_ms = int(time.time() * 1000)
            return RebuildStatusReport(
                group_id=group_id,
                mode="none",
                status="idle",
                cursor_record_id="",
                total_records=0,
                processed_records=0,
                enqueued_intents=0,
                last_error="",
                progress_percentage=100.0,
                started_at=now_ms,
                updated_at=now_ms,
                completed_at=now_ms,
            )

        total = int(row[4])
        processed = int(row[5])
        pct = (processed / total * 100.0) if total > 0 else 100.0

        return RebuildStatusReport(
            group_id=int(row[0]),
            mode=str(row[1]),
            status=str(row[2]),
            cursor_record_id=str(row[3]),
            total_records=total,
            processed_records=processed,
            enqueued_intents=int(row[6]),
            last_error=str(row[7] or ""),
            progress_percentage=min(100.0, pct),
            started_at=int(row[8]),
            updated_at=int(row[9]),
            completed_at=int(row[10]) if row[10] is not None else None,
        )
