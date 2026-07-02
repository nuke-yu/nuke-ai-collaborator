# Agent Memory Research Base

Status: draft

This note consolidates the first pass over the papers and official repos we downloaded into `/Users/Nuke/agent_memory_design/`.
It is the working basis for the bot self-learning system.

## 1. Shared conclusion

Across the papers, the same pattern repeats:

1. raw experience must be recorded,
2. useful parts must be consolidated,
3. retrieval must be selective,
4. the system must keep provenance / confidence / time,
5. reusable capability must be separated from raw memory,
6. promotion must be controlled.

So the right object is not "a smarter prompt".
It is a memory-and-skill operating layer.

## 2. What each paper contributes

### Generative Agents

Core idea:

- maintain a `memory stream`
- retrieve memories with a weighted score
- synthesize reflections over time
- use reflections and memories to plan future behavior

What we should take:

- memory is a living stream, not a dump
- retrieval must combine relevance, recency, and importance
- reflection is a separate step from storage

### Reflexion

Core idea:

- do not update weights
- convert feedback into verbal self-reflection
- store reflective text in episodic memory
- use it to improve the next trial

What we should take:

- failure feedback can become a learning signal without fine-tuning
- reflection should produce a reusable lesson, not just a log entry
- self-improvement needs a memory hook

### Voyager

Core idea:

- automatic curriculum for exploration
- ever-growing skill library of executable code
- iterative prompting with environment feedback, execution errors, and self-verification

What we should take:

- repeated behavior can be distilled into a skill library
- the best learned artifact is executable and compositional
- self-verification is part of skill promotion

### MemGPT

Core idea:

- use hierarchical memory like an OS
- manage finite context as virtual context
- page data between working context and external storage

What we should take:

- context is scarce and must be actively managed
- long-term memory needs tiers, not one bucket
- the agent should be able to decide what stays in context

### Mem0

Core idea:

- dynamically extract, consolidate, and retrieve salient information
- graph-based variant captures relations and conflict resolution
- strong practical focus on latency and production behavior

What we should take:

- memory should be compact and salient
- extraction and consolidation are distinct from retrieval
- graph memory is useful when relations matter

### Graphiti / Zep

Core idea:

- temporal knowledge graph for agent memory
- preserve relationships with validity periods
- combine semantic search, full-text search, and graph traversal
- preserve provenance and temporal changes

What we should take:

- time matters, not just fact identity
- memory must support updates, invalidation, and historical validity
- mixed retrieval strategies are stronger than a single retrieval method

## 3. The knowledge model we should adopt

We should treat learning as four separate artifacts:

### Memory

What happened, what was observed, what is likely true.

Examples:

- user preference
- project rule
- recurring blocker
- stable fact

### Reflection

What the system concludes from repeated evidence.

Examples:

- "this user prefers one clarifying question before action"
- "this project needs rollback notes for schema changes"

### Draft skill

A candidate reusable rule, not yet active.

It must include:

- name
- scope
- evidence
- rule
- confidence
- promotion state

### Active skill

A confirmed, loaded capability.

It is a governed artifact, not a spontaneous side effect.

## 4. The operating sequence

The paper-backed sequence is:

1. ingest raw material
2. normalize and attach provenance
3. extract salient facts / patterns
4. reflect on repeated behavior
5. consolidate into memory or draft skill
6. verify with human or environment feedback
7. promote only if stable

This matches our current thinking:

- memory
- reflection
- distillation
- orchestration
- approval
- rollback

## 5. What this means for our product

The product should not promise:

- automatic self-modification
- unlimited autonomous learning
- prompt rewriting as the main trick

The product should promise:

- evidence-backed compounding
- project and person scope separation
- controlled promotion
- reversible skill evolution
- visible provenance

That is the commercial moat.

## 6. Base-layer design implications

The base layer should contain:

1. **Evidence store**
   - raw materials
   - source ids
   - scope
   - timestamps

2. **Reflection queue**
   - candidate rules
   - confidence
   - reason for extraction

3. **Memory store**
   - stable facts
   - preferences
   - project norms

4. **Draft registry**
   - skill candidates
   - approval state
   - diff / evidence chain

5. **Orchestration**
   - when to run extraction
   - when to consolidate
   - when to ask for approval
   - when to activate or roll back

## 7. Immediate next research step

After this pass, the next layer is code reading:

- `letta`
- `mem0`
- `autogen`
- `langgraph`
- `graphiti`
- `Voyager`

The goal is not to copy APIs.
The goal is to identify:

- where they store evidence
- how they extract stable memory
- how they represent time
- how they promote skills
- what they do for conflict resolution

