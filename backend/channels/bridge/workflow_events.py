"""Runtime projection from committed Group workflow observations to Channel outbox."""
from __future__ import annotations

from typing import Any, Iterable

from channels.bridge.binding import ChannelBindingStore

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


async def append_workflow_channel_events(
    conn: Any,
    group_id: int,
    observations: Iterable[dict[str, Any]],
    binding_store: ChannelBindingStore,
) -> int:
    """Append notification intents to the caller's active Group transaction."""
    bindings = await binding_store.list_active_for_group(group_id)
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
