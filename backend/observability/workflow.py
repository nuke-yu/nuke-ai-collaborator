"""Durable, group-local observations for workflow state transitions."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterable, Mapping

from db import get_db, write_connect
from .payload_policy import PayloadArtifact, persist_artifact, prepare_payload


log = logging.getLogger(__name__)
WORKFLOW_OBSERVATION_SCHEMA_VERSION = 1


def build_workflow_observation(
    group_id: int,
    orchestrator_id: str,
    descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the canonical envelope from a side-effect-free transition descriptor."""
    envelope, _artifact = _prepare_workflow_observation(group_id, orchestrator_id, descriptor)
    return envelope


def _prepare_workflow_observation(
    group_id: int,
    orchestrator_id: str,
    descriptor: Mapping[str, Any],
) -> tuple[dict[str, Any], PayloadArtifact | None]:
    event_type = str(descriptor.get("event_type") or "").strip()
    workflow_id = str(descriptor.get("workflow_id") or "").strip()
    if not event_type:
        raise ValueError("workflow observation requires event_type")
    if not workflow_id:
        raise ValueError("workflow observation requires workflow_id")

    payload = descriptor.get("payload")
    if not isinstance(payload, Mapping):
        payload = {}
    prepared = prepare_payload(event_type, payload)
    policy = dict(prepared.payload.pop("_observability"))
    envelope = {
        "schema_version": WORKFLOW_OBSERVATION_SCHEMA_VERSION,
        "event_id": policy["event_id"],
        "occurred_at": int(descriptor.get("occurred_at") or time.time() * 1000),
        "event_type": event_type,
        "aggregate": {
            "type": "workflow",
            "id": workflow_id,
        },
        "context": {
            "group_id": int(group_id),
            "orchestrator_id": str(orchestrator_id or "workflow_v1"),
            "workflow_id": workflow_id,
            "stage_id": str(descriptor.get("stage_id") or ""),
            "stage_index": descriptor.get("stage_index"),
            "gate_id": str(descriptor.get("gate_id") or ""),
            "gate_instance_id": str(descriptor.get("gate_instance_id") or ""),
            "session_id": str(descriptor.get("session_id") or ""),
        },
        "actor": dict(descriptor.get("actor") or {"type": "system"}),
        "payload": prepared.payload,
        "policy": policy,
    }
    return envelope, prepared.artifact


async def record_workflow_observations(
    group_id: int,
    orchestrator_id: str,
    descriptors: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Persist envelopes without letting telemetry failure alter orchestration."""
    try:
        async with write_connect() as db:
            envelopes = await insert_workflow_observations(
                db, group_id, orchestrator_id, descriptors
            )
            await db.commit()

        try:
            from .event_policy import classify_event
            from .otel_exporter import get_otel_exporter
            from .prometheus_exporter import get_prometheus_metrics
            for desc in descriptors:
                ev_type = str(desc.get("event_type") or "workflow_observation")
                ev_payload = desc.get("payload") or {}
                resolved = classify_event(ev_type, ev_payload)
                get_otel_exporter().record_event_policy(ev_type, ev_payload, resolved)
                get_prometheus_metrics().record_event_policy(ev_type, resolved, status="success")
        except Exception:
            pass
    except Exception:
        log.exception(
            "workflow observation persistence failed group=%s orchestrator=%s",
            group_id,
            orchestrator_id,
        )
        return []
    return envelopes


async def insert_workflow_observations(
    conn,
    group_id: int,
    orchestrator_id: str,
    descriptors: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Strict no-commit insert primitive for a caller-owned SQLite transaction."""
    prepared_observations = [
        _prepare_workflow_observation(group_id, orchestrator_id, descriptor)
        for descriptor in descriptors
    ]
    envelopes = [item[0] for item in prepared_observations]
    for _envelope, artifact in prepared_observations:
        await persist_artifact(conn, group_id, artifact)
    if envelopes:
        await conn.executemany(
            """INSERT OR IGNORE INTO workflow_observations
               (observation_id,group_id,workflow_id,event_type,stage_id,
                gate_id,gate_instance_id,session_id,envelope_json,occurred_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    envelope["event_id"],
                    group_id,
                    envelope["context"]["workflow_id"],
                    envelope["event_type"],
                    envelope["context"]["stage_id"],
                    envelope["context"]["gate_id"],
                    envelope["context"]["gate_instance_id"],
                    envelope["context"]["session_id"],
                    json.dumps(envelope, ensure_ascii=False),
                    envelope["occurred_at"],
                )
                for envelope in envelopes
            ],
        )
    return envelopes


async def get_workflow_observations(
    group_id: int,
    *,
    workflow_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Read a bounded timeline in insertion order for API/tests."""
    bounded_limit = max(1, min(int(limit), 1000))
    sql = "SELECT envelope_json FROM workflow_observations WHERE group_id = ?"
    params: list[Any] = [group_id]
    if workflow_id:
        sql += " AND workflow_id = ?"
        params.append(workflow_id)
    sql += " ORDER BY id ASC LIMIT ?"
    params.append(bounded_limit)
    async with get_db() as db:
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
    return [json.loads(row[0]) for row in rows]
