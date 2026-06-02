# Engineering Metrics · Nuke AI Collaborator

> Last Updated: 2026-06-01

## 📊 Summary of Codebase Scale

Current total Lines of Code (LOC): **~38,510** (including docs and tests).

| Layer | Files | Est. LOC | % | Primary Responsibility |
| :--- | :--- | :--- | :--- | :--- |
| **Backend Core** | ~60 | **7,800+** | 20% | AI loop, orchestrator, split-DB logic, runtime |
| **Tests (Pytest)** | ~55 | **11,500+** | 30% | Unit, integration, and E2E regression suites |
| **Frontend (React)**| ~25 | **4,200+** | 11% | Real-time UI, WebSocket hooks, state management |
| **Architectural Docs**| ~15 | **5,500+** | 14% | PRDs, design specs, V3 sharding blueprints |
| **Bot Knowledge** | ~100 | **9,500+** | 25% | Skill definitions, memory, identity profiles |

---

## 🔍 Engineering Health Analysis

### 1. Quality Lever (Test-to-Code Ratio)
- **Ratio: 1.5:1**
- Our testing suite (11,500+ LOC) significantly outweighs the core backend logic (7,800+ LOC). This rigorous coverage is what allowed us to execute the "V3 Sharding Universe" and "DI Unification" refactors with near-zero regressions.

### 2. Backend Complexity Centers
- **AI Core (tool_loop_v1.py)**: 806 lines. This is our state-machine based engine for tool-use.
- **Context Management (compact.py)**: 739 lines. Implements 5 levels of AI context compression.
- **Multi-Process Runtime (runtime/)**: High modularity; average file size < 200 lines.

### 3. Frontend Architecture
- **State Bloat Controlled**: Following the DFT-074 refactor, ChatWindow.jsx was reduced and modularized.
- **Component Density**: MemberList.jsx (755 lines) is currently the highest density component and a candidate for future extraction.

### 4. Knowledge Density
- **Physical Memory**: Over 9,500 lines are dedicated to Bot identity and skills, stored as Markdown files. This ensures that Bot "knowledge" is decoupled from runtime compute and persisted across shard migrations.
