# Nuke AI Collaborator

A web-based AI collaboration workspace featuring multi-group chat, AI Bot members, real-time WebSocket communication, and an interactive experience similar to Slack or WeChat groups.

---

## 📊 Codebase Metrics

Current Project Scale: **~38,500 LOC**
Backend Test Coverage: **1.5:1 (Test-to-Code ratio)**

For a detailed breakdown by layer and engineering health analysis, see [ENGINEERING-METRICS.md](docs/ENGINEERING-METRICS.md).

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · FastAPI · aiosqlite · SQLite · JWT |
| Frontend | React 19 · Vite · Tailwind CSS v4 |
| Real-time | WebSocket |
| AI Integration | DeepSeek · OpenAI · Anthropic Claude · Ollama |

---

## Features

### 💬 Messaging

- **Markdown Rendering** — Support for headers, lists, tables, quotes, and inline code.
- **Code Blocks** — Foldable/expandable blocks with syntax highlighting (Prism) and one-click copy.
- **File & Image Upload** — Click to upload, drag-and-drop, or paste images directly.
- **Image Preview** — In-chat thumbnail display with full-screen Lightbox (Esc to close).
- **Message Replies** — Threaded replies with quoted content summaries.
- **Message Editing** — In-place editing with "Edited" markers.
- **Message Recall** — Soft deletion with "This message was recalled" markers.
- **Pinned Messages** — Top-mounted Pin bar supporting multiple pins with real-time sync.
- **Message Drafts** — Drafts automatically saved when switching groups and restored upon return.
- **@ Mentions** — Auto-complete member selector, supporting `@all`.
- **Message Search** — Keyword highlighting and jump-to-location functionality.
- **Time Grouping** — Date separators (Today / Yesterday / Specific Date).
- **Read Receipts** — Real-time indicators of which members have read a message.
- **Reactions** — Quick Emoji bar + Full Emoji selector (6 categories).

### 🤖 AI Bot

- **Multi-Model Support** — DeepSeek / OpenAI / Anthropic Claude / Ollama (Local).
- **Streaming Output** — Real-time typewriter effect.
- **Custom Personas** — Each Bot can have independent System Prompts and role descriptions.
- **Role Templates** — Built-in library to add common AI roles with one click.
- **Context Memory** — Independent conversation history for each group, fully isolated.

### 👥 Members

- **Online Presence** — Real-time green dot indicators; connected is online, disconnected is offline (multi-tab safe).
- **Auto-Reply** — Automatically trigger custom replies when @mentioned while offline.
- **Member Management** — Add/remove members with real-time sync of group counts.

### 🗂 Groups

- **Multi-Group Layout** — Sidebar listing with support for expanding multiple member sub-lists.
- **Group Announcements** — Top-mounted, collapsible announcement bar with real-time editing.
- **Empty State Guidance** — New groups display member avatars and "Send a message to start" prompts.
- **Unread Badges** — Visual indicators for unread message counts per group.
- **Group Renaming** — Click the name of a group to rename it.

### 🎨 Interface

- **Dark / Light Themes** — One-click toggle with Tailwind CSS variable-level inversion and smooth transitions.
- **Mobile Responsive** — Bottom Tab navigation (Groups / Chat) for mobile devices.
- **Flicker-Free Switching** — Local message caching for instant group switching with silent background refresh.
- **Infinite Scroll** — Smooth upward scrolling to load earlier history while maintaining position.
- **Keyboard Shortcuts** — `⌘K` / `Ctrl+K` to open search.
- **Typing Indicators** — Visual "thinking" animation when Bots are processing.

### 🛡️ Security & Robustness

- **End-to-End Authentication** — JWT-based protection for all endpoints, including secure registration/login and WebSocket handshake verification.
- **Multi-Process Sharding** — Supervisor-Worker architecture for horizontal scaling and physical fault isolation.
- **Auto-Reconnect Compensation** — Frontend automatically catches up on missing messages after WebSocket flickers, ensuring eventual consistency.
- **Distributed Tracing** — Cross-process `trace_id` propagation with structured JSON logging for easy debugging.
- **High-Performance Tunneling** — Low-latency IPC protocol based on UDS/Named Pipes (P99 < 0.2ms).
- **Memory Management** — VFS path lock cleanup mechanism to prevent leaks during long-term operation.

### 📁 File Support

| Type | Formats |
|---|---|
| Images | JPEG · PNG · GIF · WebP · SVG |
| Documents | PDF · Word (.doc / .docx) · Excel (.xls / .xlsx) |
| Text | TXT · JSON |

Maximum file size: **10 MB**. Images are displayed inline; other files show as download cards.

### ⏰ Scheduler (Cron Jobs)

- **Cron Scheduling** — Standard 5-segment cron expressions (`0 9 * * 1-5`), powered by APScheduler in the main event loop.
- **Pluggable Decoupling** — Independent `scheduler/` module; can be removed by deleting just 3 lines in `main.py`.
- **REST Management API** — CRUD operations + toggle + manual trigger (`POST /api/cron-jobs/{id}/run`).
- **Persistence** — Rules stored in `cron_jobs` table; automatically restored on reboot.

---

## Getting Started

### Requirements

- Python 3.11+
- Node.js 18+

### Backend Setup

```bash
cd backend
pip install -r requirements.txt
python main.py
```

### Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) and join to start.

---

## Architecture: Event Bus

The backend utilizes an internal Event Bus to decouple business logic from the WebSocket transport layer, inspired by the PubSub architecture of [OpenCode](https://github.com/opencode-ai/opencode).

### Layered Structure

```
Business Logic (Orchestrator / Permissions / Executors)
       ↓  bus.publish(TypedEvent)  /  bus.broadcast(group_id, dict)
EventBus (asyncio Queue, typed channel + wildcard channel)
       ↓  Wildcard Subscription
WS Adapter (The only part that knows about WSManager)
       ↓  manager.broadcast(group_id, payload)
WSManager (Connection registry, pure transport layer)
       ↓  ws.send_json × N
Browsers
```

### Event Types (29 Total)

| Category | Events |
|------|------|
| Streaming | `stream_start` · `stream_chunk` · `stream_error` · `stream_end` · `stream_aborted` |
| Messages | `message` · `read` |
| Presence | `presence` · `workflow_update` |
| Bot State | `typing` · `error` · `steer_queued` · `followup_start` · `steer_injected` · `rewake_injected` |
| Tool Execution | `tool_call` · `tool_result` |
| ReAct | `react_thought` · `react_action` · `react_observation` |
| Compaction | `compaction` |
| Skills | `skills_loaded` · `skill_fork_start` · `skill_fork_end` · `skill_draft_added` |
| Permissions | `before_finalize_review` · `before_finalize_approved` · `before_finalize_rejected` · `permission_asked` |

---

## Project Structure

```
nuke-ai-collaborator/
├── backend/
│   ├── main.py              # Entry point, WS Handshake, Central Router
│   ├── core/
│   │   ├── auth.py          # JWT Auth, Password Hashing
│   │   ├── config.py        # Centralized Magic Numbers & Thresholds
│   ├── runtime/             # V3 Sharding Runtime
│   │   ├── supervisor.py    # Master Process (Routing/Fan-out)
│   │   ├── worker.py        # Shard Process (AI Loop/Pumps)
│   │   ├── lifecycle.py     # Lazy Hydration & Eviction Management
│   │   ├── tracing.py       # Distributed Tracing & JSON Logging
│   ├── bus/                 # Event Bus (Decoupling Layer)
│   ├── db/                  # Multi-Domain Database (Central + Group Private)
│   ├── executors/           # AI Executor Plugins (Tool Loop, ReAct)
│   ├── permissions/         # Permission Engine
│   ├── scheduler/           # APScheduler Plugin
│   └── api/                 # REST Routers (Groups, Messages, Auth)
└── frontend/
    └── src/
        ├── components/      # UI Components (ChatWindow, MessageList, etc.)
        ├── hooks/           # React Hooks (useWebSocket, useNotifications)
        └── api.js           # Auth-aware REST API Wrapper
```

---

## License

MIT
