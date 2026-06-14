# Nuke AI Collaborator - 完整架构对比 Review v2

> **对比项目 (4 个):**
> 1. **Nuke AI Collaborator** (`/Users/Nuke/claudeFolder/nuke-ai-collaborator`) - 群组式 AI 协作平台
> 2. **Claude Code Haha** (`/Users/Nuke/claude-code-haha-main`) - Claude Code 本地可运行版 (泄露源码)
> 3. **gsd-2** (`/Users/Nuke/gsd-2`) - Anthropic Claude Code 官方源码
> 4. **opencode** (`/Users/Nuke/opencode`) - 另一个 AI Agent 平台

---

## 1. 架构总览对比

### 1.1 核心架构矩阵

| 维度 | Nuke AI | Claude Code Haha | gsd-2 | opencode |
|------|---------|------------------|-------|----------|
| **定位** | 群组式 AI 协作平台 | 终端 AI 助手 | 终端 AI 助手 | AI Agent 平台 |
| **主入口** | `backend/main.py` (FastAPI) | `bin/claude-haha` + `src/main.tsx` | `src/cli.ts` + `src/headless.ts` | Bun + TypeScript |
| **UI 层** | React 19 + Vite (浏览器) | Ink 6 + React 19 (终端 TUI) | Ink (终端) | 未详 |
| **运行时** | Python 3 | Bun | Bun | Bun |
| **Worker 模型** | Python 多进程 (Supervisor 管理) | 单进程 + 特征开关 | RPC 模式 child process | Effect-TS Layer 系统 |
| **MCP 处理** | 专用 Collector 进程 | MCP Client/Registry | Extension Registry | Plugin V2 System |
| **通信** | IPC (Unix Domain Socket) | In-process + API calls | RPC Client/Session Manager | Effect Layers + Context |
| **隔离模式** | **群组隔离** (独立 DB/Bot/对话) | Worktree 模式 | Project 模式 | Session 模式 |

### 1.2 进程拓扑结构

```
┌─────────────────────────────────────────────────────────────┐
│ Nuke AI                                                        │
│  ┌─────────────┐                                               │
│  │  main.py    │  (FastAPI + WebSocket)                       │
│  │  Supervisor │                                               │
│  └──────┬──────┘                                               │
│         │ IPC                                                  │
│    ┌────┴─────────────────┐                                   │
│    │ Worker × N   MCP Collector │                            │
│    │ (AI 推理)      (MCP 连接)    │                            │
│    └──────────────────────────┘                                   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Claude Code Haha / gsd-2                                     │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  CLI (Bun)                                             │ │
│  │  ├─ main.tsx (TUI)                                    │ │
│  │  ├─ headless.ts (headless mode)                       │ │
│  │  └─ Tools (Bash, Edit, Grep, MCP, etc.)               │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ opencode                                                     │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Bun Runtime                                           │ │
│  │  ├─ Effect-TS Layer System                            │ │
│  │  ├─ Plugin V2 System                                  │ │
│  │  ├─ Agent V2 (Schema-based)                           │ │
│  │  └─ Tool Registry                                     │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 工具系统设计对比

### 2.1 工具注册和路由

#### Nuke AI

```python
# backend/executors/registry.py
# 分层工具路由

# 1. Builtin/Skill/Shell 工具 → tool_executor.execute()
# 2. MCP 工具 → McpProxyProvider → mcp_bridge → Collector

# backend/tools/tool_router.py
router = ToolRouter(...)
# 不注册 Skill/Shell Provider (避免 schema 泄露)
```

**特点:**
- ✅ 清晰的职责分离
- ✅ HIL gate 在 Worker 侧执行
- ✅ MCP 工具不走 tool_executor registry

#### Claude Code Haha

```typescript
// src/tools.ts
// 25+ 工具，每个工具一个目录

import { BashTool } from './tools/BashTool/BashTool.js'
import { FileEditTool } from './tools/FileEditTool/FileEditTool.js'
import { FileWriteTool } from './tools/FileWriteTool/FileWriteTool.js'
// ... 条件导入 feature-gated tools

export function getTools(): Tools {
  return [
    new AgentTool(),
    new BashTool(...),
    new FileEditTool(...),
    // ...
  ]
}
```

**特点:**
- ✅ 每个工具独立目录，结构清晰
- ✅ 特征开关控制工具启用
- ✅ 单进程工具执行

#### gsd-2

```typescript
// Extension Registry pattern
// 扩展系统内建 MCP 支持
```

#### opencode

```typescript
// packages/opencode/src/session/tools.ts:75-116
for (const item of yield* registry.tools({ ... })) {
  const schema = ProviderTransform.schema(input.model, ToolJsonSchema.fromTool(item))
  tools[item.id] = tool({
    description: item.description,
    inputSchema: jsonSchema(schema),
    execute(args, options) {
      return run.promise(
        Effect.gen(function* () {
          yield* plugin.trigger("tool.execute.before", ...)
          const result = yield* item.execute(args, ctx)
          yield* plugin.trigger("tool.execute.after", ...)
          return output
        })
      )
    }
  })
}
```

**特点:**
- ✅ Schema-based tool definitions
- ✅ Plugin hooks (before/after)
- ✅ Effect-TS 错误处理

### 2.2 工具权限控制

| 项目 | 权限模型 | HIL Gate | 沙箱 |
|------|----------|----------|------|
| **Nuke AI** | Rule-based + Permission Ruleset | ✅ Worker 侧执行 | Shell guard (regex + shlex) |
| **Claude Code Haha** | Permission Mode + Policy | ✅ CLI level | Extension sandbox |
| **gsd-2** | Policy-based | ✅ CLI level | Extension sandbox |
| **opencode** | Schema-based Ruleset | ✅ Plugin system | Plugin V2 |

---

## 3. 实时通信对比

### 3.1 通信架构

#### Nuke AI (WebSocket + IPC)

```
Browser (WebSocket) 
    ↓
Supervisor (main.py:338-354)
    ↓ IPC
Worker (AI 推理 + tool_loop_v1)
    ↓
MCP Collector (if MCP tool)
```

**关键代码:**
```python
# backend/main.py:226-247
async def _initialize_websocket_session(websocket: WebSocket, group_id: int, member_id: int):
    await manager.connect(websocket, group_id, member_id)
    
    # 单 group 单 proxy，避免重复广播
    proxy = _group_proxies.get(group_id)
    if proxy is None:
        proxy = WSClientProxy(group_id)
        _group_proxies[group_id] = proxy
        sup_mod.supervisor.register_browser(group_id, proxy)
```

**DFT-030 优化:**
```python
# backend/runtime/supervisor.py:257-279
async def _fanout(self, group_id, payload: dict) -> None:
    _SEND_TIMEOUT = config.SUPERVISOR_SEND_TIMEOUT
    # Head-of-line blocking 防护
    for client in clients:
        await asyncio.wait_for(client.send(payload), _SEND_TIMEOUT)
```

#### Claude Code Haha (Ink TUI)

```typescript
// src/main.tsx
// React + Ink 终端渲染
// 无 WebSocket，直接调用
```

#### gsd-2 (RPC + Events)

```typescript
// src/headless-events.ts
// 事件驱动，支持 JSONL streaming
// 支持 headless mode
```

### 3.2 群组/会话隔离

#### Nuke AI

```python
# backend/main.py:80-93
async def on_unread_delta(group_id: int, payload: dict):
    """Supervisor is the sole writer of the central unread_counts projection (V3 §10.1)."""
    from db import global_db, write_connect, get_members, bump_unread_for_group
    sender_id = payload.get("member_id")
    online = set(manager.get_online_member_ids(group_id))
    async with global_db() as gdb:
        members = await get_members(gdb, group_id)
    if not any(m["type"] == "human" and m["id"] != sender_id and m["id"] not in online
               for m in members):
        return
    async with write_connect() as wdb:
        await bump_unread_for_group(wdb, group_id, members, sender_id, online)
```

**隔离级别:**
- 每个群组独立 SQLite DB
- 独立 Bot 员工
- 独立对话历史
- 独立知识库
- **不跨群共享**

#### Claude Code Haha

```typescript
// Worktree Mode
// 基于 git 仓库的上下文隔离
```

#### gsd-2

```typescript
// src/headless.ts:126-153
export function resolveResumeSession(sessions: SessionInfo[], prefix: string): ResumeSessionResult
// 会话恢复机制
```

---

## 4. MCP 实现对比

### 4.1 Nuke AI (Collector 模式)

```
Supervisor (bus) 
  ├── Worker (McpProxyProvider - proxy only)
  └── MCP Collector (唯一持有真实 MCP 连接)
```

**关键代码:**
```python
# backend/runtime/supervisor.py:165-173
# Worker 连接时获取 cached MCP schemas
if wid != ipc.protocol.MCP_COLLECTOR_ID and self._mcp_schemas is not None:
    await ipc.send_msg(writer, self._mcp_schemas)

# backend/runtime/supervisor.py:215-238
elif t in (ipc.protocol.MCP_CALL, ipc.protocol.MCP_AUTH_START):
    # worker → collector
    if not await self.send_to_worker_id(ipc.protocol.MCP_COLLECTOR_ID, frame):
        # collector 未就绪，返回错误
        await self.send_to_worker_id(frame.get("origin_worker_id"), ...)
elif t == ipc.protocol.MCP_SCHEMAS:
    # collector pushed a new snapshot → cache + fan out
    self._mcp_schemas = frame
    for wid, writer in list(self._workers.items()):
        if wid == ipc.protocol.MCP_COLLECTOR_ID:
            continue
        try:
            await ipc.send_msg(writer, frame)
        except Exception:
            log.warning("supervisor: failed to push MCP schemas to %s", wid)
```

**Known Gaps (docs/TOOL-LAYER-GAP-ANALYSIS.md):**
1. ⚠️ `mcp_bridge.py` 使用 `asyncio.get_event_loop()` → 应改 `get_running_loop()`
2. ⚠️ `mcp_proxy.py` 对无 `__` 命名空间的工具 HIL 判断静默失效
3. ⚠️ MCP Collector 并发 `MCP_AUTH_START` 无 per-server 锁

### 4.2 Claude Code Haha / gsd-2

```typescript
// src/services/mcp/client.ts
// src/services/mcp/config.ts
// Extension Registry pattern
```

### 4.3 opencode

```typescript
// packages/opencode/src/session/tools.ts:118-151
for (const [key, item] of Object.entries(yield* mcp.tools())) {
  const execute = item.execute
  if (!execute) continue
  const schema = yield* Effect.promise(() => Promise.resolve(asSchema(item.inputSchema).jsonSchema))
  // Schema transformation + execution
}
```

---

## 5. 安全设计对比

### 5.1 多层防护架构

#### Nuke AI (当前项目)

| 层 | 位置 | 机制 |
|----|------|------|
| **HIL Gate** | Worker 侧 | Write 工具需要人工审批 |
| **run_shell guard** | 两层 | Regex + shlex tokenized |
| **Secret Redaction** | 进模型前 | redaction.redact_secrets() |
| **Subagent 权限衰减** | derive_subagent_ruleset() | bypassPermissions 不向下传播 |

```python
# backend/permissions/
# HIL gate executes in Worker, not Collector
# run_shell guard: regex + shlex tokenization
# redaction.redact_secrets() before model context
```

#### Claude Code Haha

```typescript
// src/utils/permissions/permissionSetup.ts
// Permission Mode + Policy enforcement
```

#### gsd-2

```typescript
// src/security-overrides.ts
// src/cli-policy.ts
// Extension Validator + Policy System
```

### 5.2 安全特性对比

| 特性 | Nuke AI | Claude Code Haha | gsd-2 | opencode |
|------|---------|------------------|-------|----------|
| **HIL Gate** | ✅ Worker 侧 | ✅ CLI level | ✅ CLI level | ✅ Plugin |
| **Shell Guard** | ✅ 两层 | ✅ Extension sandbox | ✅ Extension sandbox | ⚠️ 未详 |
| **Secret Redaction** | ✅ 进模型前 | ⚠️ 未详 | ⚠️ 未详 | ⚠️ 未详 |
| **权限衰减** | ✅ Subagent | ⚠️ 未详 | ⚠️ 未详 | ✅ Schema |
| **审计日志** | ✅ Trace ID | ⚠️ CLI events | ⚠️ CLI events | ✅ Effect tracing |

---

## 6. 从竞品学习的最佳实践

### 6.1 从 Claude Code Haha 学习

**特点:**
- ✅ **Ink TUI**: 终端 UI 渲染优秀
- ✅ **特征开关**: `feature('FEATURE_NAME')` 控制功能
- ✅ **条件导入**: 减少启动时的模块加载
- ✅ **插件系统**: `src/plugins/bundled/`
- ✅ **技能系统**: `src/skills/bundled/`

```typescript
// src/main.tsx:1-20
// 预启动优化
profileCheckpoint('main_tsx_entry');
startMdmRawRead();  // MDM subprocess in parallel
startKeychainPrefetch();  // Keychain reads in parallel
```

### 6.2 从 gsd-2 学习

**特点:**
- ✅ **Headless Mode**: 脚本化运行，支持 CI/CD
- ✅ **Exit Codes 标准化**:
  ```typescript
  EXIT_SUCCESS = 0
  EXIT_ERROR = 1
  EXIT_BLOCKED = 10
  EXIT_CANCELLED = 11
  ```
- ✅ **Resume Sessions**: 长时间任务不丢失
  ```typescript
  --resume <session_id>
  ```
- ✅ **Idle Timeout 智能管理**:
  ```typescript
  IDLE_TIMEOUT_MS = 15_000
  NEW_MILESTONE_IDLE_TIMEOUT_MS = 120_000
  ```
- ✅ **Auto-restart with backoff**: 崩溃后自动恢复

### 6.3 从 opencode 学习

**特点:**
- ✅ **Effect-TS Layer System**: 类型安全的 DI
- ✅ **Schema-based Definitions**: 所有 config/tool/agent 用 Schema 定义
- ✅ **Plugin Hooks**: before/after 钩子
- ✅ **Streaming Tool Results**: 分块返回，降低 memory pressure

```typescript
// packages/opencode/src/session/tools.ts:84-114
execute(args, options) {
  return run.promise(
    Effect.gen(function* () {
      const ctx = context(args, options)
      yield* plugin.trigger("tool.execute.before", ...)
      const result = yield* item.execute(args, ctx)
      yield* plugin.trigger("tool.execute.after", ...)
      return output
    })
  )
}
```

---

## 7. 代码规模和结构对比

| 项目 | 主语言 | 总代码行数 | 关键目录 |
|------|--------|------------|----------|
| **Nuke AI** | Python | ~10k (backend) | `backend/`, `frontend/` |
| **Claude Code Haha** | TypeScript | ~100k+ | `src/`, `src/tools/` (20+ tools) |
| **gsd-2** | TypeScript | ~200k+ | `src/`, `packages/`, `web/` |
| **opencode** | TypeScript | ~50k+ | `packages/` (23 packages) |

### 7.1 Nuke AI 代码结构

```
backend/
├── main.py              # 354 lines - FastAPI entry
├── runtime/
│   ├── supervisor.py    # 372 lines - Supervisor engine
│   └── ipc/             # IPC protocol
├── api/                 # API routers
├── tools/               # Tool implementations
├── permissions/         # Permission system
└── executors/           # Tool executors
```

### 7.2 Claude Code Haha 代码结构

```
src/
├── main.tsx             # TUI entry
├── tools/               # 25+ tools, each in its own dir
│   ├── BashTool/
│   ├── FileEditTool/
│   ├── FileWriteTool/
│   └── ...
├── services/            # MCP, OAuth, analytics
├── commands/            # Slash commands
├── skills/              # Skill system
└── components/          # Ink UI components
```

---

## 8. 具体改进建议

### 8.1 立即修复 (本周) - P0

1. **DFT-001**: `mcp_bridge.py` 改 `get_running_loop()`
2. **DFT-002**: `mcp_proxy.py` HIL 判断修复
3. **DFT-003**: MCP Collector per-server lock

### 8.2 短期优化 (1-2 周) - P1

1. **借鉴 gsd-2 的 Headless Mode**:
   ```python
   # Add headless entry point
   # --json output format
   # --resume session support
   ```

2. **借鉴 Claude Code Haha 的预启动优化**:
   ```python
   # Start MDM-like processes in parallel
   # Profile checkpoint system
   ```

3. **Routing Cache 配置化**:
   ```python
   # backend/runtime/supervisor.py:306-319
   # 当前：60 秒硬编码
   # 建议：config-driven
   CACHE_TTL = config.ROUTING_CACHE_TTL
   ```

4. **Secret Redaction 扩展**:
   - 添加 Azure SAS tokens
   - 添加 GCP service account keys
   - 添加 SSH private keys

### 8.3 长期规划 (1-3 月) - P2

1. **测试覆盖率提升**:
   - 目标：80%+
   - 重点：MCP flows, Worker lifecycle, HIL gates

2. **Feature parity with gsd-2/opencode**:
   - Headless mode
   - Auto-restart with backoff (gsd-2 pattern)
   - Extension/plugin system (opencode pattern)

3. **性能优化**:
   - 评估共享内存 IPC (vs UDS)
   - MCP schema cache 优化

---

## 9. 结论

### 9.1 Nuke AI 的独特优势

| 优势 | 描述 |
|------|------|
| **群组隔离架构** | 真正的多租户隔离，独立 DB/Bot/对话 |
| **进程级安全** | Worker/Collector 分离，HIL gate 在 Worker 侧 |
| **WebSocket 实时通信** | 浏览器端实时推送，支持多客户端 |
| **清晰的文档** | CELL-XX 编号系统，详细架构注释 |

### 9.2 需要改进的方面

| 优先级 | 项目 | 建议 |
|--------|------|------|
| **P0** | 已知 gaps | 修复 DFT-001/002/003 |
| **P1** | Headless mode | 借鉴 gsd-2 实现脚本化运行 |
| **P1** | 预启动优化 | 借鉴 Claude Code Haha 并行加载 |
| **P2** | 测试覆盖 | 建立自动化测试流水线 |
| **P2** | Schema-based | 借鉴 opencode 的类型安全 |

### 9.3 建议下一步

1. **本周**: 修复 P0 gaps (DFT-001/002/003)
2. **下周**: 设计 headless mode 方案 (借鉴 gsd-2)
3. **下月**: 建立自动化测试流水线
4. **长期**: 逐步引入 schema-based 定义系统

---

**Review 日期:** 2026-06-10  
**Review 范围:** 4 个项目完整对比  
**版本:** v2 (加入 Claude Code Haha)
