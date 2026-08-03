"""Session Execution Timeline Projector for UI drawer and debug views.

Converts raw session_events and workflow_observations into structured, human-readable
Timeline Nodes (Inputs -> Context -> Tools -> Permissions -> Deliverables -> Memory).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

import db as _db
from observability.timeline import get_group_timeline


@dataclass
class TimelineNode:
    node_id: str
    type: str  # context_injected | tool_execution | permission_approved | deliverable_produced | system_event | error
    title: str
    detail: str
    timestamp: int
    duration_s: float | None = None
    status: str = "success"
    artifact_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "type": self.type,
            "title": self.title,
            "detail": self.detail,
            "timestamp": self.timestamp,
            "duration_s": self.duration_s,
            "status": self.status,
            "artifact_ids": self.artifact_ids,
            "metadata": self.metadata,
        }


@dataclass
class ExecutionTimelineProjection:
    session_id: str
    group_id: int
    bot_id: int | None
    status: str
    total_duration_s: float
    nodes: list[TimelineNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "group_id": self.group_id,
            "bot_id": self.bot_id,
            "status": self.status,
            "total_duration_s": round(self.total_duration_s, 3),
            "nodes": [node.to_dict() for node in self.nodes],
        }


def project_event_to_node(event: Mapping[str, Any], idx: int) -> TimelineNode | None:
    """Project a raw timeline item into a UI TimelineNode."""
    event_type = str(event.get("event_type", ""))
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    policy = event.get("policy") if isinstance(event.get("policy"), dict) else {}
    timestamp = int(event.get("occurred_at", 0))
    event_id = str(event.get("event_id") or f"node_{idx}")

    # 1. Context / Memory Injection Events
    if event_type in ("session_start", "context_injected", "memory_fact_retrieved", "skill_loaded"):
        facts_count = payload.get("facts_count", 0)
        skills = payload.get("skills") or []
        detail_msg = f"Retrieved {facts_count} memory facts" if facts_count else "Initialized execution session"
        if skills:
            detail_msg += f" & loaded skills: {', '.join(skills)}"
        return TimelineNode(
            node_id=event_id,
            type="context_injected",
            title="Loaded Memory Context & Skills",
            detail=detail_msg,
            timestamp=timestamp,
            metadata={"facts_count": facts_count, "skills": skills},
        )

    # 2. Tool Executions
    elif event_type in ("tool_execution", "tool_call", "tool_completed", "tool_events"):
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "tool")
        duration_s = payload.get("duration_s") or payload.get("duration")
        if isinstance(duration_s, (int, float)):
            duration_s = float(duration_s)
        else:
            duration_s = None

        status = "failed" if payload.get("error") or payload.get("is_error") else "success"
        art_ids = payload.get("artifact_ids") or []
        if isinstance(art_ids, str):
            art_ids = [art_ids]
        elif not isinstance(art_ids, list):
            art_ids = []

        summary_detail = str(payload.get("summary") or payload.get("reason") or f"Called {tool_name}")
        return TimelineNode(
            node_id=event_id,
            type="tool_execution",
            title=f"Executed Tool: {tool_name}",
            detail=summary_detail,
            timestamp=timestamp,
            duration_s=duration_s,
            status=status,
            artifact_ids=art_ids,
            metadata={"tool_name": tool_name},
        )

    # 3. Permissions & Human Approvals
    elif event_type.startswith("permission_") or event_type in ("human_approval", "authorization_decision"):
        source = event.get("actor", {}).get("type", "human")
        action = str(payload.get("action") or payload.get("decision") or "approved")
        tool_pattern = str(payload.get("tool_pattern") or payload.get("tool_name") or "")
        return TimelineNode(
            node_id=event_id,
            type="permission_approved",
            title=f"Authorization: {action.upper()}",
            detail=f"Decision source: {source}. Target: {tool_pattern}",
            timestamp=timestamp,
            metadata={"action": action, "source": source},
        )

    # 4. Deliverables & Artifacts Produced
    elif event_type in ("deliverable_produced", "artifact_created", "workflow_deliverable", "file_saved"):
        art_ids = payload.get("artifact_ids") or []
        if payload.get("artifact_id"):
            art_ids.append(payload["artifact_id"])
        art_ids = list(set(art_ids))

        display_name = str(payload.get("display_name") or payload.get("title") or "Project Artifact")
        return TimelineNode(
            node_id=event_id,
            type="deliverable_produced",
            title=f"Deliverable: {display_name}",
            detail=str(payload.get("description") or "Produced project artifact"),
            timestamp=timestamp,
            artifact_ids=art_ids,
            metadata={"display_name": display_name},
        )

    # 5. System or General Execution Event (Fallback if business significant)
    elif policy.get("business_significant", True):
        return TimelineNode(
            node_id=event_id,
            type="system_event",
            title=f"Event: {event_type}",
            detail=str(payload.get("reason") or payload.get("summary") or event_type),
            timestamp=timestamp,
        )

    return None


async def project_session_timeline(
    group_id: int,
    session_id: str,
) -> ExecutionTimelineProjection:
    """Fetch raw events for a session and project into a structured ExecutionTimelineProjection."""
    from runtime.dbpaths import group_db_path

    # Verify session belongs to group_id
    async with _db.connect(group_db_path(group_id)) as conn:
        async with conn.execute(
            "SELECT group_id, bot_id, status, created_at FROM agent_sessions WHERE id = ? AND group_id = ?",
            (session_id, group_id),
        ) as cursor:
            session_row = await cursor.fetchone()

    if session_row is None:
        raise ValueError(f"Session not found or group mismatch: {session_id} (group={group_id})")

    bot_id = session_row[1]
    status = str(session_row[2] or "completed")

    # Fetch raw timeline items via existing get_group_timeline (business_significant=None to include all session events)
    raw_timeline = await get_group_timeline(
        group_id=group_id,
        session_id=session_id,
        business_significant=None,
        limit=500,
    )

    items = raw_timeline.get("items") or []
    # Reverse to present chronological order (oldest -> newest)
    chronological_items = list(reversed(items))

    nodes: list[TimelineNode] = []
    total_duration = 0.0

    for idx, item in enumerate(chronological_items):
        node = project_event_to_node(item, idx)
        if node:
            nodes.append(node)
            if node.duration_s:
                total_duration += node.duration_s

    return ExecutionTimelineProjection(
        session_id=session_id,
        group_id=group_id,
        bot_id=bot_id,
        status=status,
        total_duration_s=total_duration,
        nodes=nodes,
    )
