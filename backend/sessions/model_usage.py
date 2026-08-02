"""Request-level model usage ledger projected atomically from Session Events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiosqlite

import db as _db
from ai.pricing import PRICING_VERSION, calculate_cost


_TERMINAL_EVENTS = {
    "model_request_completed": "completed",
    "model_request_failed": "failed",
}


def _nonnegative_int(value: object, field: str) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if parsed < 0:
        raise ValueError(f"invalid {field}")
    return parsed


async def project_model_usage_event(
    conn,
    *,
    session_event_id: int,
    session_id: str,
    event_type: str,
    payload: Mapping[str, Any],
) -> None:
    """Project one lifecycle event; caller owns commit/rollback."""
    request_id = str(payload.get("request_id") or "").strip()
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not request_id or not provider or not model:
        raise ValueError("model request event requires request_id, provider, and model")

    if event_type == "model_request_started":
        requested_ordinal = _nonnegative_int(
            payload.get("request_ordinal"), "request_ordinal"
        )
        if requested_ordinal < 1:
            raise ValueError("request_ordinal must be positive")
        # A recovered session constructs a new AIService whose local ordinal starts
        # at one. Keep the durable sequence monotonic across those lifetimes.
        async with conn.execute(
            "SELECT COALESCE(MAX(request_ordinal),0) FROM model_usage_ledger WHERE session_id=?",
            (session_id,),
        ) as cur:
            previous = await cur.fetchone()
        ordinal = max(requested_ordinal, int(previous[0]) + 1)
        try:
            await conn.execute(
                """INSERT INTO model_usage_ledger
                   (request_id,session_id,request_ordinal,retry_of,operation,ticket_id,
                    provider,model,streaming,status,start_event_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_id,
                    session_id,
                    ordinal,
                    str(payload.get("retry_of") or ""),
                    str(payload.get("operation") or "inference"),
                    str(payload.get("ticket_id") or ""),
                    provider,
                    model,
                    int(bool(payload.get("streaming"))),
                    "started",
                    session_event_id,
                ),
            )
        except aiosqlite.IntegrityError as exc:
            raise ValueError(f"duplicate model request: {request_id}") from exc
        return

    status = _TERMINAL_EVENTS.get(event_type)
    if status is None:
        return
    async with conn.execute(
        """SELECT status,provider,model,ticket_id FROM model_usage_ledger
             WHERE request_id=? AND session_id=?""",
        (request_id, session_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise ValueError(f"model request start not found: {request_id}")
    if row[0] != "started":
        raise ValueError(f"model request already terminal: {request_id}")
    if row[1] != provider or row[2] != model:
        raise ValueError("model request terminal identity mismatch")
    terminal_ticket_id = str(payload.get("ticket_id") or "").strip()
    if row[3] and terminal_ticket_id and row[3] != terminal_ticket_id:
        raise ValueError("model request terminal ticket mismatch")
    ticket_id = row[3] or terminal_ticket_id

    usage = {
        "input_tokens": _nonnegative_int(payload.get("input_tokens"), "input_tokens"),
        "output_tokens": _nonnegative_int(payload.get("output_tokens"), "output_tokens"),
        "cache_read_tokens": _nonnegative_int(payload.get("cache_read_tokens"), "cache_read_tokens"),
        "cache_creation_tokens": _nonnegative_int(
            payload.get("cache_creation_tokens"), "cache_creation_tokens"
        ),
    }
    duration_ms = _nonnegative_int(payload.get("duration_ms"), "duration_ms")
    cost_usd = calculate_cost(provider, model, usage) if status == "completed" else 0.0
    await conn.execute(
        """UPDATE model_usage_ledger SET
               status=?,response_type=?,ticket_id=?,input_tokens=?,output_tokens=?,
               cache_read_tokens=?,cache_creation_tokens=?,cost_usd=?,
               pricing_version=?,duration_ms=?,error_type=?,final_event_id=?,
               completed_at=datetime('now')
             WHERE request_id=? AND session_id=?""",
        (
            status,
            str(payload.get("response_type") or ""),
            ticket_id,
            usage["input_tokens"],
            usage["output_tokens"],
            usage["cache_read_tokens"],
            usage["cache_creation_tokens"],
            cost_usd,
            PRICING_VERSION,
            duration_ms,
            str(payload.get("error_type") or "")[:120],
            session_event_id,
            request_id,
            session_id,
        ),
    )
    if status == "completed":
        await conn.execute(
            """UPDATE agent_sessions SET
                   input_tokens=input_tokens+?,output_tokens=output_tokens+?,
                   cache_read_tokens=cache_read_tokens+?,
                   cache_creation_tokens=cache_creation_tokens+?,
                   updated_at=datetime('now') WHERE id=?""",
            (
                usage["input_tokens"], usage["output_tokens"],
                usage["cache_read_tokens"], usage["cache_creation_tokens"],
                session_id,
            ),
        )
        if ticket_id and cost_usd:
            await conn.execute(
                "UPDATE tickets SET total_usd_cost=total_usd_cost+? WHERE ticket_id=?",
                (cost_usd, ticket_id),
            )


async def get_model_usage_ledger(
    group_id: int, *, session_id: str | None = None, limit: int = 200
) -> dict[str, Any]:
    """Read request rows plus exact totals for one group/session."""
    bounded_limit = max(1, min(int(limit), 1000))
    where = ["s.group_id=?"]
    params: list[Any] = [group_id]
    if session_id:
        where.append("l.session_id=?")
        params.append(session_id)
    clause = " AND ".join(where)
    async with _db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            f"""SELECT l.* FROM model_usage_ledger l
                  JOIN agent_sessions s ON s.id=l.session_id
                 WHERE {clause} ORDER BY l.started_at DESC,l.request_ordinal DESC LIMIT ?""",
            (*params, bounded_limit),
        ) as cur:
            rows = await cur.fetchall()
        async with conn.execute(
            f"""SELECT COUNT(*),
                       COALESCE(SUM(CASE WHEN l.status='completed' THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(CASE WHEN l.status='failed' THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(CASE WHEN l.status='started' THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(l.input_tokens),0),COALESCE(SUM(l.output_tokens),0),
                       COALESCE(SUM(l.cache_read_tokens),0),
                       COALESCE(SUM(l.cache_creation_tokens),0),COALESCE(SUM(l.cost_usd),0),
                       COALESCE(SUM(l.duration_ms),0)
                  FROM model_usage_ledger l JOIN agent_sessions s ON s.id=l.session_id
                 WHERE {clause}""",
            params,
        ) as cur:
            totals = await cur.fetchone()
    return {
        "items": [dict(row) for row in rows],
        "totals": {
            "requests": totals[0],
            "completed_requests": totals[1],
            "failed_requests": totals[2],
            "open_requests": totals[3],
            "input_tokens": totals[4],
            "output_tokens": totals[5],
            "cache_read_tokens": totals[6],
            "cache_creation_tokens": totals[7],
            "cost_usd": totals[8],
            "duration_ms": totals[9],
        },
    }
