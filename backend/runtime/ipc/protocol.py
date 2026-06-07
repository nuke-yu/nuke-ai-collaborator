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

# ── MCP collector 总线（跨群组单例进程，经 Supervisor 作 bus 中继）────────────
# MCP 是跨群组通用能力，跑在独立的 mcp-collector 进程而非每个 worker。
# 流向：worker ──MCP_CALL──▶ supervisor ──MCP_CALL──▶ collector
#       worker ◀─MCP_RESULT── supervisor ◀─MCP_RESULT── collector
# collector 启动/ToolListChanged 时 push MCP_SCHEMAS；supervisor 缓存并下推给各 worker。
# 按 request_id 关联应答、origin_worker_id 回中继。权限在 worker 侧（call 前已 check），
# collector 只是总线内侧可信执行器。
MCP_COLLECTOR_ID = "mcp-collector"       # collector 连接的 well-known worker_id
MCP_CALL = "mcp_call"                    # worker→sup→collector：执行一个 MCP 工具
MCP_RESULT = "mcp_result"                # collector→sup→worker：工具结果（按 request_id）
MCP_SCHEMAS = "mcp_schemas"             # collector→sup→workers：当前 MCP 工具表快照（push）
# OAuth（McpAuthTool 式）：bot 调 mcp_authenticate → 触发授权 → 返回授权 URL 进聊天；
# 用户在浏览器授权 → main 的回调路由把 code/state 经 bus 直发 collector 完成握手。
MCP_AUTH_START = "mcp_auth_start"        # worker→sup→collector：为某 server 启动 OAuth（回 MCP_RESULT 带 URL）
MCP_OAUTH_CALLBACK = "mcp_oauth_callback"  # main→collector：授权码回调（code/state）

MCP_BUS = frozenset({MCP_CALL, MCP_RESULT, MCP_SCHEMAS, MCP_AUTH_START, MCP_OAUTH_CALLBACK})

# ── 控制帧（连接握手，不属于业务上/下行集） ────────────────────────────────
HELLO = "hello"   # Worker / collector → Supervisor 首帧，自报 worker_id 完成注册

# 注：LOG_RECORD 走独立日志通道，**绝不**与业务隧道共用（§10.2 队头阻塞）。


def envelope(msg_type: str, *, group_id: int, trace_id: str | None = None, **fields) -> dict:
    """构造带统一路由/追踪头的隧道消息（§10.2）。"""
    return {"type": msg_type, "group_id": group_id, "trace_id": trace_id, **fields}
