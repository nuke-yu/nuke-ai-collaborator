# Project Experience Memory Structure

> Status: discussion draft
> Scope: Nuke AI Collaborator group-based project collaboration
> Date: 2026-07-07

## 1. Core Positioning

Nuke's memory direction should not be treated as a generic chatbot memory feature.

The product problem is:

> In a long-running project group, human members and role-based AI Bots continuously produce decisions, mistakes, fixes, preferences, code changes, tool traces, external documents, research notes, operating conventions, and implicit behavior habits. The system must turn these project experiences and knowledge sources into reusable, auditable, group-local team intelligence.

Therefore the target capability is:

> Project Experience Layer: a group-local experience system that records, distills, verifies, retrieves, and promotes project knowledge.

The word "experience" includes three source families:

- Work experience: task traces, tool events, test results, failures, fixes, handoffs, and approvals.
- External knowledge: docs, specifications, research notes, ADRs, uploaded files, product material, academic material, and codebase documentation.
- Behavior habits: stable user preferences, group norms, review style, communication patterns, risk tolerance, implementation taste, and repeated correction patterns.

The implementation may use RAG, vector search, graph memory, reflection, skill promotion, or algorithms inspired by Ruflo, but those mechanisms are subordinate to the collaboration scenario.

## 2. Research Alignment

This structure follows the existing self-learning research notes in `docs/agent-self-learning-research-notes.md`.

The core product rule is unchanged:

> Experience should compound into auditable memory and approved skills, without silent self-modification or cross-group leakage.

Key mappings:

- Reflexion: failed or corrected work should become structured Reflection Candidates, not just logs.
- Generative Agents: memory retrieval should combine relevance, recency, and importance; reflection can be scheduled or threshold-based.
- Voyager: stable experience can become a draft skill, but only through evidence, verification, policy checks, and human approval.
- MemGPT / Letta: working context and archival memory must be separate; the group DB is archival memory, runtime prompt state is working memory.
- Graphiti / Zep: memory needs provenance, temporal validity, and conflict handling before a future graph layer is considered.
- Mem0: extraction and retrieval are separate capabilities; compact curated memory is more valuable than raw transcript storage.
- LangGraph: the learning pipeline should be modeled as explicit state transitions, but this project should not replace the existing Supervisor / Worker / MCP Collector topology.

Therefore this document should be read as the product-facing structure for the governed learning layer described in the research notes.

## 3. Ruflo Reference Improvements

Ruflo should be treated as an algorithm and engineering reference, not as a product model to copy.

The parts worth absorbing are below.

### 3.1 Use A Four-Step Learning Loop

Ruflo's useful loop is:

```text
RETRIEVE -> JUDGE -> DISTILL -> CONSOLIDATE
```

For Nuke, this should become:

```text
Retrieve related project context and source documents
  -> judge outcome, source authority, and evidence quality
  -> distill the episode/document/habit signal into structured learning candidates
  -> consolidate into approved memory, rejected candidate, or promotion candidate
```

This improves the current summary because it makes "experience distillation" an explicit pipeline, not just a post-task summary.

### 3.2 Add Structured Distillation Fields

Ruflo's deterministic distillation schema is small but useful:

```text
summary
detail
labels
paths
```

For Nuke, each Episode, Document Digest, Habit Candidate, and Reflection Candidate should include the same high-signal fields:

- Summary: one-line meaning of the episode.
- Detail: compact supporting context.
- Labels: action verbs, project concepts, tool names, modules, roles.
- Paths: files, APIs, docs, commands, or UI routes involved.

This is valuable because retrieval should not depend only on long natural-language summaries. Labels and paths are often stronger anchors than prose.

### 3.3 Separate Evidence Tiers

Ruflo has a strong distinction between execution-grounded evidence and proxy evidence.

For Nuke, use these evidence tiers:

| Tier | Meaning | Can Auto-Promote? |
|---|---|---|
| `oracle:human-approved` | Human explicitly approved or corrected it | Yes, within policy |
| `oracle:test-exec` | Tests, commands, or tool execution proved the outcome | Possible, low-risk only |
| `source:authoritative-doc` | Approved project docs, ADRs, specs, or imported authoritative files | Possible after scope validation |
| `source:external-doc` | External docs, research notes, vendor docs, academic material | No, requires review before behavior impact |
| `judge:model` | LLM judged the episode | No, requires review |
| `proxy:structural` | Derived from text shape, paths, labels, similarity, co-occurrence | No |

Rule:

> Proxy evidence may improve retrieval ranking, but must not justify autonomous behavior changes.

This is one of the most important Ruflo lessons to import.

### 3.4 Add Outcome Verdicts To Episodes

Ruflo's learning loop depends on success or failure verdicts.

For Nuke, every meaningful Episode should record:

- Outcome: success, failure, partial, abandoned, unknown
- Reward: optional normalized score, if measurable
- Verdict source: human, test, tool, model judge, structural proxy
- Verification artifact: test output, command result, approval event, or review comment

Without outcome verdicts, the system can store experience but cannot reliably learn which patterns are good.

For document-derived knowledge, the equivalent is source authority:

- Source type: internal doc, external doc, research note, uploaded artifact, API doc, web page, code comment.
- Authority: canonical, reference, exploratory, outdated, unknown.
- Validity: active, superseded, time-sensitive, unknown.
- Scope: group, project, module, role, task type, user.

### 3.5 Make Distillation Incremental And Non-Destructive

Ruflo's safer distillation design has several useful invariants:

- Keep raw memory entries unchanged.
- Write distilled artifacts into separate tables.
- Maintain a cursor so batch jobs do not rescan everything.
- Process in bounded batches.
- Use transactions so partial failure does not advance state.
- Run DB integrity checks before writing when possible.

For Nuke, this means:

```text
tool_events/messages/source_documents stay raw
episodes/reflections/memories are derived artifacts
distill_state tracks per-group progress
failed batches do not destroy or mutate source evidence
```

This fits our group DB model well.

### 3.6 Add Deduplication By Similarity Cluster

Ruflo clusters similar entries before creating reasoning patterns.

For Nuke, the Learning Policy Engine should dedupe candidates before review:

- Same group
- Same memory type
- Similar labels and paths
- Similar embedding or FTS match
- Same trigger condition

Possible outcomes:

- Reinforce existing memory
- Create new version
- Create conflict
- Suppress as duplicate
- Put into cooldown if similar to a rejected candidate

This prevents the review queue from filling with repeated versions of the same lesson.

### 3.7 Treat Graph Edges As Weak Unless Proven

Ruflo explicitly marks co-occurrence edges as weak and non-promoted.

For Nuke:

- A path-file-task relation is useful for retrieval.
- A repeated co-occurrence is not causal proof.
- Graph edges should carry relation type, confidence, evidence tier, and promotion status.

Rule:

> Graph relations may help find memory, but only verified memory, approved rules, or approved skills may justify behavior.

This avoids the common graph-memory mistake: turning proximity into false causality.

### 3.8 Use Hybrid Retrieval Before Advanced Vector Infrastructure

Ruflo's hybrid retrieval combines:

- Dense vector similarity
- Sparse BM25 / FTS matching
- Multi-field weighting
- MMR diversity reranking

For Nuke's first production version, this is more relevant than adopting HNSW or AgentDB wholesale.

Recommended retrieval stack:

```text
SQLite FTS5 / LIKE fallback
  + embedding similarity where available
  + metadata filters: group, bot role, memory type, status, confidence
  + rerank by importance, recency, evidence tier, and MMR diversity
```

HNSW can wait until memory volume justifies it. On small corpora, exact sparse matches and metadata filters may outperform approximate vector search.

### 3.9 Add Honest Observability And Benchmarks

Ruflo's audit shows a practical lesson: impressive learning claims are dangerous unless measured.

For Nuke, every learning feature should expose:

- How many candidates were extracted
- How many were saved, rejected, merged, or promoted
- Which evidence tier supported each item
- Retrieval hit rate or user acceptance rate
- Whether embeddings are real, mock, missing, or stale
- Whether the learning action changed future behavior

Do not market:

- Speedup numbers without benchmark
- Learning claims without before/after behavior checks
- Graph intelligence without proving it changes retrieval quality
- Skill promotion without rollback and audit

### 3.10 Add Cost Gates

Ruflo keeps default distillation at zero additional LLM cost unless a higher judge tier is explicitly enabled.

For Nuke:

- Default distillation should use deterministic extraction plus existing task context.
- LLM reflection should be threshold-triggered, not always-on.
- Expensive model judge should require policy approval or batch mode.
- The UI should show whether a candidate came from deterministic extraction, model reflection, test execution, or human approval.

This matches the existing tool memory design that rejected an always-on observer model.

### 3.11 What Not To Copy From Ruflo

Do not copy:

- Shared memory namespace across all agents.
- Cross-project memory import by default.
- Large CLI/plugin runtime shape.
- Neural routing or SONA as a near-term dependency.
- Marketing-style performance claims.
- Autonomous promotion based only on proxy evidence.

Nuke's stronger product boundary remains:

> group-local collaboration memory, human-governed promotion, and no cross-group leakage.

### 3.12 Development Reference Summary

For future feature development documents, Ruflo should be referenced in this narrow way:

| Ruflo Lesson | Nuke Translation | Development Implication |
|---|---|---|
| `RETRIEVE -> JUDGE -> DISTILL -> CONSOLIDATE` | Project context/source retrieval, evidence judging, candidate extraction, governed consolidation | Build an explicit distillation pipeline, not a one-shot summary job |
| Structured distillation | `summary`, `detail`, `labels`, `paths` for Episode, Document Digest, Habit Candidate, Reflection Candidate | Store high-signal fields for retrieval, dedupe, merge, and review |
| Evidence tiering | `oracle:*`, `source:*`, `judge:model`, `proxy:structural` | Proxy evidence can rank retrieval but cannot change behavior |
| Outcome verdicts | success/failure/partial/unknown plus source authority for documents | Learning needs verdicts; documents need authority and validity |
| Incremental non-destructive processing | raw source unchanged, derived artifacts separate, cursor per group, transactional batches | Use group-local `distill_state`; failed batches must not advance state |
| Similarity clustering | duplicate candidate suppression and reinforcement | Similar memories merge; similar skills become version updates |
| Weak graph edges | co-occurrence is not causality | Graph can help retrieval, not autonomous behavior justification |
| Hybrid retrieval | FTS/BM25 + embedding + metadata filters + MMR | Start with SQLite FTS/metadata before HNSW/AgentDB-style infrastructure |
| Cost gates | deterministic extraction by default; LLM judge only when triggered | Avoid always-on observer LLM; use threshold and batch reflection |
| Honest audit | claims need measured evidence | Track acceptance, retrieval hit, skill merge, skill failure, and override metrics |

The most important development rule:

> Ruflo is an algorithm and engineering reference for distillation discipline, not a platform architecture to copy.

### 3.13 Skill Learning Reference From Ruflo

Ruflo's reasoning-pattern consolidation is useful for Nuke's skill learning, but the target behavior is different.

For Nuke:

- Similar experience should reinforce existing memory or skill candidates.
- Similar skill candidates should merge into the existing candidate.
- Similar active skills should create draft version updates, not duplicate active skills.
- Version upgrades should preserve evidence, reviewer, previous version, and rollback path.
- Promotion should remain human-governed and group-local.

Recommended translation:

```text
Ruflo reasoning pattern
  -> Nuke Workflow Pattern / Skill Candidate

Ruflo pattern reinforcement
  -> Nuke evidence merge

Ruflo consolidation
  -> Nuke skill version upgrade or memory merge

Ruflo weak causal edge
  -> Nuke retrieval relation only, not behavior rule
```

### 3.14 Non-Goals From Ruflo

These should stay out of the near-term feature plan:

- Replacing Nuke's Supervisor / Worker / MCP Collector topology.
- Shared cross-group memory namespaces.
- Cross-project automatic memory import.
- Swarm coordination as the product center.
- SONA, neural routing, or model-routing learning as a dependency.
- AgentDB/HNSW as an early mandatory storage layer.
- Autonomous promotion from proxy evidence.
- Performance claims without local benchmark data.

## 4. What Must Be Distilled

The system should support three distillation streams.

Classification should use two separate dimensions:

```text
source_type: where the evidence came from
memory_type: what the distilled result becomes
```

This avoids confusing uploaded material with final memory.

Example:

```json
{
  "source_type": "uploaded_email_bundle",
  "source_format": "email",
  "memory_type": "customer_insight",
  "claim": "Customers repeatedly ask about security before pricing."
}
```

The uploaded email bundle is only the raw source. The distilled result may become Customer Insight, Requirement Candidate, Behavior Habit, Project Fact, Domain Knowledge, or Workflow Pattern.

### 4.1 Work Experience Distillation

This stream turns actual project work into reusable experience.

Inputs:

- Tool events
- Messages
- File changes
- Test results
- Human approvals or corrections
- Bot handoffs
- Task outcomes

Primary outputs:

- Episode
- Failure Lesson
- Decision reinforcement
- Workflow Pattern
- Skill Candidate

Main question:

> What did this group learn by doing the work?

### 4.2 External Knowledge Distillation

This stream turns documents into structured project knowledge.

Inputs:

- Imported sources dragged into the chat window
- Email bundles
- Chat exports
- Spreadsheets
- Word documents
- PDFs
- CSV files
- Screenshots or image OCR text
- Product documents
- Architecture docs
- ADRs
- Uploaded PDFs or notes
- Research notes
- API docs
- Vendor documentation
- Academic references
- Codebase docs such as `AGENTS.md`, `CLAUDE.md`, README files, or design docs

Primary outputs:

- Document Digest
- Project Fact
- Domain Knowledge
- Decision Candidate
- Constraint Candidate
- Workflow Pattern
- Reference Link

Main question:

> What knowledge from this source should future Bots be able to retrieve, cite, and apply inside this group?

Required distinction:

- A source document can be authoritative for one project and only exploratory for another.
- External material should not become a project rule until it is scoped and accepted.
- Document summaries must keep source citation and version/date metadata.
- Dragged-in or uploaded files are Imported Sources first. They do not become External Knowledge until claims are extracted, cited, scoped, and deduplicated.
- External Knowledge is a distilled result, not a synonym for raw file upload.

### 4.3 Behavior Habit Distillation

This stream turns repeated human behavior and group norms into explicit preferences or operating style.

Inputs:

- Repeated human corrections
- Repeated accept/reject actions
- Review comments
- Conversation style
- Planning preferences
- Risk tolerance
- Implementation taste
- Approval behavior
- Recurring "do this / don't do this" instructions
- Imported interaction histories such as customer emails, team chat logs, sales follow-up records, support tickets, or review logs when they contain repeated behavior patterns

Primary outputs:

- Team Preference
- User Preference
- Communication Habit
- Review Standard
- Risk Policy Candidate
- Workflow Pattern

Main question:

> How does this group prefer work to be reasoned about, communicated, reviewed, and shipped?

Required distinction:

- A habit is not always a hard rule.
- Habits can drift and need review time.
- Sensitive personal inference must be avoided.
- Behavior habits must remain group-local and should not become global personality assumptions.
- A single document, single email, or one-off comment should not become a stable habit. It can at most create a Habit Candidate.
- Imported files may produce Behavior Habits only when they contain repeated, multi-sample behavior evidence.

### 4.4 Classification Rules

Use this decision order:

1. If the evidence comes from uploaded or imported material, classify the raw input as Imported Source.
2. If the extracted claim says what a document states, classify the result as External Knowledge or Domain Knowledge.
3. If the extracted pattern says how a person, customer, or team repeatedly behaves, classify the result as Behavior Habit.
4. If the claim was explicitly approved as project direction or architecture, classify it as Decision.
5. If the claim comes from task success or failure, classify it as Work Experience, Failure Lesson, or Workflow Pattern.
6. If it can be executed as a repeatable procedure with trigger, steps, checks, tools, and verification, classify it as Skill Candidate.

Same sentence can move between types depending on evidence and approval state.

Example:

```text
"Memory is project experience distillation, not storage."
```

- From a design document: External Knowledge or Domain Knowledge.
- From repeated user corrections: Behavior Habit or Team Preference.
- Explicitly approved by the group: Decision.
- Turned into a repeatable review method: Workflow Pattern or Skill Candidate.

Recommended fields:

```json
{
  "source_type": "external_doc | uploaded_email_bundle | behavior_signal | tool_event | review_action",
  "source_format": "docx | pdf | xlsx | email | chat_log | md | tool_trace",
  "memory_type": "project_fact | decision | external_knowledge | behavior_habit | workflow_pattern | skill_candidate",
  "promotion_target": "none | memory_only | l4_bot_learned | l3_role_skill | l2_group_skill | l1_system_candidate"
}
```

## 5. What Must Be Remembered

The system should not remember everything equally. It should distinguish raw material from reusable experience.

### 5.1 Project Facts

Facts describe stable or semi-stable project state.

Examples:

- Repository structure
- Runtime topology
- API contracts
- Deployment constraints
- Group-specific configuration
- Important file ownership
- Current active milestones

Product value:

- Helps Bots enter the project context quickly
- Reduces repeated project orientation cost
- Supports precise retrieval during task planning

### 5.2 Decisions

Decisions capture choices made by humans or accepted by the group.

Examples:

- "MCP connections must only live in the Collector process."
- "Builtin, skill, and shell tools must stay on `tool_executor.execute()`."
- "Write-class tools require HIL approval."
- "Do not add AI attribution to commit messages."

Required fields:

- Decision
- Reason
- Alternatives rejected
- Owner or approver
- Date
- Evidence source
- Current status: active, superseded, deprecated
- Valid from / valid until, if known

Product value:

- Prevents repeated debate
- Gives Bots a reliable source of architectural truth
- Makes project rules explainable instead of hidden in chat history

### 5.3 Failures And Fixes

Failures are high-value memory because they encode project-specific traps.

Examples:

- A test failed because the Bot changed MCP routing without respecting Collector-only ownership.
- A shell command was blocked because it violated the shell guard policy.
- A frontend layout broke on mobile because fixed text did not fit its container.

Required fields:

- Failed goal
- Symptom
- Root cause
- Fix
- Files or modules involved
- Verification result
- Whether the lesson should become a future warning
- Similarity signature for future retrieval

Product value:

- Reduces repeated mistakes
- Helps QA Bots generate better regression checks
- Helps Developer Bots plan safer changes

### 5.4 Team Preferences

Preferences capture how this group wants work to be done.

Examples:

- The user prefers a clear architectural argument before implementation.
- The user prefers product-level strategy before low-level mechanism design.
- The user does not want large speculative rewrites.
- The user wants no AI attribution in commits.

Required fields:

- Preference
- Scope: user, group, project, role, task type
- Strength: soft preference, strong preference, hard rule
- Evidence
- Last confirmed time
- Expiration or review time for preferences that may drift

Product value:

- Makes Bots feel like long-term teammates
- Reduces repeated style corrections
- Supports role-specific behavior tuning without cross-group leakage

### 5.5 Behavior Habits

Behavior Habits capture repeated patterns in how a human or group works, not just what they explicitly say.

Examples:

- The user repeatedly asks for architectural framing before implementation.
- The user accepts concise direct answers and rejects generic summaries.
- The group prefers conservative, reversible changes over broad refactors.
- The group treats security and group isolation as hard constraints.
- The group tends to review memory/learning design through product scenarios before mechanisms.

Required fields:

- Observed habit
- Evidence count
- Evidence examples
- Scope: user, group, bot role, task type
- Confidence
- Risk of overgeneralization
- Review time
- Whether it is soft guidance or hard policy

Product value:

- Makes Bots adapt to the group without requiring repeated explicit instructions.
- Helps new Bots inherit collaboration style.
- Turns correction history into useful operating knowledge.

### 5.6 Workflow Patterns

Workflow patterns describe repeatable project methods.

Examples:

- Read AGENTS.md and backend/CLAUDE.md before backend changes.
- For MCP changes, inspect Worker, Collector, bridge, and HIL paths together.
- For frontend app work, verify desktop and mobile screenshots before final response.
- For memory-related design, separate raw events, episodes, reflections, approved memory, and skills.

Required fields:

- Trigger condition
- Recommended steps
- Required checks
- Evidence from previous tasks
- Risk level
- Whether human approval is required before activation
- Known failure mode

Product value:

- Turns experience into operational capability
- Lets new Bots inherit proven project methods
- Bridges memory and skill systems

### 5.7 External Knowledge

External Knowledge captures distilled content from documents or outside references.

Examples:

- A vendor API's authentication constraints.
- A research paper's memory architecture pattern.
- A product strategy document's definition of the target user.
- A design document's accepted system boundary.
- A compliance document's required approval process.

Required fields:

- Source title
- Source type
- Source URI or file path
- Source version/date
- Extracted claim
- Scope
- Authority level
- Confidence
- Citation or evidence span
- Expiration or review time, if time-sensitive

Product value:

- Turns documents into usable project knowledge.
- Lets Bots cite sources instead of relying on vague memory.
- Separates external reference material from approved project rules.

### 5.8 Domain Knowledge

Domain knowledge captures business, academic, or product concepts specific to the group.

Examples:

- The product is group-based AI collaboration, not a single-user agent harness.
- Group isolation is a core product principle.
- Memory is defined as project experience distillation, not just storage.
- Ruflo is an algorithm/reference source, not a product model to copy.

Required fields:

- Concept
- Explanation
- Why it matters
- Related decisions
- Evidence source
- Related entities: users, bots, files, tasks, or groups

Product value:

- Supports product strategy continuity
- Helps BA/PM Bots reason consistently
- Prevents product direction drift

### 5.9 Skill Candidates

Skill candidates are memories that may become executable routines.

Examples:

- "MCP lifecycle review checklist"
- "Tool memory compression workflow"
- "Project decision extraction workflow"
- "Frontend responsive verification workflow"

Required fields:

- Candidate skill name
- Problem solved
- Trigger condition
- Procedure
- Required tools
- Evidence count
- Failure cases
- Human approval status
- Static scan status
- Rollback plan

Product value:

- Converts repeated experience into reusable capability
- Keeps behavior changes auditable
- Avoids silent self-modification

## 6. What Should Not Become Long-Term Memory

The system should avoid polluting long-term memory with low-value or unsafe material.

Do not promote by default:

- One-off transient chat fragments
- Unverified assumptions
- Sensitive secrets or raw credentials
- Large raw tool outputs
- Large raw document chunks without distilled claims
- Temporary debug states
- Failed hypotheses without clear conclusion
- External claims without source citation
- Behavior inferences about sensitive personal traits
- Memories without group ownership
- Memories that conflict with approved project rules
- Rejected learning candidates still inside their cooldown window

These may remain in raw logs or short-term context, but should not become approved project memory.

## 7. Memory Artifact Model

The system should use a staged artifact model instead of a flat memory table.

```text
Raw Event
  -> Source Document / Behavior Signal
  -> Episode
  -> Document Digest / Habit Candidate
  -> Reflection Candidate
  -> Policy Decision
  -> Approved Memory
  -> Promotion Candidate
  -> Active Skill / Active Rule
```

### 7.1 Raw Event

Raw Event is factual and mostly deterministic.

Sources:

- Tool call
- Message
- File change
- Test result
- Human correction
- Bot handoff
- Approval or rejection event
- Source document ingestion
- Review action
- Repeated behavior signal

Properties:

- Group-local
- Append-only where possible
- Redacted before model reuse
- Cheap to capture
- Not directly used as long-term knowledge

### 7.2 Episode

Episode is a compressed task-level narrative for work experience.

It answers:

- What was attempted?
- What happened?
- What changed?
- What failed?
- What succeeded?
- What evidence exists?

Episode should be generated after task completion, after a meaningful error, after a human correction, or when a salience threshold is reached.

### 7.3 Document Digest

Document Digest is a compressed, source-grounded representation of an external or internal document.

It answers:

- What is this source?
- Is it authoritative, exploratory, or outdated?
- What claims are relevant to this group?
- What constraints, facts, or workflows does it imply?
- What source spans support each claim?
- What should not be promoted without human approval?

Document Digest should preserve citation metadata and should not collapse external claims into project rules automatically.

### 7.4 Habit Candidate

Habit Candidate is a proposed pattern inferred from repeated behavior.

It answers:

- What repeated behavior was observed?
- Is it a user preference, group norm, review standard, or risk policy?
- How many examples support it?
- What is the risk of overgeneralization?
- Should it be saved as soft preference, asked for confirmation, or rejected?

Habit Candidates require special care because behavior inference can become intrusive or overfit. They should default to soft guidance until confirmed.

### 7.5 Reflection Candidate

Reflection Candidate is the first real experience abstraction.

It answers:

- What can be learned from this episode, document, or habit signal?
- Is the lesson project-specific or general?
- Is this lesson a fact, preference, warning, workflow, or skill candidate?
- What evidence supports it?
- What could make it wrong?

Reflection Candidates are not automatically trusted.

### 7.6 Policy Decision

Policy Decision is the explicit transition where the system decides what can happen to a Reflection Candidate.

Possible decisions:

- Reject
- Save as low-risk memory
- Ask human for review
- Merge with existing memory
- Mark as conflict
- Create Promotion Candidate
- Put into cooldown

This step should be implemented by a Learning Policy Engine, not by ad hoc prompt instructions.

### 7.7 Approved Memory

Approved Memory is a stable, retrievable project memory.

It must have:

- Source type
- Source format, if imported
- Memory type
- Clear scope
- Evidence
- Confidence
- Owner or approving source
- Conflict status
- Last updated time
- Temporal validity
- Importance score
- Last used time
- Promotion target

Approved Memory may influence Bot planning and responses, but should not automatically modify tool permissions or executable behavior.

### 7.8 Promotion Candidate

Promotion Candidate is a memory that may become a project rule or skill.

It requires stronger evidence than ordinary memory.

Promotion should consider:

- Repeated occurrence
- Human confirmation
- Successful verification
- Low conflict with existing rules
- Clear trigger condition
- Reversible activation
- Static compliance scan
- Drift monitoring rule
- Promotion target: memory only, L4 bot learned, L3 role skill, L2 group skill, or L1 system candidate

### 7.9 Active Skill Or Active Rule

Active Skill or Active Rule can change Bot behavior.

Requirements:

- Human-approved
- Group-local
- Versioned
- Reversible
- Traceable to evidence
- Visible in the group knowledge or skill UI
- Rollbackable to a prior version
- Monitored for drift or repeated failure

### 7.10 Rejected Candidate

Rejected Candidate should not disappear immediately.

It should keep:

- Rejection reason
- Rejecting user or policy
- Evidence
- Cooldown window
- Similarity signature

Purpose:

- Prevents the system from repeatedly proposing the same bad learning.
- Creates an audit trail for why a pattern was not accepted.
- Allows future reconsideration if new evidence changes the situation.

## 8. Skill Promotion Levels

Most distilled knowledge should remain memory. It should only become a skill when it is executable, repeatable, and verifiable.

Decision rule:

```text
Does it help the Bot know something?
  -> Approved Memory, not Skill.

Does it tell the Bot how to perform a repeatable procedure?
  -> Skill Candidate.
```

A Skill Candidate must have:

- Trigger condition
- Step-by-step procedure
- Required tools or inputs
- Safety checks
- Expected output
- Verification method
- Scope
- Evidence
- Rollback or disable path

### 8.1 L4 Learned / Personal

Use L4 when the learning belongs to one Bot.

Suitable for:

- A Bot learned a local debugging routine.
- A Bot found a useful personal workflow that has not been proven for the group.
- The evidence is still narrow and should remain under draft approval.

Target:

```text
promotion_target = l4_bot_learned
```

Runtime location:

```text
workspaces/group_<id>/bots/bot_<id>/skills/learned/draft/
workspaces/group_<id>/bots/bot_<id>/skills/learned/active/
```

### 8.2 L3 Role Skill

Use L3 when the procedure belongs to a role inside one group.

Suitable for:

- Architect role: project experience review workflow.
- QA role: turn failure lessons into regression checks.
- PM role: extract requirements and customer behavior patterns from email or chat bundles.
- Developer role: apply project-specific MCP lifecycle checklist.

Target:

```text
promotion_target = l3_role_skill
```

Runtime location:

```text
workspaces/group_<id>/roles/<role>/skills/
```

### 8.3 L2 Group Skill

Use L2 when the procedure should be shared by the whole group.

Suitable for:

- Group-wide Experience Note generation standard.
- Group-wide external document distillation protocol.
- Group-wide memory candidate review process.
- Group-wide project decision extraction workflow.

Target:

```text
promotion_target = l2_group_skill
```

Runtime location:

```text
workspaces/group_<id>/shared/skills/
```

### 8.4 L1 System Candidate

Use L1 only for curated cross-group capabilities.

Suitable for:

- Generic Word/PDF/Excel parsing workflow.
- Generic email-bundle extraction workflow.
- Generic memory candidate generation template.
- Generic source citation and evidence extraction routine.

Target:

```text
promotion_target = l1_system_candidate
```

Runtime location after human/admin curation:

```text
workspaces/system/skills/
```

L1 should not be produced automatically by group learning. It should be proposed as a candidate and curated by a human/admin.

### 8.5 Promotion Target Defaults

Default all distilled candidates to:

```text
promotion_target = memory_only
```

Only promote after verification and approval.

Recommended target mapping:

| Distilled Result | Default Target | Possible Promotion |
|---|---|---|
| Project Fact | memory_only | Project context |
| Decision | memory_only | Active rule after approval |
| External Knowledge | memory_only | Decision candidate or workflow after scope validation |
| Behavior Habit | memory_only | Team preference or policy candidate |
| Failure Lesson | memory_only | Checklist or role skill |
| Workflow Pattern | memory_only | L3 role skill or L2 group skill |
| Skill Candidate | memory_only | L4 draft, L3 role skill, L2 group skill, or L1 candidate depending on scope |

Scope rule:

```text
Only one Bot needs it -> L4
One role needs it -> L3
Whole group needs it -> L2
All groups could use it -> L1 candidate
```

## 9. Skill Evolution And Merge

The system should not create a new skill every time similar learning appears.

The preferred path is:

```text
similar learning
  -> find existing skill or skill candidate
  -> merge evidence
  -> update trigger/steps/checks
  -> create new version
  -> review
  -> activate upgraded skill
```

Skill growth should be versioned improvement, not uncontrolled accumulation.

### 9.1 Skill Identity

Every skill should have a stable identity separate from its current text.

Recommended fields:

- skill_id
- name
- scope: L4, L3, L2, or L1 candidate
- owner: bot, role, group, or system curator
- trigger signature
- task category
- required tools
- related memory ids
- evidence ids
- current version
- status: draft, active, archived, superseded, downgraded

The skill text can change over time, but skill_id remains stable.

### 9.2 Similarity Check Before New Skill

Before creating a new Skill Candidate, the Learning Policy Engine should search existing candidates and active skills.

Similarity dimensions:

- Same group
- Same role or bot scope
- Similar trigger condition
- Similar task category
- Similar labels and paths
- Similar required tools
- Similar output type
- Similar evidence sources
- Embedding or FTS similarity

Possible decisions:

- Merge into existing Skill Candidate
- Create a new version of an existing draft skill
- Propose update to an active skill
- Keep as separate skill only if trigger and procedure are genuinely different
- Reject as duplicate

Default rule:

> Similar skill learning should update an existing skill unless the trigger, scope, or expected output is meaningfully different.

### 9.3 Merge Modes

Skill merge should be explicit. Recommended merge modes:

| Merge Mode | Meaning | Example |
|---|---|---|
| Evidence merge | Same skill, more supporting evidence | Same MCP checklist worked again |
| Trigger expansion | Same procedure, broader trigger | Also applies to MCP auth reload |
| Step refinement | Existing step becomes more precise | Add Collector cleanup verification |
| Safety check addition | Add a guardrail | Add HIL namespace check |
| Scope promotion | L4 skill becomes L3 or L2 | Developer Bot skill becomes role skill |
| Split required | One skill is becoming too broad | Separate document ingestion from skill promotion review |
| Supersede | New version replaces old behavior | Old checklist is outdated after architecture change |

### 9.4 Versioning Policy

Every active skill should be versioned.

Version events:

- Created from candidate
- Evidence reinforced
- Procedure updated
- Trigger changed
- Scope changed
- Safety check added
- Deprecated or superseded
- Downgraded to memory

Recommended version metadata:

- version
- change summary
- reason
- evidence added
- reviewer
- created_at
- activated_at
- previous_version
- rollback_version

Activation rule:

> Updating an active skill should create a draft new version first. The old active version remains active until the new version is approved.

### 9.5 Skill Budget And Pruning

Budgets prevent uncontrolled growth, but merge and upgrade are the primary control mechanisms.

Initial limits:

```text
MAX_ACTIVE_L4_SKILLS_PER_BOT = 8
MAX_ACTIVE_L3_SKILLS_PER_ROLE = 20
MAX_ACTIVE_L2_SKILLS_PER_GROUP = 30
MAX_NEW_DRAFT_SKILLS_PER_WEEK_PER_BOT = 1
```

When a budget is reached, the system should not create a new active skill. It should choose one:

- Merge into an existing skill
- Create a new draft version
- Archive an unused skill
- Downgrade a narrow skill to memory
- Promote a frequently reused L4 skill to L3 or L2
- Ask human reviewer

### 9.6 Skill Evolution State Machine

Recommended state machine:

```text
Memory
  -> Workflow Pattern
  -> Skill Candidate
  -> Draft Skill vN
  -> Active Skill vN
  -> Draft Skill vN+1
  -> Active Skill vN+1
  -> Archived / Superseded / Downgraded
```

Forbidden shortcut:

```text
Memory -> Active Skill
```

Every upgrade must preserve:

- Evidence trail
- Version history
- Reviewer or approval source
- Rollback path
- Scope

### 9.7 Example

Day 1:

```text
Skill Candidate: MCP lifecycle review checklist
Evidence: Worker/Collector cleanup issue
Target: L4 draft
```

Day 4:

```text
New similar learning: MCP auth reload also needs Collector lock check
Decision: merge into existing MCP lifecycle review checklist
Change: add safety check and trigger expansion
Result: Draft Skill v2, not a new skill
```

Day 10:

```text
Multiple Developer and Architect Bots use the same checklist
Decision: promote scope from L4 to L3 Role or L2 Group
Result: one upgraded shared skill, not multiple duplicated bot skills
```

## 10. Memory Types For Nuke

The following memory types map directly to the product scenario.

| Type | Purpose | Retrieval Moment | Promotion Path |
|---|---|---|---|
| Project Fact | Understand current state | Task start, planning | May become project context |
| Decision | Respect agreed choices | Planning, code review, conflict | May become project rule |
| Failure Lesson | Avoid repeated mistakes | Before similar task, after error | May become checklist |
| Team Preference | Match user/group expectations | Response planning, UX/design work | May become behavior policy |
| Behavior Habit | Adapt to stable group working style | Planning, response style, review | May become preference or policy candidate |
| Workflow Pattern | Repeat successful methods | Task planning | May become skill |
| External Knowledge | Apply source-grounded outside knowledge | Planning, research, implementation | May become decision candidate |
| Domain Knowledge | Preserve product/academic reasoning | Strategy/design discussion | Usually remains memory |
| Skill Candidate | Convert repeated work into procedure | Skill review | May become active skill |

## 11. Retrieval Strategy

Retrieval should be scenario-driven, not just similarity-driven.

### 11.1 Retrieval Moments

The system should retrieve memory at these moments:

- Task start
- Before modifying code
- Before calling write-class tools
- After tool failure
- Before final response
- During handoff between Bots
- During skill promotion review
- During periodic reflection jobs
- During drift review for active skills or rules
- During document-grounded research or design work
- During preference-sensitive communication or planning

### 11.2 Retrieval Dimensions

Useful memory retrieval should combine:

- Semantic relevance
- Group scope
- Bot role
- Task type
- Recency
- Importance
- Confidence
- Evidence quality
- Conflict status
- Promotion status
- Temporal validity
- Rejection cooldown status
- Evidence tier
- Source authority
- Habit confidence

### 11.3 Retrieval Output

Bots should not receive undifferentiated memory blobs.

Recommended retrieval output:

```text
Relevant Project Facts
Relevant Decisions
Known Failure Lessons
Team Preferences
Behavior Habits
Source-Grounded External Knowledge
Applicable Workflow Patterns
Possible Skill Candidates
Conflicts Or Stale Memories
Rejected Similar Candidates
Evidence Tiers
```

This structure helps the Bot reason about how to use memory instead of blindly injecting text.

## 12. Distillation Algorithm

The first product-grade algorithm should be conservative.

### 12.1 Episode Generation

Input:

- Recent tool events
- Messages in the task window
- File changes
- Test results
- Human corrections
- Relevant source documents
- Review actions and repeated behavior signals

Output:

- Goal
- Outcome
- Timeline summary
- Key files
- Key decisions
- Failures
- Fixes
- Evidence references
- Importance estimate
- Whether immediate reflection is needed
- Outcome verdict
- Evidence tier
- Source authority, when document-derived
- Habit confidence, when behavior-derived

### 12.2 Salience Score

Salience Score decides whether raw material is worth distilling.

It does not decide whether something becomes a skill. It only answers:

> Is this material important enough to become a candidate?

Use a 10-point score:

| Dimension | Range | Meaning |
|---|---:|---|
| Explicit user signal | 0-3 | Did the user confirm, correct, emphasize, or ask to remember it? |
| Task outcome signal | 0-2 | Did it involve completion, failure, fix, test result, or decision? |
| Repetition | 0-2 | Has the same pattern appeared before? |
| Scope of impact | 0-2 | Does it affect one reply, a task type, a role, or the whole group? |
| Evidence quality | 0-2 | Is it model inference, chat/doc evidence, test result, or human approval? |
| Risk/cost penalty | -2-0 | Could it affect safety, privacy, permissions, or behavior? |

Detailed scoring:

Explicit user signal:

```text
0 = no user feedback
1 = user lightly mentions or implies it
2 = user clearly corrects, confirms, or asks to record it
3 = user says it is important, a principle, or should be remembered
```

Task outcome signal:

```text
0 = ordinary discussion with no result
1 = task completed, document created, code changed, or test run
2 = failure/fix/test pass/user confirmation/key decision
```

Repetition:

```text
0 = first occurrence
1 = similar content appeared twice
2 = similar content appeared three or more times
```

Scope of impact:

```text
0 = affects one reply only
1 = affects one Bot or task type
2 = affects the group, architecture, product direction, or long-term workflow
```

Evidence quality:

```text
0 = model inference only
1 = chat record, document, or tool log
2 = human approval, test result, authoritative document, or approval/rejection record
```

Risk/cost penalty:

```text
0 = low-risk descriptive memory
-1 = may influence behavior and needs review
-2 = involves permissions, security, privacy, cross-group risk, or sensitive inference
```

Thresholds:

```text
score <= 2  -> keep raw only
score 3-4   -> low-priority candidate, batch processing
score 5-6   -> Reflection Candidate
score >= 7  -> Review Queue
score >= 8  -> may suggest Approved Memory if user-confirmed or source-authoritative
```

### 12.3 Promotion Score

Promotion Score decides whether a candidate is worth upgrading into Workflow Pattern, Skill Candidate, Draft Skill, or rule.

It answers:

> Is this candidate valuable enough to become a reusable capability?

Use this scoring:

| Dimension | Range | Meaning |
|---|---:|---|
| Executability | 0-3 | Can it be turned into a procedure? |
| Trigger clarity | 0-2 | Is it clear when to use it? |
| Reuse value | 0-2 | Will it recur? |
| Verifiability | 0-2 | Can output or behavior be checked? |
| Evidence strength | 0-3 | Is the evidence repeated, approved, or execution-proven? |
| Dedup/merge result | -2-1 | Does it enhance existing skill or duplicate one? |
| Risk penalty | -3-0 | Could it affect tools, code, permissions, safety, or privacy? |

Detailed scoring:

Executability:

```text
0 = fact or knowledge only
1 = suggestion, not executable
2 = rough steps
3 = clear trigger, steps, checks, and output
```

Trigger clarity:

```text
0 = unclear when to use
1 = broad task type is known
2 = trigger condition is explicit
```

Reuse value:

```text
0 = one-off
1 = may recur
2 = clearly recurring
```

Verifiability:

```text
0 = cannot verify correctness
1 = human review possible
2 = test, schema validation, citation completeness, or acceptance criteria available
```

Evidence strength:

```text
0 = proxy/model inference
1 = document or chat evidence
2 = repeated evidence or user confirmation
3 = user confirmation plus execution success, test pass, or repeated successful reuse
```

Dedup/merge result:

```text
1 = enhances an existing skill
0 = new candidate, no duplicate found
-1 = overlaps with existing memory or skill
-2 = clear duplicate; should merge, not create a new skill
```

Risk penalty:

```text
0 = affects read/summarize/retrieve only
-1 = affects response style or planning
-2 = affects tool use, code changes, or project rules
-3 = involves permissions, security, HIL, cross-group risk, or sensitive behavior inference
```

Thresholds:

```text
score <= 3  -> keep as memory
score 4-6   -> Workflow Pattern
score 7-8   -> Skill Candidate
score >= 9  -> Draft Skill candidate, still requires review
```

MVP rules:

```text
Salience Score >= 5
  -> create Reflection Candidate

Salience Score >= 7
  -> enter Review Queue

Promotion Score >= 7
  -> mark as Skill Candidate

Promotion Score >= 9
  + evidence_count >= 3
  + human approval
  -> generate Draft Skill

Similar skill exists
  -> merge or create version update, do not create a new skill
```

Any high-risk item requires human approval regardless of score.

### 12.4 Source Document Distillation

For selected documents, extract source-grounded candidates.

Selection should be triggered by:

- User uploads or references a document
- A group document changes
- A Bot repeatedly consults the same document
- A document is marked authoritative
- A task needs source-grounded knowledge

Each candidate must include:

- Extracted claim
- Source citation
- Source authority
- Scope
- Confidence
- Time validity
- Whether it is fact, constraint, workflow, decision candidate, or domain knowledge
- Whether human approval is required before it affects behavior

### 12.5 Behavior Habit Distillation

For repeated behavior signals, extract habit candidates.

Selection should be triggered by:

- Repeated human correction
- Repeated review rejection or acceptance
- Repeated instruction across tasks
- Repeated stylistic or planning preference
- Explicit user statement such as "I prefer..." or "Do not..."

Each candidate must include:

- Observed habit
- Evidence count
- Examples
- Scope
- Confidence
- Risk of overgeneralization
- Whether it should be soft preference, hard rule candidate, or ignored

### 12.6 Reflection Extraction

For selected episodes, extract candidate lessons.

Selection should be triggered by:

- Task completion
- Tool failure
- Test failure
- Human correction
- Repeated similar episodes
- High importance score
- Scheduled batch reflection

- Fact learned
- Decision reinforced
- Failure pattern
- Preference detected
- Habit detected
- External knowledge extracted
- Workflow pattern
- Skill candidate

Each candidate must include:

- Claim
- Evidence
- Scope
- Confidence
- Counter-evidence or uncertainty
- Suggested action: save, ignore, ask human, promote later
- Suggested cooldown if rejected
- Evidence tier required for promotion

### 12.7 Deduplication And Conflict Check

Before saving:

- Search existing memories by semantic similarity
- Check same category and scope
- Merge if it is a reinforcement
- Create new version if it updates old knowledge
- Flag conflict if it contradicts an approved memory
- Suppress or warn if it is similar to a recently rejected candidate
- Flag source conflict if two documents disagree
- Flag habit drift if recent behavior contradicts older habit memory

### 12.8 Learning Policy Engine

The Learning Policy Engine decides the next state of each candidate.

Inputs:

- Candidate type
- Source type
- Source format
- Memory type
- Scope
- Confidence
- Evidence quality
- Evidence tier
- Source authority
- Habit confidence
- Risk level
- Similar approved memories
- Similar rejected candidates
- Similar skill candidates
- Similar active skills
- Conflict status
- Whether behavior would change
- Promotion target
- Salience Score
- Promotion Score

Outputs:

- Auto-save as low-risk memory
- Require human review
- Reject
- Merge
- Create Promotion Candidate
- Merge into existing Skill Candidate
- Create draft skill version update
- Propose active skill upgrade
- Supersede existing skill
- Downgrade skill-like content to memory
- Start or extend cooldown

Rule of thumb:

- Low-risk descriptive memory may be saved with lightweight review.
- Source-grounded external knowledge may be saved as reference memory, but must not become an approved project decision without scope validation.
- Behavior habits should default to soft guidance until the user confirms them or repeated evidence is strong.
- Preferences, decisions, workflows, and skill candidates should require human confirmation before they meaningfully affect behavior.
- Similar skill candidates should merge or version an existing skill before creating a new skill.
- Any executable or write-class behavior change must require approval and must not bypass HIL.

### 12.9 Human Review

The system should expose a small review queue:

- Save
- Edit and save
- Reject
- Mark as preference
- Mark as decision
- Mark as skill candidate
- Mark as external knowledge
- Mark as habit/preference
- Promote to rule or skill
- Merge into existing skill
- Create new skill version
- Supersede old skill
- Downgrade to memory
- Reject with reason
- Set cooldown

First version can keep this simple and text-based.

### 12.10 Promotion

Promotion from memory to active behavior should be stricter than saving memory.

Promotion requires:

- At least one explicit human approval, or repeated successful evidence
- Clear trigger condition
- Clear expected behavior
- No unresolved conflict
- Reversal path
- Static compliance scan
- Drift monitor
- Similar-skill merge check
- Version history

### 12.11 Drift And Retirement

Active skills and active rules should be downgraded or retired when:

- They repeatedly fail verification
- Users correct or override them
- Related project decisions are superseded
- The files, tools, or APIs they depend on change
- They conflict with newer approved memory
- Source documents are superseded
- Behavior habits no longer match recent user corrections

Possible actions:

- Keep active
- Mark for review
- Downgrade to approved memory
- Supersede with a new version
- Disable

## 13. Product UI Requirements

The memory system should be visible enough to earn trust.

### 13.1 Group Knowledge View

Each group should have a knowledge view with tabs:

- Decisions
- Project Facts
- Lessons
- Preferences
- Behavior Habits
- External Knowledge
- Workflows
- Skill Candidates
- Rejected / Cooldown

Each item should show:

- Status
- Confidence
- Evidence
- Last used
- Last updated
- Owner or source
- Validity
- Conflict status
- Evidence tier

### 13.2 Task Completion Experience Note

After meaningful Bot work, show an Experience Note:

```text
Task:
Outcome:
What changed:
What failed:
What was learned:
Suggested memory:
Suggested skill/rule candidate:
Evidence:
Evidence tier:
Source citation:
Observed habit:
```

User actions:

- Save
- Edit
- Ignore
- Promote later
- Reject with reason

### 13.3 Memory Use Transparency

When a Bot uses memory, it should be able to say:

```text
I used these project memories:
- Decision: MCP connections only live in Collector
- Failure lesson: previous MCP lifecycle changes failed when Worker/Collector ownership was mixed
```

This should be concise and optional, but available for audit.

## 14. Security And Isolation Requirements

Memory must follow the platform's core safety model.

Hard requirements:

- Group-local storage only
- No cross-group memory reuse by default
- Redaction before memory enters model context
- Secrets must not be promoted
- Write-class behavior changes require approval
- Skill promotion cannot bypass HIL
- Sub-agent memory access must follow permission attenuation
- Memory used for decisions must keep provenance
- Rejected candidates and cooldown state must remain group-local
- Active memory, rule, and skill versions must be auditable
- Proxy evidence must not autonomously promote behavior
- External knowledge must keep citation and source authority
- Behavior habits must not infer sensitive personal traits

## 15. System Components

The product-facing structure maps to these implementation components:

- Evidence Store: group-local raw events, tool traces, messages, test results, and approvals.
- Source Ingestion Store: group-local source documents, source metadata, citations, versions, and authority status.
- Episode Builder: compresses scoped events into task-level episodes.
- Document Distiller: converts source documents into source-grounded knowledge candidates.
- Habit Distiller: converts repeated corrections, reviews, and preferences into habit candidates.
- Reflection Extractor: produces structured Reflection Candidates from selected episodes.
- Memory Store: holds approved group-local memories with evidence, validity, confidence, and importance.
- Learning Policy Engine: controls save, reject, merge, cooldown, promotion, and review transitions.
- Evidence Judge: assigns outcome verdict and evidence tier.
- Hybrid Retriever: combines FTS/BM25, embeddings, metadata filters, and diversity reranking.
- Review Queue: exposes candidates to humans and records approval or rejection.
- Draft Skill Generator: creates draft skills from stable workflow or skill candidates.
- Skill Similarity Matcher: finds existing skill candidates and active skills before new skill creation.
- Skill Version Manager: creates draft updates, tracks version history, merges evidence, and preserves rollback.
- Static Compliance Scanner: checks draft skills against security, HIL, group isolation, and tool policy.
- Active Skill Loader: loads approved group-local skills without bypassing existing runtime security.
- Drift Monitor: detects stale, failing, or superseded skills and rules.

These components should fit inside the existing runtime architecture. They must not move MCP ownership out of the Collector, bypass ToolRouter policy, or weaken group isolation.

## 16. Evaluation Metrics

Distillation quality must be measured. The system should not claim learning quality without behavior and review data.

Core metrics:

| Metric | Meaning |
|---|---|
| Candidate acceptance rate | How often humans save generated candidates |
| User edit rate | How often candidates need correction before saving |
| Wrong classification rate | How often source_type or memory_type is wrong |
| Duplicate candidate rate | How often candidates repeat existing memory or skill |
| Evidence missing rate | How often candidates lack usable evidence or citation |
| Conflict detection rate | How often new candidates correctly flag conflicts |
| Retrieval hit rate | How often saved memory is retrieved for relevant future tasks |
| Memory use count | How often Bots actually use approved memory |
| Post-retrieval task success delta | Whether memory use improves later task outcomes |
| Skill merge success rate | How often similar skill learning updates an existing skill instead of creating duplicates |
| Active skill failure rate | How often active skills fail after triggering |
| Human override rate | How often users override memory-informed behavior |

Most important MVP metrics:

```text
1. Candidate acceptance rate
2. Future retrieval hit rate
3. Skill merge success rate
```

Interpretation:

- High acceptance + low edit rate means extraction quality is good.
- High retrieval hit rate means memory is useful after saving.
- High duplicate rate means dedupe and merge logic are weak.
- High active skill failure rate means promotion gates are too loose.
- High human override rate means memory or skill behavior is overreaching.

## 17. Strategic Roadmap

### Phase 1: Experience Note MVP

Goal:

- Prove that the system can turn completed work into useful project experience.

Build:

- Episode generation from messages and tool events
- Basic document digest from selected docs
- Basic explicit preference/habit capture from user corrections
- Reflection Candidate schema
- Outcome verdict and evidence tier fields
- Salience Score v0
- Learning Policy Engine v0
- Simple human review queue
- Save approved memories into group-local memory
- Retrieve approved memories at task start

Do not build yet:

- Complex graph memory
- Fully automatic skill generation
- Cross-group knowledge marketplace
- Autonomous behavior modification

### Phase 2: Decision And Failure Memory

Goal:

- Make Bots reliably remember architectural decisions and previous mistakes.

Build:

- Decision memory type
- Failure lesson type
- External Knowledge memory type
- Behavior Habit memory type
- Conflict detection
- Evidence links
- Promotion Score v0
- Retrieval before code changes and after tool failures
- Rejected candidate cooldown
- Hybrid retrieval v0: FTS + metadata + simple embedding rerank where available

### Phase 3: Workflow Pattern And Skill Candidate

Goal:

- Convert repeated successful work into candidate procedures.

Build:

- Workflow Pattern memory type
- Skill Candidate type
- Skill Similarity Matcher
- Skill version metadata
- Promotion review UI
- Draft skill generation
- Merge existing skill candidate instead of creating duplicates
- Static compliance scan
- Human approval before activation

### Phase 4: Drift, Graph, And Advanced Retrieval

Goal:

- Improve memory organization once the basic loop is proven.

Build:

- Entity and relation extraction
- Decision-to-file and failure-to-module links
- Graph-assisted retrieval
- Memory decay and stale-memory review
- Active skill drift monitor
- Honest retrieval and learning benchmarks

## 18. Open Questions For Discussion

1. Which memory type should ship first: Decision, Failure Lesson, or Team Preference?
2. Should Experience Notes appear after every Bot task, or only after tasks above a salience threshold?
3. Who can approve memory: group owner, task requester, or any human in the group?
4. Should a Bot be allowed to save low-risk memory automatically?
5. What is the minimum evidence required before a memory can affect future behavior?
6. Should promotion to skill require one approval or multiple successful uses?
7. How visible should memory usage be in normal chat?
8. Should rejected memory candidates be retained for audit or deleted?
9. What exact threshold creates a Reflection Candidate?
10. What exact threshold creates a Promotion Candidate?
11. Which memory categories are safe enough for auto-save but not auto-skill?
12. What signals should downgrade or retire an active skill?
13. Which evidence tiers can be saved automatically?
14. Which evidence tiers can influence retrieval but not behavior?
15. What acceptance metric proves that memory improves future project work?
16. Which document sources are authoritative inside a group?
17. When should inferred behavior habit ask for user confirmation?
18. How do we prevent external knowledge from overriding group decisions?
19. What similarity threshold should merge a Skill Candidate into an existing skill?
20. Who can approve active skill version upgrades?
21. What Salience Score threshold should enter the Review Queue by default?
22. What Promotion Score threshold should create a Draft Skill candidate?

## 19. Architectural Principle

The most important boundary is:

> Memory may inform behavior, but only approved rules and approved skills may change behavior.

This keeps the system useful without allowing silent self-modification.
