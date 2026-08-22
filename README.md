<div align="center">

# 🚀 Nuke AI Collaborator

### An Operating System for Your Resident AI Engineering Team
### *A resident, tireless, and ever-evolving multi-agent collaboration platform*

<p align="center">
  <b>English</b> |
  <a href="./README_CN.md"><b>简体中文</b></a>
</p>

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg?logo=python&logoColor=white&style=flat-square)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19.0-61DAFB.svg?logo=react&logoColor=black&style=flat-square)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white&style=flat-square)](https://fastapi.tiangolo.com/)
[![Tailwind CSS v4](https://img.shields.io/badge/Tailwind_CSS-v4.0-38B2AC.svg?logo=tailwind-css&logoColor=white&style=flat-square)](https://tailwindcss.com/)
[![MCP Native](https://img.shields.io/badge/MCP-Protocol_Native-8A2BE2.svg?style=flat-square)](https://modelcontextprotocol.io/)
[![Measured Test Ratio](https://img.shields.io/badge/Measured_Test_Ratio-0.61%3A1-informational.svg?style=flat-square)](docs/decisions/ENGINEERING-METRICS.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](https://github.com/nuke-yu/nuke-ai-collaborator/pulls)

<br/>

<img src="home-page.png" width="100%" alt="Nuke AI Collaborator Home Page" />

<br/>

> **💡 Core Vision**: **Nuke AI Collaborator** is an open-source, group-based multi-agent collaboration platform. By combining a modern Slack-like chat interface with structured multi-agent execution pipelines, it enables human engineers and specialized AI agents (BA, Developer, QA, PM, DevOps) to work together seamlessly within **physically isolated, auditably governed, and constantly appreciating digital workspaces**.

</div>

---

## 🆕 Latest Updates — 2026-08-21

- **Isolated Code Mode**: `run_code` is now organized as an independent bounded context with explicit domain, application, ports, adapters, and composition layers.
- **Subprocess execution**: Code Mode scripts run in disposable subprocesses; workspace and Bash operations return through parent-mediated IPC and the existing authorization chain.
- **Stronger execution failure handling**: Child-process EOF/pipe failures are converted into controlled Code Mode rejections instead of leaking transport exceptions.
- **Structured tool results**: Tool errors use explicit `ToolResult` status values, eliminating false failures when normal user output begins with `[error]` or similar text.
- **Bounded output and observation safety**: Spill slices are read line-by-line, file versions use chunked SHA-256 hashing, and observation stores can be injected per runtime context.
- **Scoped plugin architecture**: Plugin disposer lifecycles and dependency bindings are isolated by context/composition; executor dependency dictionaries are instance-owned.
- **Complete storage contract**: Storage adapters now expose connection, serialized transaction, migration, health-check, and lifecycle capabilities, with a concrete SQLite adapter.

These changes are documented in [Runtime Features Architecture](docs/runtime-features-architecture.md).

---

## 🎬 Product Demo Videos

### 📹 Demo 1: Nuke AI Collaborator Platform Walkthrough

<div align="center">
  <video src="https://github.com/user-attachments/assets/4510ce20-7577-40e6-89b6-444c6cd17136" controls="controls" width="100%"></video>
  <p><i>▶️ Live walkthrough of group collaboration, multi-role AI team workflows, and pipeline relays</i></p>
</div>

<br/>

### 🐝 Demo 2: Nuke AI Swarm Multi-Agent Swarm Orchestration

<div align="center">
  <video src="https://github.com/user-attachments/assets/e834a604-c05e-4d69-a40c-a6a61a89a0b1" controls="controls" width="100%"></video>
  <p><i>▶️ Multi-agent concurrent pipeline orchestration and task relay in action</i></p>
</div>

---

## 🎯 Core Value: A Digital Team That Appreciates Over Time

Nuke AI Collaborator is designed around **organizational collaboration**, **cognitive memory compounding**, and **enterprise-grade safety governance**:

```
                    ┌──────────────────────────────────────────────┐
                    │          👥 Real Humans (PM / Lead)          │
                    └──────────────────────┬───────────────────────┘
                                           │ @Breakdown / @All
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 💬 Group Workspace (Group Private Domain · Physical SQLite & Process Sandboxing)       │
│                                                                                        │
│   ┌────────────────┐      ┌────────────────┐      ┌────────────────┐                   │
│   │ 📋 AI-BA Lead  │ ───► │ 💻 AI-Dev Spec │ ───► │ 🧪 AI-QA Eng   │                   │
│   └───────┬────────┘      └───────┬────────┘      └───────┬────────┘                   │
│           │                       │                       │                            │
│           └───────────────────────┼───────────────────────┘                            │
│                                   ▼                                                    │
│               📌 Shared Board & Artifacts (BOARD.md / SPEC.md)                         │
│                                   │                                                    │
│           ┌───────────────────────┴───────────────────────┐                            │
│           ▼                                               ▼                            │
│ 🧠 Cognitive Memory Engine (A-MEM)              🛡️ Enterprise Security Mesh                 │
│ ├─ Episodic Extraction ➔ Semantic Reflection   ├─ Write / Shell Human-in-the-Loop (HITL)    │
│ ├─ 3-Factor Dynamic Ranking (Rel+Time+Imp)     ├─ AST Token Dual-layer Shell Guard          │
│ └─ Provenance Chain + Conflict Resolution      └─ Secret Redaction (PEM/JWT/API Keys)       │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1. 🧠 Cognitive Memory Compounding (Industrial A-MEM)
- **Episodic Extraction to Semantic Reflection**: Moves beyond naive text concatenation by converting raw conversation logs into structured episodic experiences and distilled semantic concepts.
- **3-Factor Dynamic Retrieval**: Multi-factor scoring across **Semantic Relevance + Time Decay Recency + Decision Importance**, coupled with automated conflict resolution so memories never degrade.
- **Traceable Lineage & Redaction**: Every preserved memory piece carries an A-MEM provenance trail, stripped of sensitive tokens before storage.

### 2. 👥 Multi-Agent Relay & Structured Workflows
- **Specialized Role Personas**: Out-of-the-box templates for Business Analysts, Fullstack Developers, QA Engineers, and Agile PMs. Fully customizable via `SOUL.md` (values) and `AGENT.md` (reasoning boundaries).
- **Sequential & Graph Pipelines**: Seamless stage handoffs (e.g., BA generates requirements → Dev implements code → QA generates test suites).
- **Live Observability**: Collapsible **Thinking Sections**, real-time **ReAct Action Tracking**, and an interactive **Execution Timeline Drawer**.

### 3. 🛡️ Enterprise Security Mesh & Human-in-the-Loop (HITL)
- **Interactive Approval Gates**: High-risk operations (code writes, file modifications, shell executions, deployments) trigger interactive approval cards in the UI for explicit human **Approve / Reject**.
- **Dual-Layer Anti-Evasion Shell Guard**: Regex-based perimeter blocks combined with a `shlex` AST tokenized parser that neutralizes Base64 obfuscation and pipe-to-shell evasion tactics.
- **Dynamic Secret Redaction**: Automatically scrubs PEM keys, JWTs, AWS credentials, and GitHub tokens before outputs enter LLM context or logs.
- **Sub-Agent Permission Attenuation**: Enforces strict downward privilege boundaries when parent agents spawn sub-tasks.

#### 📊 Code Editing Security Gate & Safety Mesh Assessment

> **💡 What is a Code Editing Security Gate?**  
> A **Code Editing Security Gate** is a mandatory, multi-layered security verification, permission control, and outcome validation mechanism enforced by the system whenever an AI agent attempts to read, modify, overwrite, or commit codebase files. It acts as an automated blast wall preventing AI models from destroying code repositories due to hallucinations, guesses, line drift, or prompt injection exploits.

##### ❓ Why LLMs Require Code Editing Safety Gates
Allowing an LLM to freely invoke file read/write tools without security guardrails inevitably triggers 5 major failure scenarios:
1. **Unobserved Blind Overwrite**: The LLM hasn't read the file's latest content, hallucinating logic and invoking `write_file` to replace a 1000-line core module with 20 lines of imaginary code.
2. **Line Drift & Misalignment**: The LLM attempts to replace line 45, but code insertions shifted the target to line 58. Lacking anchor validation, it deletes unrelated code at line 45, breaking syntax.
3. **Secret Leakage**: The LLM accidentally hardcodes API keys, JWT tokens, or private PEM keys into public commits or test logs during code generation.
4. **Untested Broken Code Admission**: The LLM claims "task completed" and attempts to merge changes directly into main, despite syntax errors or breaking unit tests.
5. **Command Evasion & Privilege Escalation**: Poisoned context induces the LLM to execute `rm -rf /` or obfuscated `base64 -d | sh` pipe commands to bypass security filters.

##### 🛡️ 3-Phase 6-Layer Closed-Loop Security Gate Architecture
```
                     【Autonomous Code Editing Security Gate Mesh】

 ┌───────────────────┐    ┌────────────────────┐    ┌────────────────────┐
 │ 1. Pre-Edit Gates │ ➔ │ 2. Mid-Edit Gates  │ ➔ │ 3. Post-Edit Gates │
 └───────────────────┘    └────────────────────┘    └────────────────────┘
   • Read-Before-Mutate     • Hashline Anti-Drift     • Automated Test Evidence
   • Interactive HIL Gate   • Diff Syntax Validation   • Secret Redaction Filter
   • Git Worktree Isolation • AST Token Shell Parser   • PR Gate Admission Block
```

```
================================================================================
🏛️ Nuke AI Collaborator Autonomous Coding Safety Mesh Assessment
--------------------------------------------------------------------------------
✅ 1. Git Worktree Isolation:      Closed Loop (git_worktree.py · Promote/Discard)
✅ 2. High-Precision Atomic Edit:  Closed Loop (editing/ · Hashline Anti-Drift)
✅ 3. Automated Test Evidence:     Closed Loop (Outcome Evidence · Verified Gates)
✅ 4. Fenced Lease & Stuck Guard:  Closed Loop (pipeline.py · Auto-Renewal & Fuses)
✅ 5. Secret Redaction & Spill:    Closed Loop (redaction.py · Automatic Token Scrubbing)
✅ 6. PR Gate Admission Guard:     Closed Loop (coding_agent.py · Missing PR Blocked)
================================================================================
```

### 4. 🏰 Physical Multi-Tenant Isolation & Native MCP
- **True Physical Group Isolation**: Each group operates on its own dedicated SQLite database (`workspaces/group_X/chat.db`) and file workspace, guaranteeing zero cross-group contamination.
- **Native MCP (Model Context Protocol) Architecture**: A dedicated MCP Collector process maintains Stdio/SSE connections, while Worker processes proxy tool calls efficiently across IPC.
- **Zero Vendor Lock-in**: Mix and match models across DeepSeek, Anthropic Claude, OpenAI, and local Ollama freely per bot.

---

## 💎 Features Overview

### 💬 Seamless Group Chat & Rich Media
- **Modern Architecture**: Built with React 19, Vite, and Tailwind CSS v4 for instantaneous page switching and rendering.
- **Comprehensive Markdown Rendering**: Tables, lists, quotes, and foldable Prism code blocks with one-click copy.
- **Rich Media & Collaboration**: Drag-and-drop file upload, lightbox image preview, multi-pinned messages, message edits/recalls/drafts, emoji reactions, and `⌘K` global search.
- **Real-Time Presence**: Online status indicators and automated custom offline auto-replies.

### 📚 4-Tier Self-Evolving Skill System
- **Tiered Skill Architecture**: `System Skills` + `Group Skills` + `Role Skills` + `External Skills`.
- **Governed Self-Learning Loop**: Agents extract recurring methodologies from execution logs to draft new skills (`Draft`). Drafts must pass human review and approval before graduating to active status.

### ⏰ Integrated Cron Scheduler
- **Standard Cron Syntax**: Built on APScheduler supporting 5-part cron expressions.
- **Automated Operations**: Configure automated daily standups, repository health audits, and scheduled sprint summary reports.

Memory capability wiring is tracked in
[`docs/decisions/MEMORY-CAPABILITY-STATUS.md`](docs/decisions/MEMORY-CAPABILITY-STATUS.md).
An implemented adapter is not automatically enabled on the default production path.

---

## 🏗️ System Architecture & Process Topology

Nuke AI Collaborator utilizes a **Microkernel + Process Sharding + Event Bus** topology:

```
                              ┌────────────────────────────────────────┐
                              │            Web Browsers (UI)           │
                              └───────────────────┬────────────────────┘
                                                  │ WebSocket / REST API
                                                  ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 🛡️ Supervisor Master Process (main.py)                                                                 │
│ ├─ WebSocket Handshake & JWT Authentication · Central Routing                                         │
│ ├─ Distributed W3C Trace Propagation (trace_id) · Structured JSON Logging                              │
│ └─ Worker / Collector Lifecycle Supervision & Health Probes                                            │
└───────────────────────┬────────────────────────────────────────────────┬───────────────────────────────┘
                        │ IPC (UDS / Named Pipes, P99 < 0.2ms)           │ IPC
                        ▼                                                ▼
┌────────────────────────────────────────────────────────┐  ┌──────────────────────────────────────────┐
│ ⚙️ Worker Shard Processes × N (AI Loop & Inference)    │  │ 🔌 MCP Collector Process (Tool Hub)      │
│ ├─ Manages isolated group tool_loop_v1 and state logic │  │ ├─ Exclusively maintains Stdio/SSE MCP   │
│ ├─ 29-Type Event Bus decoupling transport & execution  │  │ ├─ Synchronizes tool schemas & auth      │
│ ├─ HITL Permission checks & Shell security enforcement │  │ └─ Executes pre-authorized tool calls    │
│ └─ Private SQLite storage (workspaces/group_X/)        │  └──────────────────────────────────────────┘
└────────────────────────────────────────────────────────┘
```

---

## 🧠 Memory: A Team Memory System That Compounds Over Time

Nuke Memory is not a raw transcript dump into a vector database. It is built around **Group isolation, selective distillation, evidence-based validation, temporal evolution, and secure projection**. Each Group owns an isolated knowledge and execution history; each Bot retains role-specific experience; personal knowledge stays in the Personal Vault and is projected into a selected Group or Bot only for an explicit purpose.

```text
Conversation / Tool Execution
          │
          ▼
Selective Observation ──► Fact / Summary / Reflection
          │
          ▼
Run ──► Case ──► Outcome Verification ──► Experience ──► Skill Candidate
                     │                         │                 │
                     └── Failure & correction evidence ──────────┘
                                                               ▼
                                                    Reuse Feedback / Evolution

Personal Vault ── explicit Projection ──► Group / Bot Context
```

### Open-Source Memory Design References

Nuke does not embed these projects as a second runtime. It studies their core mechanisms and implements them natively within the Group-first architecture, SQLite canonical storage, Chroma vector projections, Worker durable pipeline, HITL controls, and redaction boundaries.

| Reference project | Design and algorithms absorbed by Nuke | Nuke implementation focus |
|---|---|---|
| **Mem0** | Atomic fact extraction; `ADD / UPDATE / DELETE / NOOP` memory operations; idempotent writes and history retention | Conversational facts, conflict resolution, supersede history, and selective memory distillation |
| **EverOS** | `Run → Case → Experience → Skill` hierarchy | Nuke-native durable Case/Experience/Skill pipeline; not a complete EverOS OME/Markdown runtime |
| **AutoGen Task-Centric Memory** | Failure Insight extraction; linking correction outcomes to the original failure; persisting an Insight only after validation | Failure → correction → verification evidence chain for durable learning |
| **Graphiti** | Temporal knowledge and relation invalidation concepts | Optional temporal relation adapter; not the default hot-recall path |
| **Voyager** | Critic and success-gating concepts | Constrained declarative Skill candidates and execution plans; not an executable code Skill Library |
| **LangGraph** | Stateful workflows, checkpoint lineage, idempotent recovery, and durable execution | Memory Learning background jobs, stable Job identity, leases, failure recovery, and replay boundaries |
| **Letta / MemGPT** | Core Memory versus Archival Memory; context-window budgeting; on-demand retrieval of long-term knowledge | Separation of current context and long-term storage, with budgeted Experience/Personal Knowledge injection |
| **OpenMemory** | Personal Memory ACL, explicit sharing, access auditing, and export/delete lifecycle | Isolated Personal Vault, Group/Bot Projection, fail-closed ACL, usage auditing, and revocable projections |

### Nuke Memory Invariants

- **Physical Group isolation**: Group facts, Bot experiences, and learning jobs remain bound to the Group database; no automatic cross-Group sharing.
- **SQLite is canonical**: SQLite stores records, evidence, and state; Chroma and Workspace files are rebuildable projections.
- **Learning follows verification**: Model self-evaluation alone is insufficient; tool results, tests, or task acceptance signals must establish Outcome Evidence.
- **History is never silently overwritten**: Conflicting knowledge retains temporal validity, supersede relations, and provenance.
- **Personal knowledge is explicitly projected**: Personal Vault records enter Group/Bot context only after ACL checks for the target, actor, and purpose.
- **Security precedes persistence**: Secret Redaction and bounded-length handling run before data enters storage, model context, or tracing.

See [Memory System Design](docs/decisions/MEMORY-SYSTEM-DESIGN.md), [Memory Algorithm Upgrade Baseline](docs/decisions/memory-%E7%AE%97%E6%B3%95%E5%8D%87%E7%BA%A7.md), and [Agent Self-Learning Research Notes](docs/agent-self-learning-research-notes.md) for the detailed design and implementation boundaries.

---

## ⚡ Quick Start

### Option 1: One-Click Startup Script (Recommended)

- **macOS / Linux**:
  ```bash
  git clone https://github.com/nuke-yu/nuke-ai-collaborator.git
  cd nuke-ai-collaborator
  chmod +x start.sh
  ./start.sh
  ```

- **Windows (PowerShell / CMD)**:
  ```powershell
  git clone https://github.com/nuke-yu/nuke-ai-collaborator.git
  cd nuke-ai-collaborator
  .\start.bat
  ```

Open **`http://localhost:5173`** in your browser to start collaborating!

---

### Option 2: Docker Compose

```bash
# 1. Prepare persistent workspace directory
sudo mkdir -p /var/lib/nuke-ai-collaborator/workspaces
sudo chown -R "$(id -u):$(id -g)" /var/lib/nuke-ai-collaborator

# 2. Launch with prebuilt multi-arch images (amd64 / arm64)
docker compose -f docker-compose.ghcr.yml up -d

# 3. Access http://localhost:8000 (Configure API keys in UI via the 🔑 button)
```

---

### Option 3: Manual Installation

<details>
<summary><b>Click to expand manual setup details (Python 3.12+ & Node.js 18+)</b></summary>

#### 1. Backend Setup
```bash
cd backend
python3 -m venv venv

# macOS / Linux:
source venv/bin/activate
# Windows:
# venv\Scripts\activate

pip install -r requirements.txt
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

#### 2. Frontend Setup
```bash
# In a new terminal window
cd frontend
npm install
npm run dev
```

Visit `http://localhost:5173` to register your admin user and spawn your first AI team.
</details>

---

## 🎯 Typical Use Cases

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 💡 Scenario 1: End-to-End Feature Delivery Relay                             │
│ Human PM: "@BA create PRD for WeChat OAuth login and update the board"      │
│   ➔ AI-BA creates specification document and updates `BOARD.md`             │
│   ➔ AI-Dev picks up the task and drafts frontend & backend implementations  │
│   ➔ Triggers HITL modal: Human approves code write to local repository      │
│   ➔ AI-QA generates unit test suites and validates test execution           │
├─────────────────────────────────────────────────────────────────────────────┤
│ 💡 Scenario 2: Smart Operations via Native MCP Bridge                       │
│ Cron Scheduled / Human @DevOps: "Inspect production pods and error logs"     │
│   ➔ Queries live Kubernetes & Postgres tools via MCP bridge                 │
│   ➔ Pinpoints root cause stack traces and suggests pull-request fixes       │
├─────────────────────────────────────────────────────────────────────────────┤
│ 💡 Scenario 3: Compounding Team Knowledge Base                              │
│ New Hire: "@All What is the historical context for the payment retry logic?"│
│   ➔ AI queries group cognitive memory (A-MEM) and accurately summarizes     │
│     architectural decisions and edge cases from past sprints                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🤝 Contributing & Community

Nuke AI Collaborator is a fast-evolving open-source project. We welcome developers, designers, prompt engineers, and AI researchers to join us in shaping the future of multi-agent software engineering:

### 🌈 Areas to Contribute:

- 🎨 **Frontend UI/UX**: Creative themes, micro-animations, interactive visual workflow canvas, mobile PWA optimization.
- 🤖 **Agent Personas & Skills**: Specialized Bot prompt templates (Data Analyst, Security Auditor, UI Designer) and custom MCP tool adapters.
- 🧠 **Cognitive Memory**: Ranking algorithms, memory clustering, decay optimizations, and Knowledge Graph integration.
- 🔌 **Enterprise Integrations**: Bidirectional Webhook gateways for Slack, Teams, Lark / Feishu, DingTalk, and SSO support.

### 🛠️ Development Guidelines:

1. **Fork** the repository and clone your fork.
2. Create a feature branch: `git checkout -b feature/awesome-feature`
3. Implement your changes and verify tests (`pytest` & `npm test`).
4. Commit with clear, atomic commit messages:
   ```bash
   git commit -m "feat(skills): add docker-management skill"
   ```
5. Push to your branch and open a **Pull Request**!

---

## 🗺️ Roadmap

- [x] **v1.0 Foundational Sharding**: Supervisor-Worker runtime, EventBus, group physical isolation
- [x] **v2.0 Cognitive Memory & MCP**: A-MEM memory lineage, native MCP bridge collector, L4 governed skill evolution
- [x] **v2.5 Enterprise Hardening**: HITL interactive approval gates, dual-layer shell protection, secret redaction, chaos recovery
- [ ] **v3.0 Advanced Collaboration (In Progress)**:
  - [ ] Visual multi-agent pipeline drag-and-drop workflow editor
  - [ ] Lark / Feishu / Slack bidirectional bot gateway
  - [ ] Graph-enhanced memory network (Graph Memory)
  - [ ] One-click export of group intelligence into standalone agentic skill packages

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

---

<div align="center">

**Empower every engineering team with a resident, tireless, and ever-evolving AI workforce.**

<br/>

🌟 **If you find Nuke AI Collaborator helpful or inspiring, please give us a Star on GitHub!** 🌟

[Report Bug / Request Feature](https://github.com/nuke-yu/nuke-ai-collaborator/issues) · [Submit Pull Request](https://github.com/nuke-yu/nuke-ai-collaborator/pulls) · [Read Architecture Specs](docs/ARCHITECTURE.md)

</div>
