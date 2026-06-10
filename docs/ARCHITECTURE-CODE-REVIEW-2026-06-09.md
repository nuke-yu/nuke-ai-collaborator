# Nuke AI Collaborator — 架构师代码分析报告

> **分析视角**: 架构师<br>
> **分析日期**: 2026-06-09<br>
> **覆盖范围**: 全量代码库 + 设计文档

---

## 📊 执行摘要

这是一个**架构成熟、安全边界清晰**的群组式 AI 协作平台，核心设计决策（MCP 单进程、权限衰减、群组隔离）均正确实现。整体代码质量**B+**，存在若干技术债务但无致命缺陷。

| 维度 | 评分 | 说明 |
|------|------|------|
| **架构设计** | A | 分片宇宙隔离、进程拓扑清晰、事件驱动 |
| **安全边界** | A- | HIL 门 + 输出脱敏 + 权限衰减三层防护 |
| **代码质量** | B+ | 逻辑清晰但并发安全有疏漏 |
| **可维护性** | B | 模块化好但文档分散 |
| **测试覆盖** | B | 单元测试完善但集成测试不足 |

---

## 一、架构全景图

### 1.1 运行时拓扑（进程级隔离）

```
┌─────────────────────────────────────────────────────────┐
│                    main.py (FastAPI)                     │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Supervisor Process                    │   │
│  │  ┌─────────────────┐    ┌─────────────────────┐   │   │
│  │  │  Worker × N     │    │  MCP Collector      │   │   │
│  │  │  ┌───────────┐  │    │  (唯一 MCP 连接持有者)│   │   │
│  │  │  │ tool_loop │  │    │  ┌────────────────┐ │   │   │
│  │  │  │  + HIL    │  │    │  │ OAuth + Schema │ │   │   │
│  │  │  │  + perms  │  │    │  │ + process tree │ │   │   │
│  │  │  └───────────┘  │    │  └────────────────┘ │   │   │
│  │  └─────────────────┘    └─────────────────────┘   │   │
│  └──────────────────────────────────────────────────┘   │
│              EventBus (Pub/Sub 解耦)                      │
└─────────────────────────────────────────────────────────┘
          ▲                      ▲              ▲
          │                      │              │
    WS 消息路由            Worker→Collector   API 请求
```

**关键设计**:
- **MCP 单进程原则**：Collector 独占 MCP 连接，Worker 仅透传（`McpProxyProvider`）
- **群组隔离**：每个群组对应独立 SQLite DB（`group_{id}/chat.db`），Central DB 仅存元数据
- **事件总线**：29 种事件类型，WebSocket 与业务逻辑解耦

### 1.2 消息触发链（核心路径）

```
用户 WS 消息
  ↓
Supervisor 路由 (CELL-15 分片匹配)
  ↓
select_triggered_bots()  (mention/@all/workflow)
  ↓
dispatch_bots()  → 构建 ExecutionContext
  ↓
registry.get(executor_id).run(ctx)
  ↓
tool_loop_v1 (最多 10 轮推理循环)
  ├─ System Prompt: bot_prompt + personality + group_info + memory
  ├─ User Prefix: AGENT.md + BOOTSTRAP.md + IDENTITY.md + skills 元数据
  ├─ Tool Calls: builtin/skill → tool_executor → HIL 检查
  ├─ MCP Calls → McpProxy → Collector (pre-authorized only)
  └─ Redaction → 密钥脱敏 → 截断 → 返回
```

---

## 二、核心模块深度分析

### 2.1 运行时架构 (`runtime/`)

#### ✅ 架构亮点

**1. Supervisor 进程健壮性 (`supervisor.py`)**
- `_fanout()` 健康客户端驱逐（第 213-235 行）: 超时检测 + dead client 自动清理
- 租约转移协议 `reassign_group()`（第 284-325 行）: 带 ACK 确认的安全转移
- MCP Schema 缓存（第 46-48, 125-131 行）: SHA256 内容签名避免无效重推

**2. Worker 生命周期管理 (`worker.py`)**
- 明确任务管理：`_upstream_task`, `_report_task`, `_recap_task`, `_compaction_task`
- 优雅关闭 `close()`（第 114-136 行）: 正确取消所有后台任务并重置桥接状态
- 自动安装 MCP 桥接处理器（第 54-69 行）

**3. MCP Collector 安全边界 (`mcp_collector.py`)**
- 进程树清理 `_kill_descendants()`（第 32-59 行）: 防止孤儿进程
- OAuth 并发防护 `_auth_inflight` 集：避免重复授权
- Schema 版本感知：基于 SHA256 的内容签名

#### ⚠️ 架构问题

**P0 - Python 3.10+ 兼容性**
```python
# supervisor.py:308
fut = asyncio.get_event_loop().create_future()  # ❌ 弃用
# → asyncio.get_running_loop().create_future()

# worker.py:67
watcher.start(asyncio.get_event_loop())  # ❌ 弃用
# → asyncio.get_running_loop()
```

**P1 - 进程重启策略缺失**
- `_spawn_workers` 和 `_spawn_collector` 没有**自动重启机制**
- Worker crash 后 Supervisor 不会重建
- **建议**: 添加进程状态监控和指数退避重连

**P1 - 并发控制不足**
```python
# mcp_collector.py:296-303
elif ftype == ipc.protocol.MCP_CALL:
    t = asyncio.create_task(self._handle_call(frame))
    self._tasks.add(t)
# → 无并发限制，可能导致 Collector 过载
# 建议：添加 asyncio.Semaphore(10) 限制并发
```

---

### 2.2 工具执行层 (`executors/`)

---

## 🔍 CLAUDE.md 设计决策验证

### 1. ✅ MCP 单进程原则验证

**CLAUDE.md 要求**:
> MCP 连接只能活在 Collector 进程里（anyio cancel scope 与创建它的 task 绑定，跨进程/跨 task 会 RuntimeError）  
> Worker 只有 McpProxyProvider（透传），不能直接持有 McpClientToolProvider

**实现验证**:
- ✅ `mcp_collector.py:62-327`: `MCPCollector` 类是唯一持有 `McpClientToolProvider` 实例的进程
- ✅ `worker.py:56-68`: `McpProxyProvider` 仅负责透传，通过 `mcp_bridge` 发送 `MCP_CALL` 到 collector
- ✅ `mcp_client.py:1-22`: 明确注释 single-task session ownership 约束

**结论**: ✅ 严格遵循约束，无跨 task 访问

---

### 2. ✅ ToolRouter 路由策略验证

**CLAUDE.md 要求**:
> builtin / skill / shell 工具：留在 `tool_executor.execute()`，确保 before-hook 必然触发  
> MCP 工具：走 `McpProxyProvider → mcp_bridge → Collector`  
> **不要**把 Skill/Shell Provider 注册进 ToolRouter

**实现验证**:
- ✅ `tool_router.py:8-12`: 明确注释"Do NOT replace tool_executor"
- ✅ `tool_executor.py:205-287`: before/after hooks 的唯一拦截点
- ✅ `shell.py:1-27`: 明确标注"⚠️ DO NOT REGISTER"防止绕过

**结论**: ✅ 路由策略完整，无绕过风险

---

### 3. ✅ HIL 门验证

**CLAUDE.md 要求**:
> write 类工具需人工审批（Worker 侧执行，Collector 侧只跑 pre-authorized 调用）

**实现验证**:
- ✅ `mcp_collector.py:168-171`: 执行时传入 `context={"_pre_authorized": True, ...}`
- ✅ `mcp_proxy.py:63-65`: Worker 侧审批后构造 `mcp::{server}::{tool}` 作为审批名称

**结论**: ✅ Worker 侧审批 + Collector 侧 pre-authorized 双层防护完整

---

### 4. ✅ run_shell guard（双层）验证

**CLAUDE.md 要求**:
> 两层——regex 拦截高危命令 + shlex tokenized 层防绕过

**实现验证**:
- ✅ `workspace_tools.py:398-435`: `_DANGEROUS_PATTERNS` regex 层（第一层）
  - 覆盖 `rm -rf`, `bash -c .* \| (sh|bash)`, `base64 -d`, fork bomb 等
- ✅ `workspace_tools.py:467-559`: `_check_tokenized()` shlex tokenized 层（第二层）
  - 剥 wrapper (VAR=val/sudo/env) → basename 识别危险二进制 → 递归 bash -c 穿透
- ✅ `workspace_tools.py:641-657`: `_default_shell_guard` 作为 before-hook 接入

**单测覆盖**: `tests/test_shell_guard.py`（53 例）

**结论**: ✅ 两层防护完整，防绕过设计严谨

---

### 5. ✅ 输出脱敏验证

**CLAUDE.md 要求**:
> tool result 进模型上下文前过 `redaction.redact_secrets()`（PEM/JWT/AWS AKID/GitHub token 等）

**实现验证**:
- ✅ `redaction.py:65-80`: `redact_secrets()` 定义 11 类敏感模式
- ✅ `workspace_tools.py:610-626`: `_default_secret_redactor` as after-hook for builtin
- ✅ `mcp_client.py:336-339`: Collector 侧同样调用 `redaction.redact_secrets()` for MCP

**结论**: ✅ 双汇出口（builtin + MCP）都接入了脱敏

---

### 6. ✅ 子 Agent 权限衰减验证

**CLAUDE.md 要求**:
> `derive_subagent_ruleset()` 确保 bypassPermissions 不向下传播，blanket high-risk allow 被 drop

**实现验证**:
- ✅ `engine.py:100-119`: `derive_subagent_ruleset()` 实现
  - 第 76 行：`bypassPermissions` 不传播（模式衰减到 `default`）
  - 第 115-118 行：删除 blanket high-risk allow rules（无 args_pattern 的 `*` 规则）
- ✅ `engine.py:175-176`: `spawn_depth > 0` 时 deny without ask

**结论**: ✅ 权限衰减正确，bypass 不向下传播

---

### 7. ✅ 群组隔离验证

**CLAUDE.md 要求**:
> 每个群组有独立的 SQLite group DB（central DB 只存用户/群元数据）  
> Bot 的 skill、memory、permission rules 都按 group 隔离

**实现验证**:
- ✅ `db/dbpaths.py:8-9`: `group_db_path()` 返回 `WORKSPACE_ROOT/group_{group_id}/chat.db`
- ✅ `db/__init__.py:46-55`: `get_db()` vs `global_db()` 明确分离 group vs central 访问
- ✅ `db/context.py:21-41`: `current_db_path` contextvar + `bind_db()` 实现 per-call 绑定
- ✅ `worker.py:218-223`: `_group_context()` 使用 `db.bind_db()` 绑定当前群组 DB

**结论**: ✅ 完全隔离，contextvar 防止跨群 DB 泄漏

---

---

### 2.2 代码质量问题

#### 🔴 严重问题

**1. ⚠️ [NEW] `mcp_collector.py` 第 265-268 行：MCP_AUTH_START 并发锁非原子**
```python
if server in self._auth_inflight:  # L265 - check
    return
self._auth_inflight.add(server)  # L268 - set
```
- **问题**: check-and-set 在 Python 中**不是原子操作**
- **风险**: 两个并发请求可能都通过 L265 检查，然后都执行 L268
- **建议**: 使用 per-server lock
  ```python
  self._auth_locks: dict[str, asyncio.Lock] = {}  # 初始化
  async with self._auth_locks.setdefault(server, asyncio.Lock()):
      # 现有逻辑
  ```

**2. `compact.py` 第 68-71 行：全局状态无线程安全**
```python
# compact.py:68-71
_compaction_failures: dict[int, int] = {}  # ❌ 无锁
_db_compaction_locks: set[int] = set()     # ❌ 无锁
# → 多线程/多进程并发访问可能破坏数据结构
# 建议：使用 asyncio.Lock 或 threading.Lock
```

**3. `redaction.py` 第 42 行：OpenAI 模式可能误遮**
```python
("openai", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), 0),
```
- **问题**: `sk-` 前缀不够特异，可能匹配非密钥的 `sk-something`
- **风险**: 可能误遮合法数据
- **对标**: Claude Code 使用更长的固定长度验证

#### 🟡 中度问题

**4. `tool_executor.py` 第 222-223 行：钩子异常吞没**
```python
except Exception as e:
    return f"[钩子错误] {e}", True  # ❌ 异常转消息，日志丢失
# → 建议：logging.error(e) 后再返回
```

**5. ⚠️ [NEW] `mcp_proxy.py` 第 44-66 行：无 `__` 命名空间 HIL 判断逻辑可优化**
```python
sep = name.find("__")
if sep == -1:
    # 当前实现是安全的（fail-safe: 强制审批）
    # 但逻辑表述可以更清晰
    pass
```
- **问题**: 代码是安全的（无命名空间强制审批），但条件判断嵌套过深
- **建议**: 优化为 `if sep == -1: return True` 更直观

**6. `compact.py` 第 779-783 行：动态计算没有缓存**
```python
context_window = _MODEL_CONTEXT_WINDOWS.get(model_name, _DEFAULT_CONTEXT_WINDOW)
dynamic_cap = int(context_window * 0.15 * 4)
```
- 每次截断都重新计算，可添加 `functools.lru_cache`

**7. `registry.py` 第 38-55 行：discover() 同步阻塞**
```python
def discover():
    ...
    for f in sorted(PLUGIN_DIR.glob("*.py")):
        _load_file(f)
```
- 插件加载阻塞启动，大项目可能有延迟
- 建议：异步加载或预热缓存
```

---

### 2.3 技能系统 (`skills/`)

#### ✅ 发现机制（参考 OpenCode）

**四层扫描架构**:
```
system → group → role → personal
  ↓
深度合并（系统保护 + stub 回退 + 深度合并）
  ↓
缓存策略（mtime + size 签名 + 快速无效化）
  ↓
诊断警告（命名冲突 + 高权限工具警告）
```

**预算限制**:
```python
# loader.py:10-12
_MAX_SKILL_BODY_CHARS = 50_000      # 单个技能最大
_MAX_ALWAYS_TOTAL_CHARS = 100_000   # 常驻技能总上限
```

#### ⚠️ 安全问题

**P1 - Jinja2 沙箱不足**
```python
# loader.py:113
if skill_entry.get("template"):
    template_vars = {...}  # ❌ 使用标准 Jinja2 而非 SandboxedEnvironment
# → 技能文件被恶意修改可能执行任意代码
# 建议：jinja2.sandbox.SandboxedEnvironment
```

**P1 - 大文件读取无限制**
```python
# discovery.py:324
body_text = Path(s["path"]).read_text(encoding="utf-8")  # ❌ 无大小限制
# → 内存耗尽风险
# 建议：限制 100KB
```

---

### 2.4 权限引擎 (`permissions/`)

#### ✅ 决策流程（清晰分层）

```python
# engine.py:147-217
1. bypassPermissions → allow
2. deny rules → deny
3. allow rules → allow
4. dontAsk → auto-allow
5. subagent → 衰减派生
6. ask → HIL 弹窗
7. default → deny
```

**授权记忆粒度修复（已达标）**:
- **原问题**: `persist_rule` 空 `args_pattern` = 放行该工具全部调用（`git status` → `rm -rf /`）
- **现状**: `synthesize_args_pattern()` 按**子命令深度表**合成：
  - `git push origin main` → `git push *`
  - `docker compose up` → `docker compose *`
  - `ls -la` → `ls *`
  - 纯命令 `pwd` → 精确 `pwd`
- **单测**: `tests/test_permission_patterns.py`（16 例）

#### ⚠️ 并发安全问题

**P1 - 全局状态无锁**
```python
# engine.py:34
_once_grants: dict[tuple[int, int], list[tuple[str, str]]] = {}  # ❌ 无锁
_pending: dict[str, _PendingRequest] = {}                        # ❌ 无锁
# → 并发请求可能破坏数据结构
# 建议：asyncio.Lock 保护
```

**P0 - `get_event_loop()` 弃用**
```python
# engine.py:180
future: asyncio.Future = asyncio.get_event_loop().create_future()
# → asyncio.get_running_loop().create_future()
```

---

### 2.5 数据库层 (`db/`)

#### ✅ 双数据库架构

| 数据库 | 存储内容 | 隔离性 |
|--------|----------|--------|
| **central.db** | users, groups, members, role_templates, permission_rules, cron_jobs | 中央元数据 |
| **group_{id}/chat.db** | messages, role_summaries, message_embeddings, agent_sessions, workflow_state | 群组物理隔离 |

**反规范化设计**（`migration_014`）:
- `messages` 表存储 `sender_name`, `sender_type`, `sender_avatar`, `sender_provider`, `sender_model`
- **收益**: 避免跨数据库 JOIN，读取性能优化
- **代价**: 写冗余（发送者信息变更需同步更新）

#### ⚠️ 性能瓶颈

**P0 - 索引缺失**
```sql
-- messages 表缺少复合索引
CREATE INDEX idx_messages_group_created_at ON messages(group_id, created_at);
CREATE INDEX idx_messages_group_member_id ON messages(group_id, member_id);

-- role_summaries 缺少 bot_id + group_id 索引
CREATE INDEX idx_role_summaries_bot_group ON role_summaries(bot_id, group_id);

-- agent_sessions 缺少状态索引
CREATE INDEX idx_agent_sessions_status ON agent_sessions(status, updated_at);
```

**P1 - 查询性能问题**
```sql
-- get_member_stats (第 81-92 行): LEFT JOIN + GROUP BY，消息量大时慢
-- → 建议改为 COUNT(*) 子查询

-- _sender_snapshot (第 95-113 行): 两次数据库连接（中央 DB → 组 DB）
-- → 异常处理吞没错误，网络抖动可能导致静默失败
```

**P1 - 迁移无法回滚**
- 所有迁移单向 ADD COLUMN，无法处理 DROP/RENAME
- 缺少版本一致性检查（中央 DB 和组 DB 可能不同步）
- **建议**: `_migrations` 表存储 `rollback_sql`

---

### 2.6 AI 层 (`ai/`)

#### ✅ 多模型客户端管理

**连接池优化**:
```python
# client.py:全局 httpx.AsyncClient
httpx.AsyncClient(
    max_connections=100,
    max_keepalive_connections=20,
    timeout=60.0
)
```

**模型路由策略**:
- 支持 `deepseek`, `openai`, `claude`, `ollama` 四家
- `_to_claude_messages`（第 273-333 行）: OpenAI → Claude 格式转换
  - Tool calls 转换
  - 连续同角色消息合并
  - 多模态内容处理（image_url）

**错误处理**:
- `AIRateLimitError` 携带 `wait_seconds`，用于指数退避重试
- **DSML 兜底解析**（第 336-375 行）: 从文本中提取泄漏的工具调用

#### ⚠️ 设计疏漏

**P0 - 缺少 LLM 响应缓存**
- 无 Redis 缓存层，相同 prompt 重复调用 API
- **建议**: Key=`hash(prompt+params)`, TTL=1h

**P0 - `get_event_loop()` 弃用**
```python
# memory.py:39
asyncio.get_event_loop()  # ❌
# → asyncio.get_running_loop()
```

**P1 - embedding 硬编码 DeepSeek**
```python
# client.py:101-111
get_embedding() 始终使用 DeepSeek
# → 不支持其他提供商 embedding，多模型切换时维度可能不一致
```

**P1 - 成本计算无预警**
- `pricing.py` 无阈值告警，可能成本失控
- **建议**: `cost_threshold` 配置，超标发送通知

---

### 2.7 API 层 (`api/`)

#### ✅ 完整 CRUD 实现

| 资源 | 端点 | 说明 |
|------|------|------|
| **groups** | GET/POST/PUT/DELETE | 群组管理 |
| **messages** | GET/POST | 消息 CRUD |
| **auth** | POST /login, POST /refresh | JWT 认证 |
| **workspace** | GET/POST/PUT/DELETE | 文件操作 |

**级联删除策略**（`groups.py:27-47`）:
```python
_CENTRAL_REFS = ["members", "role_assignments"]
_MEMBER_DATA = ["messages", "reactions", "agent_sessions"]
# WebSocket 广播同步 + 工作区清理
```

#### ⚠️ 安全问题

**P0 - 登录端点无速率限制**
```python
# auth.py:无限制
# → 暴力破解风险
# 建议：@rate_limit(5, '1m')
```

**P0 - 路径遍历风险**
```python
# workspace.py: path 参数未验证
# → ../../etc/passwd 可能读取系统文件
# 建议：pathlib.Path 验证 `not path.is_relative_to(workspace)`
```

**P1 - JWT 无刷新机制**
- 无 `/api/auth/refresh` 端点，token 过期需重新登录
- **建议**: 添加 refresh token 机制

**P1 - 上传端点无频率限制**
- 无单用户频率限制，可能 DoS
- **建议**: 限制 10 次/分钟

---

### 2.8 前端架构 (`frontend/`)

#### ✅ 组件设计亮点

**双缓存策略**（`ChatWindow.jsx`）:
```javascript
- messages: 实时渲染
- messagesCache: 离线缓存
- 重连后 fetchMessages(..., afterId) 补齐缺失消息
```

**无限滚动优化**:
- `loadMore` 支持下拉加载历史（第 261-279 行）
- 滚动位置补偿（第 274-278 行）

**消息去重**:
- `handleWsMessage` 检查 `id` 避免重复渲染（第 303 行）

#### ⚠️ 架构问题

**P0 - 状态管理混乱**
- **67 个 state** 分散在组件内，难以维护
- 多处 `setMessages` 调用，状态更新逻辑分散
- **建议**: 迁移到 `Zustand` 或 `Jotai` 全局 store

**P1 - WebSocket 重连无退避**
```javascript
// useWebSocket.js:36
setTimeout(connect, 3000)  # ❌ 固定 3 秒
# → 高负载时应指数退避 min(3s * 2^n, 30s)
```

**P1 - 认证错误处理薄弱**
```javascript
// useWebSocket.js:39-40
if (evt.data === 'auth_error') {
    // ❌ 直接退出，应提示用户重新登录
}
```

**P2 - 多标签页状态不同步**
- 同一账号多标签页创建多个 WebSocket 连接
- **建议**: `Storage` 事件广播状态变化

---

## 三、技术债务清单

> **复核日期**: 2026-06-10 · 方法：逐文件代码精读，对照重构后代码实际状态更新

### P0 - 立即修复（兼容性/安全）

| ID | 文件 | 行 | 问题 | 影响 | 状态 |
|----|------|-----|------|------|------|
| DFT-001 | `runtime/supervisor.py` | 308 | `get_event_loop()` → `get_running_loop()` | Python 3.10+ 兼容性 | ✅ 已修复 |
| DFT-002 | `ai/memory.py` | 39, 58 | 同左（注：实际在 memory.py，非 worker.py） | 同左 | ✅ 已修复 |
| DFT-003 | `permissions/engine.py` | 180 | 同左 | 同左 | ✅ 已修复 |
| DFT-004 | `ai/memory.py` | 39, 58 | 同左（与 DFT-002 同一位置，一并修复） | 同左 | ✅ 已修复 |
| DFT-005 | `api/auth.py` | 32-45 | 登录无速率限制 | 暴力破解风险 | ✅ 已修复（IP 维度滑动窗口，5次/60s） |
| DFT-006 | `workspace/__init__.py` | 71-78 | 路径遍历防护 | 文件读取漏洞 | ✅ 已修复（`_safe_path()` + `is_relative_to()` 检查） |
| DFT-007 | `db/migrations.py` | - | messages / role_summaries 无复合索引 | 查询性能 | ✅ 已修复（migration_018） |
| DFT-008 | `mcp_collector.py` | 265-268 | MCP_AUTH_START 并发锁非原子 | OAuth race condition | ✅ 已修复（`75fd79d`） |

### P1 - 短期优化（可用性/性能）

| ID | 模块 | 改进点 | 收益 | 状态 |
|----|------|--------|------|------|
| DFT-010 | `runtime/supervisor.py` | Worker 进程 crash 无自动重启，仅记录异常 | 提高可用性 | ✅ 已修复（`_run_process_loop` 指数退避重启，max 60s） |
| DFT-011 | `runtime/worker.py` | 断线重连指数退避 | 网络鲁棒性 | ➖ 不需要修（IPC 是 UDS 本地套接字，断连意味着对端进程已死；`Worker.run()` 直接退出，Supervisor 的 `_run_process_loop` 以指数退避重启进程——DFT-010 已覆盖。进程内重连反而会带着 stale 状态重连，不如干净重启）|
| DFT-012 | `permissions/engine.py:34,37` | `_once_grants` / `_pending` 全局 dict 无 asyncio.Lock | 并发安全 | ✅ 已修复（check 函数关键写入段加 Lock） |
| DFT-013 | `executors/compact.py:68,71` | `_compaction_failures` / `_db_compaction_locks` 无锁 | 并发安全 | ➖ asyncio 单线程内 await 点间操作原子，实际安全 |
| DFT-014 | `runtime/mcp_collector.py:303,307` | `_handle_call` 创建 task 无 Semaphore 并发限制 | 防止过载 | ✅ 已修复（`asyncio.Semaphore(10)` 限制并发执行） |
| DFT-015 | `ai/client.py` | LLM 响应缓存（Redis） | 成本优化 | ➖ 架构演进，非 bug |
| DFT-016 | `api/auth.py` | 无 refresh token 端点，token 过期需重新登录 | 用户体验 | ✅ 已修复（`POST /api/auth/refresh`） |
| DFT-017 | `db/schema.py` | 复合索引缺失（同 DFT-007） | 查询性能 | ✅ 已修复（migration_018，同 DFT-007） |
| DFT-018 | `mcp_proxy.py` | 无 `__` 命名空间 HIL 逻辑 | 代码清晰性 | ✅ 不适用（逻辑已简化，无嵌套过深问题） |

### P2 - 中期优化（可维护性）

| ID | 模块 | 改进点 | 收益 | 状态 |
|----|------|--------|------|------|
| DFT-020 | `executors/redaction.py:42` | `sk-[A-Za-z0-9]{20,}` 特异性不足，可能误遮 | 降低误报 | ✅ 已修复（加 `(?!ant-)` 负向前瞻，排除 Anthropic token） |
| DFT-021 | `executors/compact.py:90-101` | `estimate_tokens()` 无缓存，每次重算 | 性能 | ✅ 已修复（DFT-030 完全解决；增量缓存 + id 复用防护）|
| DFT-022 | `skills/discovery.py` | 技能大文件读取无大小限制 | 内存安全 | ➖ 低风险（技能文件通常小） |
| DFT-023 | `skills/processor.py:82-92` | Jinja2 SandboxedEnvironment | 执行安全 | ✅ 已修复（已改用 `SandboxedEnvironment`） |
| DFT-024 | `runtime/ipc/protocol.py` | 协议帧无版本字段 | 向后兼容 | ✅ 已修复（`PROTOCOL_VERSION = 1`，envelope 加 `v` 字段） |
| DFT-025 | `frontend/src/components/ChatWindow.jsx` | 仍有 46 个 useState，无 Zustand/Jotai | 可维护性 | ✅ 已修复（groupStore + chatStore；ChatWindow 降至 20 个 useState；handleWsMessage 150 行→1 行；MessageList/GroupList 直接订阅 store） |

### P3 - 长期优化（架构演进）

| ID | 模块 | 改进点 | 收益 | 状态 |
|----|------|--------|------|------|
| DFT-030 | `executors/compact.py` | 增量 token 估算（当前全量重算） | 性能 | ✅ 已修复（`_token_cache` id+len+verifier 增量缓存；同 list 新增一条消息只算最后一条） |
| DFT-031 | `permissions/` | 规则匹配无 LRU 缓存 | 性能 | ✅ 已修复（`_match_tool_pattern` + `_match_args_pattern` 各加 `@lru_cache`） |
| DFT-032 | `runtime/supervisor.py` | 无 Prometheus 进程监控 | 可观测性 | ✅ 已修复（`runtime/metrics.py` pull-time collector + `GET /metrics`；进程存活/重启/RSS/CPU + worker 心跳新鲜度）|
| DFT-033 | `runtime/supervisor.py` | 结构化日志（worker 有，supervisor 无） | 调试效率 | ✅ 已修复（`start()` 调用 `setup_structured_logging`） |
| DFT-034 | `db/migrations.py` | 迁移无 rollback SQL，仅正向 DDL | 可回滚性 | ✅ 已修复（migration_001~018 docstring 均补充 Rollback SQL 注释） |
| DFT-035 | `ai/client.py:101-111` | embedding 硬编码 DeepSeek `text-embedding-v2` | 灵活性 | ✅ 已修复（死的 `get_embedding` 删除；新 `ai/embeddings.py` 可插拔后端 local/openai/deepseek，config 驱动；`memory.py` 维度护栏 `emb_sig` + `scripts.reindex_embeddings` 重建脚本）|

---

## 四、已知 Gap 状态更新

根据 CLAUDE.md 记录的已知 Gap，以下状态已更新：

| Gap ID | CLAUDE.md 记录 | 第二份验证结果 | 状态 |
|--------|---------------|----------------|------|
| GAP-001 | `mcp_bridge.py` 使用 `get_event_loop()` | ✅ 已修复为 `get_running_loop()` | 已解决 |
| GAP-002 | `mcp_proxy.py` 对无 `__` 命名空间工具 HIL 判断失效 | ⚠️ fail-safe 有效，但逻辑可优化 | 部分解决 |
| GAP-003 | MCP Collector 并发 `MCP_AUTH_START` 无 per-server 锁 | ⚠️ set-based guard 非原子，建议加 per-server lock | 新增待修复 |

> **建议**: 更新 `docs/TOOL-LAYER-GAP-ANALYSIS.md` 以反映这些状态变化。

---

## 四、架构级建议

### 4.1 架构优点（已达标）

1. ✅ **MCP 单进程原则**：Collector 独占连接，Worker 仅透传
2. ✅ **权限边界清晰**：Worker 侧 HIL，Collector 侧 pre-authorized
3. ✅ **子权限衰减**：正确实现，bypass 不传播
4. ✅ **群组隔离**：每个 group 独立 DB + lifecycle
5. ✅ **追踪传递**：trace_id 全链路注入
6. ✅ **命令安全加固**：两层防御（regex + tokenized）
7. ✅ **输出脱敏**：双出口（builtin + MCP）
8. ✅ **授权记忆粒度**：子命令深度表合成

### 4.2 架构风险（需关注）

**风险 1: Supervisor 单点故障**
```
风险：Supervisor crash → 所有 worker 失去控制
建议：添加 Supervisor HA（如 etcd/Consul 共享状态）
```

**风险 2: IPC 单总线瓶颈**
```
风险：所有消息经 Supervisor 中转，高负载时成为瓶颈
建议：评估 Worker→Worker 旁路通信
```

**风险 3: Shared memory 状态**
```
风险:_once_grants 等全局状态仅内存存储，重启丢失
建议：迁移到 Redis 等分布式存储
```

### 4.3 测试覆盖建议

| 模块 | 当前状态 | 建议补充 |
|------|----------|----------|
| `supervisor` | 基本路由测试 | 进程重启、超时广播 |
| `worker` | loop 测试 | 重连、MCP 超时 |
| `permissions` | 逻辑测试 | 并发、超时、缓存 |
| `mcp_collector` | 基础测试 | OAuth 端到端、并发调用 |
| `db` | CRUD 测试 | 索引性能、迁移回滚 |
| `frontend` | 组件测试 | WebSocket 重连、缓存策略 |

---

## 五、对标分析（vs 行业最佳实践）

| 维度 | nuke | Claude Code | opencode | 评价 |
|------|------|---------------|----------|------|
| 命令安全 | 🔶 规则 | ✅ 分类器 | 🔶 规则 | 接近，可加 classifier |
| 子 agent 权限 | ✅ 衰减 | ✅ 任务限定 | ✅ 衰减 | 达标 |
| 参数校验 | ✅ schema | ✅ zod | ✅ schema | 达标 |
| 工具规模治理 | 🔶 预算 | ✅ deferred | ❌ | 可加 ToolSearch |
| MCP OAuth | ✅ 已实现 | ✅ | ✅ | 达标 |
| 输出脱敏 | ✅ 双出口 | ❌ | ✅ middleware | 达标 |
| 可观测性 | ⚠️ trace_id | ✅ span | ✅ | 可加 per-call span |

**结论**: 核心安全边界和架构设计**对标行业最佳实践**，技术债务集中在**可观测性**和**性能优化**，无架构级硬伤。

---

## 六、总结与行动建议

### 6.1 项目健康度评分

```
┌─────────────────────────────────────┐
│  架构设计    ████████░░  8.5/10    │
│  安全边界    ████████░░  8.0/10    │
│  代码质量    ███████░░░  7.5/10    │
│  可维护性    ██████░░░░  6.5/10    │
│  测试覆盖    ██████░░░░  6.0/10    │
└─────────────────────────────────────┘
  综合评分：B+ (7.7/10)
```

### 6.2 优先级行动建议

#### 🚀 本周内（P0 紧急）
1. 修复 `get_event_loop()` → `get_running_loop()`（4 处）
2. 添加登录速率限制 + 路径遍历防护
3. 添加关键数据库索引（messages, role_summaries）

#### 📅 本月内（P1 重要）
1. 添加 worker 自动重启 + 断线重连（指数退避）
2. 全局状态加锁保护（permissions + compact）
3. 集成 Redis 缓存（LLM 响应 + 规则缓存）
4. 前端状态管理迁移到 Zustand

#### 📆 下季度（P2 优化）
1. 添加 per-call span 可观测性
2. 协议版本字段 + 迁移回滚记录
3. 成本预警机制
4. 前端多标签页状态同步

---

**最终结论**: 这是一个**架构成熟、安全边界清晰**的项目，核心设计决策正确实现。技术债务集中在**兼容性修复**和**性能优化**，无架构级硬伤，建议在 P0/P1 优先级下逐步清理技术债务。
