# Agent Self-Learning Design

> Canonical doc for the bot self-learning loop in `nuke-ai-collaborator`.
>
> This file merges the earlier self-learning note, the memory design note, the tool-memory design, and the pitch-deck framing into one product-level spec.

## 1. Thesis

We do not want a black-box bot that "learns itself" by mutating prompts or silently rewriting behavior.

We want a governed learning system that:

1. observes repeated behavior from mail, chat, project logs, tasks, docs, and tool traces,
2. extracts stable patterns,
3. converts those patterns into evidence-backed memory or draft skills,
4. asks for human approval before promotion, and
5. makes the learned capability reusable across future sessions.

That is the product promise:

**the bot compounds experience into durable, auditable skills without losing human control.**

## 2. Why this is hard

The user experience we want spans two learning axes at once:

- **Person axis**: the bot learns a specific human's tone, habits, response style, preferences, and decision patterns.
- **Project axis**: the bot learns a project's domain knowledge, SOPs, risk patterns, historical decisions, and collaboration conventions.

Those axes must not collapse into one blob. Otherwise the bot will mix personal style with project rules and learn the wrong thing.

So the system needs scope-aware learning:

- `person`
- `project`
- `group`
- `global`

Each learned item must carry:

- source evidence
- scope
- confidence
- update time
- promotion state

## 3. What the repo already has

The current codebase already contains the raw material for this loop:

- `skills/learned/draft/` and `skills/learned/active/` for draft-to-active promotion
- `learns: true` frontmatter support to trigger writeback after a run
- `MEMORY.md` for long-lived hand-edited memory
- tool event logging and recall infrastructure for project-level traces
- approval-driven UI and websocket events for `skill_draft_added`

That means the system already has:

- a place to store drafts,
- a place to activate approved skills,
- a memory layer for stable facts,
- and a human gate.

What is still missing is a unified explanation of how those parts fit together as one learning loop.

## 4. What the literature says

The literature and open-source projects we reviewed point to four complementary patterns.

### 4.1 Long-term memory systems

- [MemGPT / Letta](https://arxiv.org/abs/2310.08560) / [Letta repo](https://github.com/letta-ai/letta)  
  Useful for hierarchical memory management and stateful agent behavior.

- [Generative Agents](https://arxiv.org/abs/2304.03442)  
  Important for the observation -> reflection -> planning cycle, especially the idea that memories should be weighted by recency, relevance, and importance.

- [Graphiti](https://github.com/getzep/graphiti) / [Zep paper](https://arxiv.org/abs/2501.13956)  
  Useful for time-aware fact and relation tracking with provenance.

### 4.2 Reflection and self-improvement

- [Reflexion](https://arxiv.org/abs/2303.11366)  
  Shows that an agent can improve without weight updates by writing reflections into memory and reusing them later.

### 4.3 Skill acquisition

- [Voyager](https://arxiv.org/abs/2305.16291)  
  The most relevant reference for us because it turns repeated behavior into a reusable skill library.

### 4.4 Production memory frameworks

- [Mem0](https://github.com/mem0ai/mem0)  
  A practical memory framework focused on production use.

- [AutoGen](https://github.com/microsoft/autogen)  
  Useful for multi-agent orchestration and feedback loops.

- [LangGraph](https://github.com/langchain-ai/langgraph)  
  Useful for building a controllable graph of memory, reflection, extraction, and approval states.

- [OpenWiki](https://github.com/langchain-ai/openwiki)  
  Useful as a reference for turning codebase experience into maintained agent-facing documentation. OpenWiki is a CLI that writes and refreshes repository documentation for agents, can update docs from repository changes, and appends guidance to `AGENTS.md` / `CLAUDE.md` so coding agents know to consult that documentation. For this project, the relevant lesson is not memory storage itself, but the "experience -> maintained documentation -> future agent context" loop.

### 4.5 Why we are not copying these literally

The common failure mode in the literature is to treat self-learning as either:

- a memory dump,
- a prompt rewrite,
- or a self-feedback loop with no external guardrails.

That is too fragile for this product.

Our platform needs:

- per-group isolation,
- evidence chains,
- human approval before promotion,
- reversible drafts,
- and a memory model that separates person from project.

## 5. Unified model

The learning system should be described with four artifacts.

### 5.1 Memory

Memory stores stable observations.

Examples:

- a user's preference for short, direct answers
- a project's deployment rule
- a recurring blocker pattern
- a fact extracted from a repeated conversation

Memory is not the same as skill. Memory is descriptive.

### 5.2 Reflection

Reflection turns raw experience into a candidate rule.

Examples:

- "When this user is uncertain, they prefer one clarifying question before action."
- "This project always requires a rollback note for schema changes."
- "This team prefers decision-first communication."

Reflection is still tentative. It must not auto-promote.

### 5.3 Draft skill

Draft skill is the first executable form of a reusable pattern.

It lives in `skills/learned/draft/` and must include:

- a name
- a short description
- scope
- evidence
- the rule itself
- a clear reason why it is reusable

Drafts are reviewable, editable, and rejectable.

### 5.4 Active skill

Active skill is the approved, injected version.

It lives in `skills/learned/active/` and participates in runtime skill loading.

Promotion must be explicit. The bot cannot self-promote.

## 6. Learning pipeline

The unified pipeline is:

1. **Ingest**
   - collect mail, chat, docs, task updates, and tool traces

2. **Normalize**
   - redact secrets
   - attach scope
   - attach evidence source
   - deduplicate obvious noise

3. **Extract**
   - detect repeated patterns
   - separate person signals from project signals
   - separate one-off workaround from reusable rule

4. **Score**
   - confidence
   - frequency
   - recency
   - safety
   - reuse potential

5. **Write draft**
   - generate a candidate skill under `learned/draft/`
   - or write a stable memory item if it is not yet strong enough to become a skill

6. **Approve**
   - show a card with evidence and diff
   - human accepts, edits, or rejects

7. **Activate**
   - approved drafts move to `learned/active/`
   - runtime loads them on the next session

8. **Monitor drift**
   - if evidence weakens or behavior changes, downgrade or retire the skill

## 7. Product positioning

This should be a selling point, but only if we frame it correctly.

The product claim is not:

- "the bot can learn anything by itself"

The product claim is:

- "the bot compounds project and personal experience into auditable skills, with evidence and approval."

That is much stronger commercially because it implies:

- lower onboarding cost
- better continuity across sessions
- project-specific adaptation
- traceable decisions
- controlled automation

## 8. What makes this defensible

The moat is not raw model quality.

The moat is the loop:

- repeated work generates evidence,
- evidence becomes memory,
- memory becomes draft skill,
- draft skill becomes approved capability,
- capability compounds into future work.

Over time the customer accumulates their own operational knowledge inside the system.
That knowledge does not stay as a pile of logs. It becomes a usable asset.

## 9. Implementation boundaries

Do not let the system do these things:

- silently rewrite active skills from raw logs
- merge person and project scope into one profile
- auto-promote low-confidence patterns
- learn from one-off incidents as if they were stable rules
- cross group boundaries

Do this instead:

- preserve provenance
- require explicit promotion
- keep draft and active physically separated
- keep scopes explicit
- keep review reversible

## 10. References across the repo

This doc supersedes and consolidates the ideas in:

- [docs/agent_memory_design.md](/Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/agent_memory_design.md)
- [docs/2026-06-27-tool-memory-design.md](/Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/2026-06-27-tool-memory-design.md)
- [docs/PITCH-DECK-INTERNAL.md](/Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/PITCH-DECK-INTERNAL.md)
- [docs/pitch-deck-boss-en.html](/Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/pitch-deck-boss-en.html)
- [docs/decisions/2026-06-25-skill-layer-role-binding-design.md](/Users/Nuke/claudeFolder/nuke-ai-collaborator/docs/decisions/2026-06-25-skill-layer-role-binding-design.md)

## 11. Open questions

We still need a concrete answer for:

- what qualifies a pattern as reusable enough to draft
- what minimum evidence is required for approval
- whether person memory and project memory should share one UI or two
- how to score drift over time
- which categories are safe to auto-promote, if any

Those are the right next docs to write after this one.

## 12. Evolution Draft

This section captures the current agreed starting point for implementation planning.

### Sequence

1. **Build the base layer first**
   - memory
   - reflection
   - distillation
   - orchestration
   - a document intake window for bot and human input

2. **Write four internal materials**
   - capability map
   - data model
   - learning pipeline
   - evaluation criteria

3. **Validate one vertical scenario**
   - start with a narrow, high-frequency workflow
   - prove the full loop end to end

4. **Expand outward**
   - add more input sources
   - add more scenario types
   - keep the same governed learning loop

### Product rule

We do not start from "fully automatic self-learning".
We start from a controlled base that can receive materials, process them, and turn repeated patterns into approved capabilities.

### Intake principle

The system must expose a place where humans can provide source material, and a place where the bot can process that material into structured outcomes.

Those two windows should stay distinct:

- one for input
- one for analysis and conversion

That separation keeps the learning loop auditable and easier to evolve.

## 13. Antigravity Review & Hardening Recommendations (2026-07-03)

Following a comprehensive architectural review, the following enhancements are recommended to harden the security, performance, and usability of the self-learning loop:

### 13.1 Strict Group Isolation (No Cross-Group Contamination)
*   **Context**: The design defines `person`, `project`, `group`, and `global` scopes for learned capabilities.
*   **Risk**: Under the system's runtime architecture, groups are strictly isolated. If a bot learns a pattern in Group A (e.g., proprietary API endpoints, credentials, or internal conventions) and auto-promotes it globally, this information could leak to Group B.
*   **Recommendation**: In the physical implementation, all draft and active skills must remain located within the group's isolated workspaces. `project` and `global` scopes must only dictate visibility *internally* to bots within the same group. Cross-group skill sharing must never happen automatically; it must go through an admin-controlled export/import gate.

### 13.2 Confidence Accumulation & Cooldown (Mitigating Approval Fatigue)
*   **Risk**: LLMs are prone to overfitting from a single successful user interaction (e.g., a user temporarily asking for 2-space indentation). If every small preference triggers a draft notification, users will experience approval fatigue and blindly accept dangerous rules.
*   **Recommendation**:
    *   **Evidence Count Threshold**: Passive observations must recur across multiple distinct sessions ($N \ge 3$) before generating a draft, unless the user explicitly triggers learning via a command (e.g. `/learn`).
    *   **Rejection Cooldown / Decay**: If a user rejects a candidate draft, the pattern should be blacklisted for a cooldown period (e.g., 7 days) to prevent the bot from repeatedly suggesting it.

### 13.3 Storage Tiering (Separating Memory from Skills)
*   **Risk**: Storing micro-observations (e.g., "User X prefers concise summaries") as individual files under `skills/learned/` will quickly clutter the filesystem and cause major performance bottlenecks during worker hydration and layout walks.
*   **Recommendation**:
    *   **Memory (High-frequency, granular)**: Store descriptive facts in a relational SQLite table (e.g., a `bot_memories` table in the group database) or Zep/Chroma vector databases.
    *   **Skills (Low-frequency, executable)**: Only rules containing prompt instructions, tool definitions, or file overrides should be written to the `skills/learned/` directories as `SKILL.md` markdown files.

### 13.4 Static Compliance Scanning (Preventing Prompt Injection Privilege Escalation)
*   **Risk**: A bot's context can be poisoned by external untrusted inputs (e.g., a malicious README file). The prompt injection could instruct the bot to generate a draft skill containing `bypassPermissions: true` or a hidden backdoor command. If the user accepts it, they grant the hacker full Remote Code Execution (RCE).
*   **Recommendation**: All drafts under `learned/draft/` must pass a static analysis check before being presented to the user:
    *   Block the inclusion of `bypassPermissions: true` or command-bypass configurations.
    *   Scan for suspicious prompt injection signatures and raw shell execute patterns.

### 13.5 WebSocket Control Frame Protocol for Human-in-the-Loop Gate
*   **Recommendation**: Standardize the WebSocket frame schema for human approval:
    *   When a draft is written, the Worker pushes `SKILL_DRAFT_PENDING` to the Supervisor, which relays it to the browser.
    *   The frontend renders the diff card, allowing the user to `Approve` (moves the file to `learned/active/`), `Reject` (clears draft), or `Edit` (sends modified content back).
    *   Once actioned, the Supervisor notifies the Worker, which hot-reloads its active skills in-memory without requiring a process restart.
