<div align="center">

# 🚀 Nuke AI Collaborator

### An Operating System for Your Resident AI Engineering Team
### *让每个组织都拥有一支常驻、协同、越用越懂你的数字研发团队*

<p align="center">
  <a href="./README_EN.md"><b>English</b></a> |
  <a href="./README.md"><b>简体中文</b></a>
</p>

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg?logo=python&logoColor=white&style=flat-square)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19.0-61DAFB.svg?logo=react&logoColor=black&style=flat-square)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white&style=flat-square)](https://fastapi.tiangolo.com/)
[![Tailwind CSS v4](https://img.shields.io/badge/Tailwind_CSS-v4.0-38B2AC.svg?logo=tailwind-css&logoColor=white&style=flat-square)](https://tailwindcss.com/)
[![MCP Native](https://img.shields.io/badge/MCP-Protocol_Native-8A2BE2.svg?style=flat-square)](https://modelcontextprotocol.io/)
[![Test Ratio](https://img.shields.io/badge/Test_Ratio-1.5:1-success.svg?style=flat-square)](docs/ENGINEERING-METRICS.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](https://github.com/nuke-yu/nuke-ai-collaborator/pulls)

<br/>

<img src="home-page.png" width="100%" alt="Nuke AI Collaborator Home Page" />

<br/>

> **💡 Core Vision**: **Nuke AI Collaborator** is an open-source, group-based multi-agent collaboration platform. By combining a modern Slack-like chat interface with structured multi-agent execution pipelines, it enables human engineers and specialized AI agents (BA, Developer, QA, PM, DevOps) to work together seamlessly within **physically isolated, auditably governed, and constantly appreciating digital workspaces**.

</div>

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
