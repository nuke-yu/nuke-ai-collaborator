import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List

# ── 下行 (Supervisor → Worker) ────────────────────────────────────────────
USER_MESSAGE = "user_message"            # 用户消息
ABORT = "abort"                          # 中止该群在跑任务
PERMISSION_RESPONSE = "permission_response"  # 权限审批回复
CONFIRM = "confirm"                      # 人在工作流确认门点了「确认」
START_WORKFLOW = "start_workflow"        # 启动 RD 人确认流水线
WORKFLOW_NEXT = "workflow_next"          # 手动推进工作流到下一阶段（编排状态在 worker）
WORKFLOW_END = "workflow_end"            # 结束工作流（编排状态在 worker，清持久化 + 广播）
WAKE_TRIGGER = "wake_trigger"            # cron/告警唤醒沉睡群
RELEASE_LEASE = "release_lease"          # CELL-18: Request worker to release group
QUERY = "query"                          # 读 group 域数据，worker 查群库后经 bus 回 query_result
MUTATE = "mutate"                        # 写 group 域数据（反应/置顶/编辑/撤回），worker 写群库并广播更新

DOWNSTREAM = frozenset({USER_MESSAGE, ABORT, PERMISSION_RESPONSE, CONFIRM,
                        START_WORKFLOW, WORKFLOW_NEXT, WORKFLOW_END,
                        WAKE_TRIGGER, RELEASE_LEASE, QUERY, MUTATE})

# ── 上行 (Worker → Supervisor) ────────────────────────────────────────────
BROADCAST = "broadcast"                  # 包裹任一 bus 事件，供 Supervisor 扇出给浏览器
UNREAD_DELTA = "unread_delta"            # 未读增量（Supervisor 落中心库，§10.1/3）
STATS_REPORT = "stats_report"            # 可观测性聚合（DFT-057 跨 worker）
LEASE_RELEASED = "lease_released"        # CELL-18: Worker ACK that group is closed

UPSTREAM = frozenset({BROADCAST, UNREAD_DELTA, STATS_REPORT, LEASE_RELEASED})

# ── MCP collector 总线（跨群组单例进程，经 Supervisor 作 bus 中继）────────────
MCP_COLLECTOR_ID = "mcp-collector"       # collector 连接的 well-known worker_id
MCP_CALL = "mcp_call"                    # worker→sup→collector：执行一个 MCP 工具
MCP_RESULT = "mcp_result"                # collector→sup→worker：工具结果（按 request_id）
MCP_SCHEMAS = "mcp_schemas"             # collector→sup→workers：当前 MCP 工具表快照（push）
MCP_AUTH_START = "mcp_auth_start"        # worker→sup→collector：为某 server 启动 OAuth（回 MCP_RESULT 带 URL）
MCP_OAUTH_CALLBACK = "mcp_oauth_callback"  # main→collector：授权码回调（code/state）

MCP_BUS = frozenset({MCP_CALL, MCP_RESULT, MCP_SCHEMAS, MCP_AUTH_START, MCP_OAUTH_CALLBACK})

# ── 控制帧（连接握手，不属于业务上/下行集） ────────────────────────────────
HELLO = "hello"   # Worker / collector → Supervisor 首帧，自报 worker_id 完成注册

PROTOCOL_VERSION = 1

FRAME_TYPES = {}

def register_frame_type(msg_type: str):
    def decorator(cls):
        FRAME_TYPES[msg_type] = cls
        return cls
    return decorator

@dataclass(eq=False)
class BaseFrame:
    type: str
    group_id: int = 0
    trace_id: Optional[str] = None
    v: int = PROTOCOL_VERSION
    extra: Dict[str, Any] = field(default_factory=dict, init=False)

    def keys(self) -> list:
        dc_fields = [f.name for f in dataclasses.fields(self) if f.name != 'extra']
        return dc_fields + list(self.extra.keys())

    def __getitem__(self, key):
        if key == 'extra':
            return self.extra
        if hasattr(self, key) and key != 'extra':
            return getattr(self, key)
        if key in self.extra:
            return self.extra[key]
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return (hasattr(self, key) and key != 'extra') or (key in self.extra)

    def __getattr__(self, name):
        if name == 'extra':
            raise AttributeError()
        if name in self.extra:
            return self.extra[name]
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def to_dict(self) -> dict:
        res = {f.name: getattr(self, f.name) for f in dataclasses.fields(self) if f.name != 'extra'}
        res.update(self.extra)
        return res

    def __eq__(self, other):
        if isinstance(other, dict):
            for k, v in other.items():
                try:
                    if self[k] != v:
                        return False
                except KeyError:
                    return False
            return True
        if hasattr(other, 'to_dict'):
            return self.to_dict() == other.to_dict()
        return super().__eq__(other)


@register_frame_type(HELLO)
@dataclass(eq=False)
class HelloFrame(BaseFrame):
    worker_id: str = ""

@register_frame_type(USER_MESSAGE)
@dataclass(eq=False)
class UserMessageFrame(BaseFrame):
    member_id: int = 0
    content: str = ""
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    online_ids: List[int] = field(default_factory=list)
    reply_to_id: Optional[int] = None
    lang: Optional[str] = None

@register_frame_type(ABORT)
@dataclass(eq=False)
class AbortFrame(BaseFrame):
    pass

@register_frame_type(PERMISSION_RESPONSE)
@dataclass(eq=False)
class PermissionResponseFrame(BaseFrame):
    request_id: str = ""
    approved: bool = False
    persistence: str = "once"

@register_frame_type(CONFIRM)
@dataclass(eq=False)
class ConfirmFrame(BaseFrame):
    gate_id: str = ""

@register_frame_type(START_WORKFLOW)
@dataclass(eq=False)
class StartWorkflowFrame(BaseFrame):
    body: Optional[dict] = None
    lang: Optional[str] = None

@register_frame_type(WORKFLOW_NEXT)
@dataclass(eq=False)
class WorkflowNextFrame(BaseFrame):
    pass

@register_frame_type(WORKFLOW_END)
@dataclass(eq=False)
class WorkflowEndFrame(BaseFrame):
    pass

@register_frame_type(WAKE_TRIGGER)
@dataclass(eq=False)
class WakeTriggerFrame(BaseFrame):
    bot_id: Optional[int] = None
    content: str = ""

@register_frame_type(RELEASE_LEASE)
@dataclass(eq=False)
class ReleaseLeaseFrame(BaseFrame):
    pass

@register_frame_type(QUERY)
@dataclass(eq=False)
class QueryFrame(BaseFrame):
    req_id: str = ""
    query: str = ""
    limit: Optional[int] = None
    before_id: Optional[int] = None
    after_id: Optional[int] = None
    q: Optional[str] = None

@register_frame_type(MUTATE)
@dataclass(eq=False)
class MutateFrame(BaseFrame):
    action: str = ""
    member_id: int = 0
    msg_id: int = 0
    emoji: Optional[str] = None
    content: Optional[str] = None

@register_frame_type(BROADCAST)
@dataclass(eq=False)
class BroadcastFrame(BaseFrame):
    payload: dict = field(default_factory=dict)

@register_frame_type(UNREAD_DELTA)
@dataclass(eq=False)
class UnreadDeltaFrame(BaseFrame):
    pass

@register_frame_type(STATS_REPORT)
@dataclass(eq=False)
class StatsReportFrame(BaseFrame):
    payload: dict = field(default_factory=dict)

@register_frame_type(LEASE_RELEASED)
@dataclass(eq=False)
class LeaseReleasedFrame(BaseFrame):
    pass

@register_frame_type(MCP_CALL)
@dataclass(eq=False)
class McpCallFrame(BaseFrame):
    request_id: str = ""
    origin_worker_id: str = ""
    tool: str = ""
    arguments: dict = field(default_factory=dict)

@register_frame_type(MCP_RESULT)
@dataclass(eq=False)
class McpResultFrame(BaseFrame):
    request_id: str = ""
    origin_worker_id: str = ""
    result: Any = None
    is_error: bool = False

@register_frame_type(MCP_SCHEMAS)
@dataclass(eq=False)
class McpSchemasFrame(BaseFrame):
    payload: dict = field(default_factory=dict)

@register_frame_type(MCP_AUTH_START)
@dataclass(eq=False)
class McpAuthStartFrame(BaseFrame):
    request_id: str = ""
    origin_worker_id: str = ""
    server: str = ""

@register_frame_type(MCP_OAUTH_CALLBACK)
@dataclass(eq=False)
class McpOAuthCallbackFrame(BaseFrame):
    state: str = ""
    code: str = ""


def parse_frame(data: dict) -> BaseFrame:
    if not isinstance(data, dict):
        raise TypeError("Frame must be a dictionary")
    
    msg_type = data.get("type")
    cls = FRAME_TYPES.get(msg_type, BaseFrame)
    
    field_names = {f.name for f in dataclasses.fields(cls) if f.name != 'extra'}
    
    known_args = {}
    extra_args = {}
    for k, v in data.items():
        if k in field_names:
            known_args[k] = v
        else:
            extra_args[k] = v
            
    frame = cls(**known_args)
    frame.extra = extra_args
    return frame

def envelope(msg_type: str, *, group_id: int, trace_id: str | None = None, **fields) -> BaseFrame:
    """构造带统一路由/追踪头的隧道消息（§10.2）。"""
    return parse_frame({"v": PROTOCOL_VERSION, "type": msg_type, "group_id": group_id, "trace_id": trace_id, **fields})

