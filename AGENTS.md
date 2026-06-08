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
