# Agent Self-Learning Research Notes

Status: working notes  
Purpose: preserve the research and framework review behind the governed self-learning design.

This document records the papers and open-source projects we are using as references for the bot self-learning capability. It is not a shopping list of frameworks to adopt. The goal is to extract architecture lessons for `nuke-ai-collaborator`.

The core product rule remains:

> Experience should compound into auditable memory and approved skills, without silent self-modification or cross-group leakage.

## 1. Reading Set

We are tracking seven core references first:

1. Reflexion
2. Generative Agents
3. Voyager
4. MemGPT / Letta
5. Graphiti / Zep
6. Mem0
7. LangGraph

Related but non-core references:

- AutoGen
- OpenWiki

## 2. Reflexion

Links:

- Paper: <https://arxiv.org/abs/2303.11366>
- Main repo: <https://github.com/noahshinn/reflexion>
- Early draft repo: <https://github.com/noahshinn/reflexion-draft>

Core idea:

- The agent does not update model weights.
- It converts task feedback, failure, or environment signal into verbal reflection.
- The reflection is stored in episodic memory.
- On the next similar attempt, the reflection is injected back into context.

Why it matters to us:

- This is the clearest source for "failure experience -> reflection -> future reuse".
- It supports our distinction between raw event logs and learned behavior.
- It gives us a lightweight way to learn from tool failures, rejected answers, failed tests, bad plans, or user corrections.

What we should take:

- A failed run should produce a structured reflection, not just a log line.
- Reflection should carry source evidence, task type, outcome, and scope.
- Reflection is not an active skill. It is a candidate learning artifact.
- Reuse should happen through retrieval, not by rewriting prompts globally.

What we should not copy:

- Do not treat every failure as a reusable rule.
- Do not allow the agent to self-promote reflections into active behavior.
- Do not rely on a single episodic memory buffer as the whole learning system.

Mapping to our architecture:

- `tool_loop_v1` can emit failure/success traces.
- A future `ReflectionExtractor` can summarize selected traces into scoped reflections.
- Reflections should live in group-local memory storage.
- Promotion from reflection to draft skill must go through a Learning Policy Engine and human approval.

## 3. Generative Agents

Link:

- Paper: <https://arxiv.org/abs/2304.03442>

Core idea:

- Agents maintain a memory stream.
- Memories are retrieved by recency, relevance, and importance.
- Higher-level reflections are periodically synthesized from accumulated memories.
- Reflections influence future planning and behavior.

Why it matters to us:

- It gives a strong model for separating observation, reflection, and planning.
- It shows why memory cannot be a flat dump.
- It supports weighted retrieval instead of blindly loading everything into context.

What we should take:

- Memory items need timestamps, importance, source, and relevance.
- Reflection should be a scheduled or threshold-based process, not only an immediate reaction.
- Planning should retrieve relevant memories and reflections selectively.

What we should not copy:

- Do not build a simulated social world abstraction.
- Do not make memory importance purely LLM-scored without guardrails.

Mapping to our architecture:

- Group DB memory tables should support recency, importance, and source evidence.
- Bot runtime context should hydrate only the memory slice relevant to the current group/task.
- Reflection generation should be triggered by repeated patterns, not every message.

## 4. Voyager

Link:

- Paper: <https://arxiv.org/abs/2305.16291>
- Repo: <https://github.com/MineDojo/Voyager>

Core idea:

- The agent explores an environment, receives feedback, and builds an expanding library of reusable executable skills.
- Skills are composed and reused for harder tasks.
- Environment feedback and self-verification are part of skill creation.

Why it matters to us:

- Voyager is the strongest reference for "experience -> reusable skill library".
- It maps closely to our existing `skills/learned/draft/` and `skills/learned/active/` structure.
- It validates the idea that learned capability should become a durable artifact, not just a memory.

What we should take:

- Draft skills should include evidence, intended trigger, and verification result.
- Skill promotion should require repeated success or explicit human instruction.
- Skills should be composable and scoped.

What we should not copy:

- Do not assume a closed game environment with clean reward signals.
- Do not generate executable capability without policy checks.
- Do not skip human approval for project-affecting behavior.

Mapping to our architecture:

- Current draft/active skill lifecycle is aligned with this direction.
- We still need static scanning before approval.
- We need promotion rules that decide when a reflection is strong enough to become a draft skill.

## 5. MemGPT / Letta

Links:

- Paper: <https://arxiv.org/abs/2310.08560>
- Letta repo: <https://github.com/letta-ai/letta>

Core idea:

- Treat context as scarce.
- Manage memory like an operating system manages virtual memory.
- Separate working context from archival memory.
- Let the agent decide what to retrieve, store, and page in.

Why it matters to us:

- Our bots are long-lived group members, so context pressure is guaranteed.
- Group history, tool traces, docs, and decisions cannot all sit in prompt context.
- We need memory tiers, not one giant memory bucket.

What we should take:

- Working memory and long-term memory should be distinct.
- Context hydration should be deliberate and task-specific.
- Memory writes should be structured and inspectable.

What we should not copy:

- Do not let the agent freely mutate all memory tiers without policy.
- Do not adopt a full agent runtime just for memory management.

Mapping to our architecture:

- Group DB is the natural long-term memory boundary.
- Bot runtime context is working memory.
- The learning layer should decide what crosses from event trace to memory, and from memory to skill.

## 6. Graphiti / Zep

Links:

- Graphiti repo: <https://github.com/getzep/graphiti>
- Zep: <https://www.getzep.com/>

Core idea:

- Agent memory can be represented as a temporal knowledge graph.
- Facts and relationships should preserve provenance and time validity.
- Retrieval should combine semantic search, text search, and graph traversal.

Why it matters to us:

- Project knowledge changes over time.
- Decisions can be superseded.
- People, bots, tasks, files, and project rules form relationships.

What we should take:

- Memories need temporal validity, not just creation time.
- Conflicting facts should be represented and resolved, not overwritten blindly.
- Provenance is required for review and trust.

What we should not copy:

- Do not start with a graph database unless the simpler group DB model becomes insufficient.
- Do not over-model every chat message as a graph entity.

Mapping to our architecture:

- SQLite can start with explicit tables for memories, evidence, and relations.
- A future graph layer can be introduced behind a memory service boundary.
- The important near-term decision is preserving source IDs and validity fields.

## 7. Mem0

Link:

- Repo: <https://github.com/mem0ai/mem0>

Core idea:

- Production-oriented memory extraction, update, and retrieval.
- Focus on compact, salient memories rather than raw transcript storage.
- Includes graph-oriented variants for relation-heavy memory.

Why it matters to us:

- It is closer to production behavior than many research demos.
- It reinforces the need to separate memory extraction from memory retrieval.
- It is relevant to latency, memory compaction, and practical API design.

What we should take:

- Memory should be compact and curated.
- Extraction should produce structured memory candidates.
- Retrieval should return only what the current task needs.

What we should not copy:

- Do not treat a memory library as the whole self-learning system.
- Do not outsource our group isolation and approval semantics to a generic memory service.

Mapping to our architecture:

- Mem0-like extraction can inform a future memory service.
- Our system still needs project-specific scope, evidence, approval, rejection cooldown, and skill promotion.

## 8. LangGraph

Links:

- Repo: <https://github.com/langchain-ai/langgraph>
- Docs: <https://langchain-ai.github.io/langgraph/>

Core idea:

- Agent workflows can be represented as durable, stateful graphs.
- Nodes and edges make control flow explicit.
- Human-in-the-loop and persistence are first-class orchestration concerns.

Why it matters to us:

- It is useful as a reference for stateful agent orchestration.
- It maps conceptually to our extraction -> reflection -> scoring -> draft -> approval -> activation loop.
- It is not a reason to replace our Supervisor / Worker / MCP Collector topology.

What we should take:

- Learning pipeline states should be explicit.
- Human approval should be a state transition, not an ad hoc callback.
- Long-running agent workflows need resumable state.

What we should not copy:

- Do not restructure the project around LangGraph.
- Do not move MCP ownership or tool security into LangGraph.
- Do not bypass existing ToolRouter, HIL, group DB, or worker isolation.

Mapping to our architecture:

- If explored, LangGraph should be an optional executor plugin or internal orchestration implementation detail.
- The platform boundary remains our existing runtime architecture.
- The learning pipeline can borrow graph-style state modeling without taking the framework dependency.

## 9. Related: AutoGen

Link:

- Repo: <https://github.com/microsoft/autogen>

Useful for:

- Multi-agent collaboration patterns.
- Agent-to-agent feedback loops.
- Conversation-oriented task delegation.

Limited for us because:

- Our group collaboration model is already domain-specific.
- It does not solve governed self-learning, evidence promotion, or group-isolated skill evolution by itself.

Architecture stance:

- Reference only. Do not use as the core runtime.

## 10. Related: OpenWiki

Link:

- Repo: <https://github.com/langchain-ai/openwiki>

Useful for:

- Turning codebase experience into maintained agent-facing documentation.
- Refreshing repo docs as code changes.
- Teaching agents to consult generated documentation through files such as `AGENTS.md` or `CLAUDE.md`.

Limited for us because:

- It is not a memory system.
- It is not a reflection pipeline.
- It does not solve evidence accumulation, skill promotion, group isolation, approval, or drift.

Architecture stance:

- Non-core reference.
- It may inspire a documentation-solidification step after approved learning, but it should not sit in the core self-learning design.

## 11. Cross-Reference Matrix

| Reference | Best lesson | Maps to our system | Risk if copied blindly |
|---|---|---|---|
| Reflexion | Failure becomes reusable reflection | Reflection store, failure review | Overfitting one failure |
| Generative Agents | Observation -> reflection -> planning | Memory scoring and retrieval | Memory dump or weak scoring |
| Voyager | Experience becomes skill library | Draft/active learned skills | Unsafe executable self-modification |
| MemGPT / Letta | Context and memory tiering | Runtime context vs group DB memory | Agent mutates memory too freely |
| Graphiti / Zep | Temporal provenance graph | Evidence, validity, relation tracking | Over-engineered graph too early |
| Mem0 | Production memory extraction/retrieval | Memory service API | Generic memory without governance |
| LangGraph | Explicit stateful workflow | Learning pipeline state machine | Framework-driven architecture rewrite |
| AutoGen | Multi-agent feedback | Bot collaboration patterns | Runtime duplication |
| OpenWiki | Agent-facing docs refresh | Post-approval documentation | Mistaking docs for learning |

## 12. Architecture Conclusion

The references converge on one design:

1. collect scoped evidence,
2. extract compact memory,
3. synthesize reflection from repeated evidence,
4. score confidence, safety, and reuse potential,
5. create draft skills only when the pattern is stable,
6. require human approval before activation,
7. monitor drift and allow rollback.

For this project, the core implementation should be our own governed learning layer:

- group-local evidence store
- group-local memory store
- reflection queue
- learning policy engine
- draft skill generator
- static compliance scanner
- approval UI and websocket protocol
- active skill loader
- drift monitor

Frameworks can inform individual pieces, but they should not define the platform architecture.

## 13. Open Design Questions

- What exact evidence threshold creates a reflection?
- What exact threshold promotes reflection into draft skill?
- Which learning categories are safe enough for auto-memory but not auto-skill?
- How do we represent rejected patterns and cooldown?
- How should `person`, `project`, `group`, and `global-within-group` scopes map to physical storage?
- What drift signals should retire or downgrade an active skill?
- How much of the learning pipeline should be visible to users in the UI?
