"""Storage- and projection-neutral transactional outbox engine."""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Mapping

from memory.ports import MemoryDatabasePort, ProjectionDeliveryPort, ProjectionDrainResult

log = logging.getLogger(__name__)

_LEASE_MS = 30_000
_MAX_BACKOFF_MS = 60_000


DrainResult = ProjectionDrainResult


class ProjectionOutbox:
    """Persist and deliver projection intents without owning canonical writes."""

    def __init__(
        self,
        database: MemoryDatabasePort,
        delivery: ProjectionDeliveryPort,
        *,
        error_sanitizer: Callable[[str], str] | None = None,
    ) -> None:
        self._database = database
        self._delivery = delivery
        self._error_sanitizer = error_sanitizer

    async def enqueue(
        self,
        connection: Any,
        *,
        event_id: str,
        projection_type: str,
        aggregate_id: str,
        aggregate_version: str,
        group_id: int,
        payload: Mapping[str, Any],
        now_ms: int | None = None,
    ) -> None:
        """Enqueue on the caller's transaction to preserve atomicity."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        await connection.execute(
            """INSERT INTO memory_projection_outbox
            (event_id,projection_type,aggregate_id,aggregate_version,group_id,payload_json,
             status,attempt_count,next_attempt_at,lease_token,lease_until,last_error,
             created_at,updated_at,completed_at)
            VALUES (?,?,?,?,?,?,'pending',0,0,NULL,NULL,'',?,?,NULL)
            ON CONFLICT(event_id) DO UPDATE SET
              projection_type=excluded.projection_type,
              aggregate_id=excluded.aggregate_id,
              aggregate_version=excluded.aggregate_version,
              group_id=excluded.group_id,
              payload_json=excluded.payload_json,
              status='pending',attempt_count=0,next_attempt_at=0,
              lease_token=NULL,lease_until=NULL,last_error='',
              updated_at=excluded.updated_at,completed_at=NULL""",
            (
                event_id,
                projection_type,
                aggregate_id,
                aggregate_version,
                group_id,
                json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )

    async def drain(
        self, group_id: int, *, limit: int = 50, event_id: str | None = None
    ) -> DrainResult:
        """Lease and deliver pending projections; safe across crashes and retries."""
        now = int(time.time() * 1000)
        lease_token = uuid.uuid4().hex
        params: list[Any] = [group_id, now, now]
        event_filter = ""
        if event_id is not None:
            event_filter = " AND event_id=?"
            params.append(event_id)
        params.append(max(1, limit))

        async with await self._database.connect(
            "memory_projection_outbox", group_id, write=True
        ) as connection:
            async with connection.execute(
                "SELECT event_id,projection_type,payload_json,aggregate_version "
                "FROM memory_projection_outbox WHERE group_id=? "
                "AND (status='pending' OR (status='processing' AND lease_until<?)) "
                "AND next_attempt_at<=?" + event_filter
                + " ORDER BY updated_at,event_id LIMIT ?",
                tuple(params),
            ) as cursor:
                rows = await cursor.fetchall()
            claimed = []
            for row in rows:
                cursor = await connection.execute(
                    """UPDATE memory_projection_outbox
                    SET status='processing',lease_token=?,lease_until=?,updated_at=?
                    WHERE event_id=? AND aggregate_version=?
                    AND (status='pending' OR (status='processing' AND lease_until<?))""",
                    (lease_token, now + _LEASE_MS, now, row[0], row[3], now),
                )
                if cursor.rowcount == 1:
                    claimed.append(row)
            await connection.commit()

        completed = failed = 0
        for claimed_event_id, projection_type, payload_json, aggregate_version in claimed:
            try:
                await self._delivery.deliver(
                    projection_type, json.loads(payload_json)
                )
            except Exception as exc:
                failed += 1
                await self._mark_failed(
                    group_id,
                    claimed_event_id,
                    aggregate_version,
                    lease_token,
                    exc,
                )
                log.warning(
                    "memory projection delivery failed for %s (%s)",
                    claimed_event_id,
                    type(exc).__name__,
                )
            else:
                completed += await self._mark_completed(
                    group_id, claimed_event_id, aggregate_version, lease_token
                )
        return DrainResult(len(claimed), completed, failed)

    async def _mark_completed(
        self,
        group_id: int,
        event_id: str,
        aggregate_version: str,
        lease_token: str,
    ) -> int:
        now = int(time.time() * 1000)
        async with await self._database.connect(
            "memory_projection_outbox", group_id, write=True
        ) as connection:
            cursor = await connection.execute(
                """UPDATE memory_projection_outbox SET status='completed',lease_token=NULL,
                lease_until=NULL,last_error='',completed_at=?,updated_at=?
                WHERE event_id=? AND aggregate_version=? AND status='processing'
                AND lease_token=?""",
                (now, now, event_id, aggregate_version, lease_token),
            )
            await connection.commit()
            return max(0, cursor.rowcount)

    async def _mark_failed(
        self,
        group_id: int,
        event_id: str,
        aggregate_version: str,
        lease_token: str,
        exc: Exception,
    ) -> None:
        now = int(time.time() * 1000)
        safe_error = re.sub(r"[\r\n\t<>]", " ", f"{type(exc).__name__}: {exc}")[:500]
        if self._error_sanitizer is None:
            safe_error = type(exc).__name__
        else:
            try:
                safe_error = self._error_sanitizer(safe_error)[:500]
            except Exception:
                safe_error = type(exc).__name__
        async with await self._database.connect(
            "memory_projection_outbox", group_id, write=True
        ) as connection:
            await connection.execute(
                """UPDATE memory_projection_outbox SET status='pending',
                attempt_count=attempt_count+1,
                next_attempt_at=? + MIN(?, 1000 * (1 << MIN(attempt_count, 6))),
                lease_token=NULL,lease_until=NULL,last_error=?,updated_at=?
                WHERE event_id=? AND aggregate_version=? AND status='processing'
                AND lease_token=?""",
                (
                    now,
                    _MAX_BACKOFF_MS,
                    safe_error,
                    now,
                    event_id,
                    aggregate_version,
                    lease_token,
                ),
            )
            await connection.commit()
