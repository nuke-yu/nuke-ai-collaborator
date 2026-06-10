# Nuke AI Collaborator - 架构对比 Review

> **对比对象:**
> 1. **当前项目** (nuke-ai-collaborator) - 群组式 AI 协作平台
> 2. **Claude Code Haha** (`/Users/Nuke/claude-code-haha-main`) - Claude Code 本地可运行版 (泄露源码)
> 3. **gsd-2** (`/Users/Nuke/gsd-2`) - Anthropic Claude Code 官方源码
> 4. **opencode** (`/Users/Nuke/opencode`) - 另一个 AI Agent 平台

---

## 1. 架构总览对比

### 1.1 进程模型

| 维度 | Nuke AI | gsd-2 | opencode |
|------|---------|-------|----------|
| **主入口** | `backend/main.py` (FastAPI) | `src/cli.ts` + `src/headless.ts` | TypeScript + Bun |
| **Worker 模型** | Python 多进程 (Supervisor 管理) | RPC 模式 child process | Effect-TS Layer 系统 |
| **MCP 处理** | 专用 Collector 进程 | Extension Registry | Plugin System |
| **通信** | IPC (Unix Domain Socket) | RPC Client/Session Manager | Effect Layers + Context |

### 1.2 群组隔离机制

**Nuke AI (当前项目):**
```python
# backend/main.py:34-77
# Supervisor 管理 central DB + Worker 进程池
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_central_db()  # 路由/成员/模板
    sup = Supervisor(addr, num_workers=num_workers)
    await sup.start()
```

**gsd-2:**
```typescript
// src/headless.ts:250+
// 每个 session 独立，通过 project-sessions 管理
export async function runHeadless(options: HeadlessOptions): Promise<void>
```

**opencode:**
```typescript
// packages/core/src/agent.ts:10+
// Agent V2 schema-based 系统
export const Info = Schema.Struct({
  name: ID,
  mode: Mode,  // "subagent" | "primary" | "all"
  permission: PermissionV2.Ruleset,
})
```

---

## 2. 关键设计差异

### 2.1 安全层设计

#### Nuke AI (当前项目)

**优势:**
- ✅ **HIL Gate**: Write 工具需要人工审批
- ✅ **两层 run_shell guard**: Regex + shlex tokenized
- ✅ **Secret Redaction**: 进模型前脱敏 (PEM/JWT/AWS AKID)
- ✅ **Subagent 权限衰减**: `derive_subagent_ruleset()`

**实现:**
```python
# backend/permissions/
# - HIL gate executes in Worker, not Collector
# - run_shell guard: regex + shlex tokenization
# - redaction.redact_secrets() before model context
```

**发现问题:**
- ⚠️ `mcp_proxy.py` 对无 `__` 命名空间的工具 HIL 判断静默失效
- ⚠️ MCP Collector 并发 `MCP_AUTH_START` 无 per-server 锁

#### gsd-2

**特点:**
- ✅ **Extension Validator**: 沙箱验证扩展权限
- ✅ **Security Overrides**: 细粒度权限控制
- ✅ **Policy System**: CLI-level policy enforcement

**实现:**
```typescript
// src/security-overrides.ts
// src/cli-policy.ts
```

#### opencode

**特点:**
- ✅ **PermissionV2.Ruleset**: Schema-based permission rules
- ✅ **Effect-TS Error Handling**: 类型安全错误传播
- ✅ **Plugin V2 System**: 沙箱化插件

---

### 2.2 MCP 处理

#### Nuke AI

**架构:**
```
Supervisor (bus) 
  ├── Worker (McpProxyProvider)
  └── MCP Collector (only real MCP connection)
```

**关键代码:**
```python
# backend/runtime/supervisor.py:165-173
# Worker 连接时获取 cached MCP schemas
if wid != ipc.protocol.MCP_COLLECTOR_ID and self._mcp_schemas is not None:
    await ipc.send_msg(writer, self._mcp_schemas)
```

**Known Gaps (docs/TOOL-LAYER-GAP-ANALYSIS.md):**
1. `mcp_bridge.py` 使用 `asyncio.get_event_loop()` → 应改 `get_running_loop()`
2. `mcp_proxy.py` 对无 `__` 命名空间的工具 HIL 判断静默失效
3. MCP Collector 并发 `MCP_AUTH_START` 无 per-server 锁

#### gsd-2

```typescript
// src/mcp-server.ts
// src/extension-registry.ts
// 扩展系统内建 MCP 支持
```

#### opencode

```typescript
// packages/plugin/src/tool.ts
// packages/llm/src/tool.ts
// Tool runtime with streaming support
```

---

### 2.3 实时通信

#### Nuke AI

**架构:**
```
WebSocket → Supervisor → Worker
             ↓
        Browser Fan-out
```

**关键实现:**
```python
# backend/main.py:183-195
class WSClientProxy:
    async def send(self, payload: dict):
        await manager.broadcast(self.group_id, payload)

# 单 group 单 proxy，避免重复广播
_group_proxies: dict[int, WSClientProxy] = {}
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

#### gsd-2

```typescript
// src/headless-events.ts
// 事件驱动，支持 JSONL streaming
```

#### opencode

```typescript
// packages/ui/src/pierre/worker.ts
// Web Worker for UI rendering
```

---

## 3. 代码质量分析

### 3.1 Nuke AI 优点

| 方面 | 评价 | 示例 |
|------|------|------|
| **文档化** | ⭐⭐⭐⭐⭐ | 详细注释 CELL-XX 编号 |
| **安全设计** | ⭐⭐⭐⭐ | HIL + redaction + permission decay |
| **架构清晰** | ⭐⭐⭐⭐⭐ | Supervisor/Worker/Collector 分离 |
| **测试覆盖** | ⭐⭐⭐ | 有 test 文件但可加强 |

**优秀实践:**
```python
# backend/runtime/supervisor.py:1-19
"""CELL-12: Supervisor routing/fan-out engine (Project-Cell Isolation V3).
The Supervisor is the single entry process...
"""
```

### 3.2 Nuke AI 可改进之处

#### 优先级 P0: 修复已知 gaps

```markdown
## DFT-001: mcp_bridge.py get_event_loop()
当前:
    loop = asyncio.get_event_loop()
应改为:
    loop = asyncio.get_running_loop()

原因：get_event_loop() 在 async 上下文可能返回错误的 loop
```

```markdown
## DFT-002: mcp_proxy.py HIL 判断失效
问题：无 `__` 命名空间的工具 HIL 判断静默失败
影响：write 工具未正确触发人工审批
```

#### 优先级 P1: 架构优化

**建议 1: 改进 routing cache**
```python
# backend/runtime/supervisor.py:306-319
# 当前：60 秒硬编码
self._routing_cache[group_id] = (wid, time.time() + 60.0)

# 建议：config-driven
CACHE_TTL = config.ROUTING_CACHE_TTL
```

**建议 2: Worker 重启策略**
```python
# backend/runtime/supervisor.py:85-112
# 当前：指数退避 1-60 秒
backoff = 1.0
backoff = min(backoff * 2, 60.0)

# 建议：可配置最大重启次数
MAX_RESTARTS = config.WORKER_MAX_RESTARTS
```

#### 优先级 P2: 测试加强

**缺失的测试:**
- [ ] MCP schema push 完整性测试
- [ ] Worker 重启场景测试
- [ ] HIL gate 并发测试
- [ ] Secret redaction 边界测试

---

## 4. 与 gsd-2/opencode 的对比总结

### 4.1 架构哲学差异

| 项目 | 哲学 | 优势 | 劣势 |
|------|------|------|------|
| **Nuke AI** | Process-isolation | 真正的故障隔离 | 进程间通信 overhead |
| **gsd-2** | RPC model | 灵活、可扩展 | 需要序列化开销 |
| **opencode** | Effect-TS | 类型安全、可组合 | 学习曲线陡峭 |

### 4.2 工具系统设计

**Nuke AI:**
```python
# Tool Router 策略
- builtin/skill/shell → tool_executor.execute()
- MCP → McpProxyProvider → Collector
```

**gsd-2:**
```typescript
// Extension Registry
// All tools go through unified extension system
```

**opencode:**
```typescript
// Plugin V2
// Schema-based tool definitions with streaming
```

### 4.3 安全模型对比

| 维度 | Nuke AI | gsd-2 | opencode |
|------|---------|-------|----------|
| **权限模型** | Rule-based + HIL | Policy-based | Schema-based |
| **沙箱** | Shell guard | Extension sandbox | Plugin V2 |
| **审计** | Trace ID + Structured Log | CLI events | Effect tracing |

---

## 5. 具体改进建议

### 5.1 立即修复 (本周)

1. **DFT-001**: `mcp_bridge.py` 改 `get_running_loop()`
2. **DFT-002**: `mcp_proxy.py` HIL 判断修复
3. **DFT-003**: MCP Collector per-server lock

### 5.2 短期优化 (1-2 周)

1. **Routing Cache 配置化**
   - 添加 `ROUTING_CACHE_TTL` config
   - 考虑 cache invalidation 策略

2. **Worker 监控增强**
   ```python
   # backend/runtime/supervisor.py
   # Add: health check endpoint per worker
   # Add: graceful shutdown timeout
   ```

3. **Secret Redaction 扩展**
   - 添加 Azure SAS tokens
   - 添加 GCP service account keys
   - 添加 SSH private keys

### 5.3 长期规划 (1-3 月)

1. **测试覆盖率提升**
   - 目标：80%+
   - 重点：MCP flows, Worker lifecycle, HIL gates

2. **性能优化**
   - 评估共享内存 IPC (vs UDS)
   - MCP schema cache 优化

3. **Feature parity with gsd-2/opencode**
   - Headless mode
   - Auto-restart with backoff (gsd-2 pattern)
   - Extension/plugin system (opencode pattern)

---

## 6. 从竞品学习的最佳实践

### 从 gsd-2 学习:

1. **Exit Codes 标准化**
   ```typescript
   EXIT_SUCCESS = 0
   EXIT_ERROR = 1
   EXIT_BLOCKED = 10  // 明确 blocked vs error
   EXIT_CANCELLED = 11
   ```

2. **Resume Sessions**
   ```typescript
   // 支持会话恢复，长时间任务不丢失
   --resume <session_id>
   ```

3. **Headless Mode**
   ```typescript
   // 脚本化运行，支持 CI/CD
   gsd headless auto <command> --json
   ```

### 从 opencode 学习:

1. **Effect-TS Layer System**
   - Dependency injection 类型安全
   - Effect composition for complex flows

2. **Schema-based Definitions**
   - 所有 config/tool/agent 用 Schema 定义
   - Runtime validation + compile-time safety

3. **Streaming Tool Results**
   - 分块返回工具结果，降低 memory pressure

---

## 7. 结论

**Nuke AI 的优势:**
- ✅ 清晰的进程隔离架构
- ✅ 完善的安全层 (HIL + redaction)
- ✅ 良好的文档和注释

**需要改进的:**
- ⚠️ 修复已知 gaps (DFT-001/002/003)
- ⚠️ 增加测试覆盖率
- ⚠️ 借鉴 gsd-2 的 headless/resume 模式
- ⚠️ 借鉴 opencode 的 schema-based 定义

**建议下一步:**
1. 优先修复 P0 gaps
2. 设计 headless mode 方案
3. 建立自动化测试流水线

---

**Review 日期:** 2026-06-10
**Review 范围:** 核心架构 + 对比分析
