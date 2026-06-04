"""IPC 隧道消息 schema（V3 §4）—— 跨平台、依赖纯净。

下行 Supervisor → Worker；上行 Worker → Supervisor。
每条消息都带 group_id + trace_id 路由/追踪头（§10.2）。
"""

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

# ── 控制帧（连接握手，不属于业务上/下行集） ────────────────────────────────
HELLO = "hello"   # Worker → Supervisor 首帧，自报 worker_id 完成注册

# 注：LOG_RECORD 走独立日志通道，**绝不**与业务隧道共用（§10.2 队头阻塞）。


def envelope(msg_type: str, *, group_id: int, trace_id: str | None = None, **fields) -> dict:
    """构造带统一路由/追踪头的隧道消息（§10.2）。"""
    return {"type": msg_type, "group_id": group_id, "trace_id": trace_id, **fields}
