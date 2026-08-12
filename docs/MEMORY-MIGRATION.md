# Memory convergence plan

`backend/memory` is the only intended owner of Memory business behavior.
`backend/ai` is a temporary migration source, not a second supported Memory
implementation.

## End state

```text
API / Worker / Scheduler
        |
        v
memory.application
        |
        v
memory.ports -> memory.infrastructure
        |
        +--> canonical group/personal SQLite records
        +--> transactional projection outbox
        +--> Chroma/vector/search projections
```

There must be one canonical write path. Chroma and other indexes are derived
projections and are never authoritative.

Remaining compatibility facades no longer resolve storage through
`ai.memory._memory_db`. Where a historical contract still needs a bridge, it
uses the narrow `memory.compatibility._memory_db` shim, which delegates to the
canonical `SQLiteMemoryDatabase` router and contains no business logic or
vector-store access.

## Migration order

1. Group facts, Bot facts, reflections, and relations.
2. Projection delivery and vector retrieval.
3. Personal Vault and app governance.
4. Learning cases, experiences, skills, and usage tracking.
5. Conversation memory provider and remaining lifecycle jobs.
6. Delete `memory/adapters/runtime/*_legacy.py` and the corresponding
   `backend/ai` business modules.

## Progress snapshot — 2026-08-12

- Conversation Memory production read/write now uses
  `memory.canonical.CanonicalConversationMemoryService`.
- Personal Vault records, projections, app registry, usage events, and API
  entrypoints now use the canonical Personal Vault service.
- Learning hot-path recall and usage tracking now use
  `memory.canonical.CanonicalLearningService`.
- Canonical pipeline job persistence is now implemented by
  `memory.application.pipeline.CanonicalPipelineJobRepository`; lifecycle
  statistics already read through the canonical service, including lease and
  retry counters.
- The top-level `observe_turn` fan-out handler now runs through the canonical
  dispatcher and creates the four child jobs there.
- Observation event loading (message, bot/group ownership, memory policy,
  provider/model metadata, and thread metadata) now belongs to
  `CanonicalObservationLoader`; the legacy stage no longer reaches into
  `ai.memory._memory_db` for this boundary.
- `observe_turn_fact` now uses `CanonicalBotFactObserver`: extraction and
  reconciliation read canonical fact records, writes go through
  `BotFactObservationService`, and vector updates are durable outbox events.
  The Chroma adapter is now only a derived projection consumer.
- `observe_turn_summary` now persists canonical `summary` records with a
  `covered_through_id` watermark. Canonical conversation recall includes both
  conversation records and summaries.
- `observe_turn_reflection` now reads canonical facts by thread, applies the
  configured count/importance gates, persists reflections through the
  canonical reflection service and advances a canonical per-thread watermark
  even when no insight is produced.
- `observe_turn_tool_compression` now reads canonical-routed `tool_events`,
  writes `tool_episode` records, and marks the source events compressed in the
  same canonical transaction. Conversation recall includes those episodes.
- `project_skill` now reads canonical `skills/skill_versions` and performs a
  validated atomic workspace projection through
  `CanonicalSkillProjectionService`; it does not mutate Skill authority data.
- `evaluate_case` now has a canonical deterministic outcome gate. Cases that
  require Experience/Skill distillation enqueue a dedicated `distill_case` job
  after canonical evaluation completes.
- Runtime lifecycle now uses only the canonical dispatcher; it no longer
  invokes any legacy dispatcher or legacy Memory pipeline. The former
  deferred legacy bridge has been removed.
- `distill_case` is now canonical: verified Cases are converted into
  `memory_records(kind='experience')` and enqueue a dedicated
  `compile_skill_candidate` job. Skill compilation and projection are also
  canonical; the legacy adapter remains only as a compatibility/test surface.
- Experience distillation now aggregates repeated verified Cases by
  Group/Bot/task signature, increments `supporting_count`, and preserves the
  source Case list. This allows the canonical two-evidence Skill gate to work.
- Canonical Experience writes now enqueue an `experience_vector_upsert` intent
  in the same transaction; vector indexing is derived and retryable through
  the projection outbox.
- Production composition now uses the canonical Chroma delivery adapter and a
  dedicated `ChromaProjectionClient`; it no longer calls
  `ai.memory.ChromaStore` or `ai.experiences._index_vector`.
- Production `MemoryModule` reconciliation now uses
  `CanonicalProjectionReconciler`, which rebuilds Experience projection
  intents from canonical SQLite without reading the legacy Chroma facade.
- `ai.cases`, `ai.experiences`, and `ai.skill_learning` no longer contain
  durable Memory implementations. Their remaining public functions are
  compatibility facades or pure validation/trace helpers delegating to
  `memory.application`; their old SQL, ranking, Chroma, and workspace-write
  implementations have been removed.
- The old `ai.memory` implementation and its direct white-box tests have been
  deleted. Production Python code, maintenance scripts, and test fixtures no
  longer import it; Chroma fail-soft coverage now targets the canonical
  `ChromaProjectionClient`.
- Chroma timestamp and `scored_by_model` backfill commands now use
  `memory.adapters.projections.maintenance`; only the collision-recovery
  rebuild command now also replays messages through `CanonicalBotFactObserver`
  and commits projection intents through the canonical outbox.
- Tool-event persistence, retrieval, compression, and source-event watermarks
  now use canonical SQLite and `memory_records(kind='tool_episode')`; the old
  direct `ai.memory._memory_db` dependency is removed.
- Reflexion decision, memory-injection, and memory-adoption records now use
  canonical SQLite directly.
- The obsolete `ai.memory_provider` and
  `LegacyConversationMemoryAdapter` compatibility chain has been deleted;
  conversation callers now use the canonical service directly.
- The obsolete `LegacyPersonalKnowledgeAdapter` and
  `LegacyPersonalVaultPolicyAdapter` have also been deleted; Personal Vault
  authorization and persistence now use the canonical application services.
- Projection audit and Chroma backfill now use the canonical
  `ChromaBotMemoryProjectionReader`; the legacy reader is no longer exported
  from the runtime compatibility package.
- Bot fact/reflection projection reconciliation now belongs to
  `CanonicalProjectionReconciler`; `projection_legacy.py` has been deleted.
- `ai.pipeline` now uses `CanonicalPipelineJobRepository` directly; the
  obsolete `LegacyLearningAdapter`, `LegacyPipelineJobAdapter`, and their
  historical lease tests have been deleted.
- The old `ai.memory` SQLite router has been deleted; `sqlite_legacy.py` and
  the other obsolete runtime adapters are gone.
- Existing pre-split group databases are kept bootable: group initialization
  now backfills the nullable `messages.external_message_key` column before
  recreating its partial unique index. This is a compatibility schema repair,
  not a second Memory persistence path.
- `ai.pipeline` is now a compatibility facade over canonical enqueue,
  dispatch, gap-repair, and stats services; the old pipeline handler graph has
  been removed.
- The compatibility facade preserves historical batch behavior while all
  actual work is performed by canonical leased jobs; no legacy handler or
  legacy persistence path was restored.

All production lifecycle job types are now canonical:
`evaluate_case`, `distill_case`, `compile_skill_candidate`, `project_skill`,
and all four observation stages. `backend/ai/pipeline.py` remains only as a
narrow compatibility facade for direct legacy callers; it is no longer on the
production lifecycle path.

Remaining compatibility facades are limited to contract translation; no
legacy Memory implementation or direct vector/SQLite path remains.

## Rules during migration

- No new production code may import the legacy Memory modules.
- A legacy adapter may translate contracts, but may not define new business
  rules or become a second persistence path.
- Every migrated use case needs an application-level contract test and a
  cross-group isolation test before the old implementation is removed.
- Removing a legacy module requires deleting its adapter and its direct tests,
  then running the full backend regression suite.
