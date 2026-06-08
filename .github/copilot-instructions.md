# Nuke AI Collaborator — Copilot Instructions

## Project Background

This is a **group-based AI collaboration platform**. Core concept:

- The system is organized into isolated **Groups** — each group represents a project or team
- Each group has **1–2 real humans** + **multiple role-based Bots** (BA, Developer, QA, PM, etc.)
- **Groups are fully isolated**: independent Bot employees, independent conversation history, independent knowledge — no cross-group sharing
- Bots are the **most context-aware members** of their group — they participate in all discussions, accumulate domain knowledge, and remember decisions
- Humans trigger Bots via @ mentions or direct messages; Bots can also collaborate with each other and spawn sub-Agents

Think of it like Slack or WeChat groups, but with AI teammates who can write code, run tools, and remember project context.

## Tech Stack

- **Backend**: Python · FastAPI · aiosqlite · SQLite · JWT
- **Frontend**: React 19 · Vite · Tailwind CSS v4
- **Real-time**: WebSocket (UDS/IPC inter-process)
- **AI Models**: DeepSeek · OpenAI · Anthropic Claude · Ollama

## Process Architecture

```
main.py (FastAPI / WS entry)
    └── Supervisor process
            ├── Worker process × N     ← AI inference + tool execution per group
            └── MCP Collector process  ← sole owner of all MCP server connections
```

## Critical Rules

1. **ToolRouter**: builtin/skill/shell tools must stay on `tool_executor.execute()` (for hooks); MCP tools go via `McpProxyProvider → mcp_bridge → Collector`. Never register Skill/Shell Providers in ToolRouter.
2. **MCP connections**: live ONLY in the Collector process. Workers use McpProxyProvider only.
3. **Security**: HIL gate for write-class tools, two-layer shell guard (regex + tokenized), output redaction for secrets, sub-agent permission attenuation.
4. **Group isolation**: each group has its own SQLite DB. No cross-group data sharing.

## Conventions

- No `Co-Authored-By` or AI attribution in commit messages
- Author is always `nuke`
- Backend test conventions: see `backend/CLAUDE.md`
