# Bot Collaboration Logic & Flow Reshaping

> Date: 2026-06-02
> Status: Design Discussion / Conceptual Phase

This document records the analysis and future design directions for the "Collaborative" nature of the AI workspace. The goal is to move from a "Passive Chatbot" model to an "Autonomous Multi-Agent Collaboration" model.

---

## 1. Trigger Taxonomy: How Bots "Ignite"

We identified 4 existing and 2 missing trigger mechanisms to drive the collaborative flow:

### Existing Mechanisms
1.  **Manual Semantic Wake**: User sends a generic message; the system routes it to the most relevant Bot based on keyword/intent.
2.  **Explicit @Mention**: User forces a specific Bot to respond. (Currently sets a persistent "Session Lock").
3.  **Bot-to-Bot Handoff**: A Bot finishes a task (outputs a "done_keyword") and triggers the next Bot in a predefined pipeline.
4.  **Behavioral/Scheduled Triggers**: Triggers originating from Cron jobs, Jira events, or Git commits.

### Missing/Planned Mechanisms
5.  **Observer/Interceptor Trigger**: A silent Bot (e.g., Security Guard, Architect) monitors another Bot's output in real-time and interrupts if it detects a critical error/vulnerability.
6.  **Tool/System Feedback Wake**: A Bot initiates a long-running task (e.g., CI build), goes to sleep, and is re-awakened when the tool returns a result or an error (Webhook-style).

---

## 2. Current Architectural Friction Points

*   **Static Routing**: Relying on hardcoded Chinese keywords (e.g., "开发" in role) is fragile and doesn't scale.
*   **Hard Session Locks**: The @mention mechanism "locks" the channel to one Bot too aggressively, preventing natural interjections from other specialized Bots.
*   **Rigid Pipelines**: Workflows are pre-baked (A -> B -> C). Bots lack the agency to decide who should handle the next step or seek help.
*   **Lack of "Ambient" Awareness**: Bots only react to the final "stream_end". They cannot "watch" the tool-calling process of their colleagues to provide real-time assistance.

---

## 3. The "Collaboration Brain V4" Reshaping Plan

We proposed 4 strategies to evolve the orchestration engine:

### S1: Intelligent Intent Router
Replace regex matching with an **Intent Analyzer (LLM Router)**.
- For ambiguous messages, use a fast, cheap model to classify the intent against Bot capability profiles (Traits).
- **Goal**: Make "Manual Semantic Wake" feel natural and intelligent.

### S2: Contextual Focus (Soft Locks)
Replace binary session locks with **Decaying Weights**.
- An @mention gives a Bot 100% focus, but this weight decays over subsequent messages or shifts if the semantic context changes.
- **Goal**: Allow smooth conversational handoffs without manual @ re-tagging.

### S3: Delegate / Handoff Tools
Empower Bots with a system tool: "call_colleague(name, reason)".
- Moves from "Static Pipelines" to "Dynamic Discovery". Bots can decide to loop in a Peer (e.g., Dev calling QA) autonomously.
- **Goal**: Realize a truly autonomous Multi-Agent System (MAS).

### S4: Ambient Listening Mode (Observer Mode)
Enable specialized Bots to subscribe to "ToolResult" events via the EventBus.
- Observers can inject "Steer Instructions" into the current runner's queue to correct course without waiting for a full turn to end.
- **Goal**: Implement "Real-time Pair Programming" and "Continuous Guardrails".

---

## 4. Implementation Roadmap

1.  **Phase 1 (Agency)**: Implement the "call_colleague" (Handoff) tool.
2.  **Phase 2 (Smoothness)**: Implement Intent Routing and Soft Focusing.
3.  **Phase 3 (Parallelism)**: Implement Observer Mode and Interceptor logic.
