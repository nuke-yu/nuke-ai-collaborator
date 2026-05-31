"""IPC 隧道消息 schema（V3 §4）—— 跨平台、依赖纯净。

下行 Supervisor → Worker；上行 Worker → Supervisor。
每条消息都带 group_id + trace_id 路由/追踪头（§10.2）。
"""

# ── 下行 (Supervisor → Worker) ────────────────────────────────────────────
USER_MESSAGE = "user_message"            # 用户消息
ABORT = "abort"                          # 中止该群在跑任务
PERMISSION_RESPONSE = "permission_response"  # 权限审批回复
WAKE_TRIGGER = "wake_trigger"            # cron/告警唤醒沉睡群

DOWNSTREAM = frozenset({USER_MESSAGE, ABORT, PERMISSION_RESPONSE, WAKE_TRIGGER})

# ── 上行 (Worker → Supervisor) ────────────────────────────────────────────
BROADCAST = "broadcast"                  # 包裹任一 bus 事件，供 Supervisor 扇出给浏览器
UNREAD_DELTA = "unread_delta"            # 未读增量（Supervisor 落中心库，§10.1/3）
STATS_REPORT = "stats_report"            # 可观测性聚合（DFT-057 跨 worker）

UPSTREAM = frozenset({BROADCAST, UNREAD_DELTA, STATS_REPORT})

# 注：LOG_RECORD 走独立日志通道，**绝不**与业务隧道共用（§10.2 队头阻塞）。


def envelope(msg_type: str, *, group_id: int, trace_id: str | None = None, **fields) -> dict:
    """构造带统一路由/追踪头的隧道消息（§10.2）。"""
    return {"type": msg_type, "group_id": group_id, "trace_id": trace_id, **fields}
