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
from executors.redaction import redact_secrets


_TIMELINE_TEXT_LIMIT = 4000


def _safe_text(value: Any, limit: int = _TIMELINE_TEXT_LIMIT) -> str:
    redacted, _ = redact_secrets(str(value or ""))
    return redacted[:limit]


def _safe_json(value: Any, limit: int = _TIMELINE_TEXT_LIMIT) -> Any:
    if not isinstance(value, (dict, list, tuple)):
        return _safe_text(value, limit)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return _safe_text(value, limit)
    if len(encoded) > limit:
        return _safe_text(encoded, limit)
    redacted = _safe_text(encoded, limit)
    try:
        return json.loads(redacted)
    except json.JSONDecodeError:
        return redacted


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

    # 1. AI Thought / Thinking Events
    if event_type in ("ai_thought", "thinking", "thought", "reasoning"):
        thought_text = _safe_text(payload.get("thought") or payload.get("content") or payload.get("text") or "AI reasoning...")
        return TimelineNode(
            node_id=event_id,
            type="thinking",
            title="AI 推理思考过程",
            detail=thought_text,
            timestamp=timestamp,
            metadata={"thought": thought_text},
        )

    # 2. Context / Memory Injection Events
    elif event_type in ("session_start", "context_injected", "memory_fact_retrieved", "skill_loaded"):
        facts_count = payload.get("facts_count", 0)
        skills = payload.get("skills") or []
        user_content = payload.get("user_content") or ""
        detail_msg = f"已初始化 AI 执行会话"
        if user_content:
            detail_msg += f"：{str(user_content)[:120]}"
        if facts_count:
            detail_msg += f" (调取 {facts_count} 条关联记忆)"
        if skills:
            detail_msg += f" & 加载技能包: {', '.join(skills)}"
        return TimelineNode(
            node_id=event_id,
            type="context_injected",
            title="加载会话与记忆上下文",
            detail=detail_msg,
            timestamp=timestamp,
            metadata={"facts_count": facts_count, "skills": skills, "user_content": user_content},
        )

    # 3. Tool Executions & Tool Results
    elif event_type in ("tool_execution", "tool_call", "tool_result", "tool_completed", "tool_events"):
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "tool")
        duration_s = payload.get("duration_s") or payload.get("duration")
        if not duration_s and payload.get("duration_ms"):
            duration_s = payload["duration_ms"] / 1000.0
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

        arguments = payload.get("arguments") or payload.get("args") or payload.get("tool_args") or {}
        result = payload.get("result") or payload.get("output") or payload.get("stdout") or ""

        # Build readable summary
        summary_detail = ""
        if isinstance(arguments, dict) and arguments:
            if "CommandLine" in arguments or "command" in arguments:
                cmd = arguments.get("CommandLine") or arguments.get("command")
                summary_detail = f"执行命令: {cmd}"
            elif "AbsolutePath" in arguments or "TargetFile" in arguments or "path" in arguments:
                filepath = arguments.get("AbsolutePath") or arguments.get("TargetFile") or arguments.get("path")
                summary_detail = f"操作文件: {filepath}"
            elif "Query" in arguments or "query" in arguments:
                query = arguments.get("Query") or arguments.get("query")
                summary_detail = f"检索内容: {query}"

        if not summary_detail:
            summary_detail = str(payload.get("summary") or payload.get("reason") or f"执行工具: {tool_name}")

        return TimelineNode(
            node_id=event_id,
            type="tool_execution",
            title=f"执行工具: {tool_name}",
            detail=summary_detail,
            timestamp=timestamp,
            duration_s=duration_s,
            status=status,
            artifact_ids=art_ids,
            metadata={
                "tool_name": tool_name,
                "arguments": _safe_json(arguments),
                "result": _safe_text(result),
                "is_error": status == "failed",
            },
        )

    # 4. Permissions & Human Approvals
    elif event_type.startswith("permission_") or event_type in ("human_approval", "authorization_decision"):
        source = event.get("actor", {}).get("type", "human")
        action = str(payload.get("action") or payload.get("decision") or "approved")
        tool_pattern = str(payload.get("tool_pattern") or payload.get("tool_name") or "")
        return TimelineNode(
            node_id=event_id,
            type="permission_approved",
            title=f"安全授权: {action.upper()}",
            detail=f"审批来源: {source}。 目标工具: {tool_pattern}",
            timestamp=timestamp,
            metadata={"action": action, "source": source, "tool_pattern": tool_pattern},
        )

    # 5. Deliverables & Artifacts Produced
    elif event_type in ("deliverable_produced", "artifact_created", "workflow_deliverable", "file_saved"):
        art_ids = payload.get("artifact_ids") or []
        if payload.get("artifact_id"):
            art_ids.append(payload["artifact_id"])
        art_ids = list(set(art_ids))

        display_name = str(payload.get("display_name") or payload.get("title") or "项目交付产物")
        return TimelineNode(
            node_id=event_id,
            type="deliverable_produced",
            title=f"交付物生成: {display_name}",
            detail=str(payload.get("description") or "成功生成阶段性项目产物"),
            timestamp=timestamp,
            artifact_ids=art_ids,
            metadata={"display_name": display_name},
        )

    # 6. System or General Execution Event (Fallback if business significant)
    elif policy.get("business_significant", True):
        return TimelineNode(
            node_id=event_id,
            type="system_event",
            title=f"系统事件: {event_type}",
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

    with _db.bind_db(group_db_path(group_id)):
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
