# Engineering Metrics · Nuke AI Collaborator

> Last Updated: 2026-08-22

## 📊 Summary of Codebase Scale

Current tracked application code: **78,753 LOC**; tracked backend tests:
**48,071 LOC**. These figures exclude documentation and runtime workspace data
from the application-code ratio.

| Layer | Files | Est. LOC | % | Primary Responsibility |
| :--- | :--- | :--- | :--- | :--- |
| **Tracked application code** | — | **78,753** | 100% | Backend, frontend, and runtime application code |
| **Tests (Pytest)** | — | **48,071** | 61% of application code | Unit, integration, and E2E regression suites |

---

## 🔍 Engineering Health Analysis

### 1. Quality Lever (Test-to-Code Ratio)
- **Measured ratio: 0.61:1** (`48,071 / 78,753`).
- This is a line-count indicator, not a coverage percentage. Behavioral coverage
  still requires the full test suite and production-shaped integration tests.

### 2. Backend Complexity Centers
- **AI Core (tool_loop_v1.py)**: 806 lines. This is our state-machine based engine for tool-use.
- **Context Management (compact.py)**: 739 lines. Implements 5 levels of AI context compression.
- **Multi-Process Runtime (runtime/)**: High modularity; average file size < 200 lines.

### 3. Frontend Architecture
- **State Bloat Controlled**: Following the DFT-074 refactor, ChatWindow.jsx was reduced and modularized.
- **Component Density**: MemberList.jsx (755 lines) is currently the highest density component and a candidate for future extraction.

### 4. Knowledge Density
- **Physical Memory**: Over 9,500 lines are dedicated to Bot identity and skills, stored as Markdown files. This ensures that Bot "knowledge" is decoupled from runtime compute and persisted across shard migrations.
