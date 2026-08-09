"""Versioned browser event envelope with legacy top-level compatibility."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from typing import Any


PROTOCOL_VERSION = 1


def make_event_envelope(
    payload: dict[str, Any],
    *,
    group_id: int | None = None,
) -> dict[str, Any]:
    """Normalize a bus payload without breaking existing browser consumers."""
    body = dict(payload or {})
    metadata = body.get("_observability") or {}
    event_id = body.get("event_id") or metadata.get("event_id") or f"evt_{uuid4().hex}"
    event_type = str(body.get("type") or body.get("event_type") or "unknown")
    resolved_group_id = group_id if group_id is not None else body.get("group_id")
    envelope = {
        "protocol_version": PROTOCOL_VERSION,
        "event_id": str(event_id),
        "event_type": event_type,
        "occurred_at": body.get("occurred_at") or datetime.now(timezone.utc).isoformat(),
        "group_id": resolved_group_id,
        "session_id": body.get("session_id"),
        "workflow_id": body.get("workflow_id"),
        "request_id": body.get("request_id"),
        "payload": body,
        # Compatibility fields for clients that still read the old shape.
        "type": event_type,
    }
    return envelope
