# Memory convergence record

Updated: 2026-08-13

`backend/memory` is the only Memory business implementation. The former
`backend/ai` Memory modules and the compatibility facade have been removed;
there is no supported dual-write or fallback mode.

## Runtime boundary

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
        +--> derived Chroma/vector projections
```

The process-level `MemoryComposition` is the single production wiring point
for the canonical database, schema manager, projection outbox, and reconciler.
Application services receive ports or those composed dependencies; they do not
select a second storage implementation.

## Completed migration

- Conversation memory, Personal Vault, ACL policy evaluation, usage audit,
  group facts, reflections, relations, tool events, cases, experiences,
  skills, learning usage, reflexion, execution runs, metrics, and projection
  outbox services now live under `memory.application`.
- `memory.canonical` and `memory.bootstrap` inject the concrete Personal Vault
  policy. Explicit deny rules are enforced and every authorization decision is
  audited.
- Learning state transitions enforce adoption, execution, and verification
  evidence in the application service. Callers cannot self-strengthen a
  memory by skipping the evidence contract.
- Case upsert retries first resolve the authoritative `case_id` by `run_id`,
  so replays of pre-migration rows cannot return an ID that is not persisted.
- Observation summaries and tool episodes pass through bounded redaction
  before canonical storage. Summary watermarks and message reads are scoped by
  `thread_id`; Personal Vault usage events are emitted only for records that
  actually fit the rendered context.
- `safe_memory_mapping()` bounds the data structure before serialization and
  always returns round-trippable JSON. It never slices serialized JSON text.
- Memory schema v12 and central DB migration 063 scope `agent_cases` identity
  by `(group_id, run_id)`, preserving isolation when a run identifier is reused
  across Groups.
- Observation summaries read the production `meta.memory_observation.thread_id`
  path and retain a top-level fallback for older records.
- Personal Vault schema v2 repairs orphan rows; full Vault deletion removes
  records, projections, usage/audit/governance data, and the physical database
  file. Impact analysis reports actual usage sessions.
- `MemoryAuthorizationError` is translated to HTTP 403 centrally, and an
  unavailable authorization audit fails closed.
- Experience aggregation reads and writes under one writer transaction and
  matches task, failure, and environment signatures before combining evidence.
- Projection writes remain transactional outbox intents; Chroma is derived and
  retryable, never authoritative.

## Removed surfaces

The following old Memory modules no longer exist:

- `backend/ai/memory.py`
- `backend/ai/personal_vault.py`
- `backend/ai/cases.py`
- `backend/ai/experiences.py`
- `backend/ai/skill_learning.py`
- `backend/ai/pipeline.py`
- `backend/ai/tool_events.py`
- `backend/ai/execution_runs.py`
- `backend/ai/reflexion.py`
- `backend/ai/usage_tracking.py`
- `backend/ai/learning_metrics.py`
- `backend/ai/projection_outbox.py`
- `backend/memory/compatibility.py`

Historical data backfill scripts remain only where they convert existing data
into canonical records or projections. They are not runtime compatibility
entrypoints and do not define a second Memory model.

## Verification

The migration gate is:

1. `python3 -m compileall -q backend/memory`
2. `git diff --check`
3. canonical contract, authorization, safety, observation, learning, pipeline,
   and projection tests
4. the complete backend regression suite

No commit is releasable while any old Memory import, compatibility facade,
unsafe storage path, or unverified canonical transition remains.
