"""Unified, group-local Timeline projection for workflow and session events."""

from __future__ import annotations

import base64
import json
from typing import Any, Iterable, Mapping

import aiosqlite

from db import get_db

from .event_policy import OBSERVABILITY_KEY, classify_event


TIMELINE_SCHEMA_VERSION = 1
_SOURCE_RANK = {"session": 0, "workflow": 1}
_VALID_SOURCES = frozenset({"session", "permission", "workflow"})


def _encode_cursor(key: tuple[int, int, int]) -> str:
    raw = json.dumps({"v": 1, "k": list(key)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[int, int, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode())
        key = data["k"]
        if data.get("v") != 1 or not isinstance(key, list) or len(key) != 3:
            raise ValueError
        decoded = tuple(int(value) for value in key)
        if decoded[0] < 0 or decoded[1] not in _SOURCE_RANK.values() or decoded[2] < 1:
            raise ValueError
        return decoded
    except Exception as exc:
        raise ValueError("Invalid timeline cursor") from exc


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _policy_for_session(event_type: str, payload: dict[str, Any], row_id: int) -> dict[str, Any]:
    stored = payload.pop(OBSERVABILITY_KEY, None)
    if isinstance(stored, Mapping):
        policy = dict(stored)
    else:
        policy = classify_event(event_type, payload).to_metadata()
        policy["event_id"] = f"legacy_session_event_{row_id}"
    policy.setdefault("event_id", f"session_event_{row_id}")
    return policy


def _permission_actor(payload: Mapping[str, Any], bot_id: int) -> dict[str, Any]:
    if payload.get("decision_source") == "human_response":
        return {"type": "human"}
    return {"type": "bot", "id": bot_id}


def _session_item(row: Mapping[str, Any]) -> dict[str, Any]:
    row_id = int(row["row_id"])
    event_type = str(row["event_type"])
    payload = _json_object(row["body_json"])
    policy = _policy_for_session(event_type, payload, row_id)
    session_id = str(row["session_id"])
    bot_id = int(row["bot_id"])
    is_permission = event_type.startswith("permission_")
    permission_id = str(payload.get("permission_id") or "")
    source = "permission" if is_permission else "session"
    aggregate = (
        {"type": "permission", "id": permission_id or policy["event_id"]}
        if is_permission
        else {"type": "session", "id": session_id}
    )
    return {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "event_id": policy["event_id"],
        "occurred_at": int(row["occurred_at"]),
        "source": source,
        "event_type": event_type,
        "aggregate": aggregate,
        "context": {
            "group_id": int(row["group_id"]),
            "workflow_id": str(payload.get("workflow_id") or ""),
            "session_id": session_id,
            "bot_id": bot_id,
            "permission_id": permission_id,
        },
        "actor": _permission_actor(payload, bot_id) if is_permission else {"type": "bot", "id": bot_id},
        "payload": payload,
        "evidence_links": _json_array(row.get("evidence_links_json")),
        "policy": policy,
        "_cursor_key": (int(row["occurred_at"]), _SOURCE_RANK["session"], row_id),
    }


def _json_array(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _workflow_item(row: Mapping[str, Any]) -> dict[str, Any]:
    row_id = int(row["row_id"])
    envelope = _json_object(row["body_json"])
    event_type = str(row["event_type"])
    observation_id = str(row["observation_id"])
    workflow_id = str(row["workflow_id"])
    payload = _json_object(envelope.get("payload"))
    policy = _json_object(envelope.get("policy"))
    if "business_significant" not in policy:
        policy = classify_event(event_type, payload).to_metadata()
    policy["event_id"] = observation_id
    context = _json_object(envelope.get("context"))
    context["group_id"] = int(row["group_id"])
    context["workflow_id"] = workflow_id
    if row.get("session_id"):
        context["session_id"] = str(row["session_id"])

    envelope["schema_version"] = TIMELINE_SCHEMA_VERSION
    envelope["source"] = "workflow"
    envelope["event_id"] = observation_id
    envelope["event_type"] = event_type
    envelope["occurred_at"] = int(row["occurred_at"])
    envelope["aggregate"] = {"type": "workflow", "id": workflow_id}
    envelope["context"] = context
    envelope["actor"] = _json_object(envelope.get("actor")) or {"type": "system"}
    envelope["payload"] = payload
    envelope["policy"] = policy
    envelope["_cursor_key"] = (int(row["occurred_at"]), _SOURCE_RANK["workflow"], row_id)
    return envelope


def _matches(
    item: Mapping[str, Any],
    *,
    sources: set[str],
    event_types: set[str],
    event_classes: set[str],
    business_significant: bool | None,
) -> bool:
    if sources and item.get("source") not in sources:
        return False
    if event_types and item.get("event_type") not in event_types:
        return False
    policy = item.get("policy") if isinstance(item.get("policy"), Mapping) else {}
    if business_significant is not None and bool(policy.get("business_significant")) != business_significant:
        return False
    classes = set(policy.get("classes") or ())
    return not event_classes or bool(classes & event_classes)


async def _read_chunk(
    group_id: int,
    *,
    cursor: tuple[int, int, int] | None,
    limit: int,
    workflow_id: str | None,
    session_id: str | None,
) -> list[dict[str, Any]]:
    session_where = ["s.group_id = ?"]
    session_params: list[Any] = [group_id]
    if session_id:
        session_where.append("se.session_id = ?")
        session_params.append(session_id)
    if workflow_id:
        session_where.append(
            "CASE WHEN json_valid(se.payload) THEN json_extract(se.payload, '$.workflow_id') END = ?"
        )
        session_params.append(workflow_id)

    workflow_where = ["wo.group_id = ?"]
    workflow_params: list[Any] = [group_id]
    if workflow_id:
        workflow_where.append("wo.workflow_id = ?")
        workflow_params.append(workflow_id)
    if session_id:
        workflow_where.append("wo.session_id = ?")
        workflow_params.append(session_id)

    sql = f"""
        SELECT * FROM (
            SELECT 'session' AS storage_source, 0 AS source_rank, se.id AS row_id,
                   se.event_type, se.payload AS body_json,
                   COALESCE(CAST(strftime('%s', se.created_at) AS INTEGER) * 1000, 0) AS occurred_at,
                   se.session_id, s.bot_id, s.group_id,
                   '' AS observation_id, '' AS workflow_id,
                   COALESCE((
                       SELECT json_group_array(json_object(
                           'kind',sel.evidence_kind,'ref',sel.evidence_ref,
                           'relation',sel.relation,'metadata',json(sel.metadata_json)
                       ))
                         FROM session_evidence_links sel
                        WHERE sel.session_event_id=se.id
                   ), '[]') AS evidence_links_json
              FROM session_events se
              JOIN agent_sessions s ON s.id = se.session_id
             WHERE {' AND '.join(session_where)}
            UNION ALL
            SELECT 'workflow' AS storage_source, 1 AS source_rank, wo.id AS row_id,
                   wo.event_type, wo.envelope_json AS body_json, wo.occurred_at,
                   wo.session_id, 0 AS bot_id, wo.group_id,
                   wo.observation_id, wo.workflow_id, '[]' AS evidence_links_json
              FROM workflow_observations wo
             WHERE {' AND '.join(workflow_where)}
        ) timeline
    """
    params = session_params + workflow_params
    if cursor:
        timestamp, source_rank, row_id = cursor
        sql += """ WHERE occurred_at < ?
                    OR (occurred_at = ? AND source_rank < ?)
                    OR (occurred_at = ? AND source_rank = ? AND row_id < ?)"""
        params.extend((timestamp, timestamp, source_rank, timestamp, source_rank, row_id))
    sql += " ORDER BY occurred_at DESC, source_rank DESC, row_id DESC LIMIT ?"
    params.append(limit)

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_group_timeline(
    group_id: int,
    *,
    limit: int = 50,
    cursor: str | None = None,
    sources: Iterable[str] = (),
    event_types: Iterable[str] = (),
    event_classes: Iterable[str] = (),
    business_significant: bool | None = True,
    workflow_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Return newest-first, cursor-paginated observations from one group DB."""
    bounded_limit = max(1, min(int(limit), 200))
    selected_sources = {str(value) for value in sources if value}
    invalid_sources = selected_sources - _VALID_SOURCES
    if invalid_sources:
        raise ValueError(f"Invalid timeline source: {sorted(invalid_sources)[0]}")
    selected_types = {str(value) for value in event_types if value}
    selected_classes = {str(value) for value in event_classes if value}
    scan_cursor = _decode_cursor(cursor)
    matches: list[dict[str, Any]] = []
    chunk_size = max(100, min(500, bounded_limit * 3))

    while len(matches) <= bounded_limit:
        rows = await _read_chunk(
            group_id,
            cursor=scan_cursor,
            limit=chunk_size,
            workflow_id=workflow_id,
            session_id=session_id,
        )
        if not rows:
            break
        for row in rows:
            item = _workflow_item(row) if row["storage_source"] == "workflow" else _session_item(row)
            scan_cursor = item["_cursor_key"]
            if _matches(
                item,
                sources=selected_sources,
                event_types=selected_types,
                event_classes=selected_classes,
                business_significant=business_significant,
            ):
                matches.append(item)
                if len(matches) > bounded_limit:
                    break
        if len(matches) > bounded_limit or len(rows) < chunk_size:
            break

    has_more = len(matches) > bounded_limit
    items = matches[:bounded_limit]
    next_cursor = _encode_cursor(items[-1]["_cursor_key"]) if has_more and items else None
    for item in items:
        item.pop("_cursor_key", None)
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}
