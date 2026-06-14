# Nuke AI Collaborator — AI Agent 上下文

## 项目背景（必读）

这是一个**群组式 AI 协作平台**。核心场景：

- 系统按**群组（Group）**隔离——每个群组对应一个项目或团队
- 每个群组里有 **1–2 个真人** + **若干角色 Bot**（BA、开发、测试、PM 等）
- **群组之间完全隔离**：独立的 Bot 员工、独立的对话历史、独立的知识库，不跨群共享
- Bot 是该群组里**最了解项目上下文的成员**——它们持续参与该群的所有讨论，积累领域知识
- 真人通过 @ 或直接发消息触发 Bot，Bot 之间也可以互相协作、spawn 子 Agent

简单类比：像 Slack/微信群，但群员里有会写代码、跑工具、记忆上下文的 AI 同事。

---

## 技术栈

| 层 | 技术 |
|---|---|
| Backend | Python · FastAPI · aiosqlite · SQLite · JWT |
| Frontend | React 19 · Vite · Tailwind CSS v4 |
| 实时通信 | WebSocket（UDS/IPC 进程间） |
| AI 模型 | DeepSeek · OpenAI · Anthropic Claude · Ollama |

---

## 运行时架构（进程拓扑）

```
main.py (FastAPI / WS 入口)
    └── Supervisor 进程
            ├── Worker 进程 × N     ← 每个 Worker 服务若干群组的 AI 循环
            └── MCP Collector 进程  ← 唯一持有 MCP server 连接的进程
```

- **Supervisor**：路由 WS 消息、中继 MCP_CALL/MCP_RESULT、管理 Worker/Collector 生命周期
- **Worker**：跑 tool_loop_v1（AI 推理 + 工具执行）；MCP 工具通过 McpProxyProvider 经 bus 转发给 Collector
- **MCP Collector**：唯一持有真实 MCP stdio/remote 连接；将 schema 推送给所有 Worker；执行 pre-authorized tool call

---

## 关键设计决策（改代码前必须理解）

### 1. ToolRouter 路由策略
- **builtin / skill / shell** 工具：留在 `tool_executor.execute()`，确保 before-hook（permission check + shell danger guard）必然触发
- **MCP 工具**：不在 tool_executor registry 里，走 `McpProxyProvider → mcp_bridge → Collector`
- **不要**把 Skill/Shell Provider 注册进 ToolRouter——它们是 unreachable 的，且会造成 schema 泄露

### 2. MCP 单进程原则
- MCP 连接只能活在 Collector 进程里（anyio cancel scope 与创建它的 task 绑定，跨进程/跨 task 会 RuntimeError）
- Worker 只有 McpProxyProvider（透传），不能直接持有 McpClientToolProvider

### 3. 安全层次
- **HIL 门**：write 类工具需人工审批（Worker 侧执行，Collector 侧只跑 pre-authorized 调用）
- **run_shell guard**：两层——regex 拦截高危命令 + shlex tokenized 层防绕过（base64 -d / curl|bash / eval 等）
- **输出脱敏**：tool result 进模型上下文前过 `redaction.redact_secrets()`（PEM/JWT/AWS AKID/GitHub token 等）
- **子 Agent 权限衰减**：`derive_subagent_ruleset()` 确保 bypassPermissions 不向下传播，blanket high-risk allow 被 drop

### 4. 群组隔离
- 每个群组有独立的 SQLite group DB（central DB 只存用户/群元数据）
- Bot 的 skill、memory、permission rules 都按 group 隔离

---

## 当前已知 Gap（未解决问题）

详见 [`docs/TOOL-LAYER-GAP-ANALYSIS.md`](docs/TOOL-LAYER-GAP-ANALYSIS.md)。

> 以下三条原始 gap 均已在 `7ef7793`（fix: address 4 bugs found in code review）处理，保留记录以备审计：
> - ✅ `mcp_bridge.py` `get_event_loop()` → `get_running_loop()`（已修）
> - ✅ `mcp_proxy.py` 无 `__` 命名空间工具名 HIL 静默失效 → 改为 fail-safe（`sep` 缺失即走审批/fail-closed，已修）
> - ⚠️ MCP Collector 并发 `MCP_AUTH_START`：已加 `_auth_inflight` per-server guard，但 full lock 仍是 follow-up（部分修复）

> ✅ MCP 审批默认改 fail-closed（本分支）：未声明审批策略的 server 现在 gate 全部工具，不再用工具名启发式分类（名字是 server 控制的、不可信）。operator 通过 `approval_tools: []` 显式信任整个 server。`_MCP_WRITE_TOOLS` 不再决定 HIL，仅余子 Agent 衰减用途。同时补齐 `mcp_proxy` 的 always 审批会话内即时性（镜像 workspace_tools）。

---

## 操作约定

### Git commit
- **不要**在 commit message 里添加 `Co-Authored-By: Claude ...` 或任何 AI 署名
- commit 只显示 author = `nuke`
- 保持 commit message 干净，只描述改动本身

> 后端专属约定（测试节奏等）见 [backend/CLAUDE.md](backend/CLAUDE.md)。
