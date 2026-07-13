# Technical Issues — Code Review 2026-07-13

> **最终对齐版本（Codex + 原 Review 作者）。以下部署矩阵、最终优先级表和验收范围是唯一有效的执行依据。**
> 原始发现按维度保留在下方供参考，但严重度已被后续复核调整——以最终表为准。

---

## 部署模式安全矩阵

安全结论必须绑定部署模式，不能脱离上下文讨论：

| 模式 | 定位 | 安全边界 | 当前状态 |
|------|------|---------|---------|
| **standalone** (`docker-compose.standalone.yml`) | **dev / trusted single-user only** | `NUKE_SHELL_EXEC_BACKEND=local` 无沙箱，所有群组 workspace + central DB 在同一 volume 内，bot shell 可通过绝对路径访问一切 | ⚠️ 当前设了 `NUKE_ENV=production` 但无隔离——需去掉 production 标记或改为 container backend |
| **正式 production** (`docker-compose.yml` + sandbox) | multi-group | 必须使用 per-group container sandbox + `AUTH_SECRET` fail-closed + Docker policy boundary；不同人类用户之间的隔离还必须完成 AC1 | **AC1 完成前只允许单一人类信任域**；不得宣称跨用户群组安全隔离。另需补 AUTH_SECRET gate、HEALTHCHECK、socket-proxy 第一阶段 |
| **对外 / 多租户** | 不适用当前架构 | 在 AC1、密钥、文件边界和 sandbox/P0 部署项完成前**禁止** | DFT-082 仅记录历史 trusted-internal 取舍，不是安全豁免 |

---

## 最终优先级表（唯一有效版本）

> 基线 commit: `87f41ba`。经过 Codex 两轮复核 + 原 Review 作者 8 个验证 agent 逐条代码比对。

| # | 优先级 | 问题 | 状态 | 位置 |
|---|--------|------|------|------|
| 1 | **P0** | 应用层缺少 user↔group 授权边界——项目核心定义是 "Groups fully isolated"，但任意登录用户可访问任意群组、冒充任意 member。内部可信不等于群组隔离。DFT-082 是历史取舍，不覆盖当前产品要求。**必须正式决策**：(a) 实现 membership/role 模型 + `require_group_member`；或 (b) 正式修改 "Groups fully isolated" 的产品承诺。见架构文档 AC1 | 未修 | 所有 HTTP/WS 路由 |
| 2 | **P0** | Chroma fact ID 跨组覆盖——`fact_id = f"{message_id}_{idx}"` 缺 group_id/bot_id，per-group message ID 重复导致 upsert 静默覆盖。**代码改动局部，数据迁移不可省略**：新 ID namespace (`fact_{bot_id}_{group_id}_{message_id}_{idx}`) + 跨组不覆盖测试 + 删除并重建全部 fact-class 数据（含 `mem_type=fact` 及缺失 `mem_type` 的 legacy facts）。已被覆盖的数据无法从 Chroma 可靠还原，只能从 SQLite 原始消息重新提取 | 未修 | `memory.py:496` |
| 3 | **P0** | API-key `GET/PUT /api/config` 无 operator 校验。**验收范围必须包含**：(a) GET/PUT 都要求 operator + 审计日志；(b) 首个 operator 的部署配置（`NUKE_OPERATOR_USERS`/`NUKE_OPERATOR_IDS` 或数据库角色）；(c) production 未配置 operator 时行为明确：需要 UI 控制面则拒绝启动，否则显式禁用这些 endpoints，不能静默形成无人可管理的全 403 状态；(d) anonymous 401、普通用户 403、operator 成功的测试 | 未修 | `api/config.py:27,32` |
| 4 | **P0** | `read/write_local_file` 基于 deny-list 边界。**真正风险是跨组 workspace、central DB、配置和 app 运行环境访问**。修复必须包含：(a) `Path.resolve()` + `Path.relative_to()`/等价语义，不能用字符串前缀；(b) read 仅允许当前群组根和显式授权的只读 skill 根，write 仅允许当前群组可写根；(c) read、existing write、non-existing write 的 symlink escape 测试；(d) 写入拒绝末级 symlink，并防止校验到打开之间的 symlink TOCTOU；(e) HIL 或 bypassPermissions 不得扩大根目录 | 未修 | `workspace_tools.py:1259,1270` |
| 5 | **P0** | AUTH_SECRET production fail-closed——`b45150e` 只加了 CRITICAL log，**diagnostic mitigation landed; vulnerability open**。需在 `NUKE_ENV=production` 下拒绝启动 | commit `b45150e` | `core/auth.py:12` |
| 6 | **P0/部署** | standalone production local shell 无隔离——`NUKE_ENV=production` + `NUKE_SHELL_EXEC_BACKEND=local`，bot shell 可访问 central DB 和所有群组 workspace。需明确标为 dev-only 或改为 container backend | 未修 | `docker-compose.standalone.yml:21,26` |
| 7 | **P1** | Supervisor IPC send/stop 无 timeout——一个僵死 Worker 阻塞整个系统 + 无 SIGKILL 升级 | 未修 | `supervisor.py:375-382, 427-436, 216-234` |
| 8 | **P1** | 仓库无 PR/merge CI 测试流水线，回归测试没有自动门禁 | 未修 | `.github/workflows/` |
| 9 | **P1** | docker.sock 暴露——socket-proxy 作为第一阶段收缩面（关闭无关 API），但 residual host-root 风险保持 open，第二阶段需 authorization plugin 或 rootless daemon | 未修 | `docker-compose.yml:47` |
| 10 | **Major** | WS 重连无退避 + token 在 URL query string | 未修 | `useWebSocket.js:23,55` |
| 11 | **Major** | 切群异步写回未验证 group/generation——`loadRecap/loadPersonalRecap/loadMore` 未校验响应归属 | 未修 | `ChatWindow.jsx` |

### CJK token 估算状态

`87f41ba` 已实现 CJK-aware 公式 `(total_chars + cjk_chars * 2) / 4`，方向正确，**但尚未校准验证**：
- 现有 109 tests 无纯中文、CJK/ASCII 混合、JSON/tool result 用例
- 0.75 token/CJK char 是否足够保守仍未证明
- 需用实际支持模型校准，压缩安全阈值应偏保守

**状态：实现已落地，尚未校准验证。退出 P0 但不能关闭。**

### 已确认 NOT AN ISSUE — 修复过程中不得触碰

| 原 ID | 问题 | 原因 |
|-------|------|------|
| TOP10 #6 / DB-C4 | `toggle_reaction` TOCTOU 竞态 | 三层架构串行化保护：Supervisor 单 owner 路由 → Worker 串行处理 → write_connect per-DB 锁。竞态不存在。 |
| AI-M9 | final response 第二次 LLM 调用 | `_stream_final` line 410 检查 `full_text`，正常路径已设置，只做分块广播零 LLM 调用。 |
| SEC-M6 | bypassPermissions 跳过 shell guard | 两层 hook 完全独立：bypass 只影响 permission decision，`_default_shell_guard` 作为独立 before-hook 始终执行。 |
| DB-C6 | `increment_unread` ON CONFLICT 列序 | SQLite UPSERT 匹配 unique constraint 不要求列序一致。 |

---

## 后端核心架构

### Critical

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| BE-C1 | 自研 JWT，无算法协商、无 audience/issuer/jti、7 天 TTL 无吊销机制 | `core/auth.py:49-84` | 迁移到 PyJWT；加 startup 密钥校验 |
| ~~BE-C2~~ **[降为 Major — readiness 写操作无必要但 8640 次小写入/天不构成发布阻断]** | `readiness()` 每次调用创建 health_check 表+写入 | `main.py:219-226` | 改为 read-only 或内存检查 |
| ~~BE-C3~~ **[降为 Minor — `group_id` 始终为 int（FastAPI path param 解析），无注入路径；仍应参数化]** | `workflow_store.load_all_active` SQL f-string | `core/workflow_store.py:47` | 参数化查询 |
| BE-C4 | `app_config.json` 明文 API Key、无 chmod 600、无原子写 | `config.py:25-26` | 原子写 + `os.chmod(0o600)` |

### Major

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| BE-M1 | 两个 `config.py` 命名冲突（`backend/config.py` vs `backend/core/config.py`） | — | 重命名一个（如 `app_keys.py`） |
| BE-M2 | `_row_to_member` 17 个位置索引 + `len(r) > N` 守卫 | `db/models.py:22-36` | 用 `aiosqlite.Row` |
| BE-M3 | `EventBus` 重复 `import threading` + 重复创建 Lock | `bus/engine.py:13-14, 57-58` | 删除重复 |
| BE-M4 | `runner._run_unit_body` 160 行 God Function | `core/runner.py:132-291` | 拆分 `_load_context` / `_execute_with_worktree` / `_cleanup_worktrees` |
| BE-M5 | 登录限流 dict 永远不清理——无限内存增长 | `api/auth.py:10` | TTL 淘汰或 LRU cache |
| BE-M6 | `delete_group` 对 central DB 执行部分 group-DB SQL；warning 后继续，central commit 后再 best-effort 删除 DB/workspace，且未清 Chroma | `api/groups.py:169-197` | 删除无效 SQL，并纳入可重试 purge saga/reconciliation |
| BE-M7 | `get_db()` / `global_db()` 命名误导——返回 context manager 而非连接 | `db/__init__.py:46-55` | 重命名 `get_db_connection()` |
| BE-M8 | `db/writer.py` 独立定义 `DB_PATH`——与 `db/__init__.py` 不同步风险 | `db/writer.py:28` | 从 `db/__init__.py` import |
| BE-M9 | 注册端点无密码强度校验 | `api/auth.py:29-44` | 至少拒绝空密码 |
| BE-M10 | `_sender_snapshot` 每次 save_message 开新 central DB 连接 | `db/queries.py:98-116` | 调用方传入 sender 数据 |

### Minor

| ID | 问题 | 位置 |
|----|------|------|
| BE-m1 | CORS origins 硬编码 localhost | `main.py:143-148` |
| BE-m2 | `core/auth.py` FastAPI import 埋在 108 行——混合框架无关和框架相关代码 | `core/auth.py:108` |
| BE-m3 | WS disconnect handler `except Exception: pass` 无日志 | `main.py:447-452` |
| BE-m4 | API 错误响应中英混杂无统一格式 | 多处 |
| BE-m5 | `_parse_json` 静默吞所有异常——掩盖数据损坏 | `db/models.py:7-18` |
| BE-m6 | readiness 探针访问 Supervisor 私有属性 `sup._workers` | `main.py:234-251` |
| BE-m7 | `build_context_message` 大量截断逻辑重复 | `core/role_router.py:51-111` |
| BE-m8 | `lifespan()` 步骤编号混乱（1, 2, 1b, 2, 2b, 3...） | `main.py:71-109` |
| BE-m9 | `_group_proxies` 全局 dict 无锁——依赖单线程假设 | `main.py:311` |
| BE-m10 | `core/media.py` 直接 import `SECRET_KEY`——密钥轮换会 break media 签名 | `core/media.py:16` |

---

## 运行时 / IPC / 并发层

### Critical

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| RT-C1 | Supervisor 上游 schema fanout 串行发送无超时 | `supervisor.py:375-382` | 加 `wait_for` + 超时 drop |
| RT-C2 | `send_to_worker` 无超时 | `supervisor.py:427-436` | 加 `wait_for` |
| RT-C3 | `stop()` 无 SIGKILL 升级——worker 拒死则 Supervisor 永久阻塞 | `supervisor.py:216-234` | `wait_for` + `proc.kill()` |
| RT-C4 | `reassign_group` CancelledError 路径更新路由缓存但旧 worker 未释放——split brain | `supervisor.py:488-666` | CancelledError 时不更新路由 |

### Major

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| RT-M1 | Worker 下游 pump 串行 await——慢 hydrate 阻塞所有控制帧 | `worker.py:90-98` | `create_task` + per-group queue |
| RT-M2 | `_run_process_loop` CancelledError 路径 terminate 但不 wait | `supervisor.py:189-192` | terminate + wait + kill |
| RT-M3 | 无 SIGTERM 信号处理——容器 SIGTERM 无人接管 | `entry.py:107-112` | 安装 signal handler |
| RT-M4 | Worker 重连时旧/新 handler 短暂并发处理帧 | `supervisor.py:281-320` | 显式 cancel 旧 handler task |
| RT-M5 | `setup_structured_logging` removeHandler 不 close——fd 泄露 | `tracing.py:57-77` | `h.close()` |
| RT-M6 | Bus wildcard queue 满载静默丢事件——UI 看到消息缺失 | `bus/engine.py:91-95` | 区分 critical/non-critical；critical 用 `await put` |
| RT-M7 | `reassign_group` 释放锁后使用 `old_wid`——可能发给错误 worker | `supervisor.py:509-519` | 在锁内完成 old_wid 使用 |
| RT-M8 | `LifecycleManager._lock` 在 hydrate 中 acquire 3 次——中间可被 evict 穿插 | `lifecycle.py` | 合并为单次 acquire |

### Minor

| ID | 问题 | 位置 |
|----|------|------|
| RT-m1 | 重复 `import threading` + 重复 Lock 创建 | `bus/engine.py:13-14, 57-58` |
| RT-m2 | `BaseFrame.__getattr__` 性能开销——pickle/copy 触发额外 dict 查找 | `protocol.py:84-89` |
| RT-m3 | `send_msg` 类型注解用 `any`（builtin）而非 `Any` | `framing.py:15` |
| RT-m4 | UDS socket 文件关闭后不清理 | `transport_unix.py:18-22` |
| RT-m5 | UDS socket 默认 755 权限——任何本地用户可连 | `transport_unix.py:22` |
| RT-m6 | `_repush_loop` 在 `close()` 期间仍运行 | `mcp_collector.py:169-175` |
| RT-m7 | `GroupLock.__del__` 依赖 GC 释放文件锁——不可靠 | `lifecycle.py:62-68` |
| RT-m8 | `_hydrate_assigned_groups` 重试次数可能不够（大 migration 场景） | `worker.py:354` |
| RT-m9 | `ws_manager.broadcast` 递归 presence broadcast 无深度保护 | `ws_manager.py:118-120` |
| RT-m10 | reassign 后路由缓存 TTL 3600s——外部 DB 变更被忽略 1 小时 | `supervisor.py:478, 522, 543, 665` |

---

## AI/LLM 集成层

### Critical

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| AI-C1 | 中文 token 估算偏差 4-6× **[状态：实现已落地 `87f41ba`，尚未校准验证——缺纯中文/混合/JSON 测试用例，0.75 token/CJK char 是否保守足够未证明。退出 P0 但不能关闭。]** | `executors/compact.py:134` | 补充 CJK-specific 测试 + 实际 tokenizer 校准 |
| AI-C2 | doom-loop 检测只抓完全相同连续调用——路径循环轻松绕过 | `tool_loop_v1.py:296-303` | 工具名 cycle 检测 |
| AI-C3 | session snapshot 每次 tool call 后序列化全消息历史写入 SQLite——长 session 超行大小 | `sessions/store.py:141-148` | 增量更新或限制 snapshot 大小 |

### Major

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| AI-M1 | `client.py` 832 行无 Provider 抽象——if/elif 面条 | `ai/client.py` | Provider Protocol + dispatch table |
| AI-M2 | context window 硬编码 11 个模型——Claude 200K 被当 64K | `compact.py:53-65` | 配置驱动或 family matching |
| AI-M3 | `model_limits.py` 和 `compact.py` 各自维护模型知识 | 两个文件 | 合并到一个 source of truth |
| AI-M4 | system prompt 每轮重建 2-3 次（workspace 文件重复读盘） | `tool_loop_v1.py:230-231` | 缓存 + 变更检测 |
| AI-M5 | system prompt 无注入防御——恶意 memory 可注入指令 | `prompt_builder.py:32-63` | 结构化 fencing + 输入清洗 |
| AI-M6 | ChromaDB 全局单例跨所有群组 | `ai/memory.py:56-86` | per-group collection 或 hard partition |
| AI-M7 | memory 写路径每轮 4 个并发 LLM 调用 | `memory_provider.py:90-96` | batch + idle 时处理 |
| AI-M8 | streaming retry 首 chunk 后失败无法撤回已发内容 | `client.py:634-661` | partial-content 信号机制 |
| ~~AI-M9~~ | ~~final response 是第二次完整 LLM 调用~~ **[NOT AN ISSUE — `_stream_final` line 410 检查 `full_text`，正常路径已设置，只做分块广播零 LLM 调用。line 422 LLM 调用仅空 full_text 时触发（不可达 fallback）。DO NOT FIX.]** | ~~`tool_loop_v1_helpers.py:409-435`~~ | ~~loop 末次响应直接流式输出~~ |
| AI-M10 **[改写 — 每次 tool call 后已有 session snapshot；真正风险是 100 次上限过高、快照过重和缺少用户确认，不是"无 checkpoint"]** | `max_iterations=100` 上限过高 + snapshot 过重 | `tool_loop_v1.py:389` | soft cap + 用户确认 + 优化 snapshot 大小 |
| AI-M11 | `_reflect_in_flight` guard 用 set 无锁——多 worker 竞态 | `ai/memory.py:726` | 跨 worker 协调或 per-group lock |
| AI-M12 | Ollama 静默丢弃 tool schema——bot 有工具但 LLM 不知道 | `client.py:685` | 传 tools 或 log warning |
| AI-M13 | `_AI_RETRY_MAX` 配置项定义但从未使用 | `client.py:81, 597` | 用配置值替换 hardcoded `3` |

### Minor

| ID | 问题 | 位置 |
|----|------|------|
| AI-m1 | Ollama 硬编码 `tools=None` | `client.py:685` |
| AI-m2 | `call_ai` (non-streaming) 仅 DeepSeek 的死代码 | `client.py:115-139` |
| AI-m3 | `consecutive_tool_only` 计数器初始化/重置但从未递增或读取 | `tool_loop_v1.py:82,280,329` |
| AI-m4 | 并行工具执行无 timeout | `tool_loop_v1_helpers.py:601` |
| AI-m5 | 工具路由逻辑在两个文件中重复 | `tool_loop_v1.py:27-32` / `tool_dispatch.py:44-71` |
| AI-m6 | memory prompt 硬编码中文 | `ai/memory.py:18-51` |
| AI-m7 | compact prompt 英文 vs 其他 prompt 中文 | `compact.py:468-520` |
| AI-m8 | QueryRewriter 仅中文精确匹配 | `ai/memory.py:429-464` |
| AI-m9 | `_token_cache` 用 `id(list)` 做 key——GC 后地址复用风险 | `compact.py:76-77` |
| AI-m10 | recovery reconstruct 可能产生孤儿 tool_result | `sessions/recovery.py:31-70` |
| AI-m11 | fake "thinking" preview 是纯 UI 剧场——浪费时间 | `tool_loop_v1.py:250-259` |

---

## 安全与安全层

### Critical

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| SEC-C1 **[状态修正：`b45150e` 已加 CRITICAL log；`is_operator` payload marker 经讨论保留为 test/dev 便利（DFT-082 有意设计）；AUTH_SECRET fail-closed 待补]** | JWT 默认密钥 | `core/auth.py:12` | `NUKE_ENV=production` 下拒绝默认密钥 |
| SEC-C2 | API-key `GET/PUT /api/config` 无 operator 校验 | `api/config.py:27,32` | 加 `require_operator`、审计和 operator bootstrap/endpoint-disable 策略 |
| SEC-C3 | Token 在 WebSocket URL query string 中传递 | `useWebSocket.js:23` | 改首条 WS 消息认证 |

### Major

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| SEC-M1 **[表述修正：主要是 app container 内跨组/控制面访问，不等同于直接写 host `/etc`]** | `write_local_file` / `read_local_file` 基于 deny-list，可访问其他群组 workspace、central DB、配置和 app 运行环境 | `workspace_tools.py:1259,1270` | 改为 symlink-safe 的 current-group allow-list |
| SEC-M2 | JWT 无 replay 保护、无 audience/issuer、7 天 TTL 无吊销 | `core/auth.py` | 加标准 JWT claims |
| SEC-M3 | 容器 sandbox host uid + bridge 网络 | `container_sandbox.py:55-56` | 默认 `--network=none` + 不同 uid |
| SEC-M4 | Shell guard 漏掉 `python3 -c` / `nc -e` / `crontab -r` | `workspace_tools.py:450-487` | 补充 blocked binaries |
| SEC-M5 | `app_config.json` 明文 + 0o644 权限 | `config.py:25-26` | chmod 600 + 考虑加密 |
| ~~SEC-M6~~ | ~~bypassPermissions 模式下 shell guard 被跳过~~ **[NOT AN ISSUE — 两层 hook 完全独立：bypass 只影响 permission decision，`_default_shell_guard` 作为独立 before-hook 始终执行。DO NOT FIX.]** | ~~`permissions/engine.py:194`~~ | ~~bypass 模式仍执行 shell guard~~ |

### Minor

| ID | 问题 | 位置 |
|----|------|------|
| SEC-m1 | MCP injection 检测可被 Unicode homoglyph 绕过 | `mcp_client.py:77-93` |
| SEC-m2 | Redaction 缺 Azure/HashiCorp/npm token 格式 | `redaction.py:27-62` |
| SEC-m3 | Permission routes 无跨组隔离校验 | `permissions/routes.py:26-58` |
| SEC-m4 | `once` grants 无上限——累积风险 | `permissions/engine.py:35` |
| SEC-m5 | `_check_shell_command_paths` 只查 3 个根路径前缀 | `workspace_tools.py:1005` |
| SEC-m6 | PBKDF2 100K iterations 略低于 OWASP 2024 建议 | `core/auth.py:36` |

---

## 前端架构

### Critical

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| ~~FE-C1~~ **[降为 Major — 组件过长是维护性问题；真正竞态在 `loadRecap/loadPersonalRecap/loadMore` 未验证响应归属；核心 state write 已有 `active` flag 保护]** | ChatWindow 828 行 + 切群异步写回 | `ChatWindow.jsx` | 先修 group/generation scoping，再拆 hooks |
| ~~FE-C2~~ **[降为 Major — "DDoS"表述过度；应加 backoff+jitter]** | WS 重连无退避 | `useWebSocket.js:55` | 指数退避 + jitter |
| FE-C3 | JWT Token 在 URL query string 传递 | `useWebSocket.js:23` | 改 WS 首消息认证 |
| FE-C4 | 全局 `window.fetch` monkey-patch + authFetch + 手动 header 三套认证 | `main.jsx:12-25` | 统一走 authFetch |

### Major

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| FE-M1 | MemberList 825 行单体 | `MemberList.jsx` | 拆分 5+ 子组件 |
| FE-M2 | 流式 chunk 每次创建新 messages 数组引用——10-50 次/秒全 MessageList 重渲染 | `store/chatStore.js:91-95` | streaming content 单独 store |
| FE-M3 | `onAuthError` ref 未进依赖数组 | `useWebSocket.js:17` | 加入 deps |
| FE-M4 | 切群 fetch 竞态 | `ChatWindow.jsx:247-329` | AbortController |
| FE-M5 | `BotLogPanel` polling interval 每次 state 变更被清除重建 | `BotLogPanel.jsx:86-102` | 用 ref 代替 state 依赖 |
| FE-M6 | `mdComponents` i18n 变更时重建所有 markdown 组件 | `MessageBubble.jsx:156-233` | 拆分 memo 依赖 |
| FE-M7 | `INJECTED_LABEL` 用 `"null"` 字符串做 key | `SkillPanel.jsx:17-18` | 显式处理 undefined |
| FE-M8 | `WorkspacePanel` tree-loading 逻辑重复 | `WorkspacePanel.jsx:20-51` | 提取为函数 |
| FE-M9 | `mdComponentsPlaceholder` 死代码 | `MessageBubble.jsx:97` | 删除 |
| FE-M10 | `isStreaming` 每次 render 扫描全 messages 数组 | `ChatWindow.jsx:396` | 派生 store 值 |

### Minor

| ID | 问题 | 位置 |
|----|------|------|
| FE-m1 | `App.jsx` 残留 join-group 屏幕（实际由 ChatWindow 自动加入） | `App.jsx:12-15` |
| FE-m2 | `saveAnnouncement` fire-and-forget 无错误反馈 | `ChatWindow.jsx:406-412` |
| FE-m3 | Theme class list 硬编码两处 | `App.jsx:27` |
| FE-m4 | `personalRecapAt` ref 永不清理——群组删除后内存泄露 | `ChatWindow.jsx:118-121` |
| FE-m5 | 无限滚动阈值硬编码 80px | `ChatWindow.jsx:352` |
| FE-m6 | `BotLogPanel` `formatTime` 硬编码 `zh-CN` | `BotLogPanel.jsx:332` |
| FE-m7 | Emoji 按钮无 aria-label | 多处 |
| FE-m8 | Modal 无 focus trap / Escape 处理 | 多处 |
| FE-m9 | `loadMore` 闭包竞态——可 double-fire | `ChatWindow.jsx:331-349` |
| FE-m10 | `useNotifications` 首 render 就请求权限 | `useNotifications.js:5-7` |

---

## 数据库 / 数据层

### Critical

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| DB-C1 **[降级/描述修正 — 不是跨库双写；sender snapshot 已冗余到 message row]** | `save_message` 每次 open 新 central 连接；lookup 异常被吞后 snapshot 可为 NULL | `queries.py:98-140` | 调用方传已解析 snapshot 或建立 group-local member projection |
| DB-C2 | `clear_bot_context` 三阶段无回滚；`delete_group` 也会在 central commit 后部分清理 DB/workspace 且漏 Chroma | `queries.py:148-186`, `api/groups.py:169-197` | 带 operation ID 的 purge saga、幂等重试和 reconciliation |
| DB-C3 **[描述修正 — 两步操作在同一 DB，不是 split-DB 问题；去掉中间 commit 即可]** | `save_compaction_summary` 中间 commit 导致崩溃后重复 summary | `queries.py:229-254` | 合并到单事务（去掉 line 238 的中间 commit） |
| ~~DB-C4~~ | ~~`toggle_reaction` TOCTOU 竞态~~ **[NOT AN ISSUE — 架构串行化保护。DO NOT FIX.]** | ~~`queries.py:266-282`~~ | ~~`INSERT OR IGNORE`~~ |
| DB-C5 | migration_015 `DEFAULT 'w0'` vs schema `DEFAULT NULL`——legacy 群组涌向 worker 0 | `migrations.py:365` | follow-up migration NULL out |
| ~~DB-C6~~ | ~~`increment_unread` ON CONFLICT 列序与 PK 定义不一致~~ **[NOT AN ISSUE — SQLite UPSERT 匹配 unique constraint 不要求列序一致。DO NOT FIX.]** | ~~`queries.py:349-356`~~ | ~~对齐列序~~ |

### Major

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| ~~DB-M1~~ **[降为 Minor — 产品约束每组仅 1-2 名人类，N 很小；仍可合并事务]** | `bump_unread_for_group` N+1 写入 | `queries.py:377-389` | executemany |
| DB-M2 | `get_messages` 不过滤 `is_deleted`——软删消息仍返回 | `queries.py:46-72` | 加 `WHERE is_deleted = 0` |
| DB-M3 | `get_all_messages` 无 LIMIT——全表扫描 | `queries.py:75-81` | 加 LIMIT |
| DB-M4 | `delete_bot_memory` 只删 ChromaDB——SQLite artifacts 残留 | `queries.py:224` | 同步清理 role_summaries |
| DB-M5 | 缺多个高频查询索引 | 多处 | 加 `is_deleted` / `reply_to_id` / `group_id+type` 索引 |
| DB-M6 | 无 `ON DELETE CASCADE`——孤儿行累积 | `schema.py` / `schema_split.py` | 加 CASCADE 或应用层清理 |
| DB-M7 | 读连接无池——每查询 spawn 新 aiosqlite 线程 | `db/__init__.py:10-29` | 连接池 |
| DB-M8 | 缺 `synchronous=NORMAL` / `cache_size` / `temp_store` PRAGMAs | `db/__init__.py:24-26` | 补充 PRAGMAs |
| DB-M9 | `_sender_snapshot` 吞所有异常返回 NULL | `queries.py:98-116` | 至少 `log.exception` |
| DB-M10 | `soft_delete_message` / `pin_message` 不验证 group 归属 | `queries.py:224, 310` | 加 group_id 校验 |

### Minor

| ID | 问题 | 位置 |
|----|------|------|
| DB-m1 | `_row_to_msg` 25 个位置索引——列变更即全错 | `queries.py:57-90` |
| DB-m2 | migration_013 bare `except: pass` 吞所有错误 | `migrations.py:323-325` |
| DB-m3 | `sessions/store.py` 每查询设 row_factory | `store.py` |
| DB-m4 | `get_session` 每次 cross-DB 读 bot 显示字段 | `store.py:62-74` |
| DB-m5 | `ensure_group_db_ready` 两遍连接 | `schema_split.py:400-412` |
| DB-m6 | `_ready_group_dbs` 缓存永不失效 | `schema_split.py:397` |
| DB-m7 | `role_templates.name` 非 UNIQUE | `schema.py:178-185` |
| DB-m8 | `messages.created_at` UTC 无时区标记——消费端字符串 hack | `schema.py:126` |
| DB-m9 | `message_embeddings` 表疑为死代码 | `schema_split.py:210-213` |
| DB-m10 | `connect_sync` 无 row_factory | `db/__init__.py:32-43` |

---

## 测试与部署质量

### Critical

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| TD-C1 | 无 CI 测试流水线 | `.github/workflows/` | 加 pytest workflow |
| TD-C2 | docker.sock 无 socket-proxy | `docker-compose.yml:47` | 加 proxy sidecar |
| TD-C3 | 测试文件模块级 `DB_PATH` 全局变量互相覆盖 | `test_workflow.py:13-17` | pytest fixtures + `tmp_path` |
| TD-C4 | `test_memory.py` setUp 删除共享 DB 文件 | `test_memory.py:24-26` | 独立 DB 路径 |
| TD-C5 | 40+ 测试文件 `sys.path.insert` 在模块级 | 多处 | `conftest.py` 或 `pyproject.toml` `pythonpath` |

### Major

| ID | 问题 | 位置 | 修复建议 |
|----|------|------|----------|
| TD-M1 | 无 WS 集成测试（真实连接端到端） | — | 加 integration test |
| TD-M2 | 无 MCP HIL 全流程测试 | — | 加 e2e approval test |
| TD-M3 | 无 seed 脚本测试 | `Dockerfile:83` | 加 fixture test |
| TD-M4 | 无并发 group DB 访问测试 | — | 加并发写入 test |
| TD-M5 | `asyncio.sleep(0)` 当同步原语——固有竞态 | 多处 | `asyncio.Event` |
| TD-M6 | Dockerfile / compose 无 HEALTHCHECK | `Dockerfile` | 加 HEALTHCHECK 指令 |
| TD-M7 | systemd unit 无 WatchdogSec | `deploy/nuke-collaborator.service` | 加 WatchdogSec |
| TD-M8 | sandbox Python 3.11 vs app Python 3.13 | `deploy/sandbox/Dockerfile:17` | 对齐到 3.13 |
| TD-M9 | `:latest` tag 可变 + `pull_policy: always` | `publish-images.yml:64` | 不可变 tag |
| TD-M10 | seed 失败静默吞掉——app 带坏 DB 启动 | `Dockerfile:83` | 失败时 log stderr + 非零退出 |
| TD-M11 | 无 image 漏洞扫描 | CI workflow | 加 Trivy |
| TD-M12 | 无 rollback 策略文档 | deploy docs | 文档化 |

### Minor

| ID | 问题 | 位置 |
|----|------|------|
| TD-m1 | `NUKE_WORKERS` 各 compose 文件不一致 | `docker-compose*.yml` |
| TD-m2 | 无日志轮转配置 | `docker-compose*.yml` |
| TD-m3 | 无 `stop_grace_period` | `docker-compose*.yml` |
| TD-m4 | 测试断言耦合中文错误消息精确措辞 | 多处 |

---

## Codex 复核 Comments（2026-07-13）

> 复核基线：`87f41ba`。原问题清单保留不动；本节用于讨论严重级别、事实准确性和修复顺序。

### 复核后的发布阻断项

| 优先级 | 问题 | 复核意见 |
|---|---|---|
| P0 | 缺少 user↔group 授权 | 原 review 只在 permission route 等局部提及，实际是覆盖 HTTP、WS、workspace、成员身份的系统性缺口，且直接违反 Groups fully isolated |
| P0 | Chroma fact ID 跨组冲突 | 原 review 只指出逻辑隔离；实际 `message_id_idx` 会因 per-group message ID 重复而跨组覆盖 |
| P0 | 生产使用默认 `AUTH_SECRET` | `b45150e` 只记录 CRITICAL 日志，未消除可伪造 token 的风险；生产环境必须 fail closed |
| P0 | `/api/config` 非 operator 可写 | 确认成立；建议 GET/PUT 都要求 operator，并记录审计日志 |
| P0 | host/local file 边界为 deny-list | `read_local_file`/`write_local_file` 必须始终限制在当前群组允许根目录，不能由 HIL 或 bypass 模式放宽 |
| P0/P1 | Docker socket 暴露 | 风险成立，但简单 socket-proxy 若仍允许任意 container create + bind mount，依然可能获得 host root；需要专用 runner/policy boundary 或 rootless 独立 daemon |
| P1 | 无 PR CI | 确认成立，应至少运行分层后的 unit/integration 安全门禁 |
| P1 | IPC send/stop 无 timeout | 确认成立，应补 `wait_for`、连接淘汰以及 terminate→wait→kill 升级 |

### 对刚刚两个修复的 Comment

#### JWT 默认密钥（`b45150e`）

**状态：未修复。**日志能提升可观测性，但攻击面仍完整存在，而且 production compose 没有设置 `AUTH_SECRET`。建议：

- `NUKE_ENV=production` 下密钥缺失、等于默认值或强度不足时拒绝启动。
- 开发环境可生成进程级临时密钥并明确提示 token 会在重启后失效。
- `is_operator` 应由服务端用户/角色数据或受控 allow-list决定；测试 payload marker 不应成为生产可用授权来源。
- 是否迁移 PyJWT 是次要决策，不能替代密钥管理和服务端授权。

#### CJK token 估算（`87f41ba`）

**状态：方向正确，但验证不足。**`tests/test_compact.py` 当前 109 项通过，但没有针对纯中文、CJK/ASCII 混合、JSON/tool result 和不同模型 tokenizer 的直接测试。当前约 `0.75 token/CJK char` 仍需用实际支持模型校准；压缩安全阈值应偏保守，宁可略早触发，不能继续低估。

### 需要撤销或降级的评语

| 原 ID | 复核结论 | 原因 |
|---|---|---|
| AI-M9 / A3 | 撤销并改写 | 正常 tool loop 的最终 text 已由 loop 内最后一次调用产生，`_stream_final` 只是广播；额外调用主要是 reviewer 特殊路径 |
| SEC-M6 | 撤销 | `bypassPermissions` 只影响 permission decision；独立 `_default_shell_guard` 仍作为后续 before-hook 执行 |
| DB-C6 | 撤销 | SQLite 能用 `ON CONFLICT(group_id, member_id)` 命中 `PRIMARY KEY(member_id, group_id)`；已用当前 SQLite 验证 |
| BE-C3 | 降为代码质量/防御性修复 | `group_id` 参数是 int，当前没有成立的外部字符串注入路径；仍应参数化 |
| BE-C2 | 降为 Minor/Major | readiness 写操作没有必要，但 8640 次小写入/天本身不足以构成发布阻断 |
| FE-C1 | 降为 Major | 组件过长是维护性问题；真正需优先修的是切群请求竞态和取消机制 |
| FE-C2 | 降为 Major | 固定 3 秒会造成集中重连，应加 backoff+jitter，但“DDoS”表述过度 |
| DB-M1 / TOP10-10 | 降为 Minor | 产品约束每组仅 1–2 名人类，当前 unread fanout 的 N 很小；仍可合并成一次事务 |
| AI-M10 | 改写 | 每次 tool call 后已有 session snapshot；真正风险是 100 次上限过高、快照过重和缺少用户确认，不是“无 checkpoint” |

### 已确认但修复方案需调整

#### `toggle_reaction` 竞态

问题成立，但 `INSERT OR IGNORE + conditional DELETE` 仍不能给两个并发 toggle 定义稳定语义。建议把 API 改成幂等的 `PUT reaction` 和 `DELETE reaction`；若必须保留 toggle，则使用 `BEGIN IMMEDIATE` 串行化整个读改写事务。

#### IPC at-least-once

需要 message ID、ACK、持久 pending、幂等 claim、结果去重一起设计。仅在 Worker 重连时重发会重复调用模型及重复执行工具副作用。

#### Docker socket proxy

代理只有在它能校验镜像、mount、network、privileged、capabilities、user 和资源限制时才形成安全边界。允许通用 Docker create API 的普通 socket-proxy 不能充分阻止 host takeover。

#### WebSocket token

从 URL query 移除是正确方向。首帧认证需要未认证连接超时和在认证前禁止业务帧；更完整的方案是 HttpOnly/SameSite cookie 或一次性短期 WS ticket。无论采用哪种传输方式，都必须同时绑定 user、group 和 member 身份。

### Review 本身的改进建议

1. 两份文档使用同一 issue ID，架构文档描述 decision，技术文档只链接它，避免 A2/AI-M7、A6/AI-M6 等重复维护。
2. 每项增加 `status`、`evidence/repro`、`impact`、`exploitability`、`owner`、`acceptance test`，不要只凭代码行数或理论最大值定 Critical。
3. 区分“发布阻断”“近期可靠性”“性能债务”“维护性重构”；God Component、命名和重复 import 不应与密钥伪造放在同一级别。
4. 对成本和性能问题先加指标再给固定倍数结论，例如 memory LLM 调用次数、token、费用、失败率和队列 backlog。
5. 每次 review 标注 commit SHA；代码修复后更新状态，避免已修、部分修复和错误结论继续留在 TOP 10。

### 建议执行顺序

1. 群组授权模型、Chroma ID 冲突、生产密钥、config operator 权限。
2. workspace/local tool 强制 confinement、生产 sandbox/docker 边界。
3. PR CI 与上述 P0 的回归测试。
4. IPC timeout、shutdown kill escalation、消息交付/幂等协议。
5. DB 单事务修复、reaction 幂等化、软删过滤。
6. WS auth transport、重连和前端请求竞态。
7. 最后处理组件拆分、命名、重复代码和一般性能优化。

---

## 原 Review 作者回应（2026-07-13）

> 以下基于 6 个独立验证 agent 逐条对照代码后的结论。基线 commit: `87f41ba`。

### 接受 Codex 修正的条目

| 原 ID | Codex 结论 | 代码验证 | 我的回应 |
|-------|-----------|---------|---------|
| **AI-M9** | 撤销：正常路径 `_stream_final` 只做广播 | ✅ **确认。** `tool_loop_v1_helpers.py:410` 检查 `full_text`——正常路径 line 282 已设置 `full_text`，所以 line 411-419 只做分块广播 + `return`，**零 LLM 调用**。line 422 的 LLM 调用仅在 `full_text` 为空时触发（防御性 fallback），实际不可达 | **接受撤销。** 我的原结论"token 成本翻倍"是错误的。同步撤销架构文档 A3。 |
| **SEC-M6** | 撤销：bypassPermissions 不跳过 shell guard | ✅ **确认。** Hook 循环（`tool_executor.py:212-223`）是 "first block wins"。`_permission_check_hook` 在 bypass 下返回 `None`（非 block），循环继续到 `_default_shell_guard`——它独立执行 `_check_shell_command()`。两层完全独立 | **接受撤销。** 我的原结论错误。 |
| **DB-C6** | 撤销：SQLite ON CONFLICT 列序无关 | ✅ **确认。** SQLite UPSERT 匹配 unique constraint 不要求列序一致 | **接受撤销。** |
| **BE-C3** | 降为代码质量/防御性修复 | ✅ 当前实际入口由 FastAPI 将 path parameter 解析为 `int`，其他调用方也只传 DB/内部整数；type hint 本身不提供运行时保证 | **接受降级为 Minor。** 仍应参数化（防御性编程），但不是 Critical。 |
| **BE-C2** | 降为 Minor/Major | ✅ 同意。readiness 写操作无必要但不不构成发布阻断 | **接受降级为 Major。** |
| **A1 分类** | 三类问题混在一起 | ✅ **确认。** `save_message` 是读 central + 写 group（单写非双写）；`save_compaction_summary` 两步在同一 DB（去掉中间 commit 即可）；只有 `clear_bot_context` 和 `delete_group` 是真正跨存储问题 | **接受重写。** 同步更新架构文档 A1。 |
| **toggle_reaction 修复方案** | ~~`INSERT OR IGNORE + DELETE` 不够，建议 `BEGIN IMMEDIATE`~~ | 后续调用链核验确认 Supervisor single-owner + Worker 串行处理 + per-DB lock 已串行化当前 toggle | **[SUPERSEDED] 不属于已证实问题，从发布阻断移除。** |
| **AUTH_SECRET fix 不足** | 只 log 不够，生产必须 fail closed | ⚠️ 部分同意。`b45150e` 作为第一步有价值，但 Codex 的 `NUKE_ENV=production` gate 方案更完整 | **接受补完。** 后续 commit 加 production fail-closed。 |
| **CJK token 验证不足** | 需要 CJK-specific 测试 | ✅ 同意。当前 109 tests 无纯中文用例 | **接受。** 后续补充 CJK 专项测试。 |

### 接受为新增 P0 的条目

| 条目 | 代码验证 | 我的回应 |
|------|---------|---------|
| **Chroma fact ID 冲突** | ✅ **确认是确定性 bug。** `memory.py:496`: `fact_id = f"{message_id}_{idx}"` 不含 `group_id`/`bot_id`。per-group DB 的 message_id 从 1 自增，不同群组会产生相同 ID。`upsert` 静默覆盖。对比 reflection（line 888）和 tool episode（`tool_events.py:380`）已正确包含 `bot_id + group_id` | **接受为 P0。** 修复方案：`f"fact_{bot_id}_{group_id}_{message_id}_{idx}"`。需处理 legacy 数据迁移。 |
| **群组授权缺失（AC1）** | ✅ 确认：所有 HTTP/WS 路由只检查“已登录”，不检查 caller 属于目标群组；`members.user_id` 始终 NULL。DFT-082 记录了历史 trusted-internal 取舍和一次错误的直接校验实现 | **最终对齐：当前项目章程要求 Groups fully isolated，因此 AC1 是正式多用户 production release gate。应设计正式 membership/role 模型，不能重复直接比较现有 NULL `members.user_id` 的失败方案。** |

### 部分同意的条目

| 条目 | 我的判断 |
|------|---------|
| **A2 memory 调用次数** | Codex 说"4 次/轮"不准确——阈值触发 + reflection 多线程。**成立**，实际次数波动大。但成本风险的定性结论不变。采纳：先加指标统计再定策略。 |
| **FE-C1 降级** | **最终对齐：**828 行组件增加了定位和修复成本，但不是竞态的直接根因。真实问题是 `loadRecap/loadPersonalRecap/loadMore` 等异步完成回调缺少 group/generation 约束；部分请求走 WS RPC，也不能统一用 AbortController。correctness fix 与 hooks 拆分可放同一工作包，但验收标准分开。 |
| **FE-C2 降级** | "DDoS"表述确实过度。**接受降级为 Major**，backoff+jitter 修复方向不变。 |
| **DB-M1/TOP10-10 降级** | 产品约束 1-2 人类/组，N 很小。**接受降级为 Minor**，仍可合并事务。 |
| **AI-M10 改写** | 每次 tool call 后已有 snapshot。**接受改写**——真正风险是上限过高 + snapshot 过重 + 无用户确认。 |
| **Docker socket proxy 不够** | 技术上正确——普通 proxy 若允许任意 create + bind mount 仍不安全。但渐进改善有价值，不应因此否定中间步骤。 |

### ~~修正后的 TOP 10 排序~~ [SUPERSEDED — 见文档顶部"最终优先级表"]

~~基于 Codex 复核 + 代码验证，更新发布阻断优先级：~~

~~| # | 问题 | 状态 |~~
~~|---|------|------|~~
~~| 1 | Chroma fact ID 跨组覆盖（新增 P0） | 未修 |~~
~~| 2 | `PUT /api/config` 无 operator 校验 | 未修 |~~
~~| 3 | `read/write_local_file` deny-list 边界 | 未修 |~~
~~| 4 | 生产 AUTH_SECRET fail-closed（`b45150e` 只 log） | 部分修复 |~~
~~| 5 | Supervisor IPC send/stop 无 timeout | 未修 |~~
~~| 6 | 无 CI 测试流水线 | 未修 |~~
~~| 7 | `toggle_reaction` 竞态（改用 `BEGIN IMMEDIATE`） | 未修 |~~  ← 第二轮已证伪，toggle_reaction 不是 issue
~~| 8 | WS 重连无退避 + token 在 URL | 未修 |~~
~~| 9 | docker.sock 暴露（需 policy boundary，非简单 proxy） | 未修 |~~
~~| 10 | 切群 fetch 竞态 + AbortController | 未修 |~~

---

## Codex 第二轮复核（2026-07-13）

> 本轮重点核验争议项本身是否成立，以及建议方案是否真正关闭风险。结论中包含对 Codex 上一轮意见的修正。

### 7. A2 memory 调用次数

**结论：同意另一位架构师的判断，但“不可持续”仍需指标证明。**

每个长度不少于 8 的 bot 最终回复几乎固定触发 1 次 fact extraction；conflict、summary、reflection、tool compression 都是条件触发，其中 reflection 可能按多个 thread 调用。成本风险定性成立，固定“4 次/轮”不成立。

指标不能只在 memory provider 外围做计数：这些 pipeline 直接调用 `call_ai_once()`，没有进入主执行路径的 `AIService` token/cost 累积。应给 AI 调用增加 `purpose/pipeline` 标签，并同时统计 logical call、retry 后的 provider request、token、费用、延迟、失败率和 backlog。

### 8. AUTH_SECRET fix 不足

**结论：双方观点可以同时成立，但状态不能写成“安全问题部分修复”。**

`b45150e` 对运维可见性有价值，是合理的第一步；但它没有降低伪造 token 的可利用性，生产安全缺口仍是完全开放状态。建议状态写为：**diagnostic mitigation landed; vulnerability open**。

`NUKE_ENV=production` gate 在本项目可行，因为三个生产 compose 和 systemd unit 都已经设置该变量。建议在 FastAPI lifespan、日志初始化之后调用统一校验：默认值、缺失值或强度不足时拒绝启动；compose 同时使用 required variable/secret file，避免部署到启动阶段才发现。补 production 拒绝启动与 dev 行为的测试。

### 9. BE-C3 严重度

**结论：降为 Minor 正确，但“Python 类型保证”这个理由不准确。**

Python type hint 不提供运行时保证。当前没有注入路径，是因为实际入口中的 FastAPI path parameter 会解析为 int，其他调用方也只传 DB/内部整数。参数化仍应修复，同时把条件改成 `group_id is not None`，但这是低风险防御性改动。

### 10. toggle_reaction 竞态与修复方案

**结论：当前架构下没有已证实的并发 TOCTOU；`BEGIN IMMEDIATE` 不应进入 TOP 10。这里修正 Codex 上一轮建议。**

实际调用链为：

1. Supervisor 将同一群组的 MUTATE 路由到唯一 owner Worker。
2. `Worker.run()` 串行读取并 `await _handle()`，不会并发处理两条下游 frame。
3. `_run_mutate()` 又使用 `db.write_connect()`；该连接对每个 DB 有独立 `asyncio.Lock`，整个 get-meta → SELECT → DELETE/INSERT → readback 都在锁内。

所以在“一个群组同一时刻只有一个 Worker owner”的架构不变量成立时，两次 reaction toggle 已被串行化，不会触发原 review 描述的并发 IntegrityError。若 handoff split-brain 让两个进程同时写同一群库，`BEGIN IMMEDIATE` 可以提供数据库级防御，但那首先是 lease/handoff correctness 问题，不能据此把 reaction 列为发布阻断。

长期若要支持消息重放或 at-least-once，仍建议把 toggle 改成幂等的 PUT/DELETE；这是为了重复交付语义，而不是修复当前已存在的进程内竞态。

### 11. FE-C1 与切群竞态

**结论：保留 Major 合理，但原 review 的事实描述和“根因”判断都需要改写。**

- 主切群 effect 已使用 `let active = true`，cleanup 后置 false；pins、group info、reactions、messages、workflow 的核心 state write 都检查了 `active`。所以“7 个 fetch 无保护，快速切群必然覆盖数据”不符合当前代码。
- pins/reactions/messages 实际经 WebSocket RPC，不是 fetch，不能直接加 AbortController；需要给 `wsrpc.request()` 增加 cancel/generation 语义。
- 确认存在的竞态是：`loadRecap()`、`loadPersonalRecap()` 没有验证响应仍属于 active group；`loadMore()` 在切群后可能把旧群消息 append 到新群；reconnect catch-up 也缺少 response-time group 校验。

828 行组件提高了定位和修复这些问题的成本，拆成 group-data/recap/pagination/reconnect hooks 是合理的工程措施；但它不是竞态的直接根因。直接根因是“全局 current-group state + 未按 group/generation 约束的异步完成回调”。应把 correctness fix 与组件拆分放在同一工作包，但验收标准要分开：先证明旧群响应不能写入当前群，再验收模块边界。

### 12. Docker socket proxy

**结论：socket-proxy 是有价值的渐进措施，但不能关闭当前 review 所描述的 host-root 风险。**

Tecnativa 官方文档显示它主要按 Docker API section 和全局 POST 开关授权。当前应用至少需要 container create、inspect、exec、stop，即必须开放 `CONTAINERS`、`EXEC` 和 POST。该粒度不能校验 create body 中的 bind source、privileged、capabilities 或 image；而任意 container create + host bind mount 本身仍可修改 host 文件。因此：

- 作为第一阶段，proxy 能关闭 swarm、secrets、build、plugins 等无关 API，应该做。
- 但若威胁模型是“app container 被攻破”，攻击者仍能利用被允许的 container-create API 挂载 host 路径，所以不能把该 issue 标记为 resolved。
- 第二阶段需要能验证 create 参数的 broker/authorization plugin，或使用隔离的 rootless daemon。proxy 端口必须只在 app+proxy 私有网络可见，并固定镜像版本。

官方依据：[Tecnativa docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)、[Docker bind mount security](https://docs.docker.com/engine/storage/bind-mounts/)、[Docker daemon socket security](https://docs.docker.com/engine/security/protect-access/)。

### 新增部署发现：standalone production 绕过群组 shell 隔离

`docker-compose.standalone.yml` 设置了 `NUKE_ENV=production`，却同时设置 `NUKE_SHELL_EXEC_BACKEND=local`。该模式没有 docker.sock 风险，但 bot shell 直接运行在 app container；app 又挂载包含 central DB 和全部群组 workspace 的同一个 named volume。`cwd` confinement 不能阻止 shell 命令访问绝对路径，因此这条“简易生产部署”不满足项目的群组隔离承诺。

建议二选一：明确标成 trusted single-user/dev-only 并禁止多群组生产使用，或改为具备真实 per-group sandbox 的部署方式。这个问题比 standalone 模式是否使用 socket-proxy更直接。

### 本轮调整后的相关优先级

1. AUTH_SECRET production fail-closed：保持 P0，`b45150e` 记为可观测性前置工作。
2. standalone production local shell：新增 P0/部署阻断，除非明确降级为 dev-only。
3. docker.sock：保持 P0/P1；先上 proxy 收缩面，但 residual host-root 风险保持 open。
4. FE 切群异步写回：Major；先修 generation/group scoping，再拆 hooks。
5. memory cost：先完成可归因的 token/cost/backlog 指标，再决定架构策略。
6. BE-C3：Minor。
7. toggle_reaction：从发布阻断移除；仅在破坏 single-owner 不变量的并发测试可复现后再加 DB 事务防御。

---

## 原 Review 作者第二轮回应（2026-07-13）

> 基于 2 个独立验证 agent 逐条对照代码后的结论。

### toggle_reaction：接受移除——竞态不存在

**代码验证结论：Codex 正确。原 review 描述的并发 TOCTOU 在实际架构中不会发生。**

三层串行化保护：

1. **Supervisor 单 owner 路由**（`supervisor.py`）：同一群组的 MUTATE 帧始终路由到同一个 owner Worker，不会并发分发到多个 Worker。
2. **Worker 串行处理**（`worker.py:90-98`）：`recv_msg` → `await _handle(msg)` 是严格串行的，不会并发处理两条下游 frame。
3. **write_connect per-DB 锁**（`writer.py`）：`db.write_connect()` 对每个 DB path 有独立 `asyncio.Lock`，整个 SELECT → DELETE/INSERT 序列都在锁内。

**我的原结论"并发点赞会 IntegrityError"是错误的。** 在 "一个群组同一时刻只有一个 Worker owner" 的架构不变量成立时，`toggle_reaction` 已被充分串行化。从 TOP 10 和发布阻断中移除。

仅在 handoff split-brain（两个进程同时写同一群库）场景下才有风险，但那首先是 lease/handoff 正确性问题。

### standalone production local shell：接受为新增部署阻断

**代码验证结论：Codex 正确。**

`docker-compose.standalone.yml` 同时设置：
- Line 21: `NUKE_ENV: production`
- Line 26: `NUKE_SHELL_EXEC_BACKEND: local`

`LocalShellBackend`（`workspace_tools.py:1088-1135`）直接用 `asyncio.create_subprocess_exec()` 在 app 进程内运行 shell，**无 chroot、无 user namespace、无文件系统沙箱**。唯一的 confinement 是 `cwd`（起始目录），但绝对路径可以逃逸。

所有群组 workspace + central DB 都在同一个 named volume 下（`/var/lib/nuke-ai-collaborator/`），regex guard（line 1005）只查 `/Users/`、`/home/`、`~`——**不查 `/var/lib/`**。任何群组的 bot 可以 `cat /var/lib/nuke-ai-collaborator/chat.db` 读取 central DB。

**但有一个重要上下文**：standalone 模式的文件头注释（lines 3-4）明确说 "No host directories, no sudo mkdir, no docker.sock mount, no separate sandbox image needed"，`LocalShellBackend` 代码注释也标注 "NO cross-group isolation"。这是一个**有意的简化部署模式**，设计意图是避免 Docker-in-Docker 复杂性。

**我的立场**：Codex 建议 "明确标成 dev-only 或改为真实 sandbox" 是正确的方向。但不应作为代码 bug 修复——这是一个**部署文档和配置问题**。建议：(1) standalone compose 去掉 `NUKE_ENV=production` 或加醒目 warning；(2) README 明确标注 standalone 不支持多群组隔离。

### AUTH_SECRET 状态描述：接受修正

Codex 建议状态写为 **"diagnostic mitigation landed; vulnerability open"** 比我写的 "部分修复" 更精确。接受。`b45150e` 提升了可观测性但未降低可利用性。

### BE-C3 理由修正：接受

Codex 指出 "Python type hint 不提供运行时保证"——正确。当前没有注入路径的真正原因是 FastAPI path parameter 解析为 int + 所有调用方只传 DB/内部整数。接受修正理由，结论（Minor）不变。

### FE-C1 事实描述：接受修正

Codex 指出核心 state write 已有 `active` flag 保护，部分调用走 WS RPC（非 fetch），真正的竞态在 `loadRecap/loadPersonalRecap/loadMore` 未验证响应归属。接受修正事实描述，Major 定级不变。

### Docker socket proxy 两阶段方案：接受

Codex 的 Tecnativa 文档分析准确——proxy 能关闭无关 API 但不能校验 create body 参数。接受两阶段方案：先上 proxy 收缩面，后续需 authorization plugin 或 rootless daemon。

### ~~第二轮修正后的 TOP 10~~ [SUPERSEDED — 见文档顶部“最终优先级表”]

> 该表保留第二轮讨论快照，但遗漏了最终确认为 P0 架构决策的 AC1，不能作为执行依据。

| # | 问题 | 状态 | 变化 |
|---|------|------|------|
| 1 | Chroma fact ID 跨组覆盖 | 未修 | — |
| 2 | `PUT /api/config` 无 operator 校验 | 未修 | — |
| 3 | `read/write_local_file` deny-list 边界 | 未修 | — |
| 4 | AUTH_SECRET production fail-closed | diagnostic landed, vuln open | 状态描述修正 |
| 5 | standalone production local shell 无隔离 | 未修 | **新增** |
| 6 | Supervisor IPC send/stop 无 timeout | 未修 | — |
| 7 | 无 CI 测试流水线 | 未修 | — |
| 8 | WS 重连无退避 + token 在 URL | 未修 | — |
| 9 | docker.sock 暴露（proxy + 后续 policy boundary） | 未修 | 方案分两阶段 |
| 10 | 切群异步写回未验证 group/generation | 未修 | 事实描述修正 |

- **移除**：`toggle_reaction` 竞态（架构串行化保护，不存在）
- **移除**：`bump_unread_for_group` N+1（已在第一轮降级为 Minor）
- **新增**：standalone production local shell 无隔离
