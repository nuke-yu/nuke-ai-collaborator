"""
bus/events.py — 全量事件类型注册表

所有可通过 bus 传递的事件都在这里定义。
每个事件类必须携带 group_id（adapter 用它路由到对应 WS room）。
"""
from dataclasses import dataclass
from typing import Any

# 中心注册表：type string → 事件类
_registry: dict[str, type] = {}


def event(type_name: str):
    """装饰器：注册事件类，自动附加 .type 类属性。"""
    def decorator(cls):
        cls.type = type_name
        _registry[type_name] = cls
        return dataclass(cls)
    return decorator


# ─── 流式输出 ─────────────────────────────────────────────────────────────────

@event("stream_start")
class StreamStart:
    group_id:     int
    temp_id:      str
    member_id:    int
    sender_name:  str
    sender_type:  str
    avatar_color: str


@event("stream_chunk")
class StreamChunk:
    group_id: int
    temp_id:  str
    delta:    str


@event("stream_error")
class StreamError:
    group_id: int
    temp_id:  str
    message:  str


@event("stream_end")
class StreamEnd:
    group_id:    int
    temp_id:     str
    id:          int
    member_id:   int
    sender_name: str
    preview:     str
    created_at:  str


@event("stream_aborted")
class StreamAborted:
    group_id:  int
    temp_id:   str
    member_id: int


# ─── 消息 ─────────────────────────────────────────────────────────────────────

@event("message")
class Message:
    """聊天消息（用户或 bot）。payload 直接来自 DB row，字段动态。"""
    group_id:     int
    id:           int
    member_id:    int
    sender_name:  str
    sender_type:  str
    content:      str | None
    created_at:   str
    reply_to_id:  int | None  = None
    file_url:     str | None  = None
    file_name:    str | None  = None
    file_size:    int | None  = None
    file_type:    str | None  = None
    is_auto_reply: bool       = False
    is_deleted:   bool        = False
    reactions:    Any         = None


@event("read")
class Read:
    group_id:     int
    member_id:    int
    last_read_id: int


# ─── Presence ────────────────────────────────────────────────────────────────

@event("presence")
class Presence:
    group_id:  int
    member_id: int
    online:    bool


# ─── Bot 状态 ─────────────────────────────────────────────────────────────────

@event("typing")
class Typing:
    group_id:     int
    sender_name:  str
    avatar_color: str


@event("error")
class Error:
    group_id: int
    message:  str


# ─── Steer / followup ────────────────────────────────────────────────────────

@event("steer_queued")
class SteerQueued:
    group_id:  int
    member_id: int
    message:   str


@event("followup_start")
class FollowupStart:
    group_id:  int
    member_id: int
    message:   str


@event("steer_injected")
class SteerInjected:
    group_id:  int
    temp_id:   str
    message:   str


@event("rewake_injected")
class RewakeInjected:
    group_id: int
    temp_id:  str
    message:  str


# ─── Tool 执行 ────────────────────────────────────────────────────────────────

@event("tool_call")
class ToolCall:
    group_id:  int
    temp_id:   str
    tool_name: str
    tool_input: Any


@event("tool_result")
class ToolResult:
    group_id:  int
    temp_id:   str
    tool_name: str
    result:    Any
    error:     bool = False


# ─── ReAct executor ──────────────────────────────────────────────────────────

@event("react_thought")
class ReactThought:
    group_id: int
    temp_id:  str
    thought:  str


@event("react_action")
class ReactAction:
    group_id: int
    temp_id:  str
    tool:     str
    input:    Any


@event("react_observation")
class ReactObservation:
    group_id:    int
    temp_id:     str
    observation: str


# ─── 工作流 ───────────────────────────────────────────────────────────────────

@event("workflow_update")
class WorkflowUpdate:
    group_id: int
    active:   bool
    stages:   list | None = None
    current:  int | None  = None
    done:     bool        = False
    awaiting_confirm: str | None = None   # gate_id 时表示该群正挂在人确认门上


@event("workflow_paused")
class WorkflowPaused:
    group_id: int
    reason:   str  # e.g., 'gate' or 'done' or 'pause'



# ─── Compaction ──────────────────────────────────────────────────────────────

@event("compaction")
class Compaction:
    group_id: int
    temp_id:  str
    summary:  str


# ─── Skill ───────────────────────────────────────────────────────────────────

@event("skills_loaded")
class SkillsLoaded:
    group_id: int
    temp_id:  str
    skills:   list


@event("skill_fork_start")
class SkillForkStart:
    group_id:   int
    temp_id:    str
    skill_name: str


@event("skill_fork_end")
class SkillForkEnd:
    group_id:   int
    temp_id:    str
    skill_name: str


@event("skill_draft_added")
class SkillDraftAdded:
    group_id:   int
    temp_id:    str
    skill_name: str
    content:    str


# ─── Before-finalize (权限审批) ───────────────────────────────────────────────

@event("before_finalize_review")
class BeforeFinalizeReview:
    group_id: int
    temp_id:  str
    content:  str


@event("before_finalize_approved")
class BeforeFinalizeApproved:
    group_id: int
    temp_id:  str


@event("before_finalize_rejected")
class BeforeFinalizeRejected:
    group_id: int
    temp_id:  str


# ─── Permission ──────────────────────────────────────────────────────────────

@event("permission_asked")
class PermissionAsked:
    group_id:   int
    request_id: str
    bot_id:     int
    tool_name:  str
    args:       Any


# ─── R&D Domain Events (研发业务领域事件) ───────────────────────────────────

@event("rd_ticket_created")
class TicketCreated:
    """BA Bot 产生了新的 Jira Ticket 或任务。"""
    group_id:    int
    ticket_id:   str
    title:       str
    description: str
    priority:    str = "medium"
    creator_id:  int | None = None


@event("rd_code_committed")
class CodeCommitted:
    """Dev Bot 完成了代码提交。"""
    group_id:    int
    ticket_id:   str
    files:       list[str]
    commit_msg:  str
    author_id:   int


@event("rd_task_failed")
class TaskFailed:
    """研发环节（开发、测试、部署）中途失败。"""
    group_id:  int
    ticket_id: str
    reason:    str
    stage:     str  # e.g., 'coding', 'testing'
    bot_id:    int
