"""Policy-driven pruning with payload-free archival receipts."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Mapping

import db as _db

from .event_policy import OBSERVABILITY_KEY, RetentionPolicy, classify_event


_DAY_SECONDS = 24 * 60 * 60
_DIAGNOSTIC_SECONDS = 14 * _DAY_SECONDS
_EXECUTION_SECONDS = 90 * _DAY_SECONDS
_TERMINAL_SESSION_STATUSES = (
    "completed", "failed", "cancelled", "abandoned", "superseded", "expired",
)
_MODEL_REQUEST_EVENTS = (
    "model_request_started", "model_request_completed", "model_request_failed",
)
_SQL_BATCH_SIZE = 400


@dataclass
class RetentionResult:
    group_id: int
    run_id: str
    dry_run: bool
    session_events_archived: int = 0
    workflow_observations_archived: int = 0
    model_requests_deleted: int = 0
    artifacts_deleted: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_object(raw: object) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}") if not isinstance(raw, Mapping) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _retention(event_type: str, body: Mapping[str, Any], *, workflow: bool) -> RetentionPolicy:
    if workflow:
        stored = body.get("policy")
        payload = _json_object(body.get("payload"))
    else:
        stored = body.get(OBSERVABILITY_KEY)
        payload = body
    if isinstance(stored, Mapping):
        try:
            return RetentionPolicy(str(stored.get("retention") or ""))
        except ValueError:
            pass
    return classify_event(event_type, payload).retention


def _is_expired(policy: RetentionPolicy, occurred_at_ms: int, now_ms: int) -> bool:
    age_ms = max(0, now_ms - occurred_at_ms)
    if policy == RetentionPolicy.STREAM_LIFETIME:
        return True
    if policy == RetentionPolicy.DIAGNOSTIC_14_DAYS:
        return age_ms >= _DIAGNOSTIC_SECONDS * 1000
    if policy == RetentionPolicy.EXECUTION_90_DAYS:
        return age_ms >= _EXECUTION_SECONDS * 1000
    return False


def _event_id(body: Mapping[str, Any], row_id: int, *, workflow: bool) -> str:
    if workflow:
        value = body.get("event_id")
        if value:
            return str(value)
    metadata = body.get(OBSERVABILITY_KEY)
    if isinstance(metadata, Mapping) and metadata.get("event_id"):
        return str(metadata["event_id"])
    return f"legacy_session_event_{row_id}"


def _receipt(
    *, source: str, row_id: int, event_id: str, event_type: str,
    retention: RetentionPolicy, occurred_at_ms: int, raw_body: str,
) -> tuple[Any, ...]:
    return (
        source,
        row_id,
        event_id,
        event_type,
        retention.value,
        occurred_at_ms,
        hashlib.sha256(raw_body.encode("utf-8")).hexdigest(),
    )


def _chunks(values: list[Any]):
    for index in range(0, len(values), _SQL_BATCH_SIZE):
        yield values[index:index + _SQL_BATCH_SIZE]


async def _session_candidates(conn, now_ms: int) -> tuple[list[dict], list[str]]:
    cutoff_seconds = now_ms // 1000 - _DIAGNOSTIC_SECONDS
    placeholders = ",".join("?" for _ in _TERMINAL_SESSION_STATUSES)
    model_placeholders = ",".join("?" for _ in _MODEL_REQUEST_EVENTS)
    async with conn.execute(
        f"""SELECT se.id,se.event_type,se.payload,
                   CAST(strftime('%s',se.created_at) AS INTEGER)*1000
              FROM session_events se JOIN agent_sessions s ON s.id=se.session_id
             WHERE s.status IN ({placeholders})
               AND se.event_type NOT IN ({model_placeholders})
               AND (CAST(strftime('%s',se.created_at) AS INTEGER)<=?
                    OR CASE WHEN json_valid(se.payload)
                            THEN json_extract(se.payload,'$._observability.retention')
                            ELSE '' END=?)""",
        (
            *_TERMINAL_SESSION_STATUSES, *_MODEL_REQUEST_EVENTS,
            cutoff_seconds, RetentionPolicy.STREAM_LIFETIME.value,
        ),
    ) as cur:
        rows = await cur.fetchall()
    candidates: list[dict] = []
    event_ids: list[str] = []
    for row_id, event_type, raw_body, occurred_at_ms in rows:
        body = _json_object(raw_body)
        policy = _retention(event_type, body, workflow=False)
        occurred = int(occurred_at_ms) if occurred_at_ms is not None else now_ms
        if not _is_expired(policy, occurred, now_ms):
            continue
        eid = _event_id(body, int(row_id), workflow=False)
        candidates.append({
            "row_id": int(row_id),
            "receipt": _receipt(
                source="session", row_id=int(row_id), event_id=eid,
                event_type=event_type, retention=policy,
                occurred_at_ms=occurred, raw_body=str(raw_body),
            ),
        })
        event_ids.append(eid)
    return candidates, event_ids


async def _expired_model_requests(
    conn, now_ms: int
) -> tuple[list[dict], list[str], list[str]]:
    cutoff_seconds = now_ms // 1000 - _EXECUTION_SECONDS
    placeholders = ",".join("?" for _ in _TERMINAL_SESSION_STATUSES)
    async with conn.execute(
        f"""SELECT l.request_id,l.start_event_id,l.final_event_id
              FROM model_usage_ledger l JOIN agent_sessions s ON s.id=l.session_id
             WHERE s.status IN ({placeholders})
               AND CAST(strftime('%s',l.started_at) AS INTEGER)<=?
               AND (l.status='started' OR CAST(strftime('%s',l.completed_at) AS INTEGER)<=?)""",
        (*_TERMINAL_SESSION_STATUSES, cutoff_seconds, cutoff_seconds),
    ) as cur:
        ledger_rows = await cur.fetchall()
    event_row_ids = sorted({
        int(event_id)
        for _request_id, start_id, final_id in ledger_rows
        for event_id in (start_id, final_id)
        if event_id is not None
    })
    if not event_row_ids:
        return [], [], [str(row[0]) for row in ledger_rows]
    rows = []
    for batch in _chunks(event_row_ids):
        placeholders = ",".join("?" for _ in batch)
        async with conn.execute(
            f"""SELECT id,event_type,payload,
                       CAST(strftime('%s',created_at) AS INTEGER)*1000
                  FROM session_events WHERE id IN ({placeholders})""",
            batch,
        ) as cur:
            rows.extend(await cur.fetchall())
    candidates: list[dict] = []
    event_ids: list[str] = []
    for row_id, event_type, raw_body, occurred_at_ms in rows:
        body = _json_object(raw_body)
        eid = _event_id(body, int(row_id), workflow=False)
        occurred = int(occurred_at_ms) if occurred_at_ms is not None else now_ms
        candidates.append({
            "row_id": int(row_id),
            "receipt": _receipt(
                source="session", row_id=int(row_id), event_id=eid,
                event_type=event_type,
                retention=RetentionPolicy.EXECUTION_90_DAYS,
                occurred_at_ms=occurred, raw_body=str(raw_body),
            ),
        })
        event_ids.append(eid)
    return candidates, event_ids, [str(row[0]) for row in ledger_rows]


async def _workflow_candidates(conn, group_id: int, now_ms: int) -> tuple[list[dict], list[str]]:
    cutoff_ms = now_ms - _DIAGNOSTIC_SECONDS * 1000
    async with conn.execute(
        """SELECT id,observation_id,event_type,envelope_json,occurred_at
             FROM workflow_observations
            WHERE group_id=? AND (
                  occurred_at<=?
                  OR CASE WHEN json_valid(envelope_json)
                          THEN json_extract(envelope_json,'$.policy.retention')
                          ELSE '' END=?)""",
        (group_id, cutoff_ms, RetentionPolicy.STREAM_LIFETIME.value),
    ) as cur:
        rows = await cur.fetchall()
    candidates: list[dict] = []
    event_ids: list[str] = []
    for row_id, observation_id, event_type, raw_body, occurred_at_ms in rows:
        body = _json_object(raw_body)
        policy = _retention(event_type, body, workflow=True)
        if not _is_expired(policy, int(occurred_at_ms), now_ms):
            continue
        eid = str(observation_id or _event_id(body, int(row_id), workflow=True))
        candidates.append({
            "row_id": int(row_id),
            "receipt": _receipt(
                source="workflow", row_id=int(row_id), event_id=eid,
                event_type=event_type, retention=policy,
                occurred_at_ms=int(occurred_at_ms), raw_body=str(raw_body),
            ),
        })
        event_ids.append(eid)
    return candidates, event_ids


async def enforce_group_retention(
    group_id: int, *, now: float | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """Archive payload-free receipts and prune expired observations atomically."""
    now_ms = int((time.time() if now is None else now) * 1000)
    result = RetentionResult(
        group_id=group_id,
        run_id=f"retention_{uuid.uuid4().hex}",
        dry_run=dry_run,
    )
    async with _db.write_connect() as conn:
        session_rows, session_event_ids = await _session_candidates(conn, now_ms)
        model_rows, model_event_ids, request_ids = await _expired_model_requests(conn, now_ms)
        workflow_rows, workflow_event_ids = await _workflow_candidates(conn, group_id, now_ms)
        session_rows.extend(model_rows)
        session_event_ids.extend(model_event_ids)
        result.session_events_archived = len(session_rows)
        result.workflow_observations_archived = len(workflow_rows)
        result.model_requests_deleted = len(request_ids)
        all_event_ids = list(dict.fromkeys(session_event_ids + workflow_event_ids))
        for batch in _chunks(all_event_ids):
            placeholders = ",".join("?" for _ in batch)
            async with conn.execute(
                f"""SELECT COUNT(*) FROM observation_artifacts
                      WHERE group_id=? AND event_id IN ({placeholders})""",
                (group_id, *batch),
            ) as cur:
                result.artifacts_deleted += int((await cur.fetchone())[0])
        if dry_run:
            await conn.rollback()
            return result.as_dict()
        receipts = [row["receipt"] for row in session_rows + workflow_rows]
        if receipts:
            await conn.executemany(
                """INSERT OR IGNORE INTO observability_retention_archive
                   (source,source_row_id,event_id,event_type,retention,
                    occurred_at,content_sha256)
                   VALUES (?,?,?,?,?,?,?)""",
                receipts,
            )
        for batch in _chunks(request_ids):
            placeholders = ",".join("?" for _ in batch)
            await conn.execute(
                f"DELETE FROM model_usage_ledger WHERE request_id IN ({placeholders})",
                batch,
            )
        session_row_ids = [row["row_id"] for row in session_rows]
        for batch in _chunks(session_row_ids):
            placeholders = ",".join("?" for _ in batch)
            await conn.execute(
                f"DELETE FROM session_events WHERE id IN ({placeholders})",
                batch,
            )
        workflow_row_ids = [row["row_id"] for row in workflow_rows]
        for batch in _chunks(workflow_row_ids):
            placeholders = ",".join("?" for _ in batch)
            await conn.execute(
                f"DELETE FROM workflow_observations WHERE id IN ({placeholders})",
                batch,
            )
        for batch in _chunks(all_event_ids):
            placeholders = ",".join("?" for _ in batch)
            await conn.execute(
                f"""DELETE FROM observation_artifacts
                      WHERE group_id=? AND event_id IN ({placeholders})""",
                (group_id, *batch),
            )
        await conn.commit()
    return result.as_dict()
