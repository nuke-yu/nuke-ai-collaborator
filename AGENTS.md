# Nuke AI Collaborator — Agent Context

## Project Background (Read First)

This is a **group-based AI collaboration platform**. Core concept:

- The system is organized into isolated **Groups** — each group represents a project or team
- Each group has **1–2 real humans** + **multiple role-based Bots** (BA, Developer, QA, PM, etc.)
- **Groups are fully isolated**: independent Bot employees, independent conversation history, independent knowledge — no cross-group sharing
- Bots are the **most context-aware members** of their group — they participate in all discussions, accumulate domain knowledge, and remember decisions
- Humans trigger Bots via @ mentions or direct messages; Bots can also collaborate with each other and spawn sub-Agents

Think of it like Slack or WeChat groups, but with AI teammates who can write code, run tools, and remember project context.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · FastAPI · aiosqlite · SQLite · JWT |
| Frontend | React 19 · Vite · Tailwind CSS v4 |
| Real-time | WebSocket (UDS/IPC inter-process) |
| AI Models | DeepSeek · OpenAI · Anthropic Claude · Ollama |

---

## Runtime Architecture (Process Topology)

```
main.py (FastAPI / WS entry)
    └── Supervisor process
            ├── Worker process × N     ← each Worker serves several groups' AI loops
            └── MCP Collector process  ← the ONLY process that holds MCP server connections
```

- **Supervisor**: routes WS messages, relays MCP_CALL/MCP_RESULT, manages Worker/Collector lifecycle
- **Worker**: runs tool_loop_v1 (AI inference + tool execution); MCP tools are proxied to the Collector via bus
- **MCP Collector**: sole owner of real MCP stdio/remote connections; pushes schemas to all Workers; executes pre-authorized tool calls

---

## Key Design Decisions (Must Understand Before Changing Code)

### 1. ToolRouter Routing Policy
- **builtin / skill / shell** tools: stay on `tool_executor.execute()` so before-hooks (permission check + shell danger guard) always fire
- **MCP tools**: not in tool_executor's registry — routed via `McpProxyProvider → mcp_bridge → Collector`
- **Do NOT** register Skill/Shell Providers in ToolRouter — they are unreachable for execution and cause schema leaks

### 2. MCP Single-Process Principle
- MCP connections can ONLY live in the Collector process (anyio cancel scopes bind to the creating task; crossing processes/tasks raises RuntimeError)
- Workers only have McpProxyProvider (a thin proxy); they must never directly hold McpClientToolProvider

### 3. Security Layers
- **HIL gate**: write-class tools require human approval (enforced on Worker side; Collector runs pre-authorized calls only)
- **run_shell guard**: two layers — regex blocks high-severity commands + shlex tokenized layer defeats evasion (base64 -d / curl|bash / eval, etc.)
- **Output redaction**: tool results are passed through `redaction.redact_secrets()` before entering the model context (PEM/JWT/AWS AKID/GitHub tokens, etc.)
- **Sub-Agent permission attenuation**: `derive_subagent_ruleset()` ensures bypassPermissions does not propagate down and blanket high-risk allows are dropped

### 4. Group Isolation
- Each group has its own SQLite group DB (central DB only stores user/group metadata)
- Bot skills, memory, and permission rules are all isolated per group

---

## Engineering Quality & Architecture Principles (Architect Directives)

### 1. 架构三问与全局视角 (Architectural Pre-Check)
在动笔编码前，必须显式走查：
- **进程拓扑**：代码运行在 Supervisor、Worker 还是 MCP Collector？跨进程数据汇总必须通过结构化 IPC 字典/增量（如 `obs_metrics_snapshot`），严禁跨进程直接拼接原始 Exposition 文本。
- **事务与副作用**：遥测发送（OTel/Prometheus）、外部通知与状态变更必须置于 SQLite 事务提交（`await db.commit()`）之后，严禁在数据清洗/预览函数（如 `prepare_payload()`）内触发遥测。
- **安全脱敏边界**：数据在进入存储或链路追踪前，必须强制经过 `redact_secrets()` 屏蔽敏感词（Token/Key/PEM/Password），并做字符串长度截断。

### 2. 契约优先与防御性编码 (Contract & Defensive Coding)
- **拒绝凭空假设签名**：引用项目 Helper 函数（如 `redact_secrets`）前，必须确认确切返回类型（如处理返回 `(text, count)` 元组的情况）。
- **输入规范化**：入口处必须做规范化清洗（如 Trace ID 必须剥离 UUID 连字符强制转换为符合 W3C 的 32 位 Hex 字符串；支持 tuple/list 自动解包）。
- **非阻塞异步防线**：任何遥测、日志导出或网络 HTTP 请求，严禁阻塞 Worker 的主 asyncio 事件循环（必须使用 `run_in_executor` 后台线程池与 Drop Counter）。

### 3. 真实边界与破坏性测试 (Destructive & Production-Real Testing)
- **必须注入真实敏感数据**：单元测试必须包含真实敏感字符串（如 `Authorization: Bearer ...`），验证脱敏引擎真正生效。
- **必须模拟极限与失败场景**：测试 Buffer 蓄满（如 1000 条 `flush()`）、网络导出失败（HTTP 500 / 超时）、字符串转义（`\`, `"`, `\n`）等边界。

### 4. 独立可检出的原子 Commit (Bisect-Safe Commits)
- 保证每个 Commit 独立可检出、独立测试 100% 绿色。
- 先提交底层独立模块及其测试，再提交依赖上层的应用接入代码。严禁在旧 Commit 的 `__init__.py` 中提前引用后续 Commit 才创建的模块。

---

## Known Open Issues

See [`docs/TOOL-LAYER-GAP-ANALYSIS.md`](docs/TOOL-LAYER-GAP-ANALYSIS.md). Highest-priority unresolved items:
- `mcp_bridge.py` uses `asyncio.get_event_loop()` (should be `get_running_loop()`)
- `mcp_proxy.py` HIL check silently fails for tool names without `__` namespace separator
- MCP Collector has no per-server lock for concurrent `MCP_AUTH_START` frames

---

## Conventions

### Git commits
- **Do not** add `Co-Authored-By: Claude ...` or any AI attribution to commit messages
- Commits should only show author = `nuke`
- Keep commit messages clean — describe only the change itself

> Backend-specific conventions (test cadence, etc.) are in [backend/CLAUDE.md](backend/CLAUDE.md).
